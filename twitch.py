import asyncio
import os
import sys
import json
from pathlib import Path

from twitchAPI.twitch import Twitch
from twitchAPI.oauth import UserAuthenticator
from twitchAPI.type import AuthScope, ChatEvent, TwitchAuthorizationException
from twitchAPI.chat import Chat, EventData, ChatMessage, ChatSub, ChatCommand
from twitchAPI.eventsub.websocket import EventSubWebsocket
from twitchAPI.object.eventsub import (
    ChannelPointsCustomRewardRedemptionAddEvent,
    StreamOnlineEvent,
)
from twitchAPI.helper import first
from config import APP_ID, APP_SECRET, TARGET_CHANNEL, IGNORED_CHATTERS_FILE, SKIP_REWARD_HOTKEY, FIRST_CHATTER_ENABLED
from rewards import handle_reward, load_rewards_config
from reward_queue import reward_queue
from audio_handler import play_sound
from image_handler import show_fireworks_announcement
from obs_handler import (
    configure_tts_browser_source,
    configure_tts_text_browser_source,
    reset_audio_bridge_events,
)
try:
    from obs_handler import ensure_obs_browser_sources
except ImportError:
    ensure_obs_browser_sources = None  # older obs_handler.py without auto-create
from setup_wizard import setup_is_complete, run_wizard

try:
    from tts_handler import cleanup_tts_cache
except ImportError:
    cleanup_tts_cache = None

USER_SCOPE = [AuthScope.CHAT_READ, AuthScope.CHAT_EDIT, AuthScope.CHANNEL_READ_REDEMPTIONS]
try:
    from config import BOT_ROOT as _ROOT
except Exception:
    _ROOT = Path(__file__).resolve().parent
TOKEN_FILE = _ROOT / "twitch_tokens.json"

FIRST_CHATTER_NAME = None
FIRST_CHATTER_LOCK = asyncio.Lock()

# Default ignore list; replaced/extended by ignored_chatters.json on startup.
FIRST_CHATTER_IGNORED_USERS = {
    "streamelements",
    "streamlabs",
    "nightbot",
    "moobot",
    "fossabot",
}


def load_ignored_chatters(path: Path = IGNORED_CHATTERS_FILE):
    """Load bot/system accounts that must never win first-chatter."""
    global FIRST_CHATTER_IGNORED_USERS
    defaults = set(FIRST_CHATTER_IGNORED_USERS)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            loaded = {str(name).strip().lower() for name in data if str(name).strip()}
            FIRST_CHATTER_IGNORED_USERS = defaults | loaded
            print(
                f"✅ Ignoring {len(FIRST_CHATTER_IGNORED_USERS)} chatter(s) for first-message: "
                f"{sorted(FIRST_CHATTER_IGNORED_USERS)}"
            )
            return
    except FileNotFoundError:
        print(f"⚠️ {path.name} not found — using built-in ignore list")
    except Exception as error:
        print(f"⚠️ Could not load {path.name}: {error}")
    FIRST_CHATTER_IGNORED_USERS = defaults


def is_ignored_chatter(user_name: str) -> bool:
    """Case-insensitive match against ignored bot logins."""
    if not user_name:
        return True
    return user_name.strip().lower() in FIRST_CHATTER_IGNORED_USERS


async def reset_first_chatter(reason: str = ""):
    """Clear first-chatter so the next real viewer can win the celebration."""
    global FIRST_CHATTER_NAME
    async with FIRST_CHATTER_LOCK:
        previous = FIRST_CHATTER_NAME
        FIRST_CHATTER_NAME = None
    suffix = f" ({reason})" if reason else ""
    if previous:
        print(f"🔄 First chatter reset{suffix}. Previous winner was: {previous}")
    else:
        print(f"🔄 First chatter reset{suffix}. Ready for the next stream.")


async def trigger_first_chatter_celebration(user_name: str):
    """Show first chatter celebration in OBS and play fireworks audio."""
    message = f"{user_name} was the first chatter"
    await asyncio.gather(
        show_fireworks_announcement(
            "images/fireworks.gif",
            message,
            hold_duration=10.0,
            fade_duration=5.0,
            force_top=True,
        ),
        play_sound("sounds/fireworks.mp3", volume=1.0),
    )


