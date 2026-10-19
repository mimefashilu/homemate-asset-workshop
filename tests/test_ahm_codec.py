import io
from pathlib import Path
import shutil
import struct
import tempfile
import unittest

from PIL import Image

from homemate_workshop.ahm_codec import BLOCK, ema_parts, pack_directory, transform, unpack_archive
from homemate_workshop.formats import read_archive
from homemate_workshop.studio import Studio, digest, resource_id
from tests.fixtures import xx


class BuiltinCodecTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_known_stream_and_odd_tail(self):
        self.assertEqual(transform(bytes(16)).hex(), "9d0f5bd4efafe100bcad3e9af7af1b72")
        self.assertEqual(transform(b"\x00"), b"\x00")
        self.assertEqual(transform(bytes(9)).hex(), "9d0f5bd4efafe10000")

    def test_chunk_boundaries_and_long_round_trip(self):
        original = bytes(range(256)) * 4103 + b"tail!"
        encoded = transform(original)
        self.assertEqual(transform(encoded), original)
        chunks = b"".join(transform(original[i:i + 122], i, final=i + 122 >= len(original)) for i in range(0, len(original), 122))
        self.assertEqual(chunks, encoded)
        self.assertEqual(transform(bytes(BLOCK + 8))[:8], transform(bytes(BLOCK + 8))[-8:])
        with self.assertRaises(ValueError):
            transform(b"abc", offset=1)

    def test_pack_unpack_preserves_generated_files_and_names(self):
        folder = self.root / "source"
        folder.mkdir()
        files = {"empty.bin": b"", "odd.bin": b"12345", "large.bin": bytes(range(256)) * 4101,
                 "model.xx": xx(), "日本語.txt": b"text"}
        for name, data in files.items():
            (folder / name).write_bytes(data)
        packed = pack_directory(folder)
        renamed = self.root / "decode.pp"
        shutil.copy2(packed, renamed)
        output = unpack_archive(renamed)
        self.assertEqual({p.name: p.read_bytes() for p in output.iterdir()}, files)

    def test_ema_bmp_tga_and_opaque_header_round_trip(self):
        folder = self.root / "images"
        folder.mkdir()
        for extension, fmt in [("bmp", "BMP"), ("tga", "TGA")]:
            Image.new("RGB", (13, 9), (11, 87, 154)).save(folder / f"image-{extension}.ema.{extension}", fmt)
        image = io.BytesIO()
        Image.new("RGB", (8, 8), (31, 63, 95)).save(image, "BMP")
        header = bytes.fromhex("0102030405060708") + b".bmp\0"
        (folder / "opaque.ema").write_bytes(header + image.getvalue())
        packed = pack_directory(folder)
        target = self.root / "decoded.pp"
        shutil.copy2(packed, target)
        output = unpack_archive(target)
        self.assertEqual((output / "opaque.ema.bmp").read_bytes(), image.getvalue())
        for extension in ["bmp", "tga"]:
            name = f"image-{extension}.ema.{extension}"
            self.assertEqual((output / name).read_bytes(), (folder / name).read_bytes())
        self.assertEqual(ema_parts(header + image.getvalue())[0], header)

    def test_existing_outputs_are_never_overwritten(self):
        folder = self.root / "source"
        folder.mkdir()
        (folder / "data.bin").write_bytes(b"original")
        packed = pack_directory(folder)
        before = packed.read_bytes()
        with self.assertRaises(FileExistsError):
            pack_directory(folder)
        self.assertEqual(packed.read_bytes(), before)
        with self.assertRaises(ValueError):
            unpack_archive(packed)
        self.assertEqual((folder / "data.bin").read_bytes(), b"original")

    def test_malformed_ema_does_not_leave_extracted_folder(self):
        folder = self.root / "source"
        folder.mkdir()
        (folder / "bad.ema").write_bytes(bytes(8) + b"../../escape\0payload")
        packed = pack_directory(folder)
        target = self.root / "invalid.pp"
        shutil.copy2(packed, target)
        with self.assertRaises(ValueError):
            unpack_archive(target)
        self.assertFalse(target.with_suffix("").exists())

    def test_full_texture_and_ema_apply_restore_without_external_exe(self):
        game = self.root / "game"
        data = game / "data"
        data.mkdir(parents=True)
        input_dir = self.root / "input"
        input_dir.mkdir()
        image = io.BytesIO()
        Image.new("RGB", (8, 8), (1, 23, 45)).save(image, "BMP")
        header = bytes.fromhex("1122334455667788") + b".bmp\0"
        (input_dir / "wrapped.ema").write_bytes(header + image.getvalue())
        (input_dir / "model.xx").write_bytes(xx())
        packed = pack_directory(input_dir)
        archive = data / "AHM05_01.pp"
        shutil.copy2(packed, archive)
        original = digest(archive)
        studio = Studio(game, self.root / "workspace")
        self.addCleanup(studio.executor.shutdown, wait=True)
        studio.game_closed = lambda: None
        self.assertFalse(studio.extractor.is_file())
        key = resource_id("AHM05_01.pp", "wrapped.ema")
        studio.upscale(key, 2)
        result = studio.apply({}, lambda *_: None)
        entries = read_archive(archive)
        entry = next(e for e in entries if e["name"] == "wrapped.ema")
        with archive.open("rb") as stream:
            stream.seek(entry["offset"])
            raw = transform(stream.read(entry["size"]))
        self.assertEqual(ema_parts(raw)[0], header)
        self.assertEqual(studio.metadata(key)["width"], 16)
        studio.restore({"backup": result["backup"]}, lambda *_: None)
        self.assertEqual(digest(archive), original)


if __name__ == "__main__":
    unittest.main()
