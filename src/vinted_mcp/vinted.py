"""Read-only Vinted search client.

Vinted has no public API. This module loads the public catalogue search page
(the same page a browser loads at /catalog?search_text=...) and reads the
search results embedded in it. It never logs in and never writes anything.

If Vinted changes its page structure, `parse_catalog_html` is the one place
that needs updating.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass
from typing import Any

import httpx

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)

SORT_ORDERS = {
    "relevance": "relevance",
    "price_low_to_high": "price_low_to_high",
    "price_high_to_low": "price_high_to_low",
    "newest_first": "newest_first",
}

# Vinted condition labels, best first. Rank is used for "at least this good" filters.
CONDITIONS = [
    "new with tags",
    "new without tags",
    "very good",
    "good",
    "satisfactory",
]
CONDITION_RANK = {name: len(CONDITIONS) - i for i, name in enumerate(CONDITIONS)}

_FLIGHT_RE = re.compile(r"self\.__next_f\.push\((\[.*?\])\)</script>", re.S)
_ITEMS_KEY = '"items":{"items":'


class VintedError(RuntimeError):
    pass


@dataclass
class Listing:
    id: int
    title: str
    brand: str | None
    size: str | None
    condition: str | None
    price: float
    total_price: float  # price + Vinted buyer protection fee, excluding postage
    currency: str
    url: str
    favourites: int
    seller_id: int | None
    is_business_seller: bool
    photo: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _money(obj: Any) -> float | None:
    if isinstance(obj, dict) and obj.get("amount") not in (None, "$undefined"):
        try:
            return float(obj["amount"])
        except (TypeError, ValueError):
            return None
    return None


def _clean(value: Any) -> Any:
    return None if value == "$undefined" else value


def _split_second_line(line: str | None) -> tuple[str | None, str | None]:
    """itemBox.secondLine is "<size> · <condition>" or just "<condition>"."""
    if not line:
        return None, None
    parts = [p.strip() for p in line.split("·")]
    if len(parts) >= 2:
        return " · ".join(parts[:-1]), parts[-1]
    if parts[0].lower() in CONDITION_RANK:
        return None, parts[0]
    return parts[0], None


def _flight_payload(html: str) -> str:
    out = []
    for chunk in _FLIGHT_RE.findall(html):
        try:
            arr = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        if len(arr) > 1 and isinstance(arr[1], str):
            out.append(arr[1])
    return "".join(out)


def parse_catalog_html(html: str, base_url: str) -> list[Listing]:
    """Extract listings from a Vinted /catalog page."""
    flight = _flight_payload(html)
    idx = flight.find(_ITEMS_KEY)
    if idx == -1:
        if "items" in html and "no results" in html.lower():
            return []
        raise VintedError(
            "Could not find search results in the Vinted page. Vinted may have "
            "changed its page layout, or the request was blocked."
        )
    try:
        raw_items, _ = json.JSONDecoder().raw_decode(flight, idx + len(_ITEMS_KEY))
    except json.JSONDecodeError as exc:
        raise VintedError(f"Could not decode Vinted search results: {exc}") from exc

    listings: list[Listing] = []
    for raw in raw_items:
        p = raw.get("productItem") if isinstance(raw, dict) else None
        if not isinstance(p, dict):
            continue
        price = _money(p.get("priceWithDiscount")) or _money(p.get("price"))
        if price is None:
            continue
        total = _money(p.get("totalItemPrice")) or price
        box = p.get("itemBox") or {}
        size, condition = _split_second_line(_clean(box.get("secondLine")))
        user = p.get("user") or {}
        url = p.get("url") or f"/items/{p.get('id')}"
        listings.append(
            Listing(
                id=int(p["id"]),
                title=p.get("title") or "",
                brand=_clean(box.get("firstLine")),
                size=size,
                condition=condition,
                price=price,
                total_price=total,
                currency=(p.get("price") or {}).get("currencyCode", "GBP"),
                url=url if url.startswith("http") else base_url + url,
                favourites=int(p.get("favouriteCount") or 0),
                seller_id=user.get("id"),
                is_business_seller=bool(user.get("isBusiness")),
                photo=_clean(p.get("thumbnailUrl")),
            )
        )
    return listings


# --- filtering helpers -------------------------------------------------------

_SIZE_ALIASES = {
    "xxs": "xxs", "xs": "xs", "extra small": "xs",
    "s": "s", "small": "s",
    "m": "m", "medium": "m",
    "l": "l", "large": "l",
    "xl": "xl", "extra large": "xl",
    "xxl": "xxl", "2xl": "xxl",
    "xxxl": "xxxl", "3xl": "xxxl",
}


def _size_tokens(size: str) -> set[str]:
    tokens = set()
    for part in re.split(r"[/·,]", size.lower()):
        part = part.strip()
        if not part:
            continue
        part = re.sub(r"^uk\s*", "", part)
        tokens.add(_SIZE_ALIASES.get(part, part))
    return tokens


def size_matches(listing_size: str | None, wanted: list[str] | None) -> bool:
    if not wanted:
        return True
    if not listing_size:
        return False
    have = _size_tokens(listing_size)
    return any(_size_tokens(w) & have for w in wanted)


def condition_at_least(condition: str | None, minimum: str | None) -> bool:
    if not minimum:
        return True
    if not condition:
        return False
    need = CONDITION_RANK.get(minimum.lower())
    if need is None:
        raise ValueError(f"Unknown condition '{minimum}'. Use one of: {', '.join(CONDITIONS)}")
    return CONDITION_RANK.get(condition.lower(), 0) >= need


def _compact(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def matches_all_words(listing: Listing, query: str) -> bool:
    """True if every word of the query appears in the title or brand.

    Punctuation and spacing are ignored, so "r1 air" matches "R1-Air" and "R1Air".
    """
    hay = _compact(f"{listing.title} {listing.brand or ''}")
    words = [_compact(w) for w in query.split()]
    return all(w in hay for w in words if w)


_WOMENS_WORDS = ("women", "womens", "woman", "ladies", "lady", "girls", "girl")


def looks_womens(listing: Listing) -> bool:
    """Heuristic: Vinted shows women's letter sizes with a UK dress size ("M / UK 12-14"),
    and men's as just the letter. Also catches women's words in the title."""
    if listing.size and "/ uk" in listing.size.lower():
        return True
    words = re.findall(r"[a-z]+", listing.title.lower())
    return any(w in _WOMENS_WORDS for w in words)


def text_excludes(listing: Listing, exclude: list[str] | None) -> bool:
    if not exclude:
        return False
    hay = f"{listing.title} {listing.brand or ''}".lower()
    return any(w.lower() in hay for w in exclude)


# --- HTTP client -------------------------------------------------------------


class VintedClient:
    """Polite, cached, read-only client for Vinted's public search page."""

    def __init__(
        self,
        domain: str = "www.vinted.co.uk",
        min_interval: float = 1.5,
        cache_ttl: float = 300.0,
    ) -> None:
        self.base_url = f"https://{domain}"
        self.min_interval = min_interval
        self.cache_ttl = cache_ttl
        self._client = httpx.Client(
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-GB,en;q=0.9",
            },
            follow_redirects=True,
            timeout=30.0,
        )
        self._warmed_up = False
        self._last_request = 0.0
        self._cache: dict[str, tuple[float, list[Listing]]] = {}

    def _get(self, path: str, params: dict[str, Any] | None = None) -> httpx.Response:
        wait = self.min_interval - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        resp = self._client.get(self.base_url + path, params=params)
        self._last_request = time.monotonic()
        return resp

    def _warm_up(self) -> None:
        # The homepage sets the anonymous session cookies the catalogue page expects.
        if not self._warmed_up:
            self._get("/")
            self._warmed_up = True

    def search(
        self,
        query: str,
        *,
        sort: str = "relevance",
        price_from: float | None = None,
        price_to: float | None = None,
        page: int = 1,
    ) -> list[Listing]:
        if sort not in SORT_ORDERS:
            raise ValueError(f"sort must be one of {', '.join(SORT_ORDERS)}")
        params: dict[str, Any] = {"search_text": query, "order": SORT_ORDERS[sort]}
        if price_from is not None:
            params["price_from"] = f"{price_from:g}"
        if price_to is not None:
            params["price_to"] = f"{price_to:g}"
        if page > 1:
            params["page"] = page

        key = json.dumps(params, sort_keys=True)
        hit = self._cache.get(key)
        if hit and time.monotonic() - hit[0] < self.cache_ttl:
            return hit[1]

        self._warm_up()
        resp = self._get("/catalog", params)
        if resp.status_code in (401, 403, 429):
            raise VintedError(
                f"Vinted refused the request (HTTP {resp.status_code}). "
                "Wait a few minutes before searching again."
            )
        resp.raise_for_status()
        listings = parse_catalog_html(resp.text, self.base_url)
        self._cache[key] = (time.monotonic(), listings)
        return listings

    def close(self) -> None:
        self._client.close()
