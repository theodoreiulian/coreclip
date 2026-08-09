import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, ForeignKey, Index
from sqlalchemy.orm import relationship
from database import Base


def generate_uuid():
    return str(uuid.uuid4())


class ScheduledPost(Base):
    """One planned upload: a single clip going to a single account at a time.
    The posting beat task drives off these rows."""

    __tablename__ = "scheduled_posts"

    id = Column(String, primary_key=True, default=generate_uuid)
    clip_id = Column(String, ForeignKey("clips.id", ondelete="CASCADE"), nullable=False, index=True)
    account_id = Column(String, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    platform = Column(String, nullable=False)

    scheduled_time = Column(DateTime, nullable=False)
    status = Column(String, default="pending")  # pending|posting|posted|failed|cancelled

    result_url = Column(String)
    error_message = Column(String)
    posted_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_scheduled_posts_status_time", "status", "scheduled_time"),
    )

    clip = relationship("Clip")
    account = relationship("Account")
