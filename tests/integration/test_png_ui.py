"""PNG workflows on a private test server; preserve the user's live workspace."""
import io
import os
import json
from pathlib import Path
import sys
import threading
import unittest

from PIL import Image
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
import test_studio as fixtures
from homemate_workshop.studio import ThreadingHTTPServer
from homemate_workshop.server import Handler


@unittest.skipUnless(os.environ.get('HM_TEST_GAME_DIR') and sync_playwright, 'Requires HM_TEST_GAME_DIR and Playwright')
class PNGWorkflowBrowserTest(unittest.TestCase):
    def test_current_batch_and_package_png_exports_and_imports(self):
        fixture = fixtures.TextureRoundTripTests('test_export_is_saved_locally_without_browser_download')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
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
        browser = playwright.chromium.launch(headless=True, executable_path=str(executable))
        self.addCleanup(browser.close)
        page = browser.new_page(viewport={'width': 1440, 'height': 960})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(f'http://127.0.0.1:{server.server_port}/textures/', wait_until='networkidle')
        page.locator('#archiveSelect').select_option('AHM07_00.pp')
        page.get_by_role('button', name='预览 b_t_40_a_01.tga', exact=True).click()
        page.wait_for_function("document.getElementById('selectedName').textContent==='b_t_40_a_01.tga' && document.getElementById('previewEmpty').hidden")
        self.assertTrue(page.locator('#preserveAlpha').is_checked())
        self.assertEqual(page.locator('#exportFormat').input_value(), 'png')
        self.assertEqual(page.locator('#currentExportFormat').input_value(), 'png')
        page.get_by_role('button', name='导出 PNG', exact=True).click()
        page.locator('#openExport').wait_for(state='visible')
        single_folder = Path(page.locator('#exportPath').inner_text())
        single_png = single_folder / 'b_t_40_a_01.png'
        self.assertTrue(single_png.is_file())
        self.assertEqual(single_png.read_bytes()[:8], b'\x89PNG\r\n\x1a\n')
        with Image.open(single_png) as image:
            self.assertEqual(image.mode, 'RGBA')
            self.assertLess(image.getchannel('A').getextrema()[0], 255)

        page.locator('#currentExportFormat').select_option('native')
        self.assertEqual(page.locator('#exportFormat').input_value(), 'native')
        page.get_by_role('button', name='导出原图', exact=True).click()
        page.wait_for_function("document.getElementById('status').textContent.includes('原始格式贴图')")
        native_folder = Path(page.locator('#exportPath').inner_text())
        self.assertTrue((native_folder / 'b_t_40_a_01.tga').is_file())
        page.locator('#exportFormat').select_option('png')
        self.assertEqual(page.locator('#currentExportFormat').input_value(), 'png')

        edited_png = fixture.root / 'edited.png'
        with Image.open(single_png) as image:
            resized = image.convert('RGB').resize((1024, 1024), Image.Resampling.BILINEAR)
            resized.save(edited_png)
            expected_alpha = image.getchannel('A').resize((1024, 1024), Image.Resampling.BILINEAR).tobytes()
            expected_rgb = resized.tobytes()
        page.locator('#imageInput').set_input_files(str(edited_png))
        page.wait_for_function("document.getElementById('status').textContent.includes('新图已加入')")
        with Image.open(fixture.studio.stage_file(fixture.cloth)) as staged:
            self.assertEqual(staged.format, 'TGA')
            self.assertEqual(staged.size, (1024, 1024))
            self.assertEqual(staged.getchannel('A').tobytes(), expected_alpha)
            self.assertEqual(staged.convert('RGB').tobytes(), expected_rgb)
        self.assertEqual(fixture.studio.staged[fixture.cloth]['alpha_source'], 'original')
        self.assertIn('原贴图蒙版', page.locator('#textureDetails').inner_text())
        page.get_by_role('button', name='待写入', exact=True).click()
        page.get_by_role('button', name='导出待写入 PNG', exact=True).click()
        page.wait_for_function("!document.getElementById('openExport').hidden && document.getElementById('jobProgress').hidden")
        restored_png = Path(page.locator('#exportPath').inner_text()) / 'b_t_40_a_01.png'
        with Image.open(restored_png) as image:
            self.assertEqual(image.mode, 'RGBA')
            self.assertEqual(image.size, (1024, 1024))
            self.assertEqual(image.getchannel('A').tobytes(), expected_alpha)
            self.assertEqual(image.convert('RGB').tobytes(), expected_rgb)
        page.get_by_role('button', name='原图', exact=True).click()
        self.assertEqual(page.locator('#exportCurrent').inner_text(), '导出 PNG')

        page.locator('#preserveAlpha').uncheck()
        page.locator('#imageInput').set_input_files([])
        page.locator('#imageInput').set_input_files(str(edited_png))
        page.wait_for_function("document.getElementById('status').textContent.includes('新图已加入')")
        with Image.open(fixture.studio.stage_file(fixture.cloth)) as staged:
            self.assertEqual(staged.getchannel('A').getextrema(), (255, 255))
        self.assertEqual(fixture.studio.staged[fixture.cloth]['alpha_source'], 'image')
        page.locator('#preserveAlpha').check()

        invalid_png = fixture.root / 'wrong-aspect.png'
        Image.new('RGB', (512, 256)).save(invalid_png)
        previous_staged = fixture.studio.stage_file(fixture.cloth).read_bytes()
        page.locator('#imageInput').set_input_files(str(invalid_png))
        page.wait_for_function("document.getElementById('status').textContent.includes('宽高比')")
        self.assertEqual(fixture.studio.stage_file(fixture.cloth).read_bytes(), previous_staged)

        (native_folder / 'b_t_40_a_01.PNG').write_bytes(edited_png.read_bytes())
        page.locator('#folderInput').set_input_files(str(native_folder))
        page.wait_for_function("document.getElementById('status').textContent.includes('已导入 1 张')")
        self.assertEqual(fixture.studio.staged[fixture.cloth]['width'], 1024)
        with Image.open(fixture.studio.stage_file(fixture.cloth)) as staged:
            self.assertEqual(staged.getchannel('A').tobytes(), expected_alpha)
        self.assertEqual(fixture.studio.staged[fixture.cloth]['alpha_source'], 'original')

        page.locator('#folderInput').set_input_files(str(single_folder))
        page.wait_for_function("document.getElementById('status').textContent.includes('已导入 1 张')")
        self.assertEqual(fixture.studio.staged[fixture.cloth]['width'], 512)
        page.locator('#selectPage').check()
        page.locator('#exportSelected').click()
        page.wait_for_function("document.getElementById('status').textContent.includes('已导出 2 张 PNG')")
        batch_folder = Path(page.locator('#exportPath').inner_text())
        self.assertEqual(len(list(batch_folder.rglob('*.png'))), 2)
        page.locator('#folderInput').set_input_files(str(batch_folder))
        page.wait_for_function("document.getElementById('status').textContent.includes('已导入 2 张')")
        page.locator('#exportAll').click()
        page.wait_for_function("document.getElementById('status').textContent.includes('已导出 2 张 PNG') && document.getElementById('jobProgress').hidden")
        package_folder = Path(page.locator('#exportPath').inner_text())
        self.assertEqual(len(list(package_folder.rglob('*.png'))), 2)
        for width, height in ((1440, 960), (768, 1024), (390, 844), (320, 750)):
            page.set_viewport_size({'width': width, 'height': height})
            page.wait_for_timeout(100)
            self.assertFalse(page.evaluate('document.documentElement.scrollWidth > window.innerWidth + 1'))
        page.set_viewport_size({'width': 1440, 'height': 960})
        page.get_by_role('button', name='待写入', exact=True).click()
        output = APP / 'workspace' / 'png-check'
        output.mkdir(exist_ok=True)
        page.screenshot(path=str(output / 'png-export.png'), full_page=True)
        (output / 'checks.json').write_text(json.dumps(dict(single_png=True, selected_png=True,
            archive_png=True, rgba_preserved=True, png_to_tga=True, single_folder_import=True,
            rgb_alpha_restored=True, staged_rgba_export=True, alpha_opt_out=True,
            batch_folder_import=True, converted_native_folder_import=True,
            isolated_workspace=True, javascript_errors=errors), indent=2), encoding='utf-8')
        self.assertEqual(errors, [])


if __name__ == '__main__':
    unittest.main()
