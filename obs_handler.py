"""
OBS Websocket Handler
Requires: pip install obs-websocket-py
"""
import asyncio
import hashlib
import json
import mimetypes
import os
import shutil
import threading
import time
import uuid
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

from obswebsocket import obsws, requests as obs_requests

try:
    import config as _config
except Exception:
    _config = None

if _config is None:
    OBS_HOST = "127.0.0.1"
    OBS_PORT = 4455
    OBS_PASSWORD = ""
    OBS_TTS_SOURCE = "Bot"
    OBS_TTS_TEXT_SOURCE = "BotText"
    OBS_OVERLAY_SOURCE = "RewardOverlay"
    AUDIO_BRIDGE_BACKEND = "local"
    HOSTED_TTS_PLAYER_URL = ""
    HOSTED_AUDIO_API_URL = ""
    HOSTED_AUDIO_API_KEY = ""
    HOSTED_STORAGE_BACKEND = "direct_url"
    HOSTED_STORAGE_PUBLIC_BASE_URL = ""
    HOSTED_S3_BUCKET = ""
    HOSTED_S3_REGION = ""
    HOSTED_S3_PREFIX = "twitchbot-audio"
    HOSTED_S3_PRESIGNED_EXPIRES_SECONDS = 3600
    TTS_TEXT_ANCHOR = "bottom"
    TTS_TEXT_OFFSET_X = 0
    TTS_TEXT_OFFSET_Y = 64
else:
    OBS_HOST = getattr(_config, "OBS_HOST", "127.0.0.1")
    OBS_PORT = getattr(_config, "OBS_PORT", 4455)
    OBS_PASSWORD = getattr(_config, "OBS_PASSWORD", "")
    OBS_TTS_SOURCE = getattr(_config, "OBS_TTS_SOURCE", "Bot")
    OBS_TTS_TEXT_SOURCE = getattr(_config, "OBS_TTS_TEXT_SOURCE", "BotText")
    OBS_OVERLAY_SOURCE = getattr(_config, "OBS_OVERLAY_SOURCE", "RewardOverlay")
    AUDIO_BRIDGE_BACKEND = getattr(_config, "AUDIO_BRIDGE_BACKEND", "local")
    HOSTED_TTS_PLAYER_URL = getattr(_config, "HOSTED_TTS_PLAYER_URL", "")
    HOSTED_AUDIO_API_URL = getattr(_config, "HOSTED_AUDIO_API_URL", "")
    HOSTED_AUDIO_API_KEY = getattr(_config, "HOSTED_AUDIO_API_KEY", "")
    HOSTED_STORAGE_BACKEND = getattr(_config, "HOSTED_STORAGE_BACKEND", "direct_url")
    HOSTED_STORAGE_PUBLIC_BASE_URL = getattr(_config, "HOSTED_STORAGE_PUBLIC_BASE_URL", "")
    HOSTED_S3_BUCKET = getattr(_config, "HOSTED_S3_BUCKET", "")
    HOSTED_S3_REGION = getattr(_config, "HOSTED_S3_REGION", "")
    HOSTED_S3_PREFIX = getattr(_config, "HOSTED_S3_PREFIX", "twitchbot-audio")
    HOSTED_S3_PRESIGNED_EXPIRES_SECONDS = int(getattr(_config, "HOSTED_S3_PRESIGNED_EXPIRES_SECONDS", 3600))
    TTS_TEXT_ANCHOR = getattr(_config, "TTS_TEXT_ANCHOR", "bottom")
    TTS_TEXT_OFFSET_X = int(getattr(_config, "TTS_TEXT_OFFSET_X", 0))
    TTS_TEXT_OFFSET_Y = int(getattr(_config, "TTS_TEXT_OFFSET_Y", 64))

BOT_ROOT = Path(__file__).resolve().parent
AUDIO_BRIDGE_PORT = int(os.getenv("TTS_BROWSER_PORT", "8765"))
BRIDGE_DIR = BOT_ROOT / "bridge"
TEMP_TTS_DIR = BOT_ROOT / "temp_tts"
AUDIO_BRIDGE_HTML = BRIDGE_DIR / "tts_audio_bridge.html"
TTS_TEXT_BRIDGE_HTML = BRIDGE_DIR / "tts_text_bridge.html"
AUDIO_BRIDGE_STATE = BRIDGE_DIR / "tts_audio_state.json"
LAYOUT_SETTINGS_FILE = BRIDGE_DIR / "layout_settings.json"
LAYOUT_EDITOR_HTML = BRIDGE_DIR / "layout_editor.html"
OVERLAY_TEMP_HTML = BRIDGE_DIR / "overlay_temp.html"

# Global OBS connection
_obs_client = None
_audio_bridge_server = None
_audio_bridge_thread = None
_audio_bridge_state_lock = threading.Lock()
_layout_settings_lock = threading.Lock()
AUDIO_BRIDGE_SESSION_ID = uuid.uuid4().hex


def _is_hosted_backend_enabled():
    return str(AUDIO_BRIDGE_BACKEND).strip().lower() == "hosted"


def _hosted_events_endpoint():
    base = str(HOSTED_AUDIO_API_URL or "").strip()
    if not base:
        return ""
    if base.endswith("/api/events"):
        return base
    return f"{base.rstrip('/')}/api/events"


def _resolve_audio_path_for_upload(audio_path):
    requested_path = Path(audio_path)
    resolved_path = requested_path if requested_path.is_absolute() else (BOT_ROOT / requested_path)
    resolved_path = resolved_path.resolve()
    if not resolved_path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")
    return resolved_path


def _build_direct_public_audio_url(local_audio_path):
    base_url = str(HOSTED_STORAGE_PUBLIC_BASE_URL or "").strip().rstrip("/")
    if not base_url:
        raise RuntimeError("HOSTED_STORAGE_PUBLIC_BASE_URL is required for direct_url storage backend")

    resolved_path = _resolve_audio_path_for_upload(local_audio_path)
    try:
        relative = resolved_path.relative_to(BOT_ROOT).as_posix()
    except ValueError as error:
        raise RuntimeError("direct_url backend requires audio files inside the bot folder") from error

    encoded = "/".join(quote(part) for part in relative.split("/"))
    return f"{base_url}/{encoded}"


def _upload_to_s3_and_get_url(local_audio_path):
    if not HOSTED_S3_BUCKET:
        raise RuntimeError("HOSTED_S3_BUCKET is required for s3 storage backend")

    try:
        import boto3
    except Exception as error:
        raise RuntimeError("boto3 is required for s3 hosted storage. Run: pip install boto3") from error

    resolved_path = _resolve_audio_path_for_upload(local_audio_path)
    content_type = mimetypes.guess_type(str(resolved_path))[0] or "application/octet-stream"

    region = str(HOSTED_S3_REGION or "").strip() or None
    client_kwargs = {"region_name": region} if region else {}
    s3 = boto3.client("s3", **client_kwargs)

    file_hash = hashlib.md5(str(resolved_path).encode("utf-8")).hexdigest()[:12]
    key_prefix = str(HOSTED_S3_PREFIX or "twitchbot-audio").strip().strip("/")
    key = f"{key_prefix}/{file_hash}/{int(time.time() * 1000)}_{resolved_path.name}"

    s3.upload_file(
        str(resolved_path),
        HOSTED_S3_BUCKET,
        key,
        ExtraArgs={"ContentType": content_type},
    )

    expires = max(int(HOSTED_S3_PRESIGNED_EXPIRES_SECONDS), 0)
    if expires > 0:
        return s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": HOSTED_S3_BUCKET, "Key": key},
            ExpiresIn=expires,
        )

    if region:
        return f"https://{HOSTED_S3_BUCKET}.s3.{region}.amazonaws.com/{quote(key)}"
    return f"https://{HOSTED_S3_BUCKET}.s3.amazonaws.com/{quote(key)}"


