"""Exercise the real AHM codec only on a temporary resource copy."""
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from homemate_workshop.formats import build_archive, read_archive
from homemate_workshop.models import ModelWorkshop
from homemate_workshop.studio import Studio, digest


@unittest.skipUnless(os.environ.get("HM_TEST_GAME_DIR"), "Set HM_TEST_GAME_DIR to test the native AHM codec")
class NativeModelTests(unittest.TestCase):
    def test_prepare_candidate_native_install_and_restore(self):
        root = Path(__file__).resolve().parent / "workspace" / "test-runs"
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as temporary:
            target = Path(temporary)
            game = target / "game"
            (game / "data").mkdir(parents=True)
            source = Path(os.environ["HM_TEST_GAME_DIR"]) / "data"
            wanted = {"b_k_01.xx", "b_k_21.xx", "g_k_41.xx"}
            entries = [e for e in read_archive(source / "AHM05_01.pp") if e["name"] in wanted]
            self.assertEqual({e["name"] for e in entries}, wanted)
            archive = game / "data" / "AHM05_01.pp"
            build_archive(source / "AHM05_01.pp", entries, {}, archive)
            shutil.copy2(source / "PPExtractor0.6.2.exe", game / "data" / "PPExtractor0.6.2.exe")
            studio = Studio(game, target / "workspace" / "textures")
            # The only write target is a temporary game copy with no running executable.
            studio.game_closed = lambda: None
            try:
                workshop = ModelWorkshop(studio, target / "workspace" / "models")
                original_hash = digest(archive)
                self.assertEqual(len(workshop.prepare()["models"]), 3)
                candidate = workshop.create({"model": "g_k_41", "operation": "smooth",
                                              "parameters": {"strength": .15, "iterations": 2, "max_displacement": .03}})
                self.assertTrue(workshop.preflight(candidate["id"])["ready"])
                result = workshop.install([candidate["id"]])
                self.assertNotEqual(digest(archive), original_hash)
                self.assertEqual(studio.decoded("AHM05_01.pp", "g_k_41.xx").read_bytes(),
                                 (workshop.candidates / candidate["id"] / "g_k_41.xx").read_bytes())
                studio.restore({"backup": result["backup"]}, lambda *_: None)
                self.assertEqual(digest(archive), original_hash)
            finally:
                studio.executor.shutdown(wait=True)
