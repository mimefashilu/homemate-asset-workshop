"""Shared runtime paths, instance locks and frozen-safe system program calls."""
from __future__ import annotations

from contextlib import contextmanager
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import urllib.request
from urllib.parse import urlparse

DLL_LOCK = threading.RLock()


def application_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


class InstanceBusy(RuntimeError):
    pass


class InstanceLock:
    """An OS lock is released even if the application crashes."""
    def __init__(self, path):
        self.path = Path(path)
        self.stream = None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open("a+b")
        stream.seek(0, 2)
        if not stream.tell():
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            stream.close()
            raise InstanceBusy("此工作区已经由另一个工坊实例使用") from error
        self.stream = stream
        return self

    def release(self):
        if self.stream is not None:
            self.stream.close()
            self.stream = None

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *_args):
        self.release()


@contextmanager
def clean_dll_search():
    """Don't pass the frozen application's bundled DLL search path to system tools."""
    with DLL_LOCK:
        frozen_windows = os.name == "nt" and getattr(sys, "frozen", False)
        kernel = None
        previous = ""
        if frozen_windows:
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.GetDllDirectoryW.argtypes = [ctypes.c_uint32, ctypes.c_wchar_p]
            kernel.SetDllDirectoryW.argtypes = [ctypes.c_wchar_p]
            buffer = ctypes.create_unicode_buffer(32768)
            kernel.GetDllDirectoryW(len(buffer), buffer)
            previous = buffer.value
            if not kernel.SetDllDirectoryW(None):
                raise ctypes.WinError(ctypes.get_last_error())
        try:
            yield
        finally:
            if kernel is not None:
                kernel.SetDllDirectoryW(previous or None)


def run_external(arguments, **kwargs):
    kwargs.setdefault("capture_output", True)
    if os.name == "nt":
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | subprocess.CREATE_NO_WINDOW
    timeout = kwargs.pop("timeout", None)
    with clean_dll_search():
        process = subprocess.Popen(arguments, stdout=subprocess.PIPE if kwargs.pop("capture_output") else None,
                                   stderr=subprocess.PIPE, **kwargs)
    with process:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            raise
    return subprocess.CompletedProcess(arguments, process.returncode, stdout, stderr)


def managed_service(workspace):
    """Read only a matching loopback service, never a remote URL from a config file."""
    workspace = Path(workspace).resolve()
    record_path = workspace / "server.json"
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
        parsed = urlparse(record["url"])
        if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port or parsed.path:
            return None
        health = json.load(urllib.request.urlopen(record["url"] + "/api/health", timeout=2))
        if (health.get("service") != "HomeMate Asset Workshop"
                or health.get("workspace") != str(workspace)
                or health.get("instance") != record.get("instance")):
            return None
        return {**record, "health": health}
    except (ValueError, KeyError, OSError):
        return None
