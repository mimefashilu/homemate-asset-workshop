"""Blend an AI hair-detail tile into an original UV texture conservatively."""
from __future__ import annotations

import argparse
from pathlib import Path
from PIL import Image, ImageChops, ImageEnhance, ImageFilter


def composite(original_path: Path, detail_path: Path, png_output: Path, bmp_output: Path, opacity: float):
    with Image.open(original_path) as source_image, Image.open(detail_path) as detail_image:
        source = source_image.convert("RGBA")
        detail = detail_image.convert("RGB").resize(source.size, Image.Resampling.LANCZOS)
        gray = detail.convert("L")
        blurred = gray.filter(ImageFilter.GaussianBlur(max(1.0, source.width / 128)))
        high_pass = ImageChops.subtract(gray, blurred, scale=1, offset=128)
        high_pass = ImageEnhance.Contrast(high_pass).enhance(1.35)
        high_pass = ImageEnhance.Brightness(high_pass).enhance(0.98)
        detail_rgb = Image.merge("RGB", (high_pass, high_pass, high_pass))
        detail_layer = Image.blend(Image.new("RGB", source.size, (128, 128, 128)), detail_rgb, 0.55)
        base = source.convert("RGB")
        blended = Image.blend(base, ImageChops.multiply(base, detail_layer), max(0.0, min(float(opacity), 0.25)))
        result = blended.convert("RGBA")
        if source.getchannel("A").getextrema() != (255, 255):
            result.putalpha(source.getchannel("A"))
        png_output.parent.mkdir(parents=True, exist_ok=True)
        bmp_output.parent.mkdir(parents=True, exist_ok=True)
        result.save(png_output, format="PNG")
        result.convert("RGB").save(bmp_output, format="BMP")
        print(f"PNG: {png_output}")
        print(f"BMP: {bmp_output}")
        print(f"Size: {result.width}x{result.height}; opacity={opacity}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original", required=True)
    parser.add_argument("--detail", required=True)
    parser.add_argument("--png-output", required=True)
    parser.add_argument("--bmp-output", required=True)
    parser.add_argument("--opacity", type=float, default=0.08)
    args = parser.parse_args()
    composite(Path(args.original), Path(args.detail), Path(args.png_output), Path(args.bmp_output), args.opacity)


if __name__ == "__main__":
    main()
