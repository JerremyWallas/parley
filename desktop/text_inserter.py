import time
import pyperclip
from pynput.keyboard import Controller, Key

keyboard = Controller()


def insert_text(text: str, auto_paste: bool = True):
    """Insert text into the active window.

    If auto_paste is True, copies to clipboard and simulates Ctrl+V.
    Otherwise, just copies to clipboard.
    """
    pyperclip.copy(text)

    if auto_paste:
        time.sleep(0.1)
        keyboard.press(Key.ctrl)
        keyboard.press("v")
        keyboard.release("v")
        keyboard.release(Key.ctrl)
