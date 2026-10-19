"""Resolution indexing and filtered exports on isolated resource copies."""
import io
import os
import json
from pathlib import Path
import sys
import threading
import unittest
from unittest import mock

from PIL import Image
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
import test_studio as fixtures
from homemate_workshop.formats import build_archive, read_archive
from homemate_workshop.studio import Studio, ThreadingHTTPServer, digest
from homemate_workshop.server import Handler


@unittest.skipUnless(os.environ.get('HM_TEST_GAME_DIR') and sync_playwright, 'Requires HM_TEST_GAME_DIR and Playwright')
class ResolutionIndexTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.TextureRoundTripTests('test_export_is_saved_locally_without_browser_download')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.studio = self.fixture.studio

    def test_bulk_sizes_are_cached_across_restarts_and_ignore_staged_dimensions(self):
        original_hash = digest(self.fixture.game / 'data' / 'AHM07_00.pp')
        with mock.patch.object(self.studio, 'native', wraps=self.studio.native) as native:
            images = self.studio.images('AHM07_00.pp')
            self.assertEqual(native.call_count, 1)
            self.assertEqual(self.studio.images('AHM07_00.pp'), images)
            self.assertEqual(native.call_count, 1)
        sizes = {image['name']: (image['width'], image['height']) for image in images}
        self.assertEqual(sizes['b_t_40_a_01.tga'], (512, 512))
        self.assertEqual(sizes['b_t_01_a_01.bmp'], (256, 256))
        self.studio.upscale(self.fixture.cloth, 2)
        self.assertEqual(self.studio.staged[self.fixture.cloth]['width'], 1024)
        self.assertEqual(self.studio.images('AHM07_00.pp'), images)
        reopened = Studio(self.fixture.game, self.studio.workspace)
        self.addCleanup(reopened.executor.shutdown, wait=True)
        with mock.patch.object(reopened, 'native', side_effect=AssertionError('cache was ignored')):
            self.assertEqual(reopened.images('AHM07_00.pp'), images)
        self.assertEqual(digest(self.fixture.game / 'data' / 'AHM07_00.pp'), original_hash)
        self.assertEqual(list((self.studio.workspace / 'work').iterdir()), [])

    def test_index_updates_after_archive_write_and_restore_including_rectangular_images(self):
        before = self.studio.images('AHM07_00.pp')
        payload = io.BytesIO()
        Image.new('RGB', (512, 256), (70, 120, 180)).save(payload, 'PNG')
        self.studio.stage(self.fixture.face, payload.getvalue())
        with mock.patch.object(self.studio, 'game_closed'):
            result = self.studio.apply({}, lambda *_: None)
            images = self.studio.images('AHM07_00.pp')
            face = next(image for image in images if image['name'] == 'b_t_01_a_01.bmp')
            self.assertEqual((face['width'], face['height']), (512, 256))
            self.studio.restore({'backup': result['backup']}, lambda *_: None)
        self.assertEqual(self.studio.images('AHM07_00.pp'), before)

    def test_ema_wrapper_dimensions_match_decoded_image(self):
        images = self.studio.images('AHM00_00.pp')
        self.assertEqual(len(images), 1)
        with Image.open(self.studio.decoded('AHM00_00.pp', self.fixture.ema_name)) as source:
            self.assertEqual((images[0]['width'], images[0]['height']), source.size)


