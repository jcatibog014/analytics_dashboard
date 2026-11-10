"""The numbers: hand-counted KPIs, rollups that always agree with the raw sales,
window functions, date buckets, date ranges and the cache."""
from datetime import date, timedelta

import pytest
from sqlalchemy import text

from tallyho import analytics, cache, db, dialect, importer, ranges, rollups, sample, workspaces

# Three customers over three days. Worked out by hand below.
HAND = """Order,Date,SKU,Product,Category,Qty,Price,Cost,Discount,Customer,Region,Channel
A1,2026-03-01 09:00,BEAN,Beans,Coffee,2,300,100,,C1,North,Store
A1,2026-03-01 09:00,MUG,Mug,Merch,1,400,150,40,C1,North,Store
A2,2026-03-01 15:00,BEAN,Beans,Coffee,1,300,100,,C2,South,Web
A3,2026-03-02 10:00,MUG,Mug,Merch,3,400,150,,C1,North,Web
A4,2026-03-03 11:00,BEAN,Beans,Coffee,5,300,100,150,C3,South,Store
A4,2026-03-03 11:00,TEA,Tea,Coffee,1,200,50,,C3,South,Store
OLD,2026-02-10,BEAN,Beans,Coffee,1,300,100,,C2,South,Web
"""


def load(app, content, ws_id=None):
    """Import CSV text straight through the importer (no web page)."""
    with app.app_context():
        s = db.session()
        workspace = s.get(workspaces.Workspace, ws_id) if ws_id else workspaces.main_workspace(s)
        headers, rows = _table(content)
        result = importer.parse_file(headers, rows, importer.auto_match(headers), "ymd")
        record = importer.run_import(s, workspace, None, "test.csv", result)
        s.commit()
        return record.id


def _table(content):
    import csv
    import io
    reader = csv.reader(io.StringIO(content))
    headers = next(reader)
    return headers, [(n, row) for n, row in enumerate(reader, start=2) if row]


def filters(app, first, last, **extra):
    with app.app_context():
        workspace = workspaces.main_workspace(db.session())
        return analytics.Filters(workspace.id, workspace.data_version, first, last, **extra)


MARCH = (date(2026, 3, 1), date(2026, 3, 3))


def test_kpis_counted_by_hand(app):
    load(app, HAND)
    with app.app_context():
        s = db.session()
        k = analytics.kpis(s, filters(app, *MARCH))
    # Revenue: A1 600 + 400 - 40 = 960, A2 300, A3 1200, A4 1500 - 150 + 200 = 1550 -> 4010 pesos
    assert k["revenue"] == 401000
    assert k["orders"] == 4
    assert k["units"] == 2 + 1 + 1 + 3 + 5 + 1
    assert k["cost"] == (200 + 150 + 100 + 450 + 500 + 50) * 100
    assert k["profit"] == 401000 - 145000
    assert k["aov"] == round(401000 / 4)
    assert k["discount"] == 19000
    assert k["customers"] == 3
    # C2's first order was in February, so only C1 and C3 are new in March.
    assert k["new_customers"] == 2
    assert k["returning_customers"] == 1
    assert k["repeat_rate"] == pytest.approx(1 / 3)
    assert k["source"] == "orders"


def test_filters_by_region_channel_category_product_and_customer(app):
    load(app, HAND)
    with app.app_context():
        s = db.session()
        names = {name: id_ for id_, name in s.execute(text("SELECT id, name FROM regions"))}
        names.update({name: id_ for id_, name in s.execute(text("SELECT id, name FROM channels"))})
        names.update({name: id_ for id_, name in s.execute(text("SELECT id, name FROM categories"))})
        products = {sku: id_ for id_, sku in s.execute(text("SELECT id, sku FROM products"))}
        customers = {code: id_ for id_, code in s.execute(text("SELECT id, code FROM customers"))}
        north = analytics.kpis(s, filters(app, *MARCH, region_id=names["North"]))
        assert (north["orders"], north["revenue"]) == (2, 96000 + 120000)
        web = analytics.kpis(s, filters(app, *MARCH, channel_id=names["Web"]))
        assert (web["orders"], web["revenue"]) == (2, 30000 + 120000)
        coffee = analytics.kpis(s, filters(app, *MARCH, category_id=names["Coffee"]))
        # Lines: A1 beans 600, A2 300, A4 beans 1350 + tea 200. Orders with coffee: A1, A2, A4.
        assert (coffee["orders"], coffee["revenue"], coffee["source"]) == (3, 245000, "raw")
        beans = analytics.kpis(s, filters(app, *MARCH, product_id=products["BEAN"]))
        assert (beans["orders"], beans["revenue"], beans["source"]) == (3, 225000, "products")
        c1 = analytics.kpis(s, filters(app, *MARCH, customer_id=customers["C1"]))
        assert (c1["orders"], c1["revenue"], c1["customers"], c1["source"]) == (2, 216000, 1, "raw")


