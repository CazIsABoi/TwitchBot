"""
Channel Point Rewards Handler

Handler *logic* lives here (registered by action id).
Twitch reward *titles* are mapped in rewards_config.json so you can rename
rewards in the Creator Dashboard without touching Python.
"""
import asyncio
import json
import random
import re
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from audio_handler import play_sound, play_sound_local_and_browser
from obs_handler import (
    toggle_source_visibility,
    get_source_filter_settings,
    set_source_filter,
    stop_stream,
    show_spelling_challenge_in_browser_source,
)
from image_handler import show_text, show_main_monitor_flash
from config import (
    OBS_WEBCAM_SOURCE,
    OBS_WEBCAM_COLOR_CORRECTION_FILTER,
    FLASHBANG_LOCAL_VOLUME,
    FLASHBANG_BROWSER_VOLUME,
    FLASHBANG_HOLD_SECONDS,
    FLASHBANG_FADE_SECONDS,
    FLASHBANG_WEBCAM_GAMMA_BOOST,
    SPELLING_REWARD_API_URL,
    SPELLING_REWARD_WORD_MIN_LENGTH,
    SPELLING_REWARD_WORD_MAX_LENGTH,
    REWARDS_CONFIG_FILE,
)

try:
    from tts_handler import parse_and_speak
    TTS_AVAILABLE = True
except ImportError:
    TTS_AVAILABLE = False
    print("⚠️ TTS not available. Install with: pip install edge-tts")

# action_id -> async handler(redemption, params: dict)
ACTION_HANDLERS = {}

# title.lower() -> {"action": str, "params": dict}
TITLE_ROUTES = {}


def action_handler(action_id: str):
    """Register a complex reward action by stable internal id."""
    def decorator(func):
        ACTION_HANDLERS[action_id] = func
        return func
    return decorator


def load_rewards_config(path: Path = REWARDS_CONFIG_FILE):
    """Load title -> action routing from JSON. Safe to call again to hot-reload."""
    global TITLE_ROUTES
    routes = {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"⚠️ rewards_config.json not found at {path}")
        TITLE_ROUTES = {}
        return
    except Exception as error:
        print(f"⚠️ Could not parse rewards_config.json: {error}")
        TITLE_ROUTES = {}
        return

    entries = data.get("rewards", data if isinstance(data, list) else [])
    if not isinstance(entries, list):
        print("⚠️ rewards_config.json: expected a 'rewards' list")
        TITLE_ROUTES = {}
        return

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        action = str(entry.get("action") or "").strip()
        if not action:
            continue
        params = entry.get("params") if isinstance(entry.get("params"), dict) else {}
        titles = entry.get("titles") or entry.get("title") or []
        if isinstance(titles, str):
            titles = [titles]
        for title in titles:
            key = str(title).strip().lower()
            if not key:
                continue
            routes[key] = {"action": action, "params": params}

    TITLE_ROUTES = routes
    print(f"✅ Loaded {len(TITLE_ROUTES)} reward title route(s) from {path.name}")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

async def _play_stream_ender_siren():
    await play_sound("sounds/nuclear-alarm-siren.mp3", volume=1.0)


BLOCKED_TERMS_FILE = Path(__file__).resolve().parent / "blocked_terms_dont_open_on_stream.json"


def load_blocked_terms():
    try:
        with BLOCKED_TERMS_FILE.open("r", encoding="utf-8") as handle:
            terms = json.load(handle)
        if isinstance(terms, list):
            return [str(term).strip() for term in terms if str(term).strip()]
    except FileNotFoundError:
        print(f"⚠️ Blocked terms file not found: {BLOCKED_TERMS_FILE}")
    except Exception as error:
        print(f"⚠️ Could not load blocked terms file {BLOCKED_TERMS_FILE}: {error}")
    return []


BLOCKED_TERMS = load_blocked_terms()
BLOCKED_PATTERNS = [
    re.compile(rf"(?<!\w){re.escape(term)}(?!\w)", re.IGNORECASE)
    for term in BLOCKED_TERMS
]


def contains_blocked_language(text):
    if not text:
        return False
    return any(pattern.search(text) for pattern in BLOCKED_PATTERNS)


def get_tts_message(redemption, fallback_message):
    message = redemption.event.user_input or fallback_message
    if contains_blocked_language(message):
        print(f"🚫 Blocked TTS from {redemption.event.user_name}: banned language detected")
        return None
    return message


def _normalize_spelling_word(word):
    return re.sub(r"[^a-z]", "", str(word or "").lower())


def _spell_out_word(word):
    letters = [character.upper() for character in str(word or "") if character.isalpha()]
    return " ".join(letters)


