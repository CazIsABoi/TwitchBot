"""
Twitch Bot Configuration

Secrets (APP_ID, APP_SECRET, OBS_PASSWORD, etc.) load from a local .env file.
Copy env.example -> .env and fill in real values. Never commit .env.
Non-secret tuning knobs stay here so they are easy to edit.
"""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv


def _resolve_bot_root() -> Path:
    """Folder that holds .env, sounds/, tokens — next to the .exe when frozen."""
    if getattr(sys, "frozen", False):
        # PyInstaller onedir: TwitchBot.exe and .env live in the same folder.
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _resolve_bundle_root() -> Path:
    """Read-only bundled assets (defaults) when running as a frozen app."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return _resolve_bot_root()


def _candidate_env_paths(root: Path):
    """Places we look for secrets (Windows sometimes uses env without a dot)."""
    return [
        root / ".env",
        root / "env",
        Path.cwd() / ".env",
        Path.cwd() / "env",
    ]


_BOT_ROOT = _resolve_bot_root()
_BUNDLE_ROOT = _resolve_bundle_root()
BOT_ROOT = _BOT_ROOT  # public alias
BUNDLE_ROOT = _BUNDLE_ROOT

# Load the first env file we can find; override=True so empty shell vars don't win.
_DOTENV_PATH = None
for _candidate in _candidate_env_paths(_BOT_ROOT):
    if _candidate.is_file():
        load_dotenv(dotenv_path=str(_candidate), override=True)
        _DOTENV_PATH = _candidate
        break

if _DOTENV_PATH is not None:
    print(f"🔐 Loaded settings from: {_DOTENV_PATH}")
else:
    print(
        f"⚠️ No .env found. Looked next to the app at:\n"
        f"   {_BOT_ROOT / '.env'}\n"
        f"   {_BOT_ROOT / 'env'}\n"
        f"   cwd={Path.cwd()}"
    )


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

# When true, bot will create missing OBS browser sources (Bot / BotText / RewardOverlay)
def _env_bool(key: str, default: bool = False) -> bool:
    raw = os.getenv(key)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


AUTO_CREATE_OBS_SOURCES = _env_bool("AUTO_CREATE_OBS_SOURCES", True)

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
    print("⚠️ APP_ID / APP_SECRET missing after loading env.")
    print(f"   App folder: {_BOT_ROOT}")
    print(f"   Put a .env file in that folder with APP_ID=... and APP_SECRET=...")
    if _DOTENV_PATH is not None:
        print(f"   Loaded file was: {_DOTENV_PATH} (check the keys are not blank)")
if not TARGET_CHANNEL:
    print("⚠️ TARGET_CHANNEL missing in .env (or env)")

# ---------------------------------------------------------------------------
# Reward queue + skip hotkey
# ---------------------------------------------------------------------------
# Actions that run one-at-a-time through the serial queue.
# Everything else (especially play_sound SFX) stacks / overlaps freely.
QUEUED_REWARD_ACTIONS = {
    "tts",
    "flashbang",
    "spelling_bee",
    "stream_ender",
    "hide_cam",
}

# Keyboard shortcut to skip the *current* queued reward.
# Examples: "f8", "f9", "ctrl+shift+s", "num_subtract"
# Set to "" to disable the hotkey (chat !skip still works for the broadcaster).
SKIP_REWARD_HOTKEY = "f8"

# ---------------------------------------------------------------------------
# First chatter celebration
# ---------------------------------------------------------------------------
# Set False to disable the fireworks / first-message-of-stream feature entirely.
FIRST_CHATTER_ENABLED = True

# ---------------------------------------------------------------------------
# TTS caption box (BotText) — easy knobs; layout editor can still override live
# ---------------------------------------------------------------------------
# Width/height of the caption box in pixels (inside the 1920x1080 browser source).
TTS_TEXT_BOX_WIDTH_PX = 900
TTS_TEXT_BOX_HEIGHT_PX = 220
TTS_TEXT_FONT_SIZE = 44
# Cap width as a percent of the preview/safe area (30–100).
TTS_TEXT_MAX_WIDTH_PERCENT = 70