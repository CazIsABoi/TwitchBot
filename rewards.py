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
    QUEUED_REWARD_ACTIONS,
)
from reward_queue import reward_queue

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
            # Optional per-reward override: "queue": true/false in rewards_config.json
            if "queue" in entry:
                queue_flag = bool(entry.get("queue"))
            else:
                queue_flag = None
            routes[key] = {"action": action, "params": params, "queue": queue_flag}

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


def _http_get_json(url, timeout=8):
    from urllib.request import Request
    request = Request(
        url,
        headers={
            "User-Agent": "TwitchBot-SpellingBee/1.0",
            "Accept": "application/json",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _definition_candidates(word):
    """Return lookup variants (exact, stripped plural, etc.)."""
    cleaned = _normalize_spelling_word(word)
    if not cleaned:
        return []
    variants = [cleaned]
    # Common plural / suffix fallbacks so rare API words still resolve.
    for suffix, repl in (("inesses", "y"), ("iness", "y"), ("ies", "y"), ("ses", "s"),
                         ("es", ""), ("s", ""), ("ed", ""), ("ing", "")):
        if cleaned.endswith(suffix) and len(cleaned) - len(suffix) >= 3:
            variants.append(cleaned[: -len(suffix)] + repl)
    # de-dupe, keep order
    seen = set()
    ordered = []
    for item in variants:
        if item and item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def fetch_word_definition(word):
    """Look up a short English definition (Datamuse first, dictionaryapi fallback)."""
    for candidate in _definition_candidates(word):
        # 1) Datamuse — better coverage for common + uncommon words
        try:
            payload = _http_get_json(
                f"https://api.datamuse.com/words?sp={candidate}&md=d&max=1",
                timeout=6,
            )
            if isinstance(payload, list) and payload:
                defs = payload[0].get("defs") or []
                if defs:
                    raw = str(defs[0]).strip()
                    # Datamuse format: "n\tdefinition text"
                    if "\t" in raw:
                        part, meaning = raw.split("\t", 1)
                    elif "	" in raw:
                        part, meaning = raw.split("	", 1)
                    else:
                        part, meaning = "", raw
                    meaning = meaning.strip()
                    part = part.strip()
                    if meaning:
                        # Keep it short for TTS + dialog
                        if len(meaning) > 140:
                            meaning = meaning[:137].rsplit(" ", 1)[0] + "…"
                        part_map = {
                            "n": "noun", "v": "verb", "adj": "adjective",
                            "adv": "adverb", "u": "",
                        }
                        label = part_map.get(part, part)
                        return f"({label}) {meaning}" if label else meaning
        except Exception as error:
            print(f"⚠️ Datamuse lookup failed for {candidate!r}: {error}")

        # 2) Free Dictionary API fallback
        try:
            payload = _http_get_json(
                f"https://api.dictionaryapi.dev/api/v2/entries/en/{candidate}",
                timeout=6,
            )
            if isinstance(payload, list) and payload:
                for meaning in payload[0].get("meanings") or []:
                    part = str(meaning.get("partOfSpeech") or "").strip()
                    for definition_entry in meaning.get("definitions") or []:
                        definition = str(definition_entry.get("definition") or "").strip()
                        if definition:
                            if len(definition) > 140:
                                definition = definition[:137].rsplit(" ", 1)[0] + "…"
                            return f"({part}) {definition}" if part else definition
        except Exception as error:
            print(f"⚠️ DictionaryAPI lookup failed for {candidate!r}: {error}")

    return ""


def fetch_spelling_word(user_input=None):
    """
    Pick a spelling word that preferably has a dictionary definition.
    Retries a few times because the random-word API often returns obscure terms.
    """
    last_error = None
    fallback_word = None

    for attempt in range(8):
        api_url = _build_spelling_api_url(user_input)
        try:
            payload = _http_get_json(api_url, timeout=8)
        except URLError as error:
            last_error = error
            continue
        except Exception as error:
            last_error = error
            continue

        if not isinstance(payload, list) or not payload:
            continue

        word = str(payload[0]).strip()
        normalized = _normalize_spelling_word(word)
        if not normalized:
            continue

        if fallback_word is None:
            fallback_word = (word, normalized)

        definition = fetch_word_definition(word)
        if definition:
            return word, normalized, definition

        print(f"⚠️ No definition for {word!r}, trying another word ({attempt + 1}/8)")

    if fallback_word is not None:
        word, normalized = fallback_word
        return word, normalized, ""

    if last_error is not None:
        raise RuntimeError(f"Could not reach spelling word API: {last_error}") from last_error
    raise RuntimeError("Spelling word API returned no usable words")


def prompt_streamer_for_spelling_answer(definition=""):
    """Custom Spelling Bee dialog with definition, styled inputs, and action buttons."""
    try:
        import tkinter as tk
        from tkinter import font as tkfont

        result = {"value": None}

        root = tk.Tk()
        root.title("Spelling Bee")
        root.attributes("-topmost", True)
        root.resizable(False, False)
        root.configure(bg="#12141c")

        # Center on screen
        width, height = 520, 360
        root.update_idletasks()
        screen_w = root.winfo_screenwidth()
        screen_h = root.winfo_screenheight()
        x = max((screen_w - width) // 2, 0)
        y = max((screen_h - height) // 3, 0)
        root.geometry(f"{width}x{height}+{x}+{y}")

        title_font = tkfont.Font(family="Segoe UI", size=18, weight="bold")
        body_font = tkfont.Font(family="Segoe UI", size=11)
        def_font = tkfont.Font(family="Segoe UI", size=11, slant="italic")
        button_font = tkfont.Font(family="Segoe UI", size=10, weight="bold")

        outer = tk.Frame(root, bg="#12141c", padx=22, pady=18)
        outer.pack(fill="both", expand=True)

        tk.Label(
            outer,
            text="🐝  Spelling Bee",
            font=title_font,
            fg="#f2f5ff",
            bg="#12141c",
        ).pack(anchor="w")

        tk.Label(
            outer,
            text="Listen carefully, then type the word below.",
            font=body_font,
            fg="#a7b2cc",
            bg="#12141c",
        ).pack(anchor="w", pady=(4, 12))

        def_box = tk.Frame(outer, bg="#1a1f2b", highlightbackground="#2b3345", highlightthickness=1)
        def_box.pack(fill="x", pady=(0, 14))

        tk.Label(
            def_box,
            text="DEFINITION",
            font=tkfont.Font(family="Segoe UI", size=9, weight="bold"),
            fg="#4cd2ff",
            bg="#1a1f2b",
        ).pack(anchor="w", padx=14, pady=(10, 2))

        definition_text = str(definition or "").strip() or "No definition found — rely on the spoken word."
        def_label = tk.Label(
            def_box,
            text=definition_text,
            font=def_font,
            fg="#e8ecf8",
            bg="#1a1f2b",
            wraplength=460,
            justify="left",
        )
        def_label.pack(anchor="w", padx=14, pady=(0, 12))

        tk.Label(
            outer,
            text="Your spelling",
            font=tkfont.Font(family="Segoe UI", size=9, weight="bold"),
            fg="#a7b2cc",
            bg="#12141c",
        ).pack(anchor="w")

        entry_var = tk.StringVar()
        entry = tk.Entry(
            outer,
            textvariable=entry_var,
            font=tkfont.Font(family="Segoe UI", size=16),
            bg="#0f131d",
            fg="#ffffff",
            insertbackground="#ffffff",
            relief="flat",
            highlightthickness=2,
            highlightbackground="#36445f",
            highlightcolor="#4cd2ff",
        )
        entry.pack(fill="x", ipady=10, pady=(6, 16))
        entry.focus_set()

        hint = tk.Label(
            outer,
            text="Enter = submit   ·   Esc = skip   ·   or use the buttons",
            font=tkfont.Font(family="Segoe UI", size=9),
            fg="#6b7690",
            bg="#12141c",
        )
        hint.pack(anchor="w", pady=(0, 10))

        buttons = tk.Frame(outer, bg="#12141c")
        buttons.pack(fill="x")

        def style_button(btn, bg, fg="#02131a"):
            btn.configure(
                bg=bg,
                fg=fg,
                activebackground=bg,
                activeforeground=fg,
                relief="flat",
                bd=0,
                padx=14,
                pady=8,
                cursor="hand2",
                font=button_font,
            )

        def finish(value):
            result["value"] = value
            root.destroy()

        def on_submit(_event=None):
            finish(entry_var.get())

        def on_repeat():
            finish("REPEAT")

        def on_skip():
            finish("")

        submit_btn = tk.Button(buttons, text="Submit", command=on_submit)
        style_button(submit_btn, "#4cd2ff")
        submit_btn.pack(side="left")

        repeat_btn = tk.Button(buttons, text="Repeat word", command=on_repeat)
        style_button(repeat_btn, "#9eb0d5")
        repeat_btn.pack(side="left", padx=(10, 0))

        skip_btn = tk.Button(buttons, text="Skip", command=on_skip)
        style_button(skip_btn, "#3a4254", fg="#f2f5ff")
        skip_btn.pack(side="right")

        root.bind("<Return>", on_submit)
        root.bind("<Escape>", lambda _e: on_skip())
        root.protocol("WM_DELETE_WINDOW", on_skip)

        # Keep above other windows while the streamer answers.
        root.lift()
        root.focus_force()
        root.mainloop()
        return result["value"]
    except Exception as error:
        print(f"⚠️ Could not open spelling dialog, falling back to console input: {error}")
        try:
            return input(
                "Spelling Bee: type the answer, REPEAT to hear the word again, or press Enter to skip: "
            )
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
        word, normalized_word, definition = await asyncio.to_thread(
            fetch_spelling_word, redemption.event.user_input
        )
    except Exception as error:
        print(f"❌ Could not start spelling challenge: {error}")
        await parse_and_speak(
            "Spelling challenge failed to load a word.",
            base_voice="female",
            speaker="Spelling Bee",
            show_caption=False,
        )
        return

    if definition:
        print(f"📖 Definition for {word}: {definition}")
    else:
        print(f"📖 No definition found for {word}")

    # Do not show the answer word on stream while active — only status.
    show_spelling_challenge_in_browser_source(
        word=word,
        status="Spell the word",
        state="active",
        reveal_word=False,
    )
    # Definition is shown in the local dialog only — do not read it aloud.
    await parse_and_speak("Spell this word.", base_voice="female", speaker="Spelling Bee", show_caption=False)
    await parse_and_speak(word, base_voice="female", speaker="Spelling Bee", show_caption=False)

    answer = None
    while True:
        answer = await asyncio.to_thread(prompt_streamer_for_spelling_answer, definition)
        if answer is None:
            break
        if str(answer).strip().lower() == "repeat":
            show_spelling_challenge_in_browser_source(
                word=word,
                status="Replaying word",
                state="active",
                reveal_word=False,
            )
            # Just replay the word — no extra "listen again" line.
            await parse_and_speak(word, base_voice="female", speaker="Spelling Bee", show_caption=False)
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
            show_caption=False,
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

    await parse_and_speak(confirmation, base_voice="female", speaker="Spelling Bee", show_caption=False)


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------

def _should_queue_action(action_id: str, route: dict) -> bool:
    """Decide if this redemption goes through the serial queue."""
    override = route.get("queue")
    if override is not None:
        return bool(override)
    queued_actions = {str(a).strip().lower() for a in (QUEUED_REWARD_ACTIONS or set())}
    return action_id.strip().lower() in queued_actions


async def handle_reward(redemption):
    """Route a channel-point redemption using rewards_config.json titles.

    Heavy actions (TTS / Flashbang / Spelling Bee / ...) are serialized via
    reward_queue. Short SFX (play_sound) run immediately and may overlap.
    """
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

    user_name = redemption.event.user_name or "unknown"

    if _should_queue_action(action_id, route):
        async def _run():
            await handler(redemption, params)

        await reward_queue.enqueue(
            action_id=action_id,
            title=reward_title,
            user_name=user_name,
            factory=_run,
        )
        return

    # Stackable path (SFX etc.) — fire and forget so overlaps are allowed.
    try:
        await handler(redemption, params)
    except Exception as error:
        print(f"❌ Error handling reward '{reward_title}' (action={action_id}): {error}")


# Load routes at import time
load_rewards_config()
