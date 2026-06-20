#!/usr/bin/env python3
"""Fetch the Bitget Unified Trading Account (UTA / v3) API docs that RUNECLAW
relies on and render them to clean Markdown under docs/bitget-uta/.

The Bitget docs site is a server-rendered Docusaurus app, so the full article
body is present in the initial HTML inside <div class="theme-doc-markdown ...">.
We extract that fragment and convert it to Markdown locally (no external
dependencies, no per-page LLM calls).

Usage:
    python3 scripts/fetch_bitget_uta_docs.py

Re-running is cheap: fetched HTML is cached in .cache/bitget-uta/.
"""
from __future__ import annotations

import os
import re
import sys
import time
import urllib.request
from html.parser import HTMLParser

BASE = "https://www.bitget.com/api-doc/uta/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(REPO, "docs", "bitget-uta")
CACHE_DIR = os.path.join(REPO, ".cache", "bitget-uta")

# Pages grouped into output files. Order within a group is preserved.
GROUPS: dict[str, dict] = {
    "README": {
        "title": "Bitget UTA API — Overview, Auth & Conventions",
        "slugs": [
            "intro", "guide", "best-practices", "enum", "error-code/restapi",
        ],
    },
    "market-data": {
        "title": "Bitget UTA API — Market Data (public REST)",
        "slugs": [
            "public/Instruments", "public/Tickers", "public/OrderBook",
            "public/Get-Candle-Data", "public/Get-History-Candle-Data",
            "public/Fills", "public/Get-Current-Funding-Rate",
            "public/Get-History-Funding-Rate", "public/Get-Open-Interest",
            "public/Get-Contracts-Oi", "public/Get-Position-Tier-Data",
            "public/Get-Discount-Rate", "public/Get-Index-Components",
        ],
    },
    "account": {
        "title": "Bitget UTA API — Account",
        "slugs": [
            "account/Get-Account", "account/Get-Account-Info",
            "account/Get-Account-Setting", "account/Adjust-Account-Mode",
            "account/Change-Leverage", "account/Change-Position-Mode",
            "account/Get-Account-Fee-Rate", "account/Set-Margin",
            "account/Get-Max-Transferable", "account/Get-Max-Withdrawal",
            "account/Get-Financial-Records", "account/Switch-Account",
            "account/Get-Switch-Status", "account/Get-OI-Limit",
            "account/Get-Account-Funding-Assets",
        ],
    },
    "trade": {
        "title": "Bitget UTA API — Trade (orders & positions)",
        "slugs": [
            "trade/Place-Order", "trade/Place-Batch", "trade/Modify-Order",
            "trade/Batch-Modify-Orders", "trade/Cancel-Order",
            "trade/Cancel-Batch", "trade/Cancel-All-Order",
            "trade/Close-All-Positions", "trade/CountDown-Cancel-All",
            "trade/Get-Order-Details", "trade/Get-Order-Pending",
            "trade/Get-Order-History", "trade/Get-Order-Fills",
            "trade/Get-Position", "trade/Get-Position-History",
            "trade/Get-Position-ADL-Rank", "trade/Get-Max-Open-Available",
        ],
    },
    "strategy": {
        "title": "Bitget UTA API — Strategy / Plan orders (TP/SL, triggers)",
        "slugs": [
            "strategy/Place-Strategy-Order", "strategy/Modify-Strategy-Order",
            "strategy/Cancel-Strategy-Order",
            "strategy/Get-Unfilled-Strategy-Orders",
            "strategy/Get-History-Strategy-Orders",
        ],
    },
    "websocket": {
        "title": "Bitget UTA API — WebSocket (public + private channels)",
        "slugs": [
            "websocket/public/Tickers-Channel",
            "websocket/public/Candlesticks-Channel",
            "websocket/public/Order-Book-Channel",
            "websocket/public/New-Trades-Channel",
            "websocket/public/Liquidation-Channel",
            "websocket/private/Account-Channel",
            "websocket/private/Positions-Channel",
            "websocket/private/Order-Channel",
            "websocket/private/Fill-Channel",
            "websocket/private/Fast-Fill-Channel",
            "websocket/private/Place-Order-Channel",
            "websocket/private/Cancel-Order-Channel",
            "websocket/private/Modify-Order-Channel",
            "websocket/private/Batch-Place-Order-Channel",
            "websocket/private/Batch-Cancel-Order-Channel",
            "websocket/private/Batch-Modify-Order-Channel",
            "websocket/private/Strategy-Order-Channel",
            "websocket/private/ADL-Notification-Channel",
        ],
    },
}

