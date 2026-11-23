"""Turn an uploaded photo into the PNG the app stores and uses as a reference.

Accepts PNG, JPEG, GIF and WebP (checked by decoding, not by name or type):

- an animated GIF or WebP gives its first frame;
- the EXIF orientation is applied, so a phone photo is the right way up;
- all metadata is dropped - EXIF, including a phone's GPS position, ICC
  profiles, text chunks - because the PNG is written from pixels alone;
- transparency is flattened onto white, since references are used as RGB;
- the long side is scaled down to MAX_SIDE; references are scaled to about
  1 MP for generation anyway.
"""

from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

ACCEPTED_FORMATS = {"PNG", "JPEG", "GIF", "WEBP"}
ACCEPTED_TYPES = ("image/png", "image/jpeg", "image/gif", "image/webp")
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_SIDE = 2048
# Refuse decompression bombs well before Pillow's own limit: no camera photo
# a person attaches is anywhere near this.
MAX_PIXELS = 60_000_000


class ReferenceError(ValueError):
    """The upload cannot be used; the message is fit to show the user."""


@dataclass(frozen=True)
class NormalizedImage:
    png: bytes
    width: int
    height: int
    source_format: str


def normalize(raw: bytes) -> NormalizedImage:
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ReferenceError(f"The image is {len(raw) / 1e6:.1f} MB; the limit is "
                             f"{MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
    try:
        with Image.open(BytesIO(raw)) as image:
            source_format = image.format or ""
            if source_format not in ACCEPTED_FORMATS:
                raise ReferenceError("Use a PNG, JPEG, GIF or WebP image.")
            if image.width * image.height > MAX_PIXELS:
                raise ReferenceError("The image is too large (more than 60 megapixels).")
            image.seek(0)  # the first frame of an animation
            image = ImageOps.exif_transpose(image)
            image = _flatten(image)
            image.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
            out = BytesIO()
            # Written from pixels only: no exif=, no pnginfo=, no icc_profile=.
            image.save(out, format="PNG", optimize=True)
            return NormalizedImage(out.getvalue(), image.width, image.height, source_format)
    except ReferenceError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ReferenceError("This file is not an image that can be read.") from exc


def _flatten(image: Image.Image) -> Image.Image:
    """RGB, with any transparency composited onto white."""
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.getchannel("A"))
        return background
    return image.convert("RGB")
