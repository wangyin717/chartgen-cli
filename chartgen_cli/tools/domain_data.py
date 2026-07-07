"""
domain_data — 垂直领域数据查询工具

并发调用多个外部数据源，返回结构化 JSON 数据供 LLM 使用 ci 生成 HTML。
API key 从环境变量读取（见 ~/.chartgen/config），缺失时对应 domain 报错降级，不影响其他 domain。
"""
import asyncio
import hashlib
import json
import logging
import os
import re
import time

logger = logging.getLogger(__name__)
from typing import Any, Dict, List, Tuple

import aiohttp

# ── workspace（由 main.py 启动时注入）─────────────────────────────────────────
_workspace_dir: str = ""

def set_workspace(path: str) -> None:
    global _workspace_dir
    _workspace_dir = path

_IMG_SUBDIR = "domain_images"


def _sanitize_filename(name: str, url: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:40]
    suffix = hashlib.md5(url.encode()).hexdigest()[:6]
    return f"{slug}_{suffix}.jpg" if slug else f"{suffix}.jpg"


async def _download_images(image_metas: List[Dict]) -> List[Dict]:
    """下载图片到 {workspace}/domain_images/，返回 [{"local_path": ..., "url": ..., "name": ...}]。"""
    if not _workspace_dir or not image_metas:
        return []
    img_dir = os.path.join(_workspace_dir, _IMG_SUBDIR)
    os.makedirs(img_dir, exist_ok=True)

    async def _fetch(meta: Dict) -> Dict | None:
        url = meta.get("url", "")
        if not url:
            return None
        filename = _sanitize_filename(meta.get("name", ""), url)
        local_path = os.path.join(img_dir, filename)
        if os.path.exists(local_path):
            return {"local_path": local_path, "url": url, "name": meta.get("name", "")}
        try:
            headers = {"User-Agent": "Mozilla/5.0"}
            async with aiohttp.ClientSession() as s:
                async with s.get(url, headers=headers,
                                 timeout=aiohttp.ClientTimeout(total=15)) as r:
                    if r.status == 200:
                        data = await r.read()
                        with open(local_path, "wb") as f:
                            f.write(data)
                        return {"local_path": local_path, "url": url, "name": meta.get("name", "")}
        except Exception as e:
            logger.warning("[Domain Data] image download failed url=%s: %s", url, e)
        return None

    results = await asyncio.gather(*[_fetch(m) for m in image_metas])
    downloaded = [r for r in results if r]
    logger.info("[Domain Data] images: attempted=%d downloaded=%d", len(image_metas), len(downloaded))
    return downloaded

# ── API 配置（从环境变量读取，见 config.py 的 ~/.chartgen/config 模板）──────────
# 独立分发版不依赖 monorepo 的 Nacos/system_settings，缺 key 时对应 domain 直接报错降级，不影响其他 domain。
_SERPAPI_HOST  = "https://serpapi.com/search"
_SERPAPI_KEY   = os.environ.get("GOOGLE_TRENDS_API", "")
_GUGUDATA_HOST = "https://api.gugudata.com/stock/hk/annualreport"
_GUGUDATA_KEY  = os.environ.get("GUGUDATA_API", "")
_TUSHARE_HOST  = "http://api.tushare.pro"
_TUSHARE_KEY   = os.environ.get("TUSHARE_API", "")

# ── domain 元数据 ─────────────────────────────────────────────────────────────
DOMAIN_META: Dict[str, str] = {
    "amazon":            "Amazon商品搜索：返回商品标题、价格、评分、ASIN、链接等",
    "google_maps":       "Google Maps地点搜索：返回地点名称、评分、地址、类型、营业时间等",
    "tripadvisor":       "TripAdvisor景点/酒店/餐厅搜索：返回评分、评论、描述、缩略图等旅游信息",
    "google_flights":    "Google航班搜索：返回航班信息、价格、时长等（需指定出发地、目的地、日期）",
    "apple_app_store":   "Apple App Store搜索：返回应用名称、评分、开发者、价格、分类等",
    "google_play_store": "Google Play Store搜索：返回应用名称、评分、开发者、下载量等",
    "gugudata":          "Gugudata港股财务数据：返回港股资产负债表、利润表、现金流量表等",
    "tushare":           "Tushare港股行情数据：返回港股日K线数据（开盘/收盘/最高/最低价、成交量等）",
}


