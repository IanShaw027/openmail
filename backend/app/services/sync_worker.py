"""Background SyncWorker: fetch non-sealed server accounts with sync_enabled.

Primary product path is browser vault + proxy fetch. This worker only helps
legacy/server-stored rows (not client_sealed). Mail search is local mailCache.
"""

from __future__ import annotations

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.config import Settings
from app.db import SessionLocal
from app.models import Account, AccountStatus, SyncRun
from app.services.fetch_service import fetch_account
from app.services.settings_service import (
    EffectiveSettings,
    as_settings_compatible,
    get_effective_settings,
)

logger = logging.getLogger("openmail.sync_worker")


def _safe_err(msg: str | None) -> str:
    from app.services.log_redact import redact_text

    return redact_text(msg)


def _try_file_lock() -> Any:
    """Cross-process lock so multiple workers/replicas don't sync the same accounts.

    Returns a held lock object, or None if lock unavailable / already held.
    """
    import os
    from pathlib import Path

    path = Path(os.environ.get("OPENMAIL_SYNC_LOCK", "data/sync_worker.lock"))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(path, "a+", encoding="utf-8")
    except OSError:
        return None
    try:
        import fcntl

        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        fh.seek(0)
        fh.truncate()
        fh.write(f"pid={os.getpid()}\n")
        fh.flush()
        return fh
    except BlockingIOError:
        fh.close()
        logger.info("SyncWorker: another process holds the lock; idle")
        return None
    except Exception:
        # Windows or no fcntl — fall back to in-process only
        try:
            fh.close()
        except Exception:
            pass
        return "nop"


