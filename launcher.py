"""
PyInstaller entry point.

Import failures (e.g. missing _cffi_backend) happen *before* twitch.main() would run,
so this file catches everything and writes crash.log next to the exe.
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path


def _app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _write_crash(details: str) -> Path:
    path = _app_dir() / "crash.log"
    try:
        path.write_text(details, encoding="utf-8")
    except Exception:
        # Last resort: try cwd
        path = Path.cwd() / "crash.log"
        try:
            path.write_text(details, encoding="utf-8")
        except Exception:
            pass
    return path


def main() -> int:
    try:
        # Import only after we are ready to catch errors
        from twitch import main as bot_main

        bot_main()
        return 0
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        return 1
    except BaseException:
        details = traceback.format_exc()
        header = (
            f"frozen={getattr(sys, 'frozen', False)}\n"
            f"executable={sys.executable}\n"
            f"cwd={Path.cwd()}\n"
            f"app_dir={_app_dir()}\n"
            f"sys.path[0:5]={sys.path[:5]}\n\n"
        )
        full = header + details
        print("\n" + "=" * 60, flush=True)
        print("CazIsABot failed to start:", flush=True)
        print("=" * 60, flush=True)
        print(full, flush=True)
        log_path = _write_crash(full)
        print(f"Saved: {log_path}", flush=True)
        print("=" * 60, flush=True)
        try:
            input("Press ENTER to close this window...")
        except EOFError:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
