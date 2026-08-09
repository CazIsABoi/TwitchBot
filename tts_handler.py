"""
Text-to-Speech Handler using edge-tts
Requires: pip install edge-tts

Supports inline tags for voice effects:
Example: "Hello [slow] I am speaking slowly [fast] now fast! [normal]"
"""
import edge_tts
import asyncio
from pathlib import Path
import os
import re
from audio_handler import play_sound, play_sound_local
from obs_handler import queue_audio_for_browser_source, show_caption_in_browser_source

try:
    from config import AUDIO_ROUTING_MODE
except Exception:
    AUDIO_ROUTING_MODE = "browser"

# Available voices - you can find more at: edge-tts --list-voices
VOICES = {
    "default": "en-US-AriaNeural",
    "male": "en-US-GuyNeural",
    "female": "en-US-AriaNeural",
    "british": "en-GB-SoniaNeural",
    "australian": "en-AU-NatashaNeural",
    "indian": "en-IN-NeerjaNeural",
    "irish": "en-IE-EmilyNeural",
    "south_african": "en-ZA-LeahNeural",
    "canadian": "en-CA-ClaraNeural",
    "anime": "en-US-JennyNeural",
    "deep": "en-US-DavisNeural",
    "chipmunk": "en-US-JennyNeural",  # Will use high pitch
    "robot": "en-US-EricNeural",
}

# Voice effect presets
EFFECTS = {
    "normal": {"rate": "+0%", "pitch": "+0Hz"},
    "slow": {"rate": "-40%", "pitch": "+0Hz"},
    "fast": {"rate": "+50%", "pitch": "+0Hz"},
    "high": {"rate": "+0%", "pitch": "+80Hz"},
    "chipmunk": {"rate": "+20%", "pitch": "+100Hz"},
    "deep": {"rate": "-20%", "pitch": "-50Hz"},
    "robot": {"rate": "-15%", "pitch": "-20Hz"},
    "whisper": {"rate": "-30%", "pitch": "-10Hz"},
    "excited": {"rate": "+30%", "pitch": "+30Hz"},
    "sad": {"rate": "-30%", "pitch": "-20Hz"},
}

# Create temp directory for TTS files
TEMP_DIR = Path("temp_tts")
TEMP_DIR.mkdir(exist_ok=True)


def use_browser_audio():
    mode = str(AUDIO_ROUTING_MODE).strip().lower()
    return mode in {"browser", "both"}


def use_local_audio():
    mode = str(AUDIO_ROUTING_MODE).strip().lower()
    return mode in {"local", "both"}


def push_tts_caption(speaker, text):
    """Send caption text to the TTS browser source even when local audio mode is active."""
    spoken = str(text or "").strip()
    if not spoken:
        return

    caption = f"{speaker} said: {spoken}" if speaker else spoken
    try:
        show_caption_in_browser_source(caption=caption, speaker=speaker or "", text=spoken)
    except Exception as error:
        print(f"⚠️ Could not push TTS caption event: {error}")


def strip_inline_tts_tags(text):
    cleaned_text = re.sub(r"\[/?\w+\]", "", text)
    return re.sub(r"\s+", " ", cleaned_text).strip()


