"""Re-encrypt at-rest blobs under the primary OPENMAIL_MASTER_KEY.

When OPENMAIL_MASTER_KEY_FALLBACKS is set, decrypt may use an old key.
This module rewrites password/credential/session ciphertext with the primary
key so fallbacks can later be removed.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.crypto import reencrypt_token
from app.models import Account, AccountSession

logger = logging.getLogger("openmail.crypto_migrate")


def reencrypt_account_row(account: Account, *, settings: Settings) -> dict[str, int]:
    """Re-encrypt one account's secrets. Returns counts of fields updated."""
    changed = {"password": 0, "credential": 0, "cookies": 0, "meta": 0, "failed": 0}

    if account.password_enc:
        new = reencrypt_token(account.password_enc, settings=settings)
        if new is None:
            changed["failed"] += 1
        else:
            account.password_enc = new
            changed["password"] = 1

    if account.credential_enc:
        new = reencrypt_token(account.credential_enc, settings=settings)
        if new is None:
            changed["failed"] += 1
        else:
            account.credential_enc = new
            changed["credential"] = 1

    sess: AccountSession | None = account.session
    if sess is not None:
        if sess.cookies_enc:
            new = reencrypt_token(sess.cookies_enc, settings=settings)
            if new is None:
                changed["failed"] += 1
            else:
                sess.cookies_enc = new
                changed["cookies"] = 1
        if sess.meta_enc:
            new = reencrypt_token(sess.meta_enc, settings=settings)
            if new is None:
                changed["failed"] += 1
            else:
                sess.meta_enc = new
                changed["meta"] = 1

    return changed


def migrate_reencrypt_all(db: Session, *, settings: Settings | None = None) -> dict[str, int]:
    """Walk all accounts and re-encrypt decryptable fields under primary key."""
    s = settings or get_settings()
    totals = {
        "accounts": 0,
        "password": 0,
        "credential": 0,
        "cookies": 0,
        "meta": 0,
        "failed": 0,
        "rows_touched": 0,
        "registry": 0,
    }
    # Skip migrate if no fallbacks configured (nothing to rewrite for rotation)
    fb = (getattr(s, "openmail_master_key_fallbacks", None) or "").strip()
    if not fb:
        logger.debug("crypto migrate: no OPENMAIL_MASTER_KEY_FALLBACKS; skip")
        return totals

    accounts = db.query(Account).all()
    for acc in accounts:
        totals["accounts"] += 1
        try:
            c = reencrypt_account_row(acc, settings=s)
        except Exception:
            logger.exception("reencrypt failed for account %s", acc.id)
            totals["failed"] += 1
            continue
        touched = c["password"] + c["credential"] + c["cookies"] + c["meta"]
        if touched:
            totals["rows_touched"] += 1
        for k in ("password", "credential", "cookies", "meta", "failed"):
            totals[k] += c[k]
    if totals["rows_touched"] or totals["failed"]:
        try:
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("reencrypt commit failed")
            raise
        logger.info(
            "crypto migrate: re-encrypted accounts_touched=%s password=%s "
            "credential=%s cookies=%s meta=%s failed_fields=%s",
            totals["rows_touched"],
            totals["password"],
            totals["credential"],
            totals["cookies"],
            totals["meta"],
            totals["failed"],
        )
    try:
        from app.services.device_auth import rewrite_registry_with_primary_key

        totals["registry"] = rewrite_registry_with_primary_key()
        if totals["registry"]:
            logger.info("crypto migrate: re-encrypted device_registry entries=%s", totals["registry"])
    except Exception:
        logger.exception("crypto migrate: device registry re-encrypt failed")
        raise
    return totals
