"""Test the actual frozen application using a restricted environment and private copies."""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from urllib.parse import urlencode
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from homemate_workshop import __version__
from homemate_workshop.formats import build_archive, read_archive


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def get(url):
    return json.load(urllib.request.urlopen(url, timeout=180))


def post(base, path, token, payload):
    request = urllib.request.Request(base + path, json.dumps(payload).encode("utf-8"),
                {"Content-Type": "application/json", "X-Studio-Token": token})
    return json.load(urllib.request.urlopen(request, timeout=300))


def job(base, token, action, **payload):
    identifier = post(base, "/api/textures/job", token, {"action": action, **payload})["job"]
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        result = get(base + "/api/textures/job/" + identifier)
        if result["status"] == "error":
            raise AssertionError(result["message"])
        if result["status"] == "done":
            return result["result"]
        time.sleep(.2)
    raise TimeoutError("Frozen app job did not complete")


def wait_ready(data_dir, process):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        record = data_dir / "workspace/server.json"
        if record.is_file():
            try:
                data = json.loads(record.read_text(encoding="utf-8"))
                get(data["url"] + "/api/health")
                return data["url"]
            except (OSError, ValueError):
                pass
        if process.poll() is not None:
            log = data_dir / "logs/launcher.log"
            raise AssertionError(log.read_text(encoding="utf-8") if log.is_file() else f"EXE exited: {process.returncode}")
        time.sleep(.2)
    raise TimeoutError("Frozen app startup timed out")


def stop(base, token, process):
    post(base, "/api/shutdown", token, {})
    assert process.wait(timeout=30) == 0, "Frozen app did not stop cleanly"


