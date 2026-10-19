"""Build a deterministic source release using an explicit publication allowlist."""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TOP = {"README.md", "README.en.md", "CHANGELOG.md", "CONTRIBUTING.md", "THIRD_PARTY_NOTICES.md", "LICENSE",
       "pyproject.toml", "requirements.txt", "requirements-dev.txt", "config.example.json", "run.py",
       "start.ps1", "stop.ps1", "启动素材工坊.cmd", "停止素材工坊.cmd", ".gitignore", ".gitattributes",
       "portable_entry.py", "requirements-build.txt"}
FOLDERS = {"homemate_workshop", "tests", "tools", "docs", ".github"}
SUFFIXES = {".py", ".md", ".txt", ".json", ".html", ".css", ".js", ".yml", ".yaml", ".svg", ".png", ".ico"}


def publication_files():
    paths = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(ROOT)
        if any(p in {"workspace", "userdata", "__pycache__", ".git", "node_modules", ".venv", "dist", "build"} or p.endswith(".egg-info") for p in relative.parts):
            continue
        allowed = relative.as_posix() in TOP or (relative.parts[0] in FOLDERS and path.suffix in SUFFIXES)
        if not allowed:
            continue
        if path.stat().st_size > 5 * 1024 * 1024:
            raise ValueError(f"Unexpected large publication file: {relative}")
        if path.suffix in {".py", ".json", ".md", ".txt", ".yml"}:
            content = path.read_text(encoding="utf-8-sig")
            # No Windows user directories or embedded private API credentials.
            if re.search(r"[A-Z]:[\\/]Users[\\/][^\s]+|sk-[A-Za-z0-9_-]{24,}|gh[pousr]_[A-Za-z0-9]{30,}", content):
                raise ValueError(f"Potential personal data/credential in {relative}")
        paths.append(path)
    return paths


def main():
    from sys import path
    path.insert(0, str(ROOT))
    from homemate_workshop import __version__
    files = publication_files()
    folder = ROOT / "dist"
    folder.mkdir(exist_ok=True)
    output = folder / f"homemate-asset-workshop-v{__version__}-source.zip"
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for source in files:
            info = zipfile.ZipInfo(f"homemate-asset-workshop/{source.relative_to(ROOT).as_posix()}", (2026, 10, 19, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, source.read_bytes())
    checksum = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(".zip.sha256").write_text(f"{checksum}  {output.name}\n", encoding="ascii")
    print(f"{output}\n{len(files)} files; {output.stat().st_size:,} bytes\nSHA256 {checksum}")


if __name__ == "__main__":
    main()
