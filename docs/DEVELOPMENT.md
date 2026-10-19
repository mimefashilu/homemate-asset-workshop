# 开发、测试与发布

## 结构

```text
homemate_workshop/
  __main__.py         CLI、路径配置、服务生命周期
  server.py           统一 HTTP 路由、会话校验、静态文件
  studio.py           贴图资源、工作区、原生调用、安装与恢复
  formats.py          AHM PP 目录、XX 贴图解析与写回
  model_io.py         XX v2 网格布局与 Three.js 几何
  model_ops.py        有位移边界的头发几何操作与候选
  prepare.py          从用户游戏生成模型预览
  models.py           候选目录、安装检查、目标替换
  render_comparison.py 三视图报告
  web/                home、texture、model、vendor
  comfyui/            可选工作流和转换工具
```

网页入口 `/`、`/textures/`、`/models/`、`/guide/`。API 分为 `/api/workshop`、`/api/health`、`/api/textures/*`、`/api/models/*`。共享解析器不使用旧目录的 sys.path 注入。贴图与模型后台共用 Studio 的锁、会话令牌和备份目录。

## 自动测试

```powershell
python -m pip install -e .
python -m unittest discover -s tests -v
```

核心测试使用程序生成的 PP/XX、图片和模拟原生编解码器，验证结构保持、选择范围、备份恢复、冲突拒绝、路径边界、令牌及干净启动，不需要游戏资源。

可选浏览器综合检查：`python tools/check_browser.py`。它同样使用生成样本，操作统一导航、贴图采样/导出、模型生成/导出/检查/安装和恢复，检查脚本与 HTTP 错误，在 `workspace/browser-check` 保存桌面/移动截图。

真实原生编码与 UI 测试只从用户指定目录读取，先构建临时的小型资源包副本，再对副本进行写入/恢复。原游戏不作为写入目标：

```powershell
python -m pip install -r requirements-dev.txt
python -m playwright install chromium
$env:HM_TEST_GAME_DIR = 'C:/Games/HomeMate'
python -m unittest discover -s tests/integration -v
Remove-Item Env:HM_TEST_GAME_DIR
```

浏览器测试会优先使用 Playwright Chromium，其次尝试已安装 Chrome/Edge。真实游戏测试不在 CI 中运行，也不上传本机数据。CI 定义运行便携核心测试和源码包检查；仓库推送后的实际 GitHub CI 结果仍需在 GitHub 查看。

## 打包

```powershell
python tools/package_release.py
```

输出 `dist/homemate-asset-workshop-v0.2.0-source.zip` 与 `.zip.sha256`。发布包采用路径/后缀白名单和大小检查，过滤工作区、日志、私有配置和游戏二进制文件，并检查文本中的常见个人用户路径和凭据形态。白名单新增目录或资源类型时同步检查包内容。

Python wheel 可选：`python -m build`。包数据声明包含网页、前端库和 ComfyUI 模板；安装后显式设置本地配置/工作区，避免写入 Python 安装目录。

## 本地约束

- 先制作候选，检查通过再安装。GUI 或 CLI 的显式安装操作才写游戏。
- 只在本机回环地址监听。写接口验证 Host、Origin 和会话令牌。
- 候选文件始终从当前工作区相对位置读取，不信任旧记录中的绝对路径。
- 安装检查原始文件、当前游戏模型、编码往返和非目标成员，备份后替换。
- 核心锁是单进程锁；不要同时启动多个工作区写入进程或在网页服务运行时执行写入 CLI。多进程并行编辑不是支持范围。
- 性能和视觉验收要在实际游戏中独立进行；本次合并不改变渲染配置。
