r"""Draw the app icons (tallyho/static/icons/) from the logo, tallyho/static/icons/logo.svg.

The logo is the source: change it, then run this again. Phones and browsers want
PNG pictures in fixed sizes, plus favicon.ico. Like take_screenshots.py, it uses
Playwright + Edge (Playwright isn't in requirements.txt: borrow another
project's, or pip install playwright):

    ..\expense_tracker\.venv\Scripts\python docs\make_icons.py
"""
import re
import struct
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).parent.parent
ICONS = ROOT / "tallyho" / "static" / "icons"
LOGO = ICONS / "logo.svg"

# (file, size, shape).
#   "rounded": the logo as it is, with see-through corners (browser tabs, installed apps).
#   "square": every pixel filled (iPhones round the corners themselves).
#   "maskable": filled, with the drawing kept inside the middle 80%, because Android
#               crops a maskable icon into its own shape (a circle, a squircle...).
PNGS = [
    ("icon-32.png", 32, "rounded"),
    ("icon-192.png", 192, "rounded"),
    ("icon-512.png", 512, "rounded"),
    ("icon-maskable-512.png", 512, "maskable"),
    ("apple-touch-icon.png", 180, "square"),
]
ICO_SIZES = (16, 32, 48)
TILE = re.compile(r'<rect width="400" height="400" rx="92" fill="url\(#bg\)"/>')
HIGHLIGHT = re.compile(r'<ellipse cx="200" cy="110"[^>]*/>')


def logo_svg(size, shape):
    """The logo's SVG text for one size and shape."""
    svg = LOGO.read_text(encoding="utf-8")
    svg = svg.replace('width="1024" height="1024"', f'width="{size}" height="{size}"')
    if shape == "rounded":
        return svg
    square = '<rect width="400" height="400" fill="url(#bg)"/>'
    if shape == "square":
        return TILE.sub(square, svg)
    # maskable: the full background and its soft highlight, then the drawing 80% as big, in the middle.
    # (The highlight stays full size: shrunk, its edge would show as an oval inside the tile.)
    highlight = HIGHLIGHT.search(svg).group(0)
    body = HIGHLIGHT.sub("", svg[TILE.search(svg).end():svg.rindex("</svg>")], count=1)
    return (svg[:TILE.search(svg).start()] + square + highlight + '<g transform="translate(40 40) scale(0.8)">'
            + body + "</g></svg>")


def render(browser, size, shape):
    page = browser.new_page(viewport={"width": size, "height": size})
    page.set_content(f"""<!doctype html><style>html, body {{ margin: 0; background: transparent; }}
        svg {{ display: block; }}</style>{logo_svg(size, shape)}""")
    png = page.locator("svg").screenshot(omit_background=True)
    page.close()
    return png


def write_ico(path, pictures):
    """favicon.ico is a small header, a list, then the PNG files themselves."""
    header = struct.pack("<HHH", 0, 1, len(pictures))
    offset = 6 + 16 * len(pictures)
    entries, data = b"", b""
    for size, png in pictures:
        entries += struct.pack("<BBBBHHII", size, size, 0, 0, 1, 32, len(png), offset + len(data))
        data += png
    path.write_bytes(header + entries + data)


def main():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge")
        for name, size, shape in PNGS:
            (ICONS / name).write_bytes(render(browser, size, shape))
            print("saved", name)
        write_ico(ICONS / "favicon.ico", [(size, render(browser, size, "rounded")) for size in ICO_SIZES])
        print("saved favicon.ico")
        browser.close()


if __name__ == "__main__":
    main()
