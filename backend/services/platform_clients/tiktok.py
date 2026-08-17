"""TikTok posting by driving the real TikTok Studio uploader in a logged-in
browser session. No API, no developer app, no audit, no quota.

Each connected account is a persistent Chromium profile (cookies on disk).
CoreClip's beat scheduler fires when a clip is due; at that moment the profile
is opened headless, the clip is uploaded through TikTok's web uploader, and
published immediately via the Post button.
"""
import logging
import os

from services.platform_clients.base import PlatformClient, ValidationResult, UploadResult, PostStatus
from services.ffmpeg_client import get_video_info
from services.tiktok_browser import post_video, NotLoggedInError

logger = logging.getLogger("clipper")

MAX_CAPTION = 2200
MAX_DURATION_SEC = 600


def _truncate(s: str, limit: int) -> str:
    return s if len(s) <= limit else s[:limit]


class TikTokClient(PlatformClient):
    def validate_video(self, video_path: str) -> ValidationResult:
        if not video_path or not os.path.exists(video_path):
            return ValidationResult(False, "Video file not found")
        if os.path.getsize(video_path) == 0:
            return ValidationResult(False, "Video file is empty")
        try:
            if get_video_info(video_path)["duration"] > MAX_DURATION_SEC:
                return ValidationResult(False, "Duration exceeds TikTok's 10-minute limit")
        except Exception as e:
            logger.warning(f"TikTok validate: could not probe {video_path}: {e}")
        return ValidationResult(True)

    def _build_caption(self, metadata: dict) -> str:
        """Caption = the generated caption text, then the canonical hashtags.

        Prefers the TikTok-specific variant over the generic description. Tags go
        last and are never truncated — they're the discovery mechanism, so when the
        whole thing would overflow it's the TEXT that gives way, not the tags."""
        variants = metadata.get("platform_variants") or {}
        text = ""
        if isinstance(variants, dict):
            text = str(variants.get("tiktok") or "").strip()
        if not text:
            text = str(metadata.get("description") or "").strip()

        # A stray '#' in the caption text would be typed as a hashtag by
        # _set_caption — popover wait and all. Tags are appended separately.
        text = text.replace("#", "").strip()

        tags = metadata.get("hashtags") or []
        tag_str = " ".join(f"#{str(t).lstrip('#')}" for t in tags if str(t).strip())

        if not text:
            return _truncate(tag_str, MAX_CAPTION)
        if not tag_str:
            return _truncate(text, MAX_CAPTION)

        budget = MAX_CAPTION - len(tag_str) - 1   # -1 for the separating space
        if budget <= 0:
            return _truncate(tag_str, MAX_CAPTION)
        return f"{_truncate(text, budget).strip()} {tag_str}"

    def upload(self, video_path: str, metadata: dict) -> UploadResult:
        session_path = metadata.get("account_session_path")
        if not session_path:
            return UploadResult(status="failed", error_message="No TikTok session — reconnect the account")

        caption = self._build_caption(metadata)

        try:
            result = post_video(session_path, video_path, caption)
        except NotLoggedInError as e:
            return UploadResult(status="failed", error_message=str(e))
        except Exception as e:
            logger.error(f"TikTok browser upload failed: {e}")
            return UploadResult(status="failed", error_message=str(e))

        if not result.get("success"):
            return UploadResult(status="failed", error_message=result.get("error") or "Unknown error")

        logger.info(f"TikTok posted via browser (session={session_path})")
        return UploadResult(status="live", url=result.get("url"))

    def check_status(self, upload_id: str) -> PostStatus:
        return PostStatus(status="live")