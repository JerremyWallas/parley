"""Settings window for Parley Desktop Client using tkinter."""
import tkinter as tk
from tkinter import ttk
from pynput import keyboard as kb

import config


class HotkeyRecorder:
    """Records a key combination by listening for key presses."""

    def __init__(self, label: tk.Label, on_done):
        self.label = label
        self.on_done = on_done
        self.pressed = set()
        self.parts = []
        self.listener = None

    def start(self):
        self.pressed.clear()
        self.parts.clear()
        self.label.config(text="Druecke deine Tastenkombination...")

        self.listener = kb.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
        )
        self.listener.start()

    def _key_to_str(self, key) -> str:
        if isinstance(key, kb.Key):
            return f"<{key.name}>"
        elif hasattr(key, "char") and key.char:
            return key.char.lower()
        return str(key)

    def _on_press(self, key):
        part = self._key_to_str(key)
        if part not in self.pressed:
            self.pressed.add(part)
            self.parts.append(part)
            self.label.config(text=" + ".join(self.parts))

    def _on_release(self, key):
        # When any key is released, finalize the combination
        if self.listener:
            self.listener.stop()
            self.listener = None
        hotkey = "+".join(self.parts)
        self.on_done(hotkey)


class SettingsWindow:
    """Tkinter settings window."""

    def __init__(self, cfg: dict, on_save):
        self.cfg = cfg.copy()
        self.on_save = on_save
        self.recorder = None

    def show(self):
        self.win = tk.Tk()
        self.win.title("Parley — Einstellungen")
        self.win.geometry("420x340")
        self.win.resizable(False, False)
        self.win.configure(bg="#1e293b")

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TLabel", background="#1e293b", foreground="#f1f5f9", font=("Segoe UI", 10))
        style.configure("TButton", font=("Segoe UI", 10))
        style.configure("Header.TLabel", font=("Segoe UI", 13, "bold"), foreground="#f1f5f9", background="#1e293b")

        pad = {"padx": 16, "pady": (8, 2)}

        # Header
        ttk.Label(self.win, text="Parley Einstellungen", style="Header.TLabel").pack(pady=(16, 12))

        # Server URL
        ttk.Label(self.win, text="Server-URL:").pack(anchor="w", **pad)
        self.server_entry = tk.Entry(self.win, font=("Segoe UI", 10), bg="#0f172a", fg="#f1f5f9",
                                     insertbackground="#f1f5f9", relief="flat", bd=4)
        self.server_entry.insert(0, self.cfg.get("server_url", ""))
        self.server_entry.pack(fill="x", padx=16, pady=(0, 4))

        # Hotkey
        ttk.Label(self.win, text="Hotkey:").pack(anchor="w", **pad)
        hotkey_frame = tk.Frame(self.win, bg="#1e293b")
        hotkey_frame.pack(fill="x", padx=16, pady=(0, 4))

        self.hotkey_label = tk.Label(hotkey_frame, text=self.cfg.get("hotkey", ""),
                                     font=("Segoe UI", 10, "bold"), bg="#0f172a", fg="#3b82f6",
                                     relief="flat", bd=4, anchor="w", padx=8)
        self.hotkey_label.pack(side="left", fill="x", expand=True)

        self.record_btn = tk.Button(hotkey_frame, text="Aufnehmen", font=("Segoe UI", 9),
                                    bg="#334155", fg="#f1f5f9", relief="flat", bd=0, padx=12, pady=2,
                                    command=self._start_recording)
        self.record_btn.pack(side="right", padx=(8, 0))

        # Mode
        ttk.Label(self.win, text="Standard-Modus:").pack(anchor="w", **pad)
        mode_frame = tk.Frame(self.win, bg="#1e293b")
        mode_frame.pack(fill="x", padx=16, pady=(0, 4))

        self.mode_var = tk.StringVar(value=self.cfg.get("mode", "raw"))
        for val, label in [("raw", "Raw"), ("cleanup", "Cleanup"), ("rephrase", "Reformulieren")]:
            tk.Radiobutton(mode_frame, text=label, variable=self.mode_var, value=val,
                           bg="#1e293b", fg="#f1f5f9", selectcolor="#334155",
                           activebackground="#1e293b", activeforeground="#f1f5f9",
                           font=("Segoe UI", 10)).pack(side="left", padx=(0, 16))

        # Auto-paste checkbox
        self.autopaste_var = tk.BooleanVar(value=self.cfg.get("auto_paste", True))
        tk.Checkbutton(self.win, text="Auto-Paste (Ctrl+V nach Transkription)", variable=self.autopaste_var,
                       bg="#1e293b", fg="#f1f5f9", selectcolor="#334155",
                       activebackground="#1e293b", activeforeground="#f1f5f9",
                       font=("Segoe UI", 10)).pack(anchor="w", padx=16, pady=(8, 4))

        # Save button
        save_btn = tk.Button(self.win, text="Speichern", font=("Segoe UI", 11, "bold"),
                             bg="#3b82f6", fg="white", relief="flat", bd=0, padx=24, pady=6,
                             command=self._save)
        save_btn.pack(pady=(16, 12))

        self.win.mainloop()

    def _start_recording(self):
        self.record_btn.config(state="disabled", text="...")
        self.recorder = HotkeyRecorder(self.hotkey_label, self._hotkey_recorded)
        self.recorder.start()

    def _hotkey_recorded(self, hotkey: str):
        self.cfg["hotkey"] = hotkey
        self.record_btn.config(state="normal", text="Aufnehmen")

    def _save(self):
        self.cfg["server_url"] = self.server_entry.get().strip()
        self.cfg["mode"] = self.mode_var.get()
        self.cfg["auto_paste"] = self.autopaste_var.get()
        config.save(self.cfg)
        self.on_save(self.cfg)
        self.win.destroy()


def open_settings(cfg: dict, on_save):
    """Open settings window in a new thread-safe way."""
    sw = SettingsWindow(cfg, on_save)
    sw.show()
