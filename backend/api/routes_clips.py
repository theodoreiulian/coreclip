from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional

from database import get_db
from models.clip import Clip
from models.schemas import ClipResponse, ClipMetadataUpdate, ClipListResponse

router = APIRouter()

@router.get("/jobs/{job_id}/clips", response_model=ClipListResponse)
def list_clips_for_job(
    job_id: str,
    status: Optional[str] = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db)
):
    query = db.query(Clip).filter(Clip.job_id == job_id)
    if status:
        query = query.filter(Clip.status == status)

    total = query.count()
    clips = (
        query.order_by(Clip.clip_index.asc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    return {
        "items": clips,
        "total": total,
        "page": page,
        "per_page": per_page,
    }

@router.get("/clips/{clip_id}", response_model=ClipResponse)
def get_clip(clip_id: str, db: Session = Depends(get_db)):
    clip = db.query(Clip).filter(Clip.id == clip_id).first()
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")
    return clip

@router.patch("/clips/{clip_id}/metadata", response_model=ClipResponse)
def update_clip_metadata(clip_id: str, update_data: ClipMetadataUpdate, db: Session = Depends(get_db)):
    clip = db.query(Clip).filter(Clip.id == clip_id).first()
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")
        
    if update_data.title is not None:
        clip.title = update_data.title
    if update_data.description is not None:
        clip.description = update_data.description
    if update_data.hashtags is not None:
        clip.hashtags = update_data.hashtags
    if update_data.platform_variants is not None:
        clip.platform_variants = update_data.platform_variants
        
    db.commit()
    db.refresh(clip)
    return clip

@router.post("/clips/{clip_id}/approve", response_model=ClipResponse)
def approve_clip(clip_id: str, db: Session = Depends(get_db)):
    clip = db.query(Clip).filter(Clip.id == clip_id).first()
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")
    
    clip.status = "approved"
    db.commit()
    db.refresh(clip)
    return clip

@router.post("/clips/{clip_id}/reject", response_model=ClipResponse)
def reject_clip(clip_id: str, db: Session = Depends(get_db)):
    clip = db.query(Clip).filter(Clip.id == clip_id).first()
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")
    
    clip.status = "rejected"
    db.commit()
    db.refresh(clip)
    return clip

from fastapi.responses import FileResponse
import os

@router.get("/clips/{clip_id}/preview")
def preview_clip(clip_id: str, db: Session = Depends(get_db)):
    clip = db.query(Clip).filter(Clip.id == clip_id).first()
    if not clip or not clip.final_clip_path or not os.path.exists(clip.final_clip_path):
        raise HTTPException(status_code=404, detail="Clip video not found")
    return FileResponse(clip.final_clip_path, media_type="video/mp4", headers={"Accept-Ranges": "bytes"})

@router.get("/clips/{clip_id}/preview/{version}")
def preview_clip_version(clip_id: str, version: str, db: Session = Depends(get_db)):
    clip = db.query(Clip).filter(Clip.id == clip_id).first()
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")
        
    path = None
    if version == "raw": path = clip.raw_clip_path
    elif version == "vertical": path = clip.vertical_clip_path
    elif version == "captioned": path = clip.final_clip_path
    
    if not path or not os.path.exists(path):
        raise HTTPException(status_code=404, detail=f"{version} video not found")
        
    return FileResponse(path, media_type="video/mp4", headers={"Accept-Ranges": "bytes"})

@router.post("/jobs/{job_id}/approve-all")
def approve_all_clips(job_id: str, db: Session = Depends(get_db)):
    clips = db.query(Clip).filter(Clip.job_id == job_id, Clip.status == "ready_for_review").all()
    for clip in clips:
        clip.status = "approved"
    db.commit()
    return {"message": f"Approved {len(clips)} clips"}

@router.post("/jobs/{job_id}/reject-all")
def reject_all_clips(job_id: str, db: Session = Depends(get_db)):
    clips = db.query(Clip).filter(Clip.job_id == job_id, Clip.status == "ready_for_review").all()
    for clip in clips:
        clip.status = "rejected"
    db.commit()
    return {"message": f"Rejected {len(clips)} clips"}
