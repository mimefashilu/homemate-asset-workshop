"""Build portable model previews from the user's own game archives."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import shutil
import tempfile

from PIL import Image

from .formats import image_bytes
from .model_io import geometry
from .studio import atomic_json, digest


def build(studio, destination: Path):
    """Replace a preview cache only after a complete successful extraction."""
    source, entries, _ = studio.archive("AHM05_01.pp")
    before = digest(source)
    names = [e["name"] for e in entries if e["name"] in {"b_k_01.xx", "b_k_21.xx"}
             or (e["name"].startswith("g_k_") and e["name"].endswith(".xx"))]
    if not {"b_k_01.xx", "b_k_21.xx"}.issubset(names):
        raise ValueError("AHM05_01.pp 缺少已支持的脸部或刘海模型")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="preview-", dir=destination.parent) as temporary:
        output = Path(temporary) / "assets"
        (output / "original-models").mkdir(parents=True)
        models = []
        for name in sorted(names):
            data = studio.decoded(source.name, name).read_bytes()
            meshes, metadata = geometry(data)
            category = "face" if name == "b_k_01.xx" else "front" if name == "b_k_21.xx" else "back"
            identifier = Path(name).stem
            textures, texture_info = {}, []
            (output / identifier).mkdir()
            for texture in metadata["textures"]:
                target = output / identifier / (Path(texture["name"]).stem + ".png")
                with Image.open(io.BytesIO(image_bytes(data, texture))) as original:
                    image = original.convert("RGBA")
                    image.save(target)
                    transparent = image.getchannel("A").getextrema()[0] < 255
                info = {"url": "/assets/" + target.relative_to(output).as_posix(), "transparent": transparent}
                textures[texture["name"]] = info
                texture_info.append({"name": texture["name"], "width": texture["width"],
                                     "height": texture["height"], **info})
            materials = []
            for material in metadata["materials"]:
                main = textures.get(material["textures"][0])
                materials.append({"name": material["name"], "map": main["url"] if main else None,
                                  "transparent": main["transparent"] if main else False})
            atomic_json(output / f"{identifier}.json", {"name": name, "meshes": meshes, "materials": materials})
            (output / "original-models" / name).write_bytes(data)
            models.append({"id": identifier, "name": name, "category": category,
                           "url": f"/assets/{identifier}.json", "textures": texture_info,
                           "vertices": sum(len(m["positions"]) // 3 for m in meshes),
                           "triangles": sum(len(m["indices"]) // 3 for m in meshes),
                           "sha256": hashlib.sha256(data).hexdigest()})
        if digest(source) != before:
            raise ValueError("提取过程中资源包发生变化，请重新准备")
        manifest = {"archive": source.name, "source_sha256": before,
                    "source_size": source.stat().st_size, "models": models}
        atomic_json(output / "manifest.json", manifest)
        previous = Path(temporary) / "previous"
        if destination.exists():
            destination.replace(previous)
        try:
            shutil.move(str(output), str(destination))
        except Exception:
            if previous.exists():
                previous.replace(destination)
            raise
    return manifest
