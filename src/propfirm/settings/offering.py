"""Products and account sizes on sale (review D4, owner decision 12 Nov 2026).

Owners or admins switch a product or a size off or on again; a second
admin or owner approves (rule AC00), as for the site mode. The latest
approved change replaces the policy file's `[catalogue]` lists. Switching
off stops new sales only: accounts already bought keep their product, size
and rules.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from propfirm.policy import PolicyError

OFFERING = "offering"


@dataclass(frozen=True, slots=True)
class Offering:
    products_off: frozenset[str]
    sizes_off: frozenset[int]  # cents

    def __post_init__(self) -> None:
        if not isinstance(self.products_off, frozenset) or not all(
            isinstance(p, str) and p for p in self.products_off
        ):
            raise PolicyError("products_off is a set of product ids")
        if not isinstance(self.sizes_off, frozenset) or not all(
            isinstance(s, int) and not isinstance(s, bool) and s > 0 for s in self.sizes_off
        ):
            raise PolicyError("sizes_off is a set of sizes in cents")


def offering_json(o: Offering) -> dict[str, Any]:
    return {"products_off": sorted(o.products_off), "sizes_off": sorted(o.sizes_off)}


def offering_from_json(value: Mapping[str, Any]) -> Offering:
    products, sizes = value["products_off"], value["sizes_off"]
    if not isinstance(products, list) or not isinstance(sizes, list):
        raise PolicyError("products_off and sizes_off are lists")
    if len(set(products)) != len(products) or len(set(sizes)) != len(sizes):
        raise PolicyError("a product or size is listed twice")
    return Offering(frozenset(products), frozenset(sizes))
