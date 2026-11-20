"""Identity clusters (D-14.27): connected sets of linked customers, the
same however the links arrive."""

from propfirm.customers.clusters import clusters_of, pair


def test_linked_customers_form_one_cluster_named_by_its_lowest_cif() -> None:
    links = [("CIF3", "CIF5"), ("CIF5", "CIF9"), ("CIF2", "CIF4")]
    assert clusters_of(links) == {
        "CIF3": "CIF3",
        "CIF5": "CIF3",
        "CIF9": "CIF3",
        "CIF2": "CIF2",
        "CIF4": "CIF2",
    }
    # the order of the links does not matter
    assert clusters_of(reversed(links)) == clusters_of(links)
    # a bridge joins two clusters under the lower id
    assert set(clusters_of([*links, ("CIF4", "CIF9")]).values()) == {"CIF2"}
    assert clusters_of([]) == {}


def test_a_pair_is_kept_in_one_order() -> None:
    assert pair("CIF9", "CIF1") == pair("CIF1", "CIF9") == ("CIF1", "CIF9")


def test_the_reserve_counts_a_cluster_as_one_trader() -> None:
    from propfirm.customers.liability import Claim, largest_trader

    claims = [
        Claim("ACC1", "CIF1", "lite", 0, 0, 30_000),
        Claim("ACC2", "CIF2", "lite", 0, 0, 25_000),
        Claim("ACC3", "CIF3", "instant", 0, 0, 40_000),
    ]
    assert largest_trader(claims, {}) == 40_000
    assert largest_trader(claims, {"CIF1": "CIF1", "CIF2": "CIF1"}) == 55_000
    assert largest_trader([], {}) == 0
