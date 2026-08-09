import logging
import os
import shutil
from services.platform_clients.base import PlatformClient, ValidationResult, UploadResult, PostStatus
from config import config

logger = logging.getLogger("clipper")

class SnapchatClient(PlatformClient):
    def validate_video(self, video_path: str) -> ValidationResult:
        return ValidationResult(True)

    def upload(self, video_path: str, metadata: dict) -> UploadResult:
        logger.info("Snapchat upload requires manual intervention")
        dest_dir = os.path.join(config.OUTPUT_DIR, "ready_for_snapchat")
        os.makedirs(dest_dir, exist_ok=True)
        shutil.copy(video_path, dest_dir)
        return UploadResult(status="manual_required")

    def check_status(self, upload_id: str) -> PostStatus:
        return PostStatus(status="manual_required")
