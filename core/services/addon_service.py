"""
插件数据服务 — BudecoTop 单一数据源（2026-10-17 按用户要求收敛）

数据源：
1. BudecoTop (budeco.top) — Blender 中文资源整合站，唯一渠道

历史说明：曾支持官方扩展平台 / GitHub / Gitee / 本地目录四路来源及
GitHub 代理镜像，用户明确要求"只保留 budeco.top，别的途径删掉"，
相关代码已全部移除（官方 API 拉取、GitHub/Gitee 搜索、本地 zip 扫描、
镜像前缀开关）。

特性：
- BudecoTop 爬取（带真实缩略图）
- 缩略图自动下载缓存
- 插件兼容性元数据
"""

import json
import re
import time
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeout
from pathlib import Path
from typing import Optional, List, Dict, Callable
from dataclasses import dataclass, field

import requests
from platformdirs import user_cache_dir


@dataclass
class AddonInfo:
    """插件信息数据结构"""
    id: str
    name: str
    author: str
    category: str
    description: str
    rating: float = 0.0
    downloads: int = 0
    min_blender: str = "2.80"
    max_blender: str = "4.2"
    official: bool = False
    free: bool = True
    price: str = ""
    icon: str = "📦"
    source: str = "unknown"          # github / gitee / blender_org / community
    source_url: str = ""             # 项目主页
    download_url: str = ""           # 直接下载链接
    thumbnail_url: str = ""          # 缩略图 URL
    version: str = "1.0.0"
    tags: List[str] = field(default_factory=list)
    license: str = "GPL-3.0"
    installed: bool = False
    cached_at: float = 0.0


