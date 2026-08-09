"""
PASS A — shot boundary detection.

Returns a list of Shot(start, end, idx). Shots are the unit within which a
position prior is valid; across shots characters teleport.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import cv2
import numpy as np

logger = logging.getLogger("clipper")


@dataclass(frozen=True)
class Shot:
    idx: int
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start

    def overlaps(self, t_start: float, t_end: float) -> bool:
        return not (t_end <= self.start or t_start >= self.end)


def detect_shots(video_path: str, threshold: float = 27.0) -> list[Shot]:
    """
    Detect shot boundaries with PySceneDetect (ContentDetector).
    Falls back to a sparse pixel-diff scan if the package is unavailable.
    """
    try:
        return _detect_with_scenedetect(video_path, threshold)
    except ImportError:
        logger.warning("scenedetect not installed — using pixel-diff fallback")
    except Exception as e:
        logger.warning(f"scenedetect failed ({e}) — using pixel-diff fallback")
    return _detect_with_pixel_diff(video_path)


def _detect_with_scenedetect(video_path: str, threshold: float) -> list[Shot]:
    from scenedetect import open_video, SceneManager
    from scenedetect.detectors import ContentDetector

    video = open_video(video_path)
    manager = SceneManager()
    manager.add_detector(ContentDetector(threshold=threshold))
    manager.detect_scenes(video=video, show_progress=False)
    scene_list = manager.get_scene_list()

    if not scene_list:
        # Whole video is one shot
        cap = cv2.VideoCapture(video_path)
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        cap.release()
        duration = total / fps if fps > 0 else 0.0
        return [Shot(idx=0, start=0.0, end=duration)]

    shots = []
    for i, (start_tc, end_tc) in enumerate(scene_list):
        shots.append(Shot(idx=i, start=start_tc.get_seconds(), end=end_tc.get_seconds()))
    logger.info(f"Detected {len(shots)} shots via PySceneDetect")
    return shots


def _detect_with_pixel_diff(video_path: str, threshold: float = 32.0, sample_every: int = 2) -> list[Shot]:
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return []

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = total / fps if fps > 0 else 0.0

    cuts: list[float] = []
    prev_small: np.ndarray | None = None
    frame_idx = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break
        if frame_idx % sample_every == 0:
            small = cv2.resize(frame, (160, 90), interpolation=cv2.INTER_AREA)
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            if prev_small is not None:
                diff = float(np.mean(cv2.absdiff(gray, prev_small)))
                if diff > threshold:
                    cuts.append(frame_idx / fps)
            prev_small = gray
        frame_idx += 1

    cap.release()

    boundaries = [0.0] + cuts + [duration]
    shots: list[Shot] = []
    for i in range(len(boundaries) - 1):
        s, e = boundaries[i], boundaries[i + 1]
        if e - s < 0.2:  # discard sub-200ms shots from spurious diffs
            continue
        shots.append(Shot(idx=len(shots), start=s, end=e))

    if not shots:
        shots = [Shot(idx=0, start=0.0, end=duration)]

    logger.info(f"Detected {len(shots)} shots via pixel-diff fallback")
    return shots


def shot_at(t: float, shots: list[Shot]) -> Shot | None:
    for shot in shots:
        if shot.start <= t < shot.end:
            return shot
    return shots[-1] if shots else None
