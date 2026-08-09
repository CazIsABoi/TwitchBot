# TwitchBot (Main Bot)

Channel points bot with OBS overlays, TTS, sound effects, and first-chatter celebrations.

## 1) Quick Start

### Prerequisites
- Windows
- Python 3.11+ (3.12 recommended)
- OBS with `obs-websocket` enabled

### Install
```powershell
pip install -r requirements.txt
```

### Configure secrets
1. Copy the example env file:
   ```powershell
   copy .env.example .env
   ```
2. Edit `.env` and set:
   - `APP_ID` / `APP_SECRET` from [Twitch Dev Console](https://dev.twitch.tv/console)
   - `TARGET_CHANNEL` (your channel login, lowercase)
   - `OBS_PASSWORD` (and host/port if needed)

**Security:** rotate `APP_SECRET` in the Twitch console and your OBS WebSocket password after sharing any old copies of this project. Never commit `.env` or `twitch_tokens.json`.

Non-secret tuning (OBS source names, flashbang timings, audio mode) still lives in `config.py`.

### Configure reward titles
Edit `rewards_config.json` to map **Twitch reward titles** to bot actions. Rename a reward in the Creator Dashboard? Update the title string in this JSON — no Python changes needed.

Simple sound effects only need a title + file path:
```json
{
  "titles": ["My New Sound"],
  "action": "play_sound",
  "params": { "file": "sounds/mysound.mp3" }
}
```

Built-in actions: `play_sound`, `flashbang`, `stream_ender`, `hide_cam`, `tts`, `spelling_bee`.

### First-chatter bots to ignore
Edit `ignored_chatters.json` (includes `streamelements`, `nightbot`, etc.). Names are matched case-insensitively against chat logins.

### Run
```powershell
python twitch.py
```

First run opens Twitch auth in the browser. First-chatter resets automatically on **stream.online** (EventSub).

## 2) OBS Setup (Required)

Add 2 Browser Sources to your live scene:

1. TTS/audio source
- Name should match `OBS_TTS_SOURCE` in `config.py` (default: `Bot`)
- URL: `http://127.0.0.1:8765/bridge/tts_audio_bridge.html`

2. Overlay source
- Name should match `OBS_OVERLAY_SOURCE` in `config.py` (default: `RewardOverlay`)
- URL: `http://127.0.0.1:8765/bridge/overlay_temp.html`

Put `RewardOverlay` above gameplay/camera sources.

## 3) Layout Editor

```powershell
python open_layout_editor.py
```

Or open `http://127.0.0.1:8765/bridge/layout_editor.html`

## 4) Common Commands

```powershell
python obs_diagnostics.py
```

## 5) Troubleshooting

### No TTS/audio in OBS
- Confirm bot is running and browser source URL is exact
- Refresh browser source cache in OBS
- Run `python obs_diagnostics.py`

### Overlay/flash effects missing
- Confirm overlay URL and scene order (`RewardOverlay` on top)

### Reward titles not triggering
- Titles in Twitch must match an entry in `rewards_config.json` (case-insensitive)
- Restart the bot after editing the JSON

### StreamElements won first chatter
- Confirm the bot login is in `ignored_chatters.json` (default includes `streamelements`)
- Look for log line: `⏭️ Skipping ignored chatter for first-message: streamelements`
- First chatter now resets on each `stream.online`, not only on bot start

### Secrets / auth failures
- Re-check `.env` values
- Delete `twitch_tokens.json` and re-authenticate if scopes changed
