"""TikTok posting by driving the real TikTok Studio web uploader (Playwright).

No API, no developer app, no audit. Each account is a persistent browser
profile (cookies on disk) that we log into once; afterwards CoreClip opens that
profile headless and publishes the clip immediately via TikTok's normal Post
button. CoreClip's own beat scheduler decides *when* a clip is due (see
tasks.celery_tasks.check_scheduled_posts); at that moment we just post it, so we
never touch TikTok's fragile native date/time picker.

IMPORTANT — selector fragility: TikTok changes its upload UI without notice, so
the CSS/role selectors below are best-effort and centralized in SELECTORS for
easy patching. Run with TIKTOK_HEADLESS=false to watch the automation, and check
the screenshots written to the debug dir on failure.
"""
import json
import logging
import os
import re
import time
from datetime import datetime

from config import config

logger = logging.getLogger("clipper")

# Hashtag-commit timing (env-tunable). TikTok's autosuggest API is rate/timing sensitive
# when tags are typed in quick succession, so we settle before each tag, wait for the
# async popover, and re-fire the query a few times if it doesn't show.
_HASHTAG_SETTLE_MS = int(os.getenv("TIKTOK_TAG_SETTLE_MS", "400"))
_HASHTAG_POPOVER_S = float(os.getenv("TIKTOK_TAG_POPOVER_S", "3.5"))
_HASHTAG_RETRIES = int(os.getenv("TIKTOK_TAG_RETRIES", "2"))

LOGIN_URL = "https://www.tiktok.com/login"
UPLOAD_URL = "https://www.tiktok.com/tiktokstudio/upload"
PROFILE_URL = "https://www.tiktok.com/profile"

# A realistic desktop UA. The bundled Playwright Chromium otherwise advertises a
# headless/automation-flavored UA that TikTok's login flags as a bot.
_DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# JS injected before any page script runs, to hide the most obvious automation
# signal (navigator.webdriver === true).
_STEALTH_JS = "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"

# Each entry is a list of fallback selectors tried in order.
SELECTORS = {
    "file_input": ["input[type=file]"],
    "caption_editor": [
        "div[contenteditable='true']",
        "div.public-DraftEditor-content",
        "[data-contents='true']",
    ],
    "post_button": [
        "[data-e2e='post_video_button']",
        "button:has-text('Post')",
    ],
    "success_marker": ["text=Your video is being uploaded", "text=Manage your posts"],
    # The popover TikTok shows while you type a '#tag' / '@mention'. We wait for
    # it (and click the matching row) so hashtags register as real, clickable
    # entities instead of plain text. Best-effort & patchable like the rest —
    # run with TIKTOK_HEADLESS=false if hashtags stop registering.
    # The dynamic hashtag autosuggest popover (captured live 2026-06-16): a body-level
    # portal `div.mention-list-popover` holding `div.hashtag-suggestion-item[role=option]`
    # rows, each with the clean tag in `span.hash-tag-topic` and the view-count separate.
    # Scoped to the popover container so we never again match the static "suggested
    # hashtags" widget that earlier guesses (e.g. `[class*=suggestion] [class*=item]`)
    # wrongly picked up — that bug left/dropped every tag as plain text.
    "hashtag_suggestion": [
        "div.mention-list-popover div.hashtag-suggestion-item",
        "div.mention-list-popover [role='option']",
        ".hashtag-suggestion-item[role='option']",
    ],
}


class TikTokBrowserError(Exception):
    pass


class NotLoggedInError(TikTokBrowserError):
    """The session expired/was revoked — the account must reconnect."""


def playwright_available() -> bool:
    """True if the Playwright Python package is importable."""
    try:
        import playwright  # noqa: F401
        return True
    except Exception:
        return False


# Common install locations of the user's *real* Google Chrome, per OS. We drive
# this (channel='chrome'), not the bundled Chromium, so it must be present.
_CHROME_PATHS = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",  # macOS
    "/usr/bin/google-chrome",  # Linux
    "/usr/bin/google-chrome-stable",
    "/opt/google/chrome/chrome",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",  # Windows
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


def chrome_available() -> bool:
    """True if the user's real Google Chrome appears to be installed."""
    import shutil
    if any(os.path.exists(p) for p in _CHROME_PATHS):
        return True
    return bool(shutil.which("google-chrome") or shutil.which("google-chrome-stable")
                or shutil.which("chrome"))


def is_available() -> bool:
    """True only if both prerequisites for TikTok browser automation are met:
    the Playwright package AND the user's real Google Chrome."""
    return playwright_available() and chrome_available()


def availability_label() -> str:
    """Human-readable status for the UI: what (if anything) is missing."""
    if not playwright_available():
        return "playwright not installed"
    if not chrome_available():
        return "Google Chrome not installed"
    return "ready"


def _debug_dir() -> str:
    d = os.path.join(config.OUTPUT_DIR, ".tiktok_debug")
    os.makedirs(d, exist_ok=True)
    return d


def new_session_path() -> str:
    """Allocate a fresh persistent-profile directory for a new account."""
    import uuid
    base = config.TIKTOK_SESSION_DIR
    os.makedirs(base, exist_ok=True)
    path = os.path.join(base, uuid.uuid4().hex)
    os.makedirs(path, exist_ok=True)
    return path


def active_login_path() -> str:
    """A STABLE profile dir reused across interactive login attempts.

    Minting a brand-new profile per attempt (the old behavior) makes TikTok see
    a fresh, cookie-less, automation-flagged browser every single time, which is
    a fast path to 'Maximum number of attempts reached'. Reusing one profile lets
    cookies / anti-bot trust accumulate so retries look like the same browser.
    On success the profile is adopted into a per-account dir (see
    adopt_login_profile)."""
    base = config.TIKTOK_SESSION_DIR
    path = os.path.join(base, "_login")
    os.makedirs(path, exist_ok=True)
    return path


def adopt_login_profile(login_path: str, handle: str) -> str:
    """After a successful login, move the shared login profile to a stable
    per-account directory and return it. If the move isn't possible, fall back
    to the login path as-is."""
    import shutil
    base = config.TIKTOK_SESSION_DIR
    safe = "".join(c for c in (handle or "").lstrip("@") if c.isalnum() or c in ("-", "_"))
    if not safe:
        return login_path
    target = os.path.join(base, safe)
    if os.path.abspath(target) == os.path.abspath(login_path):
        return login_path
    try:
        if os.path.exists(target):
            shutil.rmtree(target, ignore_errors=True)
        os.rename(login_path, target)
        return target
    except OSError:
        return login_path


