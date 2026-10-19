"""Command-line entry point, also used by the Windows launch scripts."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import urllib.error
import urllib.request
import webbrowser

from .models import ModelWorkshop
from .server import make_server, close_server
from .studio import Studio, atomic_json

SOURCE = Path(__file__).resolve().parent.parent
PROJECT = SOURCE if (SOURCE / "run.py").is_file() else Path.cwd()


def resolve_paths(args):
    config_path = Path(args.config or PROJECT / "config.local.json")
    config = json.loads(config_path.read_text(encoding="utf-8-sig")) if config_path.is_file() else {}
    def resolve(value):
        path = Path(value).expanduser()
        return (path if path.is_absolute() else config_path.parent / path).resolve()
    game = resolve(args.game_dir or config.get("game_dir") or os.environ.get("HM_GAME_DIR") or "..")
    workspace = resolve(args.workspace or config.get("workspace") or "workspace")
    if workspace == game or workspace.is_relative_to(game / "data"):
        raise ValueError("工作区不能指向游戏根目录或 data 目录")
    return game, workspace, args.port if args.port is not None else int(config.get("port", 8765))


def main(argv=None):
    parser = argparse.ArgumentParser(description="御宅伴侣素材工坊：统一贴图、模型、备份和候选预览")
    parser.add_argument("command", nargs="?", default="serve",
                        choices=["serve", "stop", "paths", "prepare-models", "recommended", "install-models", "render-comparison"])
    parser.add_argument("--game-dir", help="包含 data 的游戏根目录")
    parser.add_argument("--workspace", help="独立工作区")
    parser.add_argument("--config", help="本地 JSON 配置")
    parser.add_argument("--port", type=int)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--candidate", action="append", help="候选编号；可重复指定")
    args = parser.parse_args(argv)
    game, workspace, port = resolve_paths(args)
    if not 0 <= port <= 65535:
        parser.error("端口必须在 0 到 65535 之间")
    state_path = workspace / "server.json"
    if args.command == "paths":
        print(json.dumps({"game_dir": str(game), "workspace": str(workspace), "port": port}, ensure_ascii=False))
        return
    if args.command == "stop":
        if not state_path.is_file():
            print("没有此工作区管理的服务")
            return
        saved = json.loads(state_path.read_text(encoding="utf-8"))
        base = saved["url"]
        # Reconstruct loopback URL instead of trusting arbitrary URLs in a local file.
        from urllib.parse import urlparse
        parsed = urlparse(base)
        if parsed.hostname != "127.0.0.1" or parsed.scheme != "http":
            raise ValueError("服务记录不是本机地址")
        try:
            health = json.load(urllib.request.urlopen(base + "/api/health", timeout=2))
        except (urllib.error.URLError, OSError):
            print("记录的服务已经停止")
            return
        if health.get("instance") != saved.get("instance") or health.get("workspace") != str(workspace):
            raise ValueError("端口由其他服务占用，拒绝停止")
        state = json.load(urllib.request.urlopen(base + "/api/workshop", timeout=2))
        request = urllib.request.Request(base + "/api/shutdown", b"{}",
                                         {"Content-Type": "application/json", "X-Studio-Token": state["token"]})
        urllib.request.urlopen(request, timeout=10).close()
        print("工坊服务已请求停止")
        return
    if args.command != "serve":
        studio = Studio(game, workspace / "textures")
        models = ModelWorkshop(studio, workspace / "models")
        try:
            if args.command == "prepare-models":
                result = models.prepare()
            elif args.command == "recommended":
                result = models.recommended()
            elif args.command == "install-models":
                result = models.install(args.candidate)
            else:
                from .render_comparison import render_candidates
                result = render_candidates(models, args.candidate)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        finally:
            studio.executor.shutdown(wait=True)
        return
    # Reuse only the service for the exact same project workspace and game path.
    if state_path.is_file():
        try:
            saved = json.loads(state_path.read_text(encoding="utf-8"))
            from urllib.parse import urlparse
            parsed = urlparse(saved["url"])
            if parsed.hostname != "127.0.0.1" or parsed.scheme != "http":
                raise ValueError("无效服务记录")
            health = json.load(urllib.request.urlopen(saved["url"] + "/api/health", timeout=1))
            if health.get("service") == "HomeMate Asset Workshop" and health.get("workspace") == str(workspace):
                if health.get("game_dir") != str(game):
                    raise RuntimeError("工作区已有服务使用其他游戏目录，请先停止该服务")
                print("READY " + saved["url"], flush=True)
                if not args.no_browser:
                    webbrowser.open(saved["url"])
                return
        except (ValueError, KeyError, urllib.error.URLError, OSError):
            pass
    server = make_server(game, workspace, port)
    url = f"http://127.0.0.1:{server.server_port}"
    atomic_json(state_path, {"pid": os.getpid(), "url": url, "instance": server.instance})
    print("READY " + url, flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        close_server(server)
        if state_path.is_file() and json.loads(state_path.read_text(encoding="utf-8")).get("instance") == server.instance:
            state_path.unlink()


if __name__ == "__main__":
    main()
