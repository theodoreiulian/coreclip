"""
PASS B — video scan + per-frame face/person detection.

A single decode pass records frame metadata (real timestamps, so VFR is
handled), detects letterbox bars, and runs face + person detection on a
cadence. Everything downstream works off this in-memory result so the video
is not re-decoded for detection.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

import cv2
import numpy as np

from services.reframer.shots import Shot

logger = logging.getLogger("clipper")

BBox = tuple[int, int, int, int]


# ── Video metadata ─────────────────────────────────────────────────────


@dataclass
class VideoMeta:
    fps: float
    width: int
    height: int
    frame_count: int
    timestamps: list[float]                  # seconds, one per decoded frame
    letterbox: tuple[int, int, int, int]     # (top, bottom, left, right) black insets

    @property
    def active_x0(self) -> int:
        return self.letterbox[2]

    @property
    def active_y0(self) -> int:
        return self.letterbox[0]

    @property
    def active_w(self) -> int:
        return self.width - self.letterbox[2] - self.letterbox[3]

    @property
    def active_h(self) -> int:
        return self.height - self.letterbox[0] - self.letterbox[1]


@dataclass
class FrameDetections:
    frame_idx: int
    t: float
    faces: list = field(default_factory=list)    # (x1,y1,x2,y2,conf)
    persons: list = field(default_factory=list)  # (x1,y1,x2,y2,conf)


# ── Model loaders (lazy, process-global) ───────────────────────────────


_face_net = None
_face_net_loaded = False


def _load_face_net():
    global _face_net, _face_net_loaded
    if _face_net_loaded:
        return _face_net
    _face_net_loaded = True
    try:
        model_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            ".models",
        )
        proto = os.path.join(model_dir, "deploy.prototxt")
        model = os.path.join(model_dir, "res10_300x300_ssd.caffemodel")
        if os.path.exists(proto) and os.path.exists(model):
            _face_net = cv2.dnn.readNetFromCaffe(proto, model)
            logger.info("Reframer face detector loaded")
        else:
            logger.warning(f"Face detector model files missing in {model_dir}")
    except Exception as e:
        logger.warning(f"Face detector load failed: {e}")
    return _face_net


_yolo = None
_yolo_loaded = False


def _load_yolo():
    global _yolo, _yolo_loaded
    if _yolo_loaded:
        return _yolo
    _yolo_loaded = True
    try:
        from ultralytics import YOLO
        _yolo = YOLO("yolov8n.pt")
        logger.info("Reframer person detector loaded")
    except Exception as e:
        logger.warning(f"YOLO load failed: {e}")
    return _yolo


# ── Detection primitives ───────────────────────────────────────────────


def detect_faces(frame: np.ndarray, conf_threshold: float) -> list:
    net = _load_face_net()
    if net is None:
        return []
    h, w = frame.shape[:2]
    blob = cv2.dnn.blobFromImage(frame, 1.0, (300, 300), (104.0, 177.0, 123.0))
    net.setInput(blob)
    det = net.forward()
    faces = []
    for i in range(det.shape[2]):
        c = float(det[0, 0, i, 2])
        if c < conf_threshold:
            continue
        x1 = int(max(0.0, det[0, 0, i, 3]) * w)
        y1 = int(max(0.0, det[0, 0, i, 4]) * h)
        x2 = int(min(1.0, det[0, 0, i, 5]) * w)
        y2 = int(min(1.0, det[0, 0, i, 6]) * h)
        if x2 - x1 >= 8 and y2 - y1 >= 8:
            faces.append((x1, y1, x2, y2, c))
    return faces


def detect_persons(frame: np.ndarray, conf_threshold: float) -> list:
    yolo = _load_yolo()
    if yolo is None:
        return []
    try:
        results = yolo.predict(frame, classes=[0], conf=conf_threshold, verbose=False)
    except Exception as e:
        logger.warning(f"YOLO predict failed: {e}")
        return []
    persons = []
    for r in results:
        for box in r.boxes:
            x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
            persons.append((int(x1), int(y1), int(x2), int(y2), float(box.conf[0])))
    return persons


def associate_face_to_person(face, persons) -> BBox:
    """Return the body bbox enclosing this face, or a synthetic one."""
    fx1, fy1, fx2, fy2 = face[:4]
    fcx = (fx1 + fx2) / 2
    fcy = (fy1 + fy2) / 2
    best = None
    best_area = 1e18
    for p in persons:
        px1, py1, px2, py2 = p[:4]
        if px1 <= fcx <= px2 and py1 <= fcy <= py2:
            area = (px2 - px1) * (py2 - py1)
            if area < best_area:
                best_area = area
                best = p[:4]
    if best is not None:
        return best
    fw = fx2 - fx1
    fh = fy2 - fy1
    return (int(fx1 - fw * 0.5), int(fy1 - fh * 0.3),
            int(fx2 + fw * 0.5), int(fy2 + fh * 7.0))


# ── Letterbox detection ────────────────────────────────────────────────


def _detect_letterbox(frames: list[np.ndarray], w: int, h: int) -> tuple[int, int, int, int]:
    """Find black bars present across ALL sampled frames. Conservative."""
    if not frames:
        return (0, 0, 0, 0)
    grays = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in frames]
    DARK = 18

    def band(axis_is_row: bool, from_start: bool) -> int:
        limit = h if axis_is_row else w
        max_band = limit // 3   # never call more than a third of the frame a bar
        count = 0
        for k in range(max_band):
            idx = k if from_start else limit - 1 - k
            line_is_dark = True
            for g in grays:
                line = g[idx, :] if axis_is_row else g[:, idx]
                if float(np.mean(line)) > DARK:
                    line_is_dark = False
                    break
            if not line_is_dark:
                break
            count += 1
        return count

    top = band(True, True)
    bottom = band(True, False)
    left = band(False, True)
    right = band(False, False)

    if h - top - bottom < h * 0.5 or w - left - right < w * 0.5:
        return (0, 0, 0, 0)   # implausible — treat as no letterbox
    if top or bottom or left or right:
        logger.info(f"Letterbox detected: top={top} bottom={bottom} left={left} right={right}")
    return (top, bottom, left, right)


# ── Main entry point ───────────────────────────────────────────────────


def analyze_video(video_path: str, cfg) -> tuple[VideoMeta, dict[int, FrameDetections], list[Shot]]:
    """
    Single decode pass: collect timestamps + letterbox, run detection every
    cfg.detect_every frames, and compute shot boundaries.
    Returns (meta, {frame_idx: FrameDetections}, shots).
    """
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"analyze_video: cannot open {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    declared_total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    timestamps: list[float] = []
    detections: dict[int, FrameDetections] = {}
    letterbox_samples: list[np.ndarray] = []
    sample_stride = max(1, declared_total // 20) if declared_total > 0 else 30

    cuts: list[float] = []
    prev_small: np.ndarray | None = None
    shot_threshold = 27.0

    frame_idx = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break

        pos_ms = cap.get(cv2.CAP_PROP_POS_MSEC)
        if pos_ms and pos_ms > 0:
            t = pos_ms / 1000.0
        else:
            t = frame_idx / fps
        timestamps.append(t)

        if frame_idx % 2 == 0:
            small = cv2.resize(frame, (160, 90), interpolation=cv2.INTER_AREA)
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            if prev_small is not None:
                diff = float(np.mean(cv2.absdiff(gray, prev_small)))
                if diff > shot_threshold:
                    cuts.append(t)
            prev_small = gray

        if frame_idx % sample_stride == 0 and len(letterbox_samples) < 20:
            letterbox_samples.append(frame.copy())

        if frame_idx % cfg.detect_every == 0:
            h, w = frame.shape[:2]
            scale = 1.0
            if max(h, w) > 720:
                scale = 720.0 / max(h, w)
                small_frame = cv2.resize(frame, (int(w * scale), int(h * scale)))
            else:
                small_frame = frame

            faces = detect_faces(small_frame, cfg.face_conf)
            persons = detect_persons(small_frame, cfg.person_conf)

            if scale != 1.0:
                inv_scale = 1.0 / scale
                faces = [(int(x1 * inv_scale), int(y1 * inv_scale), int(x2 * inv_scale), int(y2 * inv_scale), c) for x1, y1, x2, y2, c in faces]
                persons = [(int(x1 * inv_scale), int(y1 * inv_scale), int(x2 * inv_scale), int(y2 * inv_scale), c) for x1, y1, x2, y2, c in persons]

            detections[frame_idx] = FrameDetections(frame_idx, t, faces, persons)

        frame_idx += 1

    cap.release()

    duration = frame_idx / fps if fps > 0 else 0.0
    boundaries = [0.0] + cuts + [duration]
    shots: list[Shot] = []
    for i in range(len(boundaries) - 1):
        s, e = boundaries[i], boundaries[i + 1]
        if e - s < 0.2:
            continue
        shots.append(Shot(idx=len(shots), start=s, end=e))
    if not shots:
        shots = [Shot(idx=0, start=0.0, end=duration)]

    letterbox = _detect_letterbox(letterbox_samples, width, height)
    meta = VideoMeta(
        fps=fps, width=width, height=height, frame_count=frame_idx,
        timestamps=timestamps, letterbox=letterbox,
    )
    logger.info(
        f"analyze_video: {frame_idx} frames @ {fps:.2f}fps, {width}x{height}, "
        f"{len(detections)} detection frames, {len(shots)} shots"
    )
    return meta, detections, shots
