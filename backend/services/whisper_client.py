import logging
from tasks.celery_app import whisper_model
from config import config

logger = logging.getLogger("clipper")


def _format_segment(seg):
    words = []
    if getattr(seg, 'words', None):
        for w in seg.words:
            words.append({
                "word": w.word,
                "start": w.start,
                "end": w.end
            })

    return {
        "id": seg.id,
        "start": seg.start,
        "end": seg.end,
        "text": seg.text,
        "words": words
    }


def transcribe(audio_path: str) -> dict:
    """
    Transcribes audio and returns a structured JSON dict matching the plan.
    Uses batched inference when batch_size > 1 and the faster-whisper version
    supports it; falls back to the standard generator otherwise.
    """
    if not whisper_model:
        raise RuntimeError("Whisper model is not loaded. Ensure faster-whisper is installed and worker initialized.")

    logger.info(f"Transcribing audio: {audio_path}")

    common_kwargs = {
        "word_timestamps": True,
        "vad_filter": config.WHISPER_VAD_FILTER,
        "beam_size": config.WHISPER_BEAM_SIZE,
    }

    batch_size = config.WHISPER_BATCH_SIZE
    if batch_size > 1:
        try:
            from faster_whisper import BatchedInferencePipeline
            pipeline = BatchedInferencePipeline(whisper_model)
            segments_generator, info = pipeline.transcribe(
                audio_path,
                batch_size=batch_size,
                **common_kwargs
            )
        except Exception as e:
            logger.warning(f"Batched Whisper inference failed ({e}), falling back to standard transcribe.")
            segments_generator, info = whisper_model.transcribe(audio_path, **common_kwargs)
    else:
        segments_generator, info = whisper_model.transcribe(audio_path, **common_kwargs)

    segments = list(segments_generator)

    result = {
        "language": info.language,
        "duration": info.duration,
        "segments": [_format_segment(seg) for seg in segments]
    }

    return result


def slice_transcript(full_transcript: dict, start_time: float, end_time: float) -> dict:
    """
    Extracts the portion of a transcript within a time range, re-zeros timestamps by subtracting start_time.
    """
    sliced_segments = []

    for seg in full_transcript.get("segments", []):
        # Check if segment overlaps with the target range
        if seg["end"] < start_time or seg["start"] > end_time:
            continue

        sliced_words = []
        for w in seg.get("words", []):
            if w["end"] >= start_time and w["start"] <= end_time:
                # Re-zero timestamps
                sliced_words.append({
                    "word": w["word"],
                    "start": max(0.0, w["start"] - start_time),
                    "end": w["end"] - start_time
                })

        if sliced_words or (not seg.get("words") and (seg["end"] >= start_time and seg["start"] <= end_time)):
            sliced_segments.append({
                "id": seg["id"],
                "start": max(0.0, seg["start"] - start_time),
                "end": seg["end"] - start_time,
                "text": seg["text"],
                "words": sliced_words
            })

    return {
        "language": full_transcript.get("language"),
        "duration": end_time - start_time,
        "segments": sliced_segments
    }
