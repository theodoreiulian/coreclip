import librosa
import numpy as np
import json
import os
import logging
from sqlalchemy.orm import Session
from models.job import Job
from config import config

logger = logging.getLogger("clipper")

def compute_features(job: Job, db: Session) -> str:
    logger.info(f"Starting audio analysis for job {job.id}")
    if not job.source_video_path or not os.path.exists(job.source_video_path):
        raise FileNotFoundError("Source video file not found")

    y, sr = librosa.load(job.source_video_path, sr=16000)
    
    rms = librosa.feature.rms(y=y)[0]
    onset_env = librosa.onset.onset_strength(y=y, sr=sr)
    zcr = librosa.feature.zero_crossing_rate(y=y)[0]
    
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
