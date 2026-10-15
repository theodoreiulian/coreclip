import numpy as np
import json
import os
import logging
from sqlalchemy.orm import Session
from models.job import Job
from config import config
from services.ffmpeg_client import decode_audio_mono

logger = logging.getLogger("clipper")

# The three features below reproduce librosa's defaults (rms, onset_strength,
# zero_crossing_rate) in plain numpy. librosa itself is not a dependency: it
# pulls in numba, llvmlite and scikit-learn — over 200 MB — for these calls.
_N_FFT = 2048
_HOP = 512
_N_MELS = 128
# Frames per STFT block; bounds peak memory to ~70 MB regardless of duration.
_STFT_BLOCK = 4096


def _frame_sums(values: np.ndarray, n_frames: int, width: int) -> np.ndarray:
    """Sum of `values[t*_HOP : t*_HOP + width]` for each frame t."""
    csum = np.concatenate(([0.0], np.cumsum(values, dtype=np.float64)))
    starts = np.arange(n_frames) * _HOP
    return csum[starts + width] - csum[starts]


def _rms(y: np.ndarray, n_frames: int) -> np.ndarray:
    padded = np.pad(y, _N_FFT // 2, mode="constant")
    return np.sqrt(_frame_sums(padded.astype(np.float64) ** 2, n_frames, _N_FFT) / _N_FFT)


def _zero_crossing_rate(y: np.ndarray, n_frames: int) -> np.ndarray:
    padded = np.pad(y, _N_FFT // 2, mode="edge")
    padded[np.abs(padded) <= 1e-10] = 0
    sign = np.signbit(padded)
    # A frame's first sample has no predecessor, so it holds _N_FFT - 1 pairs.
    return _frame_sums(sign[1:] != sign[:-1], n_frames, _N_FFT - 1) / _N_FFT


def _mel_filterbank(sr: int) -> np.ndarray:
    """Slaney-scale, area-normalised mel filters spanning 0..sr/2."""
    f_sp = 200.0 / 3
    min_log_hz = 1000.0
    min_log_mel = min_log_hz / f_sp
    logstep = np.log(6.4) / 27.0

    max_hz = sr / 2.0
    max_mel = min_log_mel + np.log(max_hz / min_log_hz) / logstep if max_hz >= min_log_hz else max_hz / f_sp
    mels = np.linspace(0.0, max_mel, _N_MELS + 2)
    mel_f = np.where(
        mels >= min_log_mel,
        min_log_hz * np.exp(logstep * (mels - min_log_mel)),
        f_sp * mels,
    )

    fft_f = np.fft.rfftfreq(_N_FFT, 1.0 / sr)
    fdiff = np.diff(mel_f)
    ramps = mel_f[:, None] - fft_f[None, :]
    lower = -ramps[:-2] / fdiff[:-1, None]
    upper = ramps[2:] / fdiff[1:, None]
    weights = np.maximum(0, np.minimum(lower, upper))
    weights *= (2.0 / (mel_f[2:] - mel_f[:-2]))[:, None]
    return weights.astype(np.float32)


def _onset_strength(y: np.ndarray, sr: int, n_frames: int) -> np.ndarray:
    """Spectral flux: mean positive frame-to-frame change of the log-mel spectrum."""
    padded = np.pad(y, _N_FFT // 2, mode="constant")
    # Periodic Hann window, as scipy.signal.get_window("hann", n) returns.
    window = (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(_N_FFT) / _N_FFT)).astype(np.float32)
    mel_basis = _mel_filterbank(sr)

    mel = np.empty((_N_MELS, n_frames), dtype=np.float32)
    for start in range(0, n_frames, _STFT_BLOCK):
        stop = min(start + _STFT_BLOCK, n_frames)
        segment = padded[start * _HOP:(stop - 1) * _HOP + _N_FFT]
        frames = np.lib.stride_tricks.sliding_window_view(segment, _N_FFT)[::_HOP]
        power = np.abs(np.fft.rfft(frames * window, axis=1)) ** 2
        mel[:, start:stop] = mel_basis @ power.T.astype(np.float32)

    log_mel = 10.0 * np.log10(np.maximum(1e-10, mel))
    log_mel = np.maximum(log_mel, log_mel.max() - 80.0)
    flux = np.maximum(0.0, log_mel[:, 1:] - log_mel[:, :-1]).mean(axis=0)
    # Shift by the lag plus the centring offset so onsets line up with frames.
    shift = 1 + _N_FFT // (2 * _HOP)
    return np.concatenate((np.zeros(shift), flux))[:n_frames]


def compute_features(job: Job, db: Session) -> str:
    logger.info(f"Starting audio analysis for job {job.id}")
    if not job.source_video_path or not os.path.exists(job.source_video_path):
        raise FileNotFoundError("Source video file not found")

    sr = 16000
    pcm = np.frombuffer(decode_audio_mono(job.source_video_path, sr), dtype="<i2")
    y = pcm.astype(np.float32) / 32768.0
    n_frames = 1 + len(y) // _HOP

    rms = _rms(y, n_frames)
    onset_env = _onset_strength(y, sr, n_frames)
    zcr = _zero_crossing_rate(y, n_frames)
    
    frames_per_sec = int(sr / 512)
    if frames_per_sec == 0:
        frames_per_sec = 1
        
    rms_1s = [float(np.mean(rms[i:i+frames_per_sec])) for i in range(0, len(rms), frames_per_sec)]
    flux_1s = [float(np.mean(onset_env[i:i+frames_per_sec])) for i in range(0, len(onset_env), frames_per_sec)]
    zcr_1s = [float(np.mean(zcr[i:i+frames_per_sec])) for i in range(0, len(zcr), frames_per_sec)]
    
    mean_rms = float(np.mean(rms_1s))
    std_rms = float(np.std(rms_1s))
    
    threshold = mean_rms + (1.5 * std_rms)
    peaks = [i for i, val in enumerate(rms_1s) if val > threshold]
    
    if len(peaks) < 3:
        threshold = mean_rms + (1.0 * std_rms)
        peaks = [i for i, val in enumerate(rms_1s) if val > threshold]

    events = []
    if peaks:
        current_event = {"start": peaks[0], "end": peaks[0]}
        for p in peaks[1:]:
            if p <= current_event["end"] + 2:
                current_event["end"] = p
            else:
                events.append(current_event)
                current_event = {"start": p, "end": p}
        events.append(current_event)

    summary_lines = ["Audio energy events detected:"]
    if not events:
        summary_lines.append("- No significant energy spikes detected.")
    
    for event in events:
        start_time = float(event["start"])
        end_time = float(event["end"] + 1)
        summary_lines.append(f"- {start_time:.1f}s–{end_time:.1f}s: HIGH energy spike")

    text_summary = "\n".join(summary_lines)
    
    features = {
        "rms_1s": rms_1s,
        "flux_1s": flux_1s,
        "zcr_1s": zcr_1s,
        "events": events
    }
    
    job_output_dir = os.path.join(config.OUTPUT_DIR, job.id)
    features_path = os.path.join(job_output_dir, 'audio_features.json')
    with open(features_path, 'w', encoding='utf-8') as f:
        json.dump(features, f)
        
    logger.info(f"Audio analysis complete. Found {len(events)} events.")
    return text_summary
