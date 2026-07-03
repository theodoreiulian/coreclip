import logging
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from sqlalchemy.orm import Session
from database import SessionLocal
from models.job import Job
from models.clip import Clip
from pipeline import ingestion, audio_analysis, moment_detection, clip_extraction, reformat, captioning
from services.redis_pubsub import publish_status, publish_status_batch
from utils.file_manager import cleanup_source_files, cleanup_intermediates, ensure_disk_space
from utils.logger import get_job_logger
from config import config

logger = logging.getLogger("clipper")

STAGES = [
    "ingesting",
    "analyzing",
    "clipping",
    "processing",
    "captioning",
]


class PipelineCancelledError(Exception):
    pass


class _BatchPublisher:
    """Buffers per-clip status events and publishes them in batches so the
    processing/captioning loops don't emit one Redis publish per clip."""

    def __init__(self, orchestrator, max_size=10, flush_interval=1.0):
        self.orchestrator = orchestrator
        self.max_size = max_size
        self.flush_interval = flush_interval
        self._buffer = []
        self._last_flush = time.monotonic()

    def publish(self, stage, status, message=None, clip_id=None):
        self._buffer.append({
            "stage": stage,
            "status": status,
            "message": message,
            "clip_id": clip_id,
            "timestamp": time.time(),
        })
        if (
            len(self._buffer) >= self.max_size
            or time.monotonic() - self._last_flush >= self.flush_interval
        ):
            self.flush()

    def flush(self):
        if self._buffer:
            publish_status_batch(self.orchestrator.job_id, self._buffer)
            self._buffer = []
            self._last_flush = time.monotonic()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.flush()
        return False


def _reformat_clip_worker(clip_id: str, job_id: str) -> dict:
    """Runs in a child process: reformat one clip to vertical."""
    get_job_logger(job_id)
    db = SessionLocal()
    try:
        clip = db.query(Clip).filter(Clip.id == clip_id).first()
        if not clip:
            return {"clip_id": clip_id, "success": False, "error": "Clip not found"}
        reformat.to_vertical(clip, db)
        return {"clip_id": clip_id, "success": True}
    except Exception as e:
        logger.error(f"Failed to reformat clip {clip_id}: {e}")
        try:
            db.rollback()
            clip = db.query(Clip).filter(Clip.id == clip_id).first()
            if clip:
                clip.status = "failed"
                clip.error_message = str(e)
                db.commit()
        except Exception as inner:
            logger.error(f"Failed to persist clip failure state: {inner}")
        return {"clip_id": clip_id, "success": False, "error": str(e)}
    finally:
        db.close()


def _caption_clip_worker(clip_id: str, job_id: str) -> dict:
    """Runs in a child process: burn captions for one clip."""
    get_job_logger(job_id)
    db = SessionLocal()
    try:
        clip = db.query(Clip).filter(Clip.id == clip_id).first()
        if not clip:
            return {"clip_id": clip_id, "success": False, "error": "Clip not found"}
        captioning.generate(clip, db)
        clip.status = "ready_for_review"
        db.commit()
        return {"clip_id": clip_id, "success": True}
    except Exception as e:
        logger.error(f"Failed to caption clip {clip_id}: {e}")
        try:
            db.rollback()
            clip = db.query(Clip).filter(Clip.id == clip_id).first()
            if clip:
                clip.status = "failed"
                clip.error_message = str(e)
                db.commit()
        except Exception as inner:
            logger.error(f"Failed to persist clip failure state: {inner}")
        return {"clip_id": clip_id, "success": False, "error": str(e)}
    finally:
        db.close()


