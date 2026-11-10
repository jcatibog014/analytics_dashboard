"""Made-up sales for "Kape Corner", a small Philippine coffee business with
cafes and an online shop. Used for the sample CSV, "Load sample data", the
demo, and the 1-million-row benchmark (flask make-sample).

The numbers follow patterns real shops have, so the charts have something
to show: more orders on weekends and on paydays (the 15th and the last day),
a December rush, a slow rainy season, slow growth, a busy morning, more online
orders as time goes on, and customers who come back.

Everything comes from one seeded random generator, so the same settings
always give the same sales (tests depend on it). All names are made up.
"""
import calendar
import csv
import io
import random
from datetime import date, timedelta

from .importer import ParsedLine, ParsedOrder

BUSINESS = "Kape Corner"

# (sku, name, category, price, cost) in centavos
PRODUCTS = [
    ("BN-HSE-250", "House Blend Beans 250g", "Coffee Beans", 32000, 15000),
    ("BN-BRK-250", "Barako Roast Beans 250g", "Coffee Beans", 35000, 16500),
    ("BN-BEN-250", "Benguet Arabica Beans 250g", "Coffee Beans", 42000, 21000),
    ("BN-SAG-250", "Sagada Peaberry Beans 250g", "Coffee Beans", 48000, 25000),
    ("BN-DCF-250", "Decaf Blend Beans 250g", "Coffee Beans", 36000, 18000),
    ("BN-ESP-1KG", "Espresso Roast Beans 1kg", "Coffee Beans", 115000, 56000),
    ("DR-SPL-ICE", "Iced Spanish Latte", "Drinks", 16500, 5500),
    ("DR-AMR-HOT", "Hot Americano", "Drinks", 11000, 3000),
    ("DR-CMC-ICE", "Iced Caramel Macchiato", "Drinks", 17500, 6000),
    ("DR-CBR-BTL", "Cold Brew Bottle", "Drinks", 15000, 5000),
    ("DR-MAT-LAT", "Matcha Latte", "Drinks", 17000, 6200),
    ("DR-CHO-HOT", "Hot Tablea Chocolate", "Drinks", 13000, 4000),
    ("PS-ENS-001", "Ensaymada", "Pastries", 7500, 2800),
    ("PS-UBE-6PC", "Ube Cheese Pandesal (6 pcs)", "Pastries", 12000, 4500),
    ("PS-BAN-SLC", "Banana Bread Slice", "Pastries", 9500, 3500),
    ("PS-CRO-001", "Butter Croissant", "Pastries", 11000, 4200),
    ("PS-CIN-001", "Cinnamon Roll", "Pastries", 10500, 3800),
    ("SN-MNG-200", "Dried Mango 200g", "Snacks", 18000, 9500),
    ("SN-CAS-100", "Cassava Chips", "Snacks", 9000, 4000),
    ("SN-CHI-100", "Chicharon", "Snacks", 8500, 3800),
    ("SN-GRN-BAR", "Granola Bar", "Snacks", 6500, 2800),
    ("GR-FPR-350", "French Press 350ml", "Brewing Gear", 129000, 69000),
    ("GR-DRP-V60", "Pour-over Dripper", "Brewing Gear", 89000, 42000),
    ("GR-GRD-HND", "Hand Grinder", "Brewing Gear", 185000, 99000),
    ("GR-FLT-100", "Paper Filters (100 pcs)", "Brewing Gear", 22000, 9000),
    ("GR-MOK-3CP", "Moka Pot 3-cup", "Brewing Gear", 149000, 78000),
    ("MR-TMB-500", "Kape Corner Tumbler", "Merchandise", 69000, 28000),
    ("MR-MUG-001", "Kape Corner Mug", "Merchandise", 39000, 15000),
    ("MR-TOT-001", "Canvas Tote Bag", "Merchandise", 35000, 12000),
    ("MR-SHT-001", "Kape Corner Shirt", "Merchandise", 59000, 24000),
]

