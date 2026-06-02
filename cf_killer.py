# -*- coding: utf-8 -*-
"""
Cloudflare 5s 盾自动求解器 — 精简版（CloakBrowser 集成）。

移除内容：
  - dingo / Ray Operator / MapperRegister 分布式依赖
  - OSS 上传（oss2）
  - 代理系统（init_proxy / get_proxy_and_vendor / netnut / brightdata）

保留内容：
  - CF challenge 检测 + Turnstile 自动求解（CFSolver）
  - CloakBrowser 持久化浏览器封装
  - 多实例并行抓取 + 上下文自动回收

使用前：
  pip install cloakbrowser
  python -c "import cloakbrowser; cloakbrowser.ensure_binary()"
"""

import asyncio
import concurrent.futures
import re
import tempfile
import time
import random
from random import randint
from typing import Dict, List, Optional

from playwright.async_api import Page

# ============================================================
# CF 解盾常量
# ============================================================
CF_CHALLENGE_TITLES_EN = [
    'Just a moment...',
    'DDoS-Guard',
    'Attention Required! | Cloudflare',
]
CF_CHALLENGE_TITLES_CN = [
    '请稍候…',
    '稍候…',
    '正在检查',
]
CF_CHALLENGE_TITLES = CF_CHALLENGE_TITLES_EN + CF_CHALLENGE_TITLES_CN

CF_IFRAME_PATTERN = re.compile(
    r"^https?://challenges\.cloudflare\.com/cdn-cgi/challenge-platform/.*"
)

TURNSTILE_CONTAINER_SELECTORS = [
    '#cf_turnstile div', '#cf-turnstile div',
    '.turnstile>div>div', '.main-content p+div>div>div',
    '.cf-turnstile', '#challenge-stage',
]


# ============================================================
# CF 检测函数
# ============================================================
def _detect_cf_challenge(html: str) -> Optional[str]:
    """从 HTML 中检测 CF Turnstile challenge 类型。"""
    if not html:
        return None
    for ctype in ('non-interactive', 'managed', 'interactive'):
        if f"cType: '{ctype}'" in html or f'cType: "{ctype}"' in html:
            return ctype
    if 'challenges.cloudflare.com/turnstile/v' in html:
        return 'embedded'
    return None


def _cf_title_present(html: str) -> bool:
    """检查 HTML 是否仍显示 CF challenge 页面。"""
    for t in CF_CHALLENGE_TITLES:
        if f'<title>{t}</title>' in html:
            return True
    return False


def _is_cf_page(html: str) -> bool:
    """检查 HTML 是否是 CF 挑战页（综合检测）。"""
    if not html:
        return False
    head = html[:3000]
    if _cf_title_present(head):
        return True
    if 'challenges.cloudflare.com/turnstile' in head:
        return True
    if "cType:" in head and 'challenges.cloudflare.com' in head:
        return True
    for sel in ['cf-challenge-running', 'challenge-spinner',
                 'trk_jschal_js', 'cf-please-wait']:
        if sel in head:
            return True
    return False


async def _quick_cf_check(page: Page) -> bool:
    """快速检测当前页面是否为 CF 5s 盾页面（用 title，比 content() 快 ~10x）。"""
    try:
        title = await page.title()
        for t in CF_CHALLENGE_TITLES:
            if t.lower() == title.lower():
                return True
        # title 没命中，再用 content 片段做二次确认
        html = await page.content()
        return _cf_title_present(html) or 'challenges.cloudflare.com/turnstile' in html[:3000]
    except Exception:
        return False


