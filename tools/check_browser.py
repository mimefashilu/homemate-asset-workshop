"""Check unified navigation and editing on generated fixtures, with screenshots."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from homemate_workshop.server import make_server
from tests.fixtures import fake_native, image_bytes, pp, xx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screenshots", type=Path, default=ROOT / "workspace" / "browser-check")
    args = parser.parse_args()
    args.screenshots.mkdir(parents=True, exist_ok=True)
    from playwright.sync_api import sync_playwright
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        game = root / "game"
        (game / "data").mkdir(parents=True)
        pp(game / "data" / "AHM05_01.pp", {"b_k_01.xx": xx("b_p_kao01_A"),
                                            "b_k_21.xx": xx("b_p_kamiF01_A"), "g_k_41.xx": xx()})
        pp(game / "data" / "AHM07_00.pp", {"cloth.tga": image_bytes()})
        server = make_server(game, root / "workspace", 0)
        server.studio.native = fake_native
        server.studio.game_closed = lambda: None
        server.models.prepare()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with sync_playwright() as playwright:
                candidates = [Path(playwright.chromium.executable_path),
                              Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
                              Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")]
                executable = next((p for p in candidates if p.is_file()), None)
                browser = playwright.chromium.launch(headless=True, executable_path=str(executable) if executable else None)
                page = browser.new_page(viewport={"width": 1440, "height": 1080})
                errors, failed = [], []
                page.on("pageerror", lambda error: errors.append(error.stack))
                page.on("response", lambda response: failed.append(f"{response.status} {response.url}") if response.status >= 400 and not response.url.endswith("favicon.ico") else None)
                page.goto(base, wait_until="networkidle")
                page.wait_for_function("document.querySelector('#model-count').textContent === '3'")
                # Documentation screenshot uses generated fixtures and generic paths only.
                page.locator("#game-dir").evaluate("element => element.textContent = 'C:/Games/HomeMate'")
                page.locator("#workspace").evaluate("element => element.textContent = 'HomeMate Asset Workshop/workspace'")
                page.screenshot(path=str(args.screenshots / "overview.png"), full_page=True)
                page.locator('.feature[href="/textures/"]').click()
                page.locator("#archiveSelect option[value='AHM07_00.pp']").wait_for(state="attached")
                page.locator("#archiveSelect").select_option("AHM07_00.pp")
                page.locator(".texture-item button").first.click()
                page.wait_for_function("document.querySelector('#previewEmpty').hidden")
                page.locator("#upscaleImage").click()
                page.wait_for_function("document.querySelector('#stageCount').textContent === '1'")
                page.locator("#exportCurrent").click()
                page.locator("#resultDownload:not([hidden])").wait_for()
                link = page.locator("#resultDownload").get_attribute("href")
                response = page.request.get(base + link)
                assert response.status == 200
                page.screenshot(path=str(args.screenshots / "textures.png"), full_page=True)
                page.locator('.workshop-nav a[href="/models/"]').click()
                page.wait_for_function("document.querySelector('#loading').hidden")
                page.locator("#create-candidate").click()
                page.wait_for_function("!document.querySelector('#report-panel').hidden")
                page.locator('input[value="heatmap"]').check()
                page.locator("#compare").click()
                page.locator("#preflight").click()
                page.wait_for_function("document.querySelector('#preflight-status').dataset.ready === 'true'")
                page.locator("#report-panel").scroll_into_view_if_needed()
                with page.expect_download() as download:
                    page.locator("#download-candidate").click()
                assert download.value.suggested_filename == "g_k_41.xx"
                page.screenshot(path=str(args.screenshots / "models.png"), full_page=True)
                page.once("dialog", lambda dialog: dialog.accept())
                page.locator("#install-candidate").click()
                page.wait_for_function("document.querySelector('#candidate-status').textContent.includes('已安装')")
                page.locator('.workshop-nav a[href="/"]').click()
                page.wait_for_function("document.querySelector('#backup-count').textContent === '1'")
                page.once("dialog", lambda dialog: dialog.accept())
                page.locator("#restore-backup").click()
                page.wait_for_function("document.querySelector('#restore-status').textContent.includes('已恢复备份')")
                page.set_viewport_size({"width": 390, "height": 844})
                page.screenshot(path=str(args.screenshots / "mobile-home.png"), full_page=True)
                page.locator('.workshop-nav a[href="/models/"]').click()
                page.wait_for_function("document.querySelector('#loading').hidden")
                page.screenshot(path=str(args.screenshots / "mobile-models.png"), full_page=True)
                assert not page.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1")
                assert not errors, errors
                assert not failed, failed
                browser.close()
                print(json.dumps({"result": "passed", "page_errors": errors, "http_errors": failed,
                                  "screenshots": str(args.screenshots)}, indent=2))
        finally:
            server.shutdown()
            thread.join(5)
            server.server_close()
            server.studio.executor.shutdown(wait=True)


if __name__ == "__main__":
    main()