def _launch_persistent(p, session_path: str, headless: bool):
    """Launch a persistent context in the user's REAL installed Google Chrome.

    We deliberately drive the user's own Chrome (channel='chrome') rather than
    Playwright's bundled Chromium / "Chrome for Testing" build, which TikTok
    flags hardest as automation. If real Chrome isn't installed we raise a clear
    error instead of silently falling back to the flagged build."""
    kwargs = dict(
        user_data_dir=session_path,
        headless=headless,
        args=["--no-first-run", "--disable-blink-features=AutomationControlled"],
        # Drop the automation switch AND Playwright's keychain-bypass flags:
        # on macOS Chrome encrypts cookies with a key in the real Keychain, so
        # `--password-store=basic` / `--use-mock-keychain` would make it unable
        # to decrypt cookies inherited from the user's real Chrome profile (the
        # session would look logged-out). We must let Chrome use the real
        # Keychain (may trigger a one-time "Allow" prompt).
        ignore_default_args=["--enable-automation", "--use-mock-keychain", "--password-store=basic"],
        user_agent=_DEFAULT_UA,
        viewport={"width": 1280, "height": 800},
    )
    try:
        context = p.chromium.launch_persistent_context(channel="chrome", **kwargs)
    except Exception as e:  # noqa: BLE001
        raise TikTokBrowserError(
            "Google Chrome is required for TikTok but couldn't be launched. "
            "Install Google Chrome from https://www.google.com/chrome and try "
            f"again. ({e})"
        )
    try:
        context.add_init_script(_STEALTH_JS)
    except Exception:
        pass
    return context


# Natural-language description per SELECTORS key that is a *visible actionable*
# element, used by the multimodal fallback when the CSS selectors drift. Keys
# absent here (file_input — hidden; success_marker — a confirmation check, not a
# target) deliberately stay selector-only.
_VISION_HINTS = {
    "caption_editor": "the large text box where the video caption/description is typed",
    "post_button": "the primary button that publishes the video (labelled 'Post')",
    # Not reached via _first (the popover is an optional step, not a required one).
    # Centralized here so _suggestion_rows_vision can self-heal popover-row detection
    # the same way the other elements do when their CSS selectors drift.
    "hashtag_suggestion": (
        "a row inside the hashtag suggestion dropdown that pops up while typing a "
        "'#tag' in the caption box (each row shows a hashtag name and a post/view count)"
    ),
}

# Runs in the page: tags each visible interactive element with a temporary
# data-coreclip-cand index and returns a compact description list. Tagging real
# DOM nodes (rather than asking the model to invent a selector) guarantees the
# selector we hand back, [data-coreclip-cand='N'], actually resolves.
_CANDIDATE_JS = """
() => {
  const sel = "button, a[role=button], [role=button], [role=radio], [role=switch], input, textarea, [contenteditable='true'], div[role], li, [role='option'], [role='listitem'], [role='menuitem']";
  const out = [];
  let i = 0;
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect();
    const st = getComputedStyle(el);
    if (r.width < 1 || r.height < 1) continue;
    if (st.display === 'none' || st.visibility === 'hidden' || st.opacity === '0') continue;
    if (r.bottom < 0 || r.right < 0 || r.top > (window.innerHeight + 600)) continue;
    el.setAttribute('data-coreclip-cand', i);
    out.push({
      i,
      tag: el.tagName.toLowerCase(),
      role: el.getAttribute('role') || '',
      type: el.getAttribute('type') || '',
      text: (el.innerText || el.value || el.getAttribute('placeholder') || el.getAttribute('aria-label') || '').trim().slice(0, 80),
      rect: {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)},
    });
    i++;
  }
  return out;
}
"""


def _locate_with_vision(page, description: str) -> str | None:
    """When the hardcoded selectors miss, screenshot the page and ask
    GEMINI_VISION_MODEL which tagged candidate matches `description`. Returns a
    [data-coreclip-cand='N'] selector or None. Never raises."""
    if not config.TIKTOK_VISION_FALLBACK or not config.GEMINI_API_KEY:
        logger.debug("Vision fallback disabled (TIKTOK_VISION_FALLBACK off or no GEMINI_API_KEY)")
        return None
    try:
        import json
        from google.genai import types
        from services.gemini_client import gemini_client

        cands = page.evaluate(_CANDIDATE_JS)
        if not cands:
            logger.debug("Vision fallback: no candidate elements found on the page")
            return None
        png = page.screenshot()
        img = types.Part.from_bytes(data=png, mime_type="image/png")
        prompt = (
            "You are locating one UI element on the TikTok Studio upload page.\n"
            f"Target: {description}\n"
            "Each candidate below is a real element tagged with data-coreclip-cand=index.\n"
            f"Candidates:\n{json.dumps(cands)}\n"
            "Return the index of the single best matching candidate, or -1 if none match."
        )
        res = gemini_client.call(
            system_prompt="You map a described UI element to one candidate by index. Reply JSON only.",
            user_message=[prompt, img],
            model=config.GEMINI_VISION_MODEL,
            temperature=0.0,
            response_schema={
                "type": "object",
                "properties": {"index": {"type": "integer"}, "reason": {"type": "string"}},
                "required": ["index"],
            },
        )
        idx = res.get("index", -1)
        if isinstance(idx, int) and 0 <= idx < len(cands):
            logger.info(
                f"Gemini-vision picked candidate {idx} ('{cands[idx].get('text','')}') "
                f"— {res.get('reason','')}"
            )
            return f"[data-coreclip-cand='{idx}']"
        logger.info(f"Gemini-vision found no candidate matching: {description}")
        return None
    except Exception as e:  # noqa: BLE001
        logger.warning(f"TikTok vision fallback failed: {e}")
        return None


