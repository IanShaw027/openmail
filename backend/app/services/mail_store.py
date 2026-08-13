"""Server-side mail storage: stable_id dedupe, upsert, delta sync, cursors.

Used by cloud poll (path A) and device delta pull (path B). See
docs/19-cloud-sync-incremental.md.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.crypto import CryptoError, decrypt_str, decrypt_str_or_plain, encrypt_str
from app.models import Account, MailItem, SyncCursor, _new_id, _utcnow

# Contract constants (time-cursor overlap lives in poll layer; delta page size here)
DELTA_PAGE = 200
_FOLDER_ALIASES = {
    "inbox": "inbox",
    "in": "inbox",
    "spam": "spam",
    "junk": "spam",
    "junk email": "spam",
    "junkemail": "spam",
    "sent": "sent",
    "sent items": "sent",
    "sentitems": "sent",
    "sent mail": "sent",
}


def normalize_folder(folder: str | None) -> str:
    """Map provider folder names to inbox|spam|sent (junk → spam)."""
    if not folder:
        return "inbox"
    key = str(folder).strip().lower()
    if not key:
        return "inbox"
    if key in _FOLDER_ALIASES:
        return _FOLDER_ALIASES[key]
    # bare junk-like tokens
    if "junk" in key or "spam" in key:
        return "spam"
    if key.startswith("sent"):
        return "sent"
    if key.startswith("in"):
        return "inbox"
    return key[:32] if len(key) > 32 else key


def _attr(msg: Any, *names: str, default: Any = None) -> Any:
    if msg is None:
        return default
    if isinstance(msg, dict):
        for n in names:
            if n in msg and msg[n] is not None:
                return msg[n]
        return default
    for n in names:
        if hasattr(msg, n):
            v = getattr(msg, n)
            if v is not None:
                return v
    return default


def _as_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _normalize_message_id(raw: str | None) -> str | None:
    if not raw:
        return None
    s = _as_str(raw)
    if not s:
        return None
    # Strip angle brackets and whitespace
    s = s.strip().strip("<>").strip()
    return s or None


def _looks_like_imap_uid(value: Any) -> bool:
    s = _as_str(value)
    return bool(s) and s.isdigit()


def _provider_id_from_msg(msg: Any) -> str | None:
    """Best provider-native id (Graph id, IMAP uidvalidity:uid, etc.)."""
    # Explicit fields
    for key in ("provider_id", "providerId"):
        v = _attr(msg, key)
        if v:
            return _as_str(v)

    pid = _attr(msg, "id")
    uidvalidity = _attr(msg, "uidvalidity")
    if pid is not None and uidvalidity is not None:
        return f"{uidvalidity}:{pid}"

    raw_refs = _attr(msg, "raw_refs", default=None) or {}
    if isinstance(raw_refs, dict):
        for key in ("graph_id", "provider_id", "mail_id", "uid"):
            if not raw_refs.get(key):
                continue
            if key == "uid":
                if uidvalidity is not None:
                    return f"{uidvalidity}:{raw_refs[key]}"
                # Bare IMAP UID is reused after mailbox rebuild — not a stable id.
                continue
            return _as_str(raw_refs[key])

    if pid is not None and _as_str(pid):
        # Digit-only ids are IMAP UIDs; without UIDVALIDITY they are not stable.
        if _looks_like_imap_uid(pid) and uidvalidity is None:
            return None
        return _as_str(pid)
    return None


def _message_id_from_msg(msg: Any) -> str | None:
    for key in ("message_id", "messageId", "internet_message_id", "internetMessageId"):
        mid = _normalize_message_id(_as_str(_attr(msg, key)) or None)
        if mid:
            return mid
    raw_refs = _attr(msg, "raw_refs", default=None) or {}
    if isinstance(raw_refs, dict):
        for key in ("message_id", "internet_message_id", "Message-ID", "messageId"):
            mid = _normalize_message_id(_as_str(raw_refs.get(key)) or None)
            if mid:
                return mid
    return None


def _weak_fingerprint(msg: Any) -> str:
    """Weak id material: trim-only fields joined by ``|`` (must match frontend).

    ``from|date|subject|size`` — size empty string when missing.
    Prefix ``wh_`` + sha256 hex[:40].
    """
    from_addr = _as_str(
        _attr(msg, "from_addr", "from_address", "from_", "from", default="")
    )
    date = _as_str(_attr(msg, "date", "received_at", default=""))
    subject = _as_str(_attr(msg, "subject", default=""))
    size = _attr(msg, "size", default=None)
    size_s = "" if size is None else str(size)
    blob = f"{from_addr}|{date}|{subject}|{size_s}"
    return "wh_" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:40]


def compute_stable_id(msg: dict | object) -> str:
    """Dedupe key: provider_id → message_id → weak hash.

    Priority matches docs/19-cloud-sync-incremental.md.
    """
    provider_id = _provider_id_from_msg(msg)
    if provider_id:
        return f"p:{provider_id}"

    mid = _message_id_from_msg(msg)
    if mid:
        return f"m:{mid.lower()}"

    return _weak_fingerprint(msg)


def parse_received_at(value: Any) -> datetime | None:
    """Parse Message.date / ISO / epoch into timezone-aware UTC datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    if isinstance(value, (int, float)):
        ts = float(value)
        if abs(ts) < 100_000_000_000:  # seconds
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        return datetime.fromtimestamp(ts / 1000.0, tz=timezone.utc)

    s = _as_str(value)
    if not s:
        return None
    try:
        numeric = float(s)
        if abs(numeric) < 100_000_000_000:
            return datetime.fromtimestamp(numeric, tz=timezone.utc)
        return datetime.fromtimestamp(numeric / 1000.0, tz=timezone.utc)
    except ValueError:
        pass

    try:
        iso = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass

    try:
        from email.utils import parsedate_to_datetime

        dt = parsedate_to_datetime(s)
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _to_addrs_json(to_val: Any) -> str | None:
    if to_val is None:
        return None
    if isinstance(to_val, list):
        items = [_as_str(x) for x in to_val if _as_str(x)]
        return json.dumps(items, ensure_ascii=False) if items else None
    s = _as_str(to_val)
    if not s:
        return None
    # single string — store as one-element JSON array for stable shape
    return json.dumps([s], ensure_ascii=False)


