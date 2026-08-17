"""Persistent user settings stored outside of .env so the Electron shell can
let users configure CoreClip from the UI. Values saved here override anything
read from environment variables at runtime."""

import json
import os
from typing import Optional

import logging

from config import config

logger = logging.getLogger("clipper")

# Store user overrides next to the database at the repo root so they survive
# app restarts and are easy to back up / delete.
_SETTINGS_PATH = os.path.join(config.BASE_DIR, "user_settings.json")


class UserSettings:
    """Simple JSON-backed key/value store for user-supplied configuration."""

    def __init__(self, path: Optional[str] = None):
        self._path = path or _SETTINGS_PATH
        self._data: dict = {}
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self._path):
            self._data = {}
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                self._data = json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load user settings from {self._path}: {e}")
            self._data = {}

    def _save(self) -> None:
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save user settings to {self._path}: {e}")
            raise

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def set(self, key: str, value) -> None:
        self._data[key] = value
        self._save()

    def delete(self, key: str) -> None:
        if key in self._data:
            del self._data[key]
            self._save()

    @property
    def default_gemini_model(self) -> str:
        from config import config
        return self._data.get("default_gemini_model") or config.GEMINI_DEFAULT_MODEL

    @default_gemini_model.setter
    def default_gemini_model(self, value: str) -> None:
        if value and value.strip():
            self.set("default_gemini_model", value.strip())
        else:
            self.delete("default_gemini_model")

    @property
    def gemini_api_key(self) -> Optional[str]:
        return self._data.get("gemini_api_key") or None

    @gemini_api_key.setter
    def gemini_api_key(self, value: Optional[str]) -> None:
        if value and value.strip():
            self.set("gemini_api_key", value.strip())
        else:
            self.delete("gemini_api_key")

    def effective_gemini_api_key(self) -> Optional[str]:
        """Return the API key that should actually be used."""
        return self.gemini_api_key

    def to_public_dict(self) -> dict:
        """Return a representation safe to send to the frontend (no full key)."""
        user_key = self.gemini_api_key

        return {
            "default_gemini_model": self.default_gemini_model,
            "gemini_api_key_set": bool(user_key),
            "gemini_api_key_last4": user_key[-4:] if user_key else None,
        }


user_settings = UserSettings()
