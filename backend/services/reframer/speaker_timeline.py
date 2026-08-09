"""
PASS D (cont.) — speaker timeline resolution.

Turns per-track ASD probabilities into a per-frame decision: who the crop
should be on. The product guarantee lives here:

    if any on-screen track is confidently speaking while VAD reports speech,
    that track is the crop target — unconditionally.

Fallbacks (held / broll / center) only fire when nobody who is speaking is
visible, so they never override a real on-screen speaker.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from services.reframer.detect import VideoMeta
from services.reframer.shots import Shot, shot_at
from services.reframer.tracking import Track

logger = logging.getLogger("clipper")


@dataclass
class FrameDecision:
    frame_idx: int
    t: float
    shot_id: int
    mode: str                       # speaker | held | broll | center
    target_track_id: int | None     # primary track to frame
    secondary_track_id: int | None  # second speaker, for group framing
    confidence: float               # ASD probability of the target (0 for non-speaker)


def _eligible_speakers(tracks_at_frame, asd_scores, frame_idx, min_dur_s, cfg):
    """(track, prob) for tracks long enough to be a speaker, sorted prob desc."""
    out = []
    for tr in tracks_at_frame:
        if tr.duration_s < min_dur_s:
            continue
        prob = asd_scores.get(tr.track_id, {}).get(frame_idx, 0.0)
        out.append((tr, prob))
    out.sort(key=lambda x: x[1], reverse=True)
    return out


def _prominence(track: Track, frame_idx: int, meta: VideoMeta) -> float:
    box = track.face_at(frame_idx)
    if box is None:
        return -1.0
    w, h = box[2] - box[0], box[3] - box[1]
    size = max(w, h) / max(1.0, meta.active_h)
    cx = (box[0] + box[2]) / 2.0
    cy = (box[1] + box[3]) / 2.0
    fcx = meta.active_x0 + meta.active_w / 2.0
    fcy = meta.active_y0 + meta.active_h / 2.0
    off = (((cx - fcx) / max(1.0, meta.active_w)) ** 2
           + ((cy - fcy) / max(1.0, meta.active_h)) ** 2) ** 0.5
    return 0.7 * min(1.0, size * 3.0) + 0.3 * max(0.0, 1.0 - off)


def resolve_speakers(
    tracks: list[Track],
    asd_scores: dict[int, dict[int, float]],
    audio,
    shots: list[Shot],
    meta: VideoMeta,
    cfg,
) -> list[FrameDecision]:
    by_id = {t.track_id: t for t in tracks}
    decisions: list[FrameDecision] = []

    committed: int | None = None      # current speaker track id (hysteresis state)
    last_speaker_frame: int = -10 ** 9
    last_speaker_t: float = -1e9
    last_speaker_shot: int = -1

    for f in range(meta.frame_count):
        t = meta.timestamps[f] if f < len(meta.timestamps) else f / meta.fps
        shot = shot_at(t, shots)
        shot_id = shot.idx if shot else 0

        present = [tr for tr in tracks if tr.covers(f) and tr.face_at(f) is not None]
        eligible = _eligible_speakers(present, asd_scores, f, cfg.track_min_duration_s, cfg)
        vad = audio.vad_speech_at(t)

        # committed speaker no longer present (e.g. shot cut) → drop it
        if committed is not None and committed not in {tr.track_id for tr in present}:
            committed = None

        winner: int | None = None
        winner_prob = 0.0
        secondary: int | None = None

        if vad and eligible and eligible[0][1] >= cfg.asd_confidence:
            best_tr, best_p = eligible[0]
            if committed is not None:
                cur_p = asd_scores.get(committed, {}).get(f, 0.0)
                if cur_p >= cfg.asd_confidence and best_tr.track_id != committed:
                    # incumbent still speaking — challenger needs a clear margin
                    if best_p >= cur_p + cfg.asd_margin:
                        committed = best_tr.track_id
                    # else keep incumbent
                elif cur_p < cfg.asd_confidence:
                    committed = best_tr.track_id
            else:
                committed = best_tr.track_id

            winner = committed
            winner_prob = asd_scores.get(winner, {}).get(f, 0.0)

            # second simultaneous speaker → candidate for group framing
            if cfg.allow_group_framing:
                for tr, p in eligible:
                    if tr.track_id != winner and p >= cfg.asd_confidence:
                        secondary = tr.track_id
                        break

        if winner is not None:
            last_speaker_frame = f
            last_speaker_t = t
            last_speaker_shot = shot_id
            decisions.append(FrameDecision(f, t, shot_id, "speaker", winner, secondary, winner_prob))
            continue

        # No on-screen speaker this frame. "held" only carries WITHIN the shot
        # the last speaker was in — a shot cut invalidates the held framing, so
        # the new shot is reframed immediately (broll/center below).
        within_grace = (
            (t - last_speaker_t) <= cfg.off_screen_grace_s
            and shot_id == last_speaker_shot
        )
        if vad and within_grace and by_id.get(committed if committed is not None else -1):
            decisions.append(FrameDecision(f, t, shot_id, "held", committed, None, 0.0))
            continue
        if vad and within_grace and last_speaker_frame >= 0:
            # speaker track gone but still inside grace — hold geometric position
            decisions.append(FrameDecision(f, t, shot_id, "held", None, None, 0.0))
            continue

        # Genuine non-speaker frame: B-roll framing on the most prominent face.
        if present:
            best = max(present, key=lambda tr: _prominence(tr, f, meta))
            decisions.append(FrameDecision(f, t, shot_id, "broll", best.track_id, None, 0.0))
        else:
            decisions.append(FrameDecision(f, t, shot_id, "center", None, None, 0.0))

    _merge_short_segments(decisions, meta, cfg)
    _log_summary(decisions)
    return decisions


def _merge_short_segments(decisions: list[FrameDecision], meta: VideoMeta, cfg) -> None:
    """Erase speaker turns shorter than min_speaker_segment_s so the camera
    never chases a sub-second blip."""
    if not decisions:
        return
    min_frames = max(1, int(cfg.min_speaker_segment_s * meta.fps))

    i = 0
    n = len(decisions)
    while i < n:
        d = decisions[i]
        if d.mode != "speaker":
            i += 1
            continue
        j = i
        while j < n and decisions[j].mode == "speaker" \
                and decisions[j].target_track_id == d.target_track_id \
                and decisions[j].shot_id == d.shot_id:
            j += 1
        seg_len = j - i
        if seg_len < min_frames:
            # absorb the blip into the previous speaker run if one exists
            prev = decisions[i - 1] if i > 0 else None
            if prev is not None and prev.mode == "speaker" and prev.shot_id == d.shot_id:
                for k in range(i, j):
                    decisions[k].mode = "speaker"
                    decisions[k].target_track_id = prev.target_track_id
                    decisions[k].secondary_track_id = None
                    decisions[k].confidence = 0.0
            else:
                # no previous speaker — demote to held
                for k in range(i, j):
                    decisions[k].mode = "held"
                    decisions[k].target_track_id = None
        i = j


def _log_summary(decisions: list[FrameDecision]) -> None:
    if not decisions:
        return
    from collections import Counter
    counts = Counter(d.mode for d in decisions)
    total = len(decisions)
    pct = 100.0 * counts.get("speaker", 0) / total
    logger.info(
        f"resolve_speakers: {total} frames — "
        f"speaker={counts.get('speaker', 0)} ({pct:.1f}%) "
        f"held={counts.get('held', 0)} broll={counts.get('broll', 0)} "
        f"center={counts.get('center', 0)}"
    )
