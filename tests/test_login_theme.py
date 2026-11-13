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


def test_both_themes_name_their_own_tab_icons() -> None:
    """Keycloak 26.8 renders `favicons.<id>` entries. They point at files
    under new names, so no browser serves a Keycloak icon it cached for the
    old URL (the tab still showed it after pull request 75)."""
    themes = IMG.parents[2].parent
    for theme in ("pipridge", "pipridge-staff"):
        props = (themes / theme / "login/theme.properties").read_text(encoding="utf-8")
        icons = dict(
            line.split("=", 1)
            for line in props.splitlines()
            if line.startswith("favicons.") and "=" in line
        )
        assert set(icons) == {"favicons.svg", "favicons.ico"}, theme
        for path in icons.values():
            assert path.startswith("img/") and path != "img/favicon.ico"
            assert (IMG / path.removeprefix("img/")).is_file(), path
    assert (IMG / "pipridge-tab.ico").read_bytes() == (IMG / "favicon.ico").read_bytes()
