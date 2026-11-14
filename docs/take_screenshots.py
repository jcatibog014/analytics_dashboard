r"""Take the README screenshots (docs/screenshots/*.png).

Retake them in every pull request that visibly changes a page, and add a new
one when a page or feature is added: the README shows everything the app can
do. tests/test_readme.py fails if the pictures and the README disagree.

Every picture uses "Try the demo", so only the made-up Kape Corner shows.
It uses Microsoft Edge, which Windows already has, through Playwright.
Playwright is only a tool for this script, so it is not in requirements.txt
(borrow expense_tracker's .venv, which has it):

    $env:DATABASE_URL = "sqlite:///C:/Temp/tallyho-screens.sqlite"
    .venv\Scripts\flask --app tallyho upgrade-db
    .venv\Scripts\flask --app tallyho run --port 5011      (keep this one running)
    ..\expense_tracker\.venv\Scripts\python docs\take_screenshots.py      (in another terminal)

Every picture is one screen (no full-page shots), so they are all the same size,
except the ones marked "full", which show a whole long page.
"""
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5011"
OUT = Path(__file__).parent / "screenshots"
DESKTOP = {"width": 1440, "height": 900}
PHONE = {"width": 390, "height": 844}

# (file name, address, theme, screen size, what to do first). {...} addresses are found on the page.
PAGES = [
    ("login.png", "/login", "light", DESKTOP, "logged-out"),
    ("dashboard.png", "/", "light", DESKTOP, "hover-chart"),
    ("dashboard-dark.png", "/?range=12m&compare=year", "dark", DESKTOP, ""),
    ("dashboard-full.png", "/", "light", DESKTOP, "full"),
    ("dashboard-filtered.png", "{region:Metro Manila}", "light", DESKTOP, ""),
    ("phone-dashboard.png", "/", "light", PHONE, ""),
    ("phone-top-products.png", "/", "light", PHONE, "top-products"),
    ("products.png", "/products?range=90d", "light", DESKTOP, ""),
    ("category.png", "{category:Coffee Beans}", "light", DESKTOP, ""),
    ("product.png", "{product}", "light", DESKTOP, "hover-chart"),
    ("customers.png", "/customers?range=90d", "light", DESKTOP, "full"),
    ("customer.png", "{customer}", "light", DESKTOP, ""),
    ("reports.png", "/reports", "light", DESKTOP, ""),
    ("report-builder.png", "/reports/build?rows=category&columns=quarter&measure=revenue&range=12m", "light",
     DESKTOP, ""),
    ("cohorts.png", "/reports/cohorts", "light", DESKTOP, ""),
    ("pareto.png", "/reports/pareto?range=90d", "light", DESKTOP, ""),
    ("goals.png", "/goals", "light", DESKTOP, "full"),
    ("alerts.png", "/alerts", "light", DESKTOP, ""),
    ("import.png", "/import", "light", DESKTOP, ""),
    ("match-columns.png", "{upload}", "light", DESKTOP, "full"),
    ("import-review.png", "{review}", "light", DESKTOP, "full"),
    ("import-problems.png", "{problem-import}", "light", DESKTOP, ""),
    ("records.png", "/records", "light", DESKTOP, ""),
    ("phone-records.png", "/records?q=latte", "light", PHONE, ""),
    ("order.png", "/orders/WEB-9001", "light", DESKTOP, ""),
    ("settings-developer.png", "/settings", "light", DESKTOP, "developer-on"),
    ("lab.png", "/lab?range=90d", "light", DESKTOP, "full"),
    ("settings.png", "/settings", "light", DESKTOP, ""),
    ("phone-menu.png", "/", "light", PHONE, "open-menu"),
]

SAMPLE = """Receipt #,Sold On,Item Code,Item,Qty,Price,Cost,Customer,Branch,Via
R-5001,2026-11-02 09:15,BN-HSE-250,House Blend Beans 250g,2,320,150,C-00007,Metro Manila,Store
R-5001,2026-11-02 09:15,PS-ENS-001,Ensaymada,1,75,28,C-00007,Metro Manila,Store
R-5002,2026-11-02 14:00,MR-MUG-001,Kape Corner Mug,1,390,150,C-00010,Cebu,Website
"""


def address(page, wanted):
    """Find addresses that depend on the data: a product's page, a filter's link..."""
    if not wanted.startswith("{"):
        return wanted
    key = wanted.strip("{}")
    if key.startswith("region:") or key.startswith("category:"):
        page.goto(BASE + ("/" if key.startswith("region:") else "/products"))
        return page.get_by_role("link", name=key.split(":", 1)[1], exact=True).first.get_attribute("href")
    if key == "product":
        page.goto(BASE + "/products")
        return page.locator("a[href^='/products/']").first.get_attribute("href")
    if key == "customer":
        page.goto(BASE + "/customers")
        return page.locator("a[href^='/customers/']").first.get_attribute("href").split("?")[0] + "?range=all"
    if key == "problem-import":
        page.goto(BASE + "/import")
        return page.get_by_role("link", name=re.compile("Website orders")).get_attribute("href")
    if key == "upload":
        page.goto(BASE + "/import")
        sample = OUT / "november-sales.csv"
        sample.write_text(SAMPLE, encoding="utf-8")
        page.set_input_files("input[type=file]", str(sample))
        page.get_by_role("button", name="Upload & map columns").click()
        page.wait_for_load_state("networkidle")
        sample.unlink()
        return page.url.replace(BASE, "")
    if key == "review":
        # Still on the Map Columns page from the picture before: press Review.
        page.get_by_role("button", name=re.compile("^Review")).click()
        page.wait_for_load_state("networkidle")
        return None
    raise ValueError(wanted)


def main():
    OUT.mkdir(exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge")
        context = browser.new_context(viewport=DESKTOP)
        page = context.new_page()
        page.goto(BASE + "/login")
        page.get_by_role("button", name="Try the demo").click()
        page.wait_for_load_state("networkidle")
        for name, wanted, theme, size, action in PAGES:
            page.set_viewport_size(size)
            if action == "logged-out":
                fresh = browser.new_page(viewport=size)
                fresh.goto(BASE + wanted)
                fresh.screenshot(path=OUT / name)
                fresh.close()
                print("saved", name)
                continue
            target = address(page, wanted)
            page.emulate_media(color_scheme=theme)
            if target is not None:  # None: the page is already open (the import's Review step)
                page.goto(BASE + target if target.startswith("/") else target)
                page.wait_for_load_state("networkidle")
            if action == "developer-on":
                # Switch on Developer mode (it saves by itself), so the Performance Lab shows.
                page.locator(".switch", has_text="Performance Lab").click()
                page.wait_for_load_state("networkidle")
            if action == "hover-chart":
                box = page.locator(".only-wide .chart-svg").first.bounding_box()
                page.mouse.move(box["x"] + box["width"] * 0.72, box["y"] + box["height"] * 0.45)
            if action == "top-products":
                page.locator("h2", has_text="Top Products").evaluate("h => window.scrollTo(0, h.getBoundingClientRect().top + window.scrollY - 24)")
            if action == "open-menu":
                page.get_by_role("button", name="Menu").click()
            page.screenshot(path=OUT / name, full_page=action == "full")
            print("saved", name)
        browser.close()


if __name__ == "__main__":
    main()
