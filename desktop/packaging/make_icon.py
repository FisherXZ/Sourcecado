"""Render the Sourcecado app icon from the DESIGN.md palette.

The mark is an avocado cross-section, which DESIGN.md names directly as "the
avocado-pit motif": avocado rind, pale flesh, terracotta pit. Every colour here
is quoted from DESIGN.md rather than chosen, so the icon and the product agree.

Drawn at 8x and downsampled, because a 32px icon drawn directly has no room to
antialias an ellipse edge.
"""

from pathlib import Path

from PIL import Image, ImageDraw

RIND = (91, 140, 42, 255)  # accent avocado  #5B8C2A
RIND_DEEP = (62, 94, 28, 255)  # accent deep     #3E5E1C
FLESH = (235, 241, 223, 255)  # accent tint     #EBF1DF
PIT = (194, 112, 61, 255)  # pit terracotta  #C2703D

SS = 8  # supersample factor


def render(size: int) -> Image.Image:
    n = size * SS
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Rounded-square rind. macOS masks its own corners, but Windows and Linux
    # do not, so the shape has to carry them.
    radius = int(n * 0.225)
    d.rounded_rectangle([0, 0, n - 1, n - 1], radius=radius, fill=RIND)

    # A darker inset ring reads as rind thickness at large sizes and simply
    # deepens the edge at 32px, which is the size that has to survive.
    inset = int(n * 0.085)
    d.rounded_rectangle(
        [inset, inset, n - 1 - inset, n - 1 - inset],
        radius=int(radius * 0.8),
        outline=RIND_DEEP,
        width=max(1, int(n * 0.018)),
    )

    # Flesh: a vertical ellipse, the avocado half seen face on.
    cx, cy = n / 2, n / 2
    fw, fh = n * 0.46, n * 0.58
    d.ellipse([cx - fw / 2, cy - fh / 2, cx + fw / 2, cy + fh / 2], fill=FLESH)

    # Pit: sits slightly low, the way it does in a real half.
    pr = n * 0.155
    pcy = cy + n * 0.045
    d.ellipse([cx - pr, pcy - pr, cx + pr, pcy + pr], fill=PIT)

    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    out = Path(__file__).resolve().parents[1] / "surfaces/gui/src-tauri/icons"
    master = render(1024)
    master.save(out / "icon-source.png")
    master.save(out / "icon.png")

    for name, size in (
        ("32x32.png", 32),
        ("64x64.png", 64),
        ("128x128.png", 128),
        ("128x128@2x.png", 256),
        ("Square30x30Logo.png", 30),
        ("Square44x44Logo.png", 44),
        ("Square71x71Logo.png", 71),
        ("Square89x89Logo.png", 89),
        ("Square107x107Logo.png", 107),
        ("Square142x142Logo.png", 142),
        ("Square150x150Logo.png", 150),
        ("Square284x284Logo.png", 284),
        ("Square310x310Logo.png", 310),
        ("StoreLogo.png", 50),
    ):
        render(size).save(out / name)

    # .ico carries its own size ladder.
    render(256).save(
        out / "icon.ico",
        sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )

    # .icns is built by iconutil from an .iconset directory.
    iconset = out / "sourcecado.iconset"
    iconset.mkdir(exist_ok=True)
    for base in (16, 32, 128, 256, 512):
        render(base).save(iconset / f"icon_{base}x{base}.png")
        render(base * 2).save(iconset / f"icon_{base}x{base}@2x.png")

    print("rendered")


if __name__ == "__main__":
    main()