class PipelineOrchestrator:
    def __init__(self, job_id: str, resume_from: str = None):
        self.job_id = job_id
        self.resume_from = resume_from
        self._last_cancel_check = 0.0

    def _publish(self, stage, status, message=None, clip_id=None):
        publish_status(self.job_id, {
            "stage": stage,
            "status": status,
            "message": message,
            "clip_id": clip_id,
            "timestamp": time.time()
        })

    def _check_cancelled(self, db: Session, force: bool = False):
        """Lightweight cancellation check, throttled to one status query
        every couple of seconds so it can sit inside clip loops."""
        now = time.monotonic()
        if not force and now - self._last_cancel_check < 2.0:
            return
        self._last_cancel_check = now
        status = db.query(Job.status).filter(Job.id == self.job_id).scalar()
        if status in ("failed", "rejected", "cancelled"):
            raise PipelineCancelledError(f"Job {self.job_id} is {status}")

    def _clip_workers(self) -> int:
        configured = config.CLIP_PROCESSING_MAX_WORKERS
        if configured > 0:
            return configured
        return max(1, min(os.cpu_count() or 1, 4))

    def _run_clip_stage(self, job: Job, db: Session, worker_fn, stage: str):
        """Run a per-clip worker over all non-failed clips in a process pool.

        Celery worker concurrency stays at 1 (Whisper model); this local pool
        parallelizes only the CPU-heavy reformat/caption work per punchlist.
        """
        clip_ids = [
            row[0] for row in db.query(Clip.id).filter(
                Clip.job_id == job.id, Clip.status != "failed"
            ).yield_per(50)
        ]

        if not clip_ids:
            return

        with _BatchPublisher(self) as batch:
            max_workers = min(self._clip_workers(), len(clip_ids))
            with ProcessPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(worker_fn, clip_id, job.id): clip_id
                    for clip_id in clip_ids
                }
                for future in as_completed(futures):
                    self._check_cancelled(db)
                    clip_id = futures[future]
                    try:
                        result = future.result()
                        if result["success"]:
                            batch.publish(stage, "in_progress", clip_id=clip_id)
                        else:
                            batch.publish(stage, "failed", message=result.get("error"), clip_id=clip_id)
                    except Exception as e:
                        logger.error(f"Unexpected error waiting for clip {clip_id}: {e}")
                        batch.publish(stage, "failed", message=str(e), clip_id=clip_id)

        # Child processes committed their own sessions; drop stale state.
        db.expire_all()

    def run(self):
        db = SessionLocal()
        try:
            get_job_logger(self.job_id)
            job = db.query(Job).filter(Job.id == self.job_id).first()
            if not job:
                return

            start_idx = 0
            if self.resume_from and self.resume_from in STAGES:
                start_idx = STAGES.index(self.resume_from)

            for stage in STAGES[start_idx:]:
                try:
                    self._check_cancelled(db, force=True)
                except PipelineCancelledError:
                    logger.info(f"Job {self.job_id} cancelled before {stage}")
                    return

                job.status = stage
                db.commit()
                self._publish(stage, "started")

                try:
                    handler = getattr(self, f"handle_{stage}")
                    handler(job, db)
                    self._publish(stage, "completed")
                except PipelineCancelledError:
                    logger.info(f"Job {self.job_id} cancelled during {stage}")
                    self._publish(stage, "cancelled")
                    return
                except Exception as e:
                    job.status = "failed"
                    job.failed_stage = stage
                    job.error_message = str(e)
                    db.commit()
                    self._publish(stage, "failed", str(e))
                    raise

            db.refresh(job)
            if job.status == "failed":
                return

            # Clips always require manual review before posting.
            if job.total_clips_found == 0:
                job.status = "completed"
            else:
                job.status = "ready_for_review"

            db.commit()
            self._publish("pipeline", "completed")

        finally:
            db.close()

    def handle_ingesting(self, job, db):
        ingestion.download_video(job, db)
        ingestion.transcribe(job, db)

    def handle_analyzing(self, job, db):
        if config.ENABLE_AUDIO_ANALYSIS:
            self.summary = audio_analysis.compute_features(job, db)
        else:
            logger.info(f"Audio analysis disabled for job {job.id}")
            self.summary = ""

    def handle_clipping(self, job, db):
        ensure_disk_space(config.MIN_DISK_SPACE_GB)
        summary = getattr(self, 'summary', "")
        moment_detection.detect_moments(job, db, summary)
        if job.total_clips_found > 0:
            clip_extraction.extract_all(job, db)
        if getattr(config, 'CLEANUP_SOURCE_AFTER_CLIPPING', False):
            cleanup_source_files(job.id)

    def handle_processing(self, job, db):
        ensure_disk_space(config.MIN_DISK_SPACE_GB)
        self._run_clip_stage(job, db, _reformat_clip_worker, "processing")

    def handle_captioning(self, job, db):
        ensure_disk_space(config.MIN_DISK_SPACE_GB)
        self._run_clip_stage(job, db, _caption_clip_worker, "captioning")
        if getattr(config, 'CLEANUP_INTERMEDIATES', False):
            cleanup_intermediates(job.id)
