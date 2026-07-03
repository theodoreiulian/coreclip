"""
PASS E (cont.) — renderer.

A dumb consumer of the precomputed crop trajectory: read frame, crop,
resize, write. No decisions are made here — every choice was resolved in
the speaker timeline and the camera planner. This keeps rendering
deterministic and trivially correct.
"""

from __future__ import annotations

import logging
import subprocess

import cv2
import numpy as np

from services.reframer.camera import CropFrame
from services.ffmpeg_client import _get_ffmpeg_binary, build_encode_args

logger = logging.getLogger("clipper")


def _draw_debug(out: np.ndarray, frame_idx: int, cf: CropFrame,
                tracks, asd_scores, cfg) -> None:
    sx = out.shape[1] / cf.crop_w
    sy = out.shape[0] / cf.crop_h
    for tr in tracks:
        box = tr.face_at(frame_idx)
        if box is None:
            continue
        x1 = int((box[0] - cf.crop_x) * sx)
        y1 = int((box[1] - cf.crop_y) * sy)
        x2 = int((box[2] - cf.crop_x) * sx)
        y2 = int((box[3] - cf.crop_y) * sy)
        prob = asd_scores.get(tr.track_id, {}).get(frame_idx, 0.0)
        is_target = tr.track_id == cf.target_track_id
        color = (0, 220, 0) if prob >= cfg.asd_confidence else (0, 140, 255)
        cv2.rectangle(out, (x1, y1), (x2, y2), color, 3 if is_target else 1)
        cv2.putText(out, f"#{tr.track_id} {prob:.2f}", (x1, max(18, y1 - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
    cv2.putText(out, f"{cf.mode} -> {cf.target_track_id}", (16, 40),
                cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)


def render_clip(
    input_path: str,
    output_path: str,
    trajectory: list[CropFrame],
    target_w: int,
    target_h: int,
    cfg,
    tracks=None,
    asd_scores=None,
) -> bool:
    cap = cv2.VideoCapture(input_path)
    if not cap.isOpened():
        logger.error(f"render_clip: cannot open {input_path}")
        return False

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    by_frame = {cf.frame_idx: cf for cf in trajectory}
    last_cf = trajectory[0] if trajectory else None
    if last_cf is None:
        cap.release()
        logger.error("render_clip: empty trajectory")
        return False

    ffmpeg_bin = _get_ffmpeg_binary()
    encode_args = build_encode_args(audio_bitrate="192k")

    cmd = [
        ffmpeg_bin, "-y",
        "-loglevel", "error",
        "-f", "rawvideo",
        "-pix_fmt", "bgr24",
        "-s", f"{target_w}x{target_h}",
        "-r", str(fps),
        "-i", "-",
        "-i", input_path,
        "-map", "0:v:0",
        "-map", "1:a:0?",
        "-shortest"
    ] + encode_args + [
        output_path
    ]

    logger.debug(f"render_clip ffmpeg cmd: {' '.join(cmd)}")

    process = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

    debug = cfg.debug_overlay and tracks is not None and asd_scores is not None
    frame_idx = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        cf = by_frame.get(frame_idx, last_cf)
        last_cf = cf

        x, y, w, h = cf.crop_x, cf.crop_y, cf.crop_w, cf.crop_h
        # defensive clamp against rounding / size drift
        x = max(0, min(x, frame.shape[1] - w))
        y = max(0, min(y, frame.shape[0] - h))
        cropped = frame[y:y + h, x:x + w]
        if cropped.shape[0] != h or cropped.shape[1] != w:
            cropped = cv2.resize(cropped, (w, h), interpolation=cv2.INTER_LINEAR)
        out = cv2.resize(cropped, (target_w, target_h), interpolation=cv2.INTER_LINEAR)

        if debug:
            _draw_debug(out, frame_idx, cf, tracks, asd_scores, cfg)

        try:
            process.stdin.write(out.tobytes())
        except BrokenPipeError:
            logger.error("render_clip: FFmpeg pipe broken")
            break

        frame_idx += 1
        if frame_idx % 300 == 0:
            logger.info(f"render: {frame_idx} frames")

    cap.release()
    
    _, stderr_data = process.communicate()
    
    if process.returncode != 0:
        err_msg = stderr_data.decode('utf-8', errors='replace') if stderr_data else "Unknown error"
        logger.error(f"render_clip: FFmpeg exited with code {process.returncode}. Stderr: {err_msg}")
        return False

    logger.info(f"render_clip: wrote {frame_idx} frames -> {output_path}")
    return frame_idx > 0