class SyncWorker:
    """Thread-based background worker; one instance per process (+ file lock)."""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._running_cycle = False
        self._last_run: SyncRun | None = None
        self._last_started_at: datetime | None = None
        self._last_finished_at: datetime | None = None
        self._next_estimate: datetime | None = None
        self._manual_wake = threading.Event()
        self._file_lock: Any = None

    # ── lifecycle ──────────────────────────────────────────────────────

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._file_lock = _try_file_lock()
            if self._file_lock is None:
                # Another replica owns the lock — don't start loop
                logger.info("SyncWorker not started (lock held by peer)")
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._loop,
                name="openmail-sync-worker",
                daemon=True,
            )
            self._thread.start()
            logger.info("SyncWorker started")

    def stop(self, *, timeout: float = 5.0) -> None:
        self._stop.set()
        self._manual_wake.set()
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=timeout)
        fl = self._file_lock
        self._file_lock = None
        if fl is not None and fl != "nop":
            try:
                import fcntl

                fcntl.flock(fl.fileno(), fcntl.LOCK_UN)
                fl.close()
            except Exception:
                try:
                    fl.close()
                except Exception:
                    pass
        logger.info("SyncWorker stopped")

    @property
    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def status_snapshot(self, db: Session) -> dict[str, Any]:
        eff = get_effective_settings(db)
        last = (
            db.query(SyncRun)
            .order_by(SyncRun.started_at.desc())
            .first()
        )
        accounts = (
            db.query(Account)
            .filter(Account.sync_enabled.is_(True))
            .order_by(Account.email.asc())
            .all()
        )
        next_est = self._next_estimate
        if next_est is None and last and last.finished_at and eff.sync_enabled_global:
            # estimate from last finished + interval
            finished = last.finished_at
            if finished.tzinfo is None:
                finished = finished.replace(tzinfo=timezone.utc)
            from datetime import timedelta

            next_est = finished + timedelta(seconds=eff.sync_interval_seconds)

        return {
            "worker_alive": self.is_alive,
            "running_cycle": self._running_cycle,
            "sync_enabled_global": eff.sync_enabled_global,
            "sync_interval_seconds": eff.sync_interval_seconds,
            "sync_concurrency": eff.sync_concurrency,
            "sync_folders": eff.sync_folder_list,
            "next_estimate": next_est.isoformat() if next_est else None,
            "last_run": _sync_run_dict(last) if last else None,
            "accounts": [
                {
                    "id": a.id,
                    "email": a.email,
                    "sync_enabled": a.sync_enabled,
                    "last_sync_at": a.last_sync_at.isoformat() if a.last_sync_at else None,
                    "last_sync_error": a.last_sync_error,
                    "status": str(getattr(a.status, "value", a.status)),
                }
                for a in accounts
            ],
        }

    # ── triggers ───────────────────────────────────────────────────────

    def request_full_sync(self) -> None:
        """Wake the loop to run a cycle soon (or run immediately if idle)."""
        self._manual_wake.set()
        if not self._running_cycle:
            # Fire-and-forget background cycle for API triggers
            threading.Thread(
                target=self._safe_run_cycle,
                kwargs={"trigger": "manual"},
                name="openmail-sync-manual",
                daemon=True,
            ).start()

    def run_cycle_now(self, *, trigger: str = "manual") -> dict[str, Any]:
        """Synchronous full cycle (used by tests / blocking admin call)."""
        return self._run_cycle(trigger=trigger)

    def sync_one_account(
        self,
        account_id: str,
        *,
        force: bool = True,
        owner_user_id: str | None = None,
    ) -> dict[str, Any]:
        """Sync a single account (all configured folders)."""
        db = SessionLocal()
        try:
            acc = db.get(Account, account_id)
            if acc is None:
                return {"ok": False, "error": "account not found", "account_id": account_id}
            if owner_user_id is not None and acc.owner_user_id != owner_user_id:
                return {"ok": False, "error": "forbidden", "account_id": account_id}
            eff = get_effective_settings(db)
            settings = as_settings_compatible(eff)
            detail = _sync_account(db, acc, eff=eff, settings=settings, force=force)
            db.commit()
            return detail
        finally:
            db.close()

    def sync_user_accounts(self, user_id: str, *, force: bool = True) -> dict[str, Any]:
        """Sync all sync_enabled private accounts for a user."""
        db = SessionLocal()
        try:
            eff = get_effective_settings(db)
            settings = as_settings_compatible(eff)
            accounts = (
                db.query(Account)
                .filter(
                    Account.owner_user_id == user_id,
                    Account.sync_enabled.is_(True),
                    Account.status != AccountStatus.disabled,
                )
                .all()
            )
            details: list[dict[str, Any]] = []
            ok_c = fail_c = 0
            for acc in accounts:
                d = _sync_account(db, acc, eff=eff, settings=settings, force=force)
                details.append(d)
                if d.get("ok"):
                    ok_c += 1
                else:
                    fail_c += 1
            db.commit()
            return {
                "ok": fail_c == 0,
                "ok_count": ok_c,
                "fail_count": fail_c,
                "details": details,
            }
        finally:
            db.close()

    # ── internal loop ──────────────────────────────────────────────────

    def _loop(self) -> None:
        # Small initial delay so startup settles
        self._stop.wait(2.0)
        while not self._stop.is_set():
            db = SessionLocal()
            try:
                eff = get_effective_settings(db)
            except Exception:
                eff = get_effective_settings(None)
            finally:
                db.close()

            interval = max(60, int(eff.sync_interval_seconds or 3600))

            if eff.sync_enabled_global:
                self._safe_run_cycle(trigger="scheduled")
            else:
                logger.debug("SyncWorker: global sync disabled, skipping cycle")

            from datetime import timedelta

            self._next_estimate = datetime.now(timezone.utc) + timedelta(seconds=interval)

            # Sleep interval, but wake early on manual request or stop
            self._manual_wake.clear()
            woke = self._manual_wake.wait(timeout=interval)
            if self._stop.is_set():
                break
            if woke:
                # Manual wake already spawns its own thread; just continue loop timing
                continue

    def _safe_run_cycle(self, *, trigger: str = "scheduled") -> None:
        try:
            self._run_cycle(trigger=trigger)
        except Exception:
            logger.exception("SyncWorker cycle crashed (non-fatal)")

    def _run_cycle(self, *, trigger: str = "scheduled") -> dict[str, Any]:
        with self._lock:
            if self._running_cycle:
                return {"ok": False, "error": "cycle already running", "skipped": True}
            self._running_cycle = True

        started = datetime.now(timezone.utc)
        self._last_started_at = started
        ok_count = 0
        fail_count = 0
        details: list[dict[str, Any]] = []
        run_id: str | None = None

        db = SessionLocal()
        try:
            eff = get_effective_settings(db)
            settings = as_settings_compatible(eff)
            run = SyncRun(
                started_at=started,
                ok_count=0,
                fail_count=0,
                trigger=trigger,
            )
            db.add(run)
            db.commit()
            db.refresh(run)
            run_id = run.id

            accounts = (
                db.query(Account)
                .filter(
                    Account.sync_enabled.is_(True),
                    Account.status != AccountStatus.disabled,
                )
                .all()
            )
            account_ids = [a.id for a in accounts]
            concurrency = max(1, min(32, int(eff.sync_concurrency or 2)))
        finally:
            db.close()

        def _job(aid: str) -> dict[str, Any]:
            local = SessionLocal()
            try:
                acc = local.get(Account, aid)
                if acc is None:
                    return {"ok": False, "account_id": aid, "error": "missing"}
                local_eff = get_effective_settings(local)
                local_settings = as_settings_compatible(local_eff)
                detail = _sync_account(
                    local,
                    acc,
                    eff=local_eff,
                    settings=local_settings,
                    force=True,
                )
                local.commit()
                return detail
            except Exception as exc:  # noqa: BLE001
                local.rollback()
                return {
                    "ok": False,
                    "account_id": aid,
                    "error": f"{exc.__class__.__name__}: {exc}",
                }
            finally:
                local.close()

        if account_ids:
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                futures = {pool.submit(_job, aid): aid for aid in account_ids}
                for fut in as_completed(futures):
                    try:
                        d = fut.result()
                    except Exception as exc:  # noqa: BLE001
                        d = {
                            "ok": False,
                            "account_id": futures[fut],
                            "error": str(exc),
                        }
                    details.append(d)
                    if d.get("ok"):
                        ok_count += 1
                    else:
                        fail_count += 1

        finished = datetime.now(timezone.utc)
        self._last_finished_at = finished

        db2 = SessionLocal()
        try:
            run = db2.get(SyncRun, run_id) if run_id else None
            if run is not None:
                run.finished_at = finished
                run.ok_count = ok_count
                run.fail_count = fail_count
                run.detail_json = json.dumps(details[:200], default=str)
                db2.commit()
                self._last_run = run
        except Exception:
            logger.exception("Failed to finalize SyncRun")
            db2.rollback()
        finally:
            db2.close()

        with self._lock:
            self._running_cycle = False

        result = {
            "ok": fail_count == 0,
            "run_id": run_id,
            "ok_count": ok_count,
            "fail_count": fail_count,
            "started_at": started.isoformat(),
            "finished_at": finished.isoformat(),
            "details": details,
            "trigger": trigger,
        }
        logger.info(
            "SyncWorker cycle done trigger=%s ok=%s fail=%s",
            trigger,
            ok_count,
            fail_count,
        )
        return result


