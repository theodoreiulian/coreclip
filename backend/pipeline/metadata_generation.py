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

MAX_TAGS = 5

METADATA_SYSTEM_PROMPT = """You are a social media growth expert. For each of the following video
clips, generate optimized posting metadata.

For each clip, provide:
1. "clip_index": The clip number (matching the input order)
2. "title": A short, plain title (max 70 chars) that simply says what the
   clip is about. Keep it understated and natural — like how a normal person
   would describe the moment. AVOID clickbait, hype, and corny hooks: no
   "Wait for it...", "Nobody talks about this", "You won't believe...",
   "This is why...", "POV:", no ALL-CAPS, and no excessive punctuation or
   emojis. No fake suspense or curiosity-bait.
3. "description": ONE short, casual sentence (aim for under 60 chars, never
   more than ~100). It should read like an offhand caption a real person
   would write — low-key and matter-of-fact. No hooks, no hype, no calls to
   action, no "see more" bait. At most one emoji, and only if it genuinely
   fits.
4. "hashtags": An array of EXACTLY 4 hashtags (without the leading '#'),
   ordered broad first, then niche:
   - 2 BROAD / high-traffic tags: major category or massive community tags
     that cast a wide net (e.g. comedy, funny, fyp, standup).
   - 2 SPECIFIC / niche tags: highly targeted, lower-competition tags (think
     under ~1M views) that precisely describe THIS clip's sub-community or
     topic, so it reaches the ideal audience.
   Do NOT include any person's name here, and do NOT include a trending/
   seasonal "wildcard" tag — person-name tags are added separately by the
   system (see "people").
5. "people": An array of the names of specific real people, public figures,
   actors, athletes, streamers, or recognizable characters who are featured in
   or explicitly named in THIS clip (based on its transcript). Use plain names
   without a leading '#'. Return an empty array if no specific person is clearly
   identifiable — never guess, invent names, or include generic words.
6. "platform_variants": An object with platform-specific tweaks. Captions
   here must be the caption TEXT ONLY — do NOT put any hashtags in the
   caption strings (hashtags are appended by the system). Keep every caption
   short, plain, and low-key (one casual sentence, ideally under 60 chars):
   - "tiktok": A caption string (no hashtags).
   - "instagram": A caption string (no hashtags).
   - "youtube": A plain YouTube Shorts title (must include #Shorts).
   - "snapchat": A caption string (no hashtags).

Rules:
- No two clips may have identical or very similar titles.
- Keep titles and captions short, plain, and natural — not clickbait.
- Avoid corny hooks, hype words, fake suspense, and curiosity-bait.
- YouTube Shorts titles must be under 100 characters.
- YouTube titles MUST include #Shorts.
- TikTok and Instagram captions must be under 2200 characters.
- Keep hashtags relevant and specific — quality over quantity.

Your response MUST be a JSON array of objects, one for each input clip."""

METADATA_RESPONSE_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "clip_index": {"type": "INTEGER"},
            "title": {"type": "STRING"},
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
        "required": ["clip_index", "title", "description", "hashtags", "people", "platform_variants"]
    }
}


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
    """Build the final hashtag list (max 5): the model's content tags followed by
    person-name tags — the people named in the video title or identified in the
    clip. Names take the slot the old trending 'wildcard' used to occupy and are
    guaranteed a place: we keep at least 2 content tags when available, then fill
    the tail with names. All tags are normalized without a leading '#'."""
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
    names = names[:3]

    keep_content = max(MAX_TAGS - len(names), min(2, len(content)))
    return (content[:keep_content] + names)[:MAX_TAGS]


def _load_clip_transcript_text(clip: Clip, job_output_dir: str) -> str:
    transcript_path = os.path.join(job_output_dir, f"clip_{clip.clip_index}", "transcript.json")
    try:
        with open(transcript_path, 'r', encoding='utf-8') as f:
            t = json.load(f)
            return " ".join([seg['text'] for seg in t.get('segments', [])])
    except Exception:
        return "[Transcript missing]"


def _apply_metadata_result(clip: Clip, res: dict, job_output_dir: str, db: Session):
    clip.title = res.get('title')
    clip.description = res.get('description')
    name_tags = res.get('people') or []
    clip.hashtags = _assemble_hashtags(res.get('hashtags'), name_tags)
    res['hashtags'] = clip.hashtags
    clip.platform_variants = res.get('platform_variants')

    clip_dir = os.path.join(job_output_dir, f"clip_{clip.clip_index}")
    os.makedirs(clip_dir, exist_ok=True)
    with open(os.path.join(clip_dir, "metadata.json"), 'w', encoding='utf-8') as f:
        json.dump(res, f, ensure_ascii=False)

    # Metadata now runs lazily, after the pipeline — never downgrade a clip the
    # user has already reviewed (approved/rejected/posted) back to review state.
    if clip.status == "processing":
        clip.status = "ready_for_review"


async def _generate_batches_async(clips: list[Clip], job_output_dir: str, model: str) -> list[dict]:
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
        model=model,
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
    model = config.GEMINI_METADATA_MODEL

    results = await _generate_batches_async(clips, job_output_dir, model)

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
