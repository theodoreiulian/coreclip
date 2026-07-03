import os
import shutil
from config import config

def ensure_dirs(job_id: str):
    job_output_dir = os.path.join(config.OUTPUT_DIR, job_id)
    os.makedirs(job_output_dir, exist_ok=True)
    
    jobs_log_dir = os.path.join(config.LOG_DIR, "jobs")
    os.makedirs(jobs_log_dir, exist_ok=True)
    
    return job_output_dir

def check_disk_space(min_gb: int) -> bool:
    """Check if there is at least min_gb gigabytes of free space in the output directory's drive."""
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    total, used, free = shutil.disk_usage(config.OUTPUT_DIR)
    free_gb = free / (1024 ** 3)
    return free_gb >= min_gb

def ensure_disk_space(min_gb: int) -> float:
    """Fail fast with a clear error if output disk space is below min_gb."""
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    total, used, free = shutil.disk_usage(config.OUTPUT_DIR)
    free_gb = free / (1024 ** 3)
    if free_gb < min_gb:
        raise RuntimeError(
            f"Insufficient disk space: {free_gb:.2f} GB free, "
            f"at least {min_gb} GB required in {config.OUTPUT_DIR}"
        )
    return free_gb

def get_clip_dir(job_id: str, clip_index: int) -> str:
    job_output_dir = os.path.join(config.OUTPUT_DIR, job_id)
    clip_dir = os.path.join(job_output_dir, f"clip_{clip_index}")
    os.makedirs(clip_dir, exist_ok=True)
    return clip_dir

def cleanup_source_files(job_id: str):
    """Delete source video for a job to reclaim disk space."""
    job_dir = os.path.join(config.OUTPUT_DIR, job_id)
    for filename in ["source.mp4"]:
        filepath = os.path.join(job_dir, filename)
        if os.path.exists(filepath):
            os.remove(filepath)

def cleanup_intermediates(job_id: str):
    """Delete intermediate clip renders (raw, vertical) keeping only captioned.mp4."""
    job_dir = os.path.join(config.OUTPUT_DIR, job_id)
    if not os.path.exists(job_dir):
        return
    for root, dirs, files in os.walk(job_dir):
        for f in files:
            if f in ["raw.mp4", "vertical.mp4"]:
                os.remove(os.path.join(root, f))

def cleanup_transcripts(job_id: str):
    """Delete per-clip transcript JSON files for a job."""
    job_dir = os.path.join(config.OUTPUT_DIR, job_id)
    if not os.path.exists(job_dir):
        return
    for root, dirs, files in os.walk(job_dir):
        for f in files:
            if f == "transcript.json":
                os.remove(os.path.join(root, f))

def cleanup_job_files(job_id: str):
    """Delete the entire output directory for a job."""
    job_dir = os.path.join(config.OUTPUT_DIR, job_id)
    if os.path.exists(job_dir):
        shutil.rmtree(job_dir)
