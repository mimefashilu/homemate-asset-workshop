# 第三方代码与工具

## Three.js

项目内置 `homemate_workshop/web/vendor/three.module.js`、`three.core.js`、`OrbitControls.js`。

Copyright © 2010–2025 three.js authors。按 MIT License 分发，完整原始许可证见同目录 `THREE-LICENSE.txt`。这些文件来自合并前的本地预览工具，未在本次合并中更新版本。

## Lucide

项目内置 `homemate_workshop/web/vendor/lucide.min.js`。Lucide Contributors 与 Feather/Cole Bemis 相关声明保留在 `LUCIDE-LICENSE.txt`；按该文件所列 ISC/MIT 条款分发。

## Python 依赖

源码方式通过 Python 包管理器安装 Pillow 与 NumPy。Windows 便携版附带 Python、Pillow、NumPy、Tcl/Tk 运行时与 PyInstaller 启动器，并在 licenses 目录保留相应许可声明；PyInstaller 声明包含启动器分发例外。具体构建版本记录在 portable-manifest.json。可选 Playwright 只用于开发测试，不放进便携包。

## 外部游戏工具和格式参考

v0.4.0 起，应用使用独立实现的 AHM 内置读写模块，不再将 PPExtractor 作为运行依赖，也不随附其 EXE 或复制其源码。

开发者可选择用自己提供的 `PPExtractor0.6.2.exe` 运行交叉验证测试；它仅作为开发参考工具，不进入源码或便携发布包。项目 MIT 许可不授予该外部程序的再分发权限。

合并前项目的格式实现参考了 SB3Utility 的公开格式资料；本项目没有随附完整 SB3Utility 源码或二进制。原实现的来源说明在贴图工作流文档中保留。

## 游戏素材与可选生图模板

本项目不包含游戏的资源包、模型、原始贴图、动画或生成缓存。运行后生成的预览、候选、补丁和备份留在工作区。

ComfyUI 工作流模板使用者需要自行安装对应模型和节点。实验模板不等于附带模型权重或素材使用授权。

本项目自身源码按 MIT License 分发，见 LICENSE。以上第三方声明继续分别适用。
