"""
Active-speaker-locked vertical cropping system.

Five-pass pipeline (see reframer.py):
  A. Shots + audio   — scene cuts, MFCC, VAD on the clip's own audio
  B. Detection       — per-frame face + person detection
  C. Tracking        — per-shot identity tracks
  D. ASD + timeline  — Light-ASD speaking score → per-frame active speaker
  E. Camera + render — smooth crop trajectory → cropped 9:16 video

Public entry point: reframe_video()
"""

from services.reframer.reframer import reframe_video, reframe_to_region

__all__ = ["reframe_video", "reframe_to_region"]