@unittest.skipUnless(os.environ.get('HM_TEST_GAME_DIR') and sync_playwright, 'Requires HM_TEST_GAME_DIR and Playwright')
class ResolutionBrowserTests(unittest.TestCase):
    def test_resolution_search_pagination_and_filtered_export(self):
        fixture = fixtures.TextureRoundTripTests('test_export_is_saved_locally_without_browser_download')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        source = fixture.game / 'data' / 'AHM07_00.pp'
        entries = read_archive(source)
        cloth = next(entry for entry in entries if entry['name'] == 'b_t_40_a_01.tga')
        aliases = [dict(cloth, name=f'filter_cloth_{index:02d}.tga') for index in range(32)]
        replacement = fixture.root / 'expanded.pp'
        build_archive(source, entries + aliases, {}, replacement)
        replacement.replace(source)
        fixture.studio.upscale(fixture.cloth, 2)
        original_hash = digest(source)
        staged_manifest = fixture.studio.stage_path.read_bytes()

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.studio = fixture.studio
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def close_server():
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        self.addCleanup(close_server)
        playwright = sync_playwright().start()
        self.addCleanup(playwright.stop)
        candidates = [Path(playwright.chromium.executable_path),
                      Path('C:/Program Files/Google/Chrome/Application/chrome.exe'),
                      Path('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')]
        executable = next(path for path in candidates if path.is_file())
        browser = playwright.chromium.launch(headless=True, executable_path=str(executable), args=['--disable-gpu'])
        self.addCleanup(browser.close)
        page = browser.new_page(viewport={'width': 1440, 'height': 960})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(f'http://127.0.0.1:{server.server_port}/textures/', wait_until='networkidle')
        page.wait_for_function("!document.getElementById('resolutionFilter').disabled && document.getElementById('previewEmpty').hidden")
        model_textures = fixture.studio.textures('AHM05_01.pp', 'b_k_01.xx')
        target = model_textures[0]
        key = f"{target['width']}x{target['height']}"
        page.locator('#resolutionFilter').select_option(key)
        expected = sum((texture['width'], texture['height']) == (target['width'], target['height']) for texture in model_textures)
        self.assertEqual(page.locator('#textureGrid .texture-item').count(), expected)
        self.assertTrue(all(text == f"{target['width']}×{target['height']}" for text in page.locator('#textureGrid .dimensions').all_text_contents()))

        page.locator('#archiveSelect').select_option('AHM07_00.pp')
        page.wait_for_function("!document.getElementById('resolutionFilter').disabled && document.getElementById('textureCount').textContent==='34 张'")
        self.assertEqual(page.locator('#resolutionFilter').input_value(), '')
        page.get_by_role('checkbox', name='勾选 b_t_01_a_01.bmp', exact=True).check()
        page.locator('#resolutionFilter').select_option('512x512')
        page.wait_for_function("document.getElementById('selectedName').textContent==='b_t_40_a_01.tga' && document.getElementById('previewEmpty').hidden")
        self.assertEqual(page.locator('#textureCount').inner_text(), '33 / 34 张')
        self.assertTrue(page.locator('#exportSelected').is_disabled())
        self.assertEqual(page.locator('#textureGrid .texture-item').count(), 30)
        self.assertNotIn('b_t_01_a_01.bmp', page.locator('#textureGrid').inner_text())
        self.assertIn('1024', page.locator('#modifiedLabel').inner_text())
        page.locator('#nextPage').click()
        self.assertEqual(page.locator('#pageInfo').inner_text(), '2 / 2')
        self.assertEqual(page.locator('#textureGrid .texture-item').count(), 3)
        page.locator('#exportFiltered').click()
        page.wait_for_function("document.getElementById('status').textContent.includes('已导出 33 张 PNG') && document.getElementById('jobProgress').hidden")
        folder = Path(page.locator('#exportPath').inner_text())
        exported = list(folder.rglob('*.png'))
        self.assertEqual(len(exported), 33)
        self.assertEqual(len(json.loads((folder / 'texture-manifest.json').read_text(encoding='utf-8'))), 33)
        for path in exported:
            with Image.open(path) as image:
                self.assertEqual(image.size, (512, 512))

        page.locator('#textureSearch').fill('filter_cloth')
        page.wait_for_function("document.getElementById('selectedName').textContent==='filter_cloth_00.tga' && document.getElementById('previewEmpty').hidden")
        self.assertEqual(page.locator('#textureCount').inner_text(), '32 / 34 张')
        self.assertEqual(page.locator('#pageInfo').inner_text(), '1 / 2')
        page.locator('#textureSearch').fill('does_not_exist')
        page.wait_for_function("document.getElementById('previewEmpty').textContent==='没有匹配的贴图'")
        self.assertEqual(page.locator('#textureGrid .texture-item').count(), 0)
        self.assertEqual(page.locator('#pageInfo').inner_text(), '1 / 1')
        self.assertTrue(page.locator('#exportFiltered').is_disabled())
        self.assertTrue(page.locator('#exportCurrent').is_disabled())
        page.locator('#textureSearch').fill('')
        page.locator('#resolutionFilter').select_option('256x256')
        self.assertEqual(page.locator('#textureCount').inner_text(), '1 / 34 张')
        self.assertEqual(page.locator('#textureGrid .texture-item').count(), 1)
        page.locator('#resolutionFilter').select_option('512x512')
        page.get_by_role('button', name='原图', exact=True).click()
        output = APP / 'workspace' / 'resolution-check'
        output.mkdir(exist_ok=True)
        for width, height in ((1440, 960), (768, 1024), (390, 844), (320, 750)):
            page.set_viewport_size({'width': width, 'height': height})
            page.wait_for_timeout(100)
            self.assertFalse(page.evaluate('document.documentElement.scrollWidth > window.innerWidth + 1'))
            if width in (1440, 390):
                page.screenshot(path=str(output / f'filter-{width}.png'), full_page=True)
        self.assertEqual(fixture.studio.stage_path.read_bytes(), staged_manifest)
        self.assertEqual(digest(source), original_hash)
        self.assertEqual(errors, [])


if __name__ == '__main__':
    unittest.main()
