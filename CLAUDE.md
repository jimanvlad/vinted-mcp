# Vinted deal finder

This repo finds second-hand deals on Vinted for the owner's shopping list. It is
read-only: it loads Vinted's public search page and never logs in or contacts sellers.

## When asked to check the Vinted list / find deals

Run the CLI, don't write new scraping code:

```bash
uv run vinted check                 # whole shopping list
uv run vinted check "Snow boots"    # one item
uv run vinted deals "patagonia r1 air" --new-price 84 --size L --min-condition "very good" --mens
uv run vinted search "icebreaker oasis" --size M --max-price 30 --sort price_low_to_high
uv run vinted list
```

- The shopping list is `wishlist.json` in the repo root. To add, change or remove items,
  edit that file (fields: name, query, new_price, sizes, min_condition, max_price,
  exclude_words, notes, match_all_words, mens_only), then commit and push it so the next session sees it.
- `total_price` includes Vinted's buyer protection fee but not postage (~£2.50–£5).
- Check titles yourself before recommending: keyword search can include a different model.
- Present each deal as: title, size, condition, total price, saving vs new, link.
- Run one check at a time; the client already spaces requests out. If Vinted returns
  403/429, report it and stop. Do not try to get around the block (proxies, header or
  TLS spoofing, retry loops).
- If asked whether something is really a deal, web-search the current lowest new price too.

## Development

`uv run pytest`. Parsing of Vinted's page lives in `parse_catalog_html` in
`src/vinted_mcp/vinted.py`; that is what breaks if Vinted changes its site.
