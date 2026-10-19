"""Optional byte comparisons against a user-provided historical reference tool.

The external EXE is a development oracle only, never a shipped dependency.
All generated and extracted data stays inside a temporary test directory.
"""
from collections import defaultdict
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from PIL import Image

from homemate_workshop.ahm_codec import pack_directory, unpack_archive, transform
from homemate_workshop.formats import build_archive, read_archive


GAME = Path(os.environ.get("HM_TEST_GAME_DIR", "."))
TOOL = GAME / "data/PPExtractor0.6.2.exe"


def encrypted_members(path):
    raw = path.read_bytes()
    return {e["name"]: raw[e["offset"]:e["offset"] + e["size"]] for e in read_archive(path)}


@unittest.skipUnless(os.environ.get("HM_TEST_GAME_DIR") and TOOL.is_file(), "Optional reference EXE is needed only for development parity tests")
class CodecOracleTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parent / "workspace/codec-oracle"
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def oracle(self, path):
        result = subprocess.run([str(TOOL.resolve()), "/AHM", "/noretro", str(path)],
                                cwd=self.root, capture_output=True, timeout=180)
        self.assertEqual(result.returncode, 0, (result.stdout + result.stderr).decode("mbcs", errors="replace"))

    def test_generated_members_encode_and_decode_against_reference(self):
        ours, reference = self.root / "ours", self.root / "reference"
        ours.mkdir()
        reference.mkdir()
        lengths = list(range(42)) + [8191, 8192, 8193, 65535, 65536, 65537, 524287, 524288, 524289, 1048583]
        for length in lengths:
            payload = bytes(range(256)) * (length // 256) + bytes(range(length % 256))
            for folder in [ours, reference]:
                (folder / f"bytes-{length}.bin").write_bytes(payload)
        for extension, fmt in [("bmp", "BMP"), ("tga", "TGA")]:
            name = f"image-{extension}.ema.{extension}"
            Image.new("RGB", (13, 9), (35, 92, 147)).save(ours / name, fmt)
            shutil.copy2(ours / name, reference / name)
        packed = pack_directory(ours)
        self.oracle(reference)
        a, b = encrypted_members(packed), encrypted_members(reference.with_suffix(".pp"))
        self.assertEqual(set(a), set(b))
        for name in a:
            with self.subTest(name=name):
                # The reference encoder leaves opaque EMA metadata unstable.
                # The built-in image-folder encoder uses a canonical zero header.
                if name.endswith(".ema"):
                    self.assertEqual(a[name][8:], b[name][8:])
                    self.assertEqual(transform(a[name])[:8], bytes(8))
                else:
                    self.assertEqual(a[name], b[name])
        target = self.root / "decoded-by-reference.pp"
        shutil.copy2(packed, target)
        self.oracle(target)
        for path in ours.iterdir():
            self.assertEqual((target.with_suffix("") / path.name).read_bytes(), path.read_bytes())
        target2 = self.root / "decoded-internally.pp"
        shutil.copy2(reference.with_suffix(".pp"), target2)
        output = unpack_archive(target2)
        for path in ours.iterdir():
            self.assertEqual((output / path.name).read_bytes(), path.read_bytes())

    def test_real_archive_member_samples_match_reference(self):
        total, archives, types = 0, 0, set()
        for source in sorted((GAME / "data").glob("*.pp")):
            grouped = defaultdict(list)
            for entry in read_archive(source):
                extension = Path(entry["name"]).suffix.lower()
                if len(grouped[extension]) < 2 and entry["size"] <= 32 * 1024 * 1024:
                    grouped[extension].append(entry)
            selected = [e for group in grouped.values() for e in group]
            if not selected:
                continue
            sample = self.root / (source.stem + ".pp")
            build_archive(source, selected, {}, sample)
            self.oracle(sample)
            internal = self.root / (source.stem + "-builtin.pp")
            shutil.copy2(sample, internal)
            output = unpack_archive(internal)
            for expected in sample.with_suffix("").iterdir():
                self.assertEqual((output / expected.name).read_bytes(), expected.read_bytes(), source.name + "/" + expected.name)
                total += 1
            types.update(Path(e["name"]).suffix.lower() for e in selected)
            archives += 1
        self.assertGreater(total, 0)
        print(f"Reference parity: {archives} archives, {total} members, {len(types)} resource types")
