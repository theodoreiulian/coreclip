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
    print("caption building (generated caption text + hashtags):")
    c = TikTokClient()
    tags = ["comedy", "funny", "officeprank"]

    cap = c._build_caption({"description": "nah this got me",
                            "platform_variants": {"tiktok": "he said it with his whole chest"},
                            "hashtags": tags})
    check("tiktok variant wins over description",
          cap == "he said it with his whole chest #comedy #funny #officeprank")

    cap = c._build_caption({"description": "nah this got me", "hashtags": tags})
    check("falls back to description when no variant",
          cap == "nah this got me #comedy #funny #officeprank")

    cap = c._build_caption({"description": "nah this got me",
                            "platform_variants": {"tiktok": "   "}, "hashtags": tags})
    check("blank variant falls back to description", cap.startswith("nah this got me #"))

    check("text-only clip still posts its text",
          c._build_caption({"description": "just this"}) == "just this")
    check("tags-only clip still posts its tags",
          c._build_caption({"hashtags": tags}) == "#comedy #funny #officeprank")
    check("empty metadata -> empty caption", c._build_caption({}) == "")

    check("hashtags preserved and #-normalized",
          c._build_caption({"hashtags": ["comedy", "#funny", "brainrot"]}).count("#") == 3)
    check("stray '#' in text stripped (would type as a hashtag)",
          "#" not in c._build_caption({"description": "so #real"}).partition(" #")[0])

    # Overflow must cost the TEXT, never the tags — tags are the discovery path.
    long = c._build_caption({"description": "y" * 5000, "hashtags": tags})
    check("caption truncated to <=2200", len(long) <= 2200)
    check("all hashtags survive truncation", long.endswith("#comedy #funny #officeprank"))
    check("tags alone over budget still truncated",
          len(c._build_caption({"hashtags": ["x" * 3000]})) <= 2200)


def test_assemble_hashtags():
    print("hashtag assembly (ALWAYS exactly 5: content + person names + filler):")
    from pipeline.metadata_generation import (
        _assemble_hashtags, _name_to_tag, TAG_COUNT, FALLBACK_TAGS)

    check("clamps to 5 tags, strips leading '#'",
          _assemble_hashtags(["#comedy", "funny", "#standupcomedy", "officeprank", "extra"], [])
          == ["comedy", "funny", "standupcomedy", "officeprank", "extra"])
    check("person name takes the last slot",
          _assemble_hashtags(["comedy", "funny", "standupcomedy", "officeprank", "improv"], ["John Cena"])
          == ["comedy", "funny", "standupcomedy", "officeprank", "johncena"])
    check("name already present as content is deduped",
          _assemble_hashtags(["comedy", "funny", "standup", "prank", "improv"], ["Comedy"])
          == ["comedy", "funny", "standup", "prank", "improv"])
    check("names capped at 3, content keeps at least 2",
          _assemble_hashtags(["comedy", "funny"], ["A B", "C D", "E F", "G H"])
          == ["comedy", "funny", "ab", "cd", "ef"])
    check("possessive dropped from name ('Matan's' -> 'matan')", _name_to_tag("Matan's") == "matan")

    # The count is a hard guarantee — a short/empty model response must still
    # ship 5, topped up from FALLBACK_TAGS.
    check("4 content + no people is topped up to 5",
          _assemble_hashtags(["comedy", "funny", "officeprank", "standup"], [])
          == ["comedy", "funny", "officeprank", "standup", FALLBACK_TAGS[0]])
    check("model returned nothing -> all filler",
          _assemble_hashtags([], []) == FALLBACK_TAGS[:TAG_COUNT])

    edge_cases = [
        (["comedy", "funny", "officeprank", "standup"], []),
        (["comedy", "funny", "officeprank", "standup", "improv"], ["John Cena"]),
        (["comedy"], ["John Cena"]),
        (["comedy", "Comedy", "COMEDY"], []),
        ([], []),
        ([], ["John Cena"]),
        (["a", "b", "c", "d", "e", "f", "g"], []),
        (None, None),
    ]
    always_five = all(len(_assemble_hashtags(c, n)) == TAG_COUNT for c, n in edge_cases)
    no_dupes = all(
        len({t.lower() for t in _assemble_hashtags(c, n)}) == TAG_COUNT for c, n in edge_cases)
    check(f"ALWAYS exactly {TAG_COUNT} tags ({len(edge_cases)} edge cases)", always_five)
    check("never duplicates a tag", no_dupes)


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
                   {"account_session_path": os.path.join(tempfile.gettempdir(),
                                                         "coreclip_nonexistent_session_xyz"),
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