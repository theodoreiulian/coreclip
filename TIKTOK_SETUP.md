# Connecting TikTok to CoreClip (browser automation)

CoreClip posts to TikTok by driving the **real TikTok Studio uploader** in a
logged-in Chromium browser (via Playwright). No API, no developer app, no audit,
no quotas — free and unlimited.

At each scheduled post time, CoreClip opens TikTok Studio in a headless browser
session, uploads your clip, types the generated caption + hashtags, and **loads
it into TikTok's native scheduler** so that TikTok (not your laptop) publishes
at the exact time you chose. You never leave CoreClip.

---

## Setup (≈ 2 minutes, one time)

### 1. Install Chromium for Playwright

From the backend directory with the venv active:
```bash
cd backend
source venv/bin/activate
playwright install chromium
```

Or let `./start.sh` handle it (it runs this automatically on first venv setup).

### 2. Connect your TikTok account in CoreClip

1. Run CoreClip (`./start.sh` from `coreclip/`).
2. Open the posting UI for a job that's `ready_for_review`.
3. Click **+ Connect TikTok**.
4. A Chromium window opens to TikTok's login page. Log in with your TikTok
   credentials (handle any 2FA/captcha yourself — this is the only manual step,
   ever).
5. CoreClip detects the login (a `sessionid` cookie appears), scrapes your
   handle from your profile page, and saves the session to
   `output/.tiktok_sessions/`.
6. The browser window closes. Your account appears in the posting UI.

That's the whole setup. Your TikTok session persists on disk — reconnect only
if you log out of TikTok elsewhere or the cookies expire.

---

## How posting works

1. You connect a TikTok account (browser login, one time) and approve clips.
2. You schedule them — one post per selected account per clip, at the times you
   pick. CoreClip's scheduler (`check_scheduled_posts`, every 60s) fires each
   post at its time.
3. CoreClip opens a **headless** browser using your saved session, navigates to
   TikTok Studio, uploads the clip, types the generated **caption + hashtags**,
   clicks the **Schedule** toggle, sets the date/time, and submits.
4. TikTok's own scheduler handles the actual publishing at the exact time —
   your laptop can even be closed.

All posting happens directly between your machine and TikTok. No third party
ever touches your video.

---

## Configuration (optional, in `.env`)

| Variable | Default | Description |
|----------|---------|-------------|
| `TIKTOK_SESSION_DIR` | `./output/.tiktok_sessions` | Where browser profiles are stored |
| `TIKTOK_HEADLESS` | `true` | Set to `false` to watch the automation in a visible window |
| `TIKTOK_SCHEDULE_HORIZON_DAYS` | `10` | TikTok accepts schedule times up to this many days out |
| `TIKTOK_MIN_SCHEDULE_LEAD_MIN` | `20` | Minimum lead time for scheduling (TikTok requires ~15-20 min) |

---

## Selector tuning

TikTok changes its upload UI occasionally. If automation stops working, the
selectors are centralized in `backend/services/tiktok_browser.py` under the
`SELECTORS` dict — each key has multiple fallback selectors tried in order.

**Debugging a failure:**
1. Set `TIKTOK_HEADLESS=false` in `.env` and restart the backend.
2. Re-run the failed post. Watch the browser window — note what element isn't
   found.
3. Check `output/.tiktok_debug/` for the failure screenshot.
4. Add a new fallback selector to the relevant key in `SELECTORS`.

---

## Trade-offs (honest)

- **You log in once yourself** (including 2FA) — after that, it's automated.
- **Selector fragility**: TikTok changes their UI, selectors break. Fallbacks
  and screenshots minimize the pain; it's easy to patch.
- **TikTok ToS**: driving the UI with a browser technically violates TikTok's
  automation policy. You're posting your own content to your own account at
  natural pacing, which is low-risk, but it's a gray area.
- **Your TikTok session lives on your machine** (under `output/.tiktok_sessions/`,
  which is gitignored). Treat that directory as sensitive.

---

## Troubleshooting

- **"+ Connect TikTok" is greyed out** → Playwright/Chromium isn't installed.
  Run `playwright install chromium` in the backend venv and restart.
- **Login pops open a browser, nothing happens** → complete the login in the
  browser window. CoreClip polls for a `sessionid` cookie — once you're logged
  in, it auto-detects.
- **A post fails with "session expired"** → your TikTok cookies expired or you
  logged out elsewhere. Reconnect in the UI.
- **A post fails with a selector error + screenshot** → TikTok changed their
  upload page. Use `TIKTOK_HEADLESS=false` to watch, then add a fallback
  selector. See "Selector tuning" above.