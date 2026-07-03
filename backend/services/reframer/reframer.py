"""
Active-speaker-locked vertical reframing.

Five-pass pipeline:
  A. Shots + audio   — scene cuts, MFCC, VAD on the clip's OWN audio
  B. Detection       — per-frame face + person detection
  C. Tracking        — per-shot identity tracks
  D. ASD + timeline  — Light-ASD speaking score per track → per-frame speaker
  E. Camera + render — smooth crop trajectory → cropped 9:16 video

Guarantee: whenever someone is visibly speaking, the crop is on them.
Public entry point reframe_video() keeps its original signature so
pipeline/reformat.py works unchanged.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from collections import Counter

logger = logging.getLogger("clipper")


def _apply_determinism(cfg) -> None:
    if not cfg.deterministic:
        return
    try:
        import random
        import numpy as np
        import cv2
        import torch

        random.seed(1234)
        np.random.seed(1234)
        torch.manual_seed(1234)
        cv2.setNumThreads(1)
        torch.set_num_threads(1)
    except Exception as e:
        logger.warning(f"Determinism setup partial: {e}")


def _write_qa(output_path: str, decisions, tracks, shots) -> None:
    try:
        counts = Counter(d.mode for d in decisions)
        total = max(1, len(decisions))
        qa = {
            "frames": len(decisions),
            "shots": len(shots),
            "tracks": len(tracks),
            "mode_counts": dict(counts),
            "speaker_pct": round(100.0 * counts.get("speaker", 0) / total, 1),
            "held_pct": round(100.0 * counts.get("held", 0) / total, 1),
            "broll_pct": round(100.0 * counts.get("broll", 0) / total, 1),
            "center_pct": round(100.0 * counts.get("center", 0) / total, 1),
        }
        qa_path = os.path.join(os.path.dirname(output_path), "reframe_qa.json")
        with open(qa_path, "w") as f:
            json.dump(qa, f, indent=2)
        logger.info(f"Reframe QA: speaker={qa['speaker_pct']}% "
                    f"held={qa['held_pct']}% broll={qa['broll_pct']}% "
                    f"center={qa['center_pct']}%")
    except Exception as e:
        logger.warning(f"QA metrics write failed: {e}")


def reframe_video(
    input_path: str,
    output_path: str,
    target_w: int = 1080,
    target_h: int = 1920,
    audio_path: str | None = None,        # ignored — the clip's own audio is used
    detect_every: int = 1,                # accepted for backward compat
    speaker_mapping: dict | None = None,  # accepted for backward compat
) -> bool:
    """Reframe a clip into target_w x target_h, locked to the active speaker."""
    if not os.path.exists(input_path):
        logger.error(f"reframe_video: input missing {input_path}")
        return False

    try:
        from services.reframer.config import load_reframer_config
        from services.reframer.detect import analyze_video
        from services.reframer.audio_features import load_clip_audio
        from services.reframer.tracking import build_tracks
        from services.reframer.asd import run_asd
        from services.reframer.speaker_timeline import resolve_speakers
        from services.reframer.camera import plan_camera
        from services.reframer.render import render_clip

        cfg = load_reframer_config()
        _apply_determinism(cfg)

        # PASS A — audio
        audio = load_clip_audio(input_path, cfg.vad_threshold)

        # PASS B — detection + shots
        meta, detections, shots = analyze_video(input_path, cfg)
        if meta.frame_count == 0:
            logger.error("reframe_video: no frames decoded")
            return False

        # PASS C — tracking
        tracks = build_tracks(detections, shots, meta, cfg)

        # PASS D — ASD + speaker timeline
        asd_scores = run_asd(input_path, tracks, audio, meta, cfg)
        decisions = resolve_speakers(tracks, asd_scores, audio, shots, meta, cfg)

        # PASS E — camera + render
        trajectory = plan_camera(decisions, tracks, meta, target_w, target_h, cfg)
        ok = render_clip(input_path, output_path, trajectory, target_w, target_h,
                         cfg, meta=meta)
        if not ok:
            if os.path.exists(output_path):
                os.remove(output_path)
            return False

        if cfg.qa_metrics:
            _write_qa(output_path, decisions, tracks, shots)

        logger.info(f"Reframe complete: {output_path}")
        return True

    except Exception as e:
        logger.error(f"reframe_video failed: {e}", exc_info=True)
        return False


def reframe_to_region(
    input_path: str,
    output_path: str,
    target_w: int,
    target_h: int,
    audio_path: str | None = None,
    detect_every: int = 1,
    speaker_mapping: dict | None = None,
) -> bool:
    return reframe_video(
        input_path, output_path,
        target_w=target_w, target_h=target_h,
        audio_path=audio_path,
        detect_every=detect_every,
        speaker_mapping=speaker_mapping,
    )
