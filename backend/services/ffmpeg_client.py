import os
import platform
import subprocess
import json
import logging
import shutil
from typing import Optional

from config import config

logger = logging.getLogger("clipper")


class FFmpegError(Exception):
    pass


# ── Binary discovery (memoized) ──────────────────────────────────────────

_FFMPEG_BIN: Optional[str] = None
_FFPROBE_BIN: Optional[str] = None
_ENCODERS_LIST: Optional[list[str]] = None
_SELECTED_ENCODER: Optional[str] = None


def _has_ass_filter(ffmpeg_path: str) -> bool:
    """Check whether the given ffmpeg binary was compiled with libass (ass filter)."""
    try:
        result = subprocess.run(
            [ffmpeg_path, "-filters"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        # The output line for the ass filter looks like:
        #   .. ass             V->V       Render ASS ...
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "ass":
                return True
    except Exception:
        pass
    return False


def _get_ffmpeg_binary() -> str:
    """Return an ffmpeg binary path that supports the ass filter.

    Falls back to Homebrew's ffmpeg-full on macOS if the default ffmpeg
    lacks libass support. The discovered path is cached for the process.
    """
    global _FFMPEG_BIN
    if _FFMPEG_BIN is not None:
        return _FFMPEG_BIN

    default = "ffmpeg"
    if _has_ass_filter(default):
        _FFMPEG_BIN = default
        return _FFMPEG_BIN

    # Common keg-only paths for Homebrew's ffmpeg-full formula
    candidates = [
        "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg",   # Apple Silicon
        "/usr/local/opt/ffmpeg-full/bin/ffmpeg",      # Intel Mac
    ]
    for candidate in candidates:
        if os.path.exists(candidate) and _has_ass_filter(candidate):
            logger.info(f"Using fallback ffmpeg with libass support: {candidate}")
            _FFMPEG_BIN = candidate
            return _FFMPEG_BIN

    _FFMPEG_BIN = default
    return _FFMPEG_BIN


def _get_ffprobe_binary() -> str:
    """Return the ffprobe binary that lives alongside the selected ffmpeg."""
    global _FFPROBE_BIN
    if _FFPROBE_BIN is not None:
        return _FFPROBE_BIN

    ffmpeg_path = _get_ffmpeg_binary()
    if ffmpeg_path == "ffmpeg":
        _FFPROBE_BIN = "ffprobe"
        return _FFPROBE_BIN
    ffprobe = os.path.join(os.path.dirname(ffmpeg_path), "ffprobe")
    if os.path.exists(ffprobe):
        _FFPROBE_BIN = ffprobe
        return _FFPROBE_BIN
    _FFPROBE_BIN = "ffprobe"
    return _FFPROBE_BIN


def _ensure_ass_support() -> None:
    """Raise a helpful error if no available ffmpeg has libass support."""
    ffmpeg_path = _get_ffmpeg_binary()
    if not _has_ass_filter(ffmpeg_path):
        raise FFmpegError(
            "FFmpeg was compiled without libass support. "
            "Caption burning requires the 'ass' filter.\n"
            "On macOS with Homebrew, install ffmpeg-full:\n"
            "  brew install ffmpeg-full\n"
            "Then add it to your PATH (ffmpeg-full is keg-only):\n"
            "  export PATH=\"/opt/homebrew/opt/ffmpeg-full/bin:$PATH\"   # Apple Silicon\n"
            "  export PATH=\"/usr/local/opt/ffmpeg-full/bin:$PATH\"     # Intel Mac"
        )


def _list_encoders() -> list[str]:
    """Return the list of available FFmpeg video encoders (cached)."""
    global _ENCODERS_LIST
    if _ENCODERS_LIST is not None:
        return _ENCODERS_LIST

    ffmpeg_path = _get_ffmpeg_binary()
    encoders: list[str] = []
    try:
        result = subprocess.run(
            [ffmpeg_path, "-encoders"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0].startswith("V"):
                encoders.append(parts[1])
    except Exception as e:
        logger.warning(f"Could not enumerate ffmpeg encoders: {e}")

    _ENCODERS_LIST = encoders
    return _ENCODERS_LIST


# ── Encoder selection ────────────────────────────────────────────────────

# Priority order is intentionally conservative: videotoolbox on macOS, then
# NVENC, then QSV, then the bullet-proof software fallback.
_ENCODER_PRIORITY = [
    "h264_videotoolbox",
    "hevc_videotoolbox",
    "h264_nvenc",
    "h264_qsv",
]


def _detect_hardware_encoder() -> str:
    """Pick the best available H.264/HEVC hardware encoder, or libx264."""
    global _SELECTED_ENCODER
    if _SELECTED_ENCODER is not None:
        return _SELECTED_ENCODER

    available = set(_list_encoders())

    preferred = config.FFMPEG_PREFERRED_ENCODER.strip()
    if preferred and preferred in available:
        logger.info(f"Using preferred encoder: {preferred}")
        _SELECTED_ENCODER = preferred
        return _SELECTED_ENCODER

    for codec in _ENCODER_PRIORITY:
        if codec in available:
            logger.info(f"Auto-selected hardware encoder: {codec}")
            _SELECTED_ENCODER = codec
            return _SELECTED_ENCODER

    logger.info("No hardware encoder found; falling back to libx264")
    _SELECTED_ENCODER = "libx264"
    return _SELECTED_ENCODER


def get_selected_encoder() -> str:
    """Public accessor for the encoder chosen for this process."""
    return _detect_hardware_encoder()


def _encoder_quality_args(codec: str, preset: str, crf: int) -> list[str]:
    """Return codec-specific quality arguments.

    Software encoders use -preset/-crf. Hardware encoders use -q:v/-cq and
    ignore CRF; the supplied preset is mapped where applicable.
    """
    if codec in {"h264_videotoolbox", "hevc_videotoolbox"}:
        # q:v roughly 0-100; 23 is a reasonable default for intermediates.
        # Some FFmpeg versions don't support -q:v for videotoolbox, so we use -b:v
        # A reasonable bitrate for intermediate is 5M
        return ["-b:v", "5M"]
    if codec == "h264_nvenc":
        nvenc_preset = "p4" if preset in {"ultrafast", "superfast", "veryfast"} else "p5"
        return ["-preset", nvenc_preset, "-cq", str(crf), "-b:v", "0"]
    if codec == "h264_qsv":
        return ["-preset", preset, "-q:v", str(crf)]
    # libx264 / software fallback
    return ["-preset", preset, "-crf", str(crf)]


def _threads_arg() -> list[str]:
    if config.FFMPEG_THREADS > 0:
        return ["-threads", str(config.FFMPEG_THREADS)]
    return ["-threads", "0"]


def build_encode_args(
    codec: Optional[str] = None,
    preset: Optional[str] = None,
    crf: Optional[int] = None,
    audio_codec: str = "aac",
    audio_bitrate: str = "128k",
    tune_faststart: bool = True,
) -> list[str]:
    """Build a reusable set of video encode arguments.

    Defaults come from config: intermediate preset/CRF for general use.
    """
    if codec is None:
        codec = _detect_hardware_encoder()
    if preset is None:
        preset = config.FFMPEG_INTERMEDIATE_PRESET
    if crf is None:
        crf = config.FFMPEG_INTERMEDIATE_CRF

    args = ["-c:v", codec] + _encoder_quality_args(codec, preset, crf)
    args += _threads_arg()
    args += ["-c:a", audio_codec]
    if audio_bitrate:
        args += ["-b:a", audio_bitrate]
    if tune_faststart:
        args += ["-movflags", "+faststart"]
    return args


# ── Process runners ──────────────────────────────────────────────────────


def run_ffmpeg(args: list[str], description: str) -> subprocess.CompletedProcess:
    ffmpeg_bin = _get_ffmpeg_binary()
    cmd = [ffmpeg_bin, "-y"] + args
    cmd_str = " ".join(cmd)
    logger.debug(f"FFmpeg ({description}): {cmd_str}")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        raise FFmpegError(f"ffmpeg executable not found in PATH: {ffmpeg_bin}")

    if result.returncode != 0:
        # If the command failed and the filter chain contains 'ass=', check
        # whether libass is missing so we can surface a clear error.
        if any("ass=" in str(a) for a in args):
            _ensure_ass_support()
        logger.error(f"FFmpeg failed ({description}): {result.stderr}")
        raise FFmpegError(f"FFmpeg failed ({description}): {result.stderr}")

    return result


def run_ffprobe(args: list[str]) -> dict:
    ffprobe_bin = _get_ffprobe_binary()
    cmd = [ffprobe_bin, "-v", "quiet", "-print_format", "json"] + args
    cmd_str = " ".join(cmd)
    logger.debug(f"ffprobe: {cmd_str}")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(result.stdout)
    except FileNotFoundError:
        raise FFmpegError(f"ffprobe executable not found in PATH: {ffprobe_bin}")
    except subprocess.CalledProcessError as e:
        logger.error(f"ffprobe failed: {e.stderr}")
        raise FFmpegError(f"ffprobe failed: {e.stderr}")
    except json.JSONDecodeError:
        raise FFmpegError("Failed to parse ffprobe output as JSON")


# ── Helpers ──────────────────────────────────────────────────────────────


def get_video_info(path: str) -> dict:
    """Return duration, width, height, codec with a selective ffprobe query."""
    args = [
        "-show_entries",
        "format=duration",
        "-show_entries",
        "stream=width,height,codec_name",
        path,
    ]
    data = run_ffprobe(args)

    video_stream = next(
        (stream for stream in data.get("streams", [])
         if stream.get("codec_type") == "video"),
        None,
    )
    if not video_stream:
        # Some files only have an audio stream; fall back gracefully.
        video_stream = next(
            (stream for stream in data.get("streams", [])),
            {},
        )

    fmt = data.get("format", {})
    duration = float(fmt.get("duration", 0)) if fmt.get("duration") else 0.0
    width = int(video_stream.get("width", 0)) if video_stream else 0
    height = int(video_stream.get("height", 0)) if video_stream else 0
    codec = video_stream.get("codec_name", "") if video_stream else ""

    return {
        "duration": duration,
        "width": width,
        "height": height,
        "codec": codec,
    }


def extract_audio(input_path: str, output_path: str):
    """Extract audio as 16kHz mono WAV (smallest viable format for Whisper)."""
    args = [
        "-i", input_path,
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", "16000",
        "-ac", "1",
        output_path,
    ]
    run_ffmpeg(args, "Extract Audio")


def extract_clip(input_path: str, output_path: str, start_time: float, duration: float):
    """Fast-seek clip extraction using the configured intermediate encoder."""
    args = [
        "-ss", str(start_time),
        "-i", input_path,
        "-t", str(duration),
    ] + build_encode_args(audio_bitrate="128k") + [
        "-avoid_negative_ts", "make_zero",
        output_path,
    ]
    run_ffmpeg(args, f"Extract Clip (fast) from {start_time} for {duration}s")


def extract_clip_accurate(input_path: str, output_path: str, start_time: float, end_time: float):
    """Frame-accurate clip extraction using the configured intermediate encoder."""
    args = [
        "-i", input_path,
        "-ss", str(start_time),
        "-to", str(end_time),
    ] + build_encode_args(audio_bitrate="128k") + [
        output_path,
    ]
    run_ffmpeg(args, f"Extract Clip (accurate) from {start_time} to {end_time}")


def build_final_burn_args(
    input_path: str,
    ass_path: str,
    output_path: str,
    fonts_dir: str,
) -> list[str]:
    """Build FFmpeg args for the final caption burn pass (high quality)."""
    escaped_ass_path = ass_path.replace("\\", "/").replace(":", "\\:").replace(",", "\\,")
    escaped_fonts_dir = fonts_dir.replace("\\", "/").replace(":", "\\:").replace(",", "\\,")

    codec = _detect_hardware_encoder()
    quality = _encoder_quality_args(
        codec,
        config.FFMPEG_FINAL_PRESET,
        config.FFMPEG_FINAL_CRF,
    )

    return [
        "-i", input_path,
        "-vf", f"ass={escaped_ass_path}:fontsdir={escaped_fonts_dir}",
        "-c:v", codec,
    ] + quality + _threads_arg() + [
        "-c:a", "copy",
        output_path,
    ]


def copy_video_stream(input_path: str, output_path: str) -> None:
    """Remux a video without re-encoding (used when captions are served separately)."""
    args = [
        "-i", input_path,
        "-c:v", "copy",
        "-c:a", "copy",
        "-movflags", "+faststart",
        output_path,
    ]
    run_ffmpeg(args, "Copy video stream")