# ── Amazon ────────────────────────────────────────────────────────────────────

def _fmt_product(p: dict, idx: int) -> str:
    c = f"\n{idx}. {p.get('title','Unknown')}\n"
    price = p.get('price', {})
    if isinstance(price, dict):
        c += f"Price: {price.get('symbol','$')}{price.get('value','N/A')}\n"
    elif price:
        c += f"Price: {price}\n"
    if p.get('rating'):
        c += f"Rating: {p['rating']} ({p.get('ratings_total',0)} reviews)\n"
    if p.get('is_prime'):
        c += "Prime: Yes\n"
    if p.get('asin'):
        c += f"ASIN: {p['asin']}\n"
    if p.get('link'):
        c += f"Link: {p['link']}\n"
    return c


async def _amazon(keywords: str) -> Dict:
    params = {"engine": "amazon", "k": keywords, "page": 1,
              "output": "json", "api_key": _SERPAPI_KEY}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(_SERPAPI_HOST, params=params,
                             timeout=aiohttp.ClientTimeout(total=30)) as r:
                data = await r.json()
        products = data.get("organic_results", [])[:20]
        content = f"Amazon Search: '{keywords}'\nFound {len(products)} products:\n"
        for i, p in enumerate(products, 1):
            content += _fmt_product(p, i)
        image_metas = [{"url": p["thumbnail"], "name": p.get("title", "")}
                       for p in products if p.get("thumbnail")]
        return {"content": content, "products": products, "_image_metas": image_metas}
    except Exception as e:
        return {"error": str(e)}


# ── Google Maps ───────────────────────────────────────────────────────────────

def _fmt_place(p: dict, idx: int) -> str:
    c = f"\n{idx}. {p.get('title','Unknown')}\n"
    if p.get('rating'):
        c += f"Rating: {p['rating']} ({p.get('reviews',0)} reviews)\n"
    if p.get('type'):
        c += f"Type: {p['type']}\n"
    if p.get('address'):
        c += f"Address: {p['address']}\n"
    if p.get('price'):
        c += f"Price: {p['price']}\n"
    desc = (p.get('description') or '')[:100]
    if desc:
        c += f"Description: {desc}\n"
    return c


async def _google_maps(keywords: str) -> Dict:
    params = {"engine": "google_maps", "q": keywords, "type": "search",
              "output": "json", "api_key": _SERPAPI_KEY}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(_SERPAPI_HOST, params=params,
                             timeout=aiohttp.ClientTimeout(total=30)) as r:
                data = await r.json()
        places = data.get("local_results") or data.get("local_results", [])
        if isinstance(places, dict):
            places = [places]
        content = f"Google Maps: '{keywords}'\nFound {len(places)} places:\n"
        for i, p in enumerate(places, 1):
            content += _fmt_place(p, i)
        image_metas = [{"url": p["thumbnail"], "name": p.get("title", "")}
                       for p in places if p.get("thumbnail")]
        return {"content": content, "places": places, "_image_metas": image_metas}
    except Exception as e:
        return {"error": str(e)}


# ── TripAdvisor ───────────────────────────────────────────────────────────────

