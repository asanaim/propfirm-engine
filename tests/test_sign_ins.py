"""Account sharing (D-14.35): the pure rules."""

from propfirm.customers.sign_ins import SignIn, check, device_hash, distance_km, tenths
from propfirm.policy import load_policy

POLICY = load_policy().sharing
H = 3_600_000


def test_device_hash() -> None:
    assert device_hash("abc", "Firefox") == device_hash("abc", "Chrome")  # the id wins
    assert device_hash(None, "Firefox") != device_hash(None, "Chrome")
    assert device_hash("abc", None) != device_hash(None, "abc")
    assert len(device_hash(None, None)) == 32


def test_tenths_round_half_away_from_zero() -> None:
    assert tenths("23.8103", 90) == 238
    assert tenths("-0.05", 90) == -1 and tenths("0.05", 90) == 1
    assert tenths("0.04", 90) == 0
    assert tenths("90.1", 90) is None and tenths("nan", 90) is None
    assert tenths("x", 180) is None and tenths(None, 180) is None


def test_distance() -> None:
    assert distance_km((238, 904), (238, 904)) == 0
    # Dhaka to London: about 8,000 km
    assert 7_900 < distance_km((238, 904), (515, -1)) < 8_100
    # a tenth of a degree of latitude: about 11 km
    assert 11 < distance_km((0, 0), (1, 0)) < 11.2


def sign_in(at: int, device: str, country: str | None, lat: int | None, lon: int | None) -> SignIn:
    return SignIn(at, device, country, lat, lon)


def test_devices_and_countries() -> None:
    rows = [sign_in(i, f"d{i}", c, None, None) for i, c in enumerate(["BD", "BD", "IN", "AE"])]
    found = {f.rule: f for f in check(rows[-1], rows, POLICY)}
    assert set(found) == {"devices"}  # 4 devices; 3 countries is not more than 3
    more = [*rows, sign_in(9, "d0", "SG", None, None)]
    found = {f.rule: f for f in check(more[-1], more, POLICY)}
    assert found["countries"].detail == {"countries": ["AE", "BD", "IN", "SG"]}
    assert found["countries"].severity == "medium"


def test_impossible_travel() -> None:
    dhaka = sign_in(0, "d0", "BD", 238, 904)
    london = sign_in(H, "d0", "GB", 515, -1)
    (travel,) = check(london, [dhaka, london], POLICY)
    assert (travel.rule, travel.severity) == ("travel", "high")
    assert travel.detail["minutes"] == 60 and travel.detail["countries"] == ["BD", "GB"]
    later = sign_in(H + 1, "d0", "GB", 515, -1)
    assert check(later, [dhaka, later], POLICY) == []  # over an hour
    near = sign_in(60_000, "d0", "BD", 228, 904)  # 111 km
    assert check(near, [dhaka, near], POLICY) == []
    unknown = sign_in(60_000, "d0", "GB", None, None)
    assert check(unknown, [dhaka, unknown], POLICY) == []
