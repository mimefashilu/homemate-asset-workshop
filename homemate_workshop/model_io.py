"""The verified XX v2 mesh layout, including offsets for bounded vertex edits."""
from __future__ import annotations

import struct
import numpy as np
from .formats import Reader, read_xx


def layout(data):
    reader = Reader(data)
    if len(data) < 26 or struct.unpack_from("<I", data)[0] != 2:
        raise ValueError("Only the verified XX v2 layout is supported")
    read_xx(data)
    reader.skip(26)
    frames = []

    def frame(parent, depth=0):
        if depth > 200:
            raise ValueError("Excessive frame depth")
        name = reader.name()
        children = reader.count()
        matrix = np.frombuffer(reader.take(64), "<f4").reshape(4, 4).astype(float)
        world = matrix @ parent
        reader.skip(16)
        count = reader.count()
        bounds_offset = reader.pos
        reader.skip(40)
        record = {"name": name, "world": world, "bounds_offset": bounds_offset, "meshes": []}
        if count:
            extra_uvs = reader.number("B")
            for _ in range(count):
                reader.skip(16)
                material = reader.number("i")
                indices = reader.count()
                if indices % 3:
                    raise ValueError("Invalid triangle index count")
                faces = np.frombuffer(reader.take(indices * 2), "<u2").reshape(-1, 3).copy()
                vertices = reader.count()
                offset = reader.pos
                reader.skip(vertices * 52)
                if faces.size and faces.max() >= vertices:
                    raise ValueError("Triangle references a missing vertex")
                record["meshes"].append({"material": material, "faces": faces,
                                         "offset": offset, "count": vertices})
                reader.skip(vertices * extra_uvs * 8 + 100)
            record["duplicate_count"] = reader.number("H")
            reader.skip(8)
            record["duplicate_offset"] = reader.pos
            reader.skip(record["duplicate_count"] * 52)
            for _ in range(reader.count()):
                reader.name()
                reader.skip(68)
        frames.append(record)
        for _ in range(children):
            frame(world, depth + 1)

    frame(np.eye(4))
    return frames


def vertex_vectors(data, offset, count, field):
    return np.ndarray((count, 3), "<f4", data, offset + field, (52, 4)).astype(float)


def geometry(data):
    metadata = read_xx(data)
    meshes = []
    for frame in layout(data):
        world = frame["world"]
        for number, mesh in enumerate(frame["meshes"]):
            count, offset = mesh["count"], mesh["offset"]
            positions = vertex_vectors(data, offset, count, 4)
            normals = vertex_vectors(data, offset, count, 32)
            uv = np.ndarray((count, 2), "<f4", data, offset + 44, (52, 4)).astype(float)
            if not all(np.isfinite(values).all() for values in (positions, normals, uv, world)):
                raise ValueError("Non-finite vertex data")
            positions = np.column_stack((positions, np.ones(count))) @ world
            normals = normals @ np.linalg.inv(world[:3, :3]).T
            normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-12)
            positions = positions[:, :3]
            # Reflect Z and winding together when moving from DirectX to Three.js.
            positions[:, 2] *= -1
            normals[:, 2] *= -1
            faces = mesh["faces"][:, [0, 2, 1]]
            meshes.append({"name": frame["name"], "submesh": number, "material": mesh["material"],
                           "positions": positions.flatten().tolist(), "normals": normals.flatten().tolist(),
                           "uv": uv.flatten().tolist(), "indices": faces.flatten().tolist()})
    return meshes, metadata
