import os
import logging
from pathlib import Path


logger = logging.getLogger(__name__)


class Config:
    """Configuration class for the conversation app.
    
    API keys (OPENAI_API_KEY, ELEVENLABS_API_KEY) are provided via the web UI.
    Other settings are hardcoded with sensible defaults.
    """

    # API Keys - provided via web UI, fall back to env vars if set
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")

    # Model settings (hardcoded)
    MODEL_NAME = "gpt-4o-realtime-preview"

    # ElevenLabs music settings (hardcoded)
    ELEVEN_MUSIC_LENGTH_MS = 30000
    ELEVEN_MUSIC_TIMEOUT_S = 180

    # Vision model settings (hardcoded)
    HF_HOME = "./cache"
    LOCAL_VISION_MODEL = "HuggingFaceTB/SmolVLM2-2.2B-Instruct"
    HF_TOKEN = os.getenv("HF_TOKEN")  # Optional, falls back to hf auth login if not set

    # Profile settings - always use dj_reachy profile
    # This is the default DJ profile and cannot be changed via environment
    DEFAULT_PROFILE = "dj_reachy"
    REACHY_MINI_CUSTOM_PROFILE = DEFAULT_PROFILE

    # Songs storage directory (permanent)
    SONGS_DIR = Path(__file__).parent / "songs"

    logger.debug(f"Model: {MODEL_NAME}, HF_HOME: {HF_HOME}, Vision Model: {LOCAL_VISION_MODEL}")
    logger.debug(f"Custom Profile: {REACHY_MINI_CUSTOM_PROFILE}")


config = Config()


def set_custom_profile(profile: str | None) -> None:
    """Update the selected custom profile at runtime.

    Note: This function is a no-op - the profile is always fixed to dj_reachy.
    This ensures the DJ Reachy experience is consistent for all users.
    """
    # Profile is locked to dj_reachy - ignore any attempts to change it
    pass


def set_api_keys(openai_key: str | None = None, elevenlabs_key: str | None = None) -> None:
    """Update API keys at runtime (from web UI)."""
    if openai_key:
        config.OPENAI_API_KEY = openai_key
        os.environ["OPENAI_API_KEY"] = openai_key
    if elevenlabs_key:
        config.ELEVENLABS_API_KEY = elevenlabs_key
        os.environ["ELEVENLABS_API_KEY"] = elevenlabs_key
