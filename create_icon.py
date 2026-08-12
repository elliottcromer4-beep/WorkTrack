"""
Generate the WorkTrack .ico file.
Design: dark rounded square, circular progress arc in accent blue,
clock hands, green pivot dot.
"""
import math

from PIL import Image, ImageDraw


def create_frame(size: int) -> Image.Image:
    s = size
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # ── Background rounded rectangle ──────────────────────────────────────
    pad = max(1, s // 14)
    cr = s // 5
    d.rounded_rectangle(
        [pad, pad, s - pad - 1, s - pad - 1],
        radius=cr,
        fill=(20, 21, 23, 255),
    )

    cx = cy = s / 2
    R = s * 0.30          # clock radius

    # ── Dial face ─────────────────────────────────────────────────────────
    ring_w = max(1, s // 36)
    d.ellipse(
        [cx - R, cy - R, cx + R, cy + R],
        fill=(30, 31, 35, 255),
        outline=(55, 58, 64, 255),
        width=ring_w,
    )

    # ── Progress arc (270° sweep, ~75% elapsed) in accent blue ────────────
    arc_w = max(2, s // 14)
    inset = arc_w / 2 + ring_w
    ab = [cx - R + inset, cy - R + inset, cx + R - inset, cy + R - inset]
    # Track (dim background arc)
    d.arc(ab, start=-90, end=270, fill=(40, 70, 110, 200), width=arc_w)
    # Progress (bright accent)
    d.arc(ab, start=-90, end=200, fill=(77, 171, 247, 255), width=arc_w)

    # ── Hour hand (pointing toward ~10) ───────────────────────────────────
    if s >= 24:
        ha = math.radians(-60)
        hx = cx + R * 0.40 * math.cos(ha)
        hy = cy + R * 0.40 * math.sin(ha)
        hw = max(1, s // 22)
        d.line([cx, cy, hx, hy], fill=(193, 194, 197, 230), width=hw)

    # ── Minute hand (pointing straight up / 12) ───────────────────────────
    ma = math.radians(-90)
    mx = cx + R * 0.58 * math.cos(ma)
    my = cy + R * 0.58 * math.sin(ma)
    mw = max(1, s // 30)
    d.line([cx, cy, mx, my], fill=(225, 230, 235, 255), width=mw)

    # ── Center pivot dot (green) ──────────────────────────────────────────
    dr = max(2, s // 18)
    d.ellipse(
        [cx - dr, cy - dr, cx + dr, cy + dr],
        fill=(105, 219, 124, 255),
    )

    # ── Top crown (stopwatch button) — only on larger sizes ───────────────
    if s >= 48:
        crown_w = max(2, s // 20)
        crown_h = max(2, s // 16)
        cx_i = int(cx)
        top = int(cy - R - ring_w)
        d.rectangle(
            [cx_i - crown_w, top - crown_h, cx_i + crown_w, top],
            fill=(77, 171, 247, 200),
        )

    return img


if __name__ == "__main__":
    from pathlib import Path

    out = Path(__file__).parent / "assets" / "worktrack.ico"
    out.parent.mkdir(exist_ok=True)

    sizes = [16, 24, 32, 48, 64, 128, 256]
    frames = [create_frame(s) for s in sizes]

    frames[0].save(
        str(out),
        format="ICO",
        sizes=[(s, s) for s in sizes],
        append_images=frames[1:],
    )
    print(f"Icon saved: {out}")

    # Also save a preview PNG
    preview = out.with_suffix(".png")
    frames[-1].save(str(preview))
    print(f"Preview: {preview}")