async def parse_and_speak(text, base_voice="female", volume=1.0, speaker=None):
    """
    Parse text with inline tags and speak with effects
    
    Example: "Hello [slow] I'm slow [fast] now fast! [normal] back to normal"
    Example: "[australian] G'day [indian] namaste [female] back to female"
    
    Supported effect tags:
        [normal] [slow] [fast] [high] [chipmunk] [deep] [robot]
        [whisper] [excited] [sad]
    Supported accent/voice tags:
        [male] [female] [british] [australian] [indian]
        [irish] [south_african] [canadian]

    Tag behavior:
        - Using the same tag again toggles it off
          Example: [slow]text[slow] resets back to [normal]
        - Closing-style tags also work
          Example: [slow]text[/slow]
    
    Args:
        text: Text with optional inline tags
        base_voice: Base voice to use (male/female/british/etc)
        volume: Playback volume
    """
    try:
        spoken_text = strip_inline_tts_tags(text)

        # Pattern captures tags like [slow], [/slow], [australian], [/australian]
        pattern = r'\[(/?\w+)\]|([^\[\]]+)'
        matches = re.findall(pattern, text)
        
        current_effect = "normal"
        current_voice = base_voice if base_voice in VOICES else "female"
        segments = []
        
        for raw_tag, content in matches:
            if raw_tag:
                tag = raw_tag.lower()
                is_closing = tag.startswith('/')
                tag_name = tag[1:] if is_closing else tag

                # [normal] always resets effect
                if tag_name == "normal":
                    current_effect = "normal"
                    continue

                # Effect tags
                if tag_name in EFFECTS:
                    if is_closing:
                        current_effect = "normal"
                    elif current_effect == tag_name and tag_name != "normal":
                        # Toggle off if same tag is used again, e.g. [slow]...[slow]
                        current_effect = "normal"
                    else:
                        current_effect = tag_name
                    continue

                # Voice/accent tags
                if tag_name in VOICES:
                    if is_closing:
                        current_voice = base_voice if base_voice in VOICES else "female"
                    elif current_voice == tag_name:
                        # Toggle off if same tag is used again
                        current_voice = base_voice if base_voice in VOICES else "female"
                    else:
                        current_voice = tag_name
                    continue

                # Unknown tag: ignore silently
            elif content.strip():
                chunk = content.strip()

                # Merge adjacent chunks with the same effect+voice to minimize segment count.
                if segments and segments[-1]["effect"] == current_effect and segments[-1]["voice"] == current_voice:
                    segments[-1]["text"] = f"{segments[-1]['text']} {chunk}"
                else:
                    segments.append({
                        "text": chunk,
                        "effect": current_effect,
                        "voice": current_voice,
                    })
        
        # If no segments (no tags found), use the whole text
        if not segments:
            segments = [{
                "text": text,
                "effect": "normal",
                "voice": current_voice,
            }]

        # Build a synthesis queue with stable cache keys.
        import hashlib
        queue = []
        for segment in segments:
            effect = EFFECTS[segment["effect"]]
            voice_name = VOICES.get(segment["voice"], VOICES["female"])
            queue.append((segment, effect, voice_name))

        # Cache by the full parsed message plan so repeated redeems play instantly.
        plan_signature = "||".join(
            f"{segment['voice']}|{segment['effect']}|{segment['text']}"
            for segment, _, _ in queue
        )
        final_hash = hashlib.md5(plan_signature.encode()).hexdigest()[:12]
        final_output = TEMP_DIR / f"tts_full_{final_hash}.mp3"

        if not final_output.exists():
            combined_audio = bytearray()

            # Synthesize each segment and append raw audio chunks to one MP3 blob.
            # This avoids starting/stopping playback per tag section.
            for segment, effect, voice_name in queue:
                communicate = edge_tts.Communicate(
                    text=segment["text"],
                    voice=voice_name,
                    rate=effect["rate"],
                    pitch=effect["pitch"],
                )

                async for msg in communicate.stream():
                    if msg.get("type") == "audio":
                        combined_audio.extend(msg.get("data", b""))

            if not combined_audio:
                print("❌ TTS produced no audio output")
                return False

            final_output.write_bytes(combined_audio)

        # Single-file playback for natural flow.
        browser_routed = False
        if use_browser_audio():
            try:
                bridge_url = queue_audio_for_browser_source(
                    final_output,
                    volume=volume,
                    caption=f"{speaker} said: {spoken_text}" if speaker else spoken_text,
                    speaker=speaker or '',
                    text=spoken_text,
                    mode="tts",
                )
                print(f"🔊 Routed TTS to OBS browser source: {bridge_url}")
                browser_routed = True
            except Exception as bridge_error:
                print(f"⚠️ TTS browser routing failed: {bridge_error}")

        if use_local_audio():
            push_tts_caption(speaker, spoken_text)

        if use_local_audio() or not browser_routed:
            await play_sound_local(str(final_output), volume=volume, wait_until_complete=True)
        
        return True
        
    except Exception as e:
        print(f"❌ Error with TTS parsing: {e}")
        return False


