"""Native launcher for the Windows portable application."""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import queue
import sys
import threading
import urllib.request
import webbrowser

from . import __version__
from .portable import Service, Settings, choose_data_dir, inspect_game
from .runtime import InstanceBusy, InstanceLock, clean_dll_search, managed_service


def open_browser(url):
    with clean_dll_search():
        return webbrowser.open(url)


def open_local(path):
    if os.name == "nt":
        with clean_dll_search():
            os.startfile(str(path))


def setup_logging(data_dir):
    path = data_dir / "logs"
    path.mkdir(exist_ok=True)
    handler = RotatingFileHandler(path / "launcher.log", maxBytes=2 * 1024 * 1024,
                                  backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.getLogger().setLevel(logging.INFO)
    logging.getLogger().addHandler(handler)
    return handler


class Launcher:
    def __init__(self, root, settings, *, port=8765, browser=True):
        import tkinter as tk
        from tkinter import ttk
        self.root, self.settings = root, settings
        self.port, self.browser = port, browser
        self.service = None
        self.busy = False
        self.closing = False
        self.events = queue.Queue()
        root.title(f"御宅伴侣素材工坊 {__version__}")
        root.geometry("740x510")
        root.minsize(700, 500)
        root.configure(background="#edf3f1")
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.report_callback_exception = self.report_callback_exception
        try:
            icon = Path(__file__).parent / "assets" / "workshop.png"
            self.icon = tk.PhotoImage(file=str(icon))
            root.iconphoto(True, self.icon)
        except (OSError, tk.TclError):
            pass
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("TFrame", background="#edf3f1")
        style.configure("TLabel", background="#edf3f1", foreground="#24494b", font=("Microsoft YaHei UI", 10))
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 21, "bold"))
        style.configure("Muted.TLabel", foreground="#607b78", font=("Microsoft YaHei UI", 9))
        style.configure("TButton", padding=(12, 8), font=("Microsoft YaHei UI", 9))
        style.configure("Primary.TButton", background="#246b60", foreground="white", padding=(14, 10))
        style.map("Primary.TButton", background=[("active", "#1b594f"), ("disabled", "#aebfba")])
        body = ttk.Frame(root, padding=(28, 23))
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        ttk.Label(body, text="御宅伴侣素材工坊", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(body, text=f"Windows 便携版  {__version__}  ·  MIT License", style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(5, 20))
        ttk.Label(body, text="选择游戏目录即可开始，AHM 资源读写已内置；下次启动会记住位置。").grid(row=2, column=0, sticky="w", pady=(0, 12))
        self.game_var = tk.StringVar(value=settings.game)
        self.tool_var = tk.StringVar(value=settings.extractor)
        self.entries, self.pickers = [], []
        for row, title, variable, command in [
            (3, "游戏目录（包含 data 的文件夹）", self.game_var, self.choose_game),
        ]:
            ttk.Label(body, text=title).grid(row=row, column=0, sticky="w", pady=(6, 5))
            line = ttk.Frame(body)
            line.grid(row=row + 1, column=0, sticky="ew")
            line.columnconfigure(0, weight=1)
            entry = ttk.Entry(line, textvariable=variable, font=("Segoe UI", 10))
            entry.grid(row=0, column=0, sticky="ew", ipady=5, padx=(0, 8))
            picker = ttk.Button(line, text="选择…", command=command)
            picker.grid(row=0, column=1)
            self.entries.append(entry)
            self.pickers.append(picker)
        self.environment = tk.StringVar()
        ttk.Label(body, textvariable=self.environment, wraplength=660).grid(row=7, column=0, sticky="w", pady=(12, 6))
        self.status = tk.StringVar(value=settings.warning or "选择好目录后，点击启动。")
        ttk.Label(body, textvariable=self.status, wraplength=660, style="Muted.TLabel").grid(row=8, column=0, sticky="w", pady=(0, 13))
        actions = ttk.Frame(body)
        actions.grid(row=9, column=0, sticky="ew")
        self.start_button = ttk.Button(actions, text="启动并打开工坊", command=self.start, style="Primary.TButton")
        self.start_button.pack(side="left")
        self.open_button = ttk.Button(actions, text="重新打开页面", command=self.reopen)
        self.open_button.pack(side="left", padx=8)
        self.stop_button = ttk.Button(actions, text="停止工坊", command=self.stop)
        self.stop_button.pack(side="left")
        ttk.Separator(body).grid(row=10, column=0, sticky="ew", pady=(20, 12))
        ttk.Label(body, text="设置、备份与候选保存于：", style="Muted.TLabel").grid(row=11, column=0, sticky="w")
        ttk.Label(body, text=str(settings.data_dir), style="Muted.TLabel", wraplength=660).grid(row=12, column=0, sticky="w", pady=(3, 9))
        bottom = ttk.Frame(body)
        bottom.grid(row=13, column=0, sticky="ew")
        ttk.Button(bottom, text="打开数据目录", command=lambda: open_local(settings.data_dir)).pack(side="left")
        ttk.Button(bottom, text="查看日志", command=lambda: open_local(settings.data_dir / "logs")).pack(side="left", padx=8)
        self.footer = ttk.Label(body, text="关闭此窗口会等待任务完成并停止服务。请保留 userdata 文件夹以保存备份。", style="Muted.TLabel", wraplength=660)
        self.footer.grid(row=14, column=0, sticky="w", pady=(11, 0))
        self.game_var.trace_add("write", lambda *_: self.refresh())
        self.tool_var.trace_add("write", lambda *_: self.refresh())
        self.refresh()
        root.after(100, self.poll)

    def report_callback_exception(self, exc_type, value, traceback):
        from tkinter import messagebox
        logging.error("Launcher callback failed", exc_info=(exc_type, value, traceback))
        messagebox.showerror("操作未完成", f"{value}\n\n详细信息已保存到数据目录的 logs 文件夹。", parent=self.root)

    @property
    def running(self):
        return bool(self.service and self.service.thread and self.service.thread.is_alive())

    def refresh(self):
        try:
            checked = inspect_game(self.game_var.get(), self.tool_var.get())
        except (ValueError, OSError):
            checked = {"game_available": False, "extractor_available": False, "ready": False}
        self.environment.set("游戏资源：" + ("已找到" if checked["game_available"] else "未找到 data/*.pp")
                             + "    ·    内置 AHM 读写：已就绪")
        locked = self.busy or self.running or self.closing
        for widget in self.entries + self.pickers:
            widget.configure(state="disabled" if locked else "normal")
        self.start_button.configure(state="normal" if checked["ready"] and not locked else "disabled")
        self.open_button.configure(state="normal" if self.running and not self.busy and not self.closing else "disabled")
        self.stop_button.configure(state="normal" if self.running and not self.busy and not self.closing else "disabled")

    def choose_game(self):
        from tkinter import filedialog
        selected = filedialog.askdirectory(parent=self.root, title="选择游戏根目录（或 data 目录）", mustexist=True)
        if selected:
            checked = inspect_game(selected)
            self.game_var.set(checked["game_dir"])

    def start(self):
        from tkinter import messagebox
        if self.busy or self.running or self.closing:
            return
        try:
            self.settings.save(self.game_var.get(), self.tool_var.get())
        except (OSError, ValueError) as error:
            messagebox.showerror("请检查目录", str(error), parent=self.root)
            return
        self.busy = True
        self.status.set("正在启动本地工坊…")
        self.refresh()

        def work():
            try:
                self.service = Service(self.settings)
                url = self.service.start(self.port)
                self.events.put(("started", url))
            except Exception as error:
                logging.exception("Startup failed")
                self.events.put(("error", str(error)))
        threading.Thread(target=work, name="workshop-start", daemon=True).start()

    def reopen(self):
        if self.running:
            open_browser(self.service.url)

    def stop(self):
        if self.busy or not self.running:
            return
        self.busy = True
        self.status.set("正在停止服务，等待当前任务安全完成…")
        self.refresh()

        def work():
            self.service.stop()
            self.events.put(("stopped", None))
        threading.Thread(target=work, name="workshop-stop", daemon=True).start()

    def close(self):
        self.closing = True
        if self.running and not self.busy:
            self.stop()
        elif not self.busy:
            self.root.destroy()
        else:
            self.status.set("正在等待当前任务完成，完成后自动退出…")

    def poll(self):
        try:
            while True:
                event, value = self.events.get_nowait()
                self.busy = False
                if event == "started":
                    self.status.set(f"工坊运行中：{value}。浏览器关闭后可在此重新打开。")
                    if self.closing:
                        self.stop()
                    elif self.browser:
                        open_browser(value)
                elif event == "stopped":
                    self.status.set("工坊已停止，设置和备份已保留。")
                else:
                    self.status.set("启动失败：" + str(value) + "。可点击“查看日志”。")
        except queue.Empty:
            pass
        if self.service and self.service.finished.is_set() and not self.busy:
            self.status.set("工坊已停止。" if not self.service.error else "服务异常：" + str(self.service.error))
        if self.closing and not self.busy and not self.running:
            self.root.destroy()
            return
        self.refresh()
        self.root.after(150, self.poll)


def diagnostics(data_dir):
    import PIL
    import numpy
    import tkinter
    from .server import WEB
    result = {"version": __version__, "frozen": bool(getattr(sys, "frozen", False)),
              "python": sys.version.split()[0], "pillow": PIL.__version__, "numpy": numpy.__version__,
              "tk": tkinter.TkVersion, "web_files": len(list(WEB.rglob("*"))), "archive_backend": "builtin-ahm",
              "required_files_present": all((WEB / name).is_file() for name in
                    ["home/index.html", "texture/app.js", "model/app.js", "vendor/three.core.js"])}
    atomic = data_dir / "diagnostics.json"
    atomic.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="御宅伴侣素材工坊 Windows 便携启动器")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--game-dir", type=Path)
    parser.add_argument("--extractor", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--serve", action="store_true", help="无启动窗口运行（用于自动检查）")
    parser.add_argument("--stop", action="store_true")
    parser.add_argument("--diagnostics", action="store_true")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--no-auto-start", action="store_true")
    parser.add_argument("--gui-check", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error("端口必须在 0 到 65535 之间")
    data_dir = choose_data_dir(args.data_dir)
    handler = setup_logging(data_dir)
    lock = InstanceLock(data_dir / "instance.lock")
    try:
        if args.diagnostics:
            diagnostics(data_dir)
            return 0
        settings = Settings(data_dir)
        existing = managed_service(settings.workspace)
        if args.stop:
            if existing:
                state = json.load(urllib.request.urlopen(existing["url"] + "/api/workshop", timeout=3))
                request = urllib.request.Request(existing["url"] + "/api/shutdown", b"{}",
                                                 {"Content-Type": "application/json", "X-Studio-Token": state["token"]})
                urllib.request.urlopen(request, timeout=10).close()
            return 0
        try:
            lock.acquire()
        except InstanceBusy:
            if existing:
                if not args.no_browser:
                    open_browser(existing["url"])
                return 0
            if args.serve:
                return 2
            from tkinter import messagebox
            messagebox.showinfo("工坊已打开", "启动器已经在运行，请切换到现有的“御宅伴侣素材工坊”窗口。")
            return 0
        if args.game_dir:
            checked = inspect_game(args.game_dir, args.extractor)
            settings.game, settings.extractor = checked["game_dir"], checked["extractor"]
        if args.serve:
            settings.save(settings.game, settings.extractor)
            service = Service(settings)
            try:
                url = service.start(args.port)
                logging.info("READY %s", url)
                if not args.no_browser:
                    open_browser(url)
                service.finished.wait()
            except KeyboardInterrupt:
                service.stop()
            return 1 if service.error else 0
        import tkinter as tk
        root = tk.Tk()
        app = Launcher(root, settings, port=args.port, browser=not args.no_browser)
        if args.gui_check:
            attempts = [0]
            def check_window():
                if args.game_dir and (app.busy or not app.running) and attempts[0] < 100:
                    attempts[0] += 1
                    root.after(150, check_window)
                    return
                from PIL import ImageGrab
                result = {"title": root.title(), "width": root.winfo_width(), "height": root.winfo_height(),
                          "start_enabled": str(app.start_button.cget("state")) == "normal",
                          "game_entry_present": app.entries[0].winfo_exists(),
                          "tool_entry_present": len(app.entries) > 1, "icon_loaded": hasattr(app, "icon"),
                          "service_running": app.running,
                          "content_fits": app.footer.winfo_y() + app.footer.winfo_height() <= app.footer.master.winfo_height()}
                try:
                    if os.name == "nt":
                        from ctypes import windll
                        window = windll.user32.GetParent(root.winfo_id()) or root.winfo_id()
                        ImageGrab.grab(window=window).save(data_dir / "launcher.png")
                    result["passed"] = bool(result["game_entry_present"] and not result["tool_entry_present"] and result["icon_loaded"]
                                            and result["content_fits"] and (not args.game_dir or app.running))
                except Exception as error:
                    logging.exception("GUI check screenshot failed")
                    result["passed"] = False
                    result["error"] = str(error)
                (data_dir / "gui-check.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                app.close()
            if args.game_dir:
                root.after(200, app.start)
            root.after(500, check_window)
        elif not args.no_auto_start and inspect_game(settings.game, settings.extractor)["ready"]:
            root.after(250, app.start)
        root.mainloop()
        return 0
    except Exception:
        logging.exception("Portable startup failed")
        if not args.serve:
            from tkinter import messagebox
            messagebox.showerror("素材工坊未能启动", f"详细错误已保存到：\n{data_dir / 'logs' / 'launcher.log'}")
        return 1
    finally:
        lock.release()
        logging.getLogger().removeHandler(handler)
        handler.close()


if __name__ == "__main__":
    raise SystemExit(main())
