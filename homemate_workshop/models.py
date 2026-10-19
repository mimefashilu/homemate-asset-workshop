"""Model catalog, candidate review and reversible archive installation."""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import shutil
import tempfile
import uuid

import numpy as np

from .formats import build_archive, exact, read_archive, read_xx, safe_name
from .model_io import geometry, layout
from .model_ops import HAIR_FRAME, create_candidate, list_candidates, model_frames, sha256
from .prepare import build
from .studio import atomic_json, digest


def verify_geometry(original: bytes, candidate: bytes):
    """Independently check that only permitted hair geometry bytes changed."""
    if len(original) != len(candidate) or read_xx(original) != read_xx(candidate):
        raise ValueError("候选大小、材质或贴图与原模型不一致")
    geometry(candidate)
    allowed = np.zeros(len(original), dtype=bool)
    for frame in layout(original):
        if not HAIR_FRAME.fullmatch(frame["name"]):
            continue
        start = frame["bounds_offset"]
        allowed[start:start + 24] = True
        blocks = [(mesh["offset"], mesh["count"]) for mesh in frame["meshes"]]
        if frame["meshes"]:
            blocks.append((frame["duplicate_offset"], frame["duplicate_count"]))
        for offset, count in blocks:
            for index in range(count):
                start = offset + index * 52
                allowed[start + 4:start + 16] = True
                allowed[start + 32:start + 44] = True
    changed = np.frombuffer(original, dtype=np.uint8) != np.frombuffer(candidate, dtype=np.uint8)
    if not changed.any() or (changed & ~allowed).any():
        raise ValueError("候选没有变化或修改了头发几何以外的字段")


def verify_package(source, target, replacements):
    old_entries, new_entries = read_archive(source), read_archive(target)
    if [(e["name"], e["size"]) for e in old_entries] != [(e["name"], e["size"]) for e in new_entries]:
        raise ValueError("重建资源包改变了成员列表或大小")
    with source.open("rb") as old, target.open("rb") as new:
        for before, after in zip(old_entries, new_entries):
            old.seek(before["offset"])
            new.seek(after["offset"])
            offset, remaining = 0, before["size"]
            while remaining:
                length = min(remaining, 1024 * 1024)
                expected = replacements[before["name"]][offset:offset + length] if before["name"] in replacements else exact(old, length)
                if exact(new, length) != expected:
                    raise ValueError("资源包出现非预期变化：" + before["name"])
                offset += length
                remaining -= length


