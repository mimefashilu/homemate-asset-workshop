"""Conservative XX v2 geometry edits for the model workshop.

The editor only changes vertex positions, normals and frame bounds. Faces, UVs,
weights, bones, materials and embedded textures remain byte-identical.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import re
import struct
import uuid

import numpy as np

from .formats import read_xx
from .model_io import geometry, layout, vertex_vectors


HAIR_FRAME = re.compile(r"(?:b_p_kamiF(?:A)?\d{2}_A|g_p_kami[A-Za-z0-9_]+)")
EDITABLE_CATEGORIES = {"front", "back"}
OPERATIONS = {"smooth", "inflate", "contract"}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _welded_neighbors(positions: np.ndarray, faces: np.ndarray):
    unique, inverse = np.unique(np.round(positions, 5), axis=0, return_inverse=True)
    count = len(unique)
    groups = [np.flatnonzero(inverse == index) for index in range(count)]
    base = np.array([positions[group].mean(axis=0) for group in groups])
    welded_faces = inverse[faces]
    edges = Counter()
    neighbors = [set() for _ in range(count)]
    for face in welded_faces:
        for start, end in zip(face, np.roll(face, -1)):
            start, end = int(start), int(end)
            if start == end:
                continue
            edges[tuple(sorted((start, end)))] += 1
            neighbors[start].add(end)
            neighbors[end].add(start)
    boundary = [set() for _ in range(count)]
    for (start, end), incidence in edges.items():
        if incidence == 1:
            boundary[start].add(end)
            boundary[end].add(start)
    return base, inverse, neighbors, boundary


def _pinned_vertices(base, boundary):
    height = max(float(np.ptp(base[:, 1])), 0.01)
    pinned = base[:, 1] >= base[:, 1].max() - height * 0.16
    for index, adjacent in enumerate(boundary):
        if not adjacent:
            continue
        if len(adjacent) != 2:
            pinned[index] = True
            continue
        left, right = (base[neighbor] - base[index] for neighbor in adjacent)
        cosine = np.dot(left, right) / max(np.linalg.norm(left) * np.linalg.norm(right), 1e-12)
        if cosine > np.cos(np.deg2rad(45)):
            pinned[index] = True
    return pinned


def _smooth(positions, normals, faces, iterations, strength, max_displacement):
    if len(positions) < 4 or not len(faces):
        return positions.copy(), normals.copy()
    base, inverse, neighbors, boundary = _welded_neighbors(positions, faces)
    pinned = _pinned_vertices(base, boundary)
    points = base.copy()
    strength = float(np.clip(strength, 0.01, 0.8))
    for _ in range(int(np.clip(iterations, 1, 8))):
        delta = np.zeros_like(points)
        for index, adjacent in enumerate(neighbors):
            if adjacent and not pinned[index]:
                delta[index] = (points[list(adjacent)].mean(axis=0) - points[index]) * strength
        points += delta
    delta = points - base
    lengths = np.linalg.norm(delta, axis=1, keepdims=True)
    delta *= np.minimum(1.0, float(max_displacement) / np.maximum(lengths, 1e-12))
    updated = positions + delta[inverse]
    updated = _safe_displacement(positions, updated, faces)
    return updated, _recompute_normals(updated, faces, normals)


def _inflate(positions, normals, faces, amount, max_displacement):
    unit = normals / np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-12)
    delta = unit * float(amount)
    lengths = np.linalg.norm(delta, axis=1, keepdims=True)
    delta *= np.minimum(1.0, float(max_displacement) / np.maximum(lengths, 1e-12))
    updated = _safe_displacement(positions, positions + delta, faces)
    return updated, _recompute_normals(updated, faces, normals)


def _safe_displacement(original, updated, faces):
    old_faces = original[faces]
    old_cross = np.cross(old_faces[:, 1] - old_faces[:, 0], old_faces[:, 2] - old_faces[:, 0])
    valid = np.linalg.norm(old_cross, axis=1) > 1e-10
    for _ in range(12):
        new_faces = updated[faces]
        new_cross = np.cross(new_faces[:, 1] - new_faces[:, 0], new_faces[:, 2] - new_faces[:, 0])
        cosines = np.einsum("ij,ij->i", old_cross, new_cross)
        ratios = np.linalg.norm(new_cross, axis=1) / np.maximum(np.linalg.norm(old_cross, axis=1), 1e-12)
        if np.all(cosines[valid] > 0) and np.all(ratios[valid] > 0.25):
            return updated
        updated = original + (updated - original) * 0.5
    raise ValueError("修改会造成三角面翻转或过度压缩")


def _recompute_normals(positions, faces, original):
    cross = np.cross(positions[faces][:, 1] - positions[faces][:, 0], positions[faces][:, 2] - positions[faces][:, 0])
    sums = np.zeros_like(positions)
    for corner in range(3):
        np.add.at(sums, faces[:, corner], cross)
    sums /= np.maximum(np.linalg.norm(sums, axis=1, keepdims=True), 1e-12)
    blend = 0.35
    result = original * (1 - blend) + sums * blend
    result /= np.maximum(np.linalg.norm(result, axis=1, keepdims=True), 1e-12)
    return result


def _edit_mesh(positions, normals, faces, operation, parameters):
    max_displacement = float(parameters.get("max_displacement", 0.06))
    max_displacement = float(np.clip(max_displacement, 0.001, 0.12))
    if operation == "smooth":
        return _smooth(positions, normals, faces, parameters.get("iterations", 3), parameters.get("strength", 0.18), max_displacement)
    amount = float(parameters.get("amount", 0.03))
    amount = float(np.clip(abs(amount), 0.001, max_displacement))
    if operation == "contract":
        amount = -amount
    return _inflate(positions, normals, faces, amount, max_displacement)


def _allowed_frames(frame_names):
    if not frame_names or "*" in frame_names:
        return None
    return set(frame_names)


def apply_operation(data: bytes, operation: str, parameters: dict | None = None, target_frames=None):
    if operation not in OPERATIONS:
        raise ValueError(f"不支持的模型操作：{operation}")
    parameters = dict(parameters or {})
    selected = _allowed_frames(target_frames)
    metadata = read_xx(data)
    result = bytearray(data)
    reports = []
    allowed_bytes = np.zeros(len(data), dtype=bool)
    for frame in layout(data):
        frame_name = frame["name"]
        if not HAIR_FRAME.fullmatch(frame_name) or (selected is not None and frame_name not in selected):
            continue
        if not frame["meshes"]:
            continue
        duplicate_updates = {}
        points_for_bounds = []
        changed_in_frame = False
        for mesh in frame["meshes"]:
            offset, count = mesh["offset"], mesh["count"]
            positions = vertex_vectors(data, offset, count, 4)
            normals = vertex_vectors(data, offset, count, 32)
            points_for_bounds.append(positions)
            material_name = metadata["materials"][mesh["material"]]["name"]
            if material_name.endswith("_11") or count < 50:
                continue
            updated, updated_normals = _edit_mesh(positions, normals, mesh["faces"], operation, parameters)
            displacement = np.linalg.norm(updated - positions, axis=1)
            changed = float(displacement.max(initial=0.0)) > 1e-8
            if not changed:
                continue
            changed_in_frame = True
            points_for_bounds[-1] = updated
            for index in range(count):
                start = offset + index * 52
                position_bytes = struct.pack("<3f", *updated[index])
                normal_bytes = struct.pack("<3f", *updated_normals[index])
                result[start + 4:start + 16] = position_bytes
                result[start + 32:start + 44] = normal_bytes
                allowed_bytes[start + 4:start + 16] = True
                allowed_bytes[start + 32:start + 44] = True
                original = data[start:start + 52]
                update = (position_bytes, normal_bytes)
                if original in duplicate_updates and duplicate_updates[original] != update:
                    raise ValueError(f"重复顶点在 {frame_name} 中存在冲突")
                duplicate_updates[original] = update
            reports.append({"frame": frame_name, "vertices": count,
                            "changed_vertices": int(np.count_nonzero(displacement > 1e-8)),
                            "max_displacement": float(displacement.max(initial=0.0)),
                            "mean_displacement": float(displacement.mean())})
        if not changed_in_frame:
            continue
        for index in range(frame["duplicate_count"]):
            start = frame["duplicate_offset"] + index * 52
            update = duplicate_updates.get(data[start:start + 52])
            if update is None:
                continue
            result[start + 4:start + 16], result[start + 32:start + 44] = update
            allowed_bytes[start + 4:start + 16] = True
            allowed_bytes[start + 32:start + 44] = True
        points = np.concatenate(points_for_bounds)
        bounds = np.r_[points.min(axis=0), points.max(axis=0)]
        start = frame["bounds_offset"]
        result[start:start + 24] = struct.pack("<6f", *bounds)
        allowed_bytes[start:start + 24] = True

    candidate = bytes(result)
    _validate_candidate(data, candidate, metadata, allowed_bytes)
    return candidate, reports


def _validate_candidate(original, candidate, metadata, allowed_bytes):
    if read_xx(candidate) != metadata:
        raise ValueError("候选模型改变了材质或内嵌贴图")
    if len(candidate) != len(original):
        raise ValueError("候选模型大小发生变化")
    changed = np.frombuffer(original, dtype=np.uint8) != np.frombuffer(candidate, dtype=np.uint8)
    if np.any(changed & ~allowed_bytes):
        raise ValueError("候选模型修改了受保护的结构字段")
    for frame in layout(candidate):
        for mesh in frame["meshes"]:
            positions = vertex_vectors(candidate, mesh["offset"], mesh["count"], 4)
            normals = vertex_vectors(candidate, mesh["offset"], mesh["count"], 32)
            if not np.isfinite(positions).all() or not np.isfinite(normals).all():
                raise ValueError("候选模型包含非有限顶点或法线")


def model_frames(data: bytes):
    return [{"name": frame["name"], "meshes": [{"vertices": mesh["count"], "triangles": int(len(mesh["faces"]))} for mesh in frame["meshes"]]}
            for frame in layout(data) if frame["meshes"] and HAIR_FRAME.fullmatch(frame["name"])]


def create_candidate(model_record: dict, source: bytes, operation: str, parameters: dict, target_frames=None, workspace: Path | None = None, label: str | None = None, original_payload: dict | None = None):
    if original_payload is None:
        raise ValueError("A prepared original preview payload is required")
    if model_record.get("category") not in EDITABLE_CATEGORIES:
        raise ValueError("当前阶段只允许编辑刘海、侧发和后发模型")
    candidate, changes = apply_operation(source, operation, parameters, target_frames)
    if not changes:
        raise ValueError("没有产生可保存的几何变化")
    stamp = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d-%H%M%S")
    identifier = f"{stamp}-{operation}-{uuid.uuid4().hex[:8]}"
    root = (workspace or Path("workspace/models/candidates")) / identifier
    root.mkdir(parents=True, exist_ok=False)
    model_path = root / model_record["name"]
    model_path.write_bytes(candidate)
    meshes, _ = geometry(candidate)
    preview_path = root / "preview.json"
    preview_path.write_text(json.dumps({**original_payload, "meshes": meshes}, separators=(",", ":")), encoding="utf-8")
    report = {"operation": operation, "parameters": parameters, "target_frames": list(target_frames or ["*"]),
              "changes": changes, "max_displacement": max((item["max_displacement"] for item in changes), default=0.0),
              "changed_vertices": sum(item["changed_vertices"] for item in changes),
              "original_sha256": sha256(source), "candidate_sha256": sha256(candidate), "geometry_only": True}
    (root / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    record = {"id": identifier, "model": model_record["id"], "name": f"{label or operation} · {stamp}",
              "label": label or operation,
              "operation": operation, "parameters": parameters, "target_frames": list(target_frames or ["*"]),
              "model_path": str(model_path), "preview_path": str(preview_path), "report_path": str(root / "report.json"),
              "candidate_sha256": report["candidate_sha256"], "original_sha256": report["original_sha256"],
              "created_at": datetime.now(timezone.utc).isoformat(), "report": report}
    (root / "manifest.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return record


def list_candidates(workspace: Path | None = None):
    root = workspace or Path("workspace/models/candidates")
    records = []
    if not root.is_dir():
        return records
    for path in sorted(root.glob("*/manifest.json"), reverse=True):
        try:
            records.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return records