def test_plan_picks_a_source_that_can_answer():
    f = analytics.Filters(1, 1, date(2026, 1, 1), date(2026, 1, 31))
    assert analytics.plan(f) == "orders"
    assert analytics.plan(f, "raw") == "raw"
    from dataclasses import replace
    assert analytics.plan(replace(f, product_id=3)) == "products"
    assert analytics.plan(replace(f, category_id=3)) == "raw"
    with pytest.raises(ValueError):
        analytics.plan(replace(f, category_id=3), "rollup")


@pytest.fixture
def sampled(app):
    """90 days of sample sales in the main workspace."""
    with app.app_context():
        s = db.session()
        workspace = workspaces.main_workspace(s)
        sample.load(s, workspace, None, date(2026, 1, 1), date(2026, 3, 31), orders_per_day=12)
        s.commit()
    return app


def test_rollups_and_raw_sales_always_agree(sampled):
    app = sampled
    with app.app_context():
        s = db.session()
        region = s.execute(text("SELECT id FROM regions ORDER BY id LIMIT 1")).scalar()
        channel = s.execute(text("SELECT id FROM channels ORDER BY id DESC LIMIT 1")).scalar()
        product = s.execute(text("SELECT id FROM products ORDER BY id LIMIT 1")).scalar()
        for extra in ({}, {"region_id": region}, {"channel_id": channel}, {"region_id": region, "channel_id": channel},
                      {"product_id": product}):
            f = filters(app, date(2026, 1, 10), date(2026, 3, 20), **extra)
            raw, rolled = analytics.kpis(s, f, source="raw"), analytics.kpis(s, f, source="rollup")
            assert {k: v for k, v in raw.items() if k != "source"} == \
                   {k: v for k, v in rolled.items() if k != "source"}, extra
            for grain in ("day", "week", "month"):
                assert strip(analytics.trend(s, f, grain, source="raw"), extra) == \
                       strip(analytics.trend(s, f, grain, source="rollup"), extra)
            for dimension in ("region", "channel", "product", "category"):
                raw_rows = analytics.breakdown(s, f, dimension, source="raw")
                rolled_rows = analytics.breakdown(s, f, dimension, source="rollup")
                keep = ("id", "revenue", "cost", "units", "place") + (("orders",) if dimension != "category" else ())
                assert [{k: r[k] for k in keep} for r in raw_rows] == [{k: r[k] for k in keep} for r in rolled_rows]


def strip(rows, extra):
    """Trend rows without new customers when a product is chosen (the product rollup can't say)."""
    if "product_id" in extra:
        return [{k: v for k, v in r.items() if k != "new_customers"} for r in rows]
    return rows


def test_undo_takes_the_numbers_out_of_the_rollups(app):
    load(app, HAND)
    record_id = load(app, "Order,Date,SKU,Qty,Price\nZ1,2026-03-02,BEAN,10,300\n")
    with app.app_context():
        s = db.session()
        assert analytics.kpis(s, filters(app, *MARCH))["orders"] == 5
        workspace = workspaces.main_workspace(s)
        importer.undo_import(s, workspace, s.get(importer.Import, record_id))
        s.commit()
        assert analytics.kpis(s, filters(app, *MARCH))["orders"] == 4
        assert s.execute(text("SELECT SUM(orders) FROM daily_order_stats")).scalar() == 5  # with February's


def test_rebuild_all_gives_the_same_rollups(sampled):
    with sampled.app_context():
        s = db.session()
        before = s.execute(text("SELECT * FROM daily_order_stats ORDER BY day, region_id, channel_id")).all()
        rollups.rebuild_all(s, workspaces.main_workspace(s).id)
        after = s.execute(text("SELECT * FROM daily_order_stats ORDER BY day, region_id, channel_id")).all()
        assert before == after and len(before) > 100


