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
import os
import tempfile

from services.reframer.camera import CropFrame
from services.reframer.detect import VideoMeta
from services.ffmpeg_client import _get_ffmpeg_binary, build_encode_args

logger = logging.getLogger("clipper")


def render_clip(
    input_path: str,
    output_path: str,
    trajectory: list[CropFrame],
    target_w: int,
    target_h: int,
    cfg,
    meta: VideoMeta,
) -> bool:
    if not trajectory:
        logger.error("render_clip: empty trajectory")
        return False

    fps = meta.fps or 30.0
    ffmpeg_bin = _get_ffmpeg_binary()
    encode_args = build_encode_args(audio_bitrate="192k")

    crop_w = trajectory[0].crop_w
    crop_h = trajectory[0].crop_h

    # Check if crop is completely static
    is_static = all(
        cf.crop_x == trajectory[0].crop_x and cf.crop_y == trajectory[0].crop_y
        for cf in trajectory
    )

    sendcmd_path = None
    if is_static:
        vf = f"crop=w={crop_w}:h={crop_h}:x={trajectory[0].crop_x}:y={trajectory[0].crop_y},scale={target_w}:{target_h}"
    else:
        fd, sendcmd_path = tempfile.mkstemp(suffix=".sendcmd", text=True)
        with os.fdopen(fd, "w") as f:
            for cf in trajectory:
                t = cf.frame_idx / fps
                f.write(f"{t:.3f} crop x {cf.crop_x}, crop y {cf.crop_y};\n")
        
        # Escape single quotes in path for FFmpeg filtergraph
        escaped_path = sendcmd_path.replace("'", r"\'")
        vf = f"sendcmd=f='{escaped_path}',crop=w={crop_w}:h={crop_h}:x={trajectory[0].crop_x}:y={trajectory[0].crop_y},scale={target_w}:{target_h}"

    cmd = [
        ffmpeg_bin, "-y",
        "-loglevel", "error",
        "-i", input_path,
        "-vf", vf,
        "-map", "0:v:0",
        "-map", "0:a:0?",
    ] + encode_args + [
        output_path
    ]

    logger.debug(f"render_clip ffmpeg cmd: {' '.join(cmd)}")

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    
    _, stderr_data = process.communicate()
    
    if sendcmd_path and os.path.exists(sendcmd_path):
        try:
            os.remove(sendcmd_path)
        except OSError:
            pass
            
    if process.returncode != 0:
        err_msg = stderr_data.decode('utf-8', errors='replace') if stderr_data else "Unknown error"
        logger.error(f"render_clip: FFmpeg exited with code {process.returncode}. Stderr: {err_msg}")
        return False

    logger.info(f"render_clip: wrote to {output_path}")
    return True
