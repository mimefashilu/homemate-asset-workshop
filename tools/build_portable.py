"""Build an audited Windows x64 folder distribution and portable ZIP."""
from __future__ import annotations

import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from homemate_workshop import __version__


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def copy_licenses(destination):
    licenses = destination / "licenses"
    licenses.mkdir()
    versions = {}
    for name in ["Pillow", "numpy", "pyinstaller", "pyinstaller-hooks-contrib"]:
        distribution = metadata.distribution(name)
        versions[name] = distribution.version
        for file in distribution.files or []:
            parts = Path(file).parts
            if not parts or not parts[0].endswith(".dist-info") or "licenses" not in parts:
                continue
            source = Path(distribution.locate_file(file))
            if not source.is_file():
                continue
            relative = Path(*parts[parts.index("licenses") + 1:])
            target = licenses / name / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_license.is_file():
        raise ValueError("Python runtime license was not found")
    shutil.copy2(python_license, licenses / "Python.txt")
    tk_license = Path(sys.base_prefix) / "tcl" / "tk8.6" / "license.terms"
    if not tk_license.is_file():
        raise ValueError("Tcl/Tk runtime license terms were not found")
    shutil.copy2(tk_license, licenses / "Tcl-Tk.txt")
    for name in ["THREE-LICENSE.txt", "LUCIDE-LICENSE.txt"]:
        shutil.copy2(ROOT / "homemate_workshop" / "web" / "vendor" / name, licenses / name)
    return versions


def audit_files(folder):
    result = {}
    for path in sorted(folder.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(folder)
        if any(p.casefold() in {"userdata", "workspace", ".git", "backups", "candidates"} for p in relative.parts):
            raise ValueError("Generated/user data cannot be included: " + relative.as_posix())
        if path.suffix.lower() in {".pp", ".xx", ".log", ".pid"} or path.name.casefold() in {"config.local.json", "settings.json", "ppextractor0.6.2.exe"}:
            raise ValueError("Private or external resource cannot be included: " + relative.as_posix())
        result[relative.as_posix()] = sha256_file(path)
    return result


def main():
    if os.name != "nt" or platform.architecture()[0] != "64bit":
        raise SystemExit("Build this package using 64-bit Python on Windows")
    name = f"HomeMateWorkshop-v{__version__}-windows-x64"
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    target = dist / name
    if target.resolve().parent != dist.resolve():
        raise ValueError("Invalid portable output path")
    if (target / "userdata").exists():
        raise ValueError("This output contains user data. Keep it and build a new version instead of overwriting it.")
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--windowed",
        "--name", "HomeMateWorkshop", "--noupx", "--collect-data", "homemate_workshop",
        "--collect-submodules", "PIL", "--icon", str(ROOT / "homemate_workshop/assets/workshop.ico"),
        "--distpath", str(ROOT / "build/portable-dist"),
        "--workpath", str(ROOT / "build/pyinstaller"), "--specpath", str(ROOT / "build"),
        str(ROOT / "portable_entry.py"),
    ], cwd=ROOT, check=True)
    frozen = ROOT / "build/portable-dist/HomeMateWorkshop"
    with tempfile.TemporaryDirectory(prefix="portable-", dir=dist) as temporary:
        package = Path(temporary) / "HomeMateWorkshop"
        shutil.copytree(frozen, package)
        (package / "HomeMateWorkshop.exe").rename(package / "素材工坊.exe")
        versions = copy_licenses(package)
        shutil.copy2(ROOT / "LICENSE", package / "LICENSE.txt")
        shutil.copy2(ROOT / "THIRD_PARTY_NOTICES.md", package / "THIRD_PARTY_NOTICES.md")
        (package / "使用说明.txt").write_text((ROOT / "docs/PORTABLE.md").read_text(encoding="utf-8"), encoding="utf-8-sig")
        files = audit_files(package)
        manifest = {"version": __version__, "platform": "windows-x64", "python": platform.python_version(),
                    "dependencies": versions, "files": files}
        (package / "portable-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        output = dist / f"homemate-asset-workshop-v{__version__}-windows-x64-portable.zip"
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for source in sorted(package.rglob("*")):
                if source.is_file():
                    archive.write(source, "HomeMateWorkshop/" + source.relative_to(package).as_posix())
        if target.exists():
            previous = ROOT / "build/previous-packages"
            previous.mkdir(exist_ok=True)
            target.rename(previous / (name + "-" + uuid.uuid4().hex[:8]))
        shutil.move(str(package), str(target))
    checksum = sha256_file(output)
    output.with_suffix(".zip.sha256").write_text(f"{checksum}  {output.name}\n", encoding="ascii")
    print(json.dumps({"folder": str(target), "zip": str(output), "bytes": output.stat().st_size,
                      "files": len(files) + 1, "sha256": checksum, "dependencies": versions}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
