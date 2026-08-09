"""
Speaker diarization service using pyannote.audio.

Determines WHO is speaking WHEN in an audio file.
Returns a list of (speaker_id, start_time, end_time) segments.
"""

import logging
import os

logger = logging.getLogger("clipper")

_pipeline = None
_pipeline_loaded = False


def _load_pipeline():
    """Load the pyannote speaker diarization pipeline."""
    global _pipeline, _pipeline_loaded
    if _pipeline_loaded:
        return _pipeline

    try:
        from pyannote.audio import Pipeline
        import torch

        # Get HuggingFace token from env
        hf_token = os.environ.get("HUGGINGFACE_TOKEN") or os.environ.get("HF_TOKEN")
        if not hf_token:
            logger.warning(
                "No HUGGINGFACE_TOKEN set. Speaker diarization requires a HuggingFace token. "
                "See: https://huggingface.co/pyannote/speaker-diarization-3.1"
            )
            _pipeline_loaded = True
            return None

        logger.info("Loading pyannote speaker diarization model...")
        _pipeline = Pipeline.from_pretrained(
            "pyannote/speaker-diarization-3.1",
            token=hf_token,
        )

        # Use MPS on Apple Silicon if available, otherwise CPU
        if torch.backends.mps.is_available():
            _pipeline = _pipeline.to(torch.device("mps"))
            logger.info("Speaker diarization loaded on MPS (Apple Silicon)")
        else:
            logger.info("Speaker diarization loaded on CPU")

    except ImportError:
        logger.warning("pyannote.audio not installed. Speaker diarization disabled.")
        _pipeline = None
    except Exception as e:
        logger.warning(f"Failed to load speaker diarization: {e}")
        _pipeline = None

    _pipeline_loaded = True
    return _pipeline


def diarize(audio_path: str, num_speakers: int | None = None) -> list[dict]:
    """
    Run speaker diarization on an audio file.

    Args:
        audio_path: Path to WAV or audio file.
        num_speakers: Optional hint for number of speakers.

    Returns:
        List of segments: [{"speaker": "SPEAKER_00", "start": 0.5, "end": 3.2}, ...]
        Sorted by start time.
    """
    pipeline = _load_pipeline()
    if pipeline is None:
        logger.info("Speaker diarization unavailable, returning empty segments")
        return []

    try:
        logger.info(f"Running speaker diarization on {audio_path}")

        kwargs = {}
        if num_speakers is not None:
            kwargs["num_speakers"] = num_speakers

        diarization = pipeline(audio_path, **kwargs)

        segments = []
        for turn, _, speaker in diarization.itertracks(yield_label=True):
            segments.append({
                "speaker": speaker,
                "start": turn.start,
                "end": turn.end,
            })

        # Sort by start time
        segments.sort(key=lambda s: s["start"])

        # Log summary
        speakers = set(s["speaker"] for s in segments)
        logger.info(
            f"Diarization complete: {len(segments)} segments, "
            f"{len(speakers)} speakers detected"
        )

        return segments

    except Exception as e:
        logger.error(f"Speaker diarization failed: {e}")
        return []
