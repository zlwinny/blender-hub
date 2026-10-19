"""界面词条表（中文 / 英文）。

key -> {"zh": ..., "en": ...}
新增文案：在这里加一行，再用 i18n.tr("key") 替换代码里的硬编码字符串。
"""

STRINGS: dict[str, dict[str, str]] = {
    # ── 通用 ──────────────────────────────────────────────────────────
    "common.cancel": {"zh": "取消", "en": "Cancel"},
    "common.confirm": {"zh": "确定", "en": "OK"},
    "common.close": {"zh": "关闭", "en": "Close"},
    "common.delete": {"zh": "删除", "en": "Delete"},
    "common.refresh": {"zh": "刷新", "en": "Refresh"},
    "common.save": {"zh": "保存", "en": "Save"},
    "common.browse": {"zh": "浏览…", "en": "Browse…"},
    "common.copy": {"zh": "复制", "en": "Copy"},
    "common.open": {"zh": "打开", "en": "Open"},
    "common.yes": {"zh": "是", "en": "Yes"},
    "common.no": {"zh": "否", "en": "No"},

    # ── 页面标题（侧栏对应名称在 manifest.json 的 name / name_en）─────
    "module.project.title": {"zh": "📁 项目", "en": "📁 Projects"},
    "module.version.title": {"zh": "📦 更新", "en": "📦 Updates"},
    "module.addon.title": {"zh": "\U0001f6d2 插件", "en": "\U0001f6d2 Addons"},
    "module.config.title": {"zh": "⚙️ 配置", "en": "⚙️ Config"},

    # ── 配置页 ────────────────────────────────────────────────────────
    "config.tab.blender": {"zh": "Blender 配置", "en": "Blender config"},
    "config.tab.app": {"zh": "应用设置", "en": "App settings"},
    "config.list.title": {"zh": "配置快照列表", "en": "Snapshots"},
    "config.detail.title": {"zh": "快照详情", "en": "Details"},
    "config.actions.title": {"zh": "快照操作", "en": "Actions"},
    "config.create": {"zh": "➕ 新建快照", "en": "➕ New snapshot"},
    "config.restore": {"zh": "📥 恢复配置（规划中）", "en": "📥 Restore (planned)"},
    "config.migrate": {"zh": "🔄 跨版本迁移（规划中）", "en": "🔄 Migrate (planned)"},
    "config.hint": {
        "zh": "恢复 / 迁移尚未实现，当前仅记录配置清单，不会改动 Blender 配置。",
        "en": "Restore and migration are not implemented yet — only an inventory is recorded, nothing is written back to Blender.",
    },
    "config.empty": {"zh": "暂无配置快照", "en": "No snapshots yet"},
    "config.detail.placeholder": {"zh": "选择一个快照查看详情", "en": "Select a snapshot to see its details"},
    "config.detail.blender_version": {"zh": "Blender 版本", "en": "Blender version"},
    "config.detail.created": {"zh": "创建时间", "en": "Created"},
    "config.detail.description": {"zh": "描述", "en": "Description"},
    "config.detail.config_dir": {"zh": "配置目录", "en": "Config directory"},
    "config.detail.exists": {"zh": "目录存在", "en": "Directory exists"},
    "config.detail.files": {"zh": "配置文件", "en": "Config files"},
    "config.detail.addons": {"zh": "插件目录", "en": "Addon directories"},
    "config.detail.more_files": {"zh": "其余", "en": "more"},
    "config.detail.tip": {"zh": "💡 快照仅记录配置清单，恢复 / 迁移为规划功能。", "en": "💡 Snapshots only record an inventory — restore and migration are planned."},
    "config.dialog.create_title": {"zh": "创建配置快照", "en": "Create snapshot"},
    "config.dialog.name": {"zh": "名称", "en": "Name"},
    "config.dialog.description": {"zh": "描述", "en": "Description"},
    "config.dialog.blender_version": {"zh": "Blender 版本", "en": "Blender version"},
    "config.dialog.auto_detect": {"zh": "自动检测…", "en": "Auto-detect…"},
    "config.planned.title": {"zh": "规划功能", "en": "Planned"},
    "config.restore.planned": {
        "zh": "恢复配置尚未实现。\n\n当前版本只记录配置清单，不会写回 Blender 配置目录。",
        "en": "Restoring config is not implemented.\n\nThis version only records an inventory and never writes back to the Blender config directory.",
    },
    "config.migrate.planned": {
        "zh": "跨版本迁移尚未实现。\n\n当前版本只记录配置清单，不会改动 Blender 配置。",
        "en": "Cross-version migration is not implemented.\n\nThis version only records an inventory and never touches Blender config.",
    },

    # ── 应用设置页 ────────────────────────────────────────────────────
    "settings.header.title": {"zh": "设置", "en": "Settings"},
    "settings.header.subtitle": {"zh": "下载镜像源、安装位置与本地数据", "en": "Mirrors, install location and local data"},
    "settings.mirror.label": {"zh": "下载镜像源", "en": "Download mirror"},
    "settings.mirror.testing": {"zh": "可用性检测中…", "en": "Checking availability…"},
    "settings.mirror.available": {"zh": "镜像源可用：", "en": "Mirror available:"},
    "settings.mirror.unavailable": {"zh": "镜像源不可用：", "en": "Mirror unavailable:"},
    "settings.install_dir.label": {"zh": "Blender 安装位置", "en": "Blender install location"},
    "settings.install_dir.hint": {"zh": "提示：路径指向的程序文件夹不存在，将不会被识别。", "en": "Tip: the folder does not exist, so it will not be detected."},
    "settings.dirs.label": {"zh": "本地数据", "en": "Local data"},
    "settings.dirs.data": {"zh": "数据目录", "en": "Data directory"},
    "settings.dirs.logs": {"zh": "日志目录", "en": "Log directory"},
    "settings.language.label": {"zh": "界面语言", "en": "Interface language"},
    "settings.language.applied": {"zh": "界面语言已切换", "en": "Interface language switched"},
    "settings.about.description": {
        "zh": "Blender 版本 / 项目 / 插件的一体化桌面管理工具。本地优先，开源无广告。",
        "en": "An all-in-one desktop manager for Blender builds, projects and addons. Local-first, open source, no ads.",
    },
    "settings.about.maintainer": {"zh": "维护者", "en": "Maintainer"},
    "settings.about.more": {"zh": "更多", "en": "More"},
    "settings.about.disclaimer": {"zh": "免责声明", "en": "Disclaimer"},
    "settings.about.license": {"zh": "开源许可证 MIT", "en": "MIT license"},
    "settings.about.site": {"zh": "Blender 官网", "en": "Blender website"},
    "settings.about.manual": {"zh": "Blender 手册", "en": "Blender manual"},
    "settings.about.log_dir": {"zh": "打开日志目录", "en": "Open log directory"},
    "settings.license.title": {"zh": "开源许可证", "en": "License"},
    "settings.license.fallback": {
        "zh": "MIT License\n\nCopyright (c) 2026 BlenderHub Team\n\n本软件基于 MIT 许可开源：允许在保留版权声明的前提下自由使用、修改与再分发。",
        "en": "MIT License\n\nCopyright (c) 2026 BlenderHub Team\n\nReleased under the MIT license: free to use, modify and redistribute as long as the copyright notice is kept.",
    },

    # ── 免责声明 ──────────────────────────────────────────────────────
    "disclaimer.title": {"zh": "免责声明", "en": "Disclaimer"},
    "disclaimer.text": {
        "zh": (
            "本软件按「现状」提供，不附带任何明示或暗示的担保；"
            "使用本软件所产生的任何风险与后果由使用者自行承担。\n\n"
            "本软件仅提供 Blender 版本、项目与插件的本地管理能力，不代为下载、分发或授权任何第三方插件与素材。"
            "通过本软件安装的插件、模型、贴图等资源，其版权与授权仍归各自作者所有，"
            "请在遵守相应许可的前提下使用。\n\n"
            "本软件基于 MIT 许可开源，允许在保留版权声明的前提下自由使用、修改与再分发；"
            "请勿将本软件用于违反当地法律法规的用途。"
        ),
        "en": (
            "This software is provided \"as is\", without warranty of any kind; "
            "any risk arising from its use is yours.\n\n"
            "It only manages Blender builds, projects and addons locally — it does not download, "
            "distribute or license any third-party addon or asset. Addons, models and textures installed "
            "through it remain under their own authors' copyright and license; use them accordingly.\n\n"
            "It is released under the MIT license: free to use, modify and redistribute as long as the "
            "copyright notice is kept. Do not use it for anything that violates local laws."
        ),
    },
    "disclaimer.agree": {"zh": "同意", "en": "Agree"},
    "disclaimer.quit": {"zh": "退出", "en": "Quit"},

    # ── 语言名称 ──────────────────────────────────────────────────────
    "lang.zh": {"zh": "简体中文", "en": "Simplified Chinese"},
    "lang.en": {"zh": "English", "en": "English"},
}