def _build_hosted_audio_url(local_audio_path):
    storage_backend = str(HOSTED_STORAGE_BACKEND or "direct_url").strip().lower()
    if storage_backend == "s3":
        return _upload_to_s3_and_get_url(local_audio_path)
    if storage_backend == "direct_url":
        return _build_direct_public_audio_url(local_audio_path)
    raise RuntimeError(f"Unsupported HOSTED_STORAGE_BACKEND: {HOSTED_STORAGE_BACKEND}")


def _post_hosted_event(payload):
    endpoint = _hosted_events_endpoint()
    if not endpoint:
        raise RuntimeError("HOSTED_AUDIO_API_URL is required when AUDIO_BRIDGE_BACKEND='hosted'")

    body = json.dumps(payload).encode("utf-8")
    request = Request(endpoint, data=body, method="POST")
    request.add_header("Content-Type", "application/json")
    if HOSTED_AUDIO_API_KEY:
        request.add_header("X-API-Key", str(HOSTED_AUDIO_API_KEY))

    try:
        with urlopen(request, timeout=10) as response:
            response.read()
    except HTTPError as error:
        raise RuntimeError(f"Hosted event POST failed: HTTP {error.code}") from error
    except URLError as error:
        raise RuntimeError(f"Hosted event POST failed: {error}") from error


class SilentHTTPRequestHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def end_headers(self):
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def _send_json(self, payload, status_code=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/layout":
            self._send_json(_read_layout_settings())
            return

        if parsed.path == "/api/layout-editor-url":
            self._send_json({"url": get_layout_editor_url()})
            return

        super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != "/api/layout":
            self.send_error(404, "Unknown API route")
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(content_length) if content_length > 0 else b"{}"
            payload = json.loads(raw_body.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("Payload must be a JSON object")
        except Exception as error:
            self._send_json({"ok": False, "error": str(error)}, status_code=400)
            return

        saved = _write_layout_settings(payload)
        self._send_json({"ok": True, "layout": saved})


def _default_layout_settings():
        return {
                "tts": {
                        "anchor": "bottom",
                        "offset_x": 0,
                        "offset_y": 64,
                        "max_width_percent": 95,
                        "box_width_px": 1280,
                        "box_height_px": 720,
                        "edge_padding_px": 48,
                        "font_size": 44,
                        "line_height": 1.28,
                        "padding_x": 24,
                        "padding_y": 18,
                        "border_radius": 18,
                        "bg_opacity": 0.72,
                        "speaker_color": "#9146FF",
                }
        }


def _deep_merge(base, override):
        merged = dict(base)
        for key, value in override.items():
                if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
                        merged[key] = _deep_merge(merged[key], value)
                else:
                        merged[key] = value
        return merged


def _read_layout_settings():
        defaults = _default_layout_settings()
        if not LAYOUT_SETTINGS_FILE.exists():
                return defaults

        try:
                data = json.loads(LAYOUT_SETTINGS_FILE.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                        return defaults
                return _deep_merge(defaults, data)
        except Exception:
                return defaults


def _write_layout_settings(new_settings):
        with _layout_settings_lock:
                merged = _deep_merge(_default_layout_settings(), new_settings)
                LAYOUT_SETTINGS_FILE.write_text(json.dumps(merged, indent=2), encoding="utf-8")
        return merged


def _normalize_tts_anchor(value):
        anchor = str(value or "").strip().lower()
        aliases = {
                "top": "top",
                "top-center": "top",
                "center": "center",
                "middle": "center",
                "bottom": "bottom",
                "bottom-center": "bottom",
        }
        return aliases.get(anchor, "bottom")


def _apply_tts_caption_position_from_config():
        """Seed caption layout with config-driven anchor/offset defaults."""
        _ensure_audio_bridge_assets()
        anchor = _normalize_tts_anchor(TTS_TEXT_ANCHOR)

        try:
                offset_x = int(TTS_TEXT_OFFSET_X)
        except Exception:
                offset_x = 0

        try:
                offset_y = int(TTS_TEXT_OFFSET_Y)
        except Exception:
                offset_y = 64 if anchor in {"top", "bottom"} else 0

        # Keep any user-tuned style values and only override placement fields.
        _write_layout_settings({
                "tts": {
                        "anchor": anchor,
                        "offset_x": offset_x,
                        "offset_y": offset_y,
                }
        })


def _write_layout_editor_html():
        LAYOUT_EDITOR_HTML.write_text(
                """<!doctype html>
<html>
<head>
    <meta charset=\"utf-8\" />
    <title>OBS Overlay Layout Editor</title>
    <style>
        :root {
            --bg: #10131a;
            --panel: #1a1f2b;
            --line: #2b3345;
            --text: #f2f5ff;
            --muted: #a7b2cc;
            --accent: #4cd2ff;
        }
        body {
            margin: 0;
            font: 15px/1.4 Segoe UI, sans-serif;
            color: var(--text);
            background: radial-gradient(circle at 20% 10%, #202944, var(--bg));
            display: grid;
            grid-template-columns: 360px 1fr;
            min-height: 100vh;
        }
        .panel {
            background: rgba(15, 18, 25, 0.92);
            border-right: 1px solid var(--line);
            padding: 18px;
            overflow: auto;
        }
        .preview-wrap {
            display: grid;
            place-items: center;
            padding: 22px;
        }
        .preview {
            width: min(96vw, 1280px);
            aspect-ratio: 16 / 9;
            background:
                linear-gradient(0deg, rgba(255,255,255,0.03) 1px, transparent 1px),
                linear-gradient(90deg, rgba(255,255,255,0.03) 1px, transparent 1px),
                #0f131d;
            background-size: 40px 40px;
            border: 1px solid #2e3850;
            border-radius: 12px;
            position: relative;
            overflow: hidden;
        }
        .caption {
            position: absolute;
            left: 50%;
            bottom: 64px;
            transform: translateX(-50%);
            width: 70%;
            max-width: 95%;
            color: #fff;
            background: rgba(0, 0, 0, 0.72);
            border-radius: 18px;
            padding: 18px 24px;
            cursor: grab;
            user-select: none;
            box-shadow: 0 10px 30px rgba(0,0,0,0.35);
            text-shadow: -2px -2px 0 #000, 2px 2px 0 #000, 0 4px 10px rgba(0,0,0,.8);
        }
        .caption:active { cursor: grabbing; }
        .speaker { color: #9146FF; font-weight: 900; display: block; margin-bottom: 6px; }
        h1 { font-size: 18px; margin: 0 0 12px 0; }
        h2 { font-size: 14px; margin: 16px 0 8px 0; color: var(--muted); }
        label { display: block; margin: 10px 0 4px 0; color: var(--muted); }
        input, select {
            width: 100%;
            box-sizing: border-box;
            padding: 8px;
            border-radius: 8px;
            border: 1px solid #36445f;
            background: #131a27;
            color: var(--text);
        }
        .row { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
        .actions { display: flex; gap: 8px; margin-top: 16px; }
        button {
            border: none;
            border-radius: 8px;
            padding: 10px 12px;
            color: #02131a;
            background: var(--accent);
            font-weight: 700;
            cursor: pointer;
        }
        button.secondary { background: #9eb0d5; }
        .hint { color: var(--muted); font-size: 12px; margin-top: 8px; }
    </style>
</head>
<body>
    <aside class=\"panel\">
        <h1>Overlay Layout Editor</h1>
        <div class=\"hint\">Drag caption in preview to set offsets. Click Save to apply to TTS browser source.</div>

        <h2>Caption Layout</h2>
        <label for=\"anchor\">Anchor</label>
        <select id=\"anchor\">
            <option value=\"bottom\">Bottom</option>
            <option value=\"center\">Center</option>
            <option value=\"top\">Top</option>
        </select>

        <div class="row">
            <div>
                <label for="offsetX">Offset X (px)</label>
                <input id="offsetX" type="number" />
            </div>
            <div>
                <label for="offsetY">Offset Y (px)</label>
                <input id="offsetY" type="number" />
            </div>
        </div>

        <div class="row">
            <div>
                <label for="fontSize">Font Size</label>
                <input id="fontSize" type="number" min="16" max="120" />
            </div>
            <div>
                <label for="maxWidth">Max Width %</label>
                <input id="maxWidth" type="number" min="30" max="100" />
            </div>
        </div>

        <div class="row">
            <div>
                <label for="boxWidth">Box Width (px)</label>
                <input id="boxWidth" type="number" min="320" max="3840" />
            </div>
            <div>
                <label for="boxHeight">Box Height (px)</label>
                <input id="boxHeight" type="number" min="120" max="2160" />
            </div>
        </div>

        <label for="edgePadding">Edge Padding (px)</label>
        <input id="edgePadding" type="number" min="0" max="400" />

        <div class="row">
            <div>
                <label for="padY">Padding Y</label>
                <input id="padY" type="number" min="0" max="80" />
            </div>
            <div>
                <label for="padX">Padding X</label>
                <input id="padX" type="number" min="0" max="120" />
            </div>
        </div>

        <label for=\"radius\">Border Radius</label>
        <input id=\"radius\" type=\"number\" min=\"0\" max=\"80\" />

        <label for=\"opacity\">Background Opacity (0-1)</label>
        <input id=\"opacity\" type=\"number\" min=\"0\" max=\"1\" step=\"0.01\" />

        <label for=\"speakerColor\">Speaker Color (hex)</label>
        <input id=\"speakerColor\" type=\"text\" />

        <div class=\"actions\">
            <button id=\"save\">Save Layout</button>
            <button id=\"reset\" class=\"secondary\">Reload</button>
        </div>
        <div id=\"status\" class=\"hint\"></div>
    </aside>

    <main class=\"preview-wrap\">
        <div class=\"preview\" id=\"preview\">
            <div class=\"caption\" id=\"caption\">
                <span class=\"speaker\" id=\"speaker\">viewer123</span>
                This is a caption preview for TTS browser source placement.
            </div>
        </div>
    </main>

    <script>
        const preview = document.getElementById('preview');
        const caption = document.getElementById('caption');
        const status = document.getElementById('status');
        const ids = ['anchor', 'offsetX', 'offsetY', 'fontSize', 'maxWidth', 'boxWidth', 'boxHeight', 'edgePadding', 'padY', 'padX', 'radius', 'opacity', 'speakerColor'];
        const fields = Object.fromEntries(ids.map((id) => [id, document.getElementById(id)]));

        let layout = null;

        function toNumber(value, fallback = 0) {
            const num = Number(value);
            return Number.isFinite(num) ? num : fallback;
        }

        function applyLayoutToPreview() {
            if (!layout) return;
            const t = layout.tts;
            const boxWidth = Math.max(toNumber(t.box_width_px, 1280), 320);
            const boxHeight = Math.max(toNumber(t.box_height_px, 720), 120);
            const edgePadding = Math.max(toNumber(t.edge_padding_px, 48), 0);
            const previewWidth = Math.max(preview.clientWidth - (edgePadding * 2), 120);
            const previewHeight = Math.max(preview.clientHeight - (edgePadding * 2), 120);
            const widthByPercent = previewWidth * ((t.max_width_percent ?? 95) / 100);
            const effectiveWidth = Math.min(Math.max(Math.min(boxWidth, widthByPercent), 120), previewWidth);
            const effectiveHeight = Math.min(Math.max(boxHeight, 120), previewHeight);

            caption.style.fontSize = `${t.font_size}px`;
            caption.style.lineHeight = String(t.line_height || 1.28);
            caption.style.maxWidth = `${effectiveWidth}px`;
            caption.style.width = `${effectiveWidth}px`;
            caption.style.maxHeight = `${effectiveHeight}px`;
            caption.style.padding = `${t.padding_y}px ${t.padding_x}px`;
            caption.style.borderRadius = `${t.border_radius}px`;
            caption.style.background = `rgba(0,0,0,${t.bg_opacity})`;
            document.getElementById('speaker').style.color = t.speaker_color;
            document.getElementById('speaker').style.textAlign = 'center';

            caption.style.left = '50%';
            caption.style.top = 'auto';
            caption.style.bottom = 'auto';

            if (t.anchor === 'top') {
                caption.style.top = `${t.offset_y}px`;
                caption.style.transform = `translate(calc(-50% + ${t.offset_x}px), 0)`;
            } else if (t.anchor === 'center') {
                caption.style.top = '50%';
                caption.style.transform = `translate(calc(-50% + ${t.offset_x}px), calc(-50% + ${t.offset_y}px))`;
            } else {
                caption.style.bottom = `${t.offset_y}px`;
                caption.style.transform = `translate(calc(-50% + ${t.offset_x}px), 0)`;
            }

            fields.anchor.value = t.anchor;
            fields.offsetX.value = t.offset_x;
            fields.offsetY.value = t.offset_y;
            fields.fontSize.value = t.font_size;
            fields.maxWidth.value = t.max_width_percent;
            fields.boxWidth.value = boxWidth;
            fields.boxHeight.value = boxHeight;
            fields.edgePadding.value = edgePadding;
            fields.padY.value = t.padding_y;
            fields.padX.value = t.padding_x;
            fields.radius.value = t.border_radius;
            fields.opacity.value = t.bg_opacity;
            fields.speakerColor.value = t.speaker_color;
        }

        function readLayoutFromInputs() {
            layout.tts.anchor = fields.anchor.value;
            layout.tts.offset_x = toNumber(fields.offsetX.value, 0);
            layout.tts.offset_y = toNumber(fields.offsetY.value, 64);
            layout.tts.font_size = toNumber(fields.fontSize.value, 44);
            layout.tts.max_width_percent = toNumber(fields.maxWidth.value, 95);
            layout.tts.box_width_px = Math.max(toNumber(fields.boxWidth.value, 1280), 320);
            layout.tts.box_height_px = Math.max(toNumber(fields.boxHeight.value, 720), 120);
            layout.tts.edge_padding_px = Math.max(toNumber(fields.edgePadding.value, 48), 0);
            layout.tts.padding_y = toNumber(fields.padY.value, 18);
            layout.tts.padding_x = toNumber(fields.padX.value, 24);
            layout.tts.border_radius = toNumber(fields.radius.value, 18);
            layout.tts.bg_opacity = toNumber(fields.opacity.value, 0.72);
            layout.tts.speaker_color = fields.speakerColor.value || '#9146FF';
        }

        async function loadLayout() {
            const response = await fetch('/api/layout', { cache: 'no-store' });
            layout = await response.json();
            applyLayoutToPreview();
        }

        async function saveLayout() {
            readLayoutFromInputs();
            const response = await fetch('/api/layout', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(layout),
            });
            const result = await response.json();
            if (!response.ok || !result.ok) {
                status.textContent = `Save failed: ${result.error || response.statusText}`;
                return;
            }
            layout = result.layout;
            applyLayoutToPreview();
            status.textContent = 'Saved. TTS browser source updates on next poll.';
        }

        ids.forEach((id) => {
            fields[id].addEventListener('input', () => {
                if (!layout) return;
                readLayoutFromInputs();
                applyLayoutToPreview();
            });
        });

        document.getElementById('save').addEventListener('click', saveLayout);
        document.getElementById('reset').addEventListener('click', loadLayout);

        let drag = null;
        caption.addEventListener('mousedown', (event) => {
            if (!layout) return;
            drag = {
                startX: event.clientX,
                startY: event.clientY,
                startOffsetX: layout.tts.offset_x,
                startOffsetY: layout.tts.offset_y,
            };
            event.preventDefault();
        });

        window.addEventListener('mousemove', (event) => {
            if (!drag || !layout) return;
            const rect = preview.getBoundingClientRect();
            const dx = event.clientX - drag.startX;
            const dy = event.clientY - drag.startY;

            const scaleX = 1920 / rect.width;
            const scaleY = 1080 / rect.height;

            layout.tts.offset_x = Math.round(drag.startOffsetX + dx * scaleX);
            layout.tts.offset_y = Math.round(drag.startOffsetY + dy * scaleY);
            applyLayoutToPreview();
        });

        window.addEventListener('mouseup', () => {
            drag = null;
        });

        loadLayout().catch((error) => {
            status.textContent = `Failed to load layout: ${error}`;
        });
    </script>
</body>
</html>
""",
                encoding="utf-8",
        )



def _sync_bridge_session_id_into_html():
    """Keep OBS bridge pages on the current process session id.

    Without this, caption/audio events are dropped because the HTML was written
    with a placeholder or a session id from a previous bot run.
    """
    import re

    pattern = re.compile(r"const expectedSessionId = '[^']*';")
    replacement = f"const expectedSessionId = '{AUDIO_BRIDGE_SESSION_ID}';"

    for bridge_path in (AUDIO_BRIDGE_HTML, TTS_TEXT_BRIDGE_HTML):
        if not bridge_path.exists():
            continue
        try:
            content = bridge_path.read_text(encoding="utf-8")
        except OSError as error:
            print(f"⚠️ Could not read bridge page {bridge_path.name}: {error}")
            continue

        updated = content.replace("{AUDIO_BRIDGE_SESSION_ID}", AUDIO_BRIDGE_SESSION_ID)
        updated = pattern.sub(replacement, updated)
        if updated != content:
            try:
                bridge_path.write_text(updated, encoding="utf-8")
            except OSError as error:
                print(f"⚠️ Could not update session id in {bridge_path.name}: {error}")


def _ensure_audio_bridge_assets():
    """Create the local HTML bridge files used by the OBS browser source."""
    BRIDGE_DIR.mkdir(parents=True, exist_ok=True)
    TEMP_TTS_DIR.mkdir(parents=True, exist_ok=True)

    # Preserve locally customized bridge pages if they already exist.
    if AUDIO_BRIDGE_HTML.exists() and TTS_TEXT_BRIDGE_HTML.exists():
        if not AUDIO_BRIDGE_STATE.exists():
            AUDIO_BRIDGE_STATE.write_text(
                json.dumps({"events": []}, indent=2),
                encoding="utf-8",
            )

        if not LAYOUT_SETTINGS_FILE.exists():
            _write_layout_settings(_default_layout_settings())

        if not OVERLAY_TEMP_HTML.exists():
            OVERLAY_TEMP_HTML.write_text("<html><body style='background:transparent;'></body></html>", encoding="utf-8")

        _write_layout_editor_html()
        _sync_bridge_session_id_into_html()
        return

    AUDIO_BRIDGE_HTML.write_text(
        """<!doctype html>
<html>
<head>
    <meta charset="utf-8" />
    <style>
        html, body {
            margin: 0;
            padding: 0;
            width: 100%;
            height: 100%;
            background: transparent;
            overflow: hidden;
        }
        body {
            display: flex;
            align-items: flex-end;
            justify-content: center;
            padding: 0 48px 64px;
            box-sizing: border-box;
        }
        audio {
            display: none;
        }
        #caption {
            position: absolute;
            max-width: 95%;
            width: 1280px;
            max-height: 720px;
            box-sizing: border-box;
            padding: 18px 24px;
            border-radius: 18px;
            background: rgba(0, 0, 0, 0.72);
            color: #fff;
            font: 800 44px/1.28 Arial, sans-serif;
            text-align: left;
            text-shadow:
                -2px -2px 0 #000,
                0 -2px 0 #000,
                2px -2px 0 #000,
                -2px 0 0 #000,
                2px 0 0 #000,
                -2px 2px 0 #000,
                0 2px 0 #000,
                2px 2px 0 #000,
                0 4px 10px rgba(0, 0, 0, 0.8);
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.35);
            opacity: 0;
            transform: translateY(12px);
            transition: opacity 220ms ease, transform 220ms ease;
            white-space: pre-wrap;
            overflow-wrap: anywhere;
            word-break: normal;
        }
        #caption.visible {
            opacity: 1;
            transform: translateY(0);
        }
        #speakerLine {
            display: block;
            margin-bottom: 8px;
            color: #9146FF;
            font-weight: 900;
            font-size: 0.95em;
            line-height: 1.1;
            text-align: center;
        }
        #messageLine {
            display: block;
            color: #ffffff;
            font-size: 1em;
            line-height: 1.25;
        }
    </style>
</head>
<body>
    <div id="caption"></div>
    <script>
        const caption = document.getElementById('caption');
        const seenTokens = new Set();
        const expectedSessionId = '{AUDIO_BRIDGE_SESSION_ID}';
        const pageLoadMs = Date.now();
        const ttsQueue = [];
        let ttsActive = false;
        let captionClearTimer = null;
        let currentLayoutToken = '';

        function getDefaultLayout() {
            return {
                tts: {
                    anchor: 'bottom',
                    offset_x: 0,
                    offset_y: 64,
                    max_width_percent: 95,
                    box_width_px: 1280,
                    box_height_px: 720,
                    edge_padding_px: 48,
                    font_size: 44,
                    line_height: 1.28,
                    padding_x: 24,
                    padding_y: 18,
                    border_radius: 18,
                    bg_opacity: 0.72,
                    speaker_color: '#9146FF'
                }
            };
        }

        let layoutSettings = getDefaultLayout();

        function sleep(ms) {
            return new Promise((resolve) => setTimeout(resolve, ms));
        }

        function buildAudioUrl(relativeFile, token) {
            const sanitized = String(relativeFile || '').replace(/^\\/+/, '');
            const encodedFile = sanitized.split('/').map((part) => encodeURIComponent(part)).join('/');
            return '/' + encodedFile + '?v=' + encodeURIComponent(token || Date.now());
        }

        function clamp(value, min, max) {
            return Math.min(Math.max(value, min), max);
        }

        function showCaption(event) {
            const speaker = event.speaker || '';
            const messageText = event.text || event.caption || '';

            if (!event.caption && !speaker && !messageText) {
                return;
            }

            if (captionClearTimer) {
                clearTimeout(captionClearTimer);
                captionClearTimer = null;
            }

            caption.innerHTML = speaker
                ? `<span id="speakerLine">${escapeHtml(speaker)}</span><span id="messageLine">${escapeHtml(messageText)}</span>`
                : `<span id="messageLine">${escapeHtml(messageText)}</span>`;
            caption.classList.add('visible');
            applyLayoutSettings();
        }

        function applyLayoutSettings() {
            const t = (layoutSettings && layoutSettings.tts) ? layoutSettings.tts : getDefaultLayout().tts;
            const anchor = t.anchor || 'bottom';
            const maxWidthPercent = clamp(Number(t.max_width_percent ?? 95), 30, 100);
            const boxWidth = Math.max(Number(t.box_width_px ?? 1280), 320);
            const boxHeight = Math.max(Number(t.box_height_px ?? 720), 120);
            const edgePadding = Math.max(Number(t.edge_padding_px ?? 48), 0);
            const rawOffsetX = Number.isFinite(Number(t.offset_x)) ? Number(t.offset_x) : 0;
            const rawOffsetY = Number.isFinite(Number(t.offset_y)) ? Number(t.offset_y) : 64;
            const usableWidth = Math.max(window.innerWidth - (edgePadding * 2), 120);
            const usableHeight = Math.max(window.innerHeight - (edgePadding * 2), 120);
            const widthByPercent = usableWidth * (maxWidthPercent / 100);
            const effectiveWidth = clamp(Math.min(boxWidth, widthByPercent), 120, usableWidth);
            const effectiveHeight = clamp(boxHeight, 120, usableHeight);
            const safeHalfRange = Math.max((usableWidth - effectiveWidth) / 2, 0);
            const safeOffsetX = clamp(rawOffsetX, -safeHalfRange, safeHalfRange);

            caption.style.position = 'absolute';
            caption.style.maxWidth = `${effectiveWidth}px`;
            caption.style.width = `${effectiveWidth}px`;
            caption.style.maxHeight = `${effectiveHeight}px`;
            caption.style.overflowWrap = 'anywhere';
            caption.style.wordBreak = 'normal';
            caption.style.padding = `${t.padding_y ?? 18}px ${t.padding_x ?? 24}px`;
            caption.style.borderRadius = `${t.border_radius ?? 18}px`;
            caption.style.fontSize = `${t.font_size ?? 44}px`;
            caption.style.lineHeight = `${t.line_height ?? 1.28}`;
            caption.style.background = `rgba(0, 0, 0, ${t.bg_opacity ?? 0.72})`;

            const speakerLine = document.getElementById('speakerLine');
            if (speakerLine) {
                speakerLine.style.color = t.speaker_color || '#9146FF';
                speakerLine.style.textAlign = 'center';
            }

            caption.style.left = `calc(50% + ${safeOffsetX}px)`;
            caption.style.top = '';
            caption.style.bottom = '';
            caption.style.transform = '';

            if (anchor === 'top') {
                const safeTop = clamp(rawOffsetY, edgePadding, Math.max(window.innerHeight - effectiveHeight - edgePadding, edgePadding));
                caption.style.top = `${safeTop}px`;
                caption.style.transform = 'translateX(-50%)';
                return;
            }

            if (anchor === 'center') {
                const safeCenterRange = Math.max((usableHeight - effectiveHeight) / 2, 0);
                const safeCenterY = clamp(rawOffsetY, -safeCenterRange, safeCenterRange);
                caption.style.top = `calc(50% + ${safeCenterY}px)`;
                caption.style.transform = 'translate(-50%, -50%)';
                return;
            }

            const safeBottom = clamp(rawOffsetY, edgePadding, Math.max(window.innerHeight - effectiveHeight - edgePadding, edgePadding));
            caption.style.bottom = `${safeBottom}px`;
            caption.style.transform = 'translateX(-50%)';
        }

        async function pollLayoutSettings() {
            try {
                const response = await fetch('/api/layout?ts=' + Date.now(), { cache: 'no-store' });
                if (!response.ok) {
                    return;
                }

                const rawText = await response.text();
                const layoutToken = `${rawText.length}:${rawText.slice(0, 120)}`;
                if (layoutToken === currentLayoutToken) {
                    return;
                }

                currentLayoutToken = layoutToken;
                const parsed = JSON.parse(rawText);
                if (parsed && typeof parsed === 'object') {
                    layoutSettings = parsed;
                    applyLayoutSettings();
                }
            } catch (error) {
                // Ignore layout polling errors and keep current settings.
            }
        }

        function hideCaption(delayMs = 260) {
            caption.classList.remove('visible');
            if (captionClearTimer) {
                clearTimeout(captionClearTimer);
            }
            captionClearTimer = setTimeout(() => {
                if (!caption.classList.contains('visible')) {
                    caption.innerHTML = '';
                }
            }, delayMs);
        }

        async function playTtsQueue() {
            if (ttsActive) {
                return;
            }

            ttsActive = true;
            while (ttsQueue.length > 0) {
                const event = ttsQueue.shift();
                if (!event || !event.file) {
                    continue;
                }

                showCaption(event);

                const audio = new Audio(buildAudioUrl(event.file, event.token));
                audio.volume = typeof event.volume === 'number' ? event.volume : 1.0;

                await new Promise((resolve) => {
                    let finished = false;
                    const finish = () => {
                        if (finished) {
                            return;
                        }
                        finished = true;
                        resolve();
                    };

                    audio.onended = finish;
                    audio.onerror = finish;
                    audio.onabort = finish;
                    audio.play().catch(finish);
                });

                hideCaption();
                await sleep(5000);
            }

            ttsActive = false;
        }

        function playSfx(event) {
            if (!event || !event.file) {
                return;
            }

            const audio = new Audio(buildAudioUrl(event.file, event.token));
            audio.volume = typeof event.volume === 'number' ? event.volume : 1.0;
            audio.play().catch(() => {
                // Keep polling if browser autoplay policy rejects this instance.
            });
        }

        function handleEvent(event) {
            if (!event || !event.token) {
                return;
            }

            const createdAt = Number(event.created_at || 0);

            const sessionOk = !expectedSessionId
                || expectedSessionId.indexOf('{') !== -1
                || !event.session_id
                || event.session_id === expectedSessionId;
            if (event.session_id && !sessionOk) {
                return;
            }
            if (!event.session_id) {
                // Legacy events without session IDs should only pass if they were created right now.
                if (!createdAt || createdAt < (pageLoadMs - 2000)) {
                    return;
                }
            }

            if (event.kind === 'caption' && !event.file) {
                showCaption(event);
                return;
            }

            if (!event.file) {
                return;
            }

            if ((event.mode || 'sfx') === 'tts') {
                ttsQueue.push(event);
                playTtsQueue();
                return;
            }

            playSfx(event);
        }

        async function pollAudioState() {
            try {
                const response = await fetch('/bridge/tts_audio_state.json?ts=' + Date.now(), { cache: 'no-store' });
                if (!response.ok) return;

                const state = await response.json();
                const events = Array.isArray(state.events) ? state.events : [];

                for (const event of events) {
                    if (!event || !event.token || seenTokens.has(event.token)) {
                        continue;
                    }
                    seenTokens.add(event.token);
                    handleEvent(event);
                }

                if (seenTokens.size > 5000) {
                    seenTokens.clear();
                }
            } catch (error) {
                // Swallow polling errors while the bot is idle.
            }
        }

        function escapeHtml(value) {
            return String(value)
                .replaceAll('&', '&amp;')
                .replaceAll('<', '&lt;')
                .replaceAll('>', '&gt;')
                .replaceAll('"', '&quot;')
                .replaceAll("'", '&#39;');
        }

        setInterval(pollAudioState, 150);
        setInterval(pollLayoutSettings, 1000);
        applyLayoutSettings();
        pollLayoutSettings();
        pollAudioState();
    </script>
</body>
</html>
""",
        encoding="utf-8",
    )

    TTS_TEXT_BRIDGE_HTML.write_text(
        """<!doctype html>
<html>
<head>
    <meta charset="utf-8" />
    <style>
        html, body {
            margin: 0;
            padding: 0;
            width: 100%;
            height: 100%;
            background: transparent;
            overflow: hidden;
        }
        body {
            display: grid;
            place-items: center;
            box-sizing: border-box;
            padding: 24px;
        }
        #caption {
            max-width: 95%;
            width: 100%;
            box-sizing: border-box;
            padding: 18px 24px;
            border-radius: 18px;
            background: rgba(0, 0, 0, 0.72);
            color: #fff;
            font: 800 44px/1.28 Arial, sans-serif;
            text-align: center;
            text-shadow:
                -2px -2px 0 #000,
                0 -2px 0 #000,
                2px -2px 0 #000,
                -2px 0 0 #000,
                2px 0 0 #000,
                -2px 2px 0 #000,
                0 2px 0 #000,
                2px 2px 0 #000,
                0 4px 10px rgba(0, 0, 0, 0.8);
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.35);
            opacity: 0;
            transform: scale(0.98);
            transition: opacity 220ms ease, transform 220ms ease;
            white-space: pre-wrap;
            overflow-wrap: anywhere;
            word-break: normal;
        }
        #caption.visible {
            opacity: 1;
            transform: scale(1);
        }
        #speakerLine {
            display: block;
            margin-bottom: 8px;
            color: #9146FF;
            font-weight: 900;
            font-size: 0.95em;
            line-height: 1.1;
            text-align: center;
        }
        #messageLine {
            display: block;
            color: #ffffff;
            font-size: 1em;
            line-height: 1.25;
            text-align: center;
        }
    </style>
</head>
<body>
    <div id="caption"></div>
    <script>
        const caption = document.getElementById('caption');
        const seenTokens = new Set();
        const expectedSessionId = '{AUDIO_BRIDGE_SESSION_ID}';
        const pageLoadMs = Date.now();
        let captionClearTimer = null;

        function showCaption(event) {
            const speaker = event.speaker || '';
            const messageText = event.text || event.caption || '';

            if (!event.caption && !speaker && !messageText) {
                return;
            }

            if (captionClearTimer) {
                clearTimeout(captionClearTimer);
                captionClearTimer = null;
            }

            caption.innerHTML = speaker
                ? `<span id="speakerLine">${escapeHtml(speaker)}</span><span id="messageLine">${escapeHtml(messageText)}</span>`
                : `<span id="messageLine">${escapeHtml(messageText)}</span>`;

            caption.classList.add('visible');
            hideCaptionLater(5000);
        }

        function hideCaptionLater(delayMs) {
            if (captionClearTimer) {
                clearTimeout(captionClearTimer);
            }

            captionClearTimer = setTimeout(() => {
                caption.classList.remove('visible');
                setTimeout(() => {
                    if (!caption.classList.contains('visible')) {
                        caption.innerHTML = '';
                    }
                }, 260);
            }, delayMs);
        }

        function handleEvent(event) {
            if (!event || !event.token) {
                return;
            }

            const createdAt = Number(event.created_at || 0);
            const sessionOk = !expectedSessionId
                || expectedSessionId.indexOf('{') !== -1
                || !event.session_id
                || event.session_id === expectedSessionId;
            if (event.session_id && !sessionOk) {
                return;
            }
            if (!event.session_id) {
                if (!createdAt || createdAt < (pageLoadMs - 2000)) {
                    return;
                }
            }

            // Text-only source: render captions for explicit caption events
            // and also for TTS audio events that carry text metadata.
            if (event.kind === 'caption' || (event.mode || '') === 'tts') {
                showCaption(event);
            }
        }

        async function pollAudioState() {
            try {
                const response = await fetch('/bridge/tts_audio_state.json?ts=' + Date.now(), { cache: 'no-store' });
                if (!response.ok) return;

                const state = await response.json();
                const events = Array.isArray(state.events) ? state.events : [];

                for (const event of events) {
                    if (!event || !event.token || seenTokens.has(event.token)) {
                        continue;
                    }
                    seenTokens.add(event.token);
                    handleEvent(event);
                }

                if (seenTokens.size > 5000) {
                    seenTokens.clear();
                }
            } catch (error) {
                // Keep polling while idle.
            }
        }

        function escapeHtml(value) {
            return String(value)
                .replaceAll('&', '&amp;')
                .replaceAll('<', '&lt;')
                .replaceAll('>', '&gt;')
                .replaceAll('"', '&quot;')
                .replaceAll("'", '&#39;');
        }

        setInterval(pollAudioState, 150);
        pollAudioState();
    </script>
</body>
</html>
""",
        encoding="utf-8",
    )

    if not AUDIO_BRIDGE_STATE.exists():
        AUDIO_BRIDGE_STATE.write_text(
            json.dumps({"events": []}, indent=2),
            encoding="utf-8",
        )

    if not LAYOUT_SETTINGS_FILE.exists():
        _write_layout_settings(_default_layout_settings())

    if not OVERLAY_TEMP_HTML.exists():
        OVERLAY_TEMP_HTML.write_text("<html><body style='background:transparent;'></body></html>", encoding="utf-8")

    _write_layout_editor_html()
    _sync_bridge_session_id_into_html()


def _start_audio_bridge_server():
    """Start a tiny local HTTP server used by the OBS browser source."""
    global _audio_bridge_server, _audio_bridge_thread

    if _audio_bridge_server is not None:
        return

    _ensure_audio_bridge_assets()
    handler = partial(SilentHTTPRequestHandler, directory=str(BOT_ROOT))

    try:
        _audio_bridge_server = ThreadingHTTPServer(("127.0.0.1", AUDIO_BRIDGE_PORT), handler)
    except OSError as error:
        print(f"❌ Failed to start audio bridge server on port {AUDIO_BRIDGE_PORT}: {error}")
        _audio_bridge_server = None
        return

    _audio_bridge_thread = threading.Thread(
        target=_audio_bridge_server.serve_forever,
        name="OBS-Audio-Bridge",
        daemon=True,
    )
    _audio_bridge_thread.start()
    print(f"🔊 Audio bridge server running at http://127.0.0.1:{AUDIO_BRIDGE_PORT}/bridge/tts_audio_bridge.html")


def ensure_audio_browser_source():
    """Ensure the local browser-audio bridge exists and return its URL."""
    if _is_hosted_backend_enabled() and str(HOSTED_TTS_PLAYER_URL or "").strip():
        return str(HOSTED_TTS_PLAYER_URL).strip()

    _start_audio_bridge_server()
    return f"http://127.0.0.1:{AUDIO_BRIDGE_PORT}/bridge/tts_audio_bridge.html"


def get_overlay_bridge_url():
    """URL for the full-screen overlay browser page."""
    _start_audio_bridge_server()
    return f"http://127.0.0.1:{AUDIO_BRIDGE_PORT}/bridge/overlay_temp.html"


def get_layout_editor_url():
    """URL for the local browser-based layout editor."""
    _start_audio_bridge_server()
    return f"http://127.0.0.1:{AUDIO_BRIDGE_PORT}/bridge/layout_editor.html"


def _resolve_bridge_audio_path(audio_path):
    requested_path = Path(audio_path)
    resolved_path = requested_path if requested_path.is_absolute() else (BOT_ROOT / requested_path)
    resolved_path = resolved_path.resolve()

    if not resolved_path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    try:
        return resolved_path.relative_to(BOT_ROOT).as_posix()
    except ValueError:
        # Keep the browser bridge sandboxed to BOT_ROOT by copying external assets.
        source_id = hashlib.md5(str(resolved_path).encode("utf-8")).hexdigest()[:12]
        target_name = f"external_{source_id}_{resolved_path.name}"
        target_path = TEMP_TTS_DIR / target_name
        if not target_path.exists():
            shutil.copy2(resolved_path, target_path)
        return target_path.relative_to(BOT_ROOT).as_posix()


def _read_audio_bridge_state():
    if not AUDIO_BRIDGE_STATE.exists():
        return {"events": []}

    try:
        data = json.loads(AUDIO_BRIDGE_STATE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"events": []}

    if not isinstance(data, dict):
        return {"events": []}

    events = data.get("events")
    if not isinstance(events, list):
        events = []

    return {"events": events}


def _append_audio_bridge_event(event_payload):
    with _audio_bridge_state_lock:
        state = _read_audio_bridge_state()
        events = state.get("events", [])
        events.append(event_payload)
        # Cap retained history to avoid unbounded file growth.
        state["events"] = events[-1000:]
        AUDIO_BRIDGE_STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def reset_audio_bridge_events():
    """Clear queued browser events so a fresh bot start cannot replay stale sounds."""
    _ensure_audio_bridge_assets()
    with _audio_bridge_state_lock:
        AUDIO_BRIDGE_STATE.write_text(json.dumps({"events": []}, indent=2), encoding="utf-8")


def queue_audio_for_browser_source(audio_path, volume=1.0, caption="", speaker="", text="", mode="sfx"):
    """Tell the browser audio backend which audio file to play next."""
    payload = {
        "kind": "audio",
        "mode": mode,
        "session_id": AUDIO_BRIDGE_SESSION_ID,
        "created_at": int(time.time() * 1000),
        "token": uuid.uuid4().hex,
        "volume": float(volume),
        "caption": caption,
        "speaker": speaker,
        "text": text,
    }

    if _is_hosted_backend_enabled():
        payload["file"] = _build_hosted_audio_url(audio_path)
        _post_hosted_event(payload)
        return ensure_audio_browser_source()

    _ensure_audio_bridge_assets()
    payload["file"] = _resolve_bridge_audio_path(audio_path)
    _append_audio_bridge_event(payload)
    return ensure_audio_browser_source()


def show_caption_in_browser_source(caption="", speaker="", text=""):
    """Show a caption immediately before the audio file is ready."""
    payload = {
        "kind": "caption",
        "mode": "tts",
        "session_id": AUDIO_BRIDGE_SESSION_ID,
        "created_at": int(time.time() * 1000),
        "file": "",
        "token": uuid.uuid4().hex,
        "volume": 1.0,
        "caption": caption,
        "speaker": speaker,
        "text": text,
    }

    preview = (text or caption or "").strip()
    if len(preview) > 80:
        preview = preview[:77] + "..."
    print(f"💬 Caption queued for OBS: {preview or '(empty)'}")

    if _is_hosted_backend_enabled():
        _post_hosted_event(payload)
        return ensure_audio_browser_source()

    _ensure_audio_bridge_assets()
    _append_audio_bridge_event(payload)

    # Quick sanity check so a dead bridge is obvious in logs.
    try:
        state = _read_audio_bridge_state()
        n = len(state.get("events") or [])
        print(f"💬 Bridge state has {n} event(s); text page: "
              f"http://127.0.0.1:{AUDIO_BRIDGE_PORT}/bridge/tts_text_bridge.html")
    except Exception as error:
        print(f"⚠️ Caption state write check failed: {error}")

    return ensure_audio_browser_source()


def show_spelling_challenge_in_browser_source(word, status="", state="active", reveal_word=True, spelled_out=""):
    """Show or update the Spelling Bee challenge card in the browser source."""
    payload = {
        "kind": "spelling",
        "mode": "spelling",
        "session_id": AUDIO_BRIDGE_SESSION_ID,
        "created_at": int(time.time() * 1000),
        "file": "",
        "token": uuid.uuid4().hex,
        "volume": 1.0,
        "word": str(word or "").strip(),
        "status": str(status or "").strip(),
        "state": str(state or "active").strip().lower(),
        "reveal_word": bool(reveal_word),
        "spelled_out": str(spelled_out or "").strip(),
    }

    if _is_hosted_backend_enabled():
        _post_hosted_event(payload)
        return ensure_audio_browser_source()

    _ensure_audio_bridge_assets()
    _append_audio_bridge_event(payload)
    return ensure_audio_browser_source()


def get_obs_client():
    """Get or create OBS WebSocket client"""
    global _obs_client
    
    if _obs_client is None:
        try:
            _obs_client = obsws(OBS_HOST, OBS_PORT, OBS_PASSWORD)
            _obs_client.connect()
            print(f"✅ Connected to OBS at ws://{OBS_HOST}:{OBS_PORT}")
        except Exception as e:
            print(f"❌ Failed to connect to OBS: {e}")
            print("Check OBS -> Tools -> WebSocket Server Settings:")
            print("  1. Enable WebSocket server")
            print(f"  2. Server Port matches: {OBS_PORT}")
            print("  3. Server Password matches config.py OBS_PASSWORD")
            return None
    
    return _obs_client


async def stop_stream():
    """Stop the current OBS stream output."""
    try:
        obs = get_obs_client()
        if not obs:
            return False

        obs.call(obs_requests.StopStream())
        print("✅ Sent OBS stop stream request")
        return True
    except Exception as error:
        print(f"❌ Error stopping OBS stream: {error}")
        return False


def _get_current_scene_name(obs_client):
    """Return current program scene name across obs-websocket response variants."""
    current_scene = obs_client.call(obs_requests.GetCurrentProgramScene())
    data = getattr(current_scene, "datain", {}) or {}

    return (
        data.get("currentProgramSceneName")
        or data.get("currentProgramScene")
        or data.get("sceneName")
        or current_scene.getCurrentProgramSceneName()
    )


def build_local_bridge_url(relative_path, cache_bust=True):
    """Build a URL served by the local bridge HTTP server for browser sources."""
    ensure_audio_browser_source()
    normalized = str(relative_path).replace("\\", "/").lstrip("/")
    base_url = f"http://127.0.0.1:{AUDIO_BRIDGE_PORT}/{normalized}"
    if cache_bust:
        return f"{base_url}?v={uuid.uuid4().hex}"
    return base_url


async def set_browser_source_url(source_name, relative_path, width=1920, height=1080):
    """Point an OBS Browser Source to a local bridge-served file."""
    try:
        obs = get_obs_client()
        if not obs:
            return False

        source_url = build_local_bridge_url(relative_path)
        obs.call(obs_requests.SetInputSettings(
            inputName=source_name,
            inputSettings={
                "is_local_file": False,
                "url": source_url,
                "width": int(width),
                "height": int(height),
            },
            overlay=True,
        ))
        return True
    except Exception as error:
        print(f"❌ Error setting browser source URL for '{source_name}': {error}")
        print("Create a Browser Source in OBS with this exact name, then try again.")
        return False


async def set_browser_source_direct_url(source_name, source_url, width=1920, height=1080):
    """Point an OBS Browser Source at an explicit URL."""
    try:
        obs = get_obs_client()
        if not obs:
            return False

        obs.call(obs_requests.SetInputSettings(
            inputName=source_name,
            inputSettings={
                "is_local_file": False,
                "url": str(source_url),
                "width": int(width),
                "height": int(height),
            },
            overlay=True,
        ))
        return True
    except Exception as error:
        print(f"❌ Error setting direct browser source URL for '{source_name}': {error}")
        return False


async def configure_tts_browser_source(source_name=None, width=1920, height=1080):
    """Point a Browser Source at the TTS bridge page."""
    target_source = source_name or OBS_TTS_SOURCE
    _apply_tts_caption_position_from_config()

    if _is_hosted_backend_enabled() and str(HOSTED_TTS_PLAYER_URL or "").strip():
        return await set_browser_source_direct_url(
            target_source,
            str(HOSTED_TTS_PLAYER_URL).strip(),
            width=width,
            height=height,
        )

    # Cache-bust so OBS reloads the page with this process session id.
    return await set_browser_source_url(
        target_source,
        f"bridge/tts_audio_bridge.html?sid={AUDIO_BRIDGE_SESSION_ID}",
        width=width,
        height=height,
    )


async def configure_tts_text_browser_source(source_name=None, width=1920, height=1080):
    """Point a Browser Source at a text-only TTS caption page (no audio playback)."""
    target_source = source_name or OBS_TTS_TEXT_SOURCE
    _ensure_audio_bridge_assets()
    ok = await set_browser_source_url(
        target_source,
        f"bridge/tts_text_bridge.html?sid={AUDIO_BRIDGE_SESSION_ID}",
        width=width,
        height=height,
    )
    if ok:
        print(
            f"💬 Caption source ready: '{target_source}' → "
            f"http://127.0.0.1:{AUDIO_BRIDGE_PORT}/bridge/tts_text_bridge.html"
        )
    else:
        print(
            f"⚠️ Caption source '{target_source}' not found in OBS.\n"
            f"   Add a Browser Source named exactly '{target_source}' with URL:\n"
            f"   http://127.0.0.1:{AUDIO_BRIDGE_PORT}/bridge/tts_text_bridge.html\n"
            f"   (1920x1080, transparent background, above your game/cam)"
        )
    return ok


async def configure_overlay_browser_source(source_name=None, width=1920, height=1080):
    """Point a Browser Source at the overlay page used for full-screen effects."""
    target_source = source_name or OBS_OVERLAY_SOURCE
    return await set_browser_source_url(target_source, "bridge/overlay_temp.html", width=width, height=height)


async def move_source_to_front(source_name):
    """Move a source to the top of the current scene draw order."""
    try:
        obs = get_obs_client()
        if not obs:
            return False

        scene_name = _get_current_scene_name(obs)
        item_id = await get_scene_item_id(scene_name, source_name)
        items = obs.call(obs_requests.GetSceneItemList(sceneName=scene_name)).getSceneItems()
        if not items:
            return False

        max_index = max(item.get("sceneItemIndex", 0) for item in items)
        obs.call(obs_requests.SetSceneItemIndex(
            sceneName=scene_name,
            sceneItemId=item_id,
            sceneItemIndex=max_index,
        ))
        return True
    except Exception as error:
        print(f"❌ Error moving source '{source_name}' to front: {error}")
        return False


async def rescale_source(source_name, scale=1.0):
    """
    Change the scale of a source
    
    Args:
        source_name: Name of the source in OBS
        scale: Scale multiplier (1.0 = normal, 0.5 = half, 2.0 = double)
    """
    try:
        obs = get_obs_client()
        if not obs:
            return
        
        # Get current scene
        scene_name = _get_current_scene_name(obs)
        
        # Get source transform
        response = obs.call(obs_requests.GetSceneItemTransform(
            sceneName=scene_name,
            sceneItemId=await get_scene_item_id(scene_name, source_name)
        ))
        
        # Set new scale
        obs.call(obs_requests.SetSceneItemTransform(
            sceneName=scene_name,
            sceneItemId=await get_scene_item_id(scene_name, source_name),
            sceneItemTransform={
                'scaleX': scale,
                'scaleY': scale
            }
        ))
        
        print(f"📐 Scaled {source_name} to {scale}x")
        
    except Exception as e:
        print(f"❌ Error rescaling source: {e}")


async def toggle_source_visibility(source_name, visible=True):
    """
    Show or hide a source
    
    Args:
        source_name: Name of the source
        visible: True to show, False to hide
    """
    try:
        obs = get_obs_client()
        if not obs:
            return
        
        scene_name = _get_current_scene_name(obs)
        
        obs.call(obs_requests.SetSceneItemEnabled(
            sceneName=scene_name,
            sceneItemId=await get_scene_item_id(scene_name, source_name),
            sceneItemEnabled=visible
        ))
        
        status = "shown" if visible else "hidden"
        print(f"👁️ {source_name} {status}")
        
    except Exception as e:
        print(f"❌ Error toggling visibility: {e}")


async def set_source_filter(source_name, filter_name, settings):
    """
    Modify a filter on a source
    
    Args:
        source_name: Name of the source
        filter_name: Name of the filter
        settings: Dictionary of filter settings
    """
    try:
        obs = get_obs_client()
        if not obs:
            return
        
        obs.call(obs_requests.SetSourceFilterSettings(
            sourceName=source_name,
            filterName=filter_name,
            filterSettings=settings
        ))
        
        print(f"🎨 Updated filter '{filter_name}' on {source_name}")
        
    except Exception as e:
        print(f"❌ Error setting filter: {e}")


async def get_source_filter_settings(source_name, filter_name):
    """Return current settings for a named filter on an OBS source."""
    try:
        obs = get_obs_client()
        if not obs:
            return None

        response = obs.call(obs_requests.GetSourceFilterList(sourceName=source_name))
        for item in response.getFilters():
            if item.get("filterName") == filter_name:
                return item.get("filterSettings", {})

        print(f"⚠️ Filter '{filter_name}' not found on source '{source_name}'")
        return None

    except Exception as e:
        print(f"❌ Error getting filter settings: {e}")
        return None


async def switch_scene(scene_name):
    """Switch to a different scene"""
    try:
        obs = get_obs_client()
        if not obs:
            return
        
        obs.call(obs_requests.SetCurrentProgramScene(sceneName=scene_name))
        print(f"🎬 Switched to scene: {scene_name}")
        
    except Exception as e:
        print(f"❌ Error switching scene: {e}")


async def get_scene_item_id(scene_name, source_name):
    """Helper function to get the scene item ID for a source"""
    obs = get_obs_client()
    if not obs:
        return None
    
    # Get all items in the scene
    items = obs.call(obs_requests.GetSceneItemList(sceneName=scene_name))
    
    # Find the source
    for item in items.getSceneItems():
        if item['sourceName'] == source_name:
            return item['sceneItemId']

    available_sources = [item['sourceName'] for item in items.getSceneItems()]
    raise ValueError(
        f"Source '{source_name}' not found in scene '{scene_name}'. "
        f"Available: {available_sources}"
    )


def list_scenes():
    """Return OBS scene names for troubleshooting and setup."""
    obs = get_obs_client()
    if not obs:
        return []

    response = obs.call(obs_requests.GetSceneList())
    scenes = [scene['sceneName'] for scene in response.getScenes()]
    print(f"🎬 OBS scenes: {scenes}")
    return scenes


def list_scene_sources(scene_name=None):
    """Return source names in a scene so reward mappings can be configured."""
    obs = get_obs_client()
    if not obs:
        return []

    if scene_name is None:
        scene_name = _get_current_scene_name(obs)

    items = obs.call(obs_requests.GetSceneItemList(sceneName=scene_name))
    sources = [item['sourceName'] for item in items.getSceneItems()]
    print(f"🧩 Sources in '{scene_name}': {sources}")
    return sources


def diagnose_obs_setup():
    """Quick OBS health check with available scenes and active scene sources."""
    obs = get_obs_client()
    if not obs:
        return False

    try:
        version = obs.call(obs_requests.GetVersion())
        print(f"✅ OBS version: {version.getObsVersion()}")
        print(f"✅ obs-websocket version: {version.getObsWebSocketVersion()}")
    except Exception as error:
        print(f"⚠️ Could not read OBS version info: {error}")

    scenes = list_scenes()
    if scenes:
        list_scene_sources()

    return True


def disconnect_obs():
    """Disconnect from OBS"""
    global _obs_client
    if _obs_client:
        _obs_client.disconnect()
        _obs_client = None
        print("🔌 Disconnected from OBS")