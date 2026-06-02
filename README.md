# to_be_learned
待学习的知识内容

- [ ] skope-rules
    - github: https://github.com/scikit-learn-contrib/skope-rules
    - description: SkopeRules 能够高精度地找到逻辑规则并将其融合
    - 完成日期: ****

- [ ] hello-agents
    - github: https://github.com/datawhalechina/hello-agents
    - description: Datawhale 社区的系统性智能体学习教程
    - 完成日期: ****

```python
# -*- coding: utf-8 -*-
"""
CF 自动解盾 + 页面抓取 — 新人测试脚本。
直接运行: python test_demo.py
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cf_solver import fetch_all

# ============================================================
# 配置（改这里）
# ============================================================
URLS_FILE              = "gut_links.json"   # 链接文件
TEST_COUNT             = 0                  # 测试前几个（0=全部）
HEADLESS               = False              # True=无头 False=有头
PROXY                  = None               # 代理，如 "http://user:pass@host:port"
# PROXY                = "http://brd-customer-hl_0c4f2698-zone-shannon_static_dedicated_pub:5lxp95wd8p78@brd.superproxy.io:33335"
CONCURRENCY            = 3                  # 每个实例并发 tab 数
INSTANCES              = 2                  # 浏览器实例数（多实例并行）
MAX_PAGES_PER_CONTEXT  = 10                 # 每 N 页自动回收浏览器
RETURN_COOKIES         = False              # 是否返回 cookies
# ============================================================


def load_urls(path: str) -> list:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


if __name__ == "__main__":
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), URLS_FILE)
    all_urls = load_urls(path)
    urls = all_urls[:TEST_COUNT] if TEST_COUNT > 0 else all_urls
    print(f"链接: {len(all_urls)} 条  测试: {len(urls)} 个")

    results = fetch_all(
        urls,
        instances=INSTANCES,
        concurrency=CONCURRENCY,
        max_pages_per_context=MAX_PAGES_PER_CONTEXT,
        headless=HEADLESS,
        solve_cf=True,
        proxy=PROXY,
        return_cookies=RETURN_COOKIES,
        verbose=False,
    )

    ok = sum(1 for r in results if r["success"])
    print(f"\n{'='*50}")
    for r in results:
        status = "✓" if r["success"] else "✗"
        print(f"  {status}  {(r['title'] or 'FAILED')[:60]}")
    print(f"{'='*50}")
    print(f"结果: {ok}/{len(results)} 成功")

```

```python
# -*- coding: utf-8 -*-
"""
CF 过盾 + PDF 下载测试。
流程: 浏览器过 CF → 拦截 PDF 响应 → 保存本地
直接运行: python test_pdf_download.py
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cf_solver import CFPageFetcher

# ============================================================
# 配置
# ============================================================
PDF_URL       = "https://www.myavls.org/assets/pdf/SuperficialVenousDiseaseGuidelinesPMS313-02.03.16.pdf"
WARMUP_URL    = "https://www.myavls.org/"       # 同域名任意页面，用于过 CF
OUTPUT_PATH   = "downloaded.pdf"                 # 保存路径
HEADLESS      = False
# ============================================================


async def download_pdf(pdf_url: str, warmup_url: str, output: str) -> bool:
    """
    浏览器过 CF → 拦截 PDF 原始字节 → 保存本地。

    返回 True 表示下载成功。
    """
    async with CFPageFetcher(headless=HEADLESS, solve_cf=True) as fetcher:
        # ---- Step 1: 预热，过 CF ----
        print(f"[1/3] 预热过 CF: {warmup_url}")
        r = await fetcher.fetch_page(warmup_url, wait_until="load")
        if not r["success"]:
            print("  预热失败，退出")
            return False
        print("  CF 已过 ✓")

        # ---- Step 2: 页内 fetch 拿 PDF 原始字节 ----
        print(f"[2/3] 请求 PDF: {pdf_url}")
        page = await fetcher._context.new_page()

        # 先用空页面加载任意同域 URL，确保 cookie 已绑定
        await page.goto(warmup_url, wait_until="load", timeout=30000)

        # 通过页内 fetch 获取 PDF（复用浏览器 cookie 和 TLS 指纹）
        pdf_base64 = await page.evaluate(f"""
            async () => {{
                const resp = await fetch('{pdf_url}');
                if (!resp.ok) return null;
                const buf = await resp.arrayBuffer();
                const bytes = new Uint8Array(buf);
                // 转 base64 传回 Python
                let binary = '';
                for (let i = 0; i < bytes.length; i++)
                    binary += String.fromCharCode(bytes[i]);
                return btoa(binary);
            }}
        """)

        if not pdf_base64:
            print("  fetch 失败，可能仍被 CF 拦截")
            await page.close()
            return False

        import base64
        pdf_bytes = base64.b64decode(pdf_base64)

        # ---- Step 3: 保存 ----
        with open(output, "wb") as f:
            f.write(pdf_bytes)
        print(f"[3/3] PDF 已保存: {output} ({len(pdf_bytes)/1024:.0f}KB) ✓")
        await page.close()
        return True


if __name__ == "__main__":
    success = asyncio.run(download_pdf(PDF_URL, WARMUP_URL, OUTPUT_PATH))
    print(f"\n结果: {'成功' if success else '失败'}")

```

```json
[
  "https://gut.bmj.com/content/75/6/1085",
  "https://gut.bmj.com/content/75/6/1087",
  "https://gut.bmj.com/content/75/6/1090",
  "https://gut.bmj.com/content/75/6/1092",
  "https://gut.bmj.com/content/75/6/1094",
  "https://gut.bmj.com/content/75/6/1097",
  "https://gut.bmj.com/content/75/6/1110",
  "https://gut.bmj.com/content/75/6/1123",
  "https://gut.bmj.com/content/75/6/1136",
  "https://gut.bmj.com/content/75/6/1147",
  "https://gut.bmj.com/content/75/6/1160",
  "https://gut.bmj.com/content/75/6/1169",
  "https://gut.bmj.com/content/75/6/1186",
  "https://gut.bmj.com/content/75/6/1201",
  "https://gut.bmj.com/content/75/6/1211",
  "https://gut.bmj.com/content/75/6/1226",
  "https://gut.bmj.com/content/75/6/1237",
  "https://gut.bmj.com/content/75/6/1248",
  "https://gut.bmj.com/content/75/6/1264",
  "https://gut.bmj.com/content/75/6/1266.1",
  "https://gut.bmj.com/content/75/6/1266.2",
  "https://gut.bmj.com/content/75/6/1267",
  "https://gut.bmj.com/content/75/6/1109",
  "http://americanfootball.fandom.com/1993_Kentucky_vs._Mississippi",
  "http://americanfootball.fandom.com/Isaiah_Foskey",
  "http://americanfootball.fandom.com/wiki/2014_Susquehanna_Crusaders",
  "http://americanfootball.fandom.com/wiki/2015_Lake_Forest_Foresters",
  "http://americanfootball.fandom.com/wiki/2023_Colorado_State_Rams",
  "http://americanfootballdatabase.fandom.com/Paul_Hackett_(American_football)",
  "http://americanfootballdatabase.fandom.com/wiki/100th_Grey_Cup",
  "https://www.myavls.org/assets/pdf/SuperficialVenousDiseaseGuidelinesPMS313-02.03.16.pdf",
  "https://www.sciencedirect.com/science/article/pii/S0039606025002491"
]
```
