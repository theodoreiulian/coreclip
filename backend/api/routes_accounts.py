import logging
import threading
from datetime import datetime, timedelta
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from config import config
from database import get_db
from models.account import Account
from models.schemas import AccountConnect, AccountResponse
from services import tiktok_browser

logger = logging.getLogger("clipper")
router = APIRouter()

SUPPORTED_PLATFORMS = ["tiktok", "instagram", "youtube", "snapchat"]

# In-flight TikTok login sessions: state_token -> {'path': str, 'handle': str|None, 'error': str|None}
_pending_logins: dict[str, dict] = {}
_pending_lock = threading.Lock()


def _finish_login(state: str, handle: str | None, error: str | None):
    with _pending_lock:
        _pending_logins[state] = {"handle": handle, "error": error}


@router.get("/accounts", response_model=List[AccountResponse])
def list_accounts(db: Session = Depends(get_db)):
    return db.query(Account).order_by(Account.platform, Account.created_at).all()


@router.post("/accounts/connect", response_model=AccountResponse)
def connect_account(payload: AccountConnect, db: Session = Depends(get_db)):
    """Connect a non-TikTok social account ('channel'). OAuth is stubbed for
    these platforms. TikTok is connected via browser login (see endpoints below).
    """
    platform = payload.platform.strip().lower()
    if platform not in SUPPORTED_PLATFORMS:
        raise HTTPException(status_code=400, detail=f"Unsupported platform '{platform}'")
    if platform == "tiktok":
        raise HTTPException(status_code=400, detail="Connect TikTok via POST /api/accounts/tiktok/login")

    name = payload.display_name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="display_name is required")

    account = db.query(Account).filter(
        Account.platform == platform, Account.display_name == name
    ).first()
    if account is None:
        account = Account(platform=platform, display_name=name)
        db.add(account)

    account.external_id = payload.external_id
    account.access_token = "stub-access-token"
    account.refresh_token = "stub-refresh-token"
    account.expires_at = datetime.utcnow() + timedelta(days=60)
    account.connected = True

    db.commit()
    db.refresh(account)
    return account


@router.delete("/accounts/{account_id}")
def disconnect_account(account_id: str, db: Session = Depends(get_db)):
    """Soft-disconnect: keeps the row so post history stays valid.
    For TikTok accounts also clears the session_path so the browser profile
    won't be reused."""
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    account.connected = False
    account.access_token = None
    account.refresh_token = None
    account.expires_at = None
    account.session_path = None
    db.commit()
    return {"status": "disconnected", "account_id": account_id}


# ── TikTok via browser login ───────────────────────────────────────────

class TikTokLoginStatusResponse(BaseModel):
    handle: str | None = None
    error: str | None = None


@router.get("/accounts/tiktok/config-status")
def tiktok_config_status():
    """Whether the Playwright + real Google Chrome stack is available for
    browser posting."""
    return {
        "configured": tiktok_browser.is_available(),
        "label": tiktok_browser.availability_label(),
    }


@router.post("/accounts/tiktok/login")
def tiktok_login_start():
    """Spawn a headful Google Chrome window for the user to log into TikTok.
    Returns a state token the frontend polls with /login-status."""
    if not tiktok_browser.is_available():
        raise HTTPException(status_code=400, detail=f"TikTok is not ready: {tiktok_browser.availability_label()}.")

    import secrets
    state = secrets.token_urlsafe(24)
    # Reuse one stable login profile across attempts so TikTok sees the same
    # browser on retry (cookies/anti-bot trust persist) rather than a brand-new
    # device each time — which triggers "Maximum number of attempts reached".
    session_path = tiktok_browser.active_login_path()

    with _pending_lock:
        _pending_logins[state] = {"handle": None, "error": None, "path": session_path}

    def _run():
        try:
            result = tiktok_browser.run_login(session_path, timeout_sec=300)
            _finish_login(state, result.get("handle"), None)
        except Exception as e:
            logger.error(f"TikTok login failed: {e}")
            _finish_login(state, None, str(e))

    threading.Thread(target=_run, daemon=True).start()
    return {"state": state, "session_path": session_path}


@router.post("/accounts/tiktok/connect-existing", response_model=AccountResponse)
def tiktok_connect_existing(db: Session = Depends(get_db)):
    """Connect a TikTok account by reusing the user's existing TikTok login from
    their real Google Chrome — copies the Chrome profile and drives real Chrome
    against the copy. No login form runs, so TikTok's automation block on the
    login flow never applies."""
    if not tiktok_browser.is_available():
        raise HTTPException(status_code=400, detail=f"TikTok is not ready: {tiktok_browser.availability_label()}.")

    try:
        result = tiktok_browser.connect_with_existing_chrome()
    except tiktok_browser.NotLoggedInError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # noqa: BLE001
        logger.error(f"TikTok connect-existing failed: {e}")
        raise HTTPException(status_code=400, detail=f"Could not connect TikTok: {e}")

    handle = result.get("handle")
    name = (handle or "").lstrip("@") or "tiktok-account"
    account = db.query(Account).filter(
        Account.platform == "tiktok", Account.display_name == name
    ).first()
    if account is None:
        account = Account(platform="tiktok", display_name=name)
        db.add(account)
    account.external_id = handle
    account.session_path = result["session_path"]
    account.connected = True
    db.commit()
    db.refresh(account)
    logger.info(f"Connected TikTok account {handle or name} by reusing Chrome login")
    return account


@router.get("/accounts/tiktok/login-status", response_model=TikTokLoginStatusResponse)
def tiktok_login_status(state: str, db: Session = Depends(get_db)):
    """Polled by the frontend while the login popup is open.
    On success (handle returned) an Account row is upserted."""
    with _pending_lock:
        entry = _pending_logins.get(state)
    if not entry:
        raise HTTPException(status_code=404, detail="Unknown or expired login session")

    if entry.get("error"):
        with _pending_lock:
            _pending_logins.pop(state, None)
        return TikTokLoginStatusResponse(error=entry["error"])

    handle = entry.get("handle")
    if handle:
        with _pending_lock:
            _pending_logins.pop(state, None)
        # Upsert account
        name = handle.lstrip("@")
        account = db.query(Account).filter(
            Account.platform == "tiktok", Account.display_name == name
        ).first()
        if account is None:
            account = Account(platform="tiktok", display_name=name)
            db.add(account)
        account.external_id = handle
        # Move the shared login profile into a stable per-account directory so
        # future logins/posts reuse this account's own trusted browser profile.
        account.session_path = tiktok_browser.adopt_login_profile(entry["path"], handle)
        account.connected = True
        db.commit()
        logger.info(f"Connected TikTok account {handle} (session={entry['path']})")
        return TikTokLoginStatusResponse(handle=handle)

    return TikTokLoginStatusResponse()