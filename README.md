# IndexLens — Know exactly why your pages aren't indexed

> **Your client's pages aren't in Google — and Ahrefs charges $99/month to tell you that.**
> IndexLens scans every URL, names the exact blocker (`noindex`, `robots.txt`, redirect, canonical, dead page), checks live search presence, and hands you a prioritized fix list in minutes.
> **One command. One-time $29. No subscription.**

---

## Why IndexLens?

| | Ahrefs / SEMrush | Human audit | **IndexLens** |
|---|---|---|---|
| Price | $99+ **per month** | $100–400 per site | **$29 one-time** |
| Per-URL root cause | vague score | days of waiting | **exact blocker, per URL** |
| Live Google presence | partial | manual | **automatic** |
| Client-ready report | export fuss | invoice | **Markdown + JSON, instantly** |

**The gap:** free tools (Google Search Console) are vague, paid suites are subscriptions, and `site:` checks are unreliable. Nobody sells a **one-shot, per-URL diagnosis** — until now.

## What it checks (per URL)

1. **HTTP status** + full redirect chain
2. **`noindex`** — meta tag or `X-Robots-Tag` header
3. **`robots.txt`** — is Googlebot actually blocked? (wildcards, `Allow` vs `Disallow`, group precedence)
4. **`canonical`** — missing / self / pointing elsewhere
5. **Live search presence** — is the URL really in search results?

Then it assigns a verdict:

```
INDEXED | NOT_INDEXED | BLOCKED:NOINDEX | BLOCKED:ROBOTS
BLOCKED:REDIRECT_301 | BLOCKED:HTTP_404 | ERROR:UNREACHABLE | ...
```

…and orders every broken URL into a **Priority Fix List** by traffic impact.

## Install

```bash
# no dependencies — Python 3.9+ standard library only
git clone https://github.com/aoodiakdiro0001-ops/indexlens.git
cd indexlens
```

Optional (recommended on Windows, for SSL certificates):

```bash
pip install certifi
```

## Usage

```bash
# audit a whole sitemap
python indexlens.py https://example.com/sitemap.xml

# audit specific URLs
python indexlens.py https://a.com/ https://a.com/pricing

# from a local file (one URL per line)
python indexlens.py urls.txt

# faster: skip the search-presence checks
python indexlens.py https://example.com --no-search --out ./report
```

**Outputs:** `report.md` (client-ready) + `report.json` (machine-readable).

### Sample output

```
| URL | HTTP | Verdict | Fix |
|---|---|---|---|
| https://a.com/blog/old-post | 200 | `BLOCKED:NOINDEX` | Remove the `noindex` meta tag or `X-Robots-Tag` header. |
| https://a.com/pricing | 200 | `BLOCKED:ROBOTS` | Allow this URL for Googlebot in robots.txt. |
| https://a.com/new-page | 200 | `NOT_INDEXED` | Request indexing in Google Search Console; improve internal links. |
```

## Options

| Flag | Default | Description |
|---|---|---|
| `--out DIR` | `.` | where to write `report.md` / `report.json` |
| `--no-search` | off | skip live search checks (faster, offline-friendly) |
| `--timeout SEC` | `15` | per-request timeout |
| `--delay SEC` | `1.0` | politeness delay between search queries |

## Scope & fairness

IndexLens only reads **public** pages and `robots.txt`, sends one polite `IndexLens/1.0` user-agent, and rate-limits search checks. Audit sites you own or are authorized to check.

## Support

- **Author:** Baran Avisat — `baranavisat@gmail.com`
- **Store:** [baranavi.gumroad.com](https://baranavi.gumroad.com/)
- **License:** MIT — © 2026 Baran Avisat

---

© 2026 Baran Avisat. All rights reserved. Free updates for life.
