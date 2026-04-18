#!/usr/bin/env python3
"""
Generate resources/icon.ico — the application icon for STL Database.

Design: isometric 3D cube on a deep-navy rounded-rectangle background.
Triangulation lines on each face evoke the STL mesh format.

Run once (or whenever you want to regenerate the icon):
    python create_icon.py
"""
import math
from pathlib import Path

from PIL import Image, ImageDraw


# ---------------------------------------------------------------------------
# Colour palette
# ---------------------------------------------------------------------------
_BG          = (17,  24,  64, 255)   # deep navy background
_COL_TOP     = (96,  165, 250, 255)  # blue-400  — top face
_COL_LEFT    = (37,  99,  235, 255)  # blue-600  — left face
_COL_RIGHT   = (30,  64,  175, 255)  # blue-800  — right face
_COL_EDGE    = (148, 198, 255, 220)  # blue-300  — outer/inner edges
_COL_MESH    = (255, 255, 255, 100)  # white semi-transparent — STL triangles


def draw_icon(size: int) -> Image.Image:
    """Render the icon at *size* × *size* pixels and return an RGBA Image."""
    img  = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    s = size

    # --- Rounded-rectangle background ---
    r = max(2, s // 7)
    draw.rounded_rectangle([0, 0, s - 1, s - 1], radius=r, fill=_BG)

    # --- Isometric cube ---
    # The cube sits slightly below the visual centre so it looks balanced.
    cx = s * 0.500
    cy = s * 0.515
    a  = s * 0.295   # cube "arm" — half the cube's projected width / height

    c30 = math.cos(math.radians(30))   # ≈ 0.866
    s30 = 0.5                          # sin(30°)

    def pt(x: float, y: float) -> tuple[int, int]:
        return (round(x), round(y))

    # 7 visible vertices of the isometric cube
    vtop = pt(cx,          cy - a)          # top apex
    vr   = pt(cx + a*c30,  cy - a*s30)     # top-right
    vl   = pt(cx - a*c30,  cy - a*s30)     # top-left
    vm   = pt(cx,          cy)              # centre (bottom of top face)
    vbr  = pt(cx + a*c30,  cy + a*s30)     # bottom-right
    vbl  = pt(cx - a*c30,  cy + a*s30)     # bottom-left
    vbot = pt(cx,          cy + a)          # bottom apex

    # Draw the three visible faces
    draw.polygon([vtop, vr, vm, vl],     fill=_COL_TOP)
    draw.polygon([vl,   vm, vbot, vbl],  fill=_COL_LEFT)
    draw.polygon([vm,   vr, vbr,  vbot], fill=_COL_RIGHT)

    lw = max(1, round(s / 56))   # line weight scales with icon size

    # Triangulation / mesh lines (skip at tiny sizes to avoid clutter)
    if size >= 32:
        draw.line([vtop, vm],  fill=_COL_MESH, width=lw)  # top face
        draw.line([vl,   vr],  fill=_COL_MESH, width=lw)  # top face
        draw.line([vl,  vbot], fill=_COL_MESH, width=lw)  # left face diagonal
        draw.line([vr,  vbot], fill=_COL_MESH, width=lw)  # right face diagonal

    # Outer silhouette + inner shared edges
    draw.line([vtop, vr, vbr, vbot, vbl, vl, vtop], fill=_COL_EDGE, width=lw)
    draw.line([vtop, vm],  fill=_COL_EDGE, width=lw)
    draw.line([vl,   vm],  fill=_COL_EDGE, width=lw)
    draw.line([vr,   vm],  fill=_COL_EDGE, width=lw)
    draw.line([vm,  vbot], fill=_COL_EDGE, width=lw)

    return img


def main() -> None:
    out = Path(__file__).parent / "resources" / "icon.ico"
    sizes = [16, 32, 48, 64, 128, 256]

    images = [draw_icon(sz) for sz in sizes]

    # Save as a multi-size ICO: largest image is the primary entry,
    # the rest are appended so Windows can pick the best fit.
    base = images[-1]   # 256 × 256
    base.save(
        str(out),
        format="ICO",
        append_images=images[:-1],
    )
    print(f"Saved {out}  ({', '.join(str(s) for s in sizes)} px)")


if __name__ == "__main__":
    main()
