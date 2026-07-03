"""
Reframer configuration.

All tunables live here in one dataclass. Values can be overridden per-run
through REFRAMER_* environment variables (see _ENV_MAP). The reframer is
deliberately decoupled from the app-wide Config in backend/config.py so the
cropping system can be tuned in isolation.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger("clipper")


@dataclass
class ReframerConfig:
    # ── Detection ──────────────────────────────────────────────────────
    detect_every: int = 3              # run face/person detection every N frames
    face_conf: float = 0.45            # min confidence for a face detection
    person_conf: float = 0.35          # min confidence for a YOLO person detection

    # ── Tracking ───────────────────────────────────────────────────────
    track_iou_gate: float = 0.30       # min IoU to associate a detection to a track
    track_max_gap_s: float = 0.40      # max time a track may coast with no detection
    track_min_duration_s: float = 0.30 # tracks shorter than this can never be the speaker

    # ── Active Speaker Detection ───────────────────────────────────────
    asd_model: str = "light_asd"       # light_asd | off
    asd_device: str = "cpu"            # cpu | mps  (cpu is deterministic + fast enough)
    asd_fps: float = 25.0              # Light-ASD expects 25 fps face crops
    asd_confidence: float = 0.50       # min speaking probability to BE the speaker
    asd_margin: float = 0.08           # prob lead a challenger needs to STEAL the camera
    asd_chunk_durations: tuple = (1.0,)  # short overlapping windows — reacts fast to onsets

    # ── Speech gating ──────────────────────────────────────────────────
    vad_threshold: float = 0.50        # silero-vad speech probability threshold
    min_speaker_segment_s: float = 0.25  # speaker turns shorter than this are merged away
    off_screen_grace_s: float = 1.50   # hold last speaker this long when they leave frame

    # ── Virtual camera ─────────────────────────────────────────────────
    # Within a continuous shot the camera GLIDES to a new speaker; only a real
    # video cut produces an abrupt crop change (synced to the cut).
    speaker_switch_snap: bool = False  # True = hard-cut onto a new speaker mid-shot
    deadzone_frac: float = 0.12        # subject can drift this fraction of crop before camera moves
    pan_ease_s: float = 0.25           # glide speed to a new speaker mid-shot (smaller = quicker)
    settle_time_s: float = 0.20        # critically-damped follow time constant
    face_top_frac: float = 0.36        # vertical placement of the face inside the crop
    group_pad_frac: float = 0.08       # extra slack when testing if two faces "fit together"

    # ── Framing ────────────────────────────────────────────────────────
    allow_group_framing: bool = True   # frame two people together when they both fit
    enable_punch_in: bool = False      # never crop tighter than the base window (no upscaling)

    # ── Misc ───────────────────────────────────────────────────────────
    deterministic: bool = True         # fixed seeds + single-threaded for reproducible crops
    debug_overlay: bool = False        # draw track boxes + ASD scores + mode onto the output
    qa_metrics: bool = True            # write reframe_qa.json next to the output


_ENV_MAP = {
    "REFRAMER_DETECT_EVERY": ("detect_every", int),
    "REFRAMER_FACE_CONF": ("face_conf", float),
    "REFRAMER_PERSON_CONF": ("person_conf", float),
    "REFRAMER_TRACK_IOU_GATE": ("track_iou_gate", float),
    "REFRAMER_TRACK_MAX_GAP_S": ("track_max_gap_s", float),
    "REFRAMER_TRACK_MIN_DURATION_S": ("track_min_duration_s", float),
    "REFRAMER_ASD_MODEL": ("asd_model", str),
    "REFRAMER_ASD_DEVICE": ("asd_device", str),
    "REFRAMER_ASD_CONFIDENCE": ("asd_confidence", float),
    "REFRAMER_ASD_MARGIN": ("asd_margin", float),
    "REFRAMER_VAD_THRESHOLD": ("vad_threshold", float),
    "REFRAMER_MIN_SPEAKER_SEGMENT_S": ("min_speaker_segment_s", float),
    "REFRAMER_OFF_SCREEN_GRACE_S": ("off_screen_grace_s", float),
    "REFRAMER_SPEAKER_SWITCH_SNAP": ("speaker_switch_snap", bool),
    "REFRAMER_DEADZONE_FRAC": ("deadzone_frac", float),
    "REFRAMER_PAN_EASE_S": ("pan_ease_s", float),
    "REFRAMER_SETTLE_TIME_S": ("settle_time_s", float),
    "REFRAMER_FACE_TOP_FRAC": ("face_top_frac", float),
    "REFRAMER_ALLOW_GROUP_FRAMING": ("allow_group_framing", bool),
    "REFRAMER_ENABLE_PUNCH_IN": ("enable_punch_in", bool),
    "REFRAMER_DETERMINISTIC": ("deterministic", bool),
    "REFRAMER_DEBUG_OVERLAY": ("debug_overlay", bool),
    "REFRAMER_QA_METRICS": ("qa_metrics", bool),
}


def _coerce(raw: str, kind):
    if kind is bool:
        return raw.strip().lower() in ("1", "true", "yes", "on")
    return kind(raw)


def load_reframer_config() -> ReframerConfig:
    """Build a ReframerConfig, applying any REFRAMER_* environment overrides."""
    cfg = ReframerConfig()
    for env_key, (attr, kind) in _ENV_MAP.items():
        raw = os.getenv(env_key)
        if raw is None or raw == "":
            continue
        try:
            setattr(cfg, attr, _coerce(raw, kind))
        except Exception as e:
            logger.warning(f"Ignoring bad {env_key}={raw!r}: {e}")
    return cfg
