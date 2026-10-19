"""Small synthetic PP/XX files with a simple curved hair grid."""
import io
from pathlib import Path
import struct

import numpy as np
from PIL import Image

from homemate_workshop.formats import header_transform, read_archive


def image_bytes(size=(8, 8), color=(70, 120, 180, 128), fmt="TGA"):
    stream = io.BytesIO()
    Image.new("RGBA", size, color).save(stream, fmt)
    return stream.getvalue()


def name(text):
    raw = text.encode("cp932") + b"\0"
    return struct.pack("<I", len(raw)) + bytes(v ^ 255 for v in raw)


def xx(frame_name="g_p_kamiA01_A"):
    side = 8
    points = [(x / 3, y / 3, 0.2 if 1 < x < 6 and 1 < y < 6 else 0) for y in range(side) for x in range(side)]
    faces = []
    for y in range(side - 1):
        for x in range(side - 1):
            a = y * side + x
            faces.extend((a, a + 1, a + side, a + 1, a + side + 1, a + side))
    data = bytearray(struct.pack("<I", 2) + bytes(22))
    data += name(frame_name) + struct.pack("<I", 0) + np.eye(4, dtype="<f4").tobytes() + bytes(16)
    data += struct.pack("<I", 1) + bytes(40) + bytes(1)
    data += bytes(16) + struct.pack("<iI", 0, len(faces)) + struct.pack("<" + "H" * len(faces), *faces)
    data += struct.pack("<I", len(points))
    for index, point in enumerate(points):
        vertex = bytearray(52)
        struct.pack_into("<I3f", vertex, 0, index, *point)
        struct.pack_into("<3f", vertex, 32, 0, 0, 1)
        struct.pack_into("<2f", vertex, 44, point[0] / 2, point[1] / 2)
        data += vertex
    data += bytes(100) + struct.pack("<H", 0) + bytes(8) + struct.pack("<I", 0)
    data += bytes(4) + struct.pack("<I", 1) + name("synthetic-material") + bytes(64) + struct.pack("<f", 1)
    for texture in ["hair.tga", "", "", ""]:
        data += name(texture) + bytes(16)
    data += bytes(88) + struct.pack("<I", 1)
    pixels = image_bytes()
    data += name("hair.tga") + bytes(4) + struct.pack("<7i", 8, 8, 0, 1, 21, 0, 1)
    data += struct.pack("<BI", sum(pixels[::32]) & 255, len(pixels)) + pixels + bytes(10)
    return bytes(data)


def pp(path, members):
    offset = len(members) * 268 + 9
    directory = bytearray()
    for filename, payload in members.items():
        directory += filename.encode("cp932").ljust(260, b"\0") + struct.pack("<II", len(payload), offset)
        offset += len(payload)
    payload = header_transform(b"\x03") + header_transform(struct.pack("<I", len(members)))
    payload += header_transform(directory) + header_transform(struct.pack("<I", len(directory) + 9))
    Path(path).write_bytes(payload + b"".join(members.values()))


def fake_native(argument):
    """Codec double for orchestration tests; real AHM encoding is tested privately."""
    argument = Path(argument)
    if argument.is_dir():
        pp(argument.with_suffix(".pp"), {p.name: p.read_bytes() for p in sorted(argument.iterdir())})
    else:
        target = argument.with_suffix("")
        target.mkdir(exist_ok=True)
        data = argument.read_bytes()
        for entry in read_archive(argument):
            (target / entry["name"]).write_bytes(data[entry["offset"]:entry["offset"] + entry["size"]])
