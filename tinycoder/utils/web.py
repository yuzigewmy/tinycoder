from __future__ import annotations

import asyncio
import html
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 TinyCoder/0.1"
DEFAULT_TIMEOUT_MS = 12_000
DEFAULT_MAX_RETRIES = 2


def _request(url: str, headers: dict[str, str], timeout_ms: int) -> tuple[int, str, str, str]:
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout_ms / 1000) as resp:
            data = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"
            text = data.decode(charset, errors="replace")
            return resp.status, resp.reason, resp.geturl(), resp.headers.get("content-type", ""), text
    except urllib.error.HTTPError as e:
        data = e.read()
        charset = e.headers.get_content_charset() or "utf-8"
        return e.code, e.reason, e.geturl(), e.headers.get("content-type", ""), data.decode(charset, errors="replace")


async def fetch_with_retry(url: str, headers: dict[str, str], timeout_ms: int = DEFAULT_TIMEOUT_MS, max_retries: int = DEFAULT_MAX_RETRIES) -> tuple[int, str, str, str, str]:
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            status, reason, final_url, content_type, text = await asyncio.to_thread(_request, url, headers, timeout_ms)
            if status in {429} or 500 <= status < 600:
                if attempt < max_retries:
                    await asyncio.sleep(0.3 * (2 ** attempt))
                    continue
            return status, reason, final_url, content_type, text
        except Exception as exc:
            last_error = exc
            if attempt < max_retries:
                await asyncio.sleep(0.3 * (2 ** attempt))
                continue
            raise RuntimeError(f"request failed for {url}: {exc}") from exc
    raise RuntimeError(f"request failed for {url}: {last_error}")


def normalize_domain_list(domains: list[str] | None) -> list[str]:
    result = []
    for domain in domains or []:
        raw = domain.strip().lower().lstrip("*." ).lstrip(".")
        if not raw:
            continue
        parsed = urllib.parse.urlparse(raw)
        result.append((parsed.hostname or raw).lower())
    return result


