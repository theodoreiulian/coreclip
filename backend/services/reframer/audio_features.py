"""
Audio features for the reframer.

The clip's OWN embedded audio track is used — never the job-level audio.wav,
which is the whole source and therefore misaligned with a per-clip video.
Extracting from raw.mp4 guarantees the audio shares the video's timeline.

Provides:
  • MFCC at 100 fps  — the Light-ASD audio input
  • silero-VAD speech segments — the "is anyone speaking" gate
  • an RMS loudness envelope at 100 fps — used by the fallback scorer
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile

import numpy as np

logger = logging.getLogger("clipper")

SR = 16000          # everything runs at 16 kHz mono
MFCC_FPS = 100      # python_speech_features default winstep = 10 ms


def _extract_wav(video_path: str, wav_path: str) -> bool:
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vn", "-ac", "1", "-ar", str(SR),
        "-acodec", "pcm_s16le",
        wav_path, "-loglevel", "error",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0 or not os.path.exists(wav_path):
        logger.warning(f"Audio extract failed for {video_path}: {result.stderr[-300:]}")
        return False
    return True


class ClipAudio:
    """Audio analysis for one clip, all on a shared 16 kHz mono timeline."""

    def __init__(self, samples: np.ndarray, mfcc: np.ndarray,
                 envelope: np.ndarray, speech_segments: list[tuple[float, float]]):
        self.samples = samples                  # float32 in [-1, 1]
        self.mfcc = mfcc                         # (N, 13) at 100 fps
        self.envelope = envelope                 # (N,) RMS at 100 fps, normalised
        self.speech_segments = speech_segments   # list of (start_s, end_s)
        self.duration = len(samples) / SR if len(samples) else 0.0
        self.has_audio = len(samples) > 0

    def mfcc_window(self, t0: float, t1: float) -> np.ndarray:
        """MFCC rows covering [t0, t1) seconds. Shape (M, 13)."""
        if self.mfcc.shape[0] == 0:
            return np.zeros((0, 13), dtype=np.float32)
        i0 = max(0, int(round(t0 * MFCC_FPS)))
        i1 = min(self.mfcc.shape[0], int(round(t1 * MFCC_FPS)))
        if i1 <= i0:
            return np.zeros((0, 13), dtype=np.float32)
        return self.mfcc[i0:i1]

    def vad_speech_at(self, t: float) -> bool:
        """True if VAD reports speech at time t."""
        for s, e in self.speech_segments:
            if s <= t < e:
                return True
        return False

    def loudness(self, t: float, half_window: float = 0.05) -> float:
        if self.envelope.shape[0] == 0:
            return 0.0
        i0 = max(0, int((t - half_window) * MFCC_FPS))
        i1 = min(self.envelope.shape[0], int((t + half_window) * MFCC_FPS) + 1)
        if i1 <= i0:
            return 0.0
        return float(np.mean(self.envelope[i0:i1]))


def _compute_mfcc(samples_int16: np.ndarray) -> np.ndarray:
    try:
        import python_speech_features
        mfcc = python_speech_features.mfcc(
            samples_int16, SR, numcep=13, winlen=0.025, winstep=0.010
        )
        return np.asarray(mfcc, dtype=np.float32)
    except Exception as e:
        logger.warning(f"MFCC computation failed: {e}")
        return np.zeros((0, 13), dtype=np.float32)


def _compute_envelope(samples_f32: np.ndarray) -> np.ndarray:
    """RMS energy at 100 fps, peak-normalised to [0, 1]."""
    hop = SR // MFCC_FPS
    if len(samples_f32) < hop:
        return np.zeros(0, dtype=np.float32)
    n = len(samples_f32) // hop
    frames = samples_f32[: n * hop].reshape(n, hop)
    rms = np.sqrt(np.mean(frames ** 2, axis=1) + 1e-9)
    peak = float(np.max(rms)) if n else 0.0
    if peak > 1e-6:
        rms = rms / peak
    return rms.astype(np.float32)


_vad_model = None

def _compute_vad(samples_f32: np.ndarray, threshold: float) -> list[tuple[float, float]]:
    global _vad_model
    try:
        import torch
        from silero_vad import load_silero_vad, get_speech_timestamps

        if _vad_model is None:
            _vad_model = load_silero_vad()
        tensor = torch.from_numpy(samples_f32)
        ts = get_speech_timestamps(
            tensor, _vad_model, sampling_rate=SR, threshold=threshold,
        )
        return [(seg["start"] / SR, seg["end"] / SR) for seg in ts]
    except Exception as e:
        logger.warning(f"silero-VAD unavailable ({e}) — using energy-based VAD")
        return _energy_vad(samples_f32)


def _energy_vad(samples_f32: np.ndarray) -> list[tuple[float, float]]:
    """Crude fallback: speech = RMS above an adaptive floor."""
    env = _compute_envelope(samples_f32)
    if env.shape[0] == 0:
        return []
    floor = float(np.percentile(env, 20))
    gate = max(0.12, floor + 0.10)
    voiced = env > gate
    segs: list[tuple[float, float]] = []
    start = None
    for i, v in enumerate(voiced):
        t = i / MFCC_FPS
        if v and start is None:
            start = t
        elif not v and start is not None:
            if t - start > 0.15:
                segs.append((start, t))
            start = None
    if start is not None:
        segs.append((start, len(voiced) / MFCC_FPS))
    return segs


def load_clip_audio(video_path: str, vad_threshold: float = 0.5) -> ClipAudio:
    """Extract and analyse the clip's own audio track."""
    tmp_fd, tmp_wav = tempfile.mkstemp(suffix=".wav")
    os.close(tmp_fd)
    try:
        if not _extract_wav(video_path, tmp_wav):
            return ClipAudio(np.zeros(0, np.float32), np.zeros((0, 13), np.float32),
                             np.zeros(0, np.float32), [])
        from scipy.io import wavfile
        sr, data = wavfile.read(tmp_wav)
        if data.ndim > 1:
            data = data[:, 0]
        samples_int16 = data.astype(np.int16, copy=False)
        samples_f32 = (samples_int16.astype(np.float32)) / 32768.0

        mfcc = _compute_mfcc(samples_int16)
        envelope = _compute_envelope(samples_f32)
        speech = _compute_vad(samples_f32, vad_threshold)

        logger.info(
            f"ClipAudio: {len(samples_f32) / SR:.1f}s, "
            f"{mfcc.shape[0]} mfcc rows, {len(speech)} speech segments"
        )
        return ClipAudio(samples_f32, mfcc, envelope, speech)
    finally:
        if os.path.exists(tmp_wav):
            os.remove(tmp_wav)