def load_saved_tokens():
    """Load previously saved Twitch OAuth tokens if they exist."""
    if not TOKEN_FILE.exists():
        return None

    try:
        data = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    except Exception as error:
        print(f"⚠️ Could not read token file: {error}")
        return None

    if not isinstance(data, dict):
        return None

    token = data.get("token")
    refresh_token = data.get("refresh_token")
    if not token or not refresh_token:
        return None

    return token, refresh_token


def save_tokens(token, refresh_token):
    """Persist Twitch OAuth tokens for future bot startups."""
    try:
        TOKEN_FILE.write_text(
            json.dumps({"token": token, "refresh_token": refresh_token}, indent=2),
            encoding="utf-8",
        )
        print(f"💾 Saved Twitch tokens to {TOKEN_FILE.name}")
    except Exception as error:
        print(f"⚠️ Could not save Twitch tokens: {error}")


async def authenticate_twitch(twitch):
    """Authenticate using saved tokens when available, otherwise start the browser flow."""
    saved_tokens = load_saved_tokens()
    if saved_tokens:
        token, refresh_token = saved_tokens
        try:
            await twitch.set_user_authentication(token, USER_SCOPE, refresh_token)
            print("✅ Reused saved Twitch OAuth tokens")
            return token, refresh_token
        except Exception as error:
            print(f"⚠️ Saved tokens failed, falling back to fresh auth: {error}")

    auth = UserAuthenticator(twitch, USER_SCOPE)
    token, refresh_token = await auth.authenticate()
    await twitch.set_user_authentication(token, USER_SCOPE, refresh_token)
    save_tokens(token, refresh_token)
    return token, refresh_token


async def on_ready(ready_event: EventData):
    print("Bot is ready for work, joining channels")
    # Do NOT permanently claim first-chatter here — wait for stream.online
    # or the first real (non-bot) chat message after going live.
    await ready_event.chat.join_room(TARGET_CHANNEL)


async def on_message(msg: ChatMessage):
    global FIRST_CHATTER_NAME

    print(f"in {msg.room.name}, {msg.user.name} said: {msg.text}")

    if not FIRST_CHATTER_ENABLED:
        return

    user_name = (msg.user.name or "").strip()
    if not user_name:
        return

    if is_ignored_chatter(user_name):
        # Explicit log so you can verify streamelements is being skipped.
        print(f"⏭️ Skipping ignored chatter for first-message: {user_name}")
        return

    if FIRST_CHATTER_NAME is not None:
        return

    should_trigger = False
    async with FIRST_CHATTER_LOCK:
        if FIRST_CHATTER_NAME is None:
            FIRST_CHATTER_NAME = user_name
            should_trigger = True

    if not should_trigger:
        return

    print(f"🎉 First chatter detected: {FIRST_CHATTER_NAME}")
    asyncio.create_task(trigger_first_chatter_celebration(FIRST_CHATTER_NAME))


async def on_sub(sub: ChatSub):
    print(
        f"New subscription in {sub.room.name}:\n"
        f"  Type: {sub.sub_plan}\n"
        f"  Message: {sub.sub_message}"
    )


async def test_command(cmd: ChatCommand):
    if len(cmd.parameter) == 0:
        await cmd.reply("you did not tell me what to reply with")
    else:
        await cmd.reply(f"{cmd.user.name}: {cmd.parameter}")



def _is_broadcaster_or_mod(cmd: ChatCommand) -> bool:
    """Allow skip/status commands for broadcaster and mods."""
    try:
        if getattr(cmd.user, 'mod', False):
            return True
        # Broadcaster badge / name match
        name = (cmd.user.name or '').strip().lower()
        if name and name == str(TARGET_CHANNEL).strip().lower():
            return True
        badges = getattr(cmd.user, 'badges', None) or {}
        if isinstance(badges, dict) and ('broadcaster' in badges or 'moderator' in badges):
            return True
        # twitchAPI sometimes exposes badge string
        if isinstance(badges, str) and ('broadcaster' in badges or 'moderator' in badges):
            return True
    except Exception:
        pass
    return False