def test_daily_trend_fills_gaps_and_has_a_7_day_average(app):
    load(app, HAND)
    with app.app_context():
        s = db.session()
        rows = analytics.trend(s, filters(app, date(2026, 2, 25), date(2026, 3, 5)), "day")
    assert [r["start"] for r in rows] == [date(2026, 2, 25) + timedelta(days=n) for n in range(9)]
    by_day = {r["start"]: r for r in rows}
    assert by_day[date(2026, 2, 28)]["revenue"] == 0
    assert by_day[date(2026, 3, 1)]["orders"] == 2
    # The average on Mar 3 covers Feb 25 - Mar 3: 960 + 300 + 1200 + 1550 pesos over 7 days.
    assert by_day[date(2026, 3, 3)]["avg7"]["revenue"] == pytest.approx(401000 / 7)
    assert analytics.moving_average(by_day[date(2026, 3, 3)], "aov") == pytest.approx(401000 / 4)


def test_breakdown_shares_and_places(app):
    load(app, HAND)
    with app.app_context():
        s = db.session()
        rows = analytics.breakdown(s, filters(app, *MARCH), "region")
        assert [r["name"] for r in rows] == ["North", "South"]
        assert rows[0]["revenue"] == 96000 + 120000
        assert sum(r["share"] for r in rows) == pytest.approx(1)
        assert [r["place"] for r in rows] == [1, 2]
        top = analytics.breakdown(s, filters(app, *MARCH), "product", limit=1)
        assert len(top) == 1 and top[0]["sku"] == "BEAN"
        # The share is of all products, not only the one LIMIT kept.
        assert top[0]["share"] == pytest.approx(225000 / 401000)
        by_profit = analytics.breakdown(s, filters(app, *MARCH), "product", measure="profit")
        assert by_profit[0]["profit"] >= by_profit[-1]["profit"]


def test_top_with_rest():
    rows = [{"id": n, "name": str(n), "revenue": 10 - n, "cost": 0, "units": 1, "profit": 10 - n, "share": 0.1,
             "orders": 1} for n in range(10)]
    out = analytics.top_with_rest(rows, keep=3)
    assert len(out) == 4 and out[-1]["is_rest"] and out[-1]["revenue"] == sum(10 - n for n in range(3, 10))


def test_kpi_cards_say_which_way_is_good():
    now = {"revenue": 120, "orders": 10, "discount_rate": 0.1, "margin": 0.3}
    before = {"revenue": 100, "orders": 10, "discount_rate": 0.05, "margin": 0.35}
    cards = {c["key"]: c for c in analytics.kpi_cards(now, before)}
    assert cards["revenue"]["change"] == pytest.approx(0.2) and cards["revenue"]["good"] is True
    assert cards["orders"]["good"] is None  # no change
    assert cards["discount_rate"]["change"] == pytest.approx(0.05) and cards["discount_rate"]["good"] is False
    assert "margin" not in cards  # it sits under Gross Profit, not in a tile of its own


def test_sql_buckets_match_python(app):
    days = [date(2024, 2, 29), date(2026, 1, 1), date(2026, 3, 29), date(2026, 3, 30), date(2026, 12, 31),
            date(2027, 1, 3)]
    with app.app_context():
        s = db.session()
        d = dialect.name_of(s)
        for grain in dialect.GRAINS:
            for day in days:
                found = s.execute(text(f"SELECT {dialect.bucket(d, dialect.date_param(d, 'day'), grain)}"),
                                  {"day": day}).scalar()
                assert dialect.as_date(found) == dialect.bucket_start(day, grain), (grain, day)
            weekday = s.execute(text(f"SELECT {dialect.weekday(d, dialect.date_param(d, 'day'))}"),
                                {"day": date(2026, 3, 29)}).scalar()
            assert int(weekday) == 6  # a Sunday
        months = s.execute(text(f"SELECT {dialect.month_number(d, dialect.date_param(d, 'a'))} - "
                                f"{dialect.month_number(d, dialect.date_param(d, 'b'))}"),
                           {"a": date(2026, 3, 5), "b": date(2025, 11, 20)}).scalar()
        assert int(months) == 4