class ModelWorkshop:
    def __init__(self, studio, workspace):
        self.studio = studio
        self.workspace = Path(workspace)
        self.assets = self.workspace / "assets"
        self.candidates = self.workspace / "candidates"

    def prepare(self):
        with self.studio.lock:
            return build(self.studio, self.assets)

    def manifest(self):
        path = self.assets / "manifest.json"
        if not path.is_file():
            return {"archive": "AHM05_01.pp", "models": [], "prepared": False}
        result = json.loads(path.read_text(encoding="utf-8"))
        result["prepared"] = True
        return result

    def catalog(self):
        manifest = self.manifest()
        candidates = list_candidates(self.candidates)
        for model in manifest["models"]:
            source = self.assets / "original-models" / safe_name(model["name"])
            model["frames"] = model_frames(source.read_bytes()) if source.is_file() else []
            model["candidates"] = [{**c, "url": f"/api/models/candidate/{c['id']}/preview"}
                                   for c in candidates if c["model"] == model["id"]]
        return manifest

    def model(self, identifier):
        return next((m for m in self.manifest()["models"] if m["id"] == identifier), None)

    def candidate(self, identifier):
        folder = self.candidates / safe_name(identifier)
        record = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        # Resolve against this workspace; moving the project never trusts old absolute paths.
        model = self.model(record["model"])
        if not model:
            raise ValueError("原始模型不在当前清单，请重新准备模型资源")
        return record, model, folder

    def create(self, payload):
        with self.studio.lock:
            record = self.model(payload.get("model"))
            if not record:
                raise ValueError("模型不存在，请先准备模型资源")
            label = payload.get("label")
            if label is not None and (not isinstance(label, str) or len(label) > 40):
                raise ValueError("候选名称必须是 40 字以内的文本")
            targets = payload.get("target_frames") or ["*"]
            if not isinstance(targets, list) or any(not isinstance(t, str) for t in targets):
                raise ValueError("目标网格必须为名称列表")
            parameters = payload.get("parameters") or {}
            if not isinstance(parameters, dict) or any(not isinstance(v, (int, float)) or not np.isfinite(v) for v in parameters.values()):
                raise ValueError("参数必须为有限数字")
            source = (self.assets / "original-models" / safe_name(record["name"])).read_bytes()
            preview = json.loads((self.assets / f"{record['id']}.json").read_text(encoding="utf-8"))
            return create_candidate(record, source, payload.get("operation"), parameters, targets,
                                    self.candidates, label, original_payload=preview)

    def preflight(self, identifier):
        with self.studio.lock:
            record, model, folder = self.candidate(identifier)
            original = (self.assets / "original-models" / safe_name(model["name"])).read_bytes()
            candidate = (folder / safe_name(model["name"])).read_bytes()
            checks = [{"name": "原始模型哈希", "ok": sha256(original) == record.get("original_sha256")},
                      {"name": "候选文件哈希", "ok": sha256(candidate) == record.get("candidate_sha256")}]
            for name, action in [
                ("结构保持校验", lambda: verify_geometry(original, candidate)),
                ("游戏当前模型", lambda: self._check_current(model, original)),
                ("游戏关闭状态", self.studio.game_closed),
            ]:
                try:
                    action()
                    checks.append({"name": name, "ok": True})
                except (ValueError, OSError) as error:
                    checks.append({"name": name, "ok": False, "detail": str(error)})
            for check in checks:
                check.setdefault("detail", check["name"] + ("通过" if check["ok"] else "不匹配"))
            return {"candidate": identifier, "ready": all(c["ok"] for c in checks), "checks": checks}

    def _check_current(self, model, original):
        archive = safe_name(self.manifest()["archive"])
        if self.studio.decoded(archive, model["name"]).read_bytes() != original:
            raise ValueError("游戏模型已变化，请重新准备预览并生成候选")

    def recommended(self):
        records = []
        existing = list_candidates(self.candidates)
        for model in self.manifest()["models"]:
            if model["category"] not in {"front", "back"}:
                continue
            parameters = {"strength": 0.15, "iterations": 2, "max_displacement": 0.03}
            match = next((c for c in existing if c["model"] == model["id"] and c.get("parameters") == parameters
                          and c.get("original_sha256") == model["sha256"] and c.get("operation") == "smooth"), None)
            records.append(match or self.create({"model": model["id"], "operation": "smooth", "parameters": parameters,
                                                 "label": "推荐轻度平滑"}))
        if not records:
            raise ValueError("请先准备模型资源")
        batch = {"candidate_ids": [r["id"] for r in records], "name": "推荐轻度平滑"}
        atomic_json(self.workspace / "recommended.json", batch)
        return batch

    def install(self, identifiers):
        """Write reviewed candidates using the texture module's backup/restore catalog."""
        if not isinstance(identifiers, list) or not identifiers:
            raise ValueError("请选择候选")
        studio = self.studio
        with studio.lock:
            studio.game_closed()
            archive = safe_name(self.manifest()["archive"])
            if any(r["archive"] == archive for r in studio.staged.values()):
                raise ValueError("此资源包还有待写入贴图，请先处理后再安装模型")
            plaintext = {}
            for identifier in identifiers:
                checked = self.preflight(identifier)
                if not checked["ready"]:
                    raise ValueError("；".join(c["detail"] for c in checked["checks"] if not c["ok"]))
                record, model, folder = self.candidate(identifier)
                if model["name"] in plaintext:
                    raise ValueError("同一模型只能选择一个候选")
                plaintext[model["name"]] = (folder / model["name"]).read_bytes()
            source, entries, _ = studio.archive(archive)
            before = digest(source)
            identifier = datetime.now().strftime("%Y%m%d-%H%M%S-model-") + uuid.uuid4().hex[:8]
            backup_dir = studio.workspace / "backups" / identifier
            backup_dir.mkdir()
            target = source.parent / (".workshop-model-" + uuid.uuid4().hex + ".pp")
            installed = False
            receipt = {}
            try:
                with tempfile.TemporaryDirectory(dir=studio.workspace / "work") as temporary:
                    work = Path(temporary)
                    incoming = work / "incoming"
                    incoming.mkdir()
                    for name, data in plaintext.items():
                        (incoming / name).write_bytes(data)
                    studio.native(incoming)
                    packed = work / "incoming.pp"
                    encoded = read_archive(packed)
                    if {e["name"] for e in encoded} != set(plaintext):
                        raise ValueError("编码改变了成员列表")
                    with packed.open("rb") as stream:
                        replacements = {}
                        for entry in encoded:
                            stream.seek(entry["offset"])
                            replacements[entry["name"]] = exact(stream, entry["size"])
                    verification = work / "verification.pp"
                    shutil.copy2(packed, verification)
                    studio.native(verification)
                    for name, data in plaintext.items():
                        if (work / "verification" / name).read_bytes() != data:
                            raise ValueError("原生编码/解码往返不一致")
                    build_archive(source, entries, replacements, target)
                    verify_package(source, target, replacements)
                if digest(source) != before:
                    raise ValueError("准备安装期间资源包发生变化")
                shutil.copy2(source, backup_dir / archive)
                if digest(backup_dir / archive) != before:
                    raise ValueError("备份哈希不匹配")
                receipt = {"id": identifier, "kind": "models", "status": "prepared", "candidates": identifiers,
                           "files": [{"archive": archive, "original": before, "patched": digest(target)}]}
                atomic_json(backup_dir / "manifest.json", receipt)
                studio.game_closed()
                if digest(source) != before:
                    raise ValueError("安装前资源包发生变化")
                target.replace(source)
                installed = True
                if digest(source) != receipt["files"][0]["patched"]:
                    raise ValueError("安装后的资源包哈希不匹配")
                receipt["status"] = "installed"
                atomic_json(backup_dir / "manifest.json", receipt)
                return {"count": len(plaintext), "backup": identifier, "archives": [archive]}
            except Exception:
                if installed and digest(source) == receipt["files"][0]["patched"]:
                    shutil.copy2(backup_dir / archive, target)
                    target.replace(source)
                    receipt["status"] = "rolled_back"
                    atomic_json(backup_dir / "manifest.json", receipt)
                raise
            finally:
                target.unlink(missing_ok=True)
