# 从旧项目迁移

v0.3.0 便携版升级到 v0.4.0 时，关闭旧工坊后，将 userdata 完整复制到新版 EXE 同级即可。保留游戏目录、待写入、候选和备份；旧配置里的外部工具路径不再使用，启动器也不再要求选择 PPExtractor。

本项目整合旧贴图工坊、模型工坊，以及模型工坊依赖的头发预览解析/资源准备代码。原目录不删除、不移动，已安装游戏修改与旧工作区保持原状。

| 原入口/依赖 | 新位置 |
| --- | --- |
| 贴图工坊/studio.py、formats.py | homemate_workshop/studio.py、formats.py |
| 模型工坊/model_lab.py | server.py 的模型 API + models.py |
| 模型工坊/model_ops.py | homemate_workshop/model_ops.py |
| 头发预览/model_io.py、prepare.py、vendor | model_io.py、prepare.py、web/vendor |
| generate_recommended.py | `python run.py recommended` |
| render_comparison.py | `python run.py render-comparison` |
| 固定最新批次的 install_recommended.py | `install-models --candidate ID` 或模型页面安装 |
| 单独启停脚本和独立端口 | 统一 start.ps1 / stop.ps1 |

旧的 mild/medium 已安装状态不会自动恢复，也不会把旧备份导入新记录。新“原版”从当前游戏提取；如果要回到旧的安装前基准，先使用原工具及原备份恢复，再用新工具准备。

新工作区默认位于本项目 `workspace`：贴图使用 `textures` 子目录，模型使用 `models` 子目录。不要把它指向旧贴图工作区根目录，避免混淆备份、待写入清单和服务记录。

旧待写入贴图可用旧工具导出贴图补丁，再用新贴图页面“载入补丁”，检查基准资源包哈希后预览。此操作不会自动导入旧备份。旧候选建议在新工具准备当前资源后重新生成，保证原模型基准和资源 URL 一致。

新旧服务可在不同端口打开，但不要同时安装或恢复同一游戏资源包。所有历史备份和原项目继续保留，发布源码时仅选择新项目目录。
