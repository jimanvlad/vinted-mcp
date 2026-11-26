import asyncio
import json

import pytest

from vinted_mcp import server as srv
from vinted_mcp.vinted import (
    VintedError,
    condition_at_least,
    parse_catalog_html,
    size_matches,
)
from vinted_mcp.wishlist import Wishlist

BASE = "https://www.vinted.co.uk"


def raw_item(id_, title, price, total, brand, second_line, business=False):
    """Shape of one result as embedded in Vinted's catalogue page (Nov 2026)."""
    return {
        "id": id_,
        "productItem": {
            "id": id_,
            "title": title,
            "url": f"/items/{id_}-slug",
            "favouriteCount": 3,
            "priceWithDiscount": None,
            "price": {"amount": f"{price:.2f}", "currencyCode": "GBP"},
            "serviceFee": {"amount": f"{total - price:.2f}", "currencyCode": "GBP"},
            "totalItemPrice": {"amount": f"{total:.2f}", "currencyCode": "GBP"},
            "thumbnailUrl": "https://images1.vinted.net/x.webp",
            "user": {"id": 99, "photo": None, "isBusiness": business},
            "itemBox": {"firstLine": brand, "secondLine": second_line, "badge": "$undefined"},
        },
        "pricingDto": {},
    }


def catalog_html(items):
    """Wrap items the way Next.js streams them: JSON split across several push() chunks."""
    flight = (
        '0:{"filters":[]}\n'
        '1:{"catalogBrand":{"brand":"$undefined"},"items":{"items":'
        + json.dumps(items)
        + ',"uiState":"SUCCESS"}}\n'
    )
    mid = len(flight) // 2
    chunks = [flight[:mid], flight[mid:]]
    scripts = "".join(
        f"<script>self.__next_f.push({json.dumps([1, c])})</script>" for c in chunks
    )
    return f"<html><body>{scripts}</body></html>"


ITEMS = [
    raw_item(1, "Patagonia R1 Air half zip black", 40.00, 42.70, "Patagonia", "L · Very good"),
    raw_item(2, "R1 Air Crew brand new", 58.00, 61.50, "Patagonia", "M · New with tags"),
    raw_item(3, "R1 Air zip neck pullover", 62.00, 65.55, "Patagonia", "S · New with tags"),
    raw_item(4, "Womens R1 Air hoody", 30.00, 32.20, "Patagonia", "L / UK 12-14 · Good"),
    raw_item(5, "R1 Air fleece worn", 20.00, 21.70, "Patagonia", "L · Satisfactory"),
    raw_item(6, "Patagonia sticker", 2.00, 2.80, "Patagonia", "Very good"),
]


def test_parse_catalog_html():
    listings = parse_catalog_html(catalog_html(ITEMS), BASE)
    assert len(listings) == 6
    first = listings[0]
    assert first.title == "Patagonia R1 Air half zip black"
    assert first.price == 40.0 and first.total_price == 42.7
    assert first.size == "L" and first.condition == "Very good"
    assert first.url == f"{BASE}/items/1-slug"
    assert listings[3].size == "L / UK 12-14"
    assert listings[5].size is None and listings[5].condition == "Very good"


def test_parse_raises_when_layout_changes():
    with pytest.raises(VintedError):
        parse_catalog_html("<html>nothing here</html>", BASE)


@pytest.mark.parametrize(
    "listing,wanted,ok",
    [
        ("L", ["L"], True),
        ("L / UK 12-14", ["large"], True),
        ("M", ["L"], False),
        ("10", ["UK 10"], True),
        ("10.5", ["10"], False),
        (None, ["L"], False),
        ("M", None, True),
    ],
)
def test_size_matches(listing, wanted, ok):
    assert size_matches(listing, wanted) is ok


def test_condition_at_least():
    assert condition_at_least("New with tags", "very good")
    assert condition_at_least("Very good", "very good")
    assert not condition_at_least("Good", "very good")
    assert not condition_at_least(None, "good")
    with pytest.raises(ValueError):
        condition_at_least("Good", "mint")


class FakeClient:
    def __init__(self, listings):
        self.listings = listings
        self.calls = []

    def search(self, query, *, sort, price_from, price_to, page):
        self.calls.append(dict(query=query, sort=sort, price_to=price_to, page=page))
        if page > 1:
            return []
        return [l for l in self.listings if price_to is None or l.price <= price_to]


@pytest.fixture
def fake(monkeypatch, tmp_path):
    fake = FakeClient(parse_catalog_html(catalog_html(ITEMS), BASE))
    monkeypatch.setattr(srv, "_client", fake)
    monkeypatch.setattr(srv, "_wishlist", Wishlist(tmp_path / "wishlist.json"))
    return fake


def call(tool, args):
    from mcp import Client

    async def go():
        async with Client(srv.server) as c:
            res = await c.call_tool(tool, args)
            assert not res.is_error, res
            return res.structured_content or json.loads(res.content[0].text)

    return asyncio.run(go())


def test_find_deals_ranks_by_saving_and_filters(fake):
    out = call(
        "find_deals",
        {"query": "patagonia r1 air", "new_price": 84, "sizes": ["L"],
         "min_condition": "good", "exclude_words": ["women"]},
    )
    titles = [d["title"] for d in out["deals"]]
    # Size L, condition >= good, not womens, <= 80% of £84 (seller price £67.20)
    assert titles == ["Patagonia R1 Air half zip black"]
    deal = out["deals"][0]
    assert deal["saving"] == 41.3
    assert deal["saving_pct"] == pytest.approx(49.2)
    assert fake.calls[0]["price_to"] == 67.2


def test_wishlist_roundtrip_and_check(fake):
    added = call("add_to_wishlist", {
        "name": "Patagonia R1 Air fleece", "query": "patagonia r1 air",
        "new_price": 84, "sizes": ["L", "M"], "min_condition": "very good",
    })
    assert added["status"] == "added"
    assert call("get_wishlist", {})["items"][0]["sizes"] == ["L", "M"]

    out = call("check_wishlist", {})
    deals = out["results"][0]["deals"]
    assert [d["id"] for d in deals] == [1, 2]  # best saving first; S, Good and Satisfactory dropped

    assert call("remove_from_wishlist", {"name": "patagonia r1 air FLEECE"})["removed"] is True
    assert "empty" in call("check_wishlist", {})["error"]


def test_bad_condition_is_reported(fake):
    out = call("add_to_wishlist", {"name": "x", "query": "x", "new_price": 10, "min_condition": "mint"})
    assert "min_condition" in out["error"]


def test_match_all_words_drops_off_target_listings(fake):
    fake.listings.append(
        parse_catalog_html(catalog_html([raw_item(
            7, "Gorpcore grid fleece", 9.0, 10.42, "Other", "L · Very good")]), BASE)[0]
    )
    out = call("find_deals", {"query": "patagonia r1-air", "new_price": 84, "sizes": ["L"]})
    assert 7 not in [d["id"] for d in out["deals"]]
    out = call("find_deals", {"query": "patagonia r1-air", "new_price": 84, "sizes": ["L"],
                              "match_all_words": False})
    assert out["deals"][0]["id"] == 7
