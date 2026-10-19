# HomeMate Asset Workshop

A local browser application combining the existing texture and model workshops for HomeMate. One Python service provides a dashboard, texture editing, XX v2 hair model candidates, validation, and shared backup/restore records.

See the [Chinese README](README.md), [feature matrix](docs/FEATURES.md), and [development guide](docs/DEVELOPMENT.md) for details.

## Windows portable edition

Target: Windows 10/11 x64; tested on Windows 11. The portable ZIP includes Python, Pillow, NumPy and a native launcher. No Python installation, pip commands or manual JSON configuration are required. The editing interface opens in the default browser.

1. Download the **windows-x64-portable.zip** release and extract the entire folder.
2. Double-click **素材工坊.exe**, keeping the accompanying `_internal` folder.
3. Select the game root containing `data/*.pp`, or its `data` directory.
4. Start the workshop. AHM archive reading/writing is built in, and the game path is saved for future launches.

Only your own game is required. Since v0.4.0, an independently implemented AHM archive backend is included: no PPExtractor download or EXE selection is needed. The historical EXE is not bundled. See [backend verification](docs/ARCHIVE_BACKEND.md). Settings, candidates, exports, backups and logs are saved in `userdata` beside the EXE, falling back to a displayed user-local directory if necessary. Closing the launcher waits for active work and stops the service; repeated launches reuse the existing instance.

For updates, stop the old version, extract the new version elsewhere and copy the old `userdata` beside the new EXE. See [portable instructions](docs/PORTABLE.md) and [packaging checks](docs/PACKAGING.md).

## Run from source

Requirements: Windows, Python 3.10+, Pillow, NumPy and your own game installation. AHM extraction/encoding is built in. Game files are not distributed here; the dashboard works without game data.

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

The project source is licensed under **MIT**, see [LICENSE](LICENSE). Third-party components retain their own licenses, included with portable distributions and listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Game assets, proprietary tools and user-created patches are separate from the project license and distribution.
