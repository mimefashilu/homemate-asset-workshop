"""Portable application configuration and owned HTTP service lifecycle."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import threading
import uuid

from .runtime import application_dir
from .server import make_server, close_server
from .studio import atomic_json


def choose_data_dir(explicit=None):
    if explicit:
        target = Path(explicit).expanduser().resolve()
        target.mkdir(parents=True, exist_ok=True)
        return target
    target = application_dir() / "userdata"
    try:
        target.mkdir(exist_ok=True)
        probe = target / (".write-check-" + uuid.uuid4().hex)
        with probe.open("xb") as stream:
            stream.write(b"1")
        probe.unlink()
        return target
    except OSError:
        fallback = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share")) / "HomeMateAssetWorkshop"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


def normalize_game(value):
    if not value or not str(value).strip():
        return None
    path = Path(str(value).strip().strip('"')).expanduser().resolve()
    if path.name.casefold() == "data":
        path = path.parent
    return path


def inspect_game(value, extractor=None):
    game = normalize_game(value)
    data = game / "data" if game else None
    archives = bool(data and data.is_dir() and next(data.glob("*.pp"), None))
    return {"game_dir": str(game) if game else "", "game_available": archives,
            "extractor": "", "extractor_available": True, "archive_backend": "builtin-ahm",
            "ready": archives}


class Settings:
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir).resolve()
        self.path = self.data_dir / "settings.json"
        self.values = {}
        self.warning = ""
        if self.path.is_file():
            try:
                values = json.loads(self.path.read_text(encoding="utf-8-sig"))
                if not isinstance(values, dict):
                    raise ValueError("配置必须为对象")
                self.values = values
            except (OSError, ValueError) as error:
                self.warning = "上次配置无法读取，请重新选择目录。原配置已保留。"
                logging.warning("Could not read settings: %s", error)
        # Game locations are machine-specific. A moved portable app asks again if needed.
        self.game = str(self.values.get("game_dir") or "")
        self.extractor = ""  # Legacy external-tool setting is no longer used.
        if not self.game:
            for candidate in [application_dir().parent, application_dir()]:
                if inspect_game(candidate)["game_available"]:
                    self.game = str(candidate)
                    break

    @property
    def workspace(self):
        return self.data_dir / "workspace"

    def save(self, game, extractor=None):
        checked = inspect_game(game, extractor)
        if not checked["game_available"]:
            raise ValueError("请选择包含 data/*.pp 的游戏根目录，也可以直接选择 data 目录")
        # Do not silently point existing staged work/backups at a different game.
        existing = self.values.get("game_dir")
        if existing and normalize_game(existing) != normalize_game(checked["game_dir"]):
            old = self.workspace / "textures"
            staged = old / "staged.json"
            has_staged = staged.is_file() and bool(json.loads(staged.read_text(encoding="utf-8")))
            has_backups = next((old / "backups").glob("*/manifest.json"), None) is not None
            if has_staged or has_backups:
                raise ValueError("当前工作区已有待写入内容或备份。切换游戏请另建一份便携版目录，保留原 userdata。")
        self.game, self.extractor = checked["game_dir"], checked["extractor"]
        self.values = {"game_dir": self.game, "archive_backend": "builtin-ahm"}
        atomic_json(self.path, self.values)
        return checked


class Service:
    def __init__(self, settings):
        self.settings = settings
        self.server = None
        self.thread = None
        self.url = None
        self.error = None
        self.finished = threading.Event()

    def start(self, port=8765):
        if self.thread and self.thread.is_alive():
            return self.url
        self.server = make_server(self.settings.game, self.settings.workspace, port,
                                  extractor=self.settings.extractor)
        self.server.portable_data_dir = self.settings.data_dir
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.error = None
        self.finished.clear()
        atomic_json(self.settings.workspace / "server.json",
                    {"pid": os.getpid(), "url": self.url, "instance": self.server.instance})

        def serve():
            try:
                self.server.serve_forever()
            except Exception as error:
                self.error = error
                logging.exception("Workshop service failed")
            finally:
                try:
                    close_server(self.server)
                    record = self.settings.workspace / "server.json"
                    if record.is_file() and json.loads(record.read_text(encoding="utf-8")).get("instance") == self.server.instance:
                        record.unlink()
                except Exception as error:
                    self.error = error
                    logging.exception("Workshop shutdown failed")
                self.finished.set()

        self.thread = threading.Thread(target=serve, name="workshop-http", daemon=True)
        self.thread.start()
        return self.url

    def request_stop(self):
        if self.server and self.thread and self.thread.is_alive():
            with self.server.activity:
                self.server.closing = True
            self.server.shutdown()

    def stop(self):
        self.request_stop()
        if self.thread:
            self.thread.join()