REGIONS = [("Metro Manila", 0.42), ("Calabarzon", 0.18), ("Central Luzon", 0.14), ("Cebu", 0.15), ("Davao", 0.11)]
CHANNELS = ["Store", "Website", "Marketplace", "Phone Order"]

# What people buy where: (category, weight)
STORE_MIX = [("Drinks", 0.52), ("Pastries", 0.28), ("Snacks", 0.08), ("Coffee Beans", 0.08), ("Merchandise", 0.04)]
ONLINE_MIX = [("Coffee Beans", 0.42), ("Brewing Gear", 0.16), ("Snacks", 0.16), ("Merchandise", 0.14),
              ("Drinks", 0.12)]

FIRST_NAMES = ["Maria", "Jose", "Ana", "Juan", "Carmela", "Paolo", "Bea", "Miguel", "Lea", "Rafael", "Joy", "Mark",
               "Kristine", "Carlo", "Angela", "Nico", "Patricia", "Gabriel", "Trisha", "Enzo", "Bianca", "Luis",
               "Camille", "Diego", "Sofia", "Andres", "Isabel", "Ramon", "Nina", "Tomas"]
LAST_NAMES = ["Santos", "Reyes", "Cruz", "Bautista", "Ocampo", "Garcia", "Mendoza", "Torres", "Villanueva",
              "Ramos", "Aquino", "Castillo", "Flores", "Navarro", "Del Rosario", "Gonzales", "Lim", "Tan",
              "Dela Cruz", "Salazar", "Morales", "Pascual", "Domingo", "Fernandez", "Soriano"]

CSV_HEADERS = ["Order No", "Line", "Date", "SKU", "Product", "Category", "Quantity", "Unit Price", "Unit Cost",
               "Discount", "Customer ID", "Customer Name", "Region", "Channel"]


def _weighted(rng, pairs):
    total = sum(w for _, w in pairs)
    pick = rng.random() * total
    for value, weight in pairs:
        pick -= weight
        if pick <= 0:
            return value
    return pairs[-1][0]


def _day_factor(day, start, end):
    """How busy a day is compared with a plain weekday."""
    factor = {0: 0.86, 1: 0.9, 2: 0.95, 3: 1.0, 4: 1.12, 5: 1.38, 6: 1.26}[day.weekday()]
    factor *= {1: 0.92, 2: 0.9, 3: 1.0, 4: 1.02, 5: 1.0, 6: 0.94, 7: 0.9, 8: 0.92, 9: 0.98, 10: 1.02,
               11: 1.12, 12: 1.45}[day.month]
    last = calendar.monthrange(day.year, day.month)[1]
    if day.day in (15, last) or day.day in (16, 1):
        factor *= 1.22 if day.day in (15, last) else 1.08  # payday
    if (day.month, day.day) in ((12, 24), (12, 31)):
        factor *= 0.7  # everyone is home
    span = max((end - start).days, 1)
    factor *= 0.85 + 0.35 * (day - start).days / span  # slow growth
    return factor


def _hour(rng, channel):
    if channel == "Store":
        return _weighted(rng, [(7, 4), (8, 9), (9, 10), (10, 8), (11, 6), (12, 7), (13, 6), (14, 6), (15, 8),
                               (16, 7), (17, 6), (18, 5), (19, 4), (20, 3)])
    return _weighted(rng, [(h, w) for h, w in zip(range(7, 24), (1, 2, 4, 5, 5, 6, 6, 5, 5, 5, 5, 6, 7, 8, 8, 6, 3))])


