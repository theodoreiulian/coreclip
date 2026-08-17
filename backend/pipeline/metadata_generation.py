import asyncio
import json
import os
import re
import logging
from sqlalchemy.orm import Session
from models.job import Job
from models.clip import Clip
from services.gemini_client import gemini_client
from config import config

logger = logging.getLogger("clipper")

# Every clip ships exactly this many hashtags — no more, no fewer.
TAG_COUNT = 5
# Ceiling on person-name tags so a multi-person clip can't crowd out every
# content tag.
MAX_NAME_TAGS = 3
# Last-resort filler, only reached when the model returns fewer than TAG_COUNT
# usable tags. Broad discovery tags: generic enough to always be on-topic,
# which is the point — they're padding, not targeting.
FALLBACK_TAGS = ["fyp", "foryou", "viral", "fypage", "trending"]

METADATA_SYSTEM_PROMPT = """You are a social media growth expert. For each of the following video
clips, generate optimized posting metadata.

For each clip, provide:
1. "clip_index": The clip number (matching the input order)
2. "description": ONE short caption, written the way a real person actually
   types on their phone — NOT the way a copywriter writes. Aim for under 60
   characters, never more than ~100.

   STYLE RULES (these are strict):
   - all lowercase. never capitalize anything, not even names or "i".
   - NEVER use a period/full stop. no "." anywhere. no ellipses ("...").
   - the only punctuation allowed is commas, exclamation marks, and question
     marks. no colons, semicolons, dashes, quotes, or parentheses.
   - write it offhand and a little lazy, like a throwaway thought — not a
     polished sentence. sentence fragments are good.
   - no hooks, no hype, no calls to action, no "wait for it", no "POV:".
   - at most one emoji, and only if it genuinely fits.

   Good: "he really said that with his whole chest"
   Good: "nah this got me"
   Good: "why is this so accurate"
   Bad:  "This one caught me off guard." (capitalized, has a period)
3. "hashtags": An array of EXACTLY 5 hashtags (without the leading '#'),
   ordered broad first, then niche:
   - 2 BROAD / high-traffic tags: major category or massive community tags
     that cast a wide net (e.g. comedy, funny, fyp, standup).
   - 3 SPECIFIC / niche tags: highly targeted, lower-competition tags (think
     under ~1M views) that precisely describe THIS clip's sub-community or
     topic, so it reaches the ideal audience.
   Order matters: put the tags you are most confident about FIRST. When a
   person is identified for this clip (see "people") their name tag takes one
   of the five slots, and the LAST tags in your list are the ones dropped to
   make room.
   Do NOT include any person's name here, and do NOT include a trending/
   seasonal "wildcard" tag — person-name tags are added separately by the
   system (see "people").
4. "people": An array of the names of specific real people, public figures,
   actors, athletes, streamers, or recognizable characters who are featured in
   or explicitly named in THIS clip (based on its transcript). Use plain names
   without a leading '#'. Return an empty array if no specific person is clearly
   identifiable — never guess, invent names, or include generic words.
5. "platform_variants": An object with platform-specific caption tweaks. These
   are caption TEXT ONLY — do NOT put any hashtags in them (hashtags are
   appended by the system). Every one must follow the SAME strict style rules
   as "description": all lowercase, no periods, only commas / "!" / "?".
   - "tiktok": A caption string (no hashtags).
   - "instagram": A caption string (no hashtags).
   - "youtube": A caption string (no hashtags).
   - "snapchat": A caption string (no hashtags).

Rules:
- No two clips may have identical or very similar captions.
- Every caption must be all lowercase with no periods — this is not optional.
- Avoid corny hooks, hype words, fake suspense, and curiosity-bait.
- Captions must be under 2200 characters.
- Keep hashtags relevant and specific — quality over quantity.

Your response MUST be a JSON array of objects, one for each input clip."""

METADATA_RESPONSE_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "clip_index": {"type": "INTEGER"},
            "description": {"type": "STRING"},
            "hashtags": {"type": "ARRAY", "items": {"type": "STRING"}},
            "people": {"type": "ARRAY", "items": {"type": "STRING"}},
            "platform_variants": {
                "type": "OBJECT",
                "properties": {
                    "tiktok": {"type": "STRING"},
                    "instagram": {"type": "STRING"},
                    "youtube": {"type": "STRING"},
                    "snapchat": {"type": "STRING"}
                }
            }
        },
        "required": ["clip_index", "description", "hashtags", "people", "platform_variants"]
    }
}


# Characters that never survive into a caption. Periods and ellipses are the
# biggest "written by a machine" tell; colons/semicolons/quotes/em-dashes are the
# rest. Apostrophes and intra-word hyphens stay (people type "dont" or "don't",
# both read human; "well-known" does too). Emoji pass through untouched.
_BANNED_CAPTION_CHARS = set('.…:;"“”‘()[]{}<>/\\|*_~`^—–')


