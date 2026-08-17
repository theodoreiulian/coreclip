import uuid
from datetime import datetime
from sqlalchemy import Column, String, Float, JSON, Integer, DateTime
from database import Base

def generate_uuid():
    return str(uuid.uuid4())

class Job(Base):
    __tablename__ = "jobs"

    id = Column(String, primary_key=True, default=generate_uuid)
    youtube_url = Column(String, nullable=False)
    # Not DB-unique on purpose: job uniqueness is enforced in application logic
    # (create_job) for non-failed jobs only, so a failed job can coexist with a
    # fresh attempt on the same URL. A hard UNIQUE index would reject that.
    youtube_video_id = Column(String, index=True)
    video_title = Column(String)
    video_duration_seconds = Column(Float)
    
    status = Column(String, default="queued", index=True)
    failed_stage = Column(String)
    target_platforms = Column(JSON)
    gemini_model = Column(String, default="gemini-3.1-flash-lite")
    
    source_video_path = Column(String)
    audio_path = Column(String)
    transcript_path = Column(String)
    
    total_clips_found = Column(Integer, default=0)
    error_message = Column(String)
    celery_task_id = Column(String)
    speaker_mapping = Column(JSON)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