def _first(page, key, timeout=15000, state="visible"):
    """Return the first selector from SELECTORS[key] that resolves, else raise.

    Defaults to state='visible' so we only return elements that are actually
    interactable — TikTok ships hidden duplicate contenteditable divs / buttons,
    and returning an attached-but-invisible one makes the later click hang for
    its full timeout ('waiting for element to be visible'). Pass state='attached'
    for elements that are meant to stay hidden (e.g. the file input).

    On total selector failure, an actionable element (one with a _VISION_HINTS
    entry) gets one self-healing attempt via the multimodal fallback before we
    give up."""
    last_err = None
    for sel in SELECTORS[key]:
        try:
            page.wait_for_selector(sel, timeout=timeout, state=state)
            return sel
        except Exception as e:  # noqa: BLE001
            last_err = e

    vision_note = "no vision fallback for this element"
    hint = _VISION_HINTS.get(key)
    if hint:
        sel = _locate_with_vision(page, hint)
        if sel:
            try:
                page.wait_for_selector(sel, timeout=5000, state=state)
                logger.warning(
                    f"TikTok selectors for '{key}' failed; using Gemini-vision fallback {sel}"
                )
                return sel
            except Exception as e:  # noqa: BLE001
                last_err = e
                vision_note = f"vision fallback element never became {state}"
        else:
            vision_note = "vision fallback found no match"

    raise TikTokBrowserError(
        f"None of the {key} selectors matched ({state}); {vision_note}: {last_err}"
    )


def _has_session_cookie(context) -> bool:
    for c in context.cookies():
        if c.get("name") == "sessionid" and c.get("value"):
            return True
    return False


def _scrape_handle(page) -> str | None:
    try:
        page.goto(PROFILE_URL, wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(1500)
        url = page.url
        if "/@" in url:
            return "@" + url.split("/@", 1)[1].split("/")[0].split("?")[0]
    except Exception as e:  # noqa: BLE001
        logger.warning(f"TikTok handle scrape failed: {e}")
    return None


# ── Connect (interactive login) ───────────────────────────────────────

def run_login(session_path: str, timeout_sec: int = 300) -> dict:
    """Open a *headful* browser for the user to log into TikTok, then persist
    the session. Blocks until login is detected or it times out. Returns
    {'handle': ..., }. Intended to run in a background thread."""
    from playwright.sync_api import sync_playwright

    os.makedirs(session_path, exist_ok=True)
    with sync_playwright() as p:
        context = _launch_persistent(p, session_path, headless=False)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(LOGIN_URL, wait_until="domcontentloaded")

            deadline = time.time() + timeout_sec
            while time.time() < deadline:
                if _has_session_cookie(context):
                    handle = _scrape_handle(page)
                    # Soft sanity check: a real interactive login sets the full
                    # cookie set, so the uploader should open without bouncing to
                    # /login. If it doesn't, log it (don't fail) as an early hint.
                    try:
                        page.goto(UPLOAD_URL, wait_until="domcontentloaded", timeout=30000)
                        page.wait_for_timeout(2000)
                        if _on_login_page(page):
                            logger.warning(
                                "TikTok login captured but TikTok Studio still "
                                "redirected to /login — posting may fail."
                            )
                    except Exception as e:  # noqa: BLE001
                        logger.warning(f"TikTok Studio post-login probe failed: {e}")
                    logger.info(f"TikTok login detected ({handle or 'handle unknown'})")
                    return {"handle": handle}
                time.sleep(2)
            raise TikTokBrowserError("Timed out waiting for TikTok login")
        finally:
            context.close()


def _on_login_page(page) -> bool:
    return "/login" in (page.url or "")


# Selectors that indicate TikTok's "verify you're human" captcha is on screen.
_CAPTCHA_SELECTORS = [
    "div.captcha_verify_container",
    "div[class*='captcha_verify']",
    "div[class*='captcha-verify']",
    "div[id*='captcha-verify']",
    "#captcha_container",
    "iframe[src*='captcha']",
]


class _CaptchaRequired(Exception):
    """Internal: a captcha is blocking the headless flow; hand off to the user."""


def _has_captcha(page) -> bool:
    """True if TikTok's captcha / verification widget is visible on the page."""
    for sel in _CAPTCHA_SELECTORS:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                return True
        except Exception:  # noqa: BLE001
            pass
    return False


def _wait_out_captcha_headful(session_path: str, timeout_sec: int = 180) -> bool:
    """Open the account's profile in a VISIBLE Chrome window so the user can
    solve a TikTok captcha, then return once it's cleared. Returns True if solved
    (captcha gone), False on timeout / login redirect. The solved state persists
    in the profile's cookies, so the caller can resume headless afterwards."""
    from playwright.sync_api import sync_playwright

    logger.warning(
        "TikTok captcha detected — opening a Chrome window for you to solve it. "
        "Posting resumes automatically once it's cleared."
    )
    with sync_playwright() as p:
        context = _launch_persistent(p, session_path, headless=False)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            try:
                page.goto(UPLOAD_URL, wait_until="domcontentloaded", timeout=60000)
            except Exception:  # noqa: BLE001
                pass
            try:
                page.bring_to_front()
            except Exception:  # noqa: BLE001
                pass
            deadline = time.time() + timeout_sec
            while time.time() < deadline:
                if _on_login_page(page):
                    return False
                if not _has_captcha(page):
                    # Confirm it stays gone (not just mid-transition).
                    page.wait_for_timeout(1500)
                    if not _has_captcha(page):
                        logger.info("TikTok captcha cleared — resuming.")
                        return True
                page.wait_for_timeout(1500)
            logger.warning("TikTok captcha was not solved within the time limit.")
            return False
        finally:
            context.close()


# ── Connect by reusing the user's existing Chrome login ───────────────

def chrome_user_data_dir() -> str | None:
    """Locate the user's real Google Chrome user-data directory, per OS."""
    home = os.path.expanduser("~")
    candidates = [
        os.path.join(home, "Library", "Application Support", "Google", "Chrome"),  # macOS
        os.path.join(home, ".config", "google-chrome"),  # Linux
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "User Data"),  # Windows
    ]
    for c in candidates:
        if c and os.path.isdir(c):
            return c
    return None


def _profile_has_tiktok(profile_dir: str) -> bool:
    """True if this Chrome profile holds a TikTok sessionid cookie. Reads only
    the plaintext host_key/name columns — cookie values stay encrypted."""
    import sqlite3
    for ck in (os.path.join(profile_dir, "Network", "Cookies"),
               os.path.join(profile_dir, "Cookies")):
        if not os.path.exists(ck):
            continue
        try:
            con = sqlite3.connect(f"file:{ck}?mode=ro&immutable=1", uri=True)
            row = con.execute(
                "SELECT 1 FROM cookies WHERE host_key LIKE '%tiktok.com' "
                "AND name='sessionid' LIMIT 1"
            ).fetchone()
            con.close()
            if row:
                return True
        except Exception:  # noqa: BLE001
            pass
    return False


