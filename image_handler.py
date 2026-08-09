"""
Image/GIF display handler using OBS Browser Source
This creates temporary HTML files that OBS displays
"""
import asyncio
import html
import random
import time
import sys
import uuid
from pathlib import Path

from config import OBS_OVERLAY_SOURCE
from obs_handler import build_local_bridge_url, move_source_to_front, set_browser_source_url, toggle_source_visibility

# Position presets
POSITIONS = {
    "center": (50, 50),
    "bottom-center": (50, 84),
    "top-left": (10, 10),
    "top-right": (80, 10),
    "bottom-left": (10, 80),
    "bottom-right": (80, 80),
    "random": None  # Will be calculated randomly
}

_OVERLAY_STATE_LOCK = asyncio.Lock()
_OVERLAY_LAYERS = {}
_OVERLAY_SEQUENCE = 0


def _blank_overlay_html():
    return "<html><body style='background:transparent;'></body></html>"


def _compose_overlay_html(layers):
    if not layers:
        return _blank_overlay_html()

    style_chunks = []
    body_chunks = []
    for layer in sorted(layers, key=lambda item: item["z_index"]):
        style_chunks.append(layer["style"])
        body_chunks.append(layer["markup"])

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            html, body {{
                margin: 0;
                padding: 0;
                width: 100%;
                height: 100%;
                overflow: hidden;
                background: transparent;
            }}
            body {{
                pointer-events: none;
            }}
            {''.join(style_chunks)}
        </style>
    </head>
    <body>
        {''.join(body_chunks)}
    </body>
    </html>
    """


async def _render_overlay_layers(layers, force_top=False):
    overlay_file = Path("bridge") / "overlay_temp.html"
    overlay_file.parent.mkdir(parents=True, exist_ok=True)
    overlay_file.write_text(_compose_overlay_html(layers), encoding="utf-8")

    overlay_updated = await set_browser_source_url(OBS_OVERLAY_SOURCE, "bridge/overlay_temp.html")
    if not overlay_updated:
        return False

    if force_top:
        await move_source_to_front(OBS_OVERLAY_SOURCE)

    await toggle_source_visibility(OBS_OVERLAY_SOURCE, visible=bool(layers))
    return True


async def _add_overlay_layer(layer_id, style, markup, force_top=False, z_index=100):
    global _OVERLAY_SEQUENCE
    async with _OVERLAY_STATE_LOCK:
        _OVERLAY_SEQUENCE += 1
        _OVERLAY_LAYERS[layer_id] = {
            "style": style,
            "markup": markup,
            "z_index": z_index + _OVERLAY_SEQUENCE,
        }
        snapshot = list(_OVERLAY_LAYERS.values())

    await _render_overlay_layers(snapshot, force_top=force_top)


async def _remove_overlay_layer(layer_id):
    async with _OVERLAY_STATE_LOCK:
        _OVERLAY_LAYERS.pop(layer_id, None)
        snapshot = list(_OVERLAY_LAYERS.values())

    await _render_overlay_layers(snapshot, force_top=False)


async def show_image(image_path, duration=3, position="center", scale=1.0):
    """
    Display an image on screen using OBS browser source
    
    Args:
        image_path: Path to image file
        duration: How long to show (seconds)
        position: "center", "top-left", "top-right", "bottom-left", "bottom-right", or "random"
        scale: Size multiplier
    """
    await _show_media(image_path, duration, position, scale, is_gif=False)


async def show_image_overlay(
    image_path,
    duration=3,
    force_top=False,
    fullscreen=False,
    hold_duration=0.0,
    fade_duration=0.0,
    start_delay=0.0,
):
    """Display an image overlay with optional fullscreen and top-most behavior."""
    await _show_media(
        image_path,
        duration=duration,
        position="center",
        scale=1.0,
        is_gif=False,
        force_top=force_top,
        fullscreen=fullscreen,
        hold_duration=hold_duration,
        fade_duration=fade_duration,
        start_delay=start_delay,
    )


async def show_main_monitor_flash(duration=0.5, color="white", hold_duration=0.0, fade_duration=0.0, start_delay=0.0):
    """Flash the streamer's primary monitor using an isolated Tkinter process.

    Running Tk in a separate process avoids Tcl thread lifecycle crashes in the bot process.
    """
    total_hold = max(float(hold_duration), float(duration), 0.0)
    total_fade = max(float(fade_duration), 0.0)

    flash_script = r'''
import sys
import time

start_delay = float(sys.argv[1])
hold_duration = float(sys.argv[2])
fade_duration = float(sys.argv[3])
color = sys.argv[4]

if start_delay > 0:
    time.sleep(start_delay)

try:
    import tkinter as tk
except Exception as exc:
    print(f"Tkinter unavailable: {exc}")
    time.sleep(max(hold_duration + fade_duration, 0.0))
    raise SystemExit(0)

root = tk.Tk()
root.overrideredirect(True)
root.attributes("-topmost", True)
root.configure(bg=color)

screen_width = root.winfo_screenwidth()
screen_height = root.winfo_screenheight()
root.geometry(f"{screen_width}x{screen_height}+0+0")
root.lift()
root.focus_force()

if fade_duration <= 0:
    root.after(max(int(hold_duration * 1000), 0), root.destroy)
else:
    step_ms = 16
    fade_steps = max(int((fade_duration * 1000) / step_ms), 1)

    def begin_fade(step=0):
        alpha = max(0.0, 1.0 - (step / fade_steps))
        root.attributes("-alpha", alpha)
        if step >= fade_steps:
            root.destroy()
            return
        root.after(step_ms, lambda: begin_fade(step + 1))

    root.after(max(int(hold_duration * 1000), 0), begin_fade)

root.mainloop()
'''

    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            flash_script,
            str(float(start_delay)),
            str(total_hold),
            str(total_fade),
            str(color),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await process.wait()
    except Exception as error:
        print(f"❌ Error showing local main-monitor flash: {error}")


async def show_gif(gif_path, duration=5, position="center", scale=1.0):
    """
    Display an animated GIF on screen
    
    Args:
        gif_path: Path to GIF file
        duration: How long to show (seconds)
        position: Position preset or "random"
        scale: Size multiplier
    """
    await _show_media(gif_path, duration, position, scale, is_gif=True)


async def show_fireworks_announcement(gif_path, text, hold_duration=10.0, fade_duration=5.0, force_top=True):
    """Show a transparent fireworks GIF with centered text, then fade out."""
    try:
        media_file = Path(gif_path)
        if not media_file.exists():
            print(f"❌ Media file not found: {gif_path}")
            return

        if media_file.is_absolute():
            try:
                media_path_for_bridge = media_file.resolve().relative_to(Path(__file__).resolve().parent).as_posix()
                media_src = build_local_bridge_url(media_path_for_bridge)
            except ValueError:
                media_src = f"file:///{media_file.resolve().as_posix()}"
        else:
            media_src = build_local_bridge_url(media_file.as_posix())

        safe_text = html.escape(str(text))
        hold_seconds = max(float(hold_duration), 0.0)
        fade_seconds = max(float(fade_duration), 0.0)
        total_seconds = max(hold_seconds + fade_seconds, 0.1)
        hold_percent = int((hold_seconds / total_seconds) * 100) if total_seconds > 0 else 0
        hold_percent = max(0, min(99, hold_percent))

        layer_id = f"fireworks-{uuid.uuid4().hex}"
        fade_name = f"fadeOutAll_{layer_id}"
        pop_name = f"popIn_{layer_id}"
        fireworks_id = f"fireworks_{layer_id}"
        announcement_id = f"announcement_{layer_id}"

        layer_style = f"""
            #{fireworks_id} {{
                position: fixed;
                inset: 0;
                width: 100vw;
                height: 100vh;
                object-fit: contain;
                pointer-events: none;
                opacity: 1;
                animation: {fade_name} {total_seconds:.3f}s linear forwards;
            }}
            #{announcement_id} {{
                position: fixed;
                left: 50%;
                top: 50%;
                transform: translate(-50%, -50%);
                width: 90vw;
                max-width: 1400px;
                color: #ffffff;
                font-family: 'Arial Black', sans-serif;
                font-size: clamp(40px, 6vw, 92px);
                text-align: center;
                text-shadow:
                    0 0 12px rgba(0, 0, 0, 0.9),
                    0 0 24px rgba(0, 0, 0, 0.8),
                    4px 4px 0 #000;
                opacity: 1;
                animation: {pop_name} 0.35s ease-out, {fade_name} {total_seconds:.3f}s linear forwards;
            }}
            @keyframes {pop_name} {{
                0% {{ transform: translate(-50%, -50%) scale(0.7); opacity: 0; }}
                100% {{ transform: translate(-50%, -50%) scale(1); opacity: 1; }}
            }}
            @keyframes {fade_name} {{
                0% {{ opacity: 1; }}
                {hold_percent}% {{ opacity: 1; }}
                100% {{ opacity: 0; }}
            }}
        """
        layer_markup = f"""
            <img id=\"{fireworks_id}\" src=\"{media_src}\" alt=\"fireworks\" />
            <div id=\"{announcement_id}\">{safe_text}</div>
        """

        await _add_overlay_layer(layer_id, layer_style, layer_markup, force_top=force_top, z_index=300)
        print(f"🎆 Showing first chatter celebration for {total_seconds:.1f}s (hold={hold_seconds:.1f}s fade={fade_seconds:.1f}s)")

        await asyncio.sleep(total_seconds)
        await _remove_overlay_layer(layer_id)
    except Exception as e:
        print(f"❌ Error showing fireworks announcement: {e}")


async def _show_media(
    media_path,
    duration,
    position,
    scale,
    is_gif,
    force_top=False,
    fullscreen=False,
    hold_duration=0.0,
    fade_duration=0.0,
    start_delay=0.0,
):
    """Internal function to display media"""
    try:
        if start_delay > 0:
            await asyncio.sleep(start_delay)

        media_file = Path(media_path)
        
        if not media_file.exists():
            print(f"❌ Media file not found: {media_path}")
            return
        
        # Get position coordinates
        if position == "random":
            x = random.randint(5, 85)
            y = random.randint(5, 85)
        else:
            x, y = POSITIONS.get(position, POSITIONS["center"])
        
        effective_hold = max(float(hold_duration), 0.0)
        effective_fade = max(float(fade_duration), 0.0)
        effective_duration = max(float(duration), 0.0)

        if effective_fade <= 0 and effective_hold < effective_duration:
            effective_fade = effective_duration - effective_hold

        total_display_seconds = effective_hold + effective_fade
        if total_display_seconds <= 0:
            total_display_seconds = max(effective_duration, 0.1)

        layer_id = f"media-{uuid.uuid4().hex}"
        media_id = f"media_{layer_id}"

        if fullscreen:
            hold_percent = int((effective_hold / total_display_seconds) * 100) if total_display_seconds > 0 else 0
            hold_percent = max(0, min(99, hold_percent))
            fade_name = f"flashFade_{layer_id}"

            media_css = f"""
                #{media_id} {{
                    position: fixed;
                    inset: 0;
                    width: 100vw;
                    height: 100vh;
                    object-fit: cover;
                    opacity: 1;
                    animation: {fade_name} {total_display_seconds:.3f}s linear forwards;
                }}
                @keyframes {fade_name} {{
                    0% {{ opacity: 1; }}
                    {hold_percent}% {{ opacity: 1; }}
                    100% {{ opacity: 0; }}
                }}
            """
        else:
            fade_name = f"fadeIn_{layer_id}"
            media_css = f"""
                #{media_id} {{
                    position: absolute;
                    left: {x}vw;
                    top: {y}vh;
                    transform: translate(-50%, -50%) scale({scale});
                    max-width: 500px;
                    max-height: 500px;
                    animation: {fade_name} 0.3s ease-in;
                }}
                @keyframes {fade_name} {{
                    from {{ opacity: 0; transform: translate(-50%, -50%) scale(0) rotate(-180deg); }}
                    to {{ opacity: 1; transform: translate(-50%, -50%) scale({scale}) rotate(0deg); }}
                }}
            """

        # Create HTML file for OBS browser source
        if media_file.is_absolute():
            try:
                media_path_for_bridge = media_file.resolve().relative_to(Path(__file__).resolve().parent).as_posix()
                media_src = build_local_bridge_url(media_path_for_bridge)
            except ValueError:
                media_src = f"file:///{media_file.resolve().as_posix()}"
        else:
            media_src = build_local_bridge_url(media_file.as_posix())

        layer_markup = f"<img id=\"{media_id}\" src=\"{media_src}\" alt=\"media\" />"
        await _add_overlay_layer(layer_id, media_css, layer_markup, force_top=force_top, z_index=150)
        
        print(f"{'🎬' if is_gif else '🖼️'} Showing {media_file.name} for {duration}s at position {position}")

        await asyncio.sleep(total_display_seconds)
        await _remove_overlay_layer(layer_id)
        
    except Exception as e:
        print(f"❌ Error showing media: {e}")


async def show_text(text, duration=3, position="center", color="white", size=48, fullscreen=False, force_top=False):
    """
    Display text on screen.

    Args:
        text: Text to display
        duration: How long to show (seconds)
        position: Position preset or "random"
        color: Text color (CSS color)
        size: Font size in pixels
        fullscreen: Center the text over the whole screen instead of using the normal overlay position
        force_top: Move the overlay source to the front before showing it
    """
    try:
        if position == "random":
            x = random.randint(5, 85)
            y = random.randint(5, 85)
        else:
            x, y = POSITIONS.get(position, POSITIONS["center"])

        layer_id = f"text-{uuid.uuid4().hex}"
        text_id = f"text_{layer_id}"
        pop_name = f"bounceIn_{layer_id}"
        safe_text = html.escape(str(text))

        if fullscreen:
            text_css = f"""
                position: fixed;
                left: 50%;
                top: 50%;
                transform: translate(-50%, -50%);
                font-size: {size}px;
                color: {color};
                font-family: 'Arial Black', sans-serif;
                text-shadow: 3px 3px 6px black;
                text-align: center;
                width: 90vw;
                max-width: 1600px;
                animation: {pop_name} 0.5s ease-out;
            """
        else:
            text_css = f"""
                position: absolute;
                left: {x}vw;
                top: {y}vh;
                transform: translate(-50%, -50%);
                font-size: {size}px;
                color: {color};
                font-family: 'Arial Black', sans-serif;
                text-shadow: 3px 3px 6px black;
                animation: {pop_name} 0.5s ease-out;
            """

        layer_style = f"""
            #{text_id} {{
                {text_css}
            }}
            @keyframes {pop_name} {{
                0% {{ transform: translate(-50%, -50%) scale(0); }}
                50% {{ transform: translate(-50%, -50%) scale(1.2); }}
                100% {{ transform: translate(-50%, -50%) scale(1); }}
            }}
        """
        layer_markup = f"<div id=\"{text_id}\">{safe_text}</div>"

        await _add_overlay_layer(layer_id, layer_style, layer_markup, force_top=force_top, z_index=220)

        print(f"📝 Showing text: '{text}' for {duration}s")

        await asyncio.sleep(duration)
        await _remove_overlay_layer(layer_id)

    except Exception as e:
        print(f"❌ Error showing text: {e}")
