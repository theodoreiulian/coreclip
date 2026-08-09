"""Headful diagnostic — capture TikTok Studio's REAL hashtag-autosuggest DOM.

Run from backend/ (venv active):  TIKTOK_HEADLESS=false python diag_hashtag.py

It reuses a connected account session + an existing clip, opens a VISIBLE Chrome,
uploads the clip, types '#comedy' character-by-character, and dumps every visible
element whose text starts with the typed prefix (the dynamic autosuggest rows) plus
the static suggestion widget — so we can see the real dropdown selector, how a
committed hashtag entity is marked up, and which commit gesture works.

It NEVER clicks Post. It closes the browser at the end. Nothing is published.
"""
import logging
import os
import sys
import time

# Surface the 'clipper' logger (used inside tiktok_browser) to stdout so we can see
# the per-tag popover rows + commit decisions during the diagnostic.
_h = logging.StreamHandler(sys.stdout)
_h.setFormatter(logging.Formatter("%(message)s"))
_clip = logging.getLogger("clipper")
_clip.addHandler(_h)
_clip.setLevel(logging.INFO)
_clip.propagate = False

from config import config
from database import SessionLocal
from models.account import Account
from models.clip import Clip
from services import tiktok_browser as tb

PREFIX = "comedy"          # we type '#comedy' and watch what TikTok suggests
WORD = "#" + PREFIX
DBG = tb._debug_dir()


def _shot(page, name):
    try:
        p = os.path.join(DBG, f"diag_{name}_{int(time.time()*1000)}.png")
        page.screenshot(path=p)
        print(f"   [screenshot] {p}")
    except Exception as e:
        print(f"   [screenshot failed: {e}]")


# Find any visible-ish element whose normalized text STARTS WITH `prefix` (the
# dynamic suggestion rows), excluding the giant caption node. Returns the ancestor
# class chain so we can derive a stable selector for the real dropdown.
_FIND_PREFIX_JS = r"""
(prefix) => {
  const res = [];
  for (const el of document.querySelectorAll('*')) {
    if (el.children.length > 2) continue;                 // leaf-ish rows only
    const t = (el.textContent || '').trim();
    if (!t) continue;
    const norm = t.toLowerCase().replace(/[^a-z0-9]/g, '');
    if (!norm.startsWith(prefix)) continue;
    if (norm.length > prefix.length + 30) continue;        // skip the whole caption
    const r = el.getBoundingClientRect();
    const st = getComputedStyle(el);
    const vis = r.width > 1 && r.height > 1 && st.display !== 'none'
                && st.visibility !== 'hidden' && st.opacity !== '0';
    const chain = [];
    let p = el;
    for (let i = 0; i < 6 && p; i++, p = p.parentElement) {
      chain.push((p.tagName||'').toLowerCase()
        + (p.getAttribute('role') ? `[role=${p.getAttribute('role')}]` : '')
        + (typeof p.className === 'string' && p.className ? '.' + p.className.trim().split(/\s+/).join('.') : ''));
    }
    res.push({ text: t.slice(0, 50), vis, rect: { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) },
               chain, html: el.outerHTML.slice(0, 400) });
  }
  return res;
}
"""

# Dump the static widget the buggy code matched, for comparison.
_STATIC_JS = r"""
() => {
  const rows = [];
  for (const el of document.querySelectorAll("[class*='suggestion'] [class*='item']")) {
    const r = el.getBoundingClientRect();
    rows.push({ text: (el.textContent||'').trim().slice(0,40), vis: r.width>1&&r.height>1, cls: (typeof el.className==='string'?el.className:'').slice(0,80) });
  }
  return rows;
}
"""


def _editor_html(page):
    try:
        return page.evaluate(
            """() => {
                const sels = ["div.public-DraftEditor-content","div[contenteditable='true']","[data-contents='true']"];
                for (const s of sels) for (const el of document.querySelectorAll(s)) {
                  const r = el.getBoundingClientRect();
                  if (r.width>0 && r.height>0) return el.innerHTML;
                }
                return '(editor not found)';
            }"""
        )
    except Exception as e:
        return f"(editor html error: {e})"


def _popover_report(page):
    """Precise check for the REAL autosuggest dropdown (mention-list-popover →
    hashtag-suggestion-item rows with a .hash-tag-topic). Works in any mode."""
    try:
        info = page.evaluate(
            """() => {
                const pop = document.querySelector("div.mention-list-popover");
                const rows = [...document.querySelectorAll(
                    "div.mention-list-popover .hashtag-suggestion-item, div.mention-list-popover [role='option']")];
                const topics = rows.map(r => {
                    const t = r.querySelector(".hash-tag-topic");
                    const rr = r.getBoundingClientRect();
                    return { topic: ((t ? t.textContent : r.textContent) || "").trim().slice(0, 30),
                             vis: rr.width > 1 && rr.height > 1 };
                });
                const cb = document.querySelector("div[role='combobox'].public-DraftEditor-content");
                return { popoverPresent: !!pop, rowCount: rows.length,
                         topics: topics.slice(0, 8).map(t => t.topic),
                         ariaExpanded: cb ? cb.getAttribute("aria-expanded") : null };
            }"""
        )
        print(f"   POPOVER: present={info['popoverPresent']} rows={info['rowCount']} "
              f"aria-expanded={info['ariaExpanded']} topics={info['topics']}")
        return info
    except Exception as e:
        print(f"   popover-report error: {e}")
        return None


