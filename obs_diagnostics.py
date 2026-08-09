"""
Quick OBS integration diagnostics.
Run: python obs_diagnostics.py
"""

from obs_handler import diagnose_obs_setup, ensure_audio_browser_source, get_layout_editor_url
from config import OBS_WEBCAM_SOURCE, OBS_DISPLAY_SOURCE, OBS_ROTATE_FILTER, OBS_OVERLAY_SOURCE, OBS_TTS_SOURCE
from obs_handler import list_scene_sources, get_obs_client
from obswebsocket import requests as obs_requests


def main():
    print("=== OBS WebSocket Diagnostics ===")
    ok = diagnose_obs_setup()
    if not ok:
        print("\nOBS WebSocket connection failed. Check config.py OBS_* values.")
        return

    scene_sources = list_scene_sources()
    missing_sources = []
    if OBS_WEBCAM_SOURCE not in scene_sources:
        missing_sources.append(("OBS_WEBCAM_SOURCE", OBS_WEBCAM_SOURCE))
    if OBS_DISPLAY_SOURCE not in scene_sources:
        missing_sources.append(("OBS_DISPLAY_SOURCE", OBS_DISPLAY_SOURCE))
    if OBS_TTS_SOURCE not in scene_sources:
        missing_sources.append(("OBS_TTS_SOURCE", OBS_TTS_SOURCE))
    if OBS_OVERLAY_SOURCE not in scene_sources:
        missing_sources.append(("OBS_OVERLAY_SOURCE", OBS_OVERLAY_SOURCE))

    if missing_sources:
        print("\nConfigured OBS source names not found in current scene:")
        for key, value in missing_sources:
            print(f"  - {key}={value}")
        print("Update these values in config.py to match OBS exactly.")
    else:
        print("\nConfigured OBS source names exist in the current scene.")

    try:
        obs = get_obs_client()
        if obs and OBS_DISPLAY_SOURCE in scene_sources:
            response = obs.call(obs_requests.GetSourceFilterList(sourceName=OBS_DISPLAY_SOURCE))
            filters = [f["filterName"] for f in response.getFilters()]
            if OBS_ROTATE_FILTER not in filters:
                print(f"Configured rotate filter not found on {OBS_DISPLAY_SOURCE}: {OBS_ROTATE_FILTER}")
                print(f"Available filters: {filters}")
            else:
                print("Configured rotate filter found.")
    except Exception as error:
        print(f"Could not validate display filter configuration: {error}")

    bridge_url = ensure_audio_browser_source()
    print(f"\nBrowser audio bridge URL: {bridge_url}")
    print(f"Overlay browser URL: {bridge_url.replace('tts_audio_bridge.html', 'overlay_temp.html')}")
    print(f"Layout editor URL: {get_layout_editor_url()}")
    print("Use two OBS browser sources: one for TTS and one for overlay effects.")


if __name__ == "__main__":
    main()
