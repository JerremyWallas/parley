import logging
import shutil
import subprocess
import sys
import time

import pyperclip
from pynput.keyboard import Controller, Key

logger = logging.getLogger(__name__)

# On Wayland (Linux) pynput cannot synthesize key events into other
# applications, so we shell out to ydotool. ydotool talks to the
# Linux uinput kernel device via a daemon (ydotoold), which works
# on both X11 and Wayland.
_USE_YDOTOOL = sys.platform.startswith("linux") and shutil.which("ydotool") is not None
_USE_WL_COPY = sys.platform.startswith("linux") and shutil.which("wl-copy") is not None

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
    if _USE_YDOTOOL:
        # ydotool uses Linux input event codes: 29=ctrl, 47=v
        try:
            subprocess.run(
                ["ydotool", "key", "29:1", "47:1", "47:0", "29:0"],
                check=True, timeout=2,
            )
            return
        except Exception as e:
            logger.warning(f"ydotool paste failed, falling back to pynput: {e}")
    keyboard.press(Key.ctrl)
    keyboard.press("v")
    keyboard.release("v")
    keyboard.release(Key.ctrl)


def _press_enter_native() -> None:
    """Send Enter to the active window."""
    if _USE_YDOTOOL:
        try:
            # 28 = KEY_ENTER
            subprocess.run(["ydotool", "key", "28:1", "28:0"], check=True, timeout=2)
            return
        except Exception as e:
            logger.warning(f"ydotool enter failed, falling back to pynput: {e}")
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