class AddonService:
    """多源插件数据聚合服务"""

    # === 数据源配置 ===

    # BudecoTop（Blender 中文资源整合站）— 唯一爬虫数据源（2026-10-17 收敛）
    BUDECO_BASE = "https://budeco.top"
    BUDECO_PLUGINS_URL = "https://budeco.top/category/plugins"
    BUDECO_SEARCH_URL = "https://budeco.top/?s={keyword}"
    BUDECO_PAGE_URL = "https://budeco.top/category/plugins/page/{page}"
    # 全量翻页安全上限（实测插件分类 60+ 页仍未翻完，1200+ 篇）
    BUDECO_MAX_PAGES = 200

    # 分类映射
    CATEGORIES = ["全部", "建模", "雕刻", "渲染", "动画", "导入导出", "社区精选"]

    def __init__(self, cache_dir: Optional[Path] = None, log_service=None):
        self._log = log_service
        # 缓存目录必须是绝对路径：旧实现在 cache_dir=None 时退化为相对路径
        # Path("addons_cache")，于是在任意 CWD 下 mkdir，留下垃圾目录。
        if cache_dir is None:
            cache_dir = Path(user_cache_dir("blenderhub")) / "addons_cache"
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._thumbnails_dir = self._cache_dir / "thumbnails"
        self._thumbnails_dir.mkdir(parents=True, exist_ok=True)
        # BudecoTop 全量目录落盘缓存：首次全量爬取（60+ 页约 2 分钟）后写盘，
        # 之后每次启动先读缓存立即显示，后台只增量爬前几页找新插件。
        self._catalog_path = self._cache_dir / "budeco_catalog.json"

        self._addons: Dict[str, AddonInfo] = {}
        self._lock = threading.Lock()
        self._search_cache: Dict[str, tuple] = {}  # key -> (results, timestamp)
        self._search_cache_ttl = 300  # 5分钟缓存

        # 在线刷新状态（节流 + 防并发）
        self.MIN_REFRESH_INTERVAL = 600.0   # 秒；10 分钟内不重复打数据源
        # 全量爬取 60+ 页约需 2 分钟，超时要覆盖整轮（旧值 40s 会截断）
        self.REFRESH_TIMEOUT = 240.0
        self._last_refresh_ts: float = 0.0
        self._refreshing = False
        self._refresh_thread: Optional[threading.Thread] = None

        # 启动即加载落盘目录（秒开，不等网络）
        self._load_catalog()

    def _load_catalog(self) -> int:
        """从磁盘加载 BudecoTop 全量目录缓存（JSON）。"""
        if not self._catalog_path.is_file():
            return 0
        try:
            import json as _json

            raw = _json.loads(self._catalog_path.read_text(encoding="utf-8"))
            loaded = 0
            with self._lock:
                for item in raw.get("addons", []):
                    try:
                        addon = AddonInfo(**item)
                        self._addons[addon.id] = addon
                        loaded += 1
                    except Exception:  # noqa: BLE001
                        continue  # 单条字段不兼容跳过，不拖垮整批
            if self._log:
                self._log.info(f"Loaded {loaded} addons from disk catalog", module="addon_service")
            return loaded
        except Exception as e:  # noqa: BLE001
            if self._log:
                self._log.warning(f"catalog load failed: {e}", module="addon_service")
            return 0

    def _save_catalog(self) -> None:
        """把当前全量目录写盘（原子写：临时文件 + replace）。"""
        try:
            import json as _json
            from dataclasses import asdict

            with self._lock:
                items = [asdict(a) for a in self._addons.values()]
            tmp = self._catalog_path.with_suffix(".tmp")
            tmp.write_text(_json.dumps({"addons": items}, ensure_ascii=False),
                           encoding="utf-8")
            tmp.replace(self._catalog_path)
        except Exception as e:  # noqa: BLE001
            if self._log:
                self._log.warning(f"catalog save failed: {e}", module="addon_service")

    def search(self, keyword: str = "", category: str = "全部") -> List[AddonInfo]:
        """搜索插件（先查缓存，后台刷新）"""
        cache_key = f"{keyword}|{category}"
        now = time.time()

        # 检查缓存
        with self._lock:
            if cache_key in self._search_cache:
                results, ts = self._search_cache[cache_key]
                if now - ts < self._search_cache_ttl:
                    return list(results)

        # 从内存数据筛选
        results = self._filter_addons(keyword, category)

        # 更新缓存 —— **空结果不缓存**：budeco 数据是启动后异步拉取的，
        # 启动瞬间的"0 条"是瞬态；缓存空结果会让接下来几分钟内所有
        # 搜索/筛选都命中空缓存（用户看到"全部里面不显示"）。
        if results:
            with self._lock:
                self._search_cache[cache_key] = (results, now)

        return results

    def _filter_addons(self, keyword: str, category: str) -> List[AddonInfo]:
        """筛选插件列表"""
        with self._lock:
            addons = list(self._addons.values())

        keyword_lower = keyword.lower().strip() if keyword else ""

        filtered = []
        for addon in addons:
            # 分类筛选
            if category == "全部":
                pass
            elif category == "官方":
                if not addon.official:
                    continue
            elif category == "社区精选":
                if addon.official:
                    continue
            elif addon.category != category:
                continue

            # 关键词筛选
            if keyword_lower:
                match_name = keyword_lower in addon.name.lower()
                match_desc = keyword_lower in addon.description.lower()
                match_tags = any(keyword_lower in tag.lower() for tag in addon.tags)
                if not (match_name or match_desc or match_tags):
                    continue

            filtered.append(addon)

        # 排序：官方优先 → 评分高优先 → 下载量高优先
        filtered.sort(key=lambda a: (
            not a.official,
            -a.rating,
            -a.downloads,
        ))

        return filtered

    def get_addon(self, addon_id: str) -> Optional[AddonInfo]:
        """获取单个插件详情"""
        with self._lock:
            return self._addons.get(addon_id)

    # === 在线搜索（GitHub / Gitee） ===


    def search_budeco(self, keyword: str = "", max_results: int = 100000,
                      max_pages: Optional[int] = None,
                      incremental_pages: int = 0) -> List[AddonInfo]:
        """从 budeco.top 爬取 Blender 插件（带真实缩略图）。

        分类列表页每页 20 篇（div.post），翻页 URL 为
        category/plugins/page/{N}。

        - max_pages=None → 全量翻到空页（用户要求"全部插件"，
          实测 60+ 页 1200+ 篇，硬上限 BUDECO_MAX_PAGES 防死循环）
        - incremental_pages>0 → 只爬前 N 页（增量刷新：新内容在页首）
        """
        results = []
        try:
            from bs4 import BeautifulSoup

            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }

            # 选择搜索或列表页
            if keyword.strip():
                url = self.BUDECO_SEARCH_URL.replace("{keyword}", requests.utils.quote(keyword.strip()))
            else:
                url = self.BUDECO_PLUGINS_URL

            page = 1
            page_cap = max_pages if max_pages is not None else self.BUDECO_MAX_PAGES
            if incremental_pages:
                page_cap = min(page_cap, incremental_pages)
            while len(results) < max_results and page <= page_cap:
                page_url = url if page == 1 else self.BUDECO_PAGE_URL.replace("{page}", str(page))
                resp = requests.get(page_url, headers=headers, timeout=20)
                if resp.status_code != 200:
                    break
                soup = BeautifulSoup(resp.text, "html.parser")
                articles = soup.find_all("div", class_="post")
                if not articles:
                    break  # 空页 = 翻完了
                for article in articles:
                    if len(results) >= max_results:
                        break
                    addon = self._parse_budeco_card(article)
                    if addon:
                        results.append(addon)
                page += 1
                # 温和爬取：页间稍歇，避免触发站点限流
                if page <= page_cap:
                    time.sleep(0.2)

        except ImportError:
            if self._log:
                self._log.warning("beautifulsoup4 not installed, budeco source disabled", module="addon_service")
        except Exception as e:
            if self._log:
                self._log.warning(f"BudecoTop search failed: {e}", module="addon_service")

        return results

    def _parse_budeco_card(self, article) -> Optional[AddonInfo]:
        """解析 budeco.top 单个插件卡片"""
        try:
            # 缩略图 — 真实图片在 data-src 属性
            img = article.find("img", class_="thumb")
            thumbnail_url = ""
            if img:
                thumbnail_url = img.get("data-src", "") or img.get("src", "")
                # 排除默认占位图
                if "thumbnail.png" in thumbnail_url:
                    thumbnail_url = ""

            # 标题和链接
            h3 = article.find("h3")
            name = ""
            source_url = ""
            if h3:
                a_tag = h3.find("a")
                if a_tag:
                    name = a_tag.get_text(strip=True)
                    source_url = a_tag.get("href", "")

            if not name:
                return None

            # 从链接提取 post ID
            post_id = ""
            import re
            m = re.search(r"/(\d+)\.html", source_url)
            if m:
                post_id = m.group(1)

            # 描述
            excerpt = article.find("div", class_="excerpt")
            description = excerpt.get_text(strip=True) if excerpt else ""
            if not description:
                # 备用：从 meta description 取
                meta_desc = article.find("meta", attrs={"name": "description"})
                if meta_desc:
                    description = meta_desc.get("content", "")

            # 作者
            author_span = article.find("span", class_="author-name")
            author = author_span.get_text(strip=True) if author_span else "Blender布的"

            # 是否免费
            vip_tag = article.find("span", class_="vip-tag")
            is_free = True
            if vip_tag:
                tag_text = vip_tag.get_text(strip=True)
                is_free = "免费" in tag_text

            # 推断分类
            category = self._guess_category(name, description)

            # 生成唯一 ID
            addon_id = f"budeco:{post_id}" if post_id else f"budeco:{name[:30]}"

            return AddonInfo(
                id=addon_id,
                name=name,
                author=author,
                category=category,
                description=description[:200],
                rating=4.0,
                downloads=0,
                source="budeco",
                source_url=source_url,
                download_url="",
                thumbnail_url=thumbnail_url,
                icon="🧩",
                tags=self._extract_tags(name, description),
                license="未知",
                free=is_free,
                price="" if is_free else "付费/充电",
                cached_at=time.time(),
            )

        except Exception:
            return None

    def _extract_tags(self, name: str, description: str) -> List[str]:
        """从名称和描述提取标签"""
        tags = []
        keyword_map = {
            "建模": ["建模", "model", "mesh", "boolean", "拓扑", "retopo"],
            "渲染": ["渲染", "render", "材质", "material", "shader", "灯光", "light", "纹理", "贴图"],
            "动画": ["动画", "animation", "rig", "骨骼", "绑定", "动作"],
            "导入导出": ["导入", "导出", "import", "export", "格式"],
            "雕刻": ["雕刻", "sculpt", "笔刷"],
            "特效": ["粒子", "particle", "流体", "fluid", "火焰", "烟雾", "爆炸", "破碎", "布料", "毛发"],
            "UV": ["UV", "展UV", "贴图"],
            "节点": ["节点", "node", "几何节点", "geonode"],
            "硬表面": ["硬表面", "hard surface", "布尔", "切割"],
            "建筑": ["建筑", "arch", "城市", "室内", "景观"],
            "AI": ["AI", "人工智能", "stable diffusion"],
            "效率": ["效率", "工具", "toolkit", "工作流"],
            "程序化": ["程序化", "procedural", "参数化"],
        }
        text = (name + " " + description).lower()
        for tag, kws in keyword_map.items():
            if any(kw.lower() in text for kw in kws):
                tags.append(tag)
        return tags[:4]  # 最多4个标签

    def _guess_category(self, name: str, description: str) -> str:
        """根据名称和描述推断插件分类"""
        text = (name + " " + description).lower()
        category_keywords = {
            "建模": ["model", "mesh", "boolean", "bevel", "extrude", "retopo", "topology", "cad", "建筑", "墙体"],
            "雕刻": ["sculpt", "brush", "texture paint", "雕刻", "笔刷"],
            "渲染": ["render", "material", "shader", "light", "texture", "材质", "灯光", "渲染"],
            "动画": ["rig", "animation", "armature", "pose", "骨骼", "绑定", "动画", "动作"],
            "导入导出": ["import", "export", "io", "format", "导入", "导出", "格式"],
        }
        for cat, keywords in category_keywords.items():
            if any(kw in text for kw in keywords):
                return cat
        return "建模"  # 默认分类

    # === 缩略图管理 ===

    def get_thumbnail_path(self, addon_id: str) -> Optional[Path]:
        """获取插件缩略图本地路径"""
        path = self._thumbnails_dir / f"{addon_id}.png"
        return path if path.exists() else None

    def download_thumbnail(self, addon_id: str, url: str) -> Optional[Path]:
        """下载插件缩略图到本地缓存"""
        if not url:
            return None

        path = self._thumbnails_dir / f"{addon_id}.png"
        if path.exists():
            return path

        try:
            resp = requests.get(url, timeout=10, stream=True)
            if resp.status_code == 200:
                with open(path, "wb") as f:
                    for chunk in resp.iter_content(8192):
                        f.write(chunk)
                return path
        except Exception:
            pass

        return None

    # === 统计信息 ===

    def get_stats(self) -> dict:
        """获取插件库统计"""
        with self._lock:
            addons = list(self._addons.values())

        return {
            "total": len(addons),
            "official": sum(1 for a in addons if a.official),
            "free": sum(1 for a in addons if a.free),
            "paid": sum(1 for a in addons if not a.free),
            "sources": {"budeco": sum(1 for a in addons if a.source == "budeco")},
        }

    def is_refreshing(self) -> bool:
        """当前是否有刷新在进行（供 UI 轮询真实完成状态）"""
        with self._lock:
            return self._refreshing

    def refresh_cache(self, wait: bool = False, timeout: float = 45.0) -> int:
        """刷新在线数据（唯一数据源：budeco.top，2026-10-17 按用户要求收敛）。

        用户明确要求插件市场只保留 budeco.top 一个扩展库，其余途径
        （官方 extensions API / GitHub / Gitee / 本地目录）已全部移除。

        - 带节流：距上次成功刷新不足 MIN_REFRESH_INTERVAL 秒则跳过
        - 已在刷新中时直接复用现有线程，不重复打接口
        - 返回新增数量；wait=True 时阻塞等待完成
        """
        now = time.time()
        with self._lock:
            if self._refreshing:
                existing = self._refresh_thread
            else:
                existing = None
                last = self._last_refresh_ts
                if last and (now - last) < self.MIN_REFRESH_INTERVAL:
                    if self._log:
                        wait_s = self.MIN_REFRESH_INTERVAL - (now - last)
                        self._log.debug(
                            f"refresh_cache throttled ({wait_s:.0f}s remaining)",
                            module="addon_service",
                        )
                    return 0

        if existing is not None:
            if wait:
                existing.join(timeout=timeout)
            return 0

        with self._lock:
            self._refreshing = True

        def _refresh() -> int:
            started = time.time()
            results: dict = {}
            errors: dict = {}

            def _fetch(name: str, fn):
                try:
                    results[name] = fn()
                except Exception as e:  # noqa: BLE001
                    errors[name] = e
                    if self._log:
                        self._log.warning(
                            f"refresh source '{name}' failed: {e}", module="addon_service"
                        )

            # 目录为空（首次/缓存丢失）→ 全量翻页（60+ 页约 2 分钟）；
            # 已有目录 → 增量只爬前 3 页（新插件在页首，秒级完成）。
            with self._lock:
                have_catalog = len(self._addons) > 0
            if have_catalog:
                sources = {
                    "budeco": lambda: self.search_budeco(keyword="", incremental_pages=3),
                }
            else:
                sources = {
                    "budeco": lambda: self.search_budeco(keyword="", max_pages=None),
                }

            with ThreadPoolExecutor(max_workers=1, thread_name_prefix="addon-refresh") as pool:
                futures = {pool.submit(_fetch, n, f): n for n, f in sources.items()}
                try:
                    for fut in as_completed(futures, timeout=self.REFRESH_TIMEOUT):
                        fut.result()
                except FuturesTimeout:
                    if self._log:
                        pending = [futures[f] for f in futures if not f.done()]
                        self._log.warning(
                            f"refresh timed out after {self.REFRESH_TIMEOUT}s (pending: {pending})",
                            module="addon_service",
                        )

            added = 0
            with self._lock:
                for addons in results.values():
                    for a in addons:
                        if a.id not in self._addons:
                            self._addons[a.id] = a
                            added += 1

            # 全量目录落盘（下次启动秒开，不必重新爬 60+ 页）
            if results.get("budeco"):
                self._save_catalog()

            with self._lock:
                self._last_refresh_ts = time.time()
                self._refreshing = False
                self._refresh_thread = None

            if self._log:
                detail = ", ".join(f"+{len(v)} {k}" for k, v in results.items())
                err_txt = f", errors: {list(errors)}" if errors else ""
                self._log.info(
                    f"Addon cache refreshed in {time.time() - started:.1f}s: {detail} "
                    f"(+{added} new, total {len(self._addons)}){err_txt}",
                    module="addon_service",
                )
            return added

        t = threading.Thread(target=_refresh, daemon=True, name="addon-refresh")
        with self._lock:
            self._refresh_thread = t
        t.start()

        if wait:
            t.join(timeout=timeout)
        return 0

