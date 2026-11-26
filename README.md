# Vinted deal finder (MCP server)

A read-only MCP server that lets Claude search Vinted for second-hand versions of
things on your shopping list, and rank them by how much you save against the new price.

It never logs in, never contacts sellers and never changes anything on Vinted. It loads
the same public search page your browser does, spaces requests at least 1.5 s apart, and
caches results for 5 minutes.

## Tools

| Tool | What it does |
| --- | --- |
| `check_wishlist` | Searches every shopping-list item and returns the best deals for each |
| `find_deals` | One-off: search text + new price → listings ranked by saving |
| `search_vinted` | Plain search with size, condition, price and word filters |
| `get_wishlist` | Shows the shopping list |
| `add_to_wishlist` / `remove_from_wishlist` | Edits the shopping list (a local JSON file only) |

Filters:

- **sizes**: matched against Vinted's size label. `"L"` matches `L` and `L / UK 16-18`;
  `"UK 10"` and `"10"` are the same. Give several, e.g. `["M", "L"]`.
- **min_condition**: one of `new with tags`, `new without tags`, `very good`, `good`, `satisfactory`.
- **exclude_words**: drops listings whose title or brand contains them. `["women", "kids"]`
  is useful because Vinted's size labels don't say men's or women's.
- **match_all_words** (on by default): keeps only listings whose title or brand contains
  every search word, because Vinted's relevance search lets loosely related items through.

`total_price` is the seller's price plus Vinted's buyer protection fee. Postage is not
included, so expect roughly £2.50–£5 on top.

## Without MCP (command line)

The same search works as a plain command, which is handy in any Claude Code session
opened on this repo (including Claude Code in the Claude mobile app). `CLAUDE.md` tells
Claude how to use it, and the shopping list is `wishlist.json` in the repo root.

```bash
uv run vinted check
uv run vinted deals "patagonia r1 air" --new-price 84 --size L --min-condition "very good" --mens
uv run vinted list
```

## Install

Needs [uv](https://docs.astral.sh/uv/) (or Python 3.10+ and pip).

```bash
cd vinted-mcp
uv sync
```

Start your shopping list from the example (or just ask Claude to add items):

```bash
mkdir -p ~/.vinted-mcp && cp examples/wishlist.json ~/.vinted-mcp/wishlist.json
```

### Claude Code

```bash
claude mcp add vinted -- uv --directory /absolute/path/to/vinted-mcp run vinted-mcp
```

### Claude Desktop

Add to `claude_desktop_config.json` (Settings → Developer → Edit Config), then restart:

```json
{
  "mcpServers": {
    "vinted": {
      "command": "uv",
      "args": ["--directory", "/absolute/path/to/vinted-mcp", "run", "vinted-mcp"]
    }
  }
}
```

### Settings (environment variables)

| Variable | Default | |
| --- | --- | --- |
| `VINTED_WISHLIST_PATH` | `~/.vinted-mcp/wishlist.json` | Where the shopping list lives |
| `VINTED_DOMAIN` | `www.vinted.co.uk` | Use e.g. `www.vinted.fr` for another country |

### Remote URL (Claude on your phone)

Run in HTTP mode with a long random secret. The secret becomes part of the URL and is the
only thing protecting it, so treat the whole URL like a password.

```bash
export VINTED_MCP_TRANSPORT=http PORT=8000
export VINTED_MCP_SECRET=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
uv run vinted-mcp
```

The endpoint is `https://<your-host>/mcp/<VINTED_MCP_SECRET>`. Expose it over HTTPS with one of:

- **Tailscale Funnel** (free, stable URL, runs on a computer at home that stays on):
  `tailscale funnel 8000` → `https://<machine>.<tailnet>.ts.net/mcp/<secret>`
- **Fly.io** (cheap always-on hosting): use the `Dockerfile`, set `VINTED_MCP_SECRET` as a
  secret, and mount a volume at `/data` so the shopping list survives restarts.

Then in claude.ai: **Settings → Connectors → Add custom connector**, paste the URL, and it
appears in the Claude mobile app too. On Team/Enterprise plans an owner may need to add it.

## Try it

> Check my Vinted shopping list for deals.

> Find a Patagonia R1 Air in large, very good or better. It's £84 new.

> Add Icebreaker 200 Oasis crewe, size M, £56.99 new, max £35, to my list.

## Caveats

- Vinted has no public API. This reads data embedded in the public search page, so it will
  break if Vinted changes that page. The parsing lives in `parse_catalog_html` in
  `src/vinted_mcp/vinted.py`. `pytest` checks it against a fixture, but only a live search
  tells you whether Vinted has changed.
- Automated access may be against Vinted's terms. Keep it to occasional personal searches.
  If Vinted starts returning 403 or 429, the tools report it. Wait before trying again.
- Search scans at most 3 pages (96 listings each) per query.

## Development

```bash
uv sync --extra dev
uv run pytest
```
