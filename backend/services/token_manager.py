import os
import json
import logging
from datetime import datetime, timedelta
from config import config

logger = logging.getLogger("clipper")

class TokenManager:
    def __init__(self):
        self.store_path = getattr(config, 'TOKEN_STORE_PATH', os.path.join(config.BASE_DIR, "tokens.json"))
        self._load()

    def _load(self):
        if os.path.exists(self.store_path):
            with open(self.store_path, 'r') as f:
                self.tokens = json.load(f)
        else:
            self.tokens = {}

    def _save(self):
        os.makedirs(os.path.dirname(self.store_path), exist_ok=True)
        with open(self.store_path, 'w') as f:
            json.dump(self.tokens, f, indent=2)

    def get_token(self, platform: str) -> dict | None:
        return self.tokens.get(platform)

    def save_token(self, platform: str, token_data: dict):
        self.tokens[platform] = token_data
        self._save()

    def is_expired(self, platform: str) -> bool:
        token = self.get_token(platform)
        if not token or 'expires_at' not in token:
            return True
        expires_at = datetime.fromisoformat(token['expires_at'])
        return datetime.utcnow() + timedelta(hours=1) >= expires_at

    def refresh_token(self, platform: str) -> dict:
        logger.info(f"Stub: refreshing token for {platform}")
        return self.get_token(platform)

    def validate_all(self) -> dict:
        platforms = ['tiktok', 'instagram', 'youtube', 'snapchat']
        result = {}
        for p in platforms:
            token = self.get_token(p)
            if token:
                result[p] = {
                    "configured": True,
                    "valid": not self.is_expired(p),
                    "expires_at": token.get('expires_at')
                }
            else:
                result[p] = {
                    "configured": False,
                    "valid": False,
                    "expires_at": None
                }
        return result

token_manager = TokenManager()
