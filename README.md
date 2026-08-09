# TwitchBot (Main Bot)

This is the original/main Twitch channel points bot.

It supports:
- Channel point reward handlers
- TTS rewards (male/female + accent variants)
- Sound effects routed into OBS via browser source
- Fullscreen overlay effects (flashbang style)
- Caption layout editor in browser

## 1) Quick Start

### Prerequisites
- Windows
- Python 3.11+ (3.12 recommended)
- OBS with `obs-websocket` enabled

### Install
```powershell
pip install -r requirements.txt
```

### Configure
Edit `config.py` and set:
- `APP_ID`
- `APP_SECRET`
- `TARGET_CHANNEL`
- `OBS_HOST`
- `OBS_PORT`
- `OBS_PASSWORD`
- OBS source names:
  - `OBS_TTS_SOURCE`
  - `OBS_OVERLAY_SOURCE`
  - `OBS_WEBCAM_SOURCE`
  - `OBS_DISPLAY_SOURCE`

### Run
```powershell
python twitch.py
```

First run opens Twitch auth in browser.

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

Open:
```powershell
python open_layout_editor.py
```

Or browse directly:
- `http://127.0.0.1:8765/bridge/layout_editor.html`

Use it to position/size caption text and save layout.

## 4) Common Commands

Run OBS diagnostics:
```powershell
python obs_diagnostics.py
```

This checks:
- OBS websocket connection
- Scene/source names
- Bridge URLs

## 5) Troubleshooting

### No TTS/audio in OBS
- Confirm bot is running
- Confirm TTS browser source URL is exactly:
  - `http://127.0.0.1:8765/bridge/tts_audio_bridge.html`
- Refresh browser source cache in OBS
- Run `python obs_diagnostics.py`

### Overlay/flash effects do not appear
- Confirm overlay source URL is exactly:
  - `http://127.0.0.1:8765/bridge/overlay_temp.html`
- Ensure `RewardOverlay` is above gameplay in scene order

### OBS source name warnings in diagnostics
- Update names in `config.py` to match OBS exactly (case-sensitive)

### Bot starts but no redemptions trigger
- Ensure reward titles in Twitch match handler names in `rewards.py`
- Re-run `python twitch.py` and complete auth flow

## 6) Docs

More docs are in `docs/`:
- `docs/QUICK_START.md`
- `docs/setup_instructions.md`
- `docs/TTS_GUIDE.md`
- `docs/TTS_VIEWER_GUIDE.md`
- `docs/AUDIO_ROUTING_GUIDE.md`
- `docs/examples.md`