def _sync_account(
    db: Session,
    account: Account,
    *,
    eff: EffectiveSettings,
    settings: Settings,
    force: bool = True,
) -> dict[str, Any]:
    """Fetch configured folders for one account; update last_sync_* fields."""
    from app.services.credentials import is_client_sealed_blob, load_credentials

    # Client-sealed vault credentials: server cannot decrypt — skip silently
    try:
        creds = load_credentials(account, settings=settings)
        if is_client_sealed_blob(creds):
            now = datetime.now(timezone.utc)
            account.last_sync_at = now
            account.last_sync_error = "client_sealed: use browser proxy fetch"
            account.sync_enabled = False
            account.updated_at = now
            return {
                "ok": False,
                "skipped": True,
                "account_id": account.id,
                "email": account.email,
                "error": "client_sealed",
                "last_sync_at": now.isoformat(),
            }
    except Exception:
        pass

    folders = eff.sync_folder_list or ["inbox", "junk"]
    folder_results: list[dict[str, Any]] = []
    any_ok = False
    errors: list[str] = []

    for folder in folders:
        try:
            result = fetch_account(
                db,
                account,
                folder=folder,
                quick=True,
                force=force,
                settings=settings,
                use_cache=False,
            )
            folder_results.append(
                {
                    "folder": folder,
                    "ok": result.ok,
                    "message_count": result.message_count,
                    "error": result.error,
                }
            )
            if result.ok:
                any_ok = True
            elif result.error:
                errors.append(f"{folder}: {_safe_err(result.error)}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{folder}: {exc.__class__.__name__}: {_safe_err(str(exc))}")
            folder_results.append(
                {
                    "folder": folder,
                    "ok": False,
                    "error": f"{exc.__class__.__name__}: {_safe_err(str(exc))}",
                }
            )

    now = datetime.now(timezone.utc)
    account.last_sync_at = now
    if any_ok and not errors:
        account.last_sync_error = None
    elif errors:
        account.last_sync_error = "; ".join(errors)[:512]
    account.updated_at = now

    return {
        "ok": any_ok and not errors,
        "account_id": account.id,
        "email": account.email,
        "folders": folder_results,
        "error": account.last_sync_error,
        "last_sync_at": now.isoformat(),
    }


def _sync_run_dict(run: SyncRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "ok_count": run.ok_count,
        "fail_count": run.fail_count,
        "trigger": run.trigger,
        "detail_json": run.detail_json,
    }


# Process-wide singleton
_worker: SyncWorker | None = None
_worker_lock = threading.Lock()


def get_sync_worker() -> SyncWorker:
    global _worker
    with _worker_lock:
        if _worker is None:
            _worker = SyncWorker()
        return _worker


def start_sync_worker() -> SyncWorker:
    w = get_sync_worker()
    w.start()
    return w


def stop_sync_worker() -> None:
    global _worker
    with _worker_lock:
        if _worker is not None:
            _worker.stop()
