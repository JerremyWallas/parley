"""Custom dark tray window replacing the native Windows context menu."""
import ctypes
import tkinter as tk
from PIL import ImageTk
from icon import create_parley_icon

# DPI awareness
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

# Design constants
BG = "#0f172a"
SURFACE = "#1e293b"
SURFACE_HOVER = "#334155"
BORDER = "#475569"
TEXT = "#f1f5f9"
TEXT_MUTED = "#94a3b8"
ACCENT = "#3b82f6"
SUCCESS = "#22c55e"
RECORDING_RED = "#ef4444"
FONT = "Segoe UI"


class TrayWindow:
    """Modern dark popup window for tray icon interaction."""

    def __init__(self):
        self._win = None
        self._visible = False
        self._on_actions = {}  # callback registry

    def is_visible(self):
        return self._visible and self._win and self._win.winfo_exists()

    def toggle(self, x=None, y=None, state=None):
        """Show or hide the window near the tray icon position."""
        if self.is_visible():
            self.hide()
        else:
            self.show(x, y, state)

    def show(self, x=None, y=None, state=None):
        """Build and show the window."""
        if self._win:
            try:
                self._win.destroy()
            except Exception:
                pass

        state = state or {}
        self._visible = True

        root = tk.Toplevel() if tk._default_root else tk.Tk()
        self._win = root
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.configure(bg=BG)

        # Build content
        self._build_content(root, state)

        # Update to get actual size
        root.update_idletasks()
        w = root.winfo_reqwidth()
        h = root.winfo_reqheight()

        # Position: above tray icon (bottom-right of screen)
        if x is None or y is None:
            screen_w = root.winfo_screenwidth()
            screen_h = root.winfo_screenheight()
            x = screen_w - w - 10
            y = screen_h - h - 50

        # Ensure window stays on screen
        x = max(10, min(x, root.winfo_screenwidth() - w - 10))
        y = max(10, min(y - h, root.winfo_screenheight() - h - 50))

        root.geometry(f"{w}x{h}+{x}+{y}")

        # Close on Escape or click outside
        root.bind("<Escape>", lambda e: self.hide())
        root.bind("<FocusOut>", lambda e: root.after(100, self._check_focus))

        root.focus_force()

    def hide(self):
        self._visible = False
        if self._win:
            try:
                self._win.destroy()
            except Exception:
                pass
            self._win = None

    def _check_focus(self):
        if self._win and self._visible:
            try:
                if not self._win.focus_get():
                    self.hide()
            except Exception:
                self.hide()

    def on(self, action: str, callback):
        """Register a callback for an action."""
        self._on_actions[action] = callback

    def _emit(self, action: str, *args):
        cb = self._on_actions.get(action)
        if cb:
            cb(*args)

    def _build_content(self, root, state):
        pad = 16

        # Main frame with border
        frame = tk.Frame(root, bg=BG, highlightthickness=1,
                         highlightbackground=BORDER)
        frame.pack(fill="both", expand=True)

        # --- Header ---
        header = tk.Frame(frame, bg=BG)
        header.pack(fill="x", padx=pad, pady=(pad, 8))

        icon_img = create_parley_icon(24, ACCENT)
        self._icon = ImageTk.PhotoImage(icon_img)
        tk.Label(header, image=self._icon, bg=BG).pack(side="left", padx=(0, 8))
        tk.Label(header, text="Parley", font=(FONT, 14, "bold"),
                 bg=BG, fg=TEXT).pack(side="left")

        # Connection status
        connected = state.get("connected", False)
        dot_color = SUCCESS if connected else RECORDING_RED
        dot_text = "Verbunden" if connected else "Nicht verbunden"
        tk.Label(header, text="●", font=(FONT, 10), bg=BG, fg=dot_color).pack(side="right")
        tk.Label(header, text=dot_text, font=(FONT, 8), bg=BG, fg=TEXT_MUTED).pack(side="right", padx=(0, 4))

        # --- Divider ---
        tk.Frame(frame, bg=BORDER, height=1).pack(fill="x", padx=pad)

        # --- Presets ---
        section = tk.Frame(frame, bg=BG)
        section.pack(fill="x", padx=pad, pady=(10, 6))
        tk.Label(section, text="Preset", font=(FONT, 9),
                 bg=BG, fg=TEXT_MUTED).pack(anchor="w")

        presets_frame = tk.Frame(frame, bg=BG)
        presets_frame.pack(fill="x", padx=pad, pady=(0, 8))

        active_preset = state.get("active_preset", "raw")
        all_presets = [{"id": "raw", "name": "Raw"}] + state.get("presets", [])

        for p in all_presets:
            pid = p.get("id", "")
            name = p.get("name", pid)
            is_active = pid == active_preset
            btn = tk.Button(
                presets_frame, text=name,
                font=(FONT, 9, "bold" if is_active else "normal"),
                bg=ACCENT if is_active else SURFACE,
                fg="white" if is_active else TEXT_MUTED,
                activebackground=ACCENT, activeforeground="white",
                relief="flat", bd=0, padx=12, pady=4, cursor="hand2",
                command=lambda pid=pid: (self._emit("set_preset", pid), self.hide()),
            )
            btn.pack(side="left", padx=(0, 4))

        # --- Divider ---
        tk.Frame(frame, bg=BORDER, height=1).pack(fill="x", padx=pad)

        # --- Last result ---
        last_text = state.get("last_result", "")
        section = tk.Frame(frame, bg=BG)
        section.pack(fill="x", padx=pad, pady=(8, 6))

        if last_text:
            preview = (last_text[:50] + "...") if len(last_text) > 50 else last_text
            lbl = tk.Label(section, text=preview, font=(FONT, 9),
                           bg=SURFACE, fg=TEXT, padx=10, pady=6, anchor="w",
                           cursor="hand2", wraplength=250, justify="left")
            lbl.pack(fill="x")
            lbl.bind("<Button-1>", lambda e: (self._emit("copy_last"), self.hide()))
            tk.Label(section, text="Klicken zum Kopieren", font=(FONT, 7),
                     bg=BG, fg=TEXT_MUTED).pack(anchor="w", pady=(2, 0))
        else:
            tk.Label(section, text="Noch kein Ergebnis", font=(FONT, 9),
                     bg=BG, fg=TEXT_MUTED).pack(anchor="w")

        # --- Divider ---
        tk.Frame(frame, bg=BORDER, height=1).pack(fill="x", padx=pad)

        # --- Toggles ---
        toggles = tk.Frame(frame, bg=BG)
        toggles.pack(fill="x", padx=pad, pady=(8, 4))

        # Auto-Paste
        auto_paste = state.get("auto_paste", True)
        self._make_toggle(toggles, "Auto-Paste", auto_paste,
                          lambda: self._emit("toggle_auto_paste"))

        # Send mode
        send_mode = state.get("send_mode", "off")
        send_labels = {"off": "Aus", "auto": "Auto (Enter)", "voice": "Sprachbefehl"}
        send_frame = tk.Frame(toggles, bg=BG)
        send_frame.pack(fill="x", pady=(4, 0))
        tk.Label(send_frame, text="Senden:", font=(FONT, 9),
                 bg=BG, fg=TEXT).pack(side="left")
        for mode, label in send_labels.items():
            is_active = send_mode == mode
            btn = tk.Button(
                send_frame, text=label,
                font=(FONT, 8),
                bg=ACCENT if is_active else SURFACE,
                fg="white" if is_active else TEXT_MUTED,
                activebackground=ACCENT, activeforeground="white",
                relief="flat", bd=0, padx=8, pady=2, cursor="hand2",
                command=lambda m=mode: (self._emit("set_send_mode", m), self.hide()),
            )
            btn.pack(side="left", padx=(6, 0))

        # --- Divider ---
        tk.Frame(frame, bg=BORDER, height=1).pack(fill="x", padx=pad, pady=(8, 0))

        # --- Hotkey info ---
        info = tk.Frame(frame, bg=BG)
        info.pack(fill="x", padx=pad, pady=(6, 4))
        hold_key = state.get("hotkey_hold", "")
        toggle_key = state.get("hotkey_toggle", "")
        if hold_key:
            tk.Label(info, text=f"Halten: {hold_key}", font=(FONT, 8),
                     bg=BG, fg=TEXT_MUTED).pack(anchor="w")
        if toggle_key:
            tk.Label(info, text=f"Freihand: {toggle_key}", font=(FONT, 8),
                     bg=BG, fg=TEXT_MUTED).pack(anchor="w")

        # --- Divider ---
        tk.Frame(frame, bg=BORDER, height=1).pack(fill="x", padx=pad)

        # --- Bottom buttons ---
        bottom = tk.Frame(frame, bg=BG)
        bottom.pack(fill="x", padx=pad, pady=(8, pad))

        tk.Button(bottom, text="Einstellungen", font=(FONT, 9),
                  bg=SURFACE, fg=TEXT, activebackground=SURFACE_HOVER,
                  relief="flat", bd=0, padx=12, pady=4, cursor="hand2",
                  command=lambda: (self._emit("open_settings"), self.hide()),
                  ).pack(side="left")

        tk.Button(bottom, text="Beenden", font=(FONT, 9),
                  bg=SURFACE, fg=RECORDING_RED, activebackground=SURFACE_HOVER,
                  relief="flat", bd=0, padx=12, pady=4, cursor="hand2",
                  command=lambda: self._emit("quit"),
                  ).pack(side="right")

    def _make_toggle(self, parent, label, active, on_toggle):
        row = tk.Frame(parent, bg=BG)
        row.pack(fill="x")
        tk.Label(row, text=label, font=(FONT, 9), bg=BG, fg=TEXT).pack(side="left")

        toggle_bg = ACCENT if active else BORDER
        toggle_btn = tk.Canvas(row, width=36, height=20, bg=BG,
                               highlightthickness=0, cursor="hand2")
        toggle_btn.pack(side="right")

        # Track
        toggle_btn.create_rectangle(2, 4, 34, 18, fill=toggle_bg, outline="", width=0)
        # Knob
        knob_x = 22 if active else 10
        toggle_btn.create_oval(knob_x - 6, 5, knob_x + 6, 17, fill="white", outline="")

        toggle_btn.bind("<Button-1>", lambda e: on_toggle())