# The Bitget "Quick Start" page lists the auth headers but does not spell out
# the HMAC prehash recipe. This block documents the signing scheme RUNECLAW
# uses in bot/core/live_executor.py (the standard Bitget v3 scheme), so the
# reference is self-contained.
SIGNING_NOTE = """## Authentication & request signing

All private REST endpoints require these headers:

| Header | Value |
| --- | --- |
| `ACCESS-KEY` | API key |
| `ACCESS-SIGN` | base64 signature (recipe below) |
| `ACCESS-TIMESTAMP` | Unix time in **milliseconds** |
| `ACCESS-PASSPHRASE` | passphrase set when the key was created |
| `Content-Type` | `application/json` |
| `locale` | e.g. `en-US` |

**Signature recipe** (matches `bot/core/live_executor.py`):

```
prehash   = timestamp + METHOD + requestPath + queryString + body
ACCESS-SIGN = base64( HMAC_SHA256( secretKey, prehash ) )
```

- `timestamp` is the same value sent in `ACCESS-TIMESTAMP` (ms) and must be
  within ~30s of server time (error `40008` = expired, `40009` = bad sign).
- `METHOD` is upper-case (`GET` / `POST`).
- `requestPath` starts with `/api/v3/...`.
- `queryString` is `?k=v&...` for GET (empty if none); `body` is the raw JSON
  string for POST (empty for GET).

```python
import time, hmac, hashlib, base64, json
ts   = str(int(time.time() * 1000))
body = json.dumps({"category": "USDT-FUTURES", "symbol": "BTCUSDT", ...})  # "" for GET
pre  = ts + "POST" + "/api/v3/trade/place-order" + body
sign = base64.b64encode(
    hmac.new(secret.encode(), pre.encode(), hashlib.sha256).digest()).decode()
```

- **Demo trading:** add header `paptrading: 1` and use the demo WS hosts.
- **WebSocket login** (private channels): RUNECLAW currently only uses the
  *public* WS for market data, so this is the standard Bitget `op: "login"`
  handshake (verify against the live endpoint before relying on it) — args
  `{apiKey, passphrase, timestamp, sign}`, where the prehash is
  `timestamp + "GET" + "/user/verify"` with the timestamp in **seconds**.
"""

VOID = {"br", "img", "hr", "input", "meta", "link", "source", "area",
        "base", "col", "embed", "param", "track", "wbr"}


class Node:
    __slots__ = ("tag", "attrs", "children", "data")

    def __init__(self, tag="", attrs=None, data=None):
        self.tag = tag
        self.attrs = dict(attrs or {})
        self.children = []
        self.data = data  # text for text nodes


class TreeBuilder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root")
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1].children.append(Node(tag, attrs))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        self.stack[-1].children.append(Node("#text", data=data))


def _clean(text: str) -> str:
    return re.sub(r"[ \t\r\n]+", " ", text)


def inline(node: Node) -> str:
    """Render a node's content as inline markdown (for headings/cells/paras)."""
    out = []
    for c in node.children:
        if c.tag == "#text":
            out.append(_clean(c.data))
        elif c.tag == "br":
            out.append("\n")
        elif c.tag in ("strong", "b"):
            out.append(f"**{inline(c).strip()}**")
        elif c.tag in ("em", "i"):
            out.append(f"*{inline(c).strip()}*")
        elif c.tag == "code":
            out.append(f"`{inline(c).strip()}`")
        elif c.tag == "a":
            cls = c.attrs.get("class", "")
            if "hash-link" in cls:
                continue
            out.append(inline(c))
        elif c.tag in ("#root", "span", "p", "div"):
            out.append(inline(c))
        else:
            out.append(inline(c))
    return "".join(out)


