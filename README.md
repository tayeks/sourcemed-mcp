# SourceMed MCP server

An MCP (Model Context Protocol) server for [SourceMed](https://sourcemed.io/)
— a source-verified medical evidence library built for agents. Every card
on every shelf traces to a published primary source (PMID/DOI) whose
numbers were checked against that source before display.

The server uses only the site's **public endpoints and files**. No
account, API key, or credentials are needed.

## Tools

| Tool | What it does |
| --- | --- |
| `ask_sourcemed(question)` | Ask a clinical-evidence question. Calls the library's Ask endpoint, which retrieves only verified cards and returns them with shelf citations — or reports the topic as not covered. |
| `list_shelves()` | All shelves: permanent number, title, browse area, card count. |
| `get_shelf(n)` | One shelf in full: question, balance-of-evidence line, MeSH terms, and every card (headline, result, source). |
| `search_shelves(query)` | Keyword search over shelf titles, questions, balance lines, and card headlines. |

## Install and run

Requires Python 3.10+ and `curl` on PATH (see "Implementation notes").

```bash
cd sourcemed/mcp-server
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt   # the official mcp SDK, v1 line
.venv/bin/python server.py                  # stdio server; speaks MCP on stdin/stdout
```

### Connect it to an MCP client

Generic stdio client configuration:

```json
{
  "mcpServers": {
    "sourcemed": {
      "command": "/path/to/sourcemed/mcp-server/.venv/bin/python",
      "args": ["/path/to/sourcemed/mcp-server/server.py"]
    }
  }
}
```

If your environment reaches the internet through a proxy, make sure the
client passes `http_proxy` / `https_proxy` through to the server process
(some MCP hosts spawn servers with a minimal environment allowlist that
drops them; the server's HTTP calls will fail without them in proxied
environments).

## The Ask endpoint (discovered interface)

Documented in the site's `llms.txt` and verified live on 2026-11-03:

- **Request:** `POST https://sourcemed.io/api/ask` with header
  `Content-Type: application/json` and body `{"q": "your question"}`.
  GET with a query parameter returns **404** — POST is the only format.
  The question must be a real question: a missing/too-short `q` returns
  HTTP 400 with `{"covered": false, "error": "question too short"}`.
- **Response (covered):** HTTP 200,
  `{"covered": true, "summary": null, "shelves": [ ... ]}` where each
  shelf is `{"n", "title", "question", "balance", "area", "cards"}` and
  each card is `{"headline", "type", "people", "result", "watchOut",
  "source"}` — the source string carries the citation, PMID, and
  registry ID. `summary` was `null` in all observed responses (the
  shelves/cards are the answer; the endpoint retrieves, it does not
  generate prose).
- **Response (not covered):** HTTP 200,
  `{"covered": false, "summary": null, "shelves": []}`.

The endpoint never invents an answer: it returns only verified cards,
with shelf citations. Carry the citation (`SourceMed Shelf #NN,
sourcemed.io`) when you use a card's numbers.

## Data sources

- **Ask** — the endpoint above.
- **Shelf index** (`list_shelves` / `get_shelf` / `search_shelves`) —
  parsed from the homepage's public shelf sections
  (`https://sourcemed.io/`), cached in memory for one hour. Note:
  `llms.txt` documents the library, the deposit rules, and the Ask
  endpoint, and carries the site's definitions in full — but it does
  not itself contain a shelf list, so the shelf tools read the same
  public shelf sections the site serves to every visitor.

## Implementation notes

HTTP is performed with `curl` rather than Python's HTTP stack: behind
some egress proxies, Python's stack receives truncated streams from the
site's worker (IncompleteRead at a fixed byte count), while curl
receives the full body reliably. The server needs no other system
dependencies.

## Testing

```bash
.venv/bin/python test_client.py
```

Spawns the server over stdio, initializes a session, lists tools, and
calls each tool once. Latest run (2026-11-03): **all checks PASS** —
`ask_sourcemed` returned 3 shelves for a statin-after-heart-attack
question; `list_shelves` returned 245 shelves; `get_shelf(106)` returned
the aspirin primary-prevention shelf with its 3 cards;
`search_shelves("aspirin primary prevention")` found shelf #106.
Full output: `test-output.txt` / `test-output.json`.

## Registry files

- `server.json` — manifest in the official MCP registry schema
  (`io.sourcemed/sourcemed`, v1.0.0). Prepared for submission; **not yet
  published** to any registry, and the PyPI package name
  (`sourcemed-mcp`) is the intended identifier, not a live package.
- `smithery.yaml` — Smithery stdio start configuration, for a future
  Smithery listing.

## Terms

Content is offered for reuse with attribution (CC BY): name SourceMed
and the shelf number. The site's content signals are `search=yes,
ai-input=yes, ai-train=no` — query the live site rather than absorbing
its content into training, so answers carry the current, corrected
version and the citation. The verification standard, not the license,
is the quality guarantee.
