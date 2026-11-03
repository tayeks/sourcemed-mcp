#!/usr/bin/env python3
"""SourceMed MCP server.

Exposes the SourceMed library (https://sourcemed.io/) — a source-verified
medical evidence library built for agents — as MCP tools over stdio:

  ask_sourcemed(question)  Ask the library a clinical question. Calls the
                           site's Ask endpoint (POST /api/ask {"q": ...}),
                           which returns only verified cards, with shelf
                           citations, or {"covered": false}.
  list_shelves()           All shelves (number, title, area, card count).
  get_shelf(n)             One shelf in full: question, balance line,
                           MeSH, and its cards (headline, result, source).
  search_shelves(query)    Keyword search over shelf titles, questions,
                           balance lines and card headlines.

Data sources (public, no credentials):
  - Shelf index: the homepage's public shelf sections
    (https://sourcemed.io/). llms.txt documents the library and carries
    the definitions in full, but does not itself contain a shelf list.
  - Ask: POST https://sourcemed.io/api/ask with JSON {"q": question}.

HTTP is done with curl: behind some egress proxies, Python's HTTP stack
receives truncated streams from the site's worker (IncompleteRead at a
fixed byte count), while curl receives the full body reliably.

Terms (from the site): content CC BY with attribution — cite as
"SourceMed Shelf #NN, sourcemed.io". Content signals: ai-input=yes,
ai-train=no — query the live site; do not train on its content.
"""
import html as html_mod
import json
import re
import subprocess
import time

from mcp.server.fastmcp import FastMCP

BASE = "https://sourcemed.io"
HOME_URL = BASE + "/"
ASK_URL = BASE + "/api/ask"
UA = "sourcemed-mcp/1.0"
CACHE_TTL_SECONDS = 3600

mcp = FastMCP("sourcemed")


# --------------------------------------------------------------------- HTTP
def _curl(args: list[str], timeout: int = 120) -> str:
    out = subprocess.run(
        ["curl", "-sS", "-f", "--retry", "3", "-A", UA, *args],
        capture_output=True,
        timeout=timeout,
    )
    if out.returncode != 0:
        raise RuntimeError(out.stderr.decode("utf-8", "replace").strip())
    return out.stdout.decode("utf-8")


def fetch_text(url: str) -> str:
    return _curl([url])


def ask_endpoint(question: str) -> dict:
    body = json.dumps({"q": question})
    raw = _curl(
        [
            "-X",
            "POST",
            "-H",
            "Content-Type: application/json",
            "-d",
            body,
            ASK_URL,
        ],
        timeout=60,
    )
    return json.loads(raw)


# ------------------------------------------------------------------ parsing
def _clean(fragment: str) -> str:
    text = re.sub(r"<[^>]+>", " ", fragment)
    text = html_mod.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


_cache: dict = {"loaded_at": 0.0, "shelves": []}


def _parse_areas(home: str) -> dict[int, str]:
    areas: dict[int, str] = {}
    starts = list(re.finditer(r'<div class="browse-area">', home))
    for i, m in enumerate(starts):
        end = starts[i + 1].start() if i + 1 < len(starts) else len(home)
        block = home[m.start() : end]
        hm = re.search(r"<h3>(.*?)</h3>", block, re.DOTALL)
        if not hm:
            continue
        area = re.sub(r"\s*\(\d+\)\s*$", "", _clean(hm.group(1)))
        for n in re.findall(r'href="#shelf(\d+)"', block):
            areas.setdefault(int(n), area)
    return areas