def _build_spelling_api_url(user_input):
    base_url = str(SPELLING_REWARD_API_URL or "").strip()
    if not base_url:
        raise RuntimeError("SPELLING_REWARD_API_URL is not configured")

    length = None
    difficulty = None
    raw_input = str(user_input or "").strip().lower()
    if raw_input.isdigit():
        length = max(3, min(int(raw_input), 12))
    elif raw_input in {"easy", "medium", "hard"}:
        difficulty = {"easy": 1, "medium": 3, "hard": 5}[raw_input]

    separator = "&" if "?" in base_url else "?"
    query = {
        "length": length if length is not None else random.randint(
            int(SPELLING_REWARD_WORD_MIN_LENGTH),
            int(SPELLING_REWARD_WORD_MAX_LENGTH),
        )
    }
    if difficulty is not None:
        query["diff"] = difficulty

    return f"{base_url}{separator}{urlencode(query)}"


def fetch_spelling_word(user_input=None):
    api_url = _build_spelling_api_url(user_input)
    try:
        with urlopen(api_url, timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except URLError as error:
        raise RuntimeError(f"Could not reach spelling word API: {error}") from error
    except Exception as error:
        raise RuntimeError(f"Could not parse spelling word API response: {error}") from error

    if not isinstance(payload, list) or not payload:
        raise RuntimeError("Spelling word API returned no words")

    word = str(payload[0]).strip()
    normalized = _normalize_spelling_word(word)
    if not normalized:
        raise RuntimeError(f"Spelling word API returned an unusable word: {word!r}")
    return word, normalized


def prompt_streamer_for_spelling_answer():
    prompt_message = (
        "Spell the word the streamer just heard.\n\n"
        "Type the answer, type REPEAT to hear it again, or leave blank/cancel to skip."
    )
    try:
        import tkinter as tk
        from tkinter import simpledialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        root.update_idletasks()
        answer = simpledialog.askstring(
            title="Spelling Bee",
            prompt=prompt_message,
            parent=root,
        )
        root.destroy()
        return answer
    except Exception as error:
        print(f"⚠️ Could not open spelling dialog, falling back to console input: {error}")
        try:
            return input("Spelling Bee: type the answer, REPEAT to hear the word again, or press Enter to skip: ")
        except EOFError:
            return None


# ---------------------------------------------------------------------------
# Built-in actions
# ---------------------------------------------------------------------------

@action_handler("play_sound")
async def action_play_sound(redemption, params):
    """Generic sound player driven entirely by rewards_config.json params."""
    files = params.get("files")
    if isinstance(files, list) and files:
        path = random.choice(files)
    else:
        path = params.get("file") or params.get("sound")
    if not path:
        print(f"⚠️ play_sound action missing file/files for reward '{redemption.event.reward.title}'")
        return
    volume = float(params.get("volume", 1.0))
    await play_sound(str(path), volume=volume)
    print(f"🔊 {redemption.event.user_name} triggered sound: {path}")


@action_handler("stream_ender")
async def stream_ender(redemption, params):
    print(f"{redemption.event.user_name} has decided to end the stream! Ending in 15 seconds...")
    await asyncio.sleep(5)
    await _play_stream_ender_siren()
    for s in range(15, 0, -1):
        await show_text(
            f"Stream ending in {s} seconds!",
            duration=1.0,
            position="center",
            fullscreen=True,
            force_top=True,
            color="red",
            size=72,
        )
        await asyncio.sleep(1)
    await stop_stream()


@action_handler("flashbang")
async def flashbang(redemption, params):
    total_flash = FLASHBANG_HOLD_SECONDS + FLASHBANG_FADE_SECONDS
    original_filter_settings = await get_source_filter_settings(
        OBS_WEBCAM_SOURCE,
        OBS_WEBCAM_COLOR_CORRECTION_FILTER,
    )

    gamma_boost_applied = False
    original_gamma_value = None
    if isinstance(original_filter_settings, dict):
        gamma_value = original_filter_settings.get("gamma")
        if isinstance(gamma_value, (int, float)):
            original_gamma_value = float(gamma_value)
            boosted_gamma_value = original_gamma_value + float(FLASHBANG_WEBCAM_GAMMA_BOOST)
            await set_source_filter(
                OBS_WEBCAM_SOURCE,
                OBS_WEBCAM_COLOR_CORRECTION_FILTER,
                {"gamma": boosted_gamma_value},
            )
            gamma_boost_applied = True
        else:
            print(
                "⚠️ Webcam gamma boost skipped: existing gamma value is missing or non-numeric "
                f"({gamma_value!r})"
            )

    try:
        await asyncio.gather(
            show_main_monitor_flash(
                duration=total_flash,
                color="white",
                hold_duration=FLASHBANG_HOLD_SECONDS,
                fade_duration=FLASHBANG_FADE_SECONDS,
                start_delay=0.0,
            ),
            play_sound_local_and_browser(
                "sounds/flashbang.mp3",
                local_volume=FLASHBANG_LOCAL_VOLUME,
                browser_volume=FLASHBANG_BROWSER_VOLUME,
                browser_mode="sfx",
                wait_for_local=False,
            ),
        )
    finally:
        if gamma_boost_applied and original_gamma_value is not None:
            await set_source_filter(
                OBS_WEBCAM_SOURCE,
                OBS_WEBCAM_COLOR_CORRECTION_FILTER,
                {"gamma": original_gamma_value},
            )

    print(
        f"{redemption.event.user_name} flashbanged main monitor only! "
        f"local_vol={FLASHBANG_LOCAL_VOLUME} browser_vol={FLASHBANG_BROWSER_VOLUME} "
        f"gamma_boost={FLASHBANG_WEBCAM_GAMMA_BOOST}"
    )


@action_handler("hide_cam")
async def hide_cam(redemption, params):
    seconds = int(params.get("seconds", 30))
    await toggle_source_visibility(OBS_WEBCAM_SOURCE, visible=False)
    await asyncio.sleep(seconds)
    await toggle_source_visibility(OBS_WEBCAM_SOURCE, visible=True)
    print(f"{redemption.event.user_name} hid the cam for {seconds}s!")


@action_handler("tts")
async def tts_action(redemption, params):
    if not TTS_AVAILABLE:
        print("❌ TTS not installed!")
        return
    voice = str(params.get("voice") or "male")
    message = get_tts_message(
        redemption,
        f"{redemption.event.user_name} redeemed TTS but said nothing!",
    )
    if not message:
        return
    await parse_and_speak(message, base_voice=voice, speaker=redemption.event.user_name)
    print(f"🗣️ {redemption.event.user_name}: {message}")


@action_handler("spelling_bee")
async def spelling_bee(redemption, params):
    if not TTS_AVAILABLE:
        print("❌ TTS not installed!")
        return

    try:
        word, normalized_word = await asyncio.to_thread(fetch_spelling_word, redemption.event.user_input)
    except Exception as error:
        print(f"❌ Could not start spelling challenge: {error}")
        await parse_and_speak(
            "Spelling challenge failed to load a word.",
            base_voice="female",
            speaker="Spelling Bee",
        )
        return

    intro = "Spelling Bee punishment. Streamer, spell this word."
    show_spelling_challenge_in_browser_source(
        word=word,
        status="Streamer must spell this word now",
        state="active",
        reveal_word=True,
    )
    await parse_and_speak(intro, base_voice="female", speaker="Spelling Bee")
    await parse_and_speak(word, base_voice="female", speaker="Spelling Bee")

    answer = None
    while True:
        answer = await asyncio.to_thread(prompt_streamer_for_spelling_answer)
        if answer is None:
            break
        if str(answer).strip().lower() == "repeat":
            show_spelling_challenge_in_browser_source(
                word=word,
                status="Word replayed",
                state="active",
                reveal_word=True,
            )
            await parse_and_speak("Listen again.", base_voice="female", speaker="Spelling Bee")
            await parse_and_speak(word, base_voice="female", speaker="Spelling Bee")
            continue
        break

    attempted_word = _normalize_spelling_word(answer)

    if not attempted_word:
        print(f"⚪ Spelling challenge skipped for {redemption.event.user_name}: word={word}")
        show_spelling_challenge_in_browser_source(
            word=word,
            status="No answer entered",
            state="done",
            reveal_word=True,
        )
        await parse_and_speak(
            f"No spelling was entered. The word was {word}.",
            base_voice="female",
            speaker="Spelling Bee",
        )
        return

    if attempted_word == normalized_word:
        confirmation = f"Correct. The word was {word}."
        show_spelling_challenge_in_browser_source(
            word=word,
            status="Correct",
            state="done",
            reveal_word=True,
        )
        print(f"✅ {redemption.event.user_name} spelled '{word}' correctly")
    else:
        spelled_out = _spell_out_word(word)
        confirmation = (
            f"Incorrect. You typed {attempted_word}. The word was {word}. "
            f"It is spelled {spelled_out}."
        )
        show_spelling_challenge_in_browser_source(
            word=word,
            status="Incorrect",
            state="done",
            reveal_word=True,
            spelled_out=spelled_out,
        )
        print(
            f"❌ {redemption.event.user_name} missed spelling challenge. "
            f"attempt={attempted_word} expected={word}"
        )

    await parse_and_speak(confirmation, base_voice="female", speaker="Spelling Bee")


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------

async def handle_reward(redemption):
    """Route a channel-point redemption using rewards_config.json titles."""
    reward_title = (redemption.event.reward.title or "").strip()
    route = TITLE_ROUTES.get(reward_title.lower())

    if route is None:
        print(f"⚠️ Unknown reward: {reward_title}")
        print(f"   User: {redemption.event.user_name}")
        if redemption.event.user_input:
            print(f"   Message: {redemption.event.user_input}")
        print("   Tip: add the title to rewards_config.json")
        return

    action_id = route["action"]
    params = route.get("params") or {}
    handler = ACTION_HANDLERS.get(action_id)
    if handler is None:
        print(f"❌ No Python handler registered for action '{action_id}' (title='{reward_title}')")
        return

    try:
        await handler(redemption, params)
    except Exception as error:
        print(f"❌ Error handling reward '{reward_title}' (action={action_id}): {error}")


# Load routes at import time
load_rewards_config()