def message_to_fields(msg: dict | object) -> dict[str, Any]:
    """Map provider Message-like object to MailItem column fields (unencrypted)."""
    from_addr = _as_str(
        _attr(msg, "from_addr", "from_address", "from_", "from", default="")
    ) or None
    subject = _as_str(_attr(msg, "subject", default="")) or None
    preview = _as_str(
        _attr(msg, "preview", "body_preview", default="")
    ) or None
    body_text = _as_str(_attr(msg, "body_text", "bodyText", default="")) or None
    body_html = _as_str(_attr(msg, "body_html", "bodyHtml", default="")) or None
    code = _attr(msg, "verification_code", "verificationCode", default=None)
    code_s = _as_str(code) if code is not None else None
    if code_s == "":
        code_s = None

    size = _attr(msg, "size", default=None)
    try:
        size_i = int(size) if size is not None else None
    except (TypeError, ValueError):
        size_i = None

    has_att = bool(_attr(msg, "has_attachments", "hasAttachments", default=False))

    received = parse_received_at(_attr(msg, "date", "received_at", "receivedAt", default=None))
    provider_id = _provider_id_from_msg(msg)
    message_id = _message_id_from_msg(msg)
    stable_id = compute_stable_id(msg)

    return {
        "stable_id": stable_id,
        "provider_id": provider_id,
        "message_id": message_id,
        "received_at": received,
        "from_addr": from_addr,
        "to_addrs": _to_addrs_json(_attr(msg, "to", "to_addrs", "toAddrs", default=None)),
        "subject": subject,
        "preview": preview,
        "verification_code": code_s,
        "body_text": body_text,
        "body_html": body_html,
        "has_attachments": has_att,
        "size": size_i,
    }


