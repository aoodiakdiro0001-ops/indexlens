#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
IndexLens — one-shot SEO indexation & technical audit CLI.

Scans every URL in a sitemap (or a list), names the exact blocker
(noindex / robots / redirect / canonical / dead), checks live search
presence, and writes a client-ready Markdown + JSON report with a
priority fix list.

Author:   Baran Avisat <baranavisat@gmail.com>
Storefront: https://baranavi.gumroad.com/
License:  MIT
"""
from __future__ import annotations

import argparse
import gzip
import io
import json
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, asdict
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    import certifi  # type: ignore
    SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:  # noqa: BLE001
    SSL_CTX = ssl.create_default_context()

USER_AGENT = "IndexLens/1.0 (SEO audit tool)"
SEARCH_ENDPOINT = "https://html.duckduckgo.com/html/"

# --------------------------------------------------------------------------
# HTML parsing (pure, testable)
# --------------------------------------------------------------------------
class _MetaParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta_robots: List[str] = []
        self.canonical: Optional[str] = None
        self.title: str = ""

    def handle_starttag(self, tag: str, attrs) -> None:
        ad = {k.lower(): (v or "") for k, v in attrs}
        if tag.lower() == "meta" and ad.get("name", "").lower() == "robots":
            self.meta_robots.append(ad.get("content", "").lower())
        elif tag.lower() == "link" and "canonical" in ad.get("rel", "").lower():
            self.canonical = ad.get("href") or None
        elif tag.lower() == "title":
            self._in_title = True

    def handle_data(self, data: str) -> None:
        if getattr(self, "_in_title", False):
            self.title += data

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._in_title = False


def extract_noindex(html: str, header_robots: str = "") -> bool:
    """True when the page or its headers forbid indexing."""
    p = _MetaParser()
    try:
        p.feed(html or "")
    except Exception:  # noqa: BLE001
        pass
    directives = " ".join(p.meta_robots).lower()
    if "noindex" in directives:
        return True
    return "noindex" in (header_robots or "").lower()


def extract_canonical(html: str, base_url: str) -> Optional[str]:
    """Absolute canonical URL, or None if absent."""
    p = _MetaParser()
    try:
        p.feed(html or "")
    except Exception:  # noqa: BLE001
        return None
    if not p.canonical:
        return None
    return urllib.parse.urljoin(base_url, p.canonical)


# --------------------------------------------------------------------------
# robots.txt (pure, testable)
# --------------------------------------------------------------------------
@dataclass
class RobotsRule:
    allow: bool
    pattern: str  # raw path pattern from robots.txt

    def _regex(self) -> "re.Pattern[str]":
        out = ""
        for ch in self.pattern:
            if ch == "*":
                out += ".*"
            elif ch == "$":
                out += "$"
            else:
                out += re.escape(ch)
        return re.compile("^" + out)

    def matches(self, path: str) -> bool:
        return bool(self._regex().match(path))


def parse_robots(text: str, agent: str = "Googlebot") -> List[RobotsRule]:
    """Rules that apply to `agent` (most specific group wins)."""
    agent_l = agent.lower()
    groups: List[Tuple[List[str], List[RobotsRule]]] = []
    cur_agents: List[str] = []
    cur_rules: List[RobotsRule] = []

    def flush() -> None:
        nonlocal cur_agents, cur_rules
        if cur_agents:
            groups.append((cur_agents, cur_rules))
        cur_agents, cur_rules = [], []

    for raw in (text or "").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, _, val = line.partition(":")
        key, val = key.strip().lower(), val.strip()
        if key == "user-agent":
            if cur_rules:
                flush()
            cur_agents.append(val)
        elif key in ("disallow", "allow"):
            cur_rules.append(RobotsRule(allow=(key == "allow"), pattern=val))
    flush()

    # The group naming our agent exactly wins; otherwise fall back to '*'.
    specific = [rules for agents, rules in groups
                if any(a.lower() == agent_l for a in agents)]
    if specific:
        return max(specific, key=len)
    star = [rules for agents, rules in groups if any(a == "*" for a in agents)]
    if star:
        return max(star, key=len)
    return []


def path_allowed(path: str, rules: List[RobotsRule]) -> bool:
    """Standard robots semantics: longest matching pattern wins; allow > deny."""
    best: Optional[Tuple[int, bool]] = None
    for r in rules:
        if r.pattern == "":
            continue
        if r.matches(path):
            if best is None or len(r.pattern) > best[0]:
                best = (len(r.pattern), r.allow)
    if best is None:
        return True
    return best[1]


# --------------------------------------------------------------------------
# Verdict logic (pure, testable)
# --------------------------------------------------------------------------
def make_verdict(status: Optional[int], noindex: bool, robots_blocked: bool,
                 search: str) -> str:
    if status is None:
        return "ERROR:UNREACHABLE"
    if status in (404, 410):
        return f"BLOCKED:HTTP_{status}"
    if noindex:
        return "BLOCKED:NOINDEX"
    if robots_blocked:
        return "BLOCKED:ROBOTS"
    if 300 <= status < 400:
        return f"BLOCKED:REDIRECT_{status}"
    if status >= 500:
        return f"ERROR:HTTP_{status}"
    if search == "present":
        return "INDEXED"
    if search == "absent":
        return "NOT_INDEXED"
    return "UNKNOWN"


VERDICT_ORDER = [
    "ERROR:UNREACHABLE", "ERROR", "BLOCKED:NOINDEX", "BLOCKED:ROBOTS",
    "BLOCKED:REDIRECT", "BLOCKED:HTTP", "NOT_INDEXED", "UNKNOWN", "INDEXED",
]


def priority_rank(verdict: str) -> int:
    for i, prefix in enumerate(VERDICT_ORDER):
        if verdict == prefix or verdict.startswith(prefix):
            return i
    return len(VERDICT_ORDER)


FIX_HINTS = {
    "BLOCKED:NOINDEX": "Remove the `noindex` meta tag or `X-Robots-Tag` header.",
    "BLOCKED:ROBOTS": "Allow this URL for Googlebot in robots.txt.",
    "BLOCKED:REDIRECT_": "Update internal links and sitemap to the final destination URL.",
    "BLOCKED:HTTP_404": "Restore the page or remove it from the sitemap.",
    "BLOCKED:HTTP_410": "Remove the URL from the sitemap (page is gone).",
    "NOT_INDEXED": "Request indexing in Google Search Console; improve internal links and content quality.",
    "ERROR:UNREACHABLE": "Fix server/connectivity — the URL does not respond.",
    "ERROR:HTTP_": "Fix the 5xx server error.",
    "UNKNOWN": "Re-run with search checks enabled (--search).",
}


def fix_hint(verdict: str) -> str:
    for key, hint in FIX_HINTS.items():
        if verdict.startswith(key):
            return hint
    return "No action needed."


# --------------------------------------------------------------------------
# Network layer
# --------------------------------------------------------------------------
def _opener(chain: List[dict]):
    class _Rec(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            chain.append({"status": code, "from": req.full_url, "to": newurl})
            return super().redirect_request(req, fp, code, msg, headers, newurl)
    return urllib.request.build_opener(_Rec, urllib.request.HTTPSHandler(context=SSL_CTX))


def http_get(url: str, timeout: float, max_bytes: int = 3_000_000) -> dict:
    """Fetch a URL; returns status/final_url/headers/body/redirect_chain/error."""
    chain: List[dict] = []
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept-Encoding": "gzip",
        "Accept": "*/*",
    })
    try:
        with _opener(chain).open(req, timeout=timeout) as resp:
            raw = resp.read(max_bytes)
            if resp.headers.get("Content-Encoding") == "gzip":
                raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read(max_bytes)
            charset = resp.headers.get_content_charset() or "utf-8"
            body = raw.decode(charset, errors="replace")
            return {
                "status": getattr(resp, "status", 200),
                "final_url": resp.geturl(),
                "headers": dict(resp.headers),
                "body": body,
                "redirect_chain": chain,
                "error": None,
            }
    except urllib.error.HTTPError as e:
        return {"status": e.code, "final_url": url, "headers": dict(e.headers or {}),
                "body": "", "redirect_chain": chain, "error": None}
    except Exception as e:  # noqa: BLE001
        return {"status": None, "final_url": url, "headers": {}, "body": "",
                "redirect_chain": chain, "error": f"{type(e).__name__}: {e}"}


def check_search_presence(url: str, timeout: float) -> str:
    """present / absent / unknown — via DuckDuckGo HTML (free, no key)."""
    q = urllib.parse.quote(f"site:{url}")
    res = http_get(f"{SEARCH_ENDPOINT}?q={q}", timeout=timeout)
    if res["error"] or not res["body"]:
        return "unknown"
    body = res["body"]
    if "No results" in body or "didn't match any results" in body:
        return "absent"
    if 'result__a' in body or 'results_links' in body:
        return "present"
    return "unknown"


# --------------------------------------------------------------------------
# Sitemap handling
# --------------------------------------------------------------------------
def parse_sitemap(xml_text: str) -> List[str]:
    urls: List[str] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        # fallback: regex for <loc>
        return [m.strip() for m in re.findall(r"<loc>\s*([^<]+?)\s*</loc>", xml_text)]
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1].lower()
        if tag == "loc" and el.text:
            loc = el.text.strip()
            if loc.endswith(".xml") and "sitemap" in loc.lower():
                continue  # nested sitemap: one level deep handled by caller
            urls.append(loc)
    return urls


def collect_targets(targets: List[str], timeout: float) -> List[str]:
    """Expand sitemap URLs / local files / plain URLs into a page URL list."""
    pages: List[str] = []
    for t in targets:
        if Path(t).exists():
            text = Path(t).read_text(encoding="utf-8", errors="replace")
            if text.lstrip().startswith("<?xml") or "<urlset" in text:
                pages.extend(parse_sitemap(text))
            else:
                pages.extend(line.strip() for line in text.splitlines()
                             if line.strip() and not line.startswith("#"))
            continue
        if t.endswith(".xml") or "sitemap" in t.lower():
            res = http_get(t, timeout=timeout)
            if res["body"]:
                pages.extend(parse_sitemap(res["body"]))
            else:
                pages.append(t)
            continue
        pages.append(t)
    seen, out = set(), []
    for p in pages:
        p = p.strip()
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return out


# --------------------------------------------------------------------------
# Audit engine
# --------------------------------------------------------------------------
@dataclass
class PageResult:
    url: str
    status: Optional[int] = None
    final_url: str = ""
    redirect_chain: list = field(default_factory=list)
    noindex: bool = False
    robots_blocked: bool = False
    canonical: Optional[str] = None
    search: str = "unknown"
    verdict: str = ""
    error: Optional[str] = None

    @property
    def fix(self) -> str:
        return fix_hint(self.verdict)


def audit_url(url: str, robots_rules: List[RobotsRule], timeout: float,
              do_search: bool) -> PageResult:
    r = PageResult(url=url)
    res = http_get(url, timeout=timeout)
    r.status = res["status"]
    r.final_url = res["final_url"]
    r.redirect_chain = res["redirect_chain"]
    r.error = res["error"]
    parsed = urllib.parse.urlsplit(r.final_url or url)
    r.robots_blocked = not path_allowed(parsed.path or "/", robots_rules)
    r.noindex = extract_noindex(res["body"], res["headers"].get("X-Robots-Tag", ""))
    r.canonical = extract_canonical(res["body"], r.final_url or url)
    if do_search and r.status and r.status < 400:
        time.sleep(1.0)
        r.search = check_search_presence(url, timeout=timeout)
    r.verdict = make_verdict(r.status, r.noindex, r.robots_blocked, r.search)
    return r


def load_robots(base_urls: List[str], timeout: float) -> List[RobotsRule]:
    origins = {urllib.parse.urlsplit(u).scheme + "://" +
               urllib.parse.urlsplit(u).netloc for u in base_urls}
    for origin in list(origins):
        res = http_get(origin + "/robots.txt", timeout=timeout)
        if res["status"] == 200 and res["body"]:
            return parse_robots(res["body"])
    return []


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------
def render_markdown(results: List[PageResult], target_label: str) -> str:
    counts: Dict[str, int] = {}
    for r in results:
        key = r.verdict.split(":")[0] if r.verdict else "UNKNOWN"
        counts[key] = counts.get(key, 0) + 1
    ordered = sorted(results, key=lambda r: (priority_rank(r.verdict), r.url))
    lines = [
        "# IndexLens Report",
        "",
        f"- **Target:** `{target_label}`",
        f"- **URLs scanned:** {len(results)}",
        f"- **Generated:** {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Summary",
        "",
        "| Status | Count |",
        "|---|---|",
    ]
    for k in sorted(counts, key=lambda k: -counts[k]):
        lines.append(f"| {k} | {counts[k]} |")
    lines += ["", "## Results", "",
              "| URL | HTTP | Verdict | Fix |", "|---|---|---|---|"]
    for r in ordered:
        status = str(r.status) if r.status is not None else "—"
        lines.append(f"| {r.url} | {status} | `{r.verdict}` | {r.fix} |")
    fixes = [r for r in ordered if not r.verdict.startswith(("INDEXED", "UNKNOWN"))]
    lines += ["", "## Priority Fix List", ""]
    if not fixes:
        lines.append("All scanned URLs look healthy. 🎉")
    else:
        for i, r in enumerate(fixes, 1):
            lines.append(f"{i}. **{r.url}** — `{r.verdict}` — {r.fix}")
    lines += ["", "---",
              f"*Generated by IndexLens — © 2026 Baran Avisat — "
              f"support: baranavisat@gmail.com*", ""]
    return "\n".join(lines)


def render_json(results: List[PageResult]) -> str:
    return json.dumps({
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "tool": "IndexLens",
        "owner": "Baran Avisat",
        "results": [asdict(r) | {"fix": r.fix} for r in results],
    }, ensure_ascii=False, indent=2)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="indexlens",
        description="One-shot SEO indexation audit: names the exact blocker per URL.")
    ap.add_argument("targets", nargs="+",
                    help="sitemap.xml URL, local sitemap/URL-list file, or page URLs")
    ap.add_argument("--out", default=".", help="output directory (default: .)")
    ap.add_argument("--timeout", type=float, default=15.0)
    ap.add_argument("--search", dest="search", action="store_true", default=True,
                    help="check live search presence (default on)")
    ap.add_argument("--no-search", dest="search", action="store_false",
                    help="skip search presence checks (faster)")
    ap.add_argument("--delay", type=float, default=1.0,
                    help="delay between search queries in seconds")
    args = ap.parse_args(argv)

    print(f"[*] IndexLens — resolving {len(args.targets)} target(s)...")
    pages = collect_targets(args.targets, timeout=args.timeout)
    if not pages:
        print("[!] No page URLs found.", file=sys.stderr)
        return 2
    print(f"[*] {len(pages)} URL(s) queued. Loading robots.txt...")
    rules = load_robots(pages, timeout=args.timeout)

    results: List[PageResult] = []
    for i, url in enumerate(pages, 1):
        print(f"[{i}/{len(pages)}] {url}")
        results.append(audit_url(url, rules, args.timeout, args.search))

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    label = args.targets[0]
    (out / "report.md").write_text(render_markdown(results, label),
                                   encoding="utf-8")
    (out / "report.json").write_text(render_json(results), encoding="utf-8")

    counts: Dict[str, int] = {}
    for r in results:
        k = r.verdict.split(":")[0]
        counts[k] = counts.get(k, 0) + 1
    print("[+] Done:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print(f"[+] Wrote {out / 'report.md'} and {out / 'report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