def _scan(page, label):
    print(f"\n── scan @ {label} ──")
    try:
        hits = page.evaluate(_FIND_PREFIX_JS, PREFIX)
    except Exception as e:
        print(f"   prefix-scan error: {e}")
        hits = []
    if not hits:
        print(f"   NO element whose text starts with '{PREFIX}' → dynamic autosuggest NOT present")
    for h in hits:
        print(f"   vis={h['vis']} text={h['text']!r} rect={h['rect']}")
        print(f"      chain: {' < '.join(h['chain'])}")
        print(f"      html : {h['html']}")
    try:
        static = page.evaluate(_STATIC_JS)
        vis_static = [s for s in static if s['vis']]
        print(f"   [static '[class*=suggestion] [class*=item]' widget]: {len(static)} rows ({len(vis_static)} visible): "
              f"{[s['text'] for s in static[:8]]}")
    except Exception as e:
        print(f"   static-scan error: {e}")


def main():
    db = SessionLocal()
    acct = (db.query(Account)
            .filter(Account.platform == "tiktok", Account.session_path.isnot(None), Account.session_path != "")
            .first())
    clip = (db.query(Clip).filter(Clip.final_clip_path.isnot(None))
            .order_by(Clip.created_at.desc()).first())
    db.close()
    if not acct:
        print("No connected TikTok account with a session_path."); return
    session_path = acct.session_path
    video = clip.final_clip_path if clip else None
    print(f"account=@{acct.display_name}  session={session_path}")
    print(f"clip={video}")
    if not video or not os.path.exists(video):
        print("No usable clip file on disk."); return
    if not os.path.isdir(session_path):
        print(f"Session dir missing: {session_path}"); return

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        headless = os.getenv("DIAG_HEADLESS", "false").lower() == "true"
        print(f"launching (headless={headless})…")
        ctx = tb._launch_persistent(p, session_path, headless=headless)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            print("opening uploader…")
            page.goto(tb.UPLOAD_URL, wait_until="domcontentloaded", timeout=60000)
            if tb._on_login_page(page):
                print("session redirected to /login — reconnect the account."); return

            # captcha: it's a visible window, give the user time to solve.
            for _ in range(80):
                if not tb._has_captcha(page):
                    break
                print("   captcha visible — solve it in the window…")
                page.wait_for_timeout(1500)

            print("uploading clip…")
            page.set_input_files(tb._first(page, "file_input", state="attached"), video)
            tb._first(page, "caption_editor", timeout=120000)
            page.wait_for_timeout(2000)
            tb._dismiss_overlays(page)

            if os.getenv("DIAG_VERIFY", "false").lower() == "true":
                # Exercise the REAL shipped code path end-to-end (still no Post).
                cap = os.getenv("DIAG_CAP", "😂😂 #funny #comedy #debate #travel #music")
                print(f"\n>>> VERIFY: running real _set_caption({cap!r})")
                tb._set_caption(page, cap)
                page.wait_for_timeout(600)
                info = page.evaluate(
                    """() => {
                        const ents = [...document.querySelectorAll("span.mention")].map(s => (s.textContent||'').trim());
                        const ed = document.querySelector("div.public-DraftEditor-content");
                        return { entities: ents, text: ed ? (ed.innerText||'').trim() : '' };
                    }""")
                print(f"   committed entities ({len(info['entities'])}): {info['entities']}")
                print(f"   caption text: {info['text']!r}")
                _shot(page, "verify")
                print("\nDONE (verify) — NOT posting.")
                page.wait_for_timeout(1000)
                return

            sel = tb._first(page, "caption_editor")
            page.click(sel)
            page.keyboard.press("Control+A"); page.keyboard.press("Meta+A"); page.keyboard.press("Delete")
            print(f"editor cleared; text now={tb._editor_text(page)!r}")
            _scan(page, "before typing")

            # Type '#comedy' char-by-char, then poll for the REAL autosuggest popover.
            page.keyboard.type("#"); page.wait_for_timeout(250)
            for ch in PREFIX:
                page.keyboard.type(ch)
                page.wait_for_timeout(250)
            rep = None
            for _ in range(10):                    # poll up to ~3s for the async popover
                rep = _popover_report(page)
                if rep and rep.get("popoverPresent") and rep.get("rowCount"):
                    break
                page.wait_for_timeout(300)
            _shot(page, "after_typed")
            print(f"\nEDITOR HTML (plain, after typing): {_editor_html(page)[:500]}")

            # CLICK the exact-match row — the real commit method we'll ship.
            print("\n>>> CLICK commit on the exact '#%s' row" % PREFIX)
            rows = page.query_selector_all(
                "div.mention-list-popover .hashtag-suggestion-item, div.mention-list-popover [role='option']")
            print(f"   found {len(rows)} popover rows")
            target = None
            for r in rows:
                te = r.query_selector(".hash-tag-topic")
                label = ((te.inner_text() if te else r.inner_text()) or "").strip()
                norm = label.lstrip("#").replace(" ", "").lower()
                print(f"   row label={label!r} norm={norm!r}{'  <-- EXACT' if norm == PREFIX else ''}")
                if norm == PREFIX and target is None:
                    target = r
            if target:
                target.click(); page.wait_for_timeout(700)
                print("   clicked exact row")
            else:
                print("   NO exact row found to click")
            _shot(page, "after_click")
            print(f"EDITOR HTML (after click): {_editor_html(page)[:600]}")
            ent = page.evaluate(
                "() => { const s = document.querySelector(\"span.mention, [data-testid='mentionText']\");"
                " return s ? {cls: s.className, testid: s.getAttribute('data-testid'),"
                " text: (s.textContent||'').trim().slice(0,30)} : null; }")
            print(f"   committed-entity check (span.mention / [data-testid=mentionText]): {ent}")

            print("\nDONE — inspect the window, then it will close. (NOT posting.)")
            page.wait_for_timeout(4000)
        finally:
            ctx.close()


if __name__ == "__main__":
    main()
