r"""Draw the app icon (tallyho/static/icons/): four tally marks and a slash, white on an indigo tile.

It is the same drawing as the logo in templates/_icons.html. Phones and browsers
want PNG pictures in fixed sizes, plus favicon.ico. Run this again after
changing the design. Like take_screenshots.py, it uses Playwright + Edge
(Playwright isn't in requirements.txt: borrow another project's, or pip install playwright):

    ..\expense_tracker\.venv\Scripts\python docs\make_icons.py
"""
import struct
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).parent.parent
ICONS = ROOT / "tallyho" / "static" / "icons"

TILE = "#4f46e5"   # indigo stands out on light and dark browser tabs alike
MARKS = "#ffffff"
SLASH = "#c7d2fe"

# (file, size, shape). "rounded" has see-through corners, for browser tabs and
# installed apps. "square" fills every pixel: iPhones round the corners
# themselves, and Android crops a "maskable" icon into its own shape.
PNGS = [
    ("icon-32.png", 32, "rounded"),
    ("icon-192.png", 192, "rounded"),
    ("icon-512.png", 512, "rounded"),
    ("icon-maskable-512.png", 512, "square"),
    ("apple-touch-icon.png", 180, "square"),
]
ICO_SIZES = (16, 32, 48)


def page_html(size, shape):
    radius = round(size * 0.25) if shape == "rounded" else 0
    # A maskable icon can be cropped to a circle, so its drawing stays in the middle.
    drawing = size * (0.8 if shape == "square" else 1)
    return f"""<!doctype html>
<style>
  html, body {{ margin: 0; background: transparent; }}
  .tile {{ width: {size}px; height: {size}px; background: {TILE}; border-radius: {radius}px;
           display: flex; align-items: center; justify-content: center; overflow: hidden; }}
</style>
<div class="tile">
  <svg width="{drawing}" height="{drawing}" viewBox="0 0 32 32">
    <path d="M9.5 9v14M14 9v14M18.5 9v14M23 9v14" stroke="{MARKS}" stroke-width="2.4" stroke-linecap="round"/>
    <path d="M6.5 20.5L25.5 11.5" stroke="{SLASH}" stroke-width="2.6" stroke-linecap="round"/>
  </svg>
</div>"""


def render(browser, size, shape):
    page = browser.new_page(viewport={"width": size, "height": size})
    page.set_content(page_html(size, shape))
    png = page.locator(".tile").screenshot(omit_background=True)
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
    ICONS.mkdir(exist_ok=True)
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
