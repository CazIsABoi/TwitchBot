"""
Open the local OBS layout editor in your default browser.
Run: python open_layout_editor.py
"""

import webbrowser

from obs_handler import get_layout_editor_url


def main():
    url = get_layout_editor_url()
    opened = webbrowser.open(url)

    print(f"Layout editor URL: {url}")
    if opened:
        print("Opened layout editor in your default browser.")
    else:
        print("Could not auto-open browser. Copy the URL above into your browser.")

    print("\nKeep this terminal open while editing layout. Press ENTER to stop.")
    try:
        input()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
