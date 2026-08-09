import logging
from services.platform_clients.base import PlatformClient, ValidationResult, UploadResult, PostStatus

logger = logging.getLogger("clipper")

class InstagramClient(PlatformClient):
    def validate_video(self, video_path: str) -> ValidationResult:
        return ValidationResult(True)

    def upload(self, video_path: str, metadata: dict) -> UploadResult:
        logger.info(f"Stub: Uploading to Instagram: {metadata}")
        return UploadResult(status="live", url="https://instagram.com/p/stub")

    def check_status(self, upload_id: str) -> PostStatus:
        return PostStatus(status="live")