def _pick_chrome_profile(user_data_dir: str) -> str | None:
    """Pick the Chrome profile to reuse: the first one logged into TikTok, else
    'Default' (or the first profile present) as a fallback."""
    import glob
    names = ["Default"] + sorted(
        os.path.basename(p) for p in glob.glob(os.path.join(user_data_dir, "Profile *"))
    )
    fallback = None
    for name in names:
        prof_dir = os.path.join(user_data_dir, name)
        if not os.path.isdir(prof_dir):
            continue
        if fallback is None:
            fallback = name
        if _profile_has_tiktok(prof_dir):
            return name
    return fallback


def _mark_profile_clean(default_dir: str):
    """Prevent the 'Chrome didn't shut down correctly / Restore pages?' prompt
    that appears because we copied a live profile."""
    import json
    prefs = os.path.join(default_dir, "Preferences")
    try:
        with open(prefs, "r", encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("profile", {})
        data["profile"]["exit_type"] = "Normal"
        data["profile"]["exited_cleanly"] = True
        with open(prefs, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:  # noqa: BLE001
        pass


def _copy_chrome_profile(user_data_dir: str, profile: str, dest: str):
    """Build a launchable user-data-dir copy at `dest`: `Local State` plus the
    chosen profile placed as 'Default', minus heavy caches."""
    import shutil
    ignore = shutil.ignore_patterns(
        "Cache", "Code Cache", "GPUCache", "DawnCache", "GraphiteDawnCache",
        "ShaderCache", "Service Worker", "Default Cache", "Crashpad", "*.log",
    )
    os.makedirs(dest, exist_ok=True)
    ls = os.path.join(user_data_dir, "Local State")
    if os.path.exists(ls):
        shutil.copy2(ls, os.path.join(dest, "Local State"))
    shutil.copytree(
        os.path.join(user_data_dir, profile),
        os.path.join(dest, "Default"),
        ignore=ignore,
        dirs_exist_ok=True,
    )
    _mark_profile_clean(os.path.join(dest, "Default"))


def connect_with_existing_chrome(session_path: str | None = None) -> dict:
    """Reuse the TikTok session from the user's real Chrome: copy their profile
    into a CoreClip-managed user-data-dir and drive real Chrome against the copy.
    No login form runs, so TikTok's automation block on the login flow never
    applies. Verification is headful so any one-time macOS Keychain prompt (to
    decrypt Chrome's cookies) can be answered. Returns {'handle', 'session_path'}.
    """
    from playwright.sync_api import sync_playwright
    import shutil

    udd = chrome_user_data_dir()
    if not udd:
        raise TikTokBrowserError(
            "Couldn't find your Google Chrome profile. Make sure Google Chrome "
            "is installed and you've logged into TikTok in it at least once."
        )
    profile = _pick_chrome_profile(udd)
    if not profile or not _profile_has_tiktok(os.path.join(udd, profile)):
        raise NotLoggedInError(
            "No TikTok login found in your Chrome. Open tiktok.com in Google "
            "Chrome, log in, then try connecting again."
        )

    if session_path is None:
        session_path = new_session_path()

    try:
        _copy_chrome_profile(udd, profile, session_path)
    except Exception as e:  # noqa: BLE001
        shutil.rmtree(session_path, ignore_errors=True)
        raise TikTokBrowserError(f"Couldn't copy your Chrome profile: {e}")

    handle = None
    studio_ok = False
    try:
        with sync_playwright() as p:
            # Headful so a Keychain 'Allow' prompt (if any) is answerable and the
            # user can see the already-logged-in session.
            context = _launch_persistent(p, session_path, headless=False)
            try:
                page = context.pages[0] if context.pages else context.new_page()
                try:
                    page.goto(UPLOAD_URL, wait_until="domcontentloaded", timeout=45000)
                    page.wait_for_timeout(3000)
                    # The window is visible — if TikTok throws a captcha, give the
                    # user time to solve it before judging the session.
                    if _has_captcha(page):
                        logger.warning("TikTok captcha during connect — solve it in the window.")
                        deadline = time.time() + 180
                        while time.time() < deadline and _has_captcha(page) and not _on_login_page(page):
                            page.wait_for_timeout(1500)
                    studio_ok = not _on_login_page(page) and not _has_captcha(page)
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"TikTok Studio probe failed during connect: {e}")
                handle = _scrape_handle(page)
            finally:
                context.close()
    except Exception as e:  # noqa: BLE001
        shutil.rmtree(session_path, ignore_errors=True)
        raise TikTokBrowserError(f"Could not open TikTok with your Chrome session: {e}")

    if not studio_ok:
        shutil.rmtree(session_path, ignore_errors=True)
        raise NotLoggedInError(
            "Your Chrome session didn't grant access to TikTok Studio. Make sure "
            "you're logged into TikTok in Google Chrome — and try quitting Chrome "
            "first so its cookies can be read — then connect again."
        )

    logger.info(
        f"TikTok connected by reusing Chrome profile '{profile}' "
        f"({handle or 'handle unknown'})"
    )
    return {"handle": handle, "session_path": session_path}


# ── Posting ───────────────────────────────────────────────────────────

# Dismiss-only controls for the modals/coachmarks TikTok Studio pops over the
# uploader (e.g. "Turn on automatic content checks?", "New editing features
# added"). Ordered safest-first; we never click the affirmative action
# ("Turn on") so we don't change the user's account settings.
_DISMISS_SELECTORS = [
    # react-joyride onboarding tour — close it via its own Skip/Close controls
    # (never 'primary'/Next, so we don't advance a multi-step tour). Safe: these
    # only end the client-side product tour, they don't touch account settings.
    "#react-joyride-portal [data-action='skip']",
    "#react-joyride-portal [data-action='close']",
    "#react-joyride-portal [aria-label*='lose']",   # Close (X)
    "#react-joyride-portal [aria-label*='kip']",     # Skip
    "div[role='dialog'] [aria-label*='lose']",   # the X close icon (Close/close)
    "[data-e2e='modal-close']",
    "button:has-text('Got it')",
    "button:has-text('Maybe later')",
    "button:has-text('Not now')",
    "button:has-text('Skip')",
    "button:has-text('Dismiss')",
    "button:has-text('Cancel')",                 # content-check modal's dismiss
]


def _overlay_present(page) -> bool:
    """True if a modal/popover overlay is currently covering the uploader."""
    for sel in (".TUXModal-overlay", "div[role='dialog']", "[data-floating-ui-portal] button",
                "#react-joyride-portal", ".react-joyride__overlay"):
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                return True
        except Exception:  # noqa: BLE001
            pass
    return False


def _dismiss_overlays(page) -> None:
    """Close TikTok's pop-ups so they stop intercepting clicks on the form.
    Clicks dismiss-only controls and falls back to Escape; safe to call when no
    overlay is present."""
    for _ in range(5):
        if not _overlay_present(page):
            return
        acted = False
        for sel in _DISMISS_SELECTORS:
            try:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    logger.info("Dismissing a TikTok pop-up that was covering the form…")
                    el.click(timeout=2000)
                    acted = True
                    page.wait_for_timeout(400)
                    break
            except Exception:  # noqa: BLE001
                pass
        if not acted:
            try:
                page.keyboard.press("Escape")
            except Exception:  # noqa: BLE001
                pass
            page.wait_for_timeout(400)

    # Last resort: a stuck react-joyride tour keeps eating clicks. Remove its
    # portal node so the form is reachable (client-side onboarding only — this
    # can't change the user's TikTok account).
    if _overlay_present(page):
        try:
            removed = page.evaluate(
                "() => { const n = document.getElementById('react-joyride-portal');"
                " if (n) { n.remove(); return true; } return false; }"
            )
            if removed:
                logger.info("Removed a stuck TikTok onboarding-tour overlay so the form is clickable.")
                page.wait_for_timeout(300)
        except Exception:  # noqa: BLE001
            pass


# Module-level memo of the popover-row selector that last matched. The class/role
# that works is stable within a TikTok UI version, so caching it avoids re-scanning
# every selector for every tag. Always re-validated by row text before we click, so
# a stale memo can't cause a wrong commit.
_popover_row_selector = None


def _norm_tag(s: str) -> str:
    """Normalize a hashtag (or a suggestion-row label) for exact comparison: drop
    leading '#'(s), remove spaces, lowercase. So '#Funny ' == 'funny', but
    'fyp' != 'fypシ' and 'fyp' != 'fypage' — we never silently swap in a variant."""
    return (s or "").strip().lstrip("#").replace(" ", "").lower()


def _row_tag_token(raw: str) -> str:
    """Extract the hashtag token from a suggestion row's text, stripping the
    trailing view/post count TikTok appends ('comedy 1.2B posts' or
    'comedy\\n523.4K videos' → 'comedy'), then normalize via _norm_tag. Robust to
    the count sitting on the SAME line as the tag with no newline separator — the
    bug that made every exact-match comparison fail and tags get dropped."""
    line = ""
    for ln in (raw or "").splitlines():
        if ln.strip():
            line = ln.strip()
            break
    line = re.sub(
        r"[\s·•|-]*\d[\d.,]*\s*[KMB]?\s*(?:posts?|views?|videos?)?\s*$",
        "",
        line,
        flags=re.IGNORECASE,
    )
    return _norm_tag(line)


def _editor_text(page) -> str:
    """Plain-text contents of the *visible* caption editor (TikTok ships hidden
    duplicate contenteditables, so pick the one with a real bounding box). Used to
    confirm the editor cleared and to undo a just-typed tag deterministically."""
    try:
        return page.evaluate(
            """() => {
                const sels = ["div.public-DraftEditor-content","div[contenteditable='true']","[data-contents='true']"];
                for (const s of sels) {
                  for (const el of document.querySelectorAll(s)) {
                    const r = el.getBoundingClientRect();
                    if (r.width > 0 && r.height > 0) return el.innerText || el.textContent || '';
                  }
                }
                return '';
            }"""
        ) or ""
    except Exception:  # noqa: BLE001
        return ""


def _rewind_editor_to(page, target: str, max_presses: int = 48) -> bool:
    """Backspace one character at a time until the editor's text is back to
    `target`. Used to undo a just-typed tag we're dropping or retrying —
    deterministic regardless of whether a suggestion click inserted an entity of a
    different length. Bounded so a representation mismatch can't eat the caption.
    Returns True once it matches."""
    for _ in range(max_presses):
        if _editor_text(page) == target:
            return True
        page.keyboard.press("Backspace")
        page.wait_for_timeout(20)
    return _editor_text(page) == target


def _tag_is_entity(page, tag: str) -> bool:
    """True if `tag` currently renders as a committed hashtag entity (a bold/clickable
    link) in the caption editor rather than plain text. Markup-agnostic: TikTok's exact
    classes drift, so we accept any DraftJS entity signal — an <a>/[data-entity-key]
    wrapper, a mention/hashtag class, or text whose color/weight differs from the
    editor's plain-text baseline. A false negative here only triggers a harmless retry
    or plain-text fallback; it never causes a drop (drops are decided by popover state)."""
    want = _norm_tag(tag)
    if not want:
        return False
    try:
        return bool(page.evaluate(
            """(want) => {
                const norm = s => (s||'').trim().replace(/^#+/, '').replace(/\\s+/g,'').toLowerCase();
                const edSel = ["div.public-DraftEditor-content","div[contenteditable='true']","[data-contents='true']"];
                let editor = null;
                for (const s of edSel) {
                  for (const el of document.querySelectorAll(s)) {
                    const r = el.getBoundingClientRect();
                    if (r.width > 0 && r.height > 0) { editor = el; break; }
                  }
                  if (editor) break;
                }
                if (!editor) return false;
                const base = getComputedStyle(editor).color;
                for (const el of editor.querySelectorAll("a, span, [data-entity-key], [data-offset-key]")) {
                  if (norm(el.textContent) !== want) continue;
                  if (el.closest("a")) return true;
                  if (el.closest("[data-entity-key]")) return true;
                  if (el.closest("[data-testid='mentionText']")) return true;
                  if (el.closest(".mention")) return true;
                  let p = el;
                  for (let i = 0; i < 4 && p; i++, p = p.parentElement) {
                    const c = p.className;
                    if (c && typeof c === "string" && /(mention|hashtag|hash[-_]?tag|anchor)/i.test(c)) return true;
                  }
                  const cs = getComputedStyle(el);
                  if (cs.color && cs.color !== base) return true;
                  const fw = parseInt(cs.fontWeight, 10);
                  if (!isNaN(fw) && fw >= 600) return true;
                }
                return false;
            }""",
            want,
        ))
    except Exception:  # noqa: BLE001
        return False


def _verify_entity(page, word: str, timeout_ms: int = 1500, poll_ms: int = 150) -> bool:
    """Poll _tag_is_entity until it's true or the timeout elapses. The DraftJS entity can
    take a beat to render after a commit, so a single immediate check produces false
    negatives — which previously drove a destructive rewind/retry that could demote a
    genuinely-committed tag back to plain text."""
    deadline = time.time() + timeout_ms / 1000
    while True:
        if _tag_is_entity(page, word):
            return True
        if time.time() >= deadline:
            return False
        page.wait_for_timeout(poll_ms)


def _diag_write(record: dict) -> None:
    """Append one diagnostic JSON line to the debug dir. Unlike the session logs (which
    start.sh truncates on every boot), this persists across restarts, so a hashtag that
    wouldn't commit stays diagnosable after the fact even when posting runs headless."""
    try:
        record = {"ts": datetime.utcnow().isoformat(timespec="seconds"), **record}
        with open(os.path.join(_debug_dir(), "hashtag_diag.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass


def _screenshot_debug(page, label: str) -> None:
    """Best-effort screenshot into the debug dir so a tag that wouldn't register is
    diagnosable from logs alone (posting runs headless)."""
    try:
        path = os.path.join(_debug_dir(), f"{label}_{int(time.time() * 1000)}.png")
        page.screenshot(path=path)
        logger.warning(f"  (debug screenshot: {path})")
    except Exception:  # noqa: BLE001
        pass


def _suggestion_rows_css(page):
    """Visible popover rows as (element, normalized_first_line) via CSS selectors.
    Tries the memoized selector first, then SELECTORS['hashtag_suggestion'], caching
    whichever matches. Returns [] if no popover is showing. Non-raising."""
    global _popover_row_selector
    sels = ([_popover_row_selector] if _popover_row_selector else []) + SELECTORS["hashtag_suggestion"]
    for sel in sels:
        try:
            items = page.query_selector_all(sel)
        except Exception:  # noqa: BLE001
            continue
        rows = []
        for it in items:
            try:
                if not it.is_visible():
                    continue
                # Prefer the clean tag from the row's topic span ('#comedy'); the
                # full inner_text also carries the count ('#comedy\n926.8M posts'),
                # which _row_tag_token strips as a fallback.
                topic = it.query_selector(".hash-tag-topic")
                raw = ((topic.inner_text() if topic else it.inner_text()) or "").strip()
                if raw:
                    rows.append((it, raw))
            except Exception:  # noqa: BLE001
                pass
        if rows:
            _popover_row_selector = sel
            return rows
    return []


def _suggestion_rows_vision(page):
    """Last-resort popover detection when every CSS selector misses: ask
    GEMINI_VISION_MODEL to point at a suggestion row. Returns at most one best-guess
    (element, label). Non-raising; [] if disabled or nothing matches."""
    vsel = _locate_with_vision(page, _VISION_HINTS["hashtag_suggestion"])
    if not vsel:
        return []
    try:
        it = page.query_selector(vsel)
        if it and it.is_visible():
            raw = (it.inner_text() or "").strip()
            if raw:
                return [(it, raw)]
    except Exception:  # noqa: BLE001
        pass
    return []


def _wait_for_suggestion_rows(page, state, want, deadline_s: float = _HASHTAG_POPOVER_S):
    """Poll for autosuggest rows that belong to the CURRENT tag — i.e. at least one row
    whose tag token starts with `want`. This (a) waits out the async popover, (b) ignores
    a STALE popover still showing the previous tag's results for a moment right after a
    commit (the race that left some tags plain), and (c) can never mistake the static
    'suggested hashtags' widget for the autosuggest. Falls back to a budgeted Gemini-vision
    probe. Returns (rows, from_vision); `rows` may be stale/empty (the caller logs them) if
    nothing matching appears before the deadline."""
    deadline = time.time() + deadline_s
    last = []
    while time.time() < deadline:
        rows = _suggestion_rows_css(page)
        if rows:
            last = rows
            if any(_row_tag_token(raw).startswith(want) for (_, raw) in rows):
                return rows, False
        page.wait_for_timeout(120)
    if state.get("vision_budget", 0) > 0:
        state["vision_budget"] -= 1
        v = _suggestion_rows_vision(page)
        if v:
            return v, True
    return last, False


def _attempt_commit(page, word: str, state) -> str:
    """One detect→click-exact-row→verify pass. Returns 'committed', 'unverified', or
    'plain'. We NEVER remove a tag: if we can't confidently commit it as an entity we
    leave it as plain text (a plain hashtag is far better than a missing one — dropping
    once produced emoji-only captions). The actual popover row texts are logged so
    real-world TikTok drift is visible from the logs alone."""
    want = _norm_tag(word)
    rows, from_vision = _wait_for_suggestion_rows(page, state, want)
    if not rows:
        try:
            dbg = page.evaluate(
                "() => { const p = document.querySelector('div.mention-list-popover');"
                " const cb = document.querySelector(\"div[role='combobox'].public-DraftEditor-content\");"
                " return { pop: !!p,"
                " items: p ? p.querySelectorAll('.hashtag-suggestion-item, [role=option]').length : 0,"
                " aria: cb ? cb.getAttribute('aria-expanded') : null,"
                " popHTML: p ? p.outerHTML.slice(0, 180) : null }; }")
        except Exception:  # noqa: BLE001
            dbg = None
        logger.info(f"    no fresh popover rows for {word}; state={dbg}")
        _diag_write({"word": word, "phase": "popover", "result": "no_rows", "dbg": dbg})
        return "plain"
    tokens = [(el, _row_tag_token(raw)) for (el, raw) in rows]
    sample = [tok for (_, tok) in tokens if tok][:8]
    logger.info(
        f"    popover for {word}: matched '{_popover_row_selector}'"
        f"{' (vision)' if from_vision else ''}, rows={sample}"
    )
    exact = next((el for (el, tok) in tokens if tok == want), None)
    if exact is None:
        _diag_write({"word": word, "phase": "popover", "result": "no_exact", "rows": sample})
        return "plain"
    # Commit. Prefer KEYBOARD ENTER when our exact tag is the focused row — which it is
    # when the full tag is typed (TikTok sorts the exact match to row 0, aria-selected).
    # Enter keeps the editor focused; CLICKING the popover (a body-level portal) blurs the
    # editor and kills the NEXT tag's autosuggest (the bug that left '#funny' plain). Fall
    # back to a click only when the exact match isn't the focused row.
    try:
        focused_is_exact = bool(exact.evaluate(
            "el => el.getAttribute('aria-selected') === 'true' "
            "|| /(^|\\s)focused(\\s|$)/.test(el.className || '')"))
    except Exception:  # noqa: BLE001
        focused_is_exact = False
    try:
        if focused_is_exact:
            page.keyboard.press("Enter")
        else:
            exact.click(timeout=2000)
        page.wait_for_timeout(200)
    except Exception:  # noqa: BLE001
        return "plain"
    return "committed" if _verify_entity(page, word) else "unverified"


def _type_tag_chars(page, word: str) -> None:
    """Type a '#tag' slowly, char-by-char, so TikTok's autosuggest arms and keeps up.
    Typing the token in a fast burst frequently leaves the editor's combobox at
    aria-expanded=false with no popover (TikTok debounces per keystroke); the '#' gets a
    longer pause to enter tag-suggest mode, then each letter is spaced out. This matches
    the cadence that reliably produced the popover in standalone tests."""
    for i, ch in enumerate(word):
        page.keyboard.type(ch)
        page.wait_for_timeout(280 if i == 0 else 160)


def _commit_with_retry(page, word: str, before: str, state: dict) -> str:
    """Attempt the popover commit; on failure retry ONCE after re-triggering the
    autosuggest. TikTok's dropdown sometimes doesn't fire on the first type of a tag
    right after the previous tag was committed (a debounce race that left tags like
    '#funny' plain) — re-firing the query (delete + retype the last char) brings it back.
    Returns 'committed' or 'plain'."""
    status = _attempt_commit(page, word, state)
    for _ in range(_HASHTAG_RETRIES):
        if status == "committed":
            break
        # Re-arm the autosuggest: clear the just-typed token (and any half-committed
        # entity) back to the pre-tag state, pause so TikTok's suggestion API isn't
        # hammered, then retype the whole '#word' (typing the '#' re-enters tag-suggest
        # mode — a trailing-char nudge does not).
        _rewind_editor_to(page, before, max_presses=len(word) + 8)
        page.wait_for_timeout(700)
        _type_tag_chars(page, word)
        status = _attempt_commit(page, word, state)
    return "committed" if status == "committed" else "plain"


def _commit_via_boundary(page, word: str, before: str) -> bool:
    """Popover-independent commit: rebuild a clean '#word' token and close it with a
    trailing space so the editor's DraftJS hashtag plugin entity-fies it locally — no
    autosuggest API call, so it survives the rate-limiting that leaves trailing tags
    uncommitted (the '#funny'/'#mataneven'-went-plain bug). The trailing space also
    serves as the separator to the next word (a single trailing one is trimmed by TikTok
    on post). Returns True once '#word' renders as a committed entity.

    A token boundary is the editor's *other* way to mint a hashtag entity besides
    accepting the popover; relying on it means a flaky/throttled autosuggest can no longer
    silently demote a tag to plain text."""
    # The popover retries may have left a stray newline/space (from an Enter pressed with
    # no open popover); reset to the pre-tag text so the rebuilt token is unambiguous.
    _rewind_editor_to(page, before, max_presses=len(word) + 16)
    page.keyboard.type(word)
    page.keyboard.type(" ")
    return _verify_entity(page, word)


def _type_hashtag(page, word: str, state: dict) -> None:
    """Type '#word' and make it a real (bold, clickable) hashtag entity (a `span.mention`).

    Primary path: accept TikTok's autosuggest popover (_commit_with_retry). Fallback when
    the popover doesn't fire or has no exact row — which is what happens to trailing tags
    once TikTok starts rate-limiting the autosuggest API after several '#' queries in quick
    succession (the bug that left '#funny'/'#mataneven' as plain text): _commit_via_boundary
    closes the token with a trailing space so the editor entity-fies it locally, no API
    needed. If BOTH paths fail the tag is left as PLAIN TEXT — never dropped (dropping once
    produced emoji-only captions). Every path leaves a single separating space after the
    tag (popover commit gets TikTok's auto-space; the boundary path types its own), so the
    caller never has to manage spacing around tags. `state` accumulates the per-caption
    counts for the summary log."""
    before = _editor_text(page)
    page.wait_for_timeout(_HASHTAG_SETTLE_MS)   # settle so the autosuggest API isn't hammered
    _type_tag_chars(page, word)

    if _commit_with_retry(page, word, before, state) == "committed":
        state["committed"] += 1
        logger.info(f"  hashtag {word} → committed as entity (popover)")
        # TikTok auto-inserts a space after a popover-committed entity; don't add our own
        # — doing so would double the gap between tags.
        return

    # Popover path gave up — try the popover-independent boundary close. It types the
    # separating space itself (kept whether or not it commits; a trailing one is trimmed).
    if _commit_via_boundary(page, word, before):
        state["committed"] += 1
        logger.info(f"  hashtag {word} → committed as entity (token boundary)")
        return

    state["plain"] += 1
    logger.info(f"  hashtag {word} → kept as plain text (not committed)")
    _diag_write({"word": word, "phase": "final", "result": "plain",
                 "editor_tail": _editor_text(page)[-160:]})
    if not state.get("shot"):
        _screenshot_debug(page, "hashtag_plain")
        state["shot"] = True


def _set_caption(page, caption: str):
    # Click the visible editor by selector (auto-waits for actionability) rather
    # than grabbing an ElementHandle that may resolve to a hidden duplicate.
    sel = _first(page, "caption_editor")
    page.click(sel)
    # Clear TikTok's auto-filled caption, then confirm it actually emptied so we
    # don't append our hashtags onto a leftover suggestion.
    page.keyboard.press("Control+A")
    page.keyboard.press("Meta+A")
    page.keyboard.press("Delete")
    if _editor_text(page).strip():
        page.keyboard.press("Control+A")
        page.keyboard.press("Meta+A")
        page.keyboard.press("Delete")
    # Type word-by-word so each '#hashtag' gets committed via TikTok's suggestion
    # popover (see _type_hashtag) and ends up bold + clickable. Non-hashtag words (the
    # leading emoji run) go in via insert_text, which inserts the string as a single
    # event and avoids multi-codepoint emoji being split into broken surrogate halves.
    state = {"committed": 0, "plain": 0, "vision_budget": 2}
    words = caption.split(" ")
    for idx, word in enumerate(words):
        is_last = idx == len(words) - 1
        if word.startswith("#") and len(word) > 1:
            _type_hashtag(page, word, state=state)
        else:
            page.keyboard.insert_text(word)
            if not is_last:
                page.keyboard.type(" ")
    # Dismiss any lingering suggestion popup so it can't intercept the Post click.
    page.keyboard.press("Escape")

    total = state["committed"] + state["plain"]
    if total:
        logger.info(
            f"TikTok caption: {state['committed']}/{total} hashtags committed as entities, "
            f"{state['plain']} left as plain text"
        )
        if state["committed"] == 0:
            logger.warning(
                "No hashtags registered as entities — none of the typed tags matched a "
                "suggestion-popover row. See the 'popover for #tag: rows=[...]' lines above "
                "for what TikTok actually offered: rows=[] means the popover wasn't detected "
                "(selectors drifted); rows with values but no exact match means the row-text "
                "parsing needs adjusting. Tags were kept as plain text (never dropped). "
                "Inspect with TIKTOK_HEADLESS=false."
            )


def _confirm_posted(page, upload_url: str, timeout_ms: int = 30000) -> bool:
    """True once the post is confirmed: a success marker shows, or TikTok leaves
    the uploader (it redirects to the content/manage page on a successful post).
    Polls up to `timeout_ms`. Used instead of trusting the click blindly, so a
    silently-rejected upload surfaces as a failure the caller can retry."""
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        for sel in SELECTORS["success_marker"]:
            try:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    return True
            except Exception:  # noqa: BLE001
                pass
        try:
            cur = page.url
            if cur and "/upload" not in cur and cur != upload_url:
                return True
        except Exception:  # noqa: BLE001
            pass
        page.wait_for_timeout(500)
    return False


def post_video(session_path: str, video_path: str, caption: str) -> dict:
    """Upload `video_path` and publish it immediately via TikTok's Post button.

    CoreClip's beat scheduler already decided this clip is due, so there's no
    native-schedule step — we just post now. Returns
    {'success': bool, 'url': str|None, 'error': str|None}."""
    from playwright.sync_api import sync_playwright

    if not os.path.isdir(session_path):
        return {"success": False, "url": None,
                "error": "TikTok session missing — reconnect the account"}

    # Posting runs headless. If TikTok throws a captcha, we can't solve it in a
    # hidden window — so we pop a VISIBLE window for the user to solve it, then
    # loop and retry the post headless (the solved state persists in cookies).
    # At most one headful hand-off per call to avoid spinning forever.
    captcha_msg = "TikTok kept showing a captcha — the next scheduled attempt will retry."
    captcha_solved_once = False
    while True:
        page = None
        with sync_playwright() as p:
            logger.info(f"Launching Chrome (headless={config.TIKTOK_HEADLESS})")
            context = _launch_persistent(p, session_path, headless=config.TIKTOK_HEADLESS)
            try:
                page = context.pages[0] if context.pages else context.new_page()
                logger.info("Opening TikTok Studio uploader…")
                page.goto(UPLOAD_URL, wait_until="domcontentloaded", timeout=60000)
                if _on_login_page(page):
                    raise NotLoggedInError("TikTok session expired — reconnect the account")

                # Captcha on load → hand off to the user (once).
                if _has_captcha(page):
                    if captcha_solved_once:
                        return {"success": False, "url": None, "error": captcha_msg}
                    logger.warning("Captcha detected on load — opening a window for you to solve it")
                    raise _CaptchaRequired()

                logger.info("Uploading video file…")
                page.set_input_files(_first(page, "file_input", state="attached"), video_path)
                # Wait for the editor to appear, signalling the upload was accepted.
                logger.info("Waiting for TikTok to accept/process the upload…")
                _first(page, "caption_editor", timeout=120000)
                page.wait_for_timeout(2000)

                # TikTok pops modals/coachmarks over the form (content-check
                # prompt, "new features") that intercept clicks — clear them.
                _dismiss_overlays(page)

                logger.info("Setting caption…")
                _set_caption(page, caption)

                # A pop-up can also surface right before submit.
                _dismiss_overlays(page)
                upload_url = page.url
                logger.info("Clicking the Post button…")
                page.click(_first(page, "post_button"))

                # A captcha can also appear on submit.
                page.wait_for_timeout(1500)
                if _has_captcha(page):
                    if captcha_solved_once:
                        return {"success": False, "url": None, "error": captcha_msg}
                    logger.warning("Captcha detected after submit — opening a window for you to solve it")
                    raise _CaptchaRequired()

                # Confirm the post actually landed rather than trusting the click:
                # TikTok shows a success marker or navigates off the uploader.
                logger.info("Confirming submission…")
                if not _confirm_posted(page, upload_url):
                    raise TikTokBrowserError(
                        "Clicked Post but TikTok never confirmed the upload "
                        "(no success marker and still on the uploader)."
                    )

                logger.info(f"TikTok posted via browser ({session_path})")
                return {"success": True, "url": None, "error": None}

            except _CaptchaRequired:
                pass  # handled below, after the headless context is closed
            except NotLoggedInError as e:
                return {"success": False, "url": None, "error": str(e)}
            except Exception as e:  # noqa: BLE001
                shot = os.path.join(_debug_dir(), f"fail_{int(time.time())}.png")
                try:
                    if page:
                        page.screenshot(path=shot)
                        logger.error(f"TikTok automation failed; screenshot at {shot}")
                except Exception:
                    pass
                return {"success": False, "url": None, "error": str(e)}
            finally:
                context.close()

        # Only reached via _CaptchaRequired: show a window for the user to solve
        # the captcha, then loop to retry the whole post headless.
        logger.info("Waiting for you to solve the captcha in the opened window…")
        solved = _wait_out_captcha_headful(session_path)
        captcha_solved_once = True
        if solved:
            logger.info("Captcha cleared — retrying the post in the background")
        if not solved:
            return {"success": False, "url": None,
                    "error": "A TikTok captcha needs solving — a window was opened but it "
                             "wasn't completed in time. The next scheduled attempt will retry."}