def _humanize(text) -> str:
    """Force a caption into phone-typed style: all lowercase, no periods, and
    only , ! ? as punctuation.

    The prompt asks for this too, but the model complies only most of the time —
    and "most of the time" is exactly the kind of inconsistency that reads as
    automated in a feed. This makes it deterministic."""
    s = str(text or "")
    s = s.replace("’", "'").replace("‛", "'")   # curly apostrophe -> straight
    s = s.lower()
    s = "".join(" " if ch in _BANNED_CAPTION_CHARS else ch for ch in s)
    s = re.sub(r"\s+([,!?])", r"\1", s)          # no space before punctuation
    s = re.sub(r"([,!?])\1{2,}", r"\1\1", s)     # cap "!!!!!" at "!!"
    s = re.sub(r",{2,}", ",", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s.strip(" ,")                          # never open or close on a comma


def _name_to_tag(name) -> str | None:
    """Normalize a person name into a bare hashtag token: drop possessives and
    punctuation, collapse spaces, lowercase. 'Matan's' -> 'matan',
    'John Cena' -> 'johncena'. Returns None if nothing usable remains."""
    s = str(name or "").strip()
    if not s:
        return None
    s = re.sub(r"['’]s\b", "", s)          # drop a trailing possessive
    s = re.sub(r"[^A-Za-z0-9]+", "", s)     # strip spaces/punctuation
    s = s.lower()
    return s or None


def _assemble_hashtags(model_tags, name_tags):
    """Build the final hashtag list — ALWAYS exactly TAG_COUNT tags: the model's
    content tags followed by person-name tags for the people identified in the
    clip. Names are guaranteed a place (we keep at least 2 content tags when
    available, then fill the tail with names).

    The count is a hard guarantee, not a request: the prompt asks for 5, but a
    model that returns 3 — or a clip with no identifiable person — must not ship
    a short tag list. Any remaining slots are topped up from FALLBACK_TAGS.
    All tags are normalized without a leading '#'."""
    content = []
    for t in (model_tags or []):
        t = str(t).lstrip('#').strip()
        if t and t.lower() not in {x.lower() for x in content}:
            content.append(t)

    names = []
    for n in (name_tags or []):
        tag = _name_to_tag(n)
        if not tag:
            continue
        if tag in {x.lower() for x in names} or tag in {x.lower() for x in content}:
            continue
        names.append(tag)
    names = names[:MAX_NAME_TAGS]

    keep_content = max(TAG_COUNT - len(names), min(2, len(content)))
    out = (content[:keep_content] + names)[:TAG_COUNT]

    # Top up to exactly TAG_COUNT: first with content tags trimmed above, then
    # with generic discovery tags.
    if len(out) < TAG_COUNT:
        seen = {x.lower() for x in out}
        for t in list(content) + FALLBACK_TAGS:
            if len(out) >= TAG_COUNT:
                break
            if t.lower() not in seen:
                out.append(t)
                seen.add(t.lower())
    return out[:TAG_COUNT]


def _load_clip_transcript_text(clip: Clip, job_output_dir: str) -> str:
    transcript_path = os.path.join(job_output_dir, f"clip_{clip.clip_index}", "transcript.json")
    try:
        with open(transcript_path, 'r', encoding='utf-8') as f:
            t = json.load(f)
            return " ".join([seg['text'] for seg in t.get('segments', [])])
    except Exception:
        return "[Transcript missing]"


def _apply_metadata_result(clip: Clip, res: dict, job_output_dir: str, db: Session):
    clip.description = _humanize(res.get('description'))
    name_tags = res.get('people') or []
    clip.hashtags = _assemble_hashtags(res.get('hashtags'), name_tags)

    variants = res.get('platform_variants')
    if isinstance(variants, dict):
        variants = {k: _humanize(v) for k, v in variants.items()}
    clip.platform_variants = variants

    # Keep the on-disk metadata.json in step with what we actually stored.
    res['description'] = clip.description
    res['hashtags'] = clip.hashtags
    res['platform_variants'] = variants
    res.pop('title', None)

    clip_dir = os.path.join(job_output_dir, f"clip_{clip.clip_index}")
    os.makedirs(clip_dir, exist_ok=True)
    with open(os.path.join(clip_dir, "metadata.json"), 'w', encoding='utf-8') as f:
        json.dump(res, f, ensure_ascii=False)

    # Metadata now runs lazily, after the pipeline — never downgrade a clip the
    # user has already reviewed (approved/rejected/posted) back to review state.
    if clip.status == "processing":
        clip.status = "ready_for_review"


async def _generate_batches_async(clips: list[Clip], job_output_dir: str) -> list[dict]:
    """Run independent metadata batch calls concurrently via Gemini's async batch helper."""
    batch_size = 12
    user_messages = []
    for clip in clips:
        text = _load_clip_transcript_text(clip, job_output_dir)
        user_messages.append(f"Clip {clip.clip_index}:\n{text}")

    batches = []
    for i in range(0, len(user_messages), batch_size):
        batches.append("\n\n".join(user_messages[i:i + batch_size]))

    results = await gemini_client.call_batch_async(
        METADATA_SYSTEM_PROMPT,
        batches,
        temperature=0.7,
        response_schema=METADATA_RESPONSE_SCHEMA,
    )

    merged = []
    for result in results:
        if isinstance(result, Exception):
            raise result
        if isinstance(result, list):
            merged.extend(result)
    return merged


async def generate_for_clips(job: Job, db: Session, clip_ids: list[str] | None = None):
    """Generate metadata for selected clips (or all non-failed clips if clip_ids is None)."""
    logger.info(f"Generating metadata for job {job.id}")

    query = db.query(Clip).filter(Clip.job_id == job.id, Clip.status != "failed").order_by(Clip.clip_index.asc())
    if clip_ids:
        query = query.filter(Clip.id.in_(clip_ids))
    clips = query.all()

    if not clips:
        return

    job_output_dir = os.path.join(config.OUTPUT_DIR, job.id)

    results = await _generate_batches_async(clips, job_output_dir)

    clip_lookup = {c.clip_index: c for c in clips}
    for res in results:
        c_idx = res.get('clip_index')
        matching_clip = clip_lookup.get(c_idx)
        if matching_clip:
            _apply_metadata_result(matching_clip, res, job_output_dir, db)

    db.commit()


def generate_batch(job: Job, db: Session):
    """Synchronous wrapper for generate_for_clips for orchestrator/backwards compatibility."""
    asyncio.run(generate_for_clips(job, db))