def raw_text(node: Node) -> str:
    out = []
    for c in node.children:
        if c.tag == "#text":
            out.append(c.data)
        elif c.tag == "br":
            out.append("\n")
        else:
            out.append(raw_text(c))
    return "".join(out)


def cell(node: Node) -> str:
    txt = inline(node)
    txt = txt.replace("\n", "<br>").replace("|", "\\|")
    return re.sub(r"\s+", " ", txt).strip()


def render_table(node: Node) -> str:
    rows: list[list[str]] = []
    header: list[str] | None = None
    for section in _descendants(node, ("thead", "tbody")):
        for tr in _children(section, "tr"):
            cells = [cell(td) for td in tr.children
                     if td.tag in ("td", "th")]
            if not cells:
                continue
            if section.tag == "thead" and header is None:
                header = cells
            else:
                rows.append(cells)
    # Fallback: table with bare <tr> (no thead/tbody)
    if header is None and not rows:
        trs = list(_descendants(node, ("tr",)))
        for i, tr in enumerate(trs):
            cells = [cell(td) for td in tr.children if td.tag in ("td", "th")]
            if not cells:
                continue
            if i == 0:
                header = cells
            else:
                rows.append(cells)
    if header is None:
        header = []
    width = max([len(header)] + [len(r) for r in rows] or [0])
    if width == 0:
        return ""
    header += [""] * (width - len(header))
    lines = ["| " + " | ".join(header) + " |",
             "| " + " | ".join(["---"] * width) + " |"]
    for r in rows:
        r += [""] * (width - len(r))
        lines.append("| " + " | ".join(r) + " |")
    return "\n".join(lines) + "\n"


def _children(node: Node, tag: str):
    return [c for c in node.children if c.tag == tag]


def _descendants(node: Node, tags: tuple):
    for c in node.children:
        if c.tag in tags:
            yield c
        else:
            yield from _descendants(c, tags)


def render_list(node: Node, depth: int = 0) -> str:
    ordered = node.tag == "ol"
    out = []
    n = 0
    for li in _children(node, "li"):
        n += 1
        marker = f"{n}. " if ordered else "- "
        # split inline content from nested lists
        inline_parts = []
        nested = []
        for c in li.children:
            if c.tag in ("ul", "ol"):
                nested.append(c)
            else:
                tmp = Node("#wrap")
                tmp.children = [c]
                inline_parts.append(inline(tmp))
        text = re.sub(r"\s+", " ", "".join(inline_parts)).strip()
        text = text.replace("\n", " ")
        out.append("  " * depth + marker + text)
        for nl in nested:
            out.append(render_list(nl, depth + 1))
    return "\n".join(out) + "\n"


def render(node: Node) -> str:
    parts = []
    for c in node.children:
        t = c.tag
        if t == "#text":
            s = _clean(c.data).strip()
            if s:
                parts.append(s + "\n")
        elif t in ("h1", "h2", "h3", "h4", "h5", "h6"):
            level = int(t[1])
            parts.append("\n" + "#" * level + " " + inline(c).strip() + "\n")
        elif t == "p":
            s = inline(c).strip()
            if s:
                parts.append("\n" + s + "\n")
        elif t in ("ul", "ol"):
            parts.append("\n" + render_list(c))
        elif t == "table":
            parts.append("\n" + render_table(c) + "\n")
        elif t == "pre":
            code = raw_text(c).strip("\n")
            parts.append("\n```\n" + code + "\n```\n")
        elif t in ("blockquote",):
            inner = render(c).strip()
            parts.append("\n" + "\n".join("> " + ln for ln in
                                          inner.splitlines()) + "\n")
        elif t == "br":
            continue
        elif t in ("img", "hr"):
            continue
        else:
            parts.append(render(c))
    return "".join(parts)