def check_archive(path):
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        manifest = json.loads(archive.read("HomeMateWorkshop/portable-manifest.json"))
        assert len(names) == len(manifest["files"]) + 1
        for relative, expected in manifest["files"].items():
            actual = hashlib.sha256(archive.read("HomeMateWorkshop/" + relative)).hexdigest()
            assert actual == expected, relative
        assert all(not any(part.casefold() in {"userdata", "workspace", ".git", "backups", "candidates"}
                           for part in Path(name).parts) for name in names)
        assert not any(name.lower().endswith((".pp", ".xx", ".log", "ppextractor0.6.2.exe", "settings.json")) for name in names)
        for required in ["素材工坊.exe", "LICENSE.txt", "使用说明.txt", "licenses/Python.txt", "licenses/Tcl-Tk.txt"]:
            assert "HomeMateWorkshop/" + required in names
        return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zip", type=Path, default=ROOT / "dist" / f"homemate-asset-workshop-v{__version__}-windows-x64-portable.zip")
    parser.add_argument("--game-dir", type=Path, required=True, help="只读取此目录，所有写入使用临时副本")
    args = parser.parse_args()
    manifest = check_archive(args.zip)
    output = ROOT / "workspace/portable-check"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="便携 空格-", dir=output) as temporary:
        root = Path(temporary)
        with zipfile.ZipFile(args.zip) as archive:
            archive.extractall(root)
        program = root / "HomeMateWorkshop/素材工坊.exe"
        environment = dict(os.environ)
        for name in ["PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "TCL_LIBRARY", "TK_LIBRARY"]:
            environment.pop(name, None)
        system = Path(os.environ["SystemRoot"])
        environment["PATH"] = str(system / "System32") + os.pathsep + str(system)
        environment["PYTHONNOUSERSITE"] = "1"
        flags = {"env": environment, "cwd": root, "creationflags": subprocess.CREATE_NO_WINDOW}
        diagnostic_data = root / "diagnostics"
        result = subprocess.run([str(program), "--diagnostics", "--data-dir", str(diagnostic_data)], timeout=30, **flags)
        assert result.returncode == 0
        diagnostics = json.loads((diagnostic_data / "diagnostics.json").read_text(encoding="utf-8"))
        assert diagnostics["frozen"] and diagnostics["required_files_present"]
        gui_data = root / "first-run-ui"
        result = subprocess.run([str(program), "--gui-check", "--no-browser", "--data-dir", str(gui_data)], timeout=30, **flags)
        assert result.returncode == 0
        gui = json.loads((gui_data / "gui-check.json").read_text(encoding="utf-8"))
        assert gui["passed"] and not gui["start_enabled"], gui
        shutil.copy2(gui_data / "launcher.png", output / "launcher-first-run.png")
        source_data = args.game_dir.resolve() / "data"
        game = root / "游戏 副本"
        (game / "data").mkdir(parents=True)
        selections = {"AHM05_01.pp": {"b_k_01.xx", "b_k_21.xx", "g_k_41.xx"},
                      "AHM07_00.pp": {"b_t_01_a_01.bmp", "b_t_40_a_01.tga"}}
        real_hashes = {}
        copy_hashes = {}
        for archive_name, selected in selections.items():
            source = source_data / archive_name
            real_hashes[archive_name] = file_hash(source)
            entries = [e for e in read_archive(source) if e["name"] in selected]
            assert {e["name"] for e in entries} == selected
            target = game / "data" / archive_name
            build_archive(source, entries, {}, target)
            copy_hashes[archive_name] = file_hash(target)
        tool = root / "解包 工具" / "PPExtractor0.6.2.exe"
        tool.parent.mkdir()
        shutil.copy2(source_data / "PPExtractor0.6.2.exe", tool)
        ready_gui_data = root / "ready-ui"
        result = subprocess.run([str(program), "--gui-check", "--no-browser", "--port", "0", "--data-dir", str(ready_gui_data),
                                 "--game-dir", str(game), "--extractor", str(tool)], timeout=30, **flags)
        assert result.returncode == 0
        ready_gui = json.loads((ready_gui_data / "gui-check.json").read_text(encoding="utf-8"))
        assert ready_gui["passed"] and ready_gui["service_running"], ready_gui
        assert (ready_gui_data / "settings.json").is_file()
        assert not (ready_gui_data / "workspace/server.json").exists()
        shutil.copy2(ready_gui_data / "launcher.png", output / "launcher-ready.png")
        data_dir = root / "userdata"
        process = subprocess.Popen([str(program), "--serve", "--no-browser", "--port", "0", "--game-dir", str(game),
                                    "--extractor", str(tool), "--data-dir", str(data_dir)], **flags)
        base = None
        try:
            base = wait_ready(data_dir, process)
            state = get(base + "/api/workshop")
            token = state["token"]
            assert state["portable"] and state["extractor_available"]
            for path in ["/", "/textures/", "/models/", "/guide/", "/vendor/three.module.js"]:
                assert urllib.request.urlopen(base + path).status == 200
            images = get(base + "/api/textures/images?" + urlencode({"archive": "AHM07_00.pp"}))
            texture = next(t for t in images if t["name"] == "b_t_40_a_01.tga")
            from PIL import Image
            payload = io.BytesIO()
            Image.new("RGB", (texture["width"], texture["height"]), (37, 119, 153)).save(payload, "PNG")
            post(base, "/api/textures/import", token, {"id": texture["id"], "data": base64.b64encode(payload.getvalue()).decode(), "preserve_alpha": True})
            applied = job(base, token, "apply")
            assert file_hash(game / "data/AHM07_00.pp") != copy_hashes["AHM07_00.pp"]
            job(base, token, "restore", backup=applied["backup"])
            assert file_hash(game / "data/AHM07_00.pp") == copy_hashes["AHM07_00.pp"]
            models = post(base, "/api/models/prepare", token, {})
            assert len(models["models"]) == 3
            candidate = post(base, "/api/models/candidate", token, {"model": "g_k_41", "operation": "smooth",
                                "parameters": {"strength": .15, "iterations": 2, "max_displacement": .03}})
            assert get(base + f"/api/models/candidate/{candidate['id']}/preflight")["ready"]
            installed = post(base, "/api/models/install", token, {"candidates": [candidate["id"]]})
            assert file_hash(game / "data/AHM05_01.pp") != copy_hashes["AHM05_01.pp"]
            job(base, token, "restore", backup=installed["backup"])
            assert file_hash(game / "data/AHM05_01.pp") == copy_hashes["AHM05_01.pp"]
            again = subprocess.run([str(program), "--serve", "--no-browser", "--data-dir", str(data_dir)], timeout=15, **flags)
            assert again.returncode == 0
            assert get(base + "/api/health")["workspace"] == str(data_dir / "workspace")
            # The browser observes the frozen backend, not a Python source test server.
            from playwright.sync_api import sync_playwright
            with sync_playwright() as playwright:
                candidates = [Path(playwright.chromium.executable_path), Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
                              Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")]
                executable = next(p for p in candidates if p.is_file())
                browser = playwright.chromium.launch(headless=True, executable_path=str(executable))
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                errors = []
                page.on("pageerror", lambda error: errors.append(error.stack))
                page.goto(base + "/models/", wait_until="networkidle")
                page.wait_for_function("document.querySelector('#loading').hidden")
                page.locator('input[value="clay"]').check()
                page.screenshot(path=str(output / "frozen-model-preview.png"), full_page=True)
                assert not errors, errors
                browser.close()
            stop(base, token, process)
            base = None
            moved = root / "移动后的 程序"
            shutil.copytree(program.parent, moved)
            shutil.copytree(data_dir, moved / "userdata")
            moved_program = moved / "素材工坊.exe"
            process = subprocess.Popen([str(moved_program), "--serve", "--no-browser", "--port", "0"], **flags)
            base = wait_ready(moved / "userdata", process)
            token = get(base + "/api/workshop")["token"]
            assert len(get(base + "/api/models/manifest")["models"]) == 3
            assert get(base + f"/api/models/candidate/{candidate['id']}/preflight")["ready"]
            stop(base, token, process)
            base = None
            for name, expected in real_hashes.items():
                assert file_hash(source_data / name) == expected, "Original game archive changed"
            result = {"result": "passed", "version": __version__, "package_bytes": args.zip.stat().st_size,
                      "package_files": len(manifest["files"]) + 1, "diagnostics": diagnostics, "first_run_gui": gui,
                      "configured_gui_start_and_close": ready_gui,
                      "restricted_environment": True, "unicode_and_spaces": True,
                      "texture_native_apply_restore": "exact", "model_native_install_restore": "exact",
                      "duplicate_launch": "reused", "moved_program_and_userdata": "passed", "original_archives": "unchanged"}
            (output / "report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(result, ensure_ascii=False, indent=2))
        finally:
            if process.poll() is None:
                if base:
                    try:
                        stop(base, token, process)
                    except Exception:
                        process.terminate()
                        process.wait(10)
                else:
                    process.terminate()
                    process.wait(10)


if __name__ == "__main__":
    main()
