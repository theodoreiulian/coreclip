"""Offline smoke checks for the TikTok browser-automation posting.
Run from backend/ (with the venv active):

    python test_tiktok.py

Covers pure logic: caption building, browser availability, and
the no-session error path. The live login + posting paths are
exercised manually per TIKTOK_SETUP.md (browser-based).
"""
import os
import tempfile

from config import config
from services import tiktok_browser
from services.platform_clients.tiktok import TikTokClient

_failures = []


def check(name, cond):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")
    if not cond:
        _failures.append(name)


def test_caption():
    print("caption building (laughing emojis + hashtags, no text):")
    from services.platform_clients.tiktok import LAUGH_EMOJIS
    c = TikTokClient()

    # Run many times to cover the random emoji count/choice.
    for _ in range(200):
        cap = c._build_caption({"description": "ignored text", "title": "also ignored",
                                "platform_variants": {"tiktok": "ignored variant"},
                                "hashtags": ["comedy", "funny", "officeprank", "standupcomedy", "brainrot"]})
        emoji_part, _, tag_part = cap.partition(" ")
        # Caption is one repeated laughing emoji, 2–4 of them.
        emojis = list(emoji_part)
        if not (2 <= len(emojis) <= 4):
            check(f"emoji count 2-4 (got {len(emojis)} in {cap!r})", False)
            break
        if any(e not in LAUGH_EMOJIS for e in emojis) or len(set(emojis)) != 1:
            check(f"only one laughing-emoji type repeated ({cap!r})", False)
            break
        # No leftover generated text — only hashtags follow the emojis.
        if any(tok and not tok.startswith("#") for tok in tag_part.split()):
            check(f"no text caption, only hashtags ({cap!r})", False)
            break
    else:
        check("2-4 laughing emojis + hashtags only (200 samples)", True)

    check("hashtags preserved and #-normalized",
          c._build_caption({"hashtags": ["comedy", "#funny", "brainrot"]}).count("#") == 3)
    long = c._build_caption({"hashtags": ["x" * 3000]})
    check("caption truncated to <=2200", len(long) <= 2200)


def test_assemble_hashtags():
    print("hashtag assembly (2 broad + 2 niche + 1 wildcard):")
    from pipeline.metadata_generation import _assemble_hashtags
    check("clamps to 4 model tags then appends wildcard",
          _assemble_hashtags(["#comedy", "funny", "#standupcomedy", "officeprank", "extra"], "brainrot")
          == ["comedy", "funny", "standupcomedy", "officeprank", "brainrot"])
    check("wildcard deduped case-insensitively",
          _assemble_hashtags(["comedy", "funny", "standup", "prank"], "Comedy")
          == ["comedy", "funny", "standup", "prank"])
    check("no wildcard -> just model tags",
          _assemble_hashtags(["comedy", "funny", "standup", "prank"], None)
          == ["comedy", "funny", "standup", "prank"])
    check("few tags still yields 3 (degrades gracefully)",
          _assemble_hashtags(["comedy", "funny"], "brainrot") == ["comedy", "funny", "brainrot"])


def test_hashtag_matcher():
    print("hashtag exact-match normalization (_norm_tag):")
    from services.tiktok_browser import _norm_tag
    # Same tag in different surface forms must compare equal (so '#Funny ' typed by
    # us matches the popover row labelled 'funny').
    check("'#Funny ' == 'funny'", _norm_tag("#Funny ") == _norm_tag("funny"))
    check("'##tag' -> 'tag' (strips all leading #)", _norm_tag("##tag") == "tag")
    check("internal spaces removed", _norm_tag("#stand up") == "standup")
    check("plain label normalizes", _norm_tag("comedy") == "comedy")
    # Distinct tags must NOT collide — this is what stops '#fyp' silently committing
    # a variant like '#fypシ'/'#fypage'.
    check("'#fyp' != '#fypシ'", _norm_tag("#fyp") != _norm_tag("#fypシ"))
    check("'#fyp' != 'fypage'", _norm_tag("#fyp") != _norm_tag("fypage"))
    check("blank/None -> ''", _norm_tag("  ") == "" and _norm_tag(None) == "")

    from services.tiktok_browser import _row_tag_token
    # Popover rows append a view/post count; the tag token must drop it or the
    # exact-match comparison fails (the bug that dropped every tag, incl. #comedy).
    check("'comedy 1.2B posts' -> 'comedy'", _row_tag_token("comedy 1.2B posts") == "comedy")
    check("'comedy1.2Bposts' (no space) -> 'comedy'", _row_tag_token("comedy1.2Bposts") == "comedy")
    check("'comedy\\n1.2B posts' -> 'comedy'", _row_tag_token("comedy\n1.2B posts") == "comedy")
    check("'#comedy' -> 'comedy'", _row_tag_token("#comedy") == "comedy")
    check("count with views suffix stripped", _row_tag_token("standupcomedy 523.4K views") == "standupcomedy")
    check("near-miss NOT collapsed ('comedyvideos' stays)", _row_tag_token("comedyvideos 5M posts") == "comedyvideos")
    check("digit-in-tag preserved", _row_tag_token("fyp2024 1.2B posts") == "fyp2024")


def test_browser_availability():
    print("browser module:")
    check("is_available returns bool", isinstance(tiktok_browser.is_available(), bool))
    check("new_session_path creates a directory",
          os.path.isdir(tiktok_browser.new_session_path()))


def test_no_session_path():
    print("no-session error paths:")
    c = TikTokClient()
    res = c.upload("/nonexistent.mp4", {"account_session_path": None, "description": "hi"})
    check("no session_path -> failed", res.status == "failed")
    check("error mentions reconnect",
          "reconnect" in (res.error_message or "").lower())

    res = c.upload("/nonexistent.mp4",
                   {"account_session_path": "/tmp/coreclip_nonexistent_session_xyz",
                    "description": "hi"})
    check("missing session dir -> failed", res.status == "failed")
    check("error is nonempty", bool(res.error_message))


if __name__ == "__main__":
    test_caption()
    test_assemble_hashtags()
    test_hashtag_matcher()
    test_browser_availability()
    test_no_session_path()
    print()
    if _failures:
        print(f"{len(_failures)} check(s) FAILED: {_failures}")
        raise SystemExit(1)
    print("All TikTok browser-automation offline checks passed.")