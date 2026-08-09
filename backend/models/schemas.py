from pydantic import BaseModel
from typing import List, Optional, Any, Dict
from datetime import datetime

class JobCreate(BaseModel):
    youtube_url: str
    target_platforms: List[str] = ["tiktok", "instagram", "youtube", "snapchat"]
    gemini_model: str = "gemini-2.5-flash"

class JobResponse(BaseModel):
    id: str
    youtube_url: str
    youtube_video_id: Optional[str] = None
    video_title: Optional[str] = None
    video_duration_seconds: Optional[float] = None
    status: str
    failed_stage: Optional[str] = None
    target_platforms: Optional[List[str]] = None
    gemini_model: str
    source_video_path: Optional[str] = None
    audio_path: Optional[str] = None
    transcript_path: Optional[str] = None
    total_clips_found: int
    error_message: Optional[str] = None
    celery_task_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    clip_count: Optional[int] = 0

    class Config:
        from_attributes = True

class ClipResponse(BaseModel):
    id: str
    job_id: str
    clip_index: int
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    duration: Optional[float] = None
    virality_score: Optional[float] = None
    virality_rationale: Optional[str] = None
    category: Optional[str] = None
    status: str
    raw_clip_path: Optional[str] = None
    vertical_clip_path: Optional[str] = None
    final_clip_path: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    hashtags: Optional[List[str]] = None
    platform_variants: Optional[Dict[str, Any]] = None
    post_results: Optional[Dict[str, Any]] = None
    scheduled_post_time: Optional[datetime] = None
    error_message: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True

class ClipMetadataUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    hashtags: Optional[List[str]] = None
    platform_variants: Optional[Dict[str, Any]] = None

class GenerateMetadataRequest(BaseModel):
    # None/empty means: all non-failed clips for the job.
    clip_ids: Optional[List[str]] = None

class ClipListResponse(BaseModel):
    items: List[ClipResponse]
    total: int
    page: int
    per_page: int

class JobListResponse(BaseModel):
    items: List[JobResponse]
    total: int
    page: int
    per_page: int


# ── Posting: accounts ('channels') ────────────────────────────────────

class AccountConnect(BaseModel):
    platform: str
    display_name: str
    external_id: Optional[str] = None

class AccountResponse(BaseModel):
    id: str
    platform: str
    display_name: str
    external_id: Optional[str] = None
    avatar_url: Optional[str] = None
    session_path: Optional[str] = None
    connected: bool
    expires_at: Optional[datetime] = None
    created_at: datetime

    class Config:
        from_attributes = True


# ── Posting: scheduled posts + plan ───────────────────────────────────

class ScheduledPostResponse(BaseModel):
    id: str
    clip_id: str
    account_id: str
    platform: str
    scheduled_time: datetime
    status: str
    result_url: Optional[str] = None
    error_message: Optional[str] = None
    posted_at: Optional[datetime] = None

    class Config:
        from_attributes = True

class PostingPlanResponse(BaseModel):
    job_status: str
    clips: List[ClipResponse]
    accounts: List[AccountResponse]
    scheduled_posts: List[ScheduledPostResponse]

class ClipScheduleItem(BaseModel):
    clip_id: str
    scheduled_time: datetime

class ScheduleRequest(BaseModel):
    account_ids: List[str]
    clips: List[ClipScheduleItem]