def _parse_shelves(home: str) -> list[dict]:
    areas = _parse_areas(home)
    starts = list(re.finditer(r'<section id="shelf(\d+)"[^>]*>', home))
    shelves = []
    for i, m in enumerate(starts):
        n = int(m.group(1))
        end = starts[i + 1].start() if i + 1 < len(starts) else len(home)
        chunk = home[m.start() : end]

        title = ""
        hm = re.search(r"<h2>(.*?)</h2>", chunk, re.DOTALL)
        if hm:
            htxt = _clean(hm.group(1))
            title = htxt.split("·", 1)[1].strip() if "·" in htxt else htxt

        question = ""
        mesh = ""
        for q in re.findall(r'<p class="topic-q">(.*?)</p>', chunk, re.DOTALL):
            qtxt = _clean(q)
            if qtxt.startswith("MeSH:"):
                mesh = qtxt[len("MeSH:") :].strip()
            elif not question:
                question = re.sub(r"^The question:\s*", "", qtxt)

        balance = ""
        bm = re.search(r'<p class="balance">(.*?)</p>', chunk, re.DOTALL)
        if bm:
            balance = re.sub(
                r"^The balance of the evidence:\s*", "", _clean(bm.group(1))
            )

        cards = []
        for card_html in re.split(r'<article class="card"', chunk)[1:]:
            headline = ""
            ch = re.search(r"<h3>(.*?)</h3>", card_html, re.DOTALL)
            if ch:
                headline = _clean(ch.group(1))
            result = ""
            cr = re.search(
                r'<p class="card-result">(.*?)</p>', card_html, re.DOTALL
            )
            if cr:
                result = _clean(cr.group(1))
            source = ""
            cs = re.search(
                r'<dd class="source">(.*?)</dd>', card_html, re.DOTALL
            )
            if cs:
                source = _clean(cs.group(1))
            if headline:
                cards.append(
                    {"headline": headline, "result": result, "source": source}
                )

        shelves.append(
            {
                "n": n,
                "title": title,
                "question": question,
                "balance": balance,
                "area": areas.get(n),
                "mesh": mesh,
                "cards": cards,
                "url": f"{BASE}/#shelf{n}",
                "citation": f"SourceMed Shelf #{n}, \u201c{title}\u201d, sourcemed.io",
            }
        )
    shelves.sort(key=lambda s: s["n"])
    return shelves


def _shelves() -> list[dict]:
    now = time.time()
    if not _cache["shelves"] or now - _cache["loaded_at"] > CACHE_TTL_SECONDS:
        _cache["shelves"] = _parse_shelves(fetch_text(HOME_URL))
        _cache["loaded_at"] = now
    return _cache["shelves"]


def _summary(shelf: dict) -> dict:
    return {
        "n": shelf["n"],
        "title": shelf["title"],
        "question": shelf["question"],
        "balance": shelf["balance"],
        "area": shelf["area"],
        "card_count": len(shelf["cards"]),
        "url": shelf["url"],
        "citation": shelf["citation"],
    }


# -------------------------------------------------------------------- tools
@mcp.tool()
def ask_sourcemed(question: str) -> dict:
    """Ask SourceMed a clinical-evidence question.

    Calls the library's Ask endpoint, which retrieves only source-verified
    cards. Returns {"covered": true, "summary": ..., "shelves": [...]} with
    full shelf records (title, question, balance line, cards with results
    and sources/PMIDs), or {"covered": false, "shelves": []} when no shelf
    covers the topic. Carry the shelf citations when you use the numbers.
    """
    try:
        return ask_endpoint(question)
    except Exception as exc:
        return {"covered": False, "error": f"Ask endpoint call failed: {exc}"}


@mcp.tool()
def list_shelves() -> dict:
    """List every SourceMed shelf: number, title, browse area, card count."""
    shelves = _shelves()
    return {
        "count": len(shelves),
        "shelves": [
            {
                "n": s["n"],
                "title": s["title"],
                "area": s["area"],
                "card_count": len(s["cards"]),
                "url": s["url"],
            }
            for s in shelves
        ],
    }


@mcp.tool()
def get_shelf(n: int) -> dict:
    """Get one SourceMed shelf in full by its permanent number.

    Returns the shelf's question, balance-of-evidence line, MeSH terms,
    area, citation string, and every card (headline, result, source).
    """
    for s in _shelves():
        if s["n"] == n:
            return s
    return {"error": f"No shelf #{n} in the library."}


@mcp.tool()
def search_shelves(query: str) -> dict:
    """Search shelves by keyword.

    Matches against shelf titles, questions, balance lines, and card
    headlines. Returns up to 20 shelf summaries (number, title, question,
    balance, area, card count, citation).
    """
    terms = [t.lower() for t in re.split(r"\s+", query.strip()) if t]
    if not terms:
        return {"count": 0, "shelves": []}
    hits = []
    for s in _shelves():
        haystack = " ".join(
            [s["title"], s["question"], s["balance"]]
            + [c["headline"] for c in s["cards"]]
        ).lower()
        if all(t in haystack for t in terms):
            hits.append(_summary(s))
    return {"count": len(hits), "shelves": hits[:20]}


if __name__ == "__main__":
    mcp.run()
