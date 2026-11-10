"""The logo and the app icons (docs/make_icons.py draws the PNGs from static/icons/logo.svg)."""
import json
import struct
from pathlib import Path

ICONS = Path(__file__).parent.parent / "tallyho" / "static" / "icons"


def png_size(path):
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", path.name
    return struct.unpack(">II", data[16:24])


def test_every_icon_has_its_size():
    for name, size in (("icon-32.png", 32), ("icon-192.png", 192), ("icon-512.png", 512),
                       ("icon-maskable-512.png", 512), ("apple-touch-icon.png", 180)):
        assert png_size(ICONS / name) == (size, size), name
    header = (ICONS / "favicon.ico").read_bytes()[:6]
    assert struct.unpack("<HHH", header) == (0, 1, 3)  # an icon file with 16, 32 and 48 px pictures


def test_the_logo_is_the_svg_file(app, owner):
    visitor = app.test_client()  # logged out (owner is logged in)
    assert visitor.get("/static/icons/logo.svg").status_code == 200
    login = visitor.get("/login").get_data(as_text=True)
    assert 'src="/static/icons/logo.svg"' in login and 'type="image/svg+xml"' in login
    assert 'src="/static/icons/logo.svg"' in owner.get("/").get_data(as_text=True)  # the menu


def test_the_manifest_lists_the_icons(client):
    manifest = json.loads(client.get("/static/site.webmanifest").get_data(as_text=True))
    sources = {icon["src"] for icon in manifest["icons"]}
    assert {"icons/icon-192.png", "icons/icon-512.png", "icons/icon-maskable-512.png", "icons/logo.svg"} <= sources
    assert any(icon["purpose"] == "maskable" for icon in manifest["icons"])
    for icon in manifest["icons"]:
        assert (ICONS.parent / icon["src"]).exists(), icon["src"]
