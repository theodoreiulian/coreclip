import uuid
from datetime import datetime
from sqlalchemy import Column, String, Float, Integer, DateTime, JSON, ForeignKey, Index
from sqlalchemy.orm import relationship
from database import Base

def generate_uuid():
    return str(uuid.uuid4())

class Clip(Base):
    __tablename__ = "clips"

    id = Column(String, primary_key=True, default=generate_uuid)
    job_id = Column(String, ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    clip_index = Column(Integer, nullable=False)
    
    start_time = Column(Float)
    end_time = Column(Float)
    duration = Column(Float)
    virality_score = Column(Float)
    virality_rationale = Column(String)
    category = Column(String)
    
    status = Column(String, default="pending")
    
    raw_clip_path = Column(String)
    vertical_clip_path = Column(String)
    final_clip_path = Column(String)
    
    title = Column(String)
    description = Column(String)
    hashtags = Column(JSON)
    platform_variants = Column(JSON)
    post_results = Column(JSON)
    
    scheduled_post_time = Column(DateTime)
    error_message = Column(String)
    
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index('ix_clips_job_id_clip_index', 'job_id', 'clip_index'),
        Index('ix_clips_job_id_status', 'job_id', 'status'),
    )
    
    job = relationship("Job", backref="clips")
