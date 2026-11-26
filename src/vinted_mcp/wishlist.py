"""Shopping list stored as a local JSON file."""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path


def default_path() -> Path:
    env = os.environ.get("VINTED_WISHLIST_PATH")
    return Path(env).expanduser() if env else Path.home() / ".vinted-mcp" / "wishlist.json"


@dataclass
class WishlistItem:
    name: str
    query: str
    new_price: float
    sizes: list[str] = field(default_factory=list)
    min_condition: str | None = "good"
    max_price: float | None = None
    exclude_words: list[str] = field(default_factory=list)
    notes: str = ""
    match_all_words: bool = True

    @property
    def slug(self) -> str:
        return re.sub(r"[^a-z0-9]+", "-", self.name.lower()).strip("-")


class Wishlist:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_path()

    def load(self) -> list[WishlistItem]:
        if not self.path.exists():
            return []
        data = json.loads(self.path.read_text(encoding="utf-8"))
        return [WishlistItem(**row) for row in data.get("items", [])]

    def save(self, items: list[WishlistItem]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"items": [asdict(i) for i in items]}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(self.path)

    def upsert(self, item: WishlistItem) -> bool:
        """Add or replace by name. Returns True if it replaced an existing entry."""
        items = self.load()
        for i, existing in enumerate(items):
            if existing.slug == item.slug:
                items[i] = item
                self.save(items)
                return True
        items.append(item)
        self.save(items)
        return False

    def remove(self, name: str) -> bool:
        items = self.load()
        slug = WishlistItem(name=name, query="", new_price=0).slug
        kept = [i for i in items if i.slug != slug]
        if len(kept) == len(items):
            return False
        self.save(kept)
        return True
