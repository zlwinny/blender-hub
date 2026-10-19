<div align="center">

# Blender Hub

**A unified desktop manager for Blender builds, projects & addons**
*Blender 版本 / 项目 / 插件的一体化桌面管理工具 — Windows · PyQt6 · 100% local*

<p>
<img src="https://img.shields.io/badge/platform-Windows%2010%2F11%20x64-0078D6?logo=windows&logoColor=white" alt="Platform">
<img src="https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white" alt="Python">
<img src="https://img.shields.io/badge/GUI-PyQt6-41CD52?logo=qt&logoColor=white" alt="GUI">
<img src="https://img.shields.io/badge/version-v1.0-E87D0D" alt="Version">
<img src="https://img.shields.io/badge/data-100%25%20local-2DA44E" alt="Local only">
<img src="https://img.shields.io/badge/license-MIT-green" alt="License MIT">
</p>

**🌐 Language:** [English](README.md) · [中文](README_zh.md)

</div>

---

## 💡 Why Blender Hub

After a while with Blender, three annoyances keep coming back:

1. **Builds get out of hand** — you install several versions and lose track of which one is the default, where the LTS build is, and you still have to dig through the download page to get a new one;
2. **Projects get lost** — `.blend` files scatter everywhere and hunting them down in Explorer gets old;
3. **Addons are a chore** — download from a site, unzip by hand, drop it into `scripts/addons`, and then you are not even sure it is compatible.

Blender Hub folds all three into one window: **detect local builds automatically, scan projects (parsing the preview embedded in the `.blend` file), install addons online**.
Everything happens on your machine — it never takes over your files and never uploads anything.

## ✨ Features

### 📂 Projects Project Center

| | |
| --- | --- |
| 🖼 **Real thumbnails** | Parses the preview embedded in the `.blend` file (TEST block, flipped for the OpenGL storage order), falls back to a sibling image or a vector placeholder, cached by mtime |
| 📂 **Directory scan** | Scans common directories (Documents/Blender, Desktop, Downloads, `D:/BlenderProjects`, …) on a cancellable background thread with live progress |
| 🔗 **Version binding** | Each project remembers which Blender opens it, and offers another version when the bound one is missing |
| 🚀 **Quick open** | Double-click a card or press the button; any `.blend` file can also be opened manually |
| 🗂 **Responsive grid** | Cards reflow to fit the window width |

### 🗂 Updates Version Manager

| | |
| --- | --- |
| 🔍 **Auto discovery** | Common directories + registry + Steam + PATH + full-disk deep scan; first screen comes from a local cache and is verified in the background |
| 🏷 **Channel badges** | Each card is tagged LTS / stable / test, with the icon extracted from `blender.exe` |
| 🚀 **One-click launch** | Click a card to start that Blender build |
| ⬇ **Online download** | Stable / LTS / daily builds; multiple mirrors (NJU, Tsinghua, USTC, official); resumable transfers with live progress |
| 🧹 **Safe removal** | Only removes builds installed by this app; external installs (Program Files / Steam) are reported but never deleted |
| 🔄 **Update hints** | Inline "updatable" marker when a newer build of the same series appears; existing installs can also be imported by scanning |

### 🛍 Addons Addon Store

| | |
| --- | --- |
| 🛍 **Online catalog** | Browse the BudecoTop catalog: keyword search, category filter, pagination, totals / official / free counts |
| ⚠ **Compatibility check** | Compares `min_blender` / `max_blender` against the target build and warns before installing |
| 🧰 **Three install paths** | ① direct-link download; ② cloud-drive share (link + extraction code resolved automatically); ③ embedded browser flow (log in → click download → zip is picked up automatically) |
| 🔐 **Sign-in** | QQ QR-code login, or a read-only cookie sync from the local Firefox profile |
| 👀 **Download watcher** | Polls the download directory and verifies + installs newly written zips |
| ✅ **Package verification** | Checks zip structure / size / content integrity before installing |

### 📸 Config Config Center