async def skip_command(cmd: ChatCommand):
    """!skip — cancel the currently running queued reward (mod/broadcaster)."""
    if not _is_broadcaster_or_mod(cmd):
        await cmd.reply('Only the broadcaster or mods can skip rewards.')
        return
    ok = reward_queue.skip_current(reason=f'chat !skip by {cmd.user.name}')
    if ok:
        await cmd.reply(f'Skipped. {reward_queue.status_text()}')
    else:
        await cmd.reply(f'Nothing to skip. {reward_queue.status_text()}')


async def queue_status_command(cmd: ChatCommand):
    """!queue — show current/pending queued rewards."""
    await cmd.reply(reward_queue.status_text())


def start_skip_hotkey_listener(loop: asyncio.AbstractEventLoop):
    """Background thread: global keyboard shortcut to skip current queued reward."""
    hotkey = str(SKIP_REWARD_HOTKEY or '').strip()
    if not hotkey:
        print('⌨️ Skip hotkey disabled (SKIP_REWARD_HOTKEY is empty)')
        return None

    try:
        from pynput import keyboard
    except ImportError:
        print('⚠️ pynput not installed — skip hotkey disabled. Run: pip install pynput')
        return None

    def on_activate():
        print(f'⌨️ Skip hotkey pressed ({hotkey})')
        reward_queue.skip_current(reason=f'hotkey {hotkey}')

    # pynput expects tokens like <f8>, <ctrl>+<shift>+s
    normalized = hotkey.lower().replace(' ', '')
    if not normalized.startswith('<') and '+' not in normalized and normalized.startswith('f') and normalized[1:].isdigit():
        normalized = f'<{normalized}>'
    elif '+' in normalized:
        parts = []
        for part in normalized.split('+'):
            part = part.strip()
            if part.startswith('f') and part[1:].isdigit():
                parts.append(f'<{part}>')
            elif part in {'ctrl', 'alt', 'shift', 'cmd', 'cmd_l', 'cmd_r'}:
                parts.append(f'<{part}>')
            else:
                parts.append(part)
        normalized = '+'.join(parts)

    try:
        hotkey_obj = keyboard.HotKey(keyboard.HotKey.parse(normalized), on_activate)
    except Exception as error:
        print(f'⚠️ Invalid SKIP_REWARD_HOTKEY={hotkey!r} (normalized={normalized!r}): {error}')
        print('   Examples: f8, f9, ctrl+shift+s')
        return None

    def on_press(key):
        try:
            hotkey_obj.press(listener.canonical(key))
        except Exception:
            pass

    def on_release(key):
        try:
            hotkey_obj.release(listener.canonical(key))
        except Exception:
            pass

    listener = keyboard.Listener(on_press=on_press, on_release=on_release)
    listener.daemon = True
    listener.start()
    print(f'⌨️ Skip hotkey armed: {hotkey} (also available as chat !skip)')
    return listener


async def on_channel_points_redeem(data: ChannelPointsCustomRewardRedemptionAddEvent):
    await handle_reward(data)


async def on_stream_online(data: StreamOnlineEvent):
    """Reset first-chatter when the channel goes live."""
    login = getattr(data.event, "broadcaster_user_login", None) or TARGET_CHANNEL
    print(f"🔴 Stream online detected for {login}")
    await reset_first_chatter(reason="stream.online")


