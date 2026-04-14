"""Settings window for Parley Desktop Client using tkinter."""
import ctypes
import tkinter as tk
from pynput import keyboard as kb
from PIL import ImageTk

# Enable DPI awareness for sharp rendering on high-DPI displays
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

import config
from icon import create_parley_icon

# --- Design constants ---
BG = "#0f172a"
SURFACE = "#1e293b"
SURFACE_HOVER = "#334155"
BORDER = "#475569"
TEXT = "#f1f5f9"
TEXT_MUTED = "#94a3b8"
ACCENT = "#3b82f6"
FONT = "Segoe UI"
FONT_SIZE = 11
FONT_SMALL = 10
FONT_HINT = 9


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
        if self.listener:
            self.listener.stop()
            self.listener = None
        hotkey = "+".join(self.parts)
        self.on_done(hotkey)


class SettingsWindow:
    """Modern settings window."""

    def __init__(self, cfg: dict, on_save):
        self.cfg = cfg.copy()
        self.on_save = on_save
        self.recorder = None

    def _make_section(self, parent, title):
        """Create a styled section card with title."""
        frame = tk.Frame(parent, bg=SURFACE, bd=0, highlightthickness=0)
        frame.pack(fill="x", padx=24, pady=(0, 12))

        # Inner padding
        inner = tk.Frame(frame, bg=SURFACE)
        inner.pack(fill="x", padx=16, pady=14)

        if title:
            tk.Label(inner, text=title, font=(FONT, FONT_SMALL, "bold"),
                     bg=SURFACE, fg=TEXT_MUTED).pack(anchor="w", pady=(0, 8))

        return inner

    def _make_entry(self, parent, value="", fg=TEXT):
        """Create a styled input field."""
        wrapper = tk.Frame(parent, bg=BG, bd=0, highlightthickness=1,
                           highlightcolor=ACCENT, highlightbackground=BORDER)
        entry = tk.Entry(wrapper, font=(FONT, FONT_SIZE), bg=BG, fg=fg,
                         insertbackground=TEXT, relief="flat", bd=0,
                         highlightthickness=0)
        entry.insert(0, value)
        entry.pack(fill="x", padx=8, pady=6)
        wrapper.pack(fill="x")
        return entry, wrapper

    def _make_button(self, parent, text, command, primary=False):
        """Create a styled button."""
        bg = ACCENT if primary else SURFACE_HOVER
        btn = tk.Button(parent, text=text, font=(FONT, FONT_SIZE, "bold" if primary else "normal"),
                        bg=bg, fg="white" if primary else TEXT,
                        activebackground=ACCENT, activeforeground="white",
                        relief="flat", bd=0, padx=20, pady=8, cursor="hand2",
                        command=command)
        return btn

    def show(self):
        self.win = tk.Tk()
        self.win.title("Parley")
        self.win.resizable(False, False)
        self.win.configure(bg=BG)

        # Window icon
        icon_img = create_parley_icon(32)
        self._icon_photo = ImageTk.PhotoImage(icon_img)
        self.win.iconphoto(True, self._icon_photo)

        # --- Header ---
        header = tk.Frame(self.win, bg=BG)
        header.pack(fill="x", padx=24, pady=(24, 16))

        # Icon + title side by side
        icon_large = create_parley_icon(40)
        self._header_icon = ImageTk.PhotoImage(icon_large)
        tk.Label(header, image=self._header_icon, bg=BG).pack(side="left", padx=(0, 12))

        title_frame = tk.Frame(header, bg=BG)
        title_frame.pack(side="left")
        tk.Label(title_frame, text="Parley", font=(FONT, 18, "bold"),
                 bg=BG, fg=TEXT).pack(anchor="w")
        tk.Label(title_frame, text="Einstellungen", font=(FONT, FONT_SMALL),
                 bg=BG, fg=TEXT_MUTED).pack(anchor="w")

        content = tk.Frame(self.win, bg=BG)
        content.pack(fill="x")

        # --- Server URL section ---
        sec = self._make_section(content, "Server")
        self.server_entry, _ = self._make_entry(sec, self.cfg.get("server_url", ""))

        # --- Hotkeys section ---
        sec = self._make_section(content, "Hotkeys")

        # Hold hotkey
        tk.Label(sec, text="Halten (gedrückt halten zum Sprechen):",
                 font=(FONT, FONT_HINT), bg=SURFACE, fg=TEXT_MUTED).pack(anchor="w", pady=(0, 4))
        hold_row = tk.Frame(sec, bg=SURFACE)
        hold_row.pack(fill="x", pady=(0, 10))

        self.hotkey_label = tk.Label(hold_row, text=self.cfg.get("hotkey_hold", ""),
                                     font=(FONT, FONT_SIZE, "bold"), bg=BG, fg=ACCENT,
                                     anchor="w", padx=10, pady=6,
                                     highlightthickness=1, highlightbackground=BORDER)
        self.hotkey_label.pack(side="left", fill="x", expand=True)

        self.record_btn = self._make_button(hold_row, "Aufnehmen", self._start_recording)
        self.record_btn.pack(side="right", padx=(10, 0))

        # Toggle hotkey
        tk.Label(sec, text="Freihand (einmal drücken = Start, nochmal = Stop):",
                 font=(FONT, FONT_HINT), bg=SURFACE, fg=TEXT_MUTED).pack(anchor="w", pady=(0, 4))
        toggle_row = tk.Frame(sec, bg=SURFACE)
        toggle_row.pack(fill="x", pady=(0, 10))

        self.toggle_label = tk.Label(toggle_row, text=self.cfg.get("hotkey_toggle", ""),
                                      font=(FONT, FONT_SIZE, "bold"), bg=BG, fg=ACCENT,
                                      anchor="w", padx=10, pady=6,
                                      highlightthickness=1, highlightbackground=BORDER)
        self.toggle_label.pack(side="left", fill="x", expand=True)

        self.toggle_btn = self._make_button(toggle_row, "Aufnehmen", self._start_toggle_recording)
        self.toggle_btn.pack(side="right", padx=(10, 0))

        # Stop key
        tk.Label(sec, text="Stopp-Taste (alternative Taste zum Beenden im Freihand-Modus):",
                 font=(FONT, FONT_HINT), bg=SURFACE, fg=TEXT_MUTED).pack(anchor="w", pady=(0, 4))
        stop_row = tk.Frame(sec, bg=SURFACE)
        stop_row.pack(fill="x")

        self.stop_key_label = tk.Label(stop_row, text=self.cfg.get("stop_key", "<esc>"),
                                       font=(FONT, FONT_SIZE, "bold"), bg=BG, fg=ACCENT,
                                       anchor="w", padx=10, pady=6,
                                       highlightthickness=1, highlightbackground=BORDER)
        self.stop_key_label.pack(side="left", fill="x", expand=True)

        self.stop_key_btn = self._make_button(stop_row, "Aufnehmen", self._start_stop_key_recording)
        self.stop_key_btn.pack(side="right", padx=(10, 0))

        # --- Mode section ---
        sec = self._make_section(content, "Standard-Modus")

        self.mode_var = tk.StringVar(value=self.cfg.get("mode", "raw"))
        mode_row = tk.Frame(sec, bg=SURFACE)
        mode_row.pack(fill="x")
        for val, label in [("raw", "Raw"), ("cleanup", "Cleanup"), ("rephrase", "Reformulieren")]:
            tk.Radiobutton(mode_row, text=label, variable=self.mode_var, value=val,
                           bg=SURFACE, fg=TEXT, selectcolor=SURFACE_HOVER,
                           activebackground=SURFACE, activeforeground=TEXT,
                           font=(FONT, FONT_SIZE), indicatoron=True,
                           ).pack(side="left", padx=(0, 20))

        # --- Options section ---
        sec = self._make_section(content, "Optionen")

        self.autopaste_var = tk.BooleanVar(value=self.cfg.get("auto_paste", True))
        tk.Checkbutton(sec, text="Auto-Paste (Ctrl+V nach Transkription)",
                       variable=self.autopaste_var,
                       bg=SURFACE, fg=TEXT, selectcolor=SURFACE_HOVER,
                       activebackground=SURFACE, activeforeground=TEXT,
                       font=(FONT, FONT_SIZE)).pack(anchor="w", pady=(0, 6))

        # Send mode
        tk.Label(sec, text="Nach Transkription senden:", font=(FONT, FONT_SMALL, "bold"),
                 bg=SURFACE, fg=TEXT_MUTED).pack(anchor="w", pady=(4, 6))

        self.send_var = tk.StringVar(value=self.cfg.get("send_mode", "off"))
        for val, label in [("off", "Aus"), ("auto", "Immer (Enter)"), ("voice", "Per Sprachbefehl")]:
            tk.Radiobutton(sec, text=label, variable=self.send_var, value=val,
                           bg=SURFACE, fg=TEXT, selectcolor=SURFACE_HOVER,
                           activebackground=SURFACE, activeforeground=TEXT,
                           font=(FONT, FONT_SIZE)).pack(anchor="w", padx=(4, 0))

        tk.Label(sec, text='Sprachbefehl: Parley hoert 10s auf "Senden"',
                 font=(FONT, FONT_HINT), bg=SURFACE, fg=TEXT_MUTED).pack(anchor="w", pady=(4, 0))

        # --- Save button ---
        btn_frame = tk.Frame(content, bg=BG)
        btn_frame.pack(fill="x", padx=24, pady=(8, 24))

        save_btn = self._make_button(btn_frame, "Speichern", self._save, primary=True)
        save_btn.pack(fill="x")

        # Set fixed width, let height auto-size, then center on screen
        self.win.update_idletasks()
        win_w = 480
        win_h = self.win.winfo_reqheight()
        screen_w = self.win.winfo_screenwidth()
        screen_h = self.win.winfo_screenheight()
        x = (screen_w - win_w) // 2
        y = (screen_h - win_h) // 2
        self.win.geometry(f"{win_w}x{win_h}+{x}+{y}")

        self.win.mainloop()

    def _start_recording(self):
        self.record_btn.config(state="disabled", text="...")
        self.recorder = HotkeyRecorder(self.hotkey_label, self._hotkey_recorded)
        self.recorder.start()

    def _hotkey_recorded(self, hotkey: str):
        self.cfg["hotkey_hold"] = hotkey
        self.record_btn.config(state="normal", text="Aufnehmen")

    def _start_toggle_recording(self):
        self.toggle_btn.config(state="disabled", text="...")
        self.recorder = HotkeyRecorder(self.toggle_label, self._toggle_recorded)
        self.recorder.start()

    def _toggle_recorded(self, hotkey: str):
        self.cfg["hotkey_toggle"] = hotkey
        self.toggle_btn.config(state="normal", text="Aufnehmen")

    def _start_stop_key_recording(self):
        self.stop_key_btn.config(state="disabled", text="...")
        self.recorder = HotkeyRecorder(self.stop_key_label, self._stop_key_recorded)
        self.recorder.start()

    def _stop_key_recorded(self, hotkey: str):
        self.cfg["stop_key"] = hotkey
        self.stop_key_btn.config(state="normal", text="Aufnehmen")

    def _save(self):
        self.cfg["server_url"] = self.server_entry.get().strip()
        self.cfg["mode"] = self.mode_var.get()
        self.cfg["auto_paste"] = self.autopaste_var.get()
        self.cfg["send_mode"] = self.send_var.get()
        config.save(self.cfg)
        self.on_save(self.cfg)
        self.win.destroy()


def open_settings(cfg: dict, on_save):
    """Open settings window in a new thread-safe way."""
    sw = SettingsWindow(cfg, on_save)
    sw.show()