Two tabs: **Blender config** (snapshot inventory) and **Application settings** (Blender Hub's own preferences).

| | |
| --- | --- |
| 📸 **Config snapshot** | Records a real inventory per Blender version (config files + addon directories, local JSON) |
| 🔄 **Restore & migration** | Planned — the entries are disabled and nothing is written back to Blender |
| ⚙️ **Application settings** | Download mirror (with availability badge), Blender install location, data / log directories, About (version / MIT license / disclaimer) |

### 🖥 Shell & UX

| | |
| --- | --- |
| 🎨 **Immersive dark theme** | Warm brown-grey surfaces with Blender orange (`#E87D0D`) accents and 14px card radius; every icon is drawn with QPainter (2× HiDPI) |
| 🪟 **Frameless window** | Custom title bar, system-level rounding on Windows 11, eight-direction edge resize, remembered geometry |
| 🧭 **Narrow sidebar** | 76px icon navigation, expandable / collapsible, state persisted across sessions |
| 📥 **Global download bar** | A real bottom progress bar (percentage + speed) plus bottom-right toasts |
| 🧩 **Plugin architecture** | Every feature is an independent module: declared by `manifest.json`, loaded by `ModuleManager`, coordinated via `EventBus` |
| 🛡 **Easy to debug** | Popup auditing (every dialog is logged), crash diagnostics (`faulthandler` to disk), module integrity check (manifest + SHA-256) |

> **🌐 Language:** [English](README.md) · [中文](README_zh.md)

## 📸 Screenshots

| Projects | Updates |
| :---: | :---: |
| ![Projects](docs/screenshots/project-center.png) | ![Updates](docs/screenshots/version-library.png) |

| Addons | Config |
| :---: | :---: |
| ![Addons](docs/screenshots/addon-store.png) | ![Config](docs/screenshots/config-center.png) |

## 🚀 Quick start

### Option 1: portable build (recommended)

Download `BlenderHub` from Releases, unzip it anywhere and double-click `BlenderHub.exe`.
It is an onedir portable build — copy the whole folder wherever you want, no installer.

### Option 2: run from source

```bash
git clone https://github.com/zlwinny/blender-hub.git
cd blender-hub
pip install -r requirements.txt
python main.py
```

**Requirements**

- Windows 10 / 11 x64
- Python 3.11+ (built and tested on Python 3.14)
- Any installed Blender version (the app still starts with none, the list is just empty)

On a headless machine (CI smoke test) set `BH_OFFSCREEN=1` to run on the `offscreen` platform.

## 🏗 Architecture

The core layer (`core/`) only owns the shell, the services and the lifecycle. Every feature lives as an independent module under `modules/`, discovered and loaded by `ModuleManager` from its `manifest.json`. Modules collaborate through the `EventBus` and the services injected via `ModuleContext`; a few shared UI pages are still reused by direct import (for example the settings page embedded into Config).

```
main.py                  # entry: init Qt / WebEngine -> build shell -> load modules
core/
├── app_context.py       # application context (service wiring)
├── main_window.py       # shell: title bar / sidebar / download bar / toast
├── module_manager.py    # module discovery, manifest validation, dynamic loading
├── api/module_api.py    # BaseModule / ModuleContext interfaces
├── theme.py             # design tokens and theme
├── widgets.py           # shared widgets (Card / Toast / badge ...)
├── icons.py             # QPainter vector icons
├── popup_audit.py       # popup auditing (duplicates / orphans)
└── services/            # event bus, config, log, download (queue / resume / mirrors),
                         # version detection, addon catalog, package verification,
                         # authentication, integrity check
modules/                 # feature modules (each ships a manifest.json; order drives the sidebar)
├── project_center/      #   Projects
├── version_library/     #   Updates
├── addon_store/         #   Addons
├── config_center/       #   Config
└── settings.py          #   application settings page (embedded in the Config tab)
tests/                   # tests (unit / regression / ui)
tools/                   # one-off migrations and crash diagnostics
packaging/               # PyInstaller spec and icon generation
```

### Adding a module

1. Create `modules/my_module/` with `__init__.py` and `manifest.json`;
2. In `module.py` define a class inheriting from `BaseModule` and implement `get_widget()`;
3. Point `entry_point` at `<module path>:<class name>`;
4. If the module ships data files, add one entry to `datas` in `packaging/BlenderHub.spec` (existing module manifests are collected automatically).

```json
{
  "id": "addon_store",
  "name": "插件",
  "version": "1.0.0",
  "order": 3,
  "entry_point": "modules.addon_store.module:AddonStoreModule",
  "permissions": ["network", "filesystem"],
  "features": ["compatibility_check"]
}
```

## 🧪 Tests

41 test scripts, grouped by whether they need a display, the network, or touch local state. Each one runs standalone and does not depend on the current working directory (they locate the project root via `parents[2]`):

| Directory | Contents |
| --- | --- |
| `tests/unit/` | Pure logic unit tests: addon verifier adversarial cases, Ed25519 (RFC 8032) official vectors |
| `tests/regression/` | One script per reported bug that was fixed |
| `tests/ui/` | Navigation / rounding / DPR / layout probes and captures |

```bash
python tests/unit/test_addon_verifier.py
python tests/regression/_test_lts_flag.py
```

## 📦 Packaging

```bash
pip install -r requirements-dev.txt
pyinstaller packaging/BlenderHub.spec --noconfirm
```

The output is `dist/BlenderHub/` (onedir portable build). Every path in the spec is derived from `SPECPATH`, so a fresh clone builds on any machine without absolute paths.

> **Keep source and release separate**: never write build output back into the source tree. Build with
> `pyinstaller packaging/BlenderHub.spec --noconfirm --distpath ../_build_out`
> to point outside the tree. `build/`, `dist/`, `dist*/`, `build*/` and `__pycache__/` are git-ignored.

## 🔒 Data & privacy

- Everything stays local: configuration lives in the `platformdirs` config directory, caches in the local cache directory.
- **No usage data is collected or reported.**
- The addon page sign-in comes from your own login inside the embedded browser, or cookies you explicitly import from a local browser. Those cookies are **stored on this machine only** (encrypted with Windows DPAPI) and are used solely to reach the site you signed in to; they are never transmitted anywhere else.
- This project only downloads, verifies and extracts addons. It does not bypass any paywall or licensing mechanism — every addon installed through it stays under its own author's license.

## 🗺 Roadmap

- [ ] Default version management (set default / quick switch)
- [ ] Addon uninstall, enable/disable and update check
- [ ] Real read/write of the Blender user config directory in Config
- [ ] Light theme
- [ ] Application auto-update and rollback
- [ ] CI (GitHub Actions)

## ⚠️ Known limitations

- Windows only (relies on the Windows recycle bin, the `%APPDATA%` layout and process path resolution).
- Parsing of the online addon catalog depends on the source site's markup; a site redesign may require adjustments.
- Some addons still need to be enabled manually in Blender's preferences after install.
- Config snapshots currently only record an inventory; restore and cross-version migration are not implemented (entries disabled).
- Dark theme only.
- If WebEngine crashes at startup in a packaged build (0xc0000409), set `BH_UNSAFE_NO_SANDBOX=1` to fall back to disabling the sandbox. This lowers security and is only a troubleshooting fallback.

## 🤝 Contributing

Issues and pull requests are welcome. Run the relevant test scripts first (see "Tests"), and prefer shipping new features as an independent module under `modules/` with its `manifest.json` and a regression test.

## 📄 License

Released under the [MIT License](LICENSE) — free to use, modify and redistribute as long as the copyright notice is kept.

Copyright © 2026 BlenderHub Team

### Disclaimer

The disclaimer is shown on first launch and never again once accepted; it can also be read any time under **Config → Application settings → About**.

The software is provided "as is", without warranty of any kind; any risk from using it is yours. Addons, models and assets installed through it remain under their own authors' copyright and license — use them according to those terms. The software itself is released under the MIT license, which does not restrict commercial use.

## 🙏 Acknowledgements

Thanks to the Blender community and to every addon author.

### About the Blender trademark

Blender Hub is an **independent third-party tool, not affiliated with, endorsed or sponsored by the Blender Foundation**. The word "Blender" is used only to describe what this tool manages (nominative use). The app icon is drawn by this project (`packaging/make_app_icon.py`) and the **official Blender logo is not used** — it is a registered asset of the Blender Foundation and its [official guidelines](https://www.blender.org/about/logo/) forbid using it as the logo of your own product.

---

<div align="center">

**🌐 Language:** [English](README.md) · [中文](README_zh.md) · [Back to top](#blender-hub)

</div>
