"""Prepare HomeMate hair textures for Qwen PNG workflows and convert them back."""
from __future__ import annotations

import argparse
from io import BytesIO
import json
from pathlib import Path
import shutil
import sys

from PIL import Image

APP_DIR = Path(__file__).resolve().parent.parent
GAME_DIR = APP_DIR.parent.parent
sys.path.insert(0, str(APP_DIR.parent))
from homemate_workshop.formats import convert_image, image_bytes, read_xx
from homemate_workshop.studio import Studio


WORKSPACE = APP_DIR.parent / "workspace" / "hair-texture"
TEMPLATE = APP_DIR / "comfyui" / "Qwen_hair_texture_i2i.json"


def source_texture(game_dir: Path, archive: str, model: str, texture_name: str):
    studio = Studio(game_dir, WORKSPACE / "texture-cache")
    try:
        decoded = studio.decoded(archive, model)
        data = decoded.read_bytes()
        metadata = read_xx(data)
        texture = next((item for item in metadata["textures"] if item["name"] == texture_name), None)
        if texture is None:
            raise ValueError(f"模型中找不到贴图：{texture_name}")
        pixels = image_bytes(data, texture)
        return pixels, texture
    finally:
        studio.executor.shutdown(wait=True)


def prepare(args):
    output = WORKSPACE / f"{Path(args.model).stem}-{Path(args.texture).stem}"
    output.mkdir(parents=True, exist_ok=True)
    pixels, texture = source_texture(Path(args.game_dir), args.archive, args.model, args.texture)
    with Image.open(BytesIO(pixels)) as image:
        source = image.convert("RGBA")
        png_name = f"{Path(args.model).stem}_{Path(args.texture).stem}.png"
        png_path = output / png_name
        source.save(png_path, format="PNG")
        input_dir = Path(args.input_dir)
        input_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(png_path, input_dir / png_path.name)
        workflow = json.loads(TEMPLATE.read_text(encoding="utf-8"))
        workflow["470"]["inputs"]["image"] = png_path.name
        workflow["461"]["inputs"]["filename_prefix"] = f"HomeMate_HairTexture_{Path(args.model).stem}_{Path(args.texture).stem}"
        workflow_path = output / "Qwen_hair_texture_i2i.json"
        workflow_path.write_text(json.dumps(workflow, ensure_ascii=False, indent=2), encoding="utf-8")
        manifest = {"archive": args.archive, "model": args.model, "texture": args.texture,
                    "width": image.width, "height": image.height, "image_format": texture["image_format"],
                    "input_png": str(png_path), "comfy_input_png": str(input_dir / png_path.name),
                    "workflow": str(workflow_path), "target_extension": Path(args.texture).suffix}
        (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


def convert_back(args):
    source = Path(args.original)
    generated = Path(args.generated)
    destination = Path(args.output)
    original = source.read_bytes()
    generated_data = generated.read_bytes()
    encoded, size, mode = convert_image(generated_data, destination.suffix, alpha_source=original)
    with Image.open(BytesIO(generated_data)) as generated_image:
        with Image.open(BytesIO(original)) as original_image:
            if generated_image.size != original_image.size:
                raise ValueError(f"输出尺寸 {generated_image.size} 与原图 {original_image.size} 不一致，请先按原尺寸输出")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(encoded)
    print(json.dumps({"output": str(destination), "width": size[0], "height": size[1], "mode": mode,
                      "bytes": len(encoded)}, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument("--game-dir", required=True, help="游戏根目录")
    prepare_parser.add_argument("--archive", default="AHM05_01.pp")
    prepare_parser.add_argument("--model", required=True, help="例如 g_k_41.xx")
    prepare_parser.add_argument("--texture", required=True, help="例如 b_t_02_b_01.bmp")
    prepare_parser.add_argument("--input-dir", required=True, help="ComfyUI input 目录")
    prepare_parser.set_defaults(function=prepare)
    convert_parser = sub.add_parser("convert-back")
    convert_parser.add_argument("--original", required=True, help="原始 BMP/TGA")
    convert_parser.add_argument("--generated", required=True, help="ComfyUI 输出 PNG")
    convert_parser.add_argument("--output", required=True, help="目标 BMP/TGA")
    convert_parser.set_defaults(function=convert_back)
    args = parser.parse_args()
    args.function(args)


if __name__ == "__main__":
    main()
