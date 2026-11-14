"""Products and account sizes on sale (review D4, owner decision 12 Nov 2026).

Owners or admins switch a product or a size off or on again; a second
admin or owner approves (rule AC00), as for the site mode. The latest
approved change replaces the policy file's `[catalogue]` lists. Switching
off stops new sales only: accounts already bought keep their product, size
and rules. Add-ons (D-13.18) are switched the same way (`addons_off`);
an account keeps the add-ons it was bought with.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from propfirm.policy import PolicyError

OFFERING = "offering"


@dataclass(frozen=True, slots=True)
class Offering:
    products_off: frozenset[str]
    sizes_off: frozenset[int]  # cents
    addons_off: frozenset[str] = field(default_factory=frozenset)  # D-13.18

    def __post_init__(self) -> None:
        if not isinstance(self.products_off, frozenset) or not all(
            isinstance(p, str) and p for p in self.products_off
        ):
            raise PolicyError("products_off is a set of product ids")
        if not isinstance(self.sizes_off, frozenset) or not all(
            isinstance(s, int) and not isinstance(s, bool) and s > 0 for s in self.sizes_off
        ):
            raise PolicyError("sizes_off is a set of sizes in cents")
        if not isinstance(self.addons_off, frozenset) or not all(
            isinstance(a, str) and a for a in self.addons_off
        ):
            raise PolicyError("addons_off is a set of add-on ids")


def offering_json(o: Offering) -> dict[str, Any]:
    out: dict[str, Any] = {
        "products_off": sorted(o.products_off),
        "sizes_off": sorted(o.sizes_off),
    }
    if o.addons_off:  # left out while empty: changes stored before add-ons read the same
        out["addons_off"] = sorted(o.addons_off)
    return out


def offering_from_json(value: Mapping[str, Any]) -> Offering:
    products, sizes = value["products_off"], value["sizes_off"]
    addons = value.get("addons_off", [])
    if not all(isinstance(v, list) for v in (products, sizes, addons)):
        raise PolicyError("products_off, sizes_off and addons_off are lists")
    if any(len(set(v)) != len(v) for v in (products, sizes, addons)):
        raise PolicyError("a product, size or add-on is listed twice")
    return Offering(frozenset(products), frozenset(sizes), frozenset(addons))
