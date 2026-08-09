from services.platform_clients.youtube import YouTubeClient
from services.platform_clients.tiktok import TikTokClient
from services.platform_clients.instagram import InstagramClient
from services.platform_clients.snapchat import SnapchatClient

def get_platform_client(platform: str):
    if platform == "youtube":
        return YouTubeClient()
    elif platform == "tiktok":
        return TikTokClient()
    elif platform == "instagram":
        return InstagramClient()
    elif platform == "snapchat":
        return SnapchatClient()
    raise ValueError(f"Unknown platform {platform}")
