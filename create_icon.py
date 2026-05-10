#!/usr/bin/env python3
"""
Generate resources/icon.ico, resources/icon.png, and resources/icon.svg —
the application icon for STL Database.

Design: icosahedron (20-face polyhedron) on a deep-navy rounded-rectangle
background, evoking a 3D-printable geometric gem/crystal.

Run once (or whenever you want to regenerate the icon):
    python create_icon.py
"""
import math
from pathlib import Path

from PIL import Image, ImageDraw

# ---------------------------------------------------------------------------
# Icosahedron geometry
# ---------------------------------------------------------------------------
PHI = (1 + math.sqrt(5)) / 2

_VERTS_RAW = [
    (0,    1,    PHI),   # 0
    (0,   -1,    PHI),   # 1
    (0,    1,   -PHI),   # 2
    (0,   -1,   -PHI),   # 3
    (1,    PHI,  0  ),   # 4
    (-1,   PHI,  0  ),   # 5
    (1,   -PHI,  0  ),   # 6
    (-1,  -PHI,  0  ),   # 7
    (PHI,  0,    1  ),   # 8
    (-PHI, 0,    1  ),   # 9
    (PHI,  0,   -1  ),   # 10
    (-PHI, 0,   -1  ),   # 11
]

_FACES = [
    # top cap
    (0, 1, 8), (0, 8, 4), (0, 4, 5), (0, 5, 9), (0, 9, 1),
    # upper belt
    (1, 6, 8), (8, 6, 10), (8, 10, 4), (4, 10, 2), (4, 2, 5),
    (5, 2, 11), (5, 11, 9), (9, 11, 7), (9, 7, 1), (1, 7, 6),
    # bottom cap
    (3, 6, 7), (3, 7, 11), (3, 11, 2), (3, 2, 10), (3, 10, 6),
]


def _normalize(v):
    x, y, z = v
    n = math.sqrt(x * x + y * y + z * z)
    return (x / n, y / n, z / n)


_VERTS = [_normalize(v) for v in _VERTS_RAW]


def _rot_x(v, angle):
    x, y, z = v
    c, s = math.cos(angle), math.sin(angle)
    return (x, y * c - z * s, y * s + z * c)


def _rot_y(v, angle):
    x, y, z = v
    c, s = math.cos(angle), math.sin(angle)
    return (x * c + z * s, y, -x * s + z * c)


def _cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _get_rotated_verts():
    angle_x = math.radians(22)
    angle_y = math.radians(18)
    return [_rot_y(_rot_x(v, angle_x), angle_y) for v in _VERTS]


def _get_face_data(rotated, size):
    cx = size * 0.5
    cy = size * 0.5
    scale = size * 0.42
    fov = 3.5

    def project(v):
        x, y, z = v
        zp = z + fov
        px = cx + x / zp * fov * scale
        py = cy - y / zp * fov * scale
        return (px, py)

    proj = [project(v) for v in rotated]
    light_dir = _normalize((0.4, 0.7, 1.0))

    face_data = []
    for i, j, k in _FACES:
        va, vb, vc = rotated[i], rotated[j], rotated[k]
        ab = _sub(vb, va)
        ac = _sub(vc, va)
        normal = _normalize(_cross(ab, ac))

        if normal[2] < 0:
            continue

        avg_z = (va[2] + vb[2] + vc[2]) / 3
        diffuse = max(0.0, _dot(normal, light_dir))
        pa, pb, pc = proj[i], proj[j], proj[k]
        face_data.append((avg_z, diffuse, pa, pb, pc))

    face_data.sort(key=lambda x: x[0])
    return face_data


def _face_color(diffuse):
    dark  = (20,  52,  164)   # blue-800
    mid   = (59,  130, 246)   # blue-500
    light = (186, 220, 254)   # blue-200

    if diffuse < 0.5:
        t = diffuse * 2
        r = int(dark[0] + t * (mid[0] - dark[0]))
        g = int(dark[1] + t * (mid[1] - dark[1]))
        b = int(dark[2] + t * (mid[2] - dark[2]))
    else:
        t = (diffuse - 0.5) * 2
        r = int(mid[0] + t * (light[0] - mid[0]))
        g = int(mid[1] + t * (light[1] - mid[1]))
        b = int(mid[2] + t * (light[2] - mid[2]))
    return (r, g, b, 255)


def draw_icon(size: int) -> Image.Image:
    img  = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    bg = (17, 24, 64, 255)
    radius = max(2, size // 7)
    draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=bg)

    rotated   = _get_rotated_verts()
    face_data = _get_face_data(rotated, size)

    edge_color = (191, 219, 254, 210)
    lw = max(1, round(size / 80))

    for _z, diffuse, pa, pb, pc in face_data:
        draw.polygon([pa, pb, pc], fill=_face_color(diffuse))

    if size >= 32:
        for _z, _d, pa, pb, pc in face_data:
            draw.line([pa, pb, pc, pa], fill=edge_color, width=lw)

    return img


# ---------------------------------------------------------------------------
# SVG generation
# ---------------------------------------------------------------------------

def _color_hex(r, g, b):
    return f"#{r:02x}{g:02x}{b:02x}"


def generate_svg(size: int = 256) -> str:
    rotated   = _get_rotated_verts()
    face_data = _get_face_data(rotated, size)

    bg    = "#111840"
    edge  = "#bfdbfe"
    r     = size // 7

    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" '
        f'width="{size}" height="{size}">',
        f'  <rect width="{size}" height="{size}" rx="{r}" ry="{r}" fill="{bg}"/>',
    ]

    for _z, diffuse, pa, pb, pc in face_data:
        fc   = _face_color(diffuse)
        fill = _color_hex(fc[0], fc[1], fc[2])
        pts  = (
            f"{pa[0]:.2f},{pa[1]:.2f} "
            f"{pb[0]:.2f},{pb[1]:.2f} "
            f"{pc[0]:.2f},{pc[1]:.2f}"
        )
        lines.append(
            f'  <polygon points="{pts}" fill="{fill}" '
            f'stroke="{edge}" stroke-width="1.5" stroke-opacity="0.85"/>'
        )

    lines.append("</svg>")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    resources = Path(__file__).parent / "resources"
    resources.mkdir(exist_ok=True)

    sizes  = [16, 32, 48, 64, 128, 256]
    images = [draw_icon(sz) for sz in sizes]

    ico_path = resources / "icon.ico"
    images[-1].save(str(ico_path), format="ICO", append_images=images[:-1])
    print(f"Saved {ico_path}  ({', '.join(str(s) for s in sizes)} px)")

    png_path = resources / "icon.png"
    images[-1].save(str(png_path), format="PNG")
    print(f"Saved {png_path}")

    svg_path = resources / "icon.svg"
    svg_path.write_text(generate_svg(256), encoding="utf-8")
    print(f"Saved {svg_path}")


if __name__ == "__main__":
    main()
