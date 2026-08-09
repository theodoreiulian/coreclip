from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from sqlalchemy import func, or_
from typing import Optional

from database import get_db
from models.job import Job
from models.clip import Clip
from models.schemas import JobCreate, JobResponse, JobListResponse, GenerateMetadataRequest
from utils.validators import extract_youtube_video_id
from utils.file_manager import check_disk_space
from config import config
from tasks.celery_tasks import process_job
from tasks.celery_app import celery_app

router = APIRouter()

@router.post("/jobs", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
def create_job(job_in: JobCreate, db: Session = Depends(get_db)):
    try:
        video_id = extract_youtube_video_id(job_in.youtube_url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
        
    existing_job = db.query(Job).filter(
        Job.youtube_video_id == video_id,
        Job.status != "failed"
    ).first()
    
    if existing_job:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": "Job already exists", "job_id": existing_job.id}
        )
        
    if not check_disk_space(config.MIN_DISK_SPACE_GB):
        raise HTTPException(
            status_code=507, 
            detail=f"Insufficient disk space. Need at least {config.MIN_DISK_SPACE_GB}GB."
        )
        
    job = Job(
        youtube_url=job_in.youtube_url,
        youtube_video_id=video_id,
        target_platforms=job_in.target_platforms,
        gemini_model=job_in.gemini_model,
        status="queued"
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    # Enqueue the work. If the broker (Redis) is unreachable, .delay() raises —
    # don't leave the job stranded as a zombie "queued" row that never runs.
    # Mark it failed with a clear reason and surface a 503 so the UI can show why.
    try:
        task = process_job.delay(job.id)
    except Exception as e:
        job.status = "failed"
        job.failed_stage = "queued"
        job.error_message = (
            "Could not enqueue the job — the task broker (Redis) is unreachable. "
            f"Make sure Redis is running. ({e})"
        )
        db.commit()
        raise HTTPException(
            status_code=503,
            detail={
                "message": "Job queue unavailable (Redis is not reachable). "
                           "Start Redis and try again.",
                "job_id": job.id,
            },
        )

    job.celery_task_id = task.id
    db.commit()
    db.refresh(job)

    job.clip_count = 0
    return job

def _attach_clip_counts(db: Session, jobs: list[Job]) -> list[Job]:
    """Attach clip_count to each job using a single COUNT subquery (avoids N+1)."""
    if not jobs:
        return jobs
    job_ids = [job.id for job in jobs]
    counts = (
        db.query(Clip.job_id, func.count(Clip.id).label("count"))
        .filter(Clip.job_id.in_(job_ids))
        .group_by(Clip.job_id)
        .all()
    )
    count_map = {row.job_id: row.count for row in counts}
    for job in jobs:
        job.clip_count = count_map.get(job.id, 0)
    return jobs


@router.get("/jobs", response_model=JobListResponse)
def list_jobs(
    status: Optional[str] = None,
    q: Optional[str] = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db)
):
    query = db.query(Job)
    if status:
        query = query.filter(Job.status == status)
    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                Job.youtube_url.ilike(like),
                Job.video_title.ilike(like),
            )
        )

    total = query.count()
    jobs = query.order_by(Job.created_at.desc()).offset((page - 1) * per_page).limit(per_page).all()
    _attach_clip_counts(db, jobs)

    return {
        "items": jobs,
        "total": total,
        "page": page,
        "per_page": per_page
    }

@router.get("/jobs/{job_id}", response_model=JobResponse)
def get_job(job_id: str, db: Session = Depends(get_db)):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    _attach_clip_counts(db, [job])
    return job

@router.delete("/jobs/{job_id}")
def delete_job(job_id: str, db: Session = Depends(get_db)):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
        
    # Revoke Celery task if running
    if job.celery_task_id and job.status not in ("completed", "failed", "rejected"):
        celery_app.control.revoke(job.celery_task_id, terminate=True)
    
    # Delete all output files
    from utils.file_manager import cleanup_job_files
    cleanup_job_files(job.id)
    
    # Delete clips, then the job
    db.query(Clip).filter(Clip.job_id == job.id).delete()
    db.delete(job)
    db.commit()
    
    return {"status": "deleted", "job_id": job_id}

@router.post("/jobs/{job_id}/retry")
def retry_job(job_id: str, db: Session = Depends(get_db)):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
        
    failed_stage = job.failed_stage
    job.status = "queued"
    job.error_message = None
    db.commit()
    
    process_job.delay(job.id, resume_from=failed_stage)
    
    return {"message": "Job retry initiated", "resume_from": failed_stage}

@router.post("/jobs/{job_id}/generate-metadata")
async def generate_metadata(
    job_id: str,
    payload: Optional[GenerateMetadataRequest] = None,
    db: Session = Depends(get_db),
):
    """Lazily generate posting metadata (title/description/hashtags/variants)
    for a job's clips. Metadata is no longer produced during the pipeline —
    it is generated on demand from the review screen, optionally for a subset
    of clips."""
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Pipeline stage names mirror pipeline.orchestrator.STAGES (not imported
    # here to keep torch/CV dependencies out of the API process).
    if job.status in ("queued", "ingesting", "analyzing", "clipping", "processing", "captioning"):
        raise HTTPException(
            status_code=409,
            detail="Job is still processing; metadata can be generated once clips are ready.",
        )

    from pipeline import metadata_generation
    clip_ids = payload.clip_ids if payload else None
    try:
        await metadata_generation.generate_for_clips(job, db, clip_ids)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Metadata generation failed: {e}")

    count = db.query(func.count(Clip.id)).filter(
        Clip.job_id == job.id, Clip.title.isnot(None)
    ).scalar()
    return {"status": "ok", "clips_with_metadata": count}

@router.get("/config/platforms")
def get_platforms_config():
    from services.token_manager import token_manager
    return token_manager.validate_all()

@router.delete("/jobs/{job_id}/files")
def delete_job_files(job_id: str, db: Session = Depends(get_db)):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job: raise HTTPException(404, "Job not found")
    if job.status not in ["completed", "failed", "rejected"]:
        raise HTTPException(400, "Job must be completed/failed")
    from utils.file_manager import cleanup_job_files
    cleanup_job_files(job_id)
    return {"status": "ok"}