# ============================================================
# CF 求解器
# ============================================================
class CFSolver:
    """
    Cloudflare 5s 盾自动求解器（v2 优化版）。

    优化点：
      - managed 类型点击后等待 spinner 出现→消失，再检测页面跳转
      - 更精准的 checkbox 定位（多路径 iframe 内选择器 + 坐标点击）
      - 延长超时时间，适配慢速网络
      - 每步详细日志，方便排查失败原因
    """

    def __init__(self, logger=None, max_retries: int = 5,
                 headless: bool = True, verbose: bool = True):
        self.logger = logger
        self.max_retries = max_retries
        self.headless = headless
        self.verbose = verbose

    def _log(self, msg: str, level: str = "info"):
        """verbose=False 时只输出 warning/error；verbose=True 输出全部。"""
        if not self.verbose and level in ("info", "debug"):
            return
        if self.logger:
            getattr(self.logger, level, print)(msg)
        else:
            print(f"[{level.upper()}] {msg}")

    async def solve(self, page: Page) -> bool:
        """
        检测并求解 CF challenge。返回 True 表示已解决（或无 CF）。
        应在 page.goto() 之后调用。
        """
        await asyncio.sleep(0.5)

        html = await self._get_content(page)
        challenge = _detect_cf_challenge(html)

        # cType 没找到但标题是 CF → 兜底按 managed 处理
        if not challenge and _is_cf_page(html):
            self._log("cType 未检测到但页面判定为 CF，兜底按 managed 求解")
            challenge = 'managed'

        if not challenge:
            return True  # 无 CF，直接返回

        self._log(f"检测到 CF Challenge: {challenge}")
        await self._solve_cb(page, challenge, retry=0)

        # 解完后等页面稳定
        try:
            await page.wait_for_load_state('networkidle', timeout=15000)
        except Exception:
            pass

        html = await self._get_content(page)
        return not _is_cf_page(html)

    async def _get_content(self, page: Page, retries: int = 10,
                           delay: float = 0.5) -> str:
        """带重试的 page.content()。"""
        for i in range(retries):
            try:
                content = await page.content()
                if content:
                    return content
            except Exception:
                if i == retries - 1:
                    raise
                await asyncio.sleep(delay)
        return ""

    # ---------- 等待 CF 消失的辅助方法 ----------

    async def _find_cf_frame(self, page: Page, deadline: float) -> bool:
        """轮询直到 CF 的 iframe 可见，返回是否找到。"""
        while time.time() < deadline:
            for f in page.frames:
                if 'challenges.cloudflare.com' in (f.url or ''):
                    try:
                        el = await f.frame_element()
                        if await el.is_visible():
                            return True
                    except Exception:
                        pass
            await asyncio.sleep(0.2)
        return False

    async def _wait_for_iframe_ready(self, page: Page, deadline: float):
        """等待 iframe 内部 checkbox 渲染就绪，避免过早点击导致无效。"""
        while time.time() < deadline:
            for f in page.frames:
                if 'challenges.cloudflare.com' not in (f.url or ''):
                    continue
                try:
                    # 尝试多种可能的选择器，任一可见即认为就绪
                    for sel in [
                        'input[type=checkbox]',
                        'div[role=checkbox]',
                        '.cb-lb',
                        'label',
                        '.mark',
                        '[class*="checkbox"]',
                        '[class*="spinner"]',
                    ]:
                        el = f.locator(sel).first
                        if await el.is_visible(timeout=500):
                            self._log(f"iframe 内容就绪 ({sel})", "debug")
                            return
                except Exception:
                    pass
            await asyncio.sleep(0.3)
        self._log("iframe 内容就绪等待超时，继续点击", "debug")

    def _is_cf_gone_by_title(self, title: str) -> bool:
        """通过 title 判断 CF 是否已消失。"""
        title_lower = title.lower()
        return not any(t.lower() == title_lower for t in CF_CHALLENGE_TITLES)

    async def _wait_for_cf_gone(self, page: Page, timeout: float,
                                 interval: float = 0.25) -> bool:
        """
        轮询等待 CF 消失。
        双重检测：title 变化 + 页面内容不再包含 CF 特征。
        返回 True 表示 CF 已消失。
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                title = await page.title()
                if self._is_cf_gone_by_title(title):
                    return True

                # title 未变但可能是网络卡了，用 content 片段确认
                html_snippet = await page.content()
                if not _is_cf_page(html_snippet):
                    return True
            except Exception:
                pass
            await asyncio.sleep(interval)
        return False

    # ---------- Turnstile 点击 ----------

    async def _click_turnstile(self, page: Page):
        """在 Turnstile iframe 内点击 checkbox（多路径递进）。"""
        cf_frames = [f for f in page.frames
                     if 'challenges.cloudflare.com' in (f.url or '')]

        for f in cf_frames:
            # --- 路径 A: iframe 内精确选择器点击（最可靠） ---
            for sel in [
                'input[type=checkbox]',
                'div[role=checkbox]',
                'label.cb-lb',
                '.cb-lb',
                'label',
                '.mark',
                'div.mark',
                '[id*="checkbox"]',
                '[class*="checkbox"]',
            ]:
                try:
                    el = f.locator(sel).first
                    if await el.is_visible(timeout=1000):
                        await el.click(delay=randint(100, 200))
                        self._log(f"iframe 内选择器点击成功: {sel}", "debug")
                        return
                except Exception:
                    continue

            # --- 路径 B: iframe 坐标点击（checkbox 通常在左上角 ~27,25 区域） ---
            try:
                frame_el = await f.frame_element()
                box = await frame_el.bounding_box()
                if box and box['width'] > 0 and box['height'] > 0:
                    # 多组坐标尝试（checkbox 大约在 iframe 内 25-35, 25-35 位置）
                    # 映射到页面坐标 = frame_el.x + iframe_inner_x
                    for offset_x, offset_y in [(27, 27), (30, 30), (35, 25), (25, 35)]:
                        cx = box['x'] + offset_x
                        cy = box['y'] + offset_y
                        await page.mouse.click(cx, cy, delay=randint(80, 150), button='left')
                        await asyncio.sleep(0.3)
                        # 检查是否触发了 spinner
                        try:
                            spinner = f.locator('[class*="spinner"], [id*="spinner"]').first
                            if await spinner.is_visible(timeout=500):
                                self._log(f"iframe 坐标点击触发 spinner @ ({cx:.0f},{cy:.0f})", "debug")
                                return
                        except Exception:
                            pass
                    # 如果坐标点击都没触发 spinner，最后用一个通用坐标
                    cx = box['x'] + randint(26, 30)
                    cy = box['y'] + randint(26, 30)
                    await page.mouse.click(cx, cy, delay=randint(100, 200), button='left')
                    self._log(f"iframe 坐标兜底点击 @ ({cx:.0f},{cy:.0f})", "debug")
                    return
            except Exception:
                pass

        # --- 路径 C: 主页面 TURNSTILE 容器选择器 ---
        for sel in TURNSTILE_CONTAINER_SELECTORS:
            try:
                el = page.locator(sel).last
                if await el.is_visible(timeout=1000):
                    box = await el.bounding_box()
                    if box and box['width'] > 0:
                        cx = box['x'] + randint(10, 30)
                        cy = box['y'] + randint(10, 30)
                        await page.mouse.click(cx, cy, delay=randint(100, 200), button='left')
                        self._log(f"主页面容器坐标点击 @ ({cx:.0f},{cy:.0f})", "debug")
                        return
            except Exception:
                continue

        # --- 路径 D: Tab+Space 兜底 ---
        try:
            await page.keyboard.press('Tab')
            await asyncio.sleep(0.15)
            await page.keyboard.press('Space')
            self._log("Tab+Space 兜底点击", "debug")
        except Exception:
            pass

    # ---------- 核心求解逻辑 ----------

    async def _solve_cb(self, page: Page, challenge_type: str,
                        retry: int = 0):
        """递归求解 CF Turnstile challenge（v2 优化版）。"""
        if retry >= self.max_retries:
            self._log(f"CF 求解达到最大重试 {self.max_retries}", "warning")
            return

        # 时间参数（无头/有头统一加长，适配慢速网络）
        max_polls = 200 if self.headless else 120
        poll_interval = 0.2 if self.headless else 0.2
        iframe_deadline = 12 if self.headless else 8
        post_click_timeout = 45 if self.headless else 30

        # ---- non-interactive: 纯等待 ----
        if challenge_type == 'non-interactive':
            self._log("non-interactive 模式：轮询等待 CF 自动消失...")
            ok = await self._wait_for_cf_gone(
                page, timeout=max_polls * poll_interval, interval=poll_interval,
            )
            if ok:
                return
            self._log(f"non-interactive 超时，进入重试 {retry + 1}/{self.max_retries}")
            return await self._solve_cb(page, challenge_type, retry + 1)

        # ---- managed / interactive / embedded: 等 iframe → 点击 → 等消失 ----
        self._log(f"等待 CF iframe 就绪 (deadline={iframe_deadline}s)...")
        if not await self._find_cf_frame(page, time.time() + iframe_deadline):
            self._log("iframe 未在时限内就绪，仍尝试点击", "warning")

        # 等 iframe 内部内容渲染完成（checkbox/spinner 需要 JS 初始化）
        await self._wait_for_iframe_ready(page, deadline=time.time() + 5)

        # 点击
        self._log("执行 Turnstile 点击...")
        await self._click_turnstile(page)

        # 点击后短暂等待，让 CF 的 JS 响应
        await asyncio.sleep(1.0)

        # 检查是否出现 spinner（说明点击被接受）
        spinner_seen = False
        for f in page.frames:
            if 'challenges.cloudflare.com' not in (f.url or ''):
                continue
            try:
                spinner = f.locator(
                    '[class*="spinner"], [id*="spinner"], '
                    '[class*="loading"], [class*="verifying"]'
                ).first
                if await spinner.is_visible(timeout=2000):
                    spinner_seen = True
                    self._log("检测到 spinner — 点击已被 CF 接受，等待验证完成...")
                    break
            except Exception:
                pass

        if not spinner_seen:
            self._log("未检测到 spinner，可能点击未生效或 challenge 类型不同")

        # 等待 CF 消失（给足时间）
        self._log(f"轮询等待 CF 消失 (最长 {post_click_timeout}s)...")
        ok = await self._wait_for_cf_gone(
            page, timeout=post_click_timeout, interval=poll_interval,
        )
        if ok:
            self._log("CF 已消失！")
            return

        # 检查页面是否发生了跳转（CF 验证成功后常见）
        try:
            current_url = page.url
            if not any(t.lower() in (await page.title()).lower() for t in CF_CHALLENGE_TITLES):
                self._log(f"title 已非 CF，页面可能已跳转 url={current_url[:80]}")
                return
        except Exception:
            pass

        # 递归重试
        self._log(f"CF 仍未消失，进入重试 {retry + 1}/{self.max_retries}")
        html = await self._get_content(page)
        if _cf_title_present(html):
            return await self._solve_cb(page, challenge_type, retry + 1)


# ============================================================
# CloakBrowser 页面抓取器（含 CF 自动解盾）
# ============================================================
class CFPageFetcher:
    """
    基于 CloakBrowser 的页面抓取器 + CF 自动解盾。

    - max_pages_per_context: 每 N 个页面自动回收浏览器上下文，默认 20
    - 用法:
        async with CFPageFetcher(headless=True) as fetcher:
            results = await fetcher.fetch_batch(urls)
    """

    def __init__(
        self,
        headless: bool = True,
        humanize: bool = False,
        solve_cf: bool = True,
        cf_max_retries: int = 5,
        timeout: int = 90000,
        profile_dir: Optional[str] = None,
        proxy: Optional[str] = None,
        verbose: bool = True,
        max_pages_per_context: int = 20,
        return_cookies: bool = False,
    ):
        self.headless = headless
        self.humanize = humanize
        self.solve_cf = solve_cf
        self.cf_max_retries = cf_max_retries
        self.timeout = timeout
        self.profile_dir = profile_dir or tempfile.mkdtemp(prefix="cb_cf_")
        self.proxy = proxy
        self.verbose = verbose
        self.max_pages_per_context = max_pages_per_context
        self.return_cookies = return_cookies
        self._context = None
        self._page_count = 0

    # ---------- 上下文生命周期 ----------

    async def _launch_context(self):
        """启动浏览器上下文。"""
        from cloakbrowser import launch_persistent_context_async
        self._context = await launch_persistent_context_async(
            self.profile_dir,
            headless=self.headless,
            proxy=self.proxy,
            viewport={"width": 1920, "height": 1080},
            locale="zh-CN",
            timezone="Asia/Shanghai",
            humanize=self.humanize,
        )
        self._page_count = 0
        if self.verbose:
            print(f"[上下文] 已创建")

    async def _close_context(self):
        """关闭浏览器上下文。"""
        if self._context:
            try:
                await self._context.close()
            except Exception:
                pass
        self._context = None

    async def _ensure_context(self):
        """确保上下文可用；超过 max_pages_per_context 时自动回收重建。"""
        if self._context is None:
            await self._launch_context()
        elif self._page_count >= self.max_pages_per_context:
            if self.verbose:
                print(f"[上下文] 已达 {self._page_count} 页，回收重建...")
            await self._close_context()
            await self._launch_context()

    async def __aenter__(self):
        await self._launch_context()
        return self

    async def __aexit__(self, *args):
        await self._close_context()

    # ---------- 抓取方法 ----------

    async def _get_cookies(self, url: str = None) -> dict:
        """获取当前浏览器上下文的 cookies，返回两种格式。"""
        raw = await self._context.cookies(url)
        return {
            "dict": {c["name"]: c["value"] for c in raw},   # 方便 requests.Session 使用
            "raw": raw,                                       # 完整信息 (domain/path/expires...)
            "header": "; ".join(f"{c['name']}={c['value']}" for c in raw),  # Cookie 请求头
        }

    async def download_file(self, url: str, output_path: str,
                              warmup_url: str = None) -> bool:
        """过 CF 后下载二进制文件（PDF/图片等），保存到本地。

        通过页内 fetch() 复用浏览器的 cookie 和 TLS 指纹，绕过 CF。

        Args:
            url:         文件下载 URL
            output_path: 保存路径
            warmup_url:  预热 URL（默认取 url 的根路径）
        """
        import base64
        from urllib.parse import urlparse

        if warmup_url is None:
            parsed = urlparse(url)
            warmup_url = f"{parsed.scheme}://{parsed.netloc}/"

        # 预热过 CF
        if self.verbose:
            print(f"[下载] 预热: {warmup_url}")
        r = await self.fetch_page(warmup_url, wait_until="load")
        if not r["success"]:
            print(f"[下载] 预热失败")
            return False

        # 用同域页面绑定 cookie，再通过页内 fetch 拿文件
        page = await self._context.new_page()
        await page.goto(warmup_url, wait_until="load", timeout=30000)

        pdf_base64 = await page.evaluate(f"""
            async () => {{
                const resp = await fetch('{url}');
                if (!resp.ok) return null;
                const buf = await resp.arrayBuffer();
                const bytes = new Uint8Array(buf);
                let binary = '';
                for (let i = 0; i < bytes.length; i++)
                    binary += String.fromCharCode(bytes[i]);
                return btoa(binary);
            }}
        """)

        await page.close()

        if not pdf_base64:
            print(f"[下载] fetch 失败")
            return False

        content = base64.b64decode(pdf_base64)
        with open(output_path, "wb") as f:
            f.write(content)
        if self.verbose:
            print(f"[下载] 已保存: {output_path} ({len(content)/1024:.0f}KB)")
        return True

    async def _detect_cf_with_retry(self, page: Page, max_wait: float = 5.0) -> bool:
        """带重试的 CF 检测，适配 JS 延迟写入标题的站点（如 ScienceDirect）。"""
        deadline = time.time() + max_wait
        interval = 0.3
        while time.time() < deadline:
            if await _quick_cf_check(page):
                return True
            # 未检测到：等 JS 执行，再试
            try:
                await page.wait_for_load_state('load', timeout=2000)
            except Exception:
                pass
            await asyncio.sleep(interval)
            interval = min(interval * 1.3, 1.5)  # 逐渐拉长间隔
        return False

    async def fetch_page(
        self,
        url: str,
        wait_until: str = 'domcontentloaded',
        wait_selector: Optional[str] = None,
        wait_network_idle: bool = False,
        wait_timeout: int = 15000,
        scroll_times: int = 0,
        final_delay: float = 2.0,
    ) -> Dict:
        """
        抓取单个页面，自动处理 CF 解盾。

        返回: {"url", "title", "html", "success"}
        """
        await self._ensure_context()
        page = await self._context.new_page()
        page.set_default_navigation_timeout(self.timeout)
        page.set_default_timeout(self.timeout)

        try:
            try:
                await page.goto(url, wait_until=wait_until)
            except Exception:
                # goto 超时（如 ScienceDirect CF 页卡 domcontentloaded），
                # 不立即失败，检查页面是否已是 CF 挑战页
                if self.verbose:
                    print(f"goto 超时 url={url}，检查页面状态...")
                try:
                    await page.wait_for_load_state('load', timeout=5000)
                except Exception:
                    pass
                await asyncio.sleep(1.0)

            # ---- CF 检测（带重试，适配 ScienceDirect 等 JS 延迟写入标题的站点）----
            if self.solve_cf:
                cf_detected = await self._detect_cf_with_retry(page)

                if cf_detected:
                    if self.verbose:
                        print(f"检测到 CF 盾 url={url}")
                    solver = CFSolver(
                        max_retries=self.cf_max_retries,
                        headless=self.headless,
                        verbose=self.verbose,
                    )
                    cf_ok = await solver.solve(page)
                    if self.verbose and not cf_ok:
                        print(f"CF 未过 url={url}")
                elif self.verbose:
                    print(f"非 CF url={url}")

            for _ in range(scroll_times):
                try:
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await asyncio.sleep(0.5)
                except Exception:
                    pass

            if wait_selector:
                try:
                    await page.wait_for_selector(wait_selector, timeout=wait_timeout, state='attached')
                except Exception:
                    pass

            if wait_network_idle:
                try:
                    await page.wait_for_load_state('networkidle', timeout=wait_timeout)
                except Exception:
                    pass

            await asyncio.sleep(final_delay)

            title = await page.title()
            html = await page.content()
            result = {"url": url, "title": title, "html": html, "success": True}

            if self.return_cookies:
                result["cookies"] = await self._get_cookies(url)

            self._page_count += 1
            return result

        except Exception as e:
            try:
                title = await page.title()
                html = await page.content()
            except Exception:
                title, html = None, None
            result = {"url": url, "title": title, "html": html, "success": False}

            if self.return_cookies:
                try:
                    result["cookies"] = await self._get_cookies(url)
                except Exception:
                    result["cookies"] = {}

            print(f"抓取失败 url={url}: {e}")
            self._page_count += 1
            return result
        finally:
            await page.close()

    async def fetch_batch(
        self,
        urls: List[str],
        concurrency: int = 3,
        **kwargs,
    ) -> List[Dict]:
        """批量抓取：先串行预热第 1 个 URL 过 CF，之后并发拉取剩余。"""
        if not urls:
            return []

        results = [None] * len(urls)

        # ---- 阶段 1: 串行预热 ----
        if self.verbose:
            print(f"[预热] {urls[0][:80]}")
        results[0] = await self.fetch_page(urls[0], **kwargs)

        if len(urls) == 1:
            return results

        # ---- 阶段 2: 并发抓取 ----
        sem = asyncio.Semaphore(concurrency)

        async def _fetch_one(i: int, url: str):
            async with sem:
                results[i] = await self.fetch_page(url, **kwargs)

        tasks = [_fetch_one(i, url) for i, url in enumerate(urls[1:], start=1)]
        if self.verbose:
            print(f"[并发] {len(tasks)} 个，并发 {concurrency}")
        await asyncio.gather(*tasks)

        return results


# ============================================================
# 同步入口
# ============================================================
def _run_in_thread(urls_chunk: List[str], index: int, concurrency: int = 3, **fetcher_kwargs) -> List[Dict]:
    """在线程中运行一个独立的 async event loop + 浏览器实例。"""
    async def _run():
        async with CFPageFetcher(**fetcher_kwargs) as fetcher:
            return await fetcher.fetch_batch(urls_chunk, concurrency=concurrency)
    return asyncio.run(_run())


def fetch_all(
    urls: List[str],
    instances: int = 1,
    concurrency: int = 3,
    max_pages_per_context: int = 20,
    verbose: bool = True,
    return_cookies: bool = False,
    **fetcher_kwargs,
) -> List[Dict]:
    """
    多实例并行抓取所有 URL。

    Args:
        urls:                  URL 列表
        instances:             并行浏览器实例数（默认 1）
        concurrency:           每个实例的并发 tab 数（默认 3）
        max_pages_per_context: 每个实例处理 N 页后自动回收
        verbose:               是否输出日志
        return_cookies:        是否在结果中返回 cookies（默认 False）
        **fetcher_kwargs:      传给 CFPageFetcher 的参数
            headless, solve_cf, cf_max_retries, timeout, humanize, ...
            proxy:  支持三种形式:
                - 字符串:  所有实例共享（不推荐，会触发服务端限流；会打印警告）
                - 列表:    每个实例分配一个，不足则循环
                - callable: 每实例调用一次，返回代理字符串

    Returns:
        结果列表（保持原 URL 顺序）

    用法:
        # 每个实例不同代理
        results = fetch_all(urls, instances=3,
                            proxy=["http://p1", "http://p2", "http://p3"])
        # 或 callable
        results = fetch_all(urls, instances=3, proxy=get_next_proxy)
    """
    # ---- 代理分配 ----
    raw_proxy = fetcher_kwargs.pop("proxy", None)

    def _resolve_proxy(idx: int) -> str:
        """为第 idx 个实例解析代理。"""
        if raw_proxy is None:
            return None
        if isinstance(raw_proxy, list):
            return raw_proxy[idx % len(raw_proxy)]
        if callable(raw_proxy):
            return raw_proxy()
        # 单个字符串：共享代理，警告
        if idx == 0 and instances > 1 and verbose:
            print("[警告] 多实例共享同一代理，可能触发服务端限流！"
                  " 建议传入 proxy=[] 列表，每个实例不同代理")
        return raw_proxy

    if instances <= 1:
        return _run_in_thread(urls, 0, concurrency=concurrency,
                              max_pages_per_context=max_pages_per_context,
                              verbose=verbose,
                              return_cookies=return_cookies,
                              proxy=_resolve_proxy(0),
                              **fetcher_kwargs)

    # 将 URL 均匀分给各实例
    chunks = [[] for _ in range(instances)]
    for i, url in enumerate(urls):
        chunks[i % instances].append(url)

    if verbose:
        proxy_info = ""
        if isinstance(raw_proxy, list):
            proxy_info = f"，代理数={len(raw_proxy)}"
        elif callable(raw_proxy):
            proxy_info = "，代理=callable"
        elif raw_proxy:
            proxy_info = "，代理=共享(⚠️)"
        print(f"[多实例] {instances} 个实例并行，共 {len(urls)} 个 URL{proxy_info}")

    all_results = [None] * len(urls)
    with concurrent.futures.ThreadPoolExecutor(max_workers=instances) as pool:
        futures = {}
        for idx, chunk in enumerate(chunks):
            if not chunk:
                continue
            fut = pool.submit(
                _run_in_thread, chunk, idx, concurrency,
                max_pages_per_context=max_pages_per_context,
                verbose=verbose,
                return_cookies=return_cookies,
                proxy=_resolve_proxy(idx),
                **fetcher_kwargs,
            )
            futures[fut] = idx

        for fut in concurrent.futures.as_completed(futures):
            idx = futures[fut]
            chunk_results = fut.result()
            for j, result in enumerate(chunk_results):
                original_index = idx + j * instances
                all_results[original_index] = result

    return all_results


def fetch_urls(
    urls: List[str],
    instances: int = 1,
    concurrency: int = 3,
    max_pages_per_context: int = 20,
    verbose: bool = True,
    return_cookies: bool = False,
    **fetcher_kwargs,
) -> List[Dict]:
    """同步批量抓取（fetch_all 的别名）。"""
    return fetch_all(urls, instances=instances, concurrency=concurrency,
                     max_pages_per_context=max_pages_per_context,
                     verbose=verbose, return_cookies=return_cookies,
                     **fetcher_kwargs)


def fetch_url(
    url: str,
    verbose: bool = True,
    **fetcher_kwargs,
) -> Dict:
    """同步抓取单个 URL。"""
    return fetch_all([url], instances=1, verbose=verbose, **fetcher_kwargs)[0]


# ============================================================
# 本地调试
# ============================================================
if __name__ == '__main__':
    # 单个 URL 测试
    result = fetch_url(
        "https://gut.bmj.com/content/75/6",
        headless=False,       # 本地调试用有头模式
        humanize=False,
        solve_cf=True,       # httpbin 无 CF，关掉
    )
    print(f"title: {result['title']}")
    print(f"html length: {len(result.get('html', '') or '')}")
