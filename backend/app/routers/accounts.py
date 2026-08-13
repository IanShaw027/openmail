"""Account API — device-scoped cloud store + local-first browser mirror.

Cloud rows use owner_user_id = X-Device-Id (no user login).
Credentials encrypted with OPENMAIL_MASTER_KEY.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.crypto import encrypt_json, encrypt_str, master_key_configured
from app.deps import DbDep, SettingsDep
from app.deps_device import device_id_strict
from app.models import Account, AccountPool, AccountSession, AccountStatus, ProviderType
from app.schemas import AccountCreate, AccountOut, AccountUpdate
from app.services.credentials import (
    is_client_sealed_blob,
    load_credentials,
    normalize_oauth_credential_fields,
    save_client_sealed,
)
from app.services.license import (
    quota_snapshot,
    reconcile_cloud_account_used,
    release_cloud_account_slot,
    reserve_cloud_account_slot,
)

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


def _require_master_key(settings) -> None:  # type: ignore[no-untyped-def]
    if not master_key_configured(settings):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OPENMAIL_MASTER_KEY not configured; cannot store credentials",
        )


def _to_out(acc: Account) -> AccountOut:
    sealed = False
    try:
        sealed = is_client_sealed_blob(load_credentials(acc))
    except Exception:
        sealed = False
    return AccountOut(
        id=acc.id,
        email=acc.email,
        provider=acc.provider,
        pool=acc.pool,
        owner_user_id=acc.owner_user_id,
        tag=acc.tag,
        note=acc.note,
        status=acc.status,
        last_fetch_at=acc.last_fetch_at,
        last_error=acc.last_error,
        latest_verification_code=acc.latest_verification_code,
        latest_code_at=acc.latest_code_at,
        latest_code_folder=acc.latest_code_folder,
        sync_enabled=acc.sync_enabled,
        last_sync_at=acc.last_sync_at,
        last_sync_error=acc.last_sync_error,
        proxy=acc.proxy,
        has_password=bool(acc.password_enc) and not sealed,
        has_credential=bool(acc.credential_enc),
        has_session=acc.session is not None and bool(acc.session.cookies_enc),
        client_sealed=sealed,
        created_at=acc.created_at,
        updated_at=acc.updated_at,
    )


def _get_owned(db: Session, account_id: str, device_id: str) -> Account:
    acc = db.get(Account, account_id)
    if acc is None or acc.owner_user_id != device_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="account not found")
    return acc


def _cloud_count(db: Session, device_id: str) -> int:
    return (
        db.query(Account)
        .filter(Account.owner_user_id == device_id)
        .count()
    )


def _provider_from_body(value: ProviderType | str | None) -> ProviderType:
    if value is None:
        return ProviderType.unknown
    if isinstance(value, ProviderType):
        return value
    try:
        return ProviderType(str(value))
    except ValueError:
        return ProviderType.unknown


@router.get("", response_model=list[AccountOut])
def list_accounts(
    db: DbDep,
    device_id: str = Depends(device_id_strict),
    x_license_token: str | None = Header(default=None, alias="X-License-Token"),
) -> list[AccountOut]:
    """List cloud accounts owned by this vault device (HMAC required)."""
    _ = x_license_token
    rows = (
        db.query(Account)
        .filter(Account.owner_user_id == device_id)
        .order_by(Account.created_at.desc())
        .all()
    )
    return [_to_out(a) for a in rows]


@router.post("", response_model=AccountOut, status_code=status.HTTP_201_CREATED)
def create_account(
    body: AccountCreate,
    db: DbDep,
    settings: SettingsDep,
    device_id: str = Depends(device_id_strict),
    x_license_token: str | None = Header(default=None, alias="X-License-Token"),
) -> AccountOut:
    """Store credentials on server for this device (encrypted / client-sealed)."""
    did = device_id
    if body.password or body.credential or body.cookies or body.client_sealed:
        _require_master_key(settings)

    email = body.email.strip().lower()
    update_body = AccountUpdate(
        email=body.email,
        provider=body.provider,
        password=body.password,
        credential=body.credential,
        tag=body.tag,
        note=body.note,
        proxy=body.proxy,
        sync_enabled=body.sync_enabled,
        cookies=body.cookies,
        client_sealed=body.client_sealed,
    )
    existing = (
        db.query(Account)
        .filter(Account.owner_user_id == did, Account.email == email)
        .first()
    )
    if existing is not None:
        # Upsert-style update when same device re-imports
        return _apply_update(
            existing,
            update_body,
            db=db,
            settings=settings,
        )

    # Client-sealed: server never sees plaintext secrets
    if body.client_sealed:
        try:
            reserve_cloud_account_slot(
                db,
                did,
                settings=settings,
                license_token=x_license_token,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=str(exc),
            ) from exc
        acc = Account(
            email=email,
            provider=_provider_from_body(body.provider),
            pool=AccountPool.user_private,
            owner_user_id=did,
            tag=body.tag,
            note=body.note,
            proxy=body.proxy,
            # hourly sync cannot decrypt client-sealed vault
            sync_enabled=False,
            status=AccountStatus.ok,
        )
        save_client_sealed(acc, body.client_sealed, settings=settings)
        db.add(acc)
        try:
            db.commit()
        except IntegrityError:
            # Reservation rolled back with the failed insert; re-sync counter.
            db.rollback()
            try:
                reconcile_cloud_account_used(db, did)
                db.commit()
            except Exception:
                db.rollback()
            winner = (
                db.query(Account)
                .filter(Account.owner_user_id == did, Account.email == email)
                .one()
            )
            return _apply_update(winner, update_body, db=db, settings=settings)
        db.refresh(acc)
        return _to_out(acc)

    cred = normalize_oauth_credential_fields(dict(body.credential or {})) if body.credential else None
    try:
        reserve_cloud_account_slot(
            db,
            did,
            settings=settings,
            license_token=x_license_token,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
        ) from exc
    acc = Account(
        email=email,
        provider=_provider_from_body(body.provider),
        pool=AccountPool.user_private,
        owner_user_id=did,
        tag=body.tag,
        note=body.note,
        proxy=body.proxy,
        sync_enabled=bool(body.sync_enabled),
        status=AccountStatus.ok,
    )
    if body.password:
        acc.password_enc = encrypt_str(body.password, settings=settings)
    if cred:
        acc.credential_enc = encrypt_json(cred, settings=settings)

    db.add(acc)
    try:
        db.flush()

        if body.cookies is not None:
            sess = AccountSession(
                account_id=acc.id,
                cookies_enc=encrypt_json(body.cookies, settings=settings),
                saved_at=datetime.now(timezone.utc),
                valid=True,
            )
            db.add(sess)

        db.commit()
    except IntegrityError:
        db.rollback()
        try:
            reconcile_cloud_account_used(db, did)
            db.commit()
        except Exception:
            db.rollback()
        winner = (
            db.query(Account)
            .filter(Account.owner_user_id == did, Account.email == email)
            .one()
        )
        return _apply_update(winner, update_body, db=db, settings=settings)
    db.refresh(acc)
    return _to_out(acc)


def _apply_update(
    acc: Account,
    body: AccountUpdate,
    *,
    db: Session,
    settings,
) -> AccountOut:
    if body.password or body.credential or body.cookies is not None or body.client_sealed:
        _require_master_key(settings)

    if body.email is not None:
        acc.email = body.email.strip().lower()
    if body.provider is not None:
        acc.provider = _provider_from_body(body.provider)
    if body.tag is not None:
        acc.tag = body.tag
    if body.note is not None:
        acc.note = body.note
    if body.proxy is not None:
        acc.proxy = body.proxy or None
    if body.status is not None:
        acc.status = body.status
    if body.client_sealed:
        save_client_sealed(acc, body.client_sealed, settings=settings)
        acc.sync_enabled = False
    else:
        if body.sync_enabled is not None:
            acc.sync_enabled = bool(body.sync_enabled)
        if body.password is not None:
            if body.password == "":
                acc.password_enc = None
            else:
                acc.password_enc = encrypt_str(body.password, settings=settings)
        if body.credential is not None:
            # Deep-merge so partial PATCH does not wipe other credential keys.
            existing_raw = load_credentials(acc, settings=settings)
            if is_client_sealed_blob(existing_raw):
                # Refuse accidental unseal via partial server-side credential patch.
                # Client must replace the sealed envelope with client_sealed, or
                # send credential={"_om_unwrap_sealed": true, ...} explicitly.
                if not body.credential.get("_om_unwrap_sealed"):
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=(
                            "account is client-sealed; send client_sealed to replace, "
                            "or set credential._om_unwrap_sealed=true to convert"
                        ),
                    )
                existing_raw = {}
            existing = normalize_oauth_credential_fields(dict(existing_raw or {}))
            incoming = normalize_oauth_credential_fields(dict(body.credential))
            incoming.pop("_om_unwrap_sealed", None)
            merged = {**existing, **incoming}
            # Empty string in the patch clears that key.
            for key, value in list(merged.items()):
                if value == "":
                    del merged[key]
            acc.credential_enc = encrypt_json(merged, settings=settings) if merged else None
        if body.cookies is not None:
            if acc.session is None:
                acc.session = AccountSession(account_id=acc.id)
                db.add(acc.session)
            acc.session.cookies_enc = encrypt_json(body.cookies, settings=settings)
            acc.session.saved_at = datetime.now(timezone.utc)
            acc.session.valid = True

    acc.updated_at = datetime.now(timezone.utc)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="account email already exists for this device",
        ) from exc
    db.refresh(acc)
    return _to_out(acc)


@router.get("/meta/quota")
def account_quota(
    db: DbDep,
    settings: SettingsDep,
    device_id: str = Depends(device_id_strict),
    x_license_token: str | None = Header(default=None, alias="X-License-Token"),
) -> dict:
    snap = quota_snapshot(
        device_id=device_id, license_token=x_license_token, settings=settings, db=db
    )
    used = _cloud_count(db, device_id)
    snap["cloud_used"] = used
    return snap


@router.get("/{account_id}", response_model=AccountOut)
def get_account(
    account_id: str,
    db: DbDep,
    device_id: str = Depends(device_id_strict),
) -> AccountOut:
    return _to_out(_get_owned(db, account_id, device_id))


@router.patch("/{account_id}", response_model=AccountOut)
def update_account(
    account_id: str,
    body: AccountUpdate,
    db: DbDep,
    settings: SettingsDep,
    device_id: str = Depends(device_id_strict),
) -> AccountOut:
    acc = _get_owned(db, account_id, device_id)
    return _apply_update(acc, body, db=db, settings=settings)


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_account(
    account_id: str,
    db: DbDep,
    device_id: str = Depends(device_id_strict),
) -> None:
    acc = _get_owned(db, account_id, device_id)
    # Flush delete first so concurrent reserves see the freed row; release
    # then decrements under a row lock (does not rewrite from COUNT).
    db.delete(acc)
    db.flush()
    release_cloud_account_slot(db, device_id)
    db.commit()
    return None
