import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.request

from homemate_workshop.portable import Service, Settings, inspect_game
from homemate_workshop.runtime import InstanceBusy, InstanceLock, managed_service, run_external
from tests.fixtures import image_bytes, pp


class PortableTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="portable-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.game = self.root / "游戏 空格"
        (self.game / "data").mkdir(parents=True)
        pp(self.game / "data" / "AHM07_00.pp", {"cloth.tga": image_bytes()})
        self.tool = self.root / "外部 工具" / "PPExtractor0.6.2.exe"
        self.tool.parent.mkdir()
        self.tool.write_bytes(b"placeholder for path validation only")
        self.data = self.root / "userdata"
        self.data.mkdir()

    def settings(self):
        settings = Settings(self.data)
        settings.save(self.game, self.tool)
        return settings

    def test_select_data_directory_and_persist_custom_tool(self):
        checked = inspect_game(self.game / "data", self.tool)
        self.assertTrue(checked["ready"])
        self.assertEqual(checked["game_dir"], str(self.game.resolve()))
        self.settings()
        again = Settings(self.data)
        self.assertEqual(again.game, str(self.game.resolve()))
        self.assertEqual(again.extractor, str(self.tool.resolve()))

    def test_corrupt_settings_are_preserved_and_reported(self):
        path = self.data / "settings.json"
        path.write_text("broken JSON", encoding="utf-8")
        settings = Settings(self.data)
        self.assertTrue(settings.warning)
        self.assertEqual(path.read_text(encoding="utf-8"), "broken JSON")

    def test_missing_tool_does_not_save_partial_settings(self):
        settings = Settings(self.data)
        with self.assertRaisesRegex(ValueError, "解包工具"):
            settings.save(self.game, self.root / "missing.exe")
        self.assertFalse(settings.path.exists())

    def test_os_instance_lock_releases_after_close(self):
        path = self.data / "instance.lock"
        first = InstanceLock(path).acquire()
        self.addCleanup(first.release)
        with self.assertRaises(InstanceBusy):
            InstanceLock(path).acquire()
        first.release()
        with InstanceLock(path):
            pass

    def test_service_start_reuse_metadata_and_stop(self):
        settings = self.settings()
        service = Service(settings)
        self.addCleanup(service.stop)
        url = service.start(0)
        self.assertEqual(service.start(0), url)
        state = json.load(urllib.request.urlopen(url + "/api/workshop"))
        self.assertTrue(state["extractor_available"])
        self.assertTrue(state["portable"])
        self.assertEqual(managed_service(settings.workspace)["url"], url)
        service.stop()
        self.assertFalse((settings.workspace / "server.json").exists())
        self.assertTrue(settings.path.exists())
        self.assertIsNone(managed_service(settings.workspace))

    def test_shutdown_waits_for_inflight_http_write(self):
        service = Service(self.settings())
        self.addCleanup(service.stop)
        url = service.start(0)
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def work(_payload):
            entered.set()
            release.wait(10)
            return {"done": True}
        service.server.models.create = work
        response = []
        def post():
            request = urllib.request.Request(url + "/api/models/candidate", b"{}",
                    {"Content-Type": "application/json", "X-Studio-Token": service.server.studio.token})
            response.append(json.load(urllib.request.urlopen(request)))
        request_thread = threading.Thread(target=post)
        request_thread.start()
        self.assertTrue(entered.wait(5))
        stop_thread = threading.Thread(target=service.stop)
        stop_thread.start()
        self.assertFalse(service.finished.wait(.15))
        release.set()
        request_thread.join(5)
        stop_thread.join(5)
        self.assertEqual(response, [{"done": True}])
        self.assertFalse(stop_thread.is_alive())

    def test_shutdown_waits_for_queued_background_work(self):
        service = Service(self.settings())
        self.addCleanup(service.stop)
        service.start(0)
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def work():
            entered.set()
            release.wait(10)
        service.server.studio.executor.submit(work)
        self.assertTrue(entered.wait(5))
        stop_thread = threading.Thread(target=service.stop)
        stop_thread.start()
        self.assertFalse(service.finished.wait(.15))
        release.set()
        stop_thread.join(5)
        self.assertFalse(stop_thread.is_alive())

    def test_moving_to_another_game_does_not_retarget_backups(self):
        settings = self.settings()
        backups = settings.workspace / "textures" / "backups" / "existing"
        backups.mkdir(parents=True)
        (backups / "manifest.json").write_text("{}", encoding="utf-8")
        other = self.root / "other-game"
        (other / "data").mkdir(parents=True)
        pp(other / "data" / "AHM07_00.pp", {"cloth.tga": image_bytes()})
        before = settings.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "另建"):
            settings.save(other, self.tool)
        self.assertEqual(settings.path.read_bytes(), before)

    def test_forged_service_url_is_not_requested(self):
        settings = self.settings()
        settings.workspace.mkdir()
        (settings.workspace / "server.json").write_text(json.dumps({"url": "https://example.com"}), encoding="utf-8")
        with patch("homemate_workshop.runtime.urllib.request.urlopen") as request:
            self.assertIsNone(managed_service(settings.workspace))
            request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
