import httpx
import logging
import os
from services.platform_clients.base import PlatformClient, ValidationResult, UploadResult, PostStatus
from services.ffmpeg_client import get_video_info

logger = logging.getLogger("clipper")

class YouTubeClient(PlatformClient):
    def validate_video(self, video_path: str) -> ValidationResult:
        size = os.path.getsize(video_path)
        if size > 128 * 1024 * 1024 * 1024:
            return ValidationResult(False, "File too large")
            
        info = get_video_info(video_path)
        if info['duration'] > 60:
            return ValidationResult(False, "Duration exceeds 60s for Shorts")
            
        ar = info['width'] / info['height']
        if abs(ar - (9/16)) > 0.01:
            return ValidationResult(False, "Aspect ratio is not 9:16")
            
        return ValidationResult(True)

    def upload(self, video_path: str, metadata: dict) -> UploadResult:
        logger.info(f"Stub: Uploading to YouTube: {metadata}")
        return UploadResult(status="live", url="https://youtube.com/shorts/stub")

    def check_status(self, upload_id: str) -> PostStatus:
        return PostStatus(status="live")