async def run():
    # First-run wizard if .env is incomplete (also available via Start Bot.bat)
    if not setup_is_complete():
        print("🛠️ First-run setup needed — opening wizard…")
        ok = await asyncio.get_running_loop().run_in_executor(None, run_wizard)
        if not ok:
            print("❌ Setup incomplete. Re-run Start Bot.bat or: python setup_wizard.py")
            return
        print("✅ Setup saved. Reloading settings…")
        # Re-exec so config.py picks up the new .env cleanly
        os.execv(sys.executable, [sys.executable, *sys.argv])

    if not APP_ID or not APP_SECRET:
        print("❌ APP_ID and APP_SECRET are required. Copy env.example to .env and fill them in.")
        return
    if not TARGET_CHANNEL:
        print("❌ TARGET_CHANNEL is required in .env (or env)")
        return

    load_ignored_chatters()
    load_rewards_config()
    if FIRST_CHATTER_ENABLED:
        print("🎉 First chatter celebration is ON (config.FIRST_CHATTER_ENABLED)")
    else:
        print("🔕 First chatter celebration is OFF (config.FIRST_CHATTER_ENABLED=False)")

    try:
        twitch = await Twitch(APP_ID, APP_SECRET)
    except TwitchAuthorizationException as error:
        message = str(error)
        if "invalid client secret" in message.lower():
            print("❌ Twitch authentication failed: invalid APP_SECRET for this APP_ID.")
            print("   Open Twitch Dev Console, copy the current Client Secret, and update your .env/env file.")
        else:
            print(f"❌ Twitch authentication failed: {message}")
        return

    await authenticate_twitch(twitch)

    chat = await Chat(twitch)

    reset_audio_bridge_events()
    await configure_tts_browser_source()
    await configure_tts_text_browser_source()
    if ensure_obs_browser_sources is not None:
        await ensure_obs_browser_sources()
    else:
        print("ℹ️ OBS auto-create sources unavailable (update obs_handler.py to enable it)")

    await reward_queue.start()
    skip_listener = start_skip_hotkey_listener(asyncio.get_running_loop())

    chat.register_event(ChatEvent.READY, on_ready)
    chat.register_event(ChatEvent.MESSAGE, on_message)
    chat.register_event(ChatEvent.SUB, on_sub)
    chat.register_command("reply", test_command)
    chat.register_command("skip", skip_command)
    chat.register_command("queue", queue_status_command)

    eventsub = EventSubWebsocket(twitch)
    eventsub.start()

    user = await first(twitch.get_users(logins=[TARGET_CHANNEL]))
    if user is None:
        print(f"⚠️ Could not resolve Twitch user '{TARGET_CHANNEL}'. Trying the authenticated user instead...")
        user = await first(twitch.get_users())

    if user is None:
        print("❌ Unable to resolve a Twitch user for EventSub subscriptions.")
        print("   Check TARGET_CHANNEL in .env and make sure the OAuth account can access it.")
        await eventsub.stop()
        await twitch.close()
        return

    await eventsub.listen_channel_points_custom_reward_redemption_add(user.id, on_channel_points_redeem)
    await eventsub.listen_stream_online(user.id, on_stream_online)
    print(f"✅ Subscribed to stream.online + channel points for {user.display_name} ({user.id})")

    chat.start()

    try:
        # Must not block the event loop — otherwise the reward queue worker never runs.
        await asyncio.get_running_loop().run_in_executor(
            None, input, "press ENTER to stop\n"
        )
    finally:
        if cleanup_tts_cache is not None:
            try:
                cleanup_tts_cache()
            except Exception:
                pass
        try:
            await reward_queue.stop()
        except Exception as stop_error:
            print(f"⚠️ Queue shutdown note: {stop_error}")
        if skip_listener is not None:
            try:
                skip_listener.stop()
            except Exception:
                pass
        try:
            chat.stop()
        except Exception:
            pass
        try:
            await eventsub.stop()
        except Exception:
            pass
        try:
            await twitch.close()
        except Exception:
            pass


def _crash_log_path():
    try:
        from config import BOT_ROOT
        return BOT_ROOT / "crash.log"
    except Exception:
        return Path(__file__).resolve().parent / "crash.log"


def main():
    """Entry point with visible errors when running as a double-clicked .exe."""
    try:
        asyncio.run(run())
    except SystemExit:
        raise
    except BaseException:
        import traceback

        details = traceback.format_exc()
        print("\n" + "=" * 60)
        print("TwitchBot crashed with an unhandled exception:")
        print("=" * 60)
        print(details)
        print(f"frozen={getattr(sys, 'frozen', False)} exe={sys.executable}")
        print(f"cwd={Path.cwd()}")

        log_path = _crash_log_path()
        try:
            log_path.write_text(details, encoding="utf-8")
            print(f"Full traceback saved to: {log_path}")
        except Exception as write_error:
            print(f"(Could not write crash.log: {write_error})")

        print("=" * 60)
        try:
            input("Press ENTER to close this window...")
        except EOFError:
            pass
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()