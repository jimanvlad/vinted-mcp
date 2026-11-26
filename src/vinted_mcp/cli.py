"""Command-line access to the same tools, for use without MCP.

    uv run vinted check                      # every item on the shopping list
    uv run vinted check "Snow boots"         # one list item
    uv run vinted deals "patagonia r1 air" --new-price 84 --size L --min-condition "very good" --exclude women
    uv run vinted search "icebreaker oasis" --size M --max-price 30 --sort price_low_to_high
    uv run vinted list

The shopping list is ./wishlist.json (override with --wishlist or VINTED_WISHLIST_PATH).
Add --json for raw output.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _fmt_deal(d: dict) -> str:
    saving = f" | save £{d['saving']:.2f} ({d['saving_pct']:.0f}%)" if "saving" in d else ""
    size = d.get("size") or "no size"
    cond = d.get("condition") or "?"
    return (
        f"  £{d['total_price']:.2f} (seller £{d['price']:.2f}){saving}\n"
        f"    {d['title']} | {d.get('brand') or ''} | {size} | {cond}\n"
        f"    {d['url']}"
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="vinted", description="Find Vinted deals (read-only).")
    p.add_argument("--wishlist", help="Shopping list JSON (default ./wishlist.json)")
    p.add_argument("--json", action="store_true", help="Print raw JSON")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="Find deals for the shopping list")
    c.add_argument("name", nargs="?", help="Only this list item")
    c.add_argument("--per-item", type=int, default=5)

    sub.add_parser("list", help="Show the shopping list")

    for name in ("deals", "search"):
        s = sub.add_parser(name)
        s.add_argument("query")
        s.add_argument("--size", action="append", dest="sizes", help="Repeatable, e.g. --size M --size L")
        s.add_argument("--min-condition")
        s.add_argument("--exclude", action="append", dest="exclude_words")
        s.add_argument("--limit", type=int, default=10)
        s.add_argument("--pages", type=int, default=2)
        s.add_argument("--loose", action="store_true", help="Don't require every query word in the title")
        s.add_argument("--mens", action="store_true", help="Drop women's listings")
        if name == "deals":
            s.add_argument("--new-price", type=float, required=True)
            s.add_argument("--max-fraction", type=float, default=0.8)
        else:
            s.add_argument("--max-price", type=float)
            s.add_argument("--min-price", type=float)
            s.add_argument("--sort", default="relevance")

    args = p.parse_args(argv)

    wl = args.wishlist or os.environ.get("VINTED_WISHLIST_PATH") or "wishlist.json"
    os.environ["VINTED_WISHLIST_PATH"] = str(Path(wl).expanduser().resolve())

    from . import server as srv  # imported after the wishlist path is set
    from .wishlist import Wishlist

    srv._wishlist = Wishlist()

    if args.cmd == "list":
        out = srv.get_wishlist()
    elif args.cmd == "check":
        out = srv.check_wishlist(name=args.name, per_item=args.per_item)
    elif args.cmd == "deals":
        out = srv.find_deals(
            args.query, args.new_price, sizes=args.sizes, min_condition=args.min_condition or "good",
            max_fraction_of_new=args.max_fraction, exclude_words=args.exclude_words,
            limit=args.limit, pages=args.pages, match_all_words=not args.loose,
            mens_only=args.mens,
        )
    else:
        out = srv.search_vinted(
            args.query, sizes=args.sizes, min_condition=args.min_condition,
            min_price=args.min_price, max_price=args.max_price,
            exclude_words=args.exclude_words, sort=args.sort, limit=args.limit,
            pages=args.pages, match_all_words=not args.loose, mens_only=args.mens,
        )

    if args.json:
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 1 if "error" in out else 0

    if "error" in out:
        print(f"Error: {out['error']}", file=sys.stderr)
        return 1
    if args.cmd == "list":
        print(f"Shopping list ({out['path']}):")
        for i in out["items"]:
            print(f"- {i['name']}: '{i['query']}', £{i['new_price']} new, sizes {i['sizes'] or 'any'}, "
                  f"min condition {i['min_condition']}, max £{i['max_price'] or round(i['new_price'] * 0.8, 2)}")
    elif args.cmd == "check":
        for r in out["results"]:
            print(f"\n{r['name']} (£{r.get('new_price')} new, sizes {r.get('sizes') or 'any'}, "
                  f"scanned {r.get('scanned', 0)})")
            if "error" in r:
                print(f"  Error: {r['error']}")
            elif not r["deals"]:
                print("  No deals right now.")
            for d in r.get("deals", []):
                print(_fmt_deal(d))
    else:
        rows = out.get("deals") or out.get("listings") or []
        print(f"{len(rows)} result(s), scanned {out.get('scanned', 0)}")
        for d in rows:
            print(_fmt_deal(d))
    return 0


if __name__ == "__main__":
    sys.exit(main())
