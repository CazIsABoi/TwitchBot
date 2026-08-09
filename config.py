"""
Twitch Bot Configuration

Secrets (APP_ID, APP_SECRET, OBS_PASSWORD, etc.) load from a local .env file.
Copy env.example -> .env and fill in real values. Never commit .env.
Non-secret tuning knobs stay here so they are easy to edit.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

_BOT_ROOT = Path(__file__).resolve().parent

# Prefer standard .env, but fall back to legacy env filename if present.
_DOTENV_PATH = _BOT_ROOT / ".env"
if _DOTENV_PATH.exists():
    load_dotenv(_DOTENV_PATH)
else:
    _LEGACY_DOTENV_PATH = _BOT_ROOT / "env"
    if _LEGACY_DOTENV_PATH.exists():
        load_dotenv(_LEGACY_DOTENV_PATH)


def _env(key: str, default: str = "") -> str:
    return str(os.getenv(key, default)).strip()


def _env_int(key: str, default: int) -> int:
    raw = os.getenv(key)
    if raw is None or str(raw).strip() == "":
        return default
    return int(raw)


def _env_float(key: str, default: float) -> float:
    raw = os.getenv(key)
    if raw is None or str(raw).strip() == "":
        return default
    return float(raw)


# ---------------------------------------------------------------------------
# Secrets / environment (from .env)
# ---------------------------------------------------------------------------
APP_ID = _env("APP_ID")
APP_SECRET = _env("APP_SECRET")
TARGET_CHANNEL = _env("TARGET_CHANNEL")

OBS_HOST = _env("OBS_HOST", "127.0.0.1")
OBS_PORT = _env_int("OBS_PORT", 4455)
OBS_PASSWORD = _env("OBS_PASSWORD")

HOSTED_AUDIO_API_KEY = _env("HOSTED_AUDIO_API_KEY")

# ---------------------------------------------------------------------------
# OBS source / filter names (must match OBS exactly)
# ---------------------------------------------------------------------------
OBS_WEBCAM_SOURCE = "Webcam"
OBS_DISPLAY_SOURCE = "Display Capture"
OBS_ROTATE_FILTER = "Rotate"
OBS_WEBCAM_COLOR_CORRECTION_FILTER = "Colour Correction"
OBS_TTS_SOURCE = "Bot"
OBS_TTS_TEXT_SOURCE = "BotText"
OBS_OVERLAY_SOURCE = "RewardOverlay"

# ---------------------------------------------------------------------------
# TTS caption layout defaults (layout editor can override at runtime)
# ---------------------------------------------------------------------------
TTS_TEXT_ANCHOR = "bottom"
TTS_TEXT_OFFSET_X = 0
TTS_TEXT_OFFSET_Y = 64

# ---------------------------------------------------------------------------
# Flashbang tuning
# ---------------------------------------------------------------------------
FLASHBANG_LOCAL_VOLUME = 1.0
FLASHBANG_BROWSER_VOLUME = 1.0
FLASHBANG_HOLD_SECONDS = 1.0
FLASHBANG_FADE_SECONDS = 5.0
FLASHBANG_WEBCAM_GAMMA_BOOST = 0.12
FLASHBANG_OBS_DELAY_SECONDS = 0.35

# ---------------------------------------------------------------------------
# Audio routing
#   "browser" | "local" | "both"
# ---------------------------------------------------------------------------
AUDIO_ROUTING_MODE = "local"

# ---------------------------------------------------------------------------
# Spelling Bee
# ---------------------------------------------------------------------------
SPELLING_REWARD_API_URL = "https://random-word-api.herokuapp.com/word?number=1&lang=en"
SPELLING_REWARD_WORD_MIN_LENGTH = 4
SPELLING_REWARD_WORD_MAX_LENGTH = 8

# ---------------------------------------------------------------------------
# Browser-audio backend
#   "local" | "hosted"
# ---------------------------------------------------------------------------
AUDIO_BRIDGE_BACKEND = "local"
HOSTED_TTS_PLAYER_URL = ""
HOSTED_AUDIO_API_URL = ""
HOSTED_STORAGE_BACKEND = "direct_url"
HOSTED_STORAGE_PUBLIC_BASE_URL = ""
HOSTED_S3_BUCKET = ""
HOSTED_S3_REGION = ""
HOSTED_S3_PREFIX = "twitchbot-audio"
HOSTED_S3_PRESIGNED_EXPIRES_SECONDS = 3600

# ---------------------------------------------------------------------------
# Config file paths
# ---------------------------------------------------------------------------
REWARDS_CONFIG_FILE = _BOT_ROOT / "rewards_config.json"
IGNORED_CHATTERS_FILE = _BOT_ROOT / "ignored_chatters.json"

# Fail fast if required secrets are missing (helps onboarding)
if not APP_ID or not APP_SECRET:
    print("⚠️ APP_ID / APP_SECRET missing. Copy env.example to .env and fill them in.")
if not TARGET_CHANNEL:
    print("⚠️ TARGET_CHANNEL missing in .env (or env)")
