import json
import logging
from sqlalchemy.orm import Session
from models.job import Job
from models.clip import Clip
from services.gemini_client import gemini_client
from config import config

logger = logging.getLogger("clipper")

SYSTEM_PROMPT = """You are an expert viral content analyst specializing in short-form video.
Your job is to analyze a full video transcript and identify EVERY moment
that could work as a standalone short-form clip (TikTok, Reels, Shorts).
Lean toward INCLUSION: when in doubt about a moment, include it. It is far
better to surface a borderline clip the user can reject than to miss one.

You are maximizing for: viewer retention (hook in first 2 seconds, payoff
before they scroll), shareability (would someone send this to a friend?),
and comment-bait (would this make someone want to react?).

Each clip you identify MUST:
- Be self-contained: a viewer with zero context should understand and
  enjoy it. No references to earlier parts of the video that aren't
  included in the clip.
- Have a natural start point: begins at the start of a sentence, thought,
  or story — never mid-sentence.
- Have a natural end point: ends at a punchline, conclusion, reaction,
  or natural pause — never mid-thought.
- Be between 15 and 120 seconds long. Aim for 30–75 seconds as the
  sweet spot, but a sharp 15–30 second one-liner or exchange is great and
  you should capture those too. Longer is fine for a gripping story.
- Have a plausible reason to exist as a clip — but you do NOT need to be
  certain it will go viral. Capture every moment with a hook, a laugh, a
  strong opinion, a surprising fact, an emotional beat, or a quotable line.

Find DISTINCT moments rather than slight variations of the same one: two
adjacent jokes, two different stories, or two separate hot-takes are each
their own clip even if they sit close together. Clips may share a few
seconds of context at their boundaries. Don't return two candidates that
cover essentially the same core content.

All timestamps in the transcript are in SECONDS. Your start_time and
end_time values MUST also be in seconds (float). Use the transcript
timestamps directly.

Be thorough and exhaustive — extract as many viable clips as the video
supports. A 10-minute video typically yields 6–12 clips, and a 60-minute
video 20–45. Err on the side of MORE clips, never fewer. Do not artificially
limit the count or stop early."""

RESPONSE_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "start_time": {"type": "NUMBER"},
            "end_time": {"type": "NUMBER"},
            "virality_score": {"type": "NUMBER"},
            "hook": {"type": "STRING"},
            "category": {"type": "STRING"}
        },
        "required": ["start_time", "end_time", "virality_score", "hook", "category"]
    }
}

def detect_moments(job: Job, db: Session, audio_summary: str):
    logger.info(f"Starting moment detection for job {job.id}")

    with open(job.transcript_path, 'r', encoding='utf-8') as f:
        transcript_data = json.load(f)

    # Prompt Gemini with segment-level text only; word timestamps are reserved
    # for local boundary snapping after coarse candidates are returned.
    transcript_lines = []
    word_timestamps = []
    for seg in transcript_data.get('segments', []):
        transcript_lines.append(f"[{seg['start']:.2f} - {seg['end']:.2f}] {seg['text']}")
        for w in seg.get('words', []):
            word_timestamps.append(w)

    transcript_text = "\n".join(transcript_lines)

    audio_section = (
        f"\n\nHere are the detected audio energy events (timestamps in seconds):\n\n{audio_summary}"
        if config.ENABLE_AUDIO_ANALYSIS and audio_summary
        else ""
    )

    user_message = f"""Here is the full transcript with timestamps (all in seconds):

{transcript_text}{audio_section}

Return a JSON array of clip candidates. Each object must have:
{{
  "start_time": <float, seconds from video start>,
  "end_time": <float, seconds from video start>,
  "virality_score": <int, 1-10>,
  "hook": "<the first thing the viewer hears — what grabs them>",
  "category": "<one of: funny, hot_take, story, emotional, educational, shocking, quotable>"
}}

Return ONLY the JSON array, no other text."""

    try:
        response_json = gemini_client.call(
            SYSTEM_PROMPT,
            user_message,
            response_schema=RESPONSE_SCHEMA
        )
    except Exception as e:
        logger.error(f"Moment detection failed: {e}")
        raise
        
    valid_clips = []
    for c in response_json:
        st = c.get('start_time', 0)
        et = c.get('end_time', 0)
        dur = et - st
        if st < et and st >= 0 and et <= job.video_duration_seconds and 15 <= dur <= 150:
            valid_clips.append(c)
        else:
            logger.debug(f"Discarding candidate due to invalid timestamps: {st}-{et} (dur: {dur})")

    snapped_clips = []
    for c in valid_clips:
        st = c['start_time']
        et = c['end_time']
        
        # Find the nearest word boundary at or before start_time
        start_word = None
        for w in reversed(word_timestamps):
            if w['start'] <= st:
                start_word = w
                break
        if not start_word:
            start_word = next((w for w in word_timestamps if w['start'] >= st), None)
        
        # Find the nearest word boundary at or after end_time
        end_word = None
        for w in word_timestamps:
            if w['end'] >= et:
                end_word = w
                break
        if not end_word:
            end_word = next((w for w in reversed(word_timestamps) if w['end'] <= et), None)
            
        if start_word and end_word:
            new_st = start_word['start']
            new_et = end_word['end']
            
            new_dur = new_et - new_st
            if 15 <= new_dur <= 150 and new_st < new_et:
                c['start_time'] = new_st
                c['end_time'] = new_et
                snapped_clips.append(c)
            else:
                logger.debug(f"Discarding candidate after snapping: {new_st}-{new_et} (dur: {new_dur})")

    snapped_clips.sort(key=lambda x: x['start_time'])
    merged_clips = []
    
    for current in snapped_clips:
        if not merged_clips:
            merged_clips.append(current)
            continue
            
        last = merged_clips[-1]
        
        overlap_start = max(last['start_time'], current['start_time'])
        overlap_end = min(last['end_time'], current['end_time'])
        overlap_dur = max(0, overlap_end - overlap_start)
        
        shorter_dur = min(last['end_time'] - last['start_time'], current['end_time'] - current['start_time'])
        
        # Only collapse near-duplicate clips. A more permissive threshold (0.7)
        # lets distinct-but-adjacent moments both survive, yielding more clips.
        if shorter_dur > 0 and (overlap_dur / shorter_dur) > 0.7:
            if current['virality_score'] > last['virality_score']:
                merged_clips[-1] = current
        else:
            merged_clips.append(current)

    merged_clips.sort(key=lambda x: x['start_time'])
    
    if not merged_clips:
        logger.warning(f"No clips found for job {job.id}")
        job.total_clips_found = 0
        db.commit()
        return

    for i, c in enumerate(merged_clips):
        clip = Clip(
            job_id=job.id,
            clip_index=i + 1,
            start_time=c['start_time'],
            end_time=c['end_time'],
            duration=c['end_time'] - c['start_time'],
            virality_score=c['virality_score'],
            category=c['category'],
            status="pending"
        )
        db.add(clip)
    
    job.total_clips_found = len(merged_clips)
    db.commit()
    logger.info(f"Successfully identified {job.total_clips_found} clips for job {job.id}")
