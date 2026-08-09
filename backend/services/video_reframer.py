"""
Backward-compatible shim. The implementation lives in services/reframer/.

The system is per-clip and self-contained: it does its own shot detection,
audio analysis, face tracking, active-speaker detection, and rendering. The
legacy `speaker_mapping` and `audio_path` parameters are accepted but ignored.
"""

from services.reframer import reframe_video
from services.reframer.reframer import reframe_to_region

__all__ = ["reframe_video", "reframe_to_region"]
