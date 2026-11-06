"""Masked views for apprentices [staff spec 5c]: they see real work, but
trader names, documents, wallets and contact details are hidden; money and
trading figures stay visible.

`mask_pii` walks any JSON value and replaces the value of every personal
field (by key, at any depth, in lists too) with `MASK`, keeping the shape so
the same screens work. Keys are matched exactly, so ids, amounts and
statuses are never touched. A field that is null stays null (it says
nothing about the person).
"""

from typing import Any

MASK = "•••"

PERSONAL = frozenset(
    {
        "email",
        "author_email",
        "full_name",
        "typed_name",
        "date_of_birth",
        "document_country",
        "document_number",
        "address",  # payout wallets
        "ip",
        "user_agent",
        "phone",
        "verification_url",
        "residence_country",
    }
)


def mask_pii(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: (MASK if k in PERSONAL and v is not None else mask_pii(v)) for k, v in value.items()
        }
    if isinstance(value, list):
        return [mask_pii(v) for v in value]
    return value
