from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

@dataclass
class ValidationResult:
    valid: bool
    error_message: Optional[str] = None

@dataclass
class UploadResult:
    status: str
    url: Optional[str] = None
    upload_id: Optional[str] = None
    error_message: Optional[str] = None

@dataclass
class PostStatus:
    status: str # "processing", "live", "failed"
    url: Optional[str] = None

class PlatformClient(ABC):
    @abstractmethod
    def validate_video(self, video_path: str) -> ValidationResult: ...
    @abstractmethod
    def upload(self, video_path: str, metadata: dict) -> UploadResult: ...
    @abstractmethod
    def check_status(self, upload_id: str) -> PostStatus: ...