def test_python_buckets():
    assert dialect.buckets(date(2026, 1, 15), date(2026, 4, 2), "month") == \
           [date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1), date(2026, 4, 1)]
    assert dialect.buckets(date(2026, 3, 4), date(2026, 3, 17), "week") == \
           [date(2026, 3, 2), date(2026, 3, 9), date(2026, 3, 16)]
    assert dialect.next_bucket(date(2026, 11, 1), "quarter") == date(2027, 2, 1)


# ---------- Date ranges ----------

NOW = date(2026, 3, 15)


def test_presets():
    assert ranges.preset_period("today", NOW) == ranges.Period(NOW, NOW)
    assert ranges.preset_period("30d", NOW) == ranges.Period(date(2026, 2, 14), NOW)
    assert ranges.preset_period("this_month", NOW) == ranges.Period(date(2026, 3, 1), NOW)
    assert ranges.preset_period("last_month", NOW) == ranges.Period(date(2026, 2, 1), date(2026, 2, 28))
    assert ranges.preset_period("this_quarter", NOW).first == date(2026, 1, 1)
    assert ranges.preset_period("ytd", NOW).first == date(2026, 1, 1)
    assert ranges.preset_period("12m", NOW) == ranges.Period(date(2025, 3, 16), NOW)
    assert ranges.preset_period("all", NOW, (date(2024, 5, 1), date(2026, 3, 1))) == \
           ranges.Period(date(2024, 5, 1), date(2026, 3, 1))


def test_compare_periods():
    period = ranges.Period(date(2026, 3, 1), date(2026, 3, 10))
    assert ranges.compare_period(period, "previous") == ranges.Period(date(2026, 2, 19), date(2026, 2, 28))
    assert ranges.compare_period(period, "year") == ranges.Period(date(2025, 3, 1), date(2025, 3, 10))
    leap = ranges.Period(date(2024, 2, 29), date(2024, 2, 29))
    assert ranges.compare_period(leap, "year") == ranges.Period(date(2023, 2, 28), date(2023, 2, 28))
    assert ranges.compare_period(period, "none") is None


def test_choose_from_the_address():
    choice = ranges.choose({}, now=NOW)
    assert (choice.key, choice.grain, choice.compare_key) == ("30d", "day", "previous")
    custom = ranges.choose({"range": "custom", "from": "2026-03-10", "to": "2025-12-01"}, now=NOW)
    assert custom.period == ranges.Period(date(2025, 12, 1), date(2026, 3, 10))  # swapped
    assert custom.grain == "week"
    assert ranges.choose({"range": "12m"}, now=NOW).grain == "month"
    assert ranges.choose({"range": "nonsense"}, now=NOW).key == "30d"
    assert ranges.choose({"range": "custom", "from": "bad"}, now=NOW).key == "30d"
    assert ranges.choose({"range": "12m", "grain": "day"}, now=NOW).grain == "day"
    assert ranges.choose({"range": "all"}, (date(2020, 1, 1), NOW), now=NOW).grain == "quarter"
    assert ranges.choose({"range": "30d", "compare": "year"}, now=NOW).compare.first == date(2025, 2, 14)
    assert ranges.args_for(custom) == {"range": "custom", "from": "2025-12-01", "to": "2026-03-10"}


# ---------- The cache ----------

def test_answers_are_remembered_until_new_data_arrives(app):
    load(app, HAND)
    with app.app_context():
        s = db.session()
        remembered = cache.current()
        remembered.clear()
        f = filters(app, *MARCH)
        first = analytics.kpis(s, f)
        again = analytics.kpis(s, f)
        assert again is first and remembered.hits == 1
        analytics.kpis(s, f, source="raw")  # the Lab's timings are never remembered
        assert remembered.hits == 1
    load(app, "Order,Date,SKU,Qty,Price\nZ1,2026-03-02,BEAN,10,300\n")
    with app.app_context():
        s = db.session()
        assert analytics.kpis(s, filters(app, *MARCH))["orders"] == 5  # a new data_version, a new answer


def test_bounds_and_choices(app):
    load(app, HAND)
    with app.app_context():
        s = db.session()
        workspace = workspaces.main_workspace(s)
        assert analytics.bounds(s, workspace.id, workspace.data_version) == (date(2026, 2, 10), date(2026, 3, 3))
        options = analytics.choices(s, workspace.id, workspace.data_version)
        assert [name for _id, name in options["regions"]] == ["North", "South"]
