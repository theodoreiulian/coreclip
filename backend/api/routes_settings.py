"""Settings API: lets the Electron/UI shell supply user-level configuration
that persists across restarts."""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from services.settings_manager import user_settings

router = APIRouter()


class SettingsResponse(BaseModel):
    default_gemini_model: str = "gemini-3.1-flash-lite"
    gemini_api_key_set: bool = False
    gemini_api_key_last4: str | None = None


class SettingsUpdate(BaseModel):
    gemini_api_key: str | None = Field(
        default=None,
        description="Gemini API key to persist. Pass null or an empty string to remove it.",
    )
    default_gemini_model: str | None = Field(
        default=None,
        description="Default Gemini model used for all AI handoffs.",
    )


@router.get("/settings", response_model=SettingsResponse)
def get_settings():
    """Return the current settings state (no secrets are returned)."""
    return user_settings.to_public_dict()


@router.put("/settings", response_model=SettingsResponse)
def update_settings(payload: SettingsUpdate):
    """Persist user settings. The Gemini client will pick up changes
    automatically on the next API call."""
    if payload.gemini_api_key is not None:
        user_settings.gemini_api_key = payload.gemini_api_key
    if payload.default_gemini_model is not None:
        user_settings.default_gemini_model = payload.default_gemini_model
    return user_settings.to_public_dict()


@router.delete("/settings/gemini-api-key", response_model=SettingsResponse)
def remove_gemini_api_key():
    """Remove the saved Gemini API key."""
    user_settings.gemini_api_key = None
    return user_settings.to_public_dict()