async def _tripadvisor(keywords: str) -> Dict:
    params = {"engine": "tripadvisor", "q": keywords,
              "output": "json", "api_key": _SERPAPI_KEY}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(_SERPAPI_HOST, params=params,
                             timeout=aiohttp.ClientTimeout(total=30)) as r:
                if r.status != 200:
                    return {"error": f"TripAdvisor API error: {r.status}"}
                data = await r.json()
        places = data.get("places", [])
        forums = data.get("forums", [])
        content = f"TripAdvisor: '{keywords}'\n"
        if places:
            content += f"\nFound {len(places)} places:\n"
            for i, p in enumerate(places, 1):
                content += f"\n{i}. {p.get('title','')}\n"
                if p.get("place_type"):
                    content += f"Type: {p['place_type']}\n"
                if p.get("rating"):
                    content += f"Rating: {p['rating']} ({p.get('reviews', '')} reviews)\n"
                if p.get("location"):
                    content += f"Location: {p['location']}\n"
                desc = (p.get("description") or "")[:200]
                if desc:
                    content += f"Description: {desc}\n"
        if forums:
            content += f"\nFound {len(forums)} forum topics:\n"
            for i, fo in enumerate(forums, 1):
                content += f"\n{i}. {fo.get('title','')}\n"
                if fo.get("location"):
                    content += f"Location: {fo['location']}\n"
                comment = fo.get("highlighted_comment", {})
                if comment.get("text"):
                    content += f"Summary: {comment['text'][:150]}\n"
        image_metas = [{"url": p["thumbnail"], "name": p.get("title", "")}
                       for p in places if p.get("thumbnail")]
        return {"content": content, "places": places, "forums": forums, "_image_metas": image_metas}
    except Exception as e:
        return {"error": str(e)}


# ── Google Flights ────────────────────────────────────────────────────────────

def _fmt_flight(flight: dict, idx: int) -> str:
    flights_list = flight.get("flights", [])
    if not flights_list:
        return ""
    first, last = flights_list[0], flights_list[-1]
    dept = first.get("departure_airport", {})
    arr  = last.get("arrival_airport", {})
    total = flight.get("total_duration", 0)
    h, m = divmod(total, 60)
    layovers = flight.get("layovers", [])
    stops = ", ".join(f"{l.get('id')}({l.get('duration')}min)" for l in layovers) if layovers else "Direct"
    return (f"{idx}. {first.get('airline','')} {first.get('flight_number','')} "
            f"- ${flight.get('price','N/A')}\n"
            f"   {dept.get('id','')} {dept.get('time','')[:16]} → "
            f"{arr.get('id','')} {arr.get('time','')[:16]} | {h}h{m}m | {stops}\n")


async def _google_flights(departure_id: str, arrival_id: str, outbound_date: str) -> Dict:
    params = {"engine": "google_flights", "departure_id": departure_id.upper(),
              "arrival_id": arrival_id.upper(), "outbound_date": outbound_date,
              "output": "json", "deep_search": "True", "api_key": _SERPAPI_KEY}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(_SERPAPI_HOST, params=params,
                             timeout=aiohttp.ClientTimeout(total=30)) as r:
                data = await r.json()
        best = data.get("best_flights", [])
        other = data.get("other_flights", [])
        all_flights = best + other
        content = (f"Flights {departure_id}→{arrival_id} on {outbound_date}\n"
                   f"Found {len(all_flights)} flights:\n")
        for i, f in enumerate(all_flights[:10], 1):
            content += _fmt_flight(f, i)
        return {"content": content, "flights": all_flights}
    except Exception as e:
        return {"error": str(e)}


# ── Apple App Store ───────────────────────────────────────────────────────────

async def _apple_app_store(keywords: str) -> Dict:
    params = {"engine": "apple_app_store", "term": keywords,
              "num": 20, "output": "json", "api_key": _SERPAPI_KEY}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(_SERPAPI_HOST, params=params,
                             timeout=aiohttp.ClientTimeout(total=30)) as r:
                data = await r.json()
        apps = data.get("organic_results", [])
        content = f"Apple App Store: '{keywords}'\nFound {len(apps)} apps:\n"
        for i, app in enumerate(apps[:20], 1):
            content += f"\n{i}. {app.get('title','')}\n"
            ratings = app.get("rating", [])
            if isinstance(ratings, list) and ratings:
                r0 = next((r for r in ratings if r.get("type") == "All Times"), ratings[0])
                content += f"Rating: {r0.get('rating','N/A')}\n"
            dev = app.get("developer", {})
            if isinstance(dev, dict):
                content += f"Developer: {dev.get('name','')}\n"
        image_metas = []
        for app in apps[:20]:
            logos = app.get("logos", [])
            url = logos[0].get("link", "") if logos else ""
            if url:
                image_metas.append({"url": url, "name": app.get("title", "")})
        return {"content": content, "apps": apps, "_image_metas": image_metas}
    except Exception as e:
        return {"error": str(e)}