def matches_domain(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def passes_domain_filter(link: str, allowed: list[str], blocked: list[str]) -> bool:
    host = urllib.parse.urlparse(link).hostname or ""
    host = host.lower()
    if not host:
        return False
    if any(matches_domain(host, d) for d in blocked):
        return False
    if not allowed:
        return True
    return any(matches_domain(host, d) for d in allowed)


def strip_tags(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", value)).strip()


def decode_html(value: str) -> str:
    return html.unescape(value)


def first_match(pattern: str, text: str, group: int = 1) -> str | None:
    m = re.search(pattern, text, flags=re.I | re.S)
    return m.group(group) if m else None


def normalize_duckduckgo_link(raw_href: str) -> str:
    href = decode_html(raw_href).strip()
    if not href:
        return ""
    absolute = "https:" + href if href.startswith("//") else href
    parsed = urllib.parse.urlparse(absolute)
    qs = urllib.parse.parse_qs(parsed.query)
    if "uddg" in qs and qs["uddg"]:
        return urllib.parse.unquote(qs["uddg"][0])
    return absolute


def parse_duckduckgo_lite(html_text: str) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    anchors = list(re.finditer(r"<a\b[^>]*>[\s\S]*?</a>", html_text, re.I))
    for i, match in enumerate(anchors):
        anchor = match.group(0)
        class_value = first_match(r"class=(['\"])([\s\S]*?)\1", anchor, 2) or ""
        if not re.search(r"\bresult-link\b", class_value, re.I):
            continue
        raw_href = first_match(r"href=(['\"])([\s\S]*?)\1", anchor, 2) or ""
        title = decode_html(strip_tags(first_match(r"<a\b[^>]*>([\s\S]*?)</a>", anchor) or ""))
        next_index = anchors[i + 1].start() if i + 1 < len(anchors) else len(html_text)
        block = html_text[match.start():next_index]
        snippet = decode_html(strip_tags(first_match(r"<td[^>]*class=(['\"])[^'\"]*\bresult-snippet\b[^'\"]*\1[^>]*>\s*([\s\S]*?)\s*</td>", block, 2) or ""))
        display = decode_html(strip_tags(first_match(r"<span[^>]*class=(['\"])[^'\"]*\blink-text\b[^'\"]*\1[^>]*>([\s\S]*?)</span>", block, 2) or ""))
        link = normalize_duckduckgo_link(raw_href)
        if title and link:
            results.append({"title": title, "link": link, "snippet": snippet, "date": "", "display_link": display})
    return results


def normalize_sogou_link(raw_href: str) -> str:
    href = decode_html(raw_href).strip()
    if not href:
        return ""
    if href.startswith("/"):
        return "https://www.sogou.com" + href
    if href.startswith("//"):
        return "https:" + href
    return href


def parse_sogou_search(html_text: str) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    matches = list(re.finditer(r"<h3\b[^>]*>\s*([\s\S]*?)</h3>", html_text, re.I))
    for i, match in enumerate(matches):
        h3 = match.group(0)
        raw_href = decode_html(first_match(r"href=(['\"])([\s\S]*?)\1", h3, 2) or "")
        title = decode_html(strip_tags(first_match(r"<a\b[^>]*>([\s\S]*?)</a>", h3, 1) or ""))
        link = normalize_sogou_link(raw_href)
        if not title or not link:
            continue
        next_index = matches[i + 1].start() if i + 1 < len(matches) else len(html_text)
        block = html_text[match.start():next_index]
        snippet = decode_html(strip_tags(first_match(r"<(div|p)\b[^>]*class=(['\"])[^'\"]*(fz-mid|str-text-info|text-layout|space-txt)[^'\"]*\2[^>]*>([\s\S]*?)</\1>", block, 4) or ""))
        display = urllib.parse.urlparse(link).hostname or link
        results.append({"title": title, "link": link, "snippet": snippet, "date": "", "display_link": display})
    return results


async def fetch_search_page(provider: str, query: str) -> tuple[int, str, str, str, str]:
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
    if provider == "duckduckgo-lite":
        url = "https://lite.duckduckgo.com/lite/?" + urllib.parse.urlencode({"q": query})
        headers["Accept-Language"] = "en-US,en;q=0.9"
        return await fetch_with_retry(url, headers)
    if provider == "sogou":
        url = "https://www.sogou.com/web?" + urllib.parse.urlencode({"query": query})
        headers["Accept-Language"] = "zh-CN,zh;q=0.9,en;q=0.6"
        return await fetch_with_retry(url, headers)
    raise RuntimeError(f"unsupported search provider: {provider}")


async def search_duckduckgo_lite(options: dict[str, Any]) -> dict[str, Any]:
    allowed = normalize_domain_list(options.get("allowedDomains") or options.get("allowed_domains"))
    blocked = normalize_domain_list(options.get("blockedDomains") or options.get("blocked_domains"))
    max_results = int(options.get("maxResults") or options.get("max_results") or 5)
    errors: list[str] = []
    for provider in ["duckduckgo-lite", "sogou"]:
        try:
            status, reason, _final, _ctype, body = await fetch_search_page(provider, options["query"])
            if status >= 400:
                errors.append(f"{provider}: HTTP {status}")
                continue
            parsed = parse_duckduckgo_lite(body) if provider == "duckduckgo-lite" else parse_sogou_search(body)
            organic = [r for r in parsed if passes_domain_filter(r["link"], allowed, blocked)][:max_results]
            if organic:
                return {"organic": organic, "base_resp": {"status_code": status, "status_msg": reason, "source": provider}}
            errors.append(f"{provider}: no results")
        except Exception as exc:
            errors.append(f"{provider}: {exc}")
    if errors:
        raise RuntimeError("all search providers failed (" + "; ".join(errors) + ")")
    return {"organic": [], "base_resp": {"status_code": 200, "status_msg": "OK", "source": "fallback-empty"}}


def extract_html_redirect_url(html_text: str, base_url: str) -> str | None:
    script = first_match(r"window\.location(?:\.href)?(?:\.replace)?\((['\"])(.*?)\1\)", html_text, 2) or first_match(r"window\.location(?:\.href)?\s*=\s*(['\"])(.*?)\1", html_text, 2)
    meta = first_match(r"<meta[^>]*http-equiv=(['\"])refresh\1[^>]*content=(['\"])[\s\S]*?url\s*=\s*('?)([^\"'>;]+)\3[\s\S]*?\2[^>]*>", html_text, 4) or first_match(r"<meta[^>]*content=(['\"])[\s\S]*?url\s*=\s*('?)([^\"'>;]+)\2[\s\S]*?\1[^>]*http-equiv=(['\"])refresh\4[^>]*>", html_text, 3)
    raw = decode_html((script or meta or "").strip())
    if not raw:
        return None
    return urllib.parse.urljoin(base_url, raw)


def extract_title(html_text: str) -> str | None:
    val = first_match(r"<title[^>]*>([\s\S]*?)</title>", html_text)
    return decode_html(strip_tags(val)).strip() if val else None


def extract_readable_text(html_text: str) -> str:
    text = re.sub(r"<script[\s\S]*?</script>", " ", html_text, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<noscript[\s\S]*?</noscript>", " ", text, flags=re.I)
    text = re.sub(r"<svg[\s\S]*?</svg>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return decode_html(re.sub(r"\s+", " ", text).strip())


async def fetch_web_page(options: dict[str, Any]) -> dict[str, Any]:
    url = options["url"]
    max_chars = int(options.get("maxChars") or options.get("max_chars") or 12000)
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,text/plain;q=0.8,*/*;q=0.7",
        "Accept-Language": "en-US,en;q=0.9",
    }
    status, reason, final_url, content_type, text = await fetch_with_retry(url, headers)
    if "html" in content_type:
        redirect = extract_html_redirect_url(text, final_url)
        if redirect and redirect != final_url:
            status, reason, final_url, content_type, text = await fetch_with_retry(redirect, headers)
    if "html" in content_type:
        content = extract_readable_text(text)[:max_chars]
        title = extract_title(text)
    else:
        content = text[:max_chars]
        title = None
    return {"url": url, "finalUrl": final_url, "status": status, "statusText": reason, "contentType": content_type, "title": title, "content": content}

searchDuckDuckGoLite = search_duckduckgo_lite
fetchWebPage = fetch_web_page