def extract_article(html: str) -> str:
    start = html.find("theme-doc-markdown")
    if start == -1:
        return ""
    start = html.find(">", start) + 1
    end = html.find("</article>", start)
    if end == -1:
        end = html.find("pagination-nav", start)
    return html[start:end] if end != -1 else html[start:]


def fetch(slug: str) -> str | None:
    cache = os.path.join(CACHE_DIR, slug.replace("/", "_") + ".html")
    if os.path.exists(cache) and os.path.getsize(cache) > 0:
        with open(cache, encoding="utf-8") as f:
            return f.read()
    url = BASE + slug
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml",
    })
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                html = resp.read().decode("utf-8", "replace")
            os.makedirs(CACHE_DIR, exist_ok=True)
            with open(cache, "w", encoding="utf-8") as f:
                f.write(html)
            time.sleep(0.3)  # be polite
            return html
        except Exception as e:  # noqa: BLE001
            wait = 2 ** attempt
            print(f"  ! {slug}: {e} (retry in {wait}s)", file=sys.stderr)
            time.sleep(wait)
    return None


def page_to_md(slug: str) -> tuple[str, bool]:
    html = fetch(slug)
    if not html:
        return f"\n> **Failed to fetch** `{slug}`\n", False
    frag = extract_article(html)
    if not frag:
        return f"\n> **No content** for `{slug}`\n", False
    tb = TreeBuilder()
    tb.feed(frag)
    md = render(tb.root)
    md = re.sub(r"\n{3,}", "\n\n", md).strip()
    src = BASE + slug
    md += f"\n\n[Source]({src})\n"
    return md, True


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    index_lines = [
        "# Bitget Unified Trading Account (UTA / v3) API — RUNECLAW reference",
        "",
        "Local mirror of the Bitget UTA API docs (the v3 endpoints RUNECLAW "
        "trades against). Generated by `scripts/fetch_bitget_uta_docs.py`.",
        "",
        f"- Base REST: `https://api.bitget.com` · doc root: <{BASE}>",
        "- Re-generate: `python3 scripts/fetch_bitget_uta_docs.py`",
        "",
        "## Files",
        "",
    ]
    ok_total = 0
    fail_total = 0
    for name, group in GROUPS.items():
        fname = "README.md" if name == "README" else f"{name}.md"
        print(f"== {fname} ==")
        out = [f"# {group['title']}", ""]
        # mini TOC
        out.append("| Endpoint / Channel | Slug |")
        out.append("| --- | --- |")
        for slug in group["slugs"]:
            label = slug.split("/")[-1].replace("-", " ")
            anchor = label.lower().replace(" ", "-")
            out.append(f"| [{label}](#{anchor}) | `{slug}` |")
        out.append("")
        for slug in group["slugs"]:
            md, ok = page_to_md(slug)
            ok_total += ok
            fail_total += (not ok)
            print(f"  {'ok ' if ok else 'FAIL'} {slug}")
            out.append("\n---\n")
            out.append(md)
        with open(os.path.join(OUT_DIR, fname), "w", encoding="utf-8") as f:
            f.write("\n".join(out) + "\n")
        if name != "README":
            index_lines.append(
                f"- [`{fname}`]({fname}) — {group['title']} "
                f"({len(group['slugs'])} pages)")
    # Prepend index links to README
    readme_path = os.path.join(OUT_DIR, "README.md")
    with open(readme_path, encoding="utf-8") as f:
        readme_body = f.read()
    with open(readme_path, "w", encoding="utf-8") as f:
        f.write("\n".join(index_lines) + "\n\n" + SIGNING_NOTE +
                "\n---\n\n" + readme_body)
    print(f"\nDone. {ok_total} pages ok, {fail_total} failed. -> {OUT_DIR}")
    return 1 if fail_total else 0


if __name__ == "__main__":
    raise SystemExit(main())