# ── Google Play Store ─────────────────────────────────────────────────────────

class _PlayPaginationState:
    def __init__(self):
        self.seen_ids: set = set()
        self.low_new_cnt = self.stable_cnt = self.high_dup_cnt = self.page_idx = 0
        self.last_dist: Dict = {}

def _play_should_stop(state: _PlayPaginationState, apps: List[Dict],
                      max_pages: int = 1) -> bool:
    if not apps:
        return True
    new = [a for a in apps if a.get("product_id") not in state.seen_ids]
    new_ratio = len(new) / len(apps)
    if new_ratio < 0.1:
        state.low_new_cnt += 1
    else:
        state.low_new_cnt = 0
    if state.high_dup_cnt >= 1 or state.low_new_cnt >= 2 or state.page_idx >= max_pages:
        return True
    state.seen_ids.update(a.get("product_id") for a in apps if "product_id" in a)
    state.page_idx += 1
    return False


async def _google_play_store(keywords: str) -> Dict:
    params = {"engine": "google_play", "q": keywords,
              "output": "json", "api_key": _SERPAPI_KEY}
    state = _PlayPaginationState()
    all_apps: List[Dict] = []
    next_token_key = next_token_val = None
    try:
        async with aiohttp.ClientSession() as sess:
            while True:
                if next_token_key and next_token_val:
                    params[next_token_key] = next_token_val
                async with sess.get(_SERPAPI_HOST, params=params,
                                    timeout=aiohttp.ClientTimeout(total=30)) as r:
                    data = await r.json()
                organic = data.get("organic_results", [])
                apps = []
                for section in organic:
                    apps.extend(section.get("items", []))
                if not apps:
                    break
                all_apps.extend(apps)
                if _play_should_stop(state, apps):
                    break
                pagination = data.get("serpapi_pagination", {})
                next_token_key = "next_page_token"
                next_token_val = pagination.get("next_page_token")
                if not next_token_val:
                    break

        seen, unique = set(), []
        for app in all_apps:
            pid = app.get("product_id", "")
            if pid and pid not in seen:
                unique.append(app)
                seen.add(pid)

        content = f"Google Play Store: '{keywords}'\nFound {len(unique)} apps:\n"
        for i, app in enumerate(unique[:20], 1):
            content += (f"\n{i}. {app.get('title','')}\n"
                        f"Rating: {app.get('rating','N/A')} | "
                        f"Downloads: {app.get('downloads','N/A')}\n")
        image_metas = [{"url": app["thumbnail"], "name": app.get("title", "")}
                       for app in unique[:20] if app.get("thumbnail")]
        return {"content": content, "apps": unique, "_image_metas": image_metas}
    except Exception as e:
        return {"error": str(e)}


# ── Gugudata ──────────────────────────────────────────────────────────────────

async def _gugudata(symbol: str, report_type: str = "", indicator: str = "年度") -> Dict:
    params = {"appkey": _GUGUDATA_KEY, "symbol": symbol,
              "type": report_type, "indicator": indicator}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(_GUGUDATA_HOST, params=params,
                             timeout=aiohttp.ClientTimeout(total=20)) as r:
                data = await r.json()
        return {"content": json.dumps(data, ensure_ascii=False)[:3000], "data": data}
    except Exception as e:
        return {"error": str(e)}


