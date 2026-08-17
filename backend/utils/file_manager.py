import logging
import os
import shutil
from config import config

logger = logging.getLogger("clipper")

# Terminal job states whose output may be deleted to reclaim space.
#
# `ready_for_review` is deliberately excluded — the user has not seen those
# clips yet and deleting them destroys the only copy of work they asked for.
# `posting` is excluded because its posts are still pending. Any in-flight
# pipeline status (clipping/processing/captioning) is excluded by the same rule,
# which is what makes it safe to call this from inside a running job.
_RECLAIMABLE_STATUSES = ("completed", "rejected", "failed")


def _free_gb(path: str) -> float:
    return shutil.disk_usage(path).free / (1024 ** 3)


def _dir_size_gb(path: str) -> float:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass  # raced with another cleanup; it's freed either way
    return total / (1024 ** 3)


def reclaim_disk_space(min_gb: int) -> float:
    """Free space by deleting the oldest expendable artifacts. Returns free GB.

    Runs only when already below `min_gb`, and stops the moment it is satisfied,
    so a healthy install never loses anything. Two passes, cheapest-to-lose
    first:

      1. Cached source downloads (`output/.cache`) — pure cache; ingestion
         re-downloads on demand, so losing these costs bandwidth, not data.
      2. Output of terminal jobs, oldest first — see `_RECLAIMABLE_STATUSES`.

    Clip rows keep their now-dangling paths; the download routes already check
    `os.path.exists` and return 404, so a reclaimed job degrades to "video no
    longer available" rather than an error.
    """
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    free = _free_gb(config.OUTPUT_DIR)
    if free >= min_gb:
        return free

    logger.warning(
        f"Low disk: {free:.2f} GB free, need {min_gb} GB - reclaiming space"
    )

    # ── Pass 1: cached source videos, oldest first ──
    cache_dir = os.path.join(config.OUTPUT_DIR, ".cache")
    if os.path.isdir(cache_dir):
        entries = []
        for name in os.listdir(cache_dir):
            path = os.path.join(cache_dir, name)
            try:
                entries.append((os.path.getmtime(path), path))
            except OSError:
                pass
        for _mtime, path in sorted(entries):
            if free >= min_gb:
                break
            try:
                size = os.path.getsize(path) / (1024 ** 3)
                os.remove(path)
                free = _free_gb(config.OUTPUT_DIR)
                logger.warning(f"Reclaimed {size:.2f} GB from source cache: {path}")
            except OSError as e:
                logger.warning(f"Could not remove cached source {path}: {e}")

    if free >= min_gb:
        logger.info(f"Disk recovered to {free:.2f} GB from the source cache alone")
        return free

    # ── Pass 2: output of terminal jobs, oldest first ──
    # Imported lazily: file_manager is otherwise dependency-light and is used by
    # scripts that have no reason to build a DB engine.
    try:
        from database import SessionLocal
        from models.job import Job
    except Exception as e:
        logger.warning(f"Cannot reclaim job output (no DB access): {e}")
        return free

    db = SessionLocal()
    try:
        stale = db.query(Job.id).filter(
            Job.status.in_(_RECLAIMABLE_STATUSES)
        ).order_by(Job.created_at.asc()).all()

        for (job_id,) in stale:
            if free >= min_gb:
                break
            reclaimed = 0.0
            for base in (config.OUTPUT_DIR, config.INTERMEDIATE_DIR):
                if not base:
                    continue
                job_dir = os.path.join(base, job_id)
                if not os.path.isdir(job_dir):
                    continue
                reclaimed += _dir_size_gb(job_dir)
                shutil.rmtree(job_dir, ignore_errors=True)
            if reclaimed > 0:
                free = _free_gb(config.OUTPUT_DIR)
                logger.warning(
                    f"Reclaimed {reclaimed:.2f} GB from terminal job {job_id}"
                )
    finally:
        db.close()

    if free < min_gb:
        logger.error(
            f"Reclaim exhausted: {free:.2f} GB free, still below {min_gb} GB"
        )
    else:
        logger.info(f"Disk recovered to {free:.2f} GB")
    return free


def ensure_dirs(job_id: str):
    job_output_dir = os.path.join(config.OUTPUT_DIR, job_id)
    os.makedirs(job_output_dir, exist_ok=True)
    
    jobs_log_dir = os.path.join(config.LOG_DIR, "jobs")
    os.makedirs(jobs_log_dir, exist_ok=True)
    
    return job_output_dir

def check_disk_space(min_gb: int) -> bool:
    """True if at least min_gb is free on the output directory's drive.

    Reclaims expendable artifacts before answering, so a queue that has simply
    accumulated finished jobs does not start rejecting new ones.
    """
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    if _free_gb(config.OUTPUT_DIR) >= min_gb:
        return True
    return reclaim_disk_space(min_gb) >= min_gb

def ensure_disk_space(min_gb: int) -> float:
    """Fail fast with a clear error if output disk space is below min_gb.

    Called before each disk-heavy stage. Space is reclaimed first — a long job
    can cross the threshold midway through, and freeing a finished job's output
    is much better than failing the one in flight.
    """
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    free_gb = _free_gb(config.OUTPUT_DIR)
    if free_gb < min_gb:
        free_gb = reclaim_disk_space(min_gb)
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

def get_intermediate_dir(job_id: str, clip_index: int) -> str:
    if config.INTERMEDIATE_DIR:
        clip_dir = os.path.join(config.INTERMEDIATE_DIR, job_id, f"clip_{clip_index}")
    else:
        job_output_dir = os.path.join(config.OUTPUT_DIR, job_id)
        clip_dir = os.path.join(job_output_dir, f"clip_{clip_index}")
    os.makedirs(clip_dir, exist_ok=True)
    return clip_dir

def cleanup_source_files(job_id: str):
    """Delete the job-level source media to reclaim disk space.

    `audio.wav` is a legacy artifact: no stage writes it today (the reframer
    pulls each clip's own embedded track instead, and Whisper reads the video
    directly), but `reformat.to_vertical` still consumes it opportunistically if
    present. Listing it keeps this function matching its documented contract at
    no cost — the existence check makes deleting an absent file a no-op.
    """
    job_dir = os.path.join(config.OUTPUT_DIR, job_id)
    for filename in ["source.mp4", "audio.wav"]:
        filepath = os.path.join(job_dir, filename)
        if os.path.exists(filepath):
            os.remove(filepath)

def cleanup_intermediates(job_id: str):
    """Delete intermediate clip renders (raw, vertical) keeping only captioned.mp4."""
    dirs_to_clean = [os.path.join(config.OUTPUT_DIR, job_id)]
    if config.INTERMEDIATE_DIR:
        dirs_to_clean.append(os.path.join(config.INTERMEDIATE_DIR, job_id))
        
    for job_dir in dirs_to_clean:
        if not os.path.exists(job_dir):
            continue
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
