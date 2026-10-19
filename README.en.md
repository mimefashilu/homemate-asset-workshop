# HomeMate Asset Workshop

A local browser application combining the existing texture and model workshops for HomeMate. One Python service provides a dashboard, texture editing, XX v2 hair model candidates, validation, and shared backup/restore records.

See the [Chinese README](README.md), [feature matrix](docs/FEATURES.md), and [development guide](docs/DEVELOPMENT.md) for details.

## Run

Requirements: Windows, Python 3.10+, Pillow, NumPy, your own game installation, and your own `data/PPExtractor0.6.2.exe` for AHM resource extraction/encoding. Game files and the extractor are not distributed here. The dashboard works without game data.

```powershell
python -m pip install -r requirements.txt
Copy-Item config.example.json config.local.json
```

Set `game_dir` in `config.local.json` to your game root, using forward slashes or escaped backslashes. Relative paths resolve against the configuration file directory. With no custom configuration, the game directory defaults to the project's parent and the workspace to `workspace` inside the project.

Double-click `启动素材工坊.cmd`, or run:

```powershell
python run.py --game-dir "C:/Games/HomeMate"
python run.py stop
```

The server binds to loopback only, starting at port 8765 and trying later ports when needed. `停止素材工坊.cmd` stops the managed server. Optional editable installation: `python -m pip install -e .`; the entry point is `homemate-workshop`.

## Workflow

- Textures: browse/filter, export PNG or native images, redraw, import, inspect comparison/alpha, exit the game, back up and apply. Batch imports use the generated `texture-manifest.json`. Texture patch ZIPs can be exported and imported.
- Models: prepare previews from the current game, select hair geometry, create smooth/inflate/contract candidates, inspect heatmaps and reference outlines, export XX or run preflight and install. Only supported XX v2 hair geometry is editable; topology, UVs, weights, bones, materials and textures are protected.
- Backups: texture and model installations share the backup catalog. Restore in reverse installation order. Changed archives are rejected rather than overwritten.
- CLI: `prepare-models`, `recommended`, `render-comparison`, and `install-models --candidate ID`. Stop the web service before running workspace-writing CLI commands.

Generated resources, backups, private paths, models and game archives are excluded from Git and source ZIPs. Existing standalone workshops and their workspaces are not automatically migrated. File validation does not replace checking appearance and animation in the game.

## Develop and distribute

```powershell
python -m unittest discover -s tests -v
python tools/package_release.py
```

Portable tests generate their own PP/XX samples. Private integration tests may use copies from a game installation; see the development guide. CI runs portable tests on Windows and Linux with Python 3.10 and 3.13. Linux is for source validation, not native game-resource support.

The project license has not yet been selected by its maintainer. Bundled Three.js and Lucide retain their own notices in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Game assets, proprietary tools and generated texture patches are separate from this source project.
