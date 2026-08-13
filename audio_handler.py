"""
Audio playback handler

Local playback uses just_playback when available (needs _cffi_backend).
If that fails (common in PyInstaller builds), browser/OBS routing still works.
"""
import asyncio
from pathlib import Path
import threading

from obs_handler import queue_audio_for_browser_source

try:
    from config import AUDIO_ROUTING_MODE
except Exception:
    AUDIO_ROUTING_MODE = "browser"

# Lazy import — do NOT import just_playback at module load time.
# PyInstaller often fails to ship _cffi_backend; importing at top level kills startup.
Playback = None
_JUST_PLAYBACK_ERROR = None


def _ensure_playback_class():
    global Playback, _JUST_PLAYBACK_ERROR
    if Playback is not None:
        return Playback
    if _JUST_PLAYBACK_ERROR is not None:
        return None
    try:
        from just_playback import Playback as _Playback
        Playback = _Playback
        return Playback
    except Exception as error:
        _JUST_PLAYBACK_ERROR = error
        print(f"⚠️ Local audio unavailable ({error}). Using browser/OBS routing only.")
        return None


_active_local_lock = threading.Lock()
_active_local_playbacks = set()


def _get_audio_routing_mode():
    mode = str(AUDIO_ROUTING_MODE).strip().lower()
    if mode not in {"browser", "local", "both"}:
        return "browser"
    return mode


def _register_local_playback(playback):
    with _active_local_lock:
        _active_local_playbacks.add(playback)


def _unregister_local_playback(playback):
    with _active_local_lock:
        _active_local_playbacks.discard(playback)


def stop_current_local_playback():
    """Stop every tracked local Playback instance (used by reward skip)."""
    with _active_local_lock:
        active = list(_active_local_playbacks)
        _active_local_playbacks.clear()
    for playback in active:
        try:
            playback.stop()
        except Exception:
            pass
    if active:
        print(f"🔇 Stopped {len(active)} local playback(s)")


async def _play_sound_local(file_path, volume=1.0, wait_until_complete=True):
    sound_path = Path(file_path)

    if not sound_path.exists():
        print(f"❌ Audio file not found: {file_path}")
        return None

    PlaybackCls = _ensure_playback_class()
    if PlaybackCls is None:
        print(f"⚠️ Skipping local playback (no backend): {file_path}")
        return None

    playback = PlaybackCls()
    playback.load_file(str(sound_path))
    playback.set_volume(volume)
    playback.play()
    _register_local_playback(playback)

    try:
        if wait_until_complete:
            while playback.active:
                await asyncio.sleep(0.1)
    except asyncio.CancelledError:
        try:
            playback.stop()
        except Exception:
            pass
        raise
    finally:
        _unregister_local_playback(playback)

    return playback


async def play_sound(file_path, volume=1.0):
    try:
        sound_path = Path(file_path)
        if not sound_path.exists():
            print(f"❌ Audio file not found: {file_path}")
            return

        routing_mode = _get_audio_routing_mode()
        browser_routed = False

        if routing_mode in {"browser", "both"}:
            try:
                bridge_url = queue_audio_for_browser_source(sound_path, volume=volume, mode="sfx")
                print(f"🔊 Routed sound to OBS browser source: {bridge_url}")
                browser_routed = True
                await asyncio.sleep(0.05)
            except Exception as bridge_error:
                print(f"⚠️ Browser audio routing failed: {bridge_error}")

        if routing_mode == "browser" and browser_routed:
            return

        # local or both, or browser failed
        await _play_sound_local(sound_path, volume=volume, wait_until_complete=True)

    except asyncio.CancelledError:
        raise
    except Exception as e:
        print(f"❌ Error playing sound {file_path}: {e}")


async def play_sound_non_blocking(file_path, volume=1.0):
    try:
        sound_path = Path(file_path)
        if not sound_path.exists():
            print(f"❌ Audio file not found: {file_path}")
            return

        routing_mode = _get_audio_routing_mode()
        browser_routed = False
        browser_result = None

        if routing_mode in {"browser", "both"}:
            try:
                bridge_url = queue_audio_for_browser_source(sound_path, volume=volume, mode="sfx")
                print(f"🔊 Routed sound to OBS browser source: {bridge_url}")
                browser_routed = True
                browser_result = {"routed": "browser", "url": bridge_url}
            except Exception as bridge_error:
                print(f"⚠️ Browser audio routing failed: {bridge_error}")

        if routing_mode == "browser" and browser_routed:
            return browser_result

        local_result = await _play_sound_local(sound_path, volume=volume, wait_until_complete=False)
        if routing_mode == "both" and browser_result is not None:
            return {"routed": "both", "browser": browser_result, "local": bool(local_result)}
        return local_result

    except Exception as e:
        print(f"❌ Error playing sound {file_path}: {e}")
        return None


def stop_all_sounds():
    stop_current_local_playback()


async def play_sound_local(file_path, volume=1.0, wait_until_complete=True):
    try:
        return await _play_sound_local(file_path, volume=volume, wait_until_complete=wait_until_complete)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        print(f"❌ Error with local playback {file_path}: {e}")
        return None


async def play_sound_browser(file_path, volume=1.0, mode="sfx"):
    try:
        sound_path = Path(file_path)
        if not sound_path.exists():
            print(f"❌ Audio file not found: {file_path}")
            return None
        bridge_url = queue_audio_for_browser_source(sound_path, volume=volume, mode=mode)
        print(f"🔊 Routed sound to OBS browser source: {bridge_url}")
        return {"routed": "browser", "url": bridge_url}
    except Exception as e:
        print(f"❌ Error with browser playback {file_path}: {e}")
        return None


async def play_sound_local_and_browser(
    file_path,
    local_volume=1.0,
    browser_volume=1.0,
    browser_mode="sfx",
    wait_for_local=True,
):
    browser_task = asyncio.create_task(
        play_sound_browser(file_path, volume=browser_volume, mode=browser_mode)
    )
    local_task = asyncio.create_task(
        play_sound_local(file_path, volume=local_volume, wait_until_complete=wait_for_local)
    )
    try:
        await asyncio.gather(browser_task, local_task)
    except asyncio.CancelledError:
        local_task.cancel()
        browser_task.cancel()
        stop_current_local_playback()
        raise
