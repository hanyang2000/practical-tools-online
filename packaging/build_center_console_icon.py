"""Build the Windows center-console icon from the checked-in source PNG."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "packaging" / "windows" / "installer" / "CenterConsoleIconSource.png"
PNG_OUTPUT = ROOT / "packaging" / "windows" / "installer" / "CenterConsoleIcon.png"
ICO_OUTPUT = ROOT / "packaging" / "windows" / "installer" / "CenterConsole.ico"


def main() -> None:
    source = Image.open(SOURCE).convert("RGBA")
    side = min(source.size)
    left = (source.width - side) // 2
    top = (source.height - side) // 2
    source = source.crop((left, top, left + side, top + side))

    # The generated source uses black outside the rounded app tile. Replace it
    # with a precise alpha mask so Windows does not show black corner pixels.
    scale = 4
    mask = Image.new("L", (side * scale, side * scale), 0)
    drawer = ImageDraw.Draw(mask)
    radius = int(side * 0.145 * scale)
    drawer.rounded_rectangle((0, 0, side * scale - 1, side * scale - 1), radius=radius, fill=255)
    mask = mask.resize((side, side), Image.Resampling.LANCZOS)
    source.putalpha(mask)

    icon = source.resize((256, 256), Image.Resampling.LANCZOS)
    icon.save(PNG_OUTPUT, optimize=True)
    icon.save(
        ICO_OUTPUT,
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print(PNG_OUTPUT)
    print(ICO_OUTPUT)


if __name__ == "__main__":
    main()