# ── Tushare ───────────────────────────────────────────────────────────────────

_TUSHARE_FIELDS = ["ts_code","trade_date","open","high","low","close",
                   "pre_close","change","pct_chg","vol","amount"]
_TUSHARE_FIELD_EN = {
    "ts_code":"Stock Code","trade_date":"Trade Date","open":"Open",
    "high":"High","low":"Low","close":"Close","pre_close":"Prev Close",
    "change":"Change","pct_chg":"Change(%)","vol":"Volume","amount":"Turnover"
}

async def _tushare(ts_code: str, start_date: str = "",
                   end_date: str = "", limit: int = 90) -> Dict:
    payload = {
        "api_name": "hk_daily",
        "token": _TUSHARE_KEY,
        "params": {"ts_code": ts_code, "start_date": start_date,
                   "end_date": end_date, "limit": limit, "offset": ""},
        "fields": _TUSHARE_FIELDS,
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(_TUSHARE_HOST, json=payload,
                              timeout=aiohttp.ClientTimeout(total=20)) as r:
                data = await r.json()
        if data.get("code") != 0:
            return {"error": data.get("msg", "tushare error")}
        fields = [_TUSHARE_FIELD_EN.get(f, f)
                  for f in data["data"]["fields"]]
        items = data["data"]["items"]
        rows = [dict(zip(fields, row)) for row in items[:50]]
        content = f"Tushare {ts_code} daily data ({len(rows)} rows):\n"
        content += json.dumps(rows[:5], ensure_ascii=False)
        return {"content": content, "fields": fields, "rows": rows}
    except Exception as e:
        return {"error": str(e)}


# ── 分发入口 ───────────────────────────────────────────────────────────────────

# domain → 所需 API key 环境变量名。SerpAPI 系（前 5 个）共用 GOOGLE_TRENDS_API 这一个 key。
_DOMAIN_REQUIRED_KEY = {
    "amazon": "GOOGLE_TRENDS_API",
    "google_maps": "GOOGLE_TRENDS_API",
    "tripadvisor": "GOOGLE_TRENDS_API",
    "google_flights": "GOOGLE_TRENDS_API",
    "apple_app_store": "GOOGLE_TRENDS_API",
    "google_play_store": "GOOGLE_TRENDS_API",
    "gugudata": "GUGUDATA_API",
    "tushare": "TUSHARE_API",
}
_DOMAIN_KEY_VALUE = {
    "GOOGLE_TRENDS_API": _SERPAPI_KEY,
    "GUGUDATA_API": _GUGUDATA_KEY,
    "TUSHARE_API": _TUSHARE_KEY,
}


async def _call_domain(domain: str, params: Dict[str, Any]) -> Tuple[str, Dict]:
    logger.info("[Domain Data] calling domain=%s params=%s", domain, list(params.keys()))
    required_key = _DOMAIN_REQUIRED_KEY.get(domain)
    if required_key and not _DOMAIN_KEY_VALUE.get(required_key):
        return domain, {
            "error": (
                f"{domain} 功能不可用：未配置 {required_key}。"
                f"请在 ~/.chartgen/config 中填入该 API key 后重试。"
            )
        }
    if domain == "amazon":
        return domain, await _amazon(params.get("keywords", ""))
    if domain == "google_maps":
        return domain, await _google_maps(params.get("keywords", ""))
    if domain == "tripadvisor":
        return domain, await _tripadvisor(params.get("keywords", ""))
    if domain == "google_flights":
        return domain, await _google_flights(
            params.get("departure_id", ""), params.get("arrival_id", ""),
            params.get("outbound_date", ""))
    if domain == "apple_app_store":
        return domain, await _apple_app_store(params.get("keywords", ""))
    if domain == "google_play_store":
        return domain, await _google_play_store(params.get("keywords", ""))
    if domain == "gugudata":
        return domain, await _gugudata(
            params.get("symbol", ""), params.get("type", ""),
            params.get("indicator", "年度"))
    if domain == "tushare":
        return domain, await _tushare(
            params.get("ts_code", ""), params.get("start_date", ""),
            params.get("end_date", ""))
    return domain, {"error": f"unknown domain: {domain}"}


def run(domains: List[Dict[str, Any]], download_images: bool = False) -> str:
    seen: set = set()
    deduped = []
    for d in domains:
        name = d.get("domain", "")
        if name not in seen:
            seen.add(name)
            deduped.append(d)
    domains = deduped
    async def _run_all():
        tasks = [_call_domain(d["domain"], d.get("params", {})) for d in domains]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        contents: Dict[str, str] = {}
        all_metas: List[Dict] = []
        meta_index: Dict[str, List[int]] = {}

        for item in results:
            if isinstance(item, Exception):
                logger.warning("[Domain Data] task exception: %s", item)
                continue
            domain, data = item
            metas = data.pop("_image_metas", [])
            err = data.get("error")
            if err:
                logger.warning("[Domain Data] domain=%s error=%s", domain, err)
            else:
                logger.info("[Domain Data] domain=%s content_len=%d images=%d",
                            domain, len(data.get("content", "")), len(metas))
            contents[domain] = data.get("content", "")
            if metas and download_images:
                meta_index[domain] = list(range(len(all_metas), len(all_metas) + len(metas)))
                all_metas.extend(metas)

        if download_images and all_metas:
            downloaded = await _download_images(all_metas)
            url_to_local = {r["url"]: r["local_path"] for r in downloaded}
            for domain, indices in meta_index.items():
                lines = []
                for i in indices:
                    url = all_metas[i]["url"]
                    if url in url_to_local:
                        lines.append(f"  {url_to_local[url]} — {all_metas[i]['name']}")
                if lines:
                    contents[domain] += f"\nImages ({len(lines)} downloaded):\n" + "\n".join(lines) + "\n"

        return contents

    result = asyncio.run(_run_all())
    return json.dumps(result, ensure_ascii=False, indent=2)


def thinking(args: dict) -> str:
    domains = args.get("domains", [])
    names = [d.get("domain", "") for d in domains]
    return f"querying: {', '.join(names)}"


SCHEMA = {
    "type": "function",
    "function": {
        "name": "domain_data",
        "description": (
            "查询一个或多个垂直领域的外部数据，并发执行，返回结构化 JSON 供后续分析或生成 HTML 报告使用。\n\n"
            "重要：每个 domain 在一次调用中只能出现一次，不可重复。如需多个关键词，合并为一个 keywords 字符串（如 'coffee shop matcha cafe Tokyo'）。\n\n"
            "可用 domain 及说明：\n" +
            "\n".join(f"- {k}: {v}" for k, v in DOMAIN_META.items())
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "domains": {
                    "type": "array",
                    "description": "要查询的 domain 列表，每项包含 domain 名和对应参数",
                    "items": {
                        "type": "object",
                        "properties": {
                            "domain": {
                                "type": "string",
                                "enum": list(DOMAIN_META.keys()),
                                "description": "domain 名称"
                            },
                            "params": {
                                "type": "object",
                                "description": (
                                    "domain 查询参数。各 domain 参数说明：\n"
                                    "- amazon/google_maps/tripadvisor/apple_app_store/google_play_store: {keywords: string}\n"
                                    "- google_flights: {departure_id: string, arrival_id: string, outbound_date: string}  日期格式 YYYY-MM-DD\n"
                                    "- gugudata: {symbol: string, type: string, indicator: string}\n"
                                    "- tushare: {ts_code: string, start_date: string, end_date: string}"
                                )
                            }
                        },
                        "required": ["domain", "params"]
                    }
                },
                "download_images": {
                    "type": "boolean",
                    "description": "是否下载图片到本地 workspace/domain_images/。仅在后续需要生成 HTML/dashboard 报告时设为 true，纯数据查询时保持默认 false。"
                }
            },
            "required": ["domains"]
        }
    }
}
