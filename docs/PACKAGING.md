# Windows 便携版构建与检查

使用 Windows x64 和独立 Python 环境构建，建议 Python 3.13。PyInstaller 输出文件夹分发包，Python、Pillow、NumPy、Tcl/Tk 与页面资源一起放在 `_internal` 中。

```powershell
python -m venv workspace/portable-build-venv
workspace/portable-build-venv/Scripts/python.exe -m pip install -r requirements-build.txt
workspace/portable-build-venv/Scripts/python.exe tools/build_portable.py
```

输出：

- `dist/HomeMateWorkshop-v0.4.0-windows-x64/素材工坊.exe` 及完整运行文件夹。
- `dist/homemate-asset-workshop-v0.4.0-windows-x64-portable.zip`。
- 对应 `.zip.sha256`。

每个包附带 `portable-manifest.json`，记录版本、运行依赖、文件清单及 SHA256。构建器从编译结果而非工作区复制内容，拒绝包含 userdata、workspace、备份、游戏包、模型、个人配置、日志和 PPExtractor。AHM 编解码已在本项目模块中实现，运行时不需要外部解包器。

项目 MIT、Python、Pillow、NumPy、Tcl/Tk、PyInstaller 与前端库的许可证分别保留。代码签名和自动升级不包含在本版本中。

## 检查实际 EXE

```powershell
python -m pip install -r requirements-dev.txt
python tools/check_portable.py --game-dir "C:/Games/HomeMate"
```

此脚本解压 ZIP 到包含中文和空格的临时路径；清除 Python/Tcl 环境变量并将 PATH 限制为 Windows 系统目录，实际运行 EXE，检查内置依赖、页面资源和首次启动窗口。之后从指定游戏只读提取小型 PP 副本；测试目录不放置 PPExtractor，全部提取/安装/恢复使用内置模块，仅针对临时副本。

检查内容包括贴图原生编码写入与完整恢复、模型候选/预览/安装/恢复、重复启动复用、停止与再次启动、移动程序和 userdata 后复用候选，以及原游戏哈希不变。报告和窗口截图保存到 `workspace/portable-check`。

当前校验在有开发环境的 Windows 主机上使用受限子进程环境进行，不能等同于全新 Windows 虚拟机验收；公开稳定版前仍建议在未安装 Python 的另一台 Windows 电脑试用。

便携版支持 `--data-dir`、`--serve --no-browser`、`--port`、`--stop` 和诊断模式，方便自动检查。普通使用者不需要这些参数。
