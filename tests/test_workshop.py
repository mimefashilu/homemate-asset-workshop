import base64
import io
import json
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

from PIL import Image

from homemate_workshop.formats import image_bytes as embedded_image, read_xx, replace_xx
from homemate_workshop.model_ops import apply_operation
from homemate_workshop.models import ModelWorkshop, verify_geometry
from homemate_workshop.server import make_server
from homemate_workshop.studio import Studio, digest, resource_id
from tests.fixtures import fake_native, image_bytes, pp, xx


class WorkshopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.game = self.root / "game"
        (self.game / "data").mkdir(parents=True)
        pp(self.game / "data" / "AHM05_01.pp", {
            "b_k_01.xx": xx("b_p_kao01_A"), "b_k_21.xx": xx("b_p_kamiF01_A"),
            "g_k_41.xx": xx(), "untouched.bin": b"unchanged-member"})
        pp(self.game / "data" / "AHM07_00.pp", {"cloth.tga": image_bytes()})
        self.workspace = self.root / "workspace"
        self.studio = Studio(self.game, self.workspace / "textures")
        self.addCleanup(self.studio.executor.shutdown, wait=True)
        self.studio.native = fake_native
        self.studio.game_closed = lambda: None
        self.models = ModelWorkshop(self.studio, self.workspace / "models")

    def candidate(self):
        self.models.prepare()
        return self.models.create({"model": "g_k_41", "operation": "smooth", "parameters": {"max_displacement": .03}})

    def test_geometry_operations_preserve_protected_bytes(self):
        original = xx()
        for operation in ["smooth", "inflate", "contract"]:
            with self.subTest(operation=operation):
                candidate, report = apply_operation(original, operation, {"max_displacement": .03}, ["*"])
                verify_geometry(original, candidate)
                self.assertEqual(read_xx(original), read_xx(candidate))
                self.assertGreater(sum(r["changed_vertices"] for r in report), 0)
                self.assertLessEqual(max(r["max_displacement"] for r in report), .03001)
        with self.assertRaises(ValueError):
            verify_geometry(original, original)

    def test_embedded_texture_replace_preserves_geometry(self):
        original = xx()
        edited = replace_xx(original, {0: image_bytes((16, 16))})
        before, after = read_xx(original), read_xx(edited)
        self.assertEqual(original[:before["textures"][0]["start"]], edited[:after["textures"][0]["start"]])
        self.assertEqual(after["textures"][0]["width"], 16)
        self.assertEqual(after["materials"], before["materials"])

    def test_texture_import_export_patch_apply_restore(self):
        original = digest(self.game / "data" / "AHM07_00.pp")
        key = resource_id("AHM07_00.pp", "cloth.tga")
        payload = io.BytesIO()
        Image.new("RGB", (16, 16), (200, 70, 60)).save(payload, "PNG")
        self.studio.stage(key, payload.getvalue(), preserve_alpha=True)
        with Image.open(self.studio.stage_file(key)) as image:
            self.assertEqual(image.getchannel("A").getextrema(), (128, 128))
        exported = self.studio.export({"keys": [key], "variant": "staged"}, lambda *_: None)
        self.assertTrue(exported["download"].startswith("/api/textures/download/"))
        patch_file = self.studio.patch({}, lambda *_: None)
        self.studio.discard([key])
        self.studio.import_patch(Path(patch_file["path"]).read_bytes())
        applied = self.studio.apply({}, lambda *_: None)
        self.assertNotEqual(digest(self.game / "data" / "AHM07_00.pp"), original)
        self.studio.restore({"backup": applied["backup"]}, lambda *_: None)
        self.assertEqual(digest(self.game / "data" / "AHM07_00.pp"), original)

    def test_model_candidate_install_and_shared_restore(self):
        source = self.game / "data" / "AHM05_01.pp"
        before = digest(source)
        record = self.candidate()
        self.assertEqual(digest(source), before)
        self.assertTrue(self.models.preflight(record["id"])["ready"])
        result = self.models.install([record["id"]])
        self.assertNotEqual(digest(source), before)
        self.assertEqual(self.studio.backups()[0]["kind"], "models")
        self.assertFalse(self.models.preflight(record["id"])["ready"])
        self.studio.restore({"backup": result["backup"]}, lambda *_: None)
        self.assertEqual(digest(source), before)

    def test_model_install_rejects_staged_texture_conflict(self):
        record = self.candidate()
        key = resource_id("AHM05_01.pp", "b_k_01.xx", 0)
        self.studio.stage(key, image_bytes())
        with self.assertRaisesRegex(ValueError, "待写入贴图"):
            self.models.install([record["id"]])
        self.assertIn(key, self.studio.staged)

    def test_tampered_candidate_is_rejected(self):
        record = self.candidate()
        candidate = self.models.candidates / record["id"] / "g_k_41.xx"
        data = bytearray(candidate.read_bytes())
        data[-11] ^= 1
        candidate.write_bytes(data)
        self.assertFalse(self.models.preflight(record["id"])["ready"])
        with self.assertRaises(ValueError):
            self.models.install([record["id"]])

    def test_failed_preparation_keeps_previous_assets(self):
        self.models.prepare()
        before = (self.models.assets / "manifest.json").read_bytes()
        with patch.object(self.studio, "decoded", side_effect=ValueError("extract failure")):
            with self.assertRaises(ValueError):
                self.models.prepare()
        self.assertEqual((self.models.assets / "manifest.json").read_bytes(), before)

    def test_recommended_batch_and_comparison(self):
        from homemate_workshop.render_comparison import render_candidates
        self.models.prepare()
        first = self.models.recommended()
        second = self.models.recommended()
        self.assertEqual(first, second)
        self.assertEqual(len(first["candidate_ids"]), 2)
        paths = render_candidates(self.models, first["candidate_ids"])
        for path in paths["images"]:
            with Image.open(path) as image:
                self.assertEqual(image.size, (1080, 768))

    def test_candidates_can_move_with_workspace(self):
        record = self.candidate()
        moved = self.root / "moved-models"
        shutil.copytree(self.models.workspace, moved)
        other = ModelWorkshop(self.studio, moved)
        self.assertTrue(other.preflight(record["id"])["ready"])

    def test_invalid_parameters_and_face_edit_are_rejected(self):
        self.models.prepare()
        for payload in [{"model": "b_k_01", "operation": "smooth"},
                        {"model": "g_k_41", "operation": "smooth", "parameters": {"strength": float("nan")}}]:
            with self.assertRaises(ValueError):
                self.models.create(payload)


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.server = make_server(Path(self.temp.name) / "no-game", Path(self.temp.name) / "workspace", 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close)
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def close(self):
        self.server.shutdown()
        self.thread.join(5)
        self.server.server_close()
        self.server.studio.executor.shutdown(wait=True)

    def get(self, path):
        return urllib.request.urlopen(self.base + path)

    def test_clean_checkout_pages_and_empty_catalog(self):
        for path in ["/", "/textures/", "/models/", "/guide/", "/shared.css", "/vendor/three.module.js"]:
            with self.subTest(path=path), self.get(path) as response:
                self.assertEqual(response.status, 200)
                self.assertGreater(len(response.read()), 0)
        self.assertFalse(json.load(self.get("/api/models/manifest"))["prepared"])
        self.assertEqual(json.load(self.get("/api/textures/state"))["archives"], [])

    def test_mutation_requires_token_and_local_origin(self):
        for headers in [{}, {"X-Studio-Token": self.server.studio.token, "Origin": "https://invalid.example"}]:
            request = urllib.request.Request(self.base + "/api/models/prepare", b"{}", headers)
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(request)
            self.assertEqual(error.exception.code, 400)

    def test_paths_cannot_leave_static_roots(self):
        for path in ["/models/%2e%2e/%2e%2e/server.py", "/assets/../staged.json", "/vendor/../../../config.local.json"]:
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.get(path)
            self.assertEqual(error.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
