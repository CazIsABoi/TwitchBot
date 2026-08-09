"""
Audio playback handler
Requires: pip install just_playback
"""
from just_playback import Playback
import asyncio
from pathlib import Path

from obs_handler import queue_audio_for_browser_source

try:
    from config import AUDIO_ROUTING_MODE
except Exception:
    AUDIO_ROUTING_MODE = "browser"


def _get_audio_routing_mode():
    mode = str(AUDIO_ROUTING_MODE).strip().lower()
    if mode not in {"browser", "local", "both"}:
        return "browser"
    return mode


async def _play_sound_local(file_path, volume=1.0, wait_until_complete=True):
    sound_path = Path(file_path)

    if not sound_path.exists():
        print(f"❌ Audio file not found: {file_path}")
        return None

    playback = Playback()
    playback.load_file(str(sound_path))
    playback.set_volume(volume)
    playback.play()

    if wait_until_complete:
        while playback.active:
            await asyncio.sleep(0.1)

    return playback

async def play_sound(file_path, volume=1.0):
    """
    Play an audio file
    
    Args:
        file_path: Path to audio file (mp3, wav, ogg, flac)
        volume: Volume level 0.0 to 1.0
    """
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

        # Local playback path (always for local/both, and as browser fallback).
        await _play_sound_local(sound_path, volume=volume, wait_until_complete=True)
        
    except Exception as e:
        print(f"❌ Error playing sound {file_path}: {e}")


async def play_sound_non_blocking(file_path, volume=1.0):
    """
    Play sound without waiting for it to finish
    Good for overlapping sounds
    """
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
    """Note: just_playback doesn't have a global stop, so this is limited"""
    print("⚠️ Individual playback objects must be stopped manually")
    # To stop a specific sound, call playback.stop() on the returned object


async def play_sound_local(file_path, volume=1.0, wait_until_complete=True):
    """Play sound directly on the local machine without browser routing."""
    try:
        return await _play_sound_local(file_path, volume=volume, wait_until_complete=wait_until_complete)
    except Exception as e:
        print(f"❌ Error with local playback {file_path}: {e}")
        return None


async def play_sound_browser(file_path, volume=1.0, mode="sfx"):
    """Queue sound for OBS browser-source playback only."""
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
    """Play sound immediately locally and also queue it for browser-source output."""
    browser_task = asyncio.create_task(play_sound_browser(file_path, volume=browser_volume, mode=browser_mode))
    local_task = asyncio.create_task(play_sound_local(file_path, volume=local_volume, wait_until_complete=wait_for_local))
    await asyncio.gather(browser_task, local_task)
