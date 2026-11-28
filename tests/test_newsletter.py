"""Newsletter (D-15.2): the footer every email carries."""

from propfirm.customers.newsletter import footer


def test_the_footer_names_the_firm_and_the_way_out() -> None:
    text = footer(
        "https://x/unsubscribe?token=t",
        {"company_name": "Pipridge Ltd", "address": "1 Road, Dhaka"},
    )
    assert "Unsubscribe: https://x/unsubscribe?token=t" in text
    assert text.rstrip().endswith("Pipridge Ltd, 1 Road, Dhaka")
    bare = footer("u", {})
    assert bare.rstrip().endswith("Unsubscribe: u")
