# 可选头发贴图实验工作流

这里保留合并前的 Qwen Image 2.1 头发贴图与细节层模板，需要使用者自行准备对应 ComfyUI、模型权重及节点。实验模板不保证保持 UV、发片方向、配色或透明轮廓。先做单张样本，通过原图对照后再导入。

`Qwen_hair_texture_i2i.json` 使用 PNG 输入输出、原尺寸设置和原图参考，当前 denoise 为 0.2。`Qwen_hair_detail_tile.json` 用于生成细节层，不能直接替换 UV 贴图。

在项目根目录执行，显式提供自己的目录：

```powershell
python homemate_workshop/comfyui/hair_texture_pipeline.py prepare --game-dir "C:/Games/HomeMate" --input-dir "C:/ComfyUI/input" --model g_k_41.xx --texture b_t_02_b_01.bmp
```

脚本从当前游戏提取指定内嵌贴图，保存 PNG 和工作流到项目 `workspace/hair-texture`，并将输入 PNG 复制到指定 ComfyUI input。不会写游戏。

生成后转换回目标格式：

```powershell
python homemate_workshop/comfyui/hair_texture_pipeline.py convert-back --original "workspace/hair-texture/g_k_41-b_t_02_b_01/g_k_41_b_t_02_b_01.png" --generated "C:/ComfyUI/output/result.png" --output "workspace/hair-texture/g_k_41-b_t_02_b_01/generated.bmp"
```

转换检查原尺寸并复用原 Alpha；BMP 保存为 RGB。导入工坊后继续检查原图/待写入对照。`hair_texture_composite.py --help` 提供细节层低比例合成选项，输出候选图片，不自动安装。

RGB 生成结果需要透明区域时，使用 `restore-original-alpha.json` 或工坊默认的保留原透明通道。16×16 辅助色块不应作为主发丝贴图重绘。