def compute_content_hash(fields: dict[str, Any]) -> str:
    """Hash of content-bearing fields for change detection."""
    parts = [
        fields.get("subject") or "",
        fields.get("from_addr") or "",
        fields.get("to_addrs") or "",
        fields.get("preview") or "",
        fields.get("verification_code") or "",
        fields.get("body_text") or "",
        fields.get("body_html") or "",
        "1" if fields.get("has_attachments") else "0",
        str(fields.get("size") if fields.get("size") is not None else ""),
        fields.get("message_id") or "",
        fields.get("provider_id") or "",
        (fields.get("received_at").isoformat() if fields.get("received_at") else ""),
    ]
    blob = "\n".join(parts)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def upsert_messages(
    db: Session,
    account_id: str,
    folder: str,
    messages: list[Any],
    *,
    settings: Settings | None = None,
) -> dict[str, int]:
    """Upsert provider messages into mail_items. Skip write if content unchanged.

    Returns counts: {inserted, updated, unchanged}.
    """
    s = settings or get_settings()
    folder_n = normalize_folder(folder)
    now = _utcnow()
    inserted = updated = unchanged = 0

    if not messages:
        return {"inserted": 0, "updated": 0, "unchanged": 0}

    # Preload existing rows for this batch of stable_ids
    fields_list = [message_to_fields(m) for m in messages]
    sids = [f["stable_id"] for f in fields_list]
    existing_rows = (
        db.query(MailItem)
        .filter(
            MailItem.account_id == account_id,
            MailItem.folder == folder_n,
            MailItem.stable_id.in_(sids),
        )
        .all()
    )
    by_sid: dict[str, MailItem] = {r.stable_id: r for r in existing_rows}

    for fields in fields_list:
        content_hash = compute_content_hash(fields)
        body_text = fields.get("body_text")
        body_html = fields.get("body_html")

        body_text_enc = encrypt_str(body_text, settings=s) if body_text else None
        body_html_enc = encrypt_str(body_html, settings=s) if body_html else None
        preview_plain = fields.get("preview")
        code_plain = fields.get("verification_code")
        preview_enc = encrypt_str(preview_plain, settings=s) if preview_plain else None
        code_enc = encrypt_str(code_plain, settings=s) if code_plain else None

        sid = fields["stable_id"]
        row = by_sid.get(sid)
        if row is None:
            row = MailItem(
                id=_new_id("mid_"),
                account_id=account_id,
                folder=folder_n,
                stable_id=sid,
                provider_id=fields.get("provider_id"),
                message_id=fields.get("message_id"),
                content_hash=content_hash,
                received_at=fields.get("received_at"),
                from_addr=fields.get("from_addr"),
                to_addrs=fields.get("to_addrs"),
                subject=fields.get("subject"),
                preview=preview_enc,
                verification_code=code_enc,
                body_text_enc=body_text_enc,
                body_html_enc=body_html_enc,
                has_attachments=bool(fields.get("has_attachments")),
                size=fields.get("size"),
                fetched_at=now,
                updated_at=now,
                deleted_at=None,
            )
            db.add(row)
            by_sid[sid] = row
            inserted += 1
            continue

        # Unchanged content → skip write
        if row.content_hash == content_hash and row.deleted_at is None:
            unchanged += 1
            continue

        # Content change or undelete
        row.provider_id = fields.get("provider_id") or row.provider_id
        row.message_id = fields.get("message_id") or row.message_id
        row.content_hash = content_hash
        row.received_at = fields.get("received_at") or row.received_at
        row.from_addr = fields.get("from_addr")
        row.to_addrs = fields.get("to_addrs")
        row.subject = fields.get("subject")
        row.preview = preview_enc
        row.verification_code = code_enc
        # Prefer non-empty body on update
        if body_text_enc is not None:
            row.body_text_enc = body_text_enc
        if body_html_enc is not None:
            row.body_html_enc = body_html_enc
        row.has_attachments = bool(fields.get("has_attachments"))
        if fields.get("size") is not None:
            row.size = fields.get("size")
        row.fetched_at = now
        row.updated_at = now
        row.deleted_at = None
        updated += 1

    db.flush()
    return {"inserted": inserted, "updated": updated, "unchanged": unchanged}


# Alias used by contract wording
upsert_mail_items = upsert_messages


