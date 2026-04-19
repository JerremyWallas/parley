import logging
import os
import shutil
import subprocess
import sys
import time

import pyperclip
from pynput.keyboard import Controller, Key

logger = logging.getLogger(__name__)

# On Wayland, pynput cannot synthesize key events into other applications.
# wtype is a small Wayland-native helper from the Ubuntu/Debian repos that
# does this without needing a daemon or root. We detect Wayland via
# XDG_SESSION_TYPE so X11 users keep using pynput directly.
_IS_WAYLAND = (
    sys.platform.startswith("linux")
    and os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
)
_USE_WTYPE = _IS_WAYLAND and shutil.which("wtype") is not None
_USE_WL_COPY = _IS_WAYLAND and shutil.which("wl-copy") is not None

keyboard = Controller()


def _copy_clipboard(text: str) -> None:
    """Copy text to clipboard. Uses wl-copy on Wayland (more reliable than pyperclip)."""
    if _USE_WL_COPY:
        try:
            subprocess.run(["wl-copy"], input=text, text=True, check=True, timeout=2)
            return
        except Exception as e:
            logger.warning(f"wl-copy failed, falling back to pyperclip: {e}")
    pyperclip.copy(text)


def _press_paste() -> None:
    """Send Ctrl+V to the active window."""
    if _USE_WTYPE:
        try:
            # -M ctrl: press ctrl, "v" types v, -m ctrl: release ctrl
            subprocess.run(["wtype", "-M", "ctrl", "v", "-m", "ctrl"],
                           check=True, timeout=2)
            return
        except Exception as e:
            logger.warning(f"wtype paste failed, falling back to pynput: {e}")
    keyboard.press(Key.ctrl)
    keyboard.press("v")
    keyboard.release("v")
    keyboard.release(Key.ctrl)


def _press_enter_native() -> None:
    """Send Enter to the active window."""
    if _USE_WTYPE:
        try:
            subprocess.run(["wtype", "-k", "Return"], check=True, timeout=2)
            return
        except Exception as e:
            logger.warning(f"wtype enter failed, falling back to pynput: {e}")
    keyboard.press(Key.enter)
    keyboard.release(Key.enter)


def insert_text(text: str, auto_paste: bool = True):
    """Insert text into the active window.

    If auto_paste is True, copies to clipboard and simulates Ctrl+V.
    Otherwise, just copies to clipboard.
    """
    _copy_clipboard(text)

    if auto_paste:
        time.sleep(0.1)
        _press_paste()


def press_enter():
    """Simulate pressing Enter to send a message."""
    time.sleep(0.15)
    _press_enter_native()
