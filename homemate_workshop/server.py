"""One loopback HTTP service for textures, models and the workshop dashboard."""
from __future__ import annotations

import json
import mimetypes
from pathlib import Path
import threading
from urllib.parse import unquote, urlparse

from . import __version__
from .formats import exact
from .models import ModelWorkshop
from .studio import Handler as TextureHandler, Studio, ThreadingHTTPServer

WEB = Path(__file__).parent / "web"


def contained(root, relative):
    root = Path(root).resolve()
    target = (root / unquote(relative).replace("\\", "/")).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise FileNotFoundError("文件不存在或路径无效")
    return target


class Handler(TextureHandler):
    server_version = "HomeMateAssetWorkshop/" + __version__

    def file(self, root, relative):
        target = contained(root, relative)
        mime = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                ".css": "text/css; charset=utf-8", ".json": "application/json; charset=utf-8"}.get(target.suffix)
        self.send(target.read_bytes(), mime or mimetypes.guess_type(target.name)[0] or "application/octet-stream")

    def texture_request(self, method):
        original = self.path
        self.path = self.path.replace("/api/textures/", "/api/", 1)
        try:
            method(self)
        finally:
            self.path = original

    def do_GET(self):
        try:
            self.validate()
            route = urlparse(self.path).path
            if route.startswith("/api/textures/"):
                return self.texture_request(TextureHandler.do_GET)
            if route == "/api/health":
                self.send({"service": "HomeMate Asset Workshop", "version": __version__,
                           "game_dir": str(self.studio.game_dir), "workspace": str(self.server.workspace),
                           "instance": self.server.instance})
            elif route == "/api/workshop":
                manifest = self.server.models.manifest()
                self.send({"service": "HomeMate Asset Workshop", "version": __version__, "token": self.studio.token,
                           "game_dir": str(self.studio.game_dir), "workspace": str(self.server.workspace),
                           "game_available": (self.studio.game_dir / "data").is_dir(),
                           "extractor_available": self.studio.extractor.is_file(),
                           "portable": hasattr(self.server, "portable_data_dir"),
                           "model_count": len(manifest["models"]), "models_prepared": manifest["prepared"],
                           "staged_count": len(self.studio.staged), "backups": self.studio.backups()})
            elif route == "/api/models/manifest":
                self.send(self.server.models.catalog())
            elif route.startswith("/api/models/candidate/"):
                rest = route.removeprefix("/api/models/candidate/").split("/")
                if len(rest) != 2:
                    raise ValueError("无效的候选地址")
                identifier, action = rest
                if action == "preflight":
                    self.send(self.server.models.preflight(identifier))
                else:
                    record, model, folder = self.server.models.candidate(identifier)
                    if action == "preview":
                        self.file(folder, "preview.json")
                    elif action == "download":
                        target = contained(folder, model["name"])
                        self.send(target.read_bytes(), "application/octet-stream", filename=model["name"])
                    else:
                        raise ValueError("未知候选操作")
            elif route.startswith("/assets/"):
                # Serve only render assets; extracted XX sources stay in the workspace.
                if route.lower().endswith(".xx"):
                    raise FileNotFoundError("使用候选导出接口导出 XX")
                self.file(self.server.models.assets, route.removeprefix("/assets/"))
            elif route.startswith("/vendor/"):
                self.file(WEB / "vendor", route.removeprefix("/vendor/"))
            elif route in {"/textures", "/models"}:
                self.send_response(302)
                self.send_header("Location", route + "/")
                self.send_header("Content-Length", "0")
                self.end_headers()
            elif route.startswith("/textures/"):
                self.file(WEB / "texture", route.removeprefix("/textures/") or "index.html")
            elif route.startswith("/models/"):
                self.file(WEB / "model", route.removeprefix("/models/") or "index.html")
            elif route == "/guide/":
                self.file(WEB / "home", "guide.html")
            elif route == "/":
                self.file(WEB / "home", "index.html")
            elif route in {"/shared.css", "/home.js", "/home.css"}:
                self.file(WEB / "home", route.lstrip("/"))
            else:
                self.send({"error": "地址不存在"}, status=404)
        except FileNotFoundError as error:
            self.send({"error": str(error)}, status=404)
        except (ValueError, KeyError, OSError) as error:
            self.send({"error": str(error)}, status=400)
        except Exception as error:
            self.send({"error": str(error)}, status=500)

    def do_POST(self):
        activity = getattr(self.server, "activity", None)
        if activity is None:
            return self._do_POST()
        with activity:
            if self.server.closing:
                self.send({"error": "工坊正在关闭，请等待任务完成后重新启动"}, status=503)
                return
            self.server.active_requests += 1
        try:
            self._do_POST()
        finally:
            with activity:
                self.server.active_requests -= 1
                activity.notify_all()

    def _do_POST(self):
        try:
            self.validate(mutation=True)
            route = urlparse(self.path).path
            if route.startswith("/api/textures/"):
                return self.texture_request(TextureHandler.do_POST)
            size = int(self.headers.get("Content-Length", 0))
            if not 0 < size <= 128 * 1024:
                raise ValueError("请求大小无效")
            payload = json.loads(exact(self.rfile, size))
            if not isinstance(payload, dict):
                raise ValueError("请求必须为 JSON 对象")
            if route == "/api/models/prepare":
                self.send(self.server.models.prepare())
            elif route == "/api/models/candidate":
                self.send(self.server.models.create(payload))
            elif route == "/api/models/recommended":
                self.send(self.server.models.recommended())
            elif route == "/api/models/install":
                self.send(self.server.models.install(payload.get("candidates")))
            elif route == "/api/shutdown":
                self.send({"ok": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            else:
                self.send({"error": "操作不存在"}, status=404)
        except (ValueError, KeyError, OSError) as error:
            self.send({"error": str(error)}, status=400)
        except Exception as error:
            self.send({"error": str(error)}, status=500)


def make_server(game_dir, workspace, port=8765, attempts=20, extractor=None):
    workspace = Path(workspace).resolve()
    studio = Studio(game_dir, workspace / "textures", extractor=extractor)
    try:
        for candidate in ([0] if port == 0 else range(port, min(port + attempts, 65536))):
            try:
                server = ThreadingHTTPServer(("127.0.0.1", candidate), Handler)
                break
            except OSError:
                if candidate == min(port + attempts, 65536) - 1:
                    raise
        server.studio = studio
        server.workspace = workspace
        server.models = ModelWorkshop(studio, workspace / "models")
        server.instance = __import__("secrets").token_hex(16)
        server.activity = threading.Condition()
        server.closing = False
        server.active_requests = 0
        return server
    except Exception:
        studio.executor.shutdown(wait=True)
        raise


def close_server(server):
    """After serve_forever stops, finish HTTP writes and queued jobs before exit."""
    activity = getattr(server, "activity", None)
    if activity is not None:
        with activity:
            server.closing = True
            while server.active_requests:
                activity.wait(timeout=1)
    server.studio.executor.shutdown(wait=True)
    with server.studio.lock:
        server.server_close()
