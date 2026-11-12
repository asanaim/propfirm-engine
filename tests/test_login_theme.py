"""The pipridge sign-in theme replaces every Keycloak mark.

Keycloak's login template asks for `img/favicon.ico` from the theme; a theme
without one falls back to the parent's, so the browser tab showed the
Keycloak icon although the page showed the pipridge logo.
"""

from pathlib import Path

IMG = Path(__file__).resolve().parents[1] / "config/keycloak/themes/pipridge/login/resources/img"


def test_the_theme_has_its_own_tab_icon() -> None:
    data = (IMG / "favicon.ico").read_bytes()
    assert data[:4] == b"\x00\x00\x01\x00"  # an ICO file
    count = int.from_bytes(data[4:6], "little")
    sizes = {data[6 + 16 * i] for i in range(count)}  # width byte per image
    assert {16, 32, 48} <= sizes


def test_the_theme_has_its_own_logos() -> None:
    for name in ("logo.svg", "logo-on-dark.svg", "favicon.svg"):
        assert (IMG / name).read_text(encoding="utf-8").lstrip().startswith("<svg")
