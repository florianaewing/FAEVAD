#!/usr/bin/env python3
"""Instagram-shaped copies of pieces whose shape Instagram won't accept.

Instagram only takes feed images from 4:5 (portrait) to 1.91:1 (landscape),
and rejects anything taller or wider. Rather than crop the art, an
out-of-range piece is centered on a white mat, widened or heightened just
enough to reach the nearest allowed shape. The copy keeps the web image's
longest side (500px), so it's no better for printing than the site's own
image. Every other platform gets the normal image.

Copies live at images/instagram/<name>.jpg so Buffer can fetch them from
the site. Run this script after adding pieces to _data/artwork.yml, then
commit the new files:

    social/bot/.venv/bin/python social/bot/instagram_images.py
"""

import math
from pathlib import Path

import yaml
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[2]
ARTWORK_PATH = REPO_ROOT / "_data" / "artwork.yml"
INSTAGRAM_DIR = Path("images") / "instagram"

MIN_RATIO = 4 / 5
MAX_RATIO = 1.91
MAT_COLOR = (255, 255, 255)
JPEG_QUALITY = 90


def needs_mat(image_path: Path) -> bool:
    with Image.open(image_path) as im:
        width, height = im.size
    return not MIN_RATIO <= width / height <= MAX_RATIO


def instagram_image(piece_image: str) -> str:
    """The repo-relative path of a piece's Instagram copy (it may not exist yet)."""
    return str(INSTAGRAM_DIR / f"{Path(piece_image).stem}.jpg")


def make_matted_copy(source: Path, dest: Path) -> None:
    with Image.open(source) as im:
        # Flatten any transparency onto the mat rather than letting it go black.
        art = Image.new("RGB", im.size, MAT_COLOR)
        art.paste(im.convert("RGBA"), mask=im.convert("RGBA"))
    width, height = art.size
    # Round the mat up, so the result never lands just outside the range.
    if width / height < MIN_RATIO:
        canvas_size = (math.ceil(height * MIN_RATIO), height)
    else:
        canvas_size = (width, math.ceil(width / MAX_RATIO))
    canvas = Image.new("RGB", canvas_size, MAT_COLOR)
    canvas.paste(art, ((canvas_size[0] - width) // 2, (canvas_size[1] - height) // 2))
    dest.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(dest, "JPEG", quality=JPEG_QUALITY)


def main() -> None:
    with open(ARTWORK_PATH) as f:
        artwork = yaml.safe_load(f)
    for collection in artwork["collections"]:
        for piece in collection["pieces"]:
            source = REPO_ROOT / piece["image"]
            dest = REPO_ROOT / instagram_image(piece["image"])
            if needs_mat(source):
                make_matted_copy(source, dest)
                print(f"made {dest.relative_to(REPO_ROOT)} for {piece['id']}")


if __name__ == "__main__":
    main()
