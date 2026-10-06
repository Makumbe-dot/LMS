"""Cut the brand artwork the app uses out of the logo as supplied.

    python scripts/make_brand_assets.py [path/to/logo.jpg]

The logo arrives as one square JPEG on a white ground. The app needs it in four
shapes, and all four are cropped from that one file so they cannot drift apart:

    frontend/src/brand/zinmad-logo.png   the whole logo, for the sign-in card
    frontend/src/brand/zinmad-mark.png   the ZM monogram and its swoosh, for the sidebar
    frontend/src/brand/favicon.png       the monogram on a white tile, for the browser tab
    backend/branding/zinmad-mark.png     the monogram on white, for PDF statements and
                                         the loan agreement (settings.STATEMENT_LOGO)

The favicon lives in src/, not public/: Django serves only the build's hashed
assets/ folder, so index.html links it as a source file and Vite puts it there.

Nothing is redrawn or recoloured. The white ground is lifted off (each pixel
becomes the colour and opacity that, laid back over white, gives the original),
so the artwork sits cleanly on any light surface; it is always shown on white,
because the black of the Z disappears on anything dark.

The crop boxes are the artwork's own bounds in the 1254 x 1254 original. If the
logo is ever reissued at another size or layout, re-measure them.
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "frontend" / "src" / "brand" / "zinmad-capital-logo.jpg"

# (left, top, right, bottom) in the original, with a little air around the ink.
WHOLE = (136, 173, 1118, 1097)   # monogram, name, "Private Limited" and the strapline
MARK = (214, 175, 1036, 658)     # the ZM and the swoosh under it


def lift_white(image: Image.Image) -> Image.Image:
    """White becomes transparent; everything else keeps its look over white."""
    rgb = image.convert("RGB")
    out = Image.new("RGBA", rgb.size)
    src, dst = rgb.load(), out.load()
    for y in range(rgb.height):
        for x in range(rgb.width):
            r, g, b = src[x, y]
            alpha = 255 - min(r, g, b)
            if alpha < 10:            # JPEG noise in the white ground
                dst[x, y] = (255, 255, 255, 0)
                continue
            a = alpha / 255
            dst[x, y] = (round((r - 255 * (1 - a)) / a), round((g - 255 * (1 - a)) / a),
                         round((b - 255 * (1 - a)) / a), alpha)
    return out


def fit(image: Image.Image, width: int) -> Image.Image:
    height = round(image.height * width / image.width)
    return image.resize((width, height), Image.LANCZOS)


def on_white(image: Image.Image, size: tuple[int, int], pad: int) -> Image.Image:
    """The artwork centred on an opaque white ground of `size`."""
    ground = Image.new("RGB", size, "white")
    room = (size[0] - 2 * pad, size[1] - 2 * pad)
    scale = min(room[0] / image.width, room[1] / image.height)
    art = image.resize((round(image.width * scale), round(image.height * scale)), Image.LANCZOS)
    ground.paste(art, ((size[0] - art.width) // 2, (size[1] - art.height) // 2), art)
    return ground


def main() -> None:
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else SOURCE
    original = Image.open(source).convert("RGB")
    if original.size != (1254, 1254):
        raise SystemExit(f"{source} is {original.size}, not 1254 x 1254: re-measure the crop boxes")
    if source.resolve() != SOURCE.resolve():
        SOURCE.parent.mkdir(parents=True, exist_ok=True)
        SOURCE.write_bytes(source.read_bytes())

    whole = lift_white(original.crop(WHOLE))
    mark = lift_white(original.crop(MARK))
    brand = ROOT / "frontend" / "src" / "brand"

    # The browser-tab tile gets rounded corners; a square white block looks pasted on.
    tile = on_white(mark, (96, 96), 7).convert("RGBA")
    corners = Image.new("L", tile.size, 0)
    ImageDraw.Draw(corners).rounded_rectangle((0, 0, tile.width - 1, tile.height - 1), 20, fill=255)
    tile.putalpha(corners)

    outputs = {
        brand / "zinmad-logo.png": fit(whole, 640),
        brand / "zinmad-mark.png": fit(mark, 360),
        brand / "favicon.png": tile,
        ROOT / "backend" / "branding" / "zinmad-mark.png": on_white(
            mark, (round(240 * mark.width / mark.height), 240), 0),
    }

    for path, image in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path, optimize=True)
        print(f"{path.relative_to(ROOT)}  {image.width} x {image.height}  {path.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
