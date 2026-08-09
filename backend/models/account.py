import uuid
from datetime import datetime
from sqlalchemy import Column, String, Boolean, DateTime
from database import Base


def generate_uuid():
    return str(uuid.uuid4())


class Account(Base):
    """A connected social account ('channel'). Multiple accounts per platform
    are supported. A TikTok account is a logged-in browser session: `session_path`
    points at a persistent Playwright profile dir used to post on its behalf."""

    __tablename__ = "accounts"

    id = Column(String, primary_key=True, default=generate_uuid)
    platform = Column(String, nullable=False, index=True)  # tiktok|instagram|youtube|snapchat
    display_name = Column(String, nullable=False)          # handle / channel name
    external_id = Column(String)                           # platform-side account id (TikTok @handle)
    avatar_url = Column(String)                            # profile image, when available
    session_path = Column(String)                          # TikTok: persistent browser profile dir

    # Used by the stub clients (non-TikTok).
    access_token = Column(String)
    refresh_token = Column(String)
    expires_at = Column(DateTime)
    connected = Column(Boolean, default=True)

    created_at = Column(DateTime, default=datetime.utcnow)