def _parse_iso_dt(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    s = str(value).strip()
    if not s:
        return None
    try:
        iso = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def list_delta(
    db: Session,
    device_id: str,
    *,
    since: datetime | str | None = None,
    since_id: str | None = None,
    limit: int = DELTA_PAGE,
    include_body: bool = True,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Keyset delta of mail_items for accounts owned by device_id.

    Pagination key: (updated_at, id). Default includes decrypted bodies.
    Preview and verification_code are decrypted (legacy plaintext still accepted).
    """
    s = settings or get_settings()
    lim = max(1, min(int(limit or DELTA_PAGE), 500))
    since_dt = _parse_iso_dt(since)
    server_time = _utcnow()

    q = (
        db.query(MailItem, Account.email)
        .join(Account, Account.id == MailItem.account_id)
        .filter(Account.owner_user_id == device_id)
    )

    if since_dt is not None:
        if since_id:
            q = q.filter(
                or_(
                    MailItem.updated_at > since_dt,
                    and_(MailItem.updated_at == since_dt, MailItem.id > since_id),
                )
            )
        else:
            q = q.filter(MailItem.updated_at > since_dt)

    q = q.order_by(MailItem.updated_at.asc(), MailItem.id.asc()).limit(lim + 1)
    rows = q.all()
    has_more = len(rows) > lim
    rows = rows[:lim]

    mails: list[dict[str, Any]] = []
    for item, email in rows:
        to_list: list[str] = []
        if item.to_addrs:
            try:
                parsed = json.loads(item.to_addrs)
                if isinstance(parsed, list):
                    to_list = [str(x) for x in parsed]
                elif parsed:
                    to_list = [str(parsed)]
            except Exception:
                to_list = [item.to_addrs] if item.to_addrs else []

        body_text = None
        body_html = None
        if include_body:
            if item.body_text_enc:
                try:
                    body_text = decrypt_str(item.body_text_enc, settings=s)
                except CryptoError:
                    body_text = None
            if item.body_html_enc:
                try:
                    body_html = decrypt_str(item.body_html_enc, settings=s)
                except CryptoError:
                    body_html = None

        mails.append(
            {
                "account_id": item.account_id,
                "email": email,
                "folder": item.folder,
                "stable_id": item.stable_id,
                "id": item.id,
                "subject": item.subject,
                "from_addr": item.from_addr,
                "to_addrs": to_list,
                "date": item.received_at.isoformat() if item.received_at else None,
                "preview": decrypt_str_or_plain(item.preview, settings=s),
                "verification_code": decrypt_str_or_plain(item.verification_code, settings=s),
                "body_text": body_text,
                "body_html": body_html,
                "updated_at": item.updated_at.isoformat() if item.updated_at else None,
                "deleted": item.deleted_at is not None,
            }
        )

    return {
        "mails": mails,
        "has_more": has_more,
        "server_time": server_time.isoformat(),
    }


# Alias for contract naming
list_delta_for_device = list_delta


def get_cursor(db: Session, account_id: str, folder: str) -> SyncCursor | None:
    folder_n = normalize_folder(folder)
    return (
        db.query(SyncCursor)
        .filter(SyncCursor.account_id == account_id, SyncCursor.folder == folder_n)
        .one_or_none()
    )


def get_or_create_cursor(
    db: Session,
    account_id: str,
    folder: str,
    *,
    mode: str = "time",
) -> SyncCursor:
    folder_n = normalize_folder(folder)
    row = get_cursor(db, account_id, folder_n)
    if row is not None:
        return row
    row = SyncCursor(
        id=_new_id("cur_"),
        account_id=account_id,
        folder=folder_n,
        mode=mode or "time",
        cursor_json=None,
        updated_at=_utcnow(),
    )
    db.add(row)
    db.flush()
    return row


def save_cursor(
    db: Session,
    account_id: str,
    folder: str,
    cursor_data: dict[str, Any],
    *,
    mode: str | None = None,
) -> SyncCursor:
    """Persist cursor_json (merge with existing dict keys)."""
    row = get_or_create_cursor(db, account_id, folder, mode=mode or "time")
    existing: dict[str, Any] = {}
    if row.cursor_json:
        try:
            existing = json.loads(row.cursor_json)
            if not isinstance(existing, dict):
                existing = {}
        except Exception:
            existing = {}
    existing.update(cursor_data)
    row.cursor_json = json.dumps(existing, ensure_ascii=False, separators=(",", ":"))
    if mode:
        row.mode = mode
    row.updated_at = _utcnow()
    db.flush()
    return row


def save_cursor_time_high_water(
    db: Session,
    account_id: str,
    folder: str,
    high_water_time: str | datetime,
    high_water_ids: list[str] | None = None,
) -> SyncCursor:
    """Advance time-mode cursor high-water mark after successful upsert."""
    if isinstance(high_water_time, datetime):
        hw = high_water_time.astimezone(timezone.utc).isoformat()
    else:
        hw = str(high_water_time)
    return save_cursor(
        db,
        account_id,
        folder,
        {
            "high_water_time": hw,
            "high_water_ids": list(high_water_ids or []),
        },
        mode="time",
    )
