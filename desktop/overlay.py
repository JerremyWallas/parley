"""Transparent overlay window showing recording/processing state — centered above taskbar."""
import ctypes
import tkinter as tk
import threading
import math

# Enable DPI awareness for sharp rendering on high-DPI displays
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # Per-monitor DPI aware
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()  # Fallback
    except Exception:
        pass


class RecordingOverlay:
    """Floating overlay that shows recording state, centered at bottom of screen."""

    def __init__(self):
        self._root = None
        self._canvas = None
        self._thread = None
        self._running = False
        self._state = "hidden"  # hidden, recording, processing, listening
        self._animation_step = 0

    def show(self, state: str = "recording"):
        """Show overlay with given state. Thread-safe."""
        self._state = state
        if self._root and self._running:
            self._root.after(0, self._update_visuals)
            self._root.after(0, lambda: self._root.deiconify())
        elif not self._running:
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
            threading.Event().wait(0.2)

    def hide(self):
        """Hide overlay. Thread-safe."""
        self._state = "hidden"
        if self._root and self._running:
            self._root.after(0, lambda: self._root.withdraw())

    def update_state(self, state: str):
        """Update the visual state without showing/hiding."""
        self._state = state
        if self._root and self._running:
            self._root.after(0, self._update_visuals)

    def _run(self):
        self._running = True
        self._root = tk.Tk()
        self._root.title("")
        self._root.overrideredirect(True)
        self._root.attributes("-topmost", True)
        self._root.attributes("-transparentcolor", "#010101")
        self._root.configure(bg="#010101")

        size = 100

        # Position: centered horizontally, above taskbar (~60px from bottom)
        screen_w = self._root.winfo_screenwidth()
        screen_h = self._root.winfo_screenheight()
        x = (screen_w - size) // 2
        y = screen_h - size - 60

        self._root.geometry(f"{size}x{size}+{x}+{y}")

        self._canvas = tk.Canvas(
            self._root, width=size, height=size,
            bg="#010101", highlightthickness=0,
        )
        self._canvas.pack()

        self._animate()
        self._root.mainloop()
        self._running = False

    def _animate(self):
        if not self._running or not self._root:
            return

        if self._state == "hidden":
            self._root.after(100, self._animate)
            return

        self._canvas.delete("all")
        cx, cy = 50, 50

        self._animation_step += 1
        pulse = math.sin(self._animation_step * 0.15) * 0.15 + 1.0

        colors = {
            "recording": ("#ef4444", "#ff6b6b"),
            "processing": ("#f59e0b", "#fbbf24"),
            "listening": ("#22c55e", "#4ade80"),
        }
        color, light_color = colors.get(self._state, ("#3b82f6", "#60a5fa"))

        # Outer glow ring (pulsing)
        glow_r = int(38 * pulse)
        self._canvas.create_oval(
            cx - glow_r, cy - glow_r, cx + glow_r, cy + glow_r,
            fill="", outline=light_color, width=2,
        )

        # Inner solid circle
        r = 28
        self._canvas.create_oval(
            cx - r, cy - r, cx + r, cy + r,
            fill=color, outline="",
        )

        # Speech bubble icon (matching the Parley brand)
        self._draw_speech_icon(cx, cy)

        # State-specific animation on top
        if self._state == "recording":
            # Pulsing sound waves
            for i in range(3):
                wave_r = int((18 + i * 6) * pulse)
                alpha_width = max(1, 3 - i)
                self._canvas.create_arc(
                    cx + 8 - wave_r, cy - wave_r,
                    cx + 8 + wave_r, cy + wave_r,
                    start=-30, extent=60,
                    outline="white", width=alpha_width, style="arc",
                )
        elif self._state == "processing":
            # Spinning dots around the circle
            for i in range(3):
                angle = (self._animation_step * 0.2 + i * 2.1)
                dx = math.cos(angle) * 22
                dy = math.sin(angle) * 22
                self._canvas.create_oval(
                    cx + dx - 3, cy + dy - 3, cx + dx + 3, cy + dy + 3,
                    fill="white", outline="",
                )

        self._root.after(50, self._animate)

    def _draw_speech_icon(self, cx, cy):
        """Draw a small speech bubble in the center."""
        # Bubble body
        bx, by = cx - 8, cy - 8
        self._canvas.create_rectangle(
            bx, by, bx + 16, by + 12,
            fill="white", outline="",
        )
        # Bubble tail
        self._canvas.create_polygon(
            bx + 2, by + 12,
            bx - 2, by + 18,
            bx + 8, by + 12,
            fill="white", outline="",
        )
        # Three dots inside
        for dx in [-4, 0, 4]:
            self._canvas.create_oval(
                cx + dx - 1, cy - 3, cx + dx + 1, cy - 1,
                fill="#333333", outline="",
            )

    def _update_visuals(self):
        pass  # Animation loop handles it

    def destroy(self):
        self._running = False
        if self._root:
            self._root.after(0, self._root.destroy)