def generate(start, end, orders_per_day=24, seed=7, prefix="KC"):
    """ParsedOrders from start to end (both included), in date order."""
    rng = random.Random(seed)
    by_category = {}
    for sku, name, category, price, cost in PRODUCTS:
        by_category.setdefault(category, []).append((sku, name, category, price, cost))
    customers = []  # (code, name, region)
    number = 100000
    day = start
    while day <= end:
        expected = orders_per_day * _day_factor(day, start, end)
        count = max(0, int(rng.gauss(expected, expected ** 0.5)))
        progress = (day - start).days / max((end - start).days, 1)
        channel_weights = [("Store", 0.58 - 0.12 * progress), ("Website", 0.17 + 0.08 * progress),
                           ("Marketplace", 0.18 + 0.04 * progress), ("Phone Order", 0.07)]
        for _ in range(count):
            number += 1
            channel = _weighted(rng, channel_weights)
            # About 6 in 10 orders come from a customer who bought before. Older customers
            # come back a bit more often (random() ** 1.6 leans towards the start of the list).
            if customers and rng.random() < 0.6:
                code, name, region = customers[int(len(customers) * rng.random() ** 1.6)]
            else:
                region = _weighted(rng, REGIONS)
                code = f"C-{len(customers) + 1:05d}"
                name = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"
                customers.append((code, name, region))
            mix = STORE_MIX if channel == "Store" else ONLINE_MIX
            line_count = _weighted(rng, [(1, 0.42), (2, 0.33), (3, 0.17), (4, 0.08)])
            lines, used = [], set()
            for line_no in range(1, line_count + 1):
                options = by_category[_weighted(rng, mix)]
                sku, name_, category, price, cost = rng.choice(options)
                if sku in used:
                    continue
                used.add(sku)
                quantity = 1 if price > 60000 else _weighted(rng, [(1, 0.62), (2, 0.26), (3, 0.08), (4, 0.04)])
                discount = 0
                if channel == "Marketplace" and rng.random() < 0.35:
                    discount = quantity * price * rng.choice((5, 10, 15)) // 100  # voucher
                elif rng.random() < 0.06:
                    discount = quantity * price * 10 // 100  # loyalty card
                lines.append(ParsedLine(len(lines) + 1, sku, name_, category, quantity, price, cost, discount))
            yield ParsedOrder(f"{prefix}-{number}", day, _hour(rng, channel), code, name, region, channel, lines)
        day += timedelta(days=1)


def to_csv(orders):
    """The orders as CSV text, in the layout a till or online shop might export."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(CSV_HEADERS)
    for order in orders:
        for line in order.lines:
            minute = int(order.order_no.rsplit("-", 1)[-1]) * 7 % 60 if order.order_no[-1].isdigit() else 0
            when = f"{order.order_date.isoformat()} {order.hour:02d}:{minute:02d}" \
                if order.hour is not None else order.order_date.isoformat()
            writer.writerow([order.order_no, line.line_no, when, line.sku, line.product, line.category,
                             line.quantity, f"{line.unit_price / 100:.2f}", f"{line.unit_cost / 100:.2f}",
                             f"{line.discount / 100:.2f}" if line.discount else "", order.customer_code,
                             order.customer_name, order.region, order.channel])
    return out.getvalue()


def load(s, workspace, user, start, end, orders_per_day=24, seed=7, filename=None, prefix="KC"):
    """Put sample sales straight into a workspace, as one import (so Undo works).
    The generator feeds the Writer a batch at a time, so even a million lines fit in memory."""
    import time

    from .importer import BATCH_SIZE, Writer, new_record
    started = time.perf_counter()
    record = new_record(s, workspace, user, filename or f"Sample data ({BUSINESS})")
    writer = Writer(s, workspace, record)
    batch = []
    for order in generate(start, end, orders_per_day, seed, prefix):
        batch.append(order)
        record.rows_read += len(order.lines)
        if len(batch) >= BATCH_SIZE:
            writer.write(batch)
            batch = []
    writer.write(batch)
    writer.finish()
    record.seconds = round(time.perf_counter() - started, 3)
    return record


def small_csv(today):
    """The downloadable example: the last 60 days, about 1,400 orders."""
    return to_csv(generate(today - timedelta(days=59), today, orders_per_day=22, seed=11))
