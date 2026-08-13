# CazIsABot

Twitch channel-points bot for OBS: TTS, SFX, flashbang, spelling bee, overlays, and first-chatter celebrations.

## Requirements

- **Windows**
- **Python 3.12** ([download](https://www.python.org/downloads/) — tick *Add to PATH*)
- **OBS Studio** with WebSocket enabled (Tools → WebSocket Server Settings)
- **Your own Twitch app** at [dev.twitch.tv/console/apps](https://dev.twitch.tv/console/apps)

## Quick start

1. Double-click **`Start Bot.bat`** (creates venv, installs deps, opens setup if needed).
2. In the wizard (or `.env`): paste **Client ID** / **Client Secret** from your Twitch app, your channel login (lowercase), and OBS WebSocket password.
3. Create channel-point rewards whose **titles match** `rewards_config.json`.
4. Put audio in `sounds\` (see titles in that JSON).
5. Log in with Twitch when the browser opens.

Optional: leave **Auto-create OBS browser sources** on so the bot adds `Bot`, `BotText`, and `RewardOverlay`.

Re-run setup anytime: `python setup_wizard.py`

## Twitch app

1. [dev.twitch.tv/console/apps](https://dev.twitch.tv/console/apps) → **Register Your Application**
2. OAuth redirect: `http://localhost:17563` (or the URL shown on first login)
3. Copy **Client ID** → `APP_ID`, **Client Secret** → `APP_SECRET` in `.env`

Each streamer should use **their own** app and complete the browser login for their channel.

## OBS sources

| Source | URL | Role |
|--------|-----|------|
| `Bot` | `http://127.0.0.1:8765/bridge/tts_audio_bridge.html` | Stream audio |
| `BotText` | `http://127.0.0.1:8765/bridge/tts_text_bridge.html` | TTS captions |
| `RewardOverlay` | `http://127.0.0.1:8765/bridge/overlay_temp.html` | Fireworks / overlays |

Flashbang needs a **Colour Correction** filter on the webcam (name must match config / `.env`).

## Configure

| File | What |
|------|------|
| **`.env`** | Secrets + preferences (first chatter, audio mode, skip key, caption size, flashbang, OBS names) |
| **`rewards_config.json`** | Reward title → action |
| **`ignored_chatters.json`** | Bots that can’t win first chatter |
| **`sounds\`** | MP3/WAV assets |

Copy `env.example` → `.env` if you’re not using the wizard. **Never commit `.env` or `twitch_tokens.json`.**

### Useful `.env` prefs

```env
FIRST_CHATTER_ENABLED=true
AUDIO_ROUTING_MODE=local
SKIP_REWARD_HOTKEY=f8
```

`AUDIO_ROUTING_MODE`: `local` | `browser` | `both`

## Rewards

Built-in actions: `play_sound`, `tts`, `spelling_bee`, `flashbang`, `hide_cam`, `stream_ender`.

Heavy actions queue one-at-a-time. Skip with **F8** or chat `!skip` / `!queue` (mod/broadcaster).

### TTS tags (viewer input)

`[male] [female] [british] [australian] [indian] [irish] [south_african] [canadian] [anime]`  
`[normal] [slow] [fast] [chipmunk] [deep] [robot] [whisper]`

## Run / build

```text
Start Bot.bat          → recommended
python twitch.py       → same, with venv active
build_exe.bat          → dist\CazIsABot\CazIsABot.exe
```

Exe users edit **`.env`** and **`rewards_config.json`** next to the exe, then restart. Do not share a folder that contains your secrets.

## Troubleshooting

| Issue | Check |
|-------|--------|
| Auth / redemptions error | Own Twitch app; delete `twitch_tokens.json`; log in as channel owner |
| No audio in OBS | Bot running; browser source URL; try `AUDIO_ROUTING_MODE=both` |
| Reward does nothing | Title must match `rewards_config.json` exactly |
| SmartScreen on exe | Unsigned build — More info → Run anyway |

More detail: `python obs_diagnostics.py` · layout editor: `python open_layout_editor.py`