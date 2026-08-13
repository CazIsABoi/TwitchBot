# CazIsABoi Bot — Quick Start Guide

Channel points bot with OBS overlays, TTS, sound effects, spelling bee, flashbang, and first-chatter celebrations.

---

## 0. Easiest start (recommended)

1. Install [Python 3.11+](https://www.python.org/downloads/) (tick **Add to PATH**).
2. Open OBS → **Tools → WebSocket Server Settings** → enable, set a password.
3. Double-click **`Start Bot.bat`**
4. Fill in the first-run window (Twitch Client ID/Secret, channel, OBS password).
5. Leave **Auto-create OBS browser sources** checked if you want the bot to add `Bot`, `BotText`, and `RewardOverlay` for you.
6. Log in with Twitch when the browser opens.

Re-run setup any time:

```powershell
python setup_wizard.py
```

Toggle auto-create later in `.env`:

```env
AUTO_CREATE_OBS_SOURCES=true
```

---

## 1. Prerequisites

| Requirement | Notes |
|-------------|--------|
| **Windows** | Primary target (tkinter dialogs, pynput hotkeys) |
| **Python 3.11+** | 3.12 recommended |
| **OBS Studio** | With **WebSocket** server enabled (Tools → WebSocket Server Settings) |
| **Twitch app** | Create one at [dev.twitch.tv/console](https://dev.twitch.tv/console) |

---

## 2. Install

```powershell
cd path\to\this\folder
pip install -r requirements.txt
```

Dependencies include: `twitchAPI`, `obs-websocket-py`, `edge-tts`, `just_playback`, `python-dotenv`, `pynput`.

---

## 3. Secrets (`.env`)

1. Copy the example file:

```powershell
copy .env.example .env
```

2. Edit `.env`:

| Variable | What to put |
|----------|-------------|
| `APP_ID` | Client ID from Twitch Dev Console |
| `APP_SECRET` | Client Secret from Twitch Dev Console |
| `TARGET_CHANNEL` | Your channel login (**lowercase**) |
| `OBS_HOST` | Usually `127.0.0.1` |
| `OBS_PORT` | Usually `4455` |
| `OBS_PASSWORD` | Password from OBS WebSocket settings |

**Never commit `.env` or `twitch_tokens.json`.**

After rotating secrets in the Twitch console or OBS, update `.env` and delete `twitch_tokens.json` so the bot re-authenticates.

---

## 4. Non-secret settings (`config.py`)

Edit these to match your OBS scene and preferred behavior:

| Setting | Purpose | Typical value |
|---------|---------|----------------|
| `OBS_WEBCAM_SOURCE` | Webcam source name in OBS | `Webcam` |
| `OBS_DISPLAY_SOURCE` | Display capture name | `Display Capture` |
| `OBS_TTS_SOURCE` | Browser source for TTS audio | `Bot` |
| `OBS_TTS_TEXT_SOURCE` | Browser source for captions | `BotText` |
| `OBS_OVERLAY_SOURCE` | Browser source for overlays | `RewardOverlay` |
| `OBS_WEBCAM_COLOR_CORRECTION_FILTER` | Color filter used by flashbang | `Colour Correction` |
| `AUDIO_ROUTING_MODE` | `local` / `browser` / `both` | `local` or `both` |
| `FLASHBANG_*` | Hold/fade timings and volumes | see file |
| `QUEUED_REWARD_ACTIONS` | Actions that run one-at-a-time | tts, flashbang, … |
| `SKIP_REWARD_HOTKEY` | Keyboard skip for current queued reward | `f8` |

**Source and filter names must match OBS exactly** (including spelling like `Colour` vs `Color`).

---

## 5. Map rewards (`rewards_config.json`)

Each entry maps one or more **Twitch reward titles** (exact text, case-insensitive) to a bot action.

### Built-in actions

| Action | Behavior | Queued by default? |
|--------|----------|--------------------|
| `play_sound` | Play an MP3/WAV (stacks) | No |
| `flashbang` | White flash + sound | Yes |
| `tts` | Text-to-speech with tags | Yes |
| `spelling_bee` | Word challenge + definition dialog | Yes |
| `hide_cam` | Hide webcam for N seconds | Yes |
| `stream_ender` | Countdown then stop stream | Yes |

### Add a simple sound

1. Put the file in `sounds/` (create the folder if needed).
2. Add a block to `rewards_config.json`:

```json
{
  "titles": ["Applause"],
  "action": "play_sound",
  "params": { "file": "sounds/applause.mp3" },
  "queue": false
}
```

Multiple random files:

```json
"params": {
  "files": ["sounds/dodgeball.mp3", "sounds/dodgeball2.mp3"]
}
```

3. Create a channel point reward in Twitch with the **same title**.
4. Restart the bot (or re-run after saving JSON — restart is safest).

### Force queue / stack

- `"queue": true` — always serialize this reward  
- `"queue": false` — allow overlapping (good for short SFX)

---

## 6. Sound files

Place audio under `sounds/`. Current config expects files such as:

| Reward title | File |
|--------------|------|
| Fart Sound Effect | `sounds/fart.mp3` |
| Vine Boom Sound Effect | `sounds/vine-boom.mp3` |
| Fahh Sound Effect | `sounds/fah.mp3` |
| Dodgeball Sound Effect | `sounds/dodgeball.mp3` / `dodgeball2.mp3` |
| Pipe Sound Effect | `sounds/pipe.mp3` |
| Discord join call Sound | `sounds/discordjoincall.mp3` |
| LETS GO! | `sounds/letsgo.mp3` |
| **Applause** | `sounds/applause.mp3` |
| **Boo** | `sounds/boo.mp3` |
| **Laugh** | `sounds/laugh.mp3` |

Missing files log a warning and skip playback — the bot keeps running.

---

## 7. OBS setup (required)

### Enable WebSocket
**Tools → WebSocket Server Settings** → Enable, set password, port `4455`.

### Browser sources in your live scene

| Source name (default) | URL |
|-----------------------|-----|
| `Bot` | `http://127.0.0.1:8765/bridge/tts_audio_bridge.html` |
| `BotText` (**required for captions**) | `http://127.0.0.1:8765/bridge/tts_text_bridge.html` |
| `RewardOverlay` | `http://127.0.0.1:8765/bridge/overlay_temp.html` |

**TTS captions:** create a Browser Source named exactly `BotText` (or match `OBS_TTS_TEXT_SOURCE` in `config.py`), URL as above, 1920×1080, transparent background. Put it above gameplay. Restart the bot after adding it so the URL is applied. When TTS runs you should see a log line: `💬 Caption queued for OBS: ...`

- Put **RewardOverlay** above gameplay and camera.
- Width/height can match canvas (e.g. 1920×1080); shutdown source when not visible is optional.
- Refresh browser source cache in OBS if audio/overlay looks stuck.

### Flashbang
Needs a **Color Correction** filter on the webcam (name must match `OBS_WEBCAM_COLOR_CORRECTION_FILTER` in `config.py`).

### Check setup

```powershell
python obs_diagnostics.py
```

### Layout editor (overlay positions)

```powershell
python open_layout_editor.py
```

Or open `http://127.0.0.1:8765/bridge/layout_editor.html` while the bot is running.

---

## 8. Twitch Creator Dashboard checklist

For each reward you want live:

1. Create the reward (Channel Points → Manage Rewards).
2. Title **must match** an entry in `rewards_config.json`.
3. For TTS / Spelling Bee: enable **user input** text.
4. Set cost, cooldown, max per stream as you like.

---

## 9. TTS tags (for reward description)

Viewers can embed these in the TTS text box:

**Voices / accents**
```text
[male] [female] [british] [australian] [indian] [irish] [south_african] [canadian] [anime]
```

**Effects**
```text
[normal] [slow] [fast] [chipmunk] [deep] [robot] [whisper]
```

Example: `[australian] G'day [slow] mate [chipmunk] woo!`

Same tag twice toggles off; `[normal]` resets effects.

---

## 10. Queue, skip, and chat commands

Heavy rewards (TTS, flashbang, spelling bee, hide cam, stream ender) run **one at a time**. Short SFX stack.

| Control | How |
|---------|-----|
| Skip current queued reward | Hotkey `SKIP_REWARD_HOTKEY` (default **F8**) |
| Skip from chat | `!skip` (broadcaster / mods) |
| Queue status | `!queue` |

---

## 11. First chatter & ignored bots

- First chat message of the stream triggers a fireworks-style celebration.
- Resets automatically on **stream.online** (EventSub).
- Bots to ignore: edit `ignored_chatters.json` (includes `streamelements`, `nightbot`, etc.).

---

## 12. Run the bot

```powershell
python twitch.py
```

First run opens a browser for Twitch OAuth. Tokens are saved to `twitch_tokens.json`.

You should see lines like:
- Connected to OBS  
- Audio bridge server running  
- Reward queue started  
- Subscribed to stream.online + channel points  
- `press ENTER to stop`

---

## 13. Troubleshooting

| Problem | What to check |
|---------|----------------|
| No TTS/audio in OBS | Bot running? Browser source URL exact? Refresh cache. Try `AUDIO_ROUTING_MODE = "both"`. |
| Overlay / flash missing | Overlay URL; `RewardOverlay` on top; color-correction filter name. |
| Reward does nothing | Title mismatch in `rewards_config.json`; restart bot; check console logs. |
| Queue never starts | Do not block the process oddly; use latest `twitch.py` (Enter is waited in a thread). |
| StreamElements is first chatter | Confirm login in `ignored_chatters.json`; look for skip log line. |
| Auth / scope errors | Fix `.env`; delete `twitch_tokens.json`; run again. |
| Skip hotkey invalid | Use forms like `f8` or `ctrl+shift+s` in `config.py`. |
| Spelling bee no definition | Needs internet (Datamuse); rare words are retried automatically. |
| Sound not playing | File path under `sounds/`; volume in params; `AUDIO_ROUTING_MODE`. |

---

## 14. File map

| File | Role |
|------|------|
| `twitch.py` | Entry point, chat, EventSub, hotkey |
| `rewards.py` | Action handlers (SFX, TTS, flashbang, spelling bee, …) |
| `rewards_config.json` | Title → action mapping |
| `tts_handler.py` | Edge-TTS + effect tags |
| `audio_handler.py` | Local / browser playback |
| `obs_handler.py` | OBS WebSocket + local HTTP bridge |
| `image_handler.py` | Overlay layers, fireworks, text |
| `reward_queue.py` | Serial queue + skip |
| `config.py` | Non-secret settings |
| `.env` | Secrets |
| `ignored_chatters.json` | First-chatter ignore list |
| `blocked_terms_dont_open_on_stream.json` | TTS moderation list |

## 15. Personal .exe build (optional)

If this bot is **only for you**, you can package it so you double-click an exe instead of using Python.

1. Finish normal setup once (`.env` with your Twitch + OBS values).
2. Double-click **`build_exe.bat`** (needs Python one last time).
3. Run **`dist\CazIsABoi\CazIsABoi.exe`**.

The build copies `.env` **next to** the exe (not compiled into the binary). That way:
- You are not asked for Twitch Client ID/Secret again
- You can still edit OBS password / rewards without rebuilding
- Python source stays out of the way

**Security**
- Do **not** upload or share the `dist\CazIsABoi` folder — it contains your app secret
- Secrets inside or beside an exe can still be extracted by a determined person
- Fine for a private folder on your streaming PC; bad as a public download

Put sound files in `dist\CazIsABoi\sounds\`.