"""Vinted deal-finder MCP server (read-only)."""

from __future__ import annotations

import os
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from .vinted import (
    CONDITIONS,
    Listing,
    VintedClient,
    VintedError,
    condition_at_least,
    matches_all_words,
    size_matches,
    text_excludes,
)
from .wishlist import Wishlist, WishlistItem

MAX_PAGES = 3

server = MCPServer(
    name="vinted",
    # WARNING by default so HTTP access logs don't record the secret URL path.
    log_level=os.environ.get("LOG_LEVEL", "WARNING").upper(),
    instructions=(
        "Read-only tools for finding second-hand deals on Vinted against a shopping "
        "list of things the user wants to buy. Prices are what the seller asks; "
        "total_price adds Vinted's buyer protection fee but not postage. "
        "When presenting deals, give the listing title, size, condition, total price, "
        "the saving against the new price, and the link. Check titles yourself: "
        "keyword search returns some off-target items (e.g. a different model)."
    ),
)

_client: VintedClient | None = None
_wishlist = Wishlist()

READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
LOCAL_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)


def client() -> VintedClient:
    global _client
    if _client is None:
        _client = VintedClient(domain=os.environ.get("VINTED_DOMAIN", "www.vinted.co.uk"))
    return _client


def _collect(
    query: str,
    *,
    sizes: list[str] | None,
    min_condition: str | None,
    min_price: float | None,
    max_price: float | None,
    exclude_words: list[str] | None,
    sort: str,
    limit: int,
    pages: int,
    match_all_words: bool = True,
) -> tuple[list[Listing], int]:
    """Search up to `pages` result pages and filter locally. Returns (matches, scanned)."""
    matches: list[Listing] = []
    seen: set[int] = set()
    scanned = 0
    for page in range(1, max(1, min(pages, MAX_PAGES)) + 1):
        batch = client().search(
            query, sort=sort, price_from=min_price, price_to=max_price, page=page
        )
        if not batch:
            break
        for item in batch:
            if item.id in seen:
                continue
            seen.add(item.id)
            scanned += 1
            if not size_matches(item.size, sizes):
                continue
            if not condition_at_least(item.condition, min_condition):
                continue
            if text_excludes(item, exclude_words):
                continue
            if match_all_words and not matches_all_words(item, query):
                continue
            matches.append(item)
        if len(matches) >= limit:
            break
    return matches, scanned


def _deal_rows(listings: list[Listing], new_price: float, limit: int) -> list[dict[str, Any]]:
    rows = []
    for item in listings:
        row = item.to_dict()
        row["saving"] = round(new_price - item.total_price, 2)
        row["saving_pct"] = round(100 * (1 - item.total_price / new_price), 1)
        rows.append(row)
    rows.sort(key=lambda r: r["saving_pct"], reverse=True)
    return rows[:limit]


@server.tool(annotations=READ_ONLY)
def search_vinted(
    query: str,
    sizes: list[str] | None = None,
    min_condition: str | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    exclude_words: list[str] | None = None,
    sort: str = "relevance",
    limit: int = 20,
    pages: int = 1,
    match_all_words: bool = True,
) -> dict[str, Any]:
    """Search Vinted listings.

    Args:
        query: Search text, e.g. "patagonia r1 air".
        sizes: Acceptable sizes, e.g. ["L"] or ["10", "UK 10"]. Matches Vinted's size label.
        min_condition: Lowest acceptable condition: "new with tags", "new without tags",
            "very good", "good" or "satisfactory".
        min_price: Minimum seller price.
        max_price: Maximum seller price.
        exclude_words: Drop listings whose title or brand contains any of these words.
        sort: "relevance", "price_low_to_high", "price_high_to_low" or "newest_first".
        limit: Maximum listings to return.
        pages: Result pages to scan (96 listings each, max 3). More pages help when
            size or condition filters remove most results.
        match_all_words: Keep only listings whose title or brand contains every word of
            the query (default true). Turn off for broad searches.
    """
    try:
        matches, scanned = _collect(
            query, sizes=sizes, min_condition=min_condition, min_price=min_price,
            max_price=max_price, exclude_words=exclude_words, sort=sort,
            limit=limit, pages=pages, match_all_words=match_all_words,
        )
    except (VintedError, ValueError) as exc:
        return {"error": str(exc)}
    return {
        "query": query,
        "scanned": scanned,
        "matched": len(matches),
        "listings": [m.to_dict() for m in matches[:limit]],
    }


@server.tool(annotations=READ_ONLY)
def find_deals(
    query: str,
    new_price: float,
    sizes: list[str] | None = None,
    min_condition: str | None = "good",
    max_fraction_of_new: float = 0.8,
    exclude_words: list[str] | None = None,
    limit: int = 10,
    pages: int = 2,
    match_all_words: bool = True,
) -> dict[str, Any]:
    """Find Vinted listings well below the new retail price, best saving first.

    Args:
        query: Search text, e.g. "icebreaker 200 oasis long sleeve".
        new_price: What the item costs new, in the site's currency.
        sizes: Acceptable sizes, e.g. ["M"].
        min_condition: Lowest acceptable condition (default "good").
        max_fraction_of_new: Only show listings whose price is at most this fraction
            of new_price (default 0.8, i.e. at least 20% off).
        exclude_words: Drop listings whose title or brand contains any of these words.
        limit: Maximum deals to return.
        pages: Result pages to scan (max 3).
        match_all_words: Keep only listings whose title or brand contains every word of
            the query (default true). Turn off for broad searches.
    """
    if new_price <= 0:
        return {"error": "new_price must be positive"}
    try:
        matches, scanned = _collect(
            query, sizes=sizes, min_condition=min_condition, min_price=None,
            max_price=round(new_price * max_fraction_of_new, 2),
            exclude_words=exclude_words, sort="relevance", limit=limit * 3, pages=pages,
            match_all_words=match_all_words,
        )
    except (VintedError, ValueError) as exc:
        return {"error": str(exc)}
    deals = [
        m for m in matches if m.total_price <= new_price * max_fraction_of_new
    ]
    return {
        "query": query,
        "new_price": new_price,
        "scanned": scanned,
        "deals": _deal_rows(deals, new_price, limit),
    }


@server.tool(annotations=READ_ONLY)
def check_wishlist(name: str | None = None, per_item: int = 5) -> dict[str, Any]:
    """Search Vinted for every item on the shopping list (or one item by name)
    and return the best deals for each against its new price.

    Args:
        name: Only check this list item. Omit to check the whole list.
        per_item: Maximum deals to return per list item.
    """
    items = _wishlist.load()
    if name:
        items = [i for i in items if i.slug == WishlistItem(name, "", 0).slug]
        if not items:
            return {"error": f"No list item called '{name}'."}
    if not items:
        return {"error": "The shopping list is empty. Add items with add_to_wishlist."}

    results = []
    for item in items:
        cap = item.max_price if item.max_price is not None else item.new_price * 0.8
        try:
            matches, scanned = _collect(
                item.query, sizes=item.sizes or None, min_condition=item.min_condition,
                min_price=None, max_price=cap, exclude_words=item.exclude_words or None,
                sort="relevance", limit=per_item * 3, pages=2,
                match_all_words=item.match_all_words,
            )
        except (VintedError, ValueError) as exc:
            results.append({"name": item.name, "error": str(exc)})
            continue
        results.append(
            {
                "name": item.name,
                "new_price": item.new_price,
                "sizes": item.sizes,
                "min_condition": item.min_condition,
                "notes": item.notes,
                "scanned": scanned,
                "deals": _deal_rows(
                    [m for m in matches if m.total_price <= cap], item.new_price, per_item
                ),
            }
        )
    return {"results": results}


@server.tool(annotations=READ_ONLY)
def get_wishlist() -> dict[str, Any]:
    """Show the shopping list: what the user wants, new prices, sizes and conditions."""
    return {
        "path": str(_wishlist.path),
        "items": [vars(i) for i in _wishlist.load()],
    }


@server.tool(annotations=LOCAL_WRITE)
def add_to_wishlist(
    name: str,
    query: str,
    new_price: float,
    sizes: list[str] | None = None,
    min_condition: str | None = "good",
    max_price: float | None = None,
    exclude_words: list[str] | None = None,
    notes: str = "",
    match_all_words: bool = True,
) -> dict[str, Any]:
    """Add an item to the shopping list, or update it if the name already exists.
    This only changes a local file; nothing is sent to Vinted.

    Args:
        name: Short label, e.g. "Patagonia R1 Air fleece".
        query: Vinted search text, e.g. "patagonia r1 air".
        new_price: What it costs new.
        sizes: Acceptable sizes, e.g. ["L"] or ["10", "10.5"].
        min_condition: Lowest acceptable condition.
        max_price: Most you would pay. Defaults to 80% of new_price.
        exclude_words: Words that mark wrong listings, e.g. ["women", "kids"].
        notes: Anything else worth remembering.
        match_all_words: Require every query word in the listing title or brand.
    """
    if min_condition and min_condition.lower() not in CONDITIONS:
        return {"error": f"min_condition must be one of: {', '.join(CONDITIONS)}"}
    item = WishlistItem(
        name=name, query=query, new_price=new_price, sizes=sizes or [],
        min_condition=min_condition, max_price=max_price,
        exclude_words=exclude_words or [], notes=notes,
        match_all_words=match_all_words,
    )
    replaced = _wishlist.upsert(item)
    return {"status": "updated" if replaced else "added", "item": vars(item)}


@server.tool(annotations=LOCAL_WRITE)
def remove_from_wishlist(name: str) -> dict[str, Any]:
    """Remove an item from the shopping list by name."""
    return {"removed": _wishlist.remove(name)}


def main() -> None:
    """Run over stdio (default) or HTTP when VINTED_MCP_TRANSPORT=http.

    HTTP mode serves the MCP endpoint at /mcp/<VINTED_MCP_SECRET>. The secret path is
    the only protection, so it is required and must be long and random.
    """
    if os.environ.get("VINTED_MCP_TRANSPORT", "stdio").lower() != "http":
        server.run("stdio")
        return

    secret = os.environ.get("VINTED_MCP_SECRET", "")
    if len(secret) < 24 or not secret.replace("-", "").replace("_", "").isalnum():
        raise SystemExit(
            "VINTED_MCP_SECRET must be at least 24 letters, digits, '-' or '_'. "
            "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
        )
    server.run(
        "streamable-http",
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8000")),
        streamable_http_path=f"/mcp/{secret}",
        stateless_http=True,
        json_response=True,
    )


if __name__ == "__main__":
    main()
