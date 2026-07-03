import time
from tasks.celery_app import celery_app
from database import SessionLocal
from models.job import Job
import logging

logger = logging.getLogger("clipper")

from config import config

@celery_app.task(
    bind=True,
    max_retries=1,
    queue="pipeline",
    soft_time_limit=config.PIPELINE_SOFT_TIME_LIMIT,
    time_limit=config.PIPELINE_TIME_LIMIT
)
def process_job(self, job_id: str, resume_from: str = None):
    """Stub task for processing a job."""
    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            return
            
        job.celery_task_id = self.request.id
        db.commit()
        
        from pipeline.orchestrator import PipelineOrchestrator
        orchestrator = PipelineOrchestrator(job_id, resume_from)
        orchestrator.run()
        
    except Exception as e:
        logger.error(f"Job {job_id} orchestrator failed: {e}")
        try:
            job = db.query(Job).filter(Job.id == job_id).first()
            if job and job.status != "failed":
                job.status = "failed"
                job.error_message = str(e)
                db.commit()
        except Exception as inner_e:
            logger.error(f"Failed to update job status to failed: {inner_e}")
            db.rollback()
        raise
    finally:
        db.close()

@celery_app.task(
    bind=True,
    max_retries=3,
    queue="posting",
    soft_time_limit=config.POSTING_SOFT_TIME_LIMIT,
    time_limit=config.POSTING_TIME_LIMIT
)
def post_clip(self, scheduled_post_id: str):
    """Upload one ScheduledPost (a clip → a single account)."""
    db = SessionLocal()
    from utils.logger import current_job_id, current_clip_id, current_stage
    try:
        from datetime import datetime
        from models.clip import Clip
        from models.account import Account
        from models.scheduled_post import ScheduledPost
        from pipeline.distribution import get_platform_client

        sp = db.query(ScheduledPost).filter(ScheduledPost.id == scheduled_post_id).first()
        if not sp or sp.status in ("posted", "cancelled"):
            return

        clip = db.query(Clip).filter(Clip.id == sp.clip_id).first()
        account = db.query(Account).filter(Account.id == sp.account_id).first()

        # Tag every log below with this job + the 'posting' stage so they land in
        # the per-job log file and show up under the UI's Posting tab.
        if clip:
            current_job_id.set(clip.job_id)
            current_clip_id.set(clip.id)
        current_stage.set("posting")

        if not clip or not clip.final_clip_path or not account:
            logger.error("Cannot post: missing clip video or account")
            sp.status = "failed"
            sp.error_message = "Missing clip video or account"
            db.commit()
            return

        attempt = self.request.retries + 1
        logger.info(
            f"▶ Posting clip '{clip.title or clip.id}' to {sp.platform} "
            f"account @{account.display_name} (attempt {attempt}/{self.max_retries + 1})"
        )
        try:
            client = get_platform_client(sp.platform)
            logger.info(f"Validating video: {clip.final_clip_path}")
            val = client.validate_video(clip.final_clip_path)
            if not val.valid:
                raise Exception(f"Validation failed: {val.error_message}")
            logger.info("Video validated OK")

            metadata = {
                "title": clip.title,
                "description": clip.description,
                "hashtags": clip.hashtags,
                "platform_variants": clip.platform_variants,
                "account": account.display_name,
                "account_handle": account.display_name,
                "account_external_id": account.external_id,
            }

            # TikTok: hand the browser session the path. The post is already due
            # (check_scheduled_posts only dispatches due rows), so we publish now
            # via the Post button rather than TikTok's native scheduler.
            if sp.platform == "tiktok":
                metadata["account_session_path"] = account.session_path
                logger.info("Handing off to TikTok browser automation (posting now)")

            res = client.upload(clip.final_clip_path, metadata)
            if res.error_message:
                raise Exception(res.error_message)

            sp.status = "posted"
            sp.result_url = res.url
            sp.posted_at = datetime.utcnow()
            sp.error_message = None
            db.commit()
            logger.info(
                f"✓ Done: clip posted/scheduled to {sp.platform}/@{account.display_name}"
                + (f" → {res.url}" if res.url else "")
            )

        except Exception as e:
            logger.error(f"✗ Failed to post to {sp.platform}/@{account.display_name}: {e}")
            if self.request.retries < self.max_retries:
                sp.status = "posting"
                sp.error_message = str(e)
                try:
                    db.commit()
                except Exception as inner_e:
                    logger.error(f"Failed to update sp status to posting: {inner_e}")
                    db.rollback()
                logger.warning(
                    f"Will retry in 60s (attempt {attempt + 1}/{self.max_retries + 1})"
                )
                raise self.retry(exc=e, countdown=60)
            sp.status = "failed"
            sp.error_message = str(e)
            try:
                db.commit()
            except Exception as inner_e:
                logger.error(f"Failed to update sp status to failed: {inner_e}")
                db.rollback()
            logger.error("Giving up after final retry — marked failed")
    finally:
        # Don't let the posting tags bleed into the next task in this worker.
        current_stage.set(None)
        current_clip_id.set(None)
        current_job_id.set(None)
        db.close()


@celery_app.task(queue="posting")
def check_scheduled_posts():
    """Beat task: dispatch any ScheduledPost that has come due, and mark
    posting jobs complete once all their posts reach a terminal state."""
    db = SessionLocal()
    try:
        from datetime import datetime
        from models.clip import Clip
        from models.scheduled_post import ScheduledPost

        due = db.query(ScheduledPost).filter(
            ScheduledPost.status == "pending",
            ScheduledPost.scheduled_time <= datetime.utcnow(),
        ).all()
        for sp in due:
            sp.status = "posting"
            db.commit()
            post_clip.delay(sp.id)

        # complete jobs whose posts are all done
        for job in db.query(Job).filter(Job.status == "posting").all():
            posts = db.query(ScheduledPost).join(
                Clip, ScheduledPost.clip_id == Clip.id
            ).filter(Clip.job_id == job.id).all()
            if posts and all(p.status in ("posted", "failed", "cancelled") for p in posts):
                job.status = "completed"
                db.commit()
    finally:
        db.close()
