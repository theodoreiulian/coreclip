from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from models.job import Job
from models.clip import Clip
from models.account import Account
from models.scheduled_post import ScheduledPost
from models.schemas import PostingPlanResponse, ScheduleRequest
from utils.logger import logger, current_job_id, current_stage

router = APIRouter()


def _to_naive_utc(dt: datetime) -> datetime:
    """Scheduled times are compared against datetime.utcnow() (naive UTC)."""
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


@router.get("/jobs/{job_id}/posting-plan", response_model=PostingPlanResponse)
def get_posting_plan(job_id: str, db: Session = Depends(get_db)):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    clips = db.query(Clip).filter(
        Clip.job_id == job_id, Clip.status == "approved"
    ).order_by(Clip.clip_index).all()

    accounts = db.query(Account).filter(
        Account.connected == True  # noqa: E712
    ).order_by(Account.platform, Account.created_at).all()

    posts = db.query(ScheduledPost).join(
        Clip, ScheduledPost.clip_id == Clip.id
    ).filter(Clip.job_id == job_id).all()

    return PostingPlanResponse(
        job_status=job.status,
        clips=clips,
        accounts=accounts,
        scheduled_posts=posts,
    )


@router.post("/jobs/{job_id}/schedule")
def schedule_posts(job_id: str, payload: ScheduleRequest, db: Session = Depends(get_db)):
    """Replace the job's posting plan and move it into the posting phase.

    Creates one ScheduledPost per (approved clip in the request) x
    (selected account). The posting beat task takes it from there.
    """
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != "ready_for_review":
        raise HTTPException(status_code=400, detail="Job is not awaiting review")

    accounts = db.query(Account).filter(
        Account.id.in_(payload.account_ids), Account.connected == True  # noqa: E712
    ).all()
    if len(accounts) != len(set(payload.account_ids)):
        raise HTTPException(status_code=400, detail="One or more accounts are invalid or disconnected")

    approved = {
        c.id: c for c in db.query(Clip).filter(
            Clip.job_id == job_id, Clip.status == "approved"
        ).all()
    }

    # clear any previous plan for this job's clips
    if approved:
        old = db.query(ScheduledPost).filter(
            ScheduledPost.clip_id.in_(approved.keys())
        ).all()
        for sp in old:
            db.delete(sp)

    # Floor every scheduled time at "now": a past time would make the beat fire it on the
    # next tick, dumping the post immediately (and, with several, all at once). The frontend
    # already keeps times live, so this is a defense-in-depth guard against a stale/replayed
    # request — scheduling in the past must not be possible.
    now_utc = datetime.utcnow()

    created = 0
    for item in payload.clips:
        clip = approved.get(item.clip_id)
        if clip is None:
            continue
        when = _to_naive_utc(item.scheduled_time)
        if when < now_utc:
            logger.warning(
                f"Scheduled time {when} UTC for clip {clip.id} is in the past; clamping to now"
            )
            when = now_utc
        clip.scheduled_post_time = when
        for account in accounts:
            db.add(ScheduledPost(
                clip_id=clip.id,
                account_id=account.id,
                platform=account.platform,
                scheduled_time=when,
            ))
            created += 1

    # Log into this job's posting tab so the user sees something immediately.
    current_job_id.set(job_id)
    current_stage.set("posting")
    try:
        if created == 0:
            job.status = "completed"
            db.commit()
            logger.warning("Nothing to post — pick at least one account and one clip")
            return {"status": "completed", "scheduled_posts": 0,
                    "message": "Nothing to post — pick at least one account and one clip"}

        job.status = "posting"
        db.commit()
        times = sorted({sp.scheduled_time for sp in db.query(ScheduledPost).join(
            Clip, ScheduledPost.clip_id == Clip.id).filter(Clip.job_id == job_id).all()})
        next_at = times[0] if times else None
        logger.info(
            f"Scheduled {created} post(s) across {len(accounts)} account(s). "
            f"The scheduler checks every ~60s; the first is due at "
            f"{next_at} UTC." if next_at else f"Scheduled {created} post(s)."
        )
        return {"status": "posting", "scheduled_posts": created}
    finally:
        current_stage.set(None)
        current_job_id.set(None)
