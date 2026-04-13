"""Transparent overlay window showing recording/processing state."""
import tkinter as tk
import threading
import math


class RecordingOverlay:
    """Small floating circle overlay that shows recording state."""

    def __init__(self):
        self._root = None
        self._canvas = None
        self._thread = None
        self._running = False
        self._state = "hidden"  # hidden, recording, processing, listening
        self._animation_step = 0
        self._pulse_growing = True

    def show(self, state: str = "recording"):
        """Show overlay with given state. Thread-safe."""
        self._state = state
        if self._root and self._running:
            self._root.after(0, self._update_visuals)
            self._root.after(0, lambda: self._root.deiconify())
        elif not self._running:
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
            # Wait a bit for window to initialize
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

        size = 80
        self._root.geometry(f"{size}x{size}+20+20")

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
        cx, cy = 40, 40

        # Pulse animation
        self._animation_step += 1
        pulse = math.sin(self._animation_step * 0.15) * 0.15 + 1.0

        colors = {
            "recording": ("#ef4444", "#ff6b6b"),
            "processing": ("#f59e0b", "#fbbf24"),
            "listening": ("#22c55e", "#4ade80"),
        }
        color, light_color = colors.get(self._state, ("#3b82f6", "#60a5fa"))

        # Outer glow ring (pulsing)
        glow_r = int(30 * pulse)
        self._canvas.create_oval(
            cx - glow_r, cy - glow_r, cx + glow_r, cy + glow_r,
            fill="", outline=light_color, width=2,
        )

        # Inner solid circle
        r = 20
        self._canvas.create_oval(
            cx - r, cy - r, cx + r, cy + r,
            fill=color, outline="",
        )

        # Icon in center
        if self._state == "recording":
            # Mic shape
            self._canvas.create_rectangle(
                cx - 4, cy - 10, cx + 4, cy + 2,
                fill="white", outline="",
            )
            self._canvas.create_arc(
                cx - 8, cy - 4, cx + 8, cy + 8,
                start=0, extent=-180,
                outline="white", width=2, style="arc",
            )
            self._canvas.create_line(cx, cy + 8, cx, cy + 12, fill="white", width=2)
        elif self._state == "processing":
            # Spinning dots
            for i in range(3):
                angle = (self._animation_step * 0.2 + i * 2.1)
                dx = math.cos(angle) * 8
                dy = math.sin(angle) * 8
                self._canvas.create_oval(
                    cx + dx - 2, cy + dy - 2, cx + dx + 2, cy + dy + 2,
                    fill="white", outline="",
                )
        elif self._state == "listening":
            # Ear/listen icon (simple)
            self._canvas.create_text(cx, cy, text="👂", font=("Segoe UI Emoji", 14))

        self._root.after(50, self._animate)

    def _update_visuals(self):
        """Force a visual update."""
        pass  # Animation loop handles it

    def destroy(self):
        self._running = False
        if self._root:
            self._root.after(0, self._root.destroy)
