"""The pipridge sign-in themes for Keycloak (config/keycloak/themes)."""

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
THEMES = ROOT / "config" / "keycloak" / "themes"
REALM = ROOT / "config" / "keycloak" / "propfirm-realm.json"
BUILT_IN = {"keycloak.v2", "base"}  # inside Keycloak's own themes jar


def props(theme: str) -> dict[str, str]:
    lines = (THEMES / theme / "login" / "theme.properties").read_text().splitlines()
    pairs = (line.split("=", 1) for line in lines if line and not line.startswith("#"))
    return {k.strip(): v.strip() for k, v in pairs}


def resource(theme: str, path: str) -> Path | None:
    """The file a theme serves at `path`, from itself or a parent of ours."""
    while theme not in BUILT_IN:
        found = THEMES / theme / "login" / "resources" / path
        if found.is_file():
            return found
        theme = props(theme)["parent"]
    return None


def test_the_realm_uses_the_trader_theme_and_the_back_office_the_staff_theme() -> None:
    realm = json.loads(REALM.read_text())
    assert realm["loginTheme"] == "pipridge"
    clients = {c["clientId"]: c for c in realm["clients"]}
    assert clients["backoffice"]["attributes"]["login_theme"] == "pipridge-staff"
    assert "login_theme" not in clients["portal"]["attributes"]


@pytest.mark.parametrize("theme", ["pipridge", "pipridge-staff"])
def test_each_theme_extends_keycloak_v2_and_serves_what_it_lists(theme: str) -> None:
    chain, name = [], theme
    while name not in BUILT_IN:
        chain.append(name)
        name = props(name)["parent"]
    assert name == "keycloak.v2"
    p = props(theme)
    styles = p["styles"].split()
    assert styles[0] == "css/styles.css"  # keycloak.v2's own sheet first
    for path in styles[1:] + p["scripts"].split():
        assert resource(theme, path) is not None, path


def test_every_file_a_stylesheet_names_exists() -> None:
    for css in THEMES.glob("*/login/resources/css/*.css"):
        for ref in re.findall(r"url\(\"?([^\")]+)\"?\)", css.read_text()):
            assert (css.parent / ref).resolve().is_file(), f"{css.name}: {ref}"


def test_the_terrain_respects_reduced_motion_and_the_font_is_open_licence() -> None:
    script = (THEMES / "pipridge/login/resources/js/terrain.js").read_text()
    assert "prefers-reduced-motion: reduce" in script
    assert "pp-no-webgl" in script  # CSS ridges without WebGL
    css = (THEMES / "pipridge/login/resources/css/pipridge.css").read_text()
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert (
        "SIL OPEN FONT LICENSE" in (THEMES / "pipridge/login/resources/fonts/OFL.txt").read_text()
    )


def test_compose_mounts_the_themes_and_points_an_existing_realm_at_them() -> None:
    compose = (ROOT / "compose.yaml").read_text()
    assert "./config/keycloak/themes:/opt/keycloak/themes:ro" in compose
    setup = (ROOT / "config/keycloak/setup.sh").read_text()
    assert "loginTheme=pipridge" in setup
    assert "attributes.login_theme=pipridge-staff" in setup
    assert "./config/keycloak/setup.sh:/setup.sh:ro" in compose
    job = compose.split("  keycloak-theme:")[1].split("\n  api:")[0]
    assert 'profiles: ["setup"]' in job  # a one-shot job is never part of `up --wait`


def test_google_sign_in_is_off_without_keys_and_always_asks_for_the_code() -> None:
    setup = (ROOT / "config/keycloak/setup.sh").read_text()
    assert 'if [[ -z "${GOOGLE_CLIENT_ID:-}" || -z "${GOOGLE_CLIENT_SECRET:-}" ]]' in setup
    assert "postBrokerLoginFlowAlias=$FLOW" in setup  # the OTP step after Google
    assert "provider=auth-otp-form" in setup and "REQUIRED" in setup
    compose = (ROOT / "compose.yaml").read_text()
    assert "GOOGLE_CLIENT_SECRET: ${GOOGLE_CLIENT_SECRET:-}" in compose
    staff = (THEMES / "pipridge-staff/login/resources/css/staff.css").read_text()
    assert "#kc-social-providers" in staff  # staff never sign in with Google
