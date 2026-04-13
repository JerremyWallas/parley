"""Unified Parley icon — speech bubble with sound waves."""
from PIL import Image, ImageDraw


def create_parley_icon(size: int = 64, color: str = "#3b82f6") -> Image.Image:
    """Create the Parley speech bubble icon at any size and color."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Scale factor
    s = size / 64

    # Speech bubble body
    draw.rounded_rectangle(
        [int(4 * s), int(6 * s), int(48 * s), int(42 * s)],
        radius=int(10 * s),
        fill=color,
    )

    # Speech bubble tail (triangle pointing down-left)
    draw.polygon([
        (int(10 * s), int(42 * s)),
        (int(6 * s), int(54 * s)),
        (int(24 * s), int(42 * s)),
    ], fill=color)

    # Sound waves (3 arcs radiating from bubble)
    wave_color = "white"
    cx = int(52 * s)
    cy = int(24 * s)
    for i, offset in enumerate([0, 8, 16]):
        r = int((10 + offset) * s)
        w = max(1, int(2 * s))
        alpha = 255 - i * 60
        draw.arc(
            [cx - r, cy - r, cx + r, cy + r],
            start=-45, end=45,
            fill=wave_color, width=w,
        )

    # Three dots inside bubble (conversation)
    dot_r = int(3 * s)
    dot_y = int(24 * s)
    for dx in [-10, 0, 10]:
        dot_cx = int(26 * s) + int(dx * s)
        draw.ellipse(
            [dot_cx - dot_r, dot_y - dot_r, dot_cx + dot_r, dot_y + dot_r],
            fill="white",
        )

    return img


def create_tray_icon(color: str = "#3b82f6") -> Image.Image:
    """Create tray icon at high resolution for sharp display on high-DPI screens."""
    return create_parley_icon(128, color)