async def text_to_speech(text, voice="default", rate="+0%", pitch="+0Hz", volume=1.0, speaker=None):
    """
    Convert text to speech and play it
    
    Args:
        text: Text to speak
        voice: Voice preset from VOICES dict or custom voice name
        rate: Speed adjustment (e.g., "+50%", "-25%")
        pitch: Pitch adjustment (e.g., "+10Hz", "-5Hz")
        volume: Playback volume 0.0 to 1.0
    
    Returns:
        Path to the generated audio file
    """
    try:
        # Get voice name
        voice_name = VOICES.get(voice, voice)
        
        # Generate unique filename
        import hashlib
        text_hash = hashlib.md5(text.encode()).hexdigest()[:8]
        output_file = TEMP_DIR / f"tts_{text_hash}.mp3"
        
        # Check if already cached
        if output_file.exists():
            browser_routed = False
            if use_browser_audio():
                try:
                    bridge_url = queue_audio_for_browser_source(
                        output_file,
                        volume=volume,
                        caption=f"{speaker} said: {text}" if speaker else text,
                        speaker=speaker or "",
                        text=text,
                        mode="tts",
                    )
                    print(f"🔊 Routed TTS to OBS browser source: {bridge_url}")
                    browser_routed = True
                except Exception as bridge_error:
                    print(f"⚠️ TTS browser routing failed: {bridge_error}")

            if use_local_audio():
                push_tts_caption(speaker, text)

            if use_local_audio() or not browser_routed:
                await play_sound_local(str(output_file), volume=volume, wait_until_complete=True)
            return output_file
        
        # Create TTS
        communicate = edge_tts.Communicate(
            text=text,
            voice=voice_name,
            rate=rate,
            pitch=pitch
        )
        
        # Save to file
        await communicate.save(str(output_file))
        
        browser_routed = False
        if use_browser_audio():
            try:
                bridge_url = queue_audio_for_browser_source(
                    output_file,
                    volume=volume,
                    caption=f"{speaker} said: {text}" if speaker else text,
                    speaker=speaker or "",
                    text=text,
                    mode="tts",
                )
                print(f"🔊 Routed TTS to OBS browser source: {bridge_url}")
                browser_routed = True
            except Exception as bridge_error:
                print(f"⚠️ TTS browser routing failed: {bridge_error}")

        if use_local_audio():
            push_tts_caption(speaker, text)

        if use_local_audio() or not browser_routed:
            await play_sound_local(str(output_file), volume=volume, wait_until_complete=True)
        
        return output_file
        
    except Exception as e:
        print(f"❌ Error with TTS: {e}")
        return None


async def tts_with_effects(text, effect="normal", volume=1.0):
    """
    Text-to-speech with pre-configured effects
    
    Effects:
        - normal: Standard voice
        - fast: 50% faster
        - slow: 50% slower
        - chipmunk: High pitched
        - deep: Low pitched
        - robot: Slow and monotone
    """
    effects = {
        "normal": {"rate": "+0%", "pitch": "+0Hz", "voice": "default"},
        "fast": {"rate": "+50%", "pitch": "+0Hz", "voice": "default"},
        "slow": {"rate": "-50%", "pitch": "+0Hz", "voice": "default"},
        "chipmunk": {"rate": "+25%", "pitch": "+100Hz", "voice": "female"},
        "deep": {"rate": "-25%", "pitch": "-50Hz", "voice": "deep"},
        "robot": {"rate": "-10%", "pitch": "-10Hz", "voice": "robot"},
        "anime": {"rate": "+10%", "pitch": "+20Hz", "voice": "anime"},
    }
    
    config = effects.get(effect, effects["normal"])
    
    await text_to_speech(
        text=text,
        voice=config["voice"],
        rate=config["rate"],
        pitch=config["pitch"],
        volume=volume
    )


async def list_available_voices():
    """Print all available edge-tts voices"""
    voices = await edge_tts.list_voices()
    
    print("\n🎤 Available Voices:")
    print("-" * 60)
    
    for voice in voices:
        if voice['Locale'].startswith('en'):  # Show only English voices
            print(f"{voice['ShortName']}")
            print(f"  Gender: {voice['Gender']}")
            print(f"  Locale: {voice['Locale']}")
            print()


def cleanup_tts_cache():
    """Delete all cached TTS files"""
    try:
        for file in TEMP_DIR.glob("*.mp3"):
            file.unlink()
        print("🗑️ TTS cache cleared")
    except Exception as e:
        print(f"❌ Error cleaning TTS cache: {e}")
