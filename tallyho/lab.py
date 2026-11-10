"""The Performance Lab: the same questions asked in different ways, timed.

Everything here runs real queries against this workspace's data:

- raw tables vs rollups      the same KPI, trend, breakdown from orders/lines and from the daily rollups
- EXPLAIN                    the database's own plan for a query (SQLite: EXPLAIN QUERY PLAN,
                             PostgreSQL: EXPLAIN ANALYZE, which also runs it and says how long each step took)
- without the index          SQLite's NOT INDEXED, or PostgreSQL's enable_indexscan = off for one transaction
- OFFSET vs keyset paging    how deep paging gets slower with OFFSET and doesn't with a keyset

flask benchmark runs the same measurements on a million sample lines (on your
own computer: it is too big for Neon's free plan) and writes docs/BENCHMARKS.md.
"""
import json
import time
from pathlib import Path

from sqlalchemy import text

from . import analytics
from . import dialect as sql

BENCHMARK_FILE = Path(__file__).parent.parent / "docs" / "benchmarks.json"


def benchmark_file():
    """Where flask benchmark writes its results (tests point this somewhere else)."""
    from flask import current_app
    return Path(current_app.config.get("BENCHMARK_FILE") or BENCHMARK_FILE)


def best_of(function, repeat=3):
    """The fastest of a few runs, in ms (the first run also warms the database's cache)."""
    times = []
    result = None
    for _ in range(repeat):
        started = time.perf_counter()
        result = function()
        times.append((time.perf_counter() - started) * 1000)
    return min(times), result


def count_rows(s, table, ws, first, last, day="order_date"):
    return s.execute(text(f"SELECT COUNT(*) FROM {table} WHERE workspace_id = :ws AND {day} BETWEEN :first AND :last"),
                     {"ws": ws, "first": first, "last": last}).scalar()


def sources(s, f, repeat=3):
    """Each question, from the raw tables and from the rollups: time and rows read."""
    raw_orders = count_rows(s, "orders", f.ws, f.first, f.last)
    raw_lines = s.execute(text("SELECT COUNT(*) FROM order_lines l JOIN orders o ON o.id = l.order_id "
                               "WHERE o.workspace_id = :ws AND o.order_date BETWEEN :first AND :last"),
                          {"ws": f.ws, "first": f.first, "last": f.last}).scalar()
    order_stats = count_rows(s, "daily_order_stats", f.ws, f.first, f.last, "day")
    product_stats = count_rows(s, "daily_product_stats", f.ws, f.first, f.last, "day")
    questions = [
        ("KPI totals", lambda source: analytics.kpis(s, f, source=source), raw_orders, order_stats),
        ("Revenue by day (with a 7-day average)", lambda source: analytics.trend(s, f, "day", source=source),
         raw_orders, order_stats),
        ("Sales by region", lambda source: analytics.breakdown(s, f, "region", source=source), raw_orders, order_stats),
        ("Top 10 products", lambda source: analytics.breakdown(s, f, "product", limit=10, source=source),
         raw_lines, product_stats),
    ]
    out = []
    for name, ask, raw_rows, rolled_rows in questions:
        raw_ms, raw_answer = best_of(lambda: ask("raw"), repeat)
        rolled_ms, rolled_answer = best_of(lambda: ask("rollup"), repeat)
        same = _comparable(raw_answer) == _comparable(rolled_answer)
        out.append({"name": name, "raw_ms": raw_ms, "rollup_ms": rolled_ms,
                    "speedup": raw_ms / rolled_ms if rolled_ms else None,
                    "raw_rows": raw_rows, "rollup_rows": rolled_rows, "same": same})
    return out


def _comparable(answer):
    """Answers without the parts that are allowed to differ (which source, category order counts)."""
    if isinstance(answer, dict):
        return {k: v for k, v in answer.items() if k != "source"}
    if isinstance(answer, list):
        return [{k: v for k, v in row.items() if k not in ("orders", "share")} for row in answer]
    return answer


def explain(s, query, params, without_index=False):
    """(plan lines, ms) for a query. without_index makes the database ignore indexes on orders."""
    d = sql.name_of(s)
    if d == "sqlite":
        if without_index:
            query = query.replace("orders o", "orders o NOT INDEXED", 1).replace(
                "daily_order_stats r", "daily_order_stats r NOT INDEXED", 1)
        rows = s.execute(text("EXPLAIN QUERY PLAN " + query), params).all()
        plan = [("  " * _depth(rows, row)) + row[-1] for row in rows]
        ms, _ = best_of(lambda: s.execute(text(query), params).all())
        return plan, ms
    # PostgreSQL: settings changed with SET LOCAL last only until this transaction ends.
    connection = s.connection()
    if without_index:
        for setting in ("enable_indexscan", "enable_bitmapscan", "enable_indexonlyscan"):
            connection.execute(text(f"SET LOCAL {setting} = off"))
    rows = connection.execute(text("EXPLAIN (ANALYZE, BUFFERS) " + query), params).all()
    ms, _ = best_of(lambda: connection.execute(text(query), params).all())
    s.rollback()  # undo the SET LOCAL
    return [row[0] for row in rows], ms


def _depth(rows, row):
    """SQLite's plan rows are (id, parent, ...): indent each under its parent."""
    parents = {r[0]: r[1] for r in rows}
    depth, parent = 0, row[1]
    while parent in parents and depth < 10:
        depth += 1
        parent = parents[parent]
    return depth


def index_experiment(s, f):
    """The raw KPI query with and without its index, and the plans."""
    d = sql.name_of(s)
    query, params = analytics.kpi_query(d, f, "raw")
    with_plan, with_ms = explain(s, query, params)
    without_plan, without_ms = explain(s, query, params, without_index=True)
    return {"sql": query, "with_plan": with_plan, "with_ms": with_ms, "without_plan": without_plan,
            "without_ms": without_ms, "dialect": d}


def paging(s, ws, depth_share=0.8, page=50):
    """Fetch one page deep in the Records list, with OFFSET and with a keyset."""
    total = s.execute(text("SELECT COUNT(*) FROM order_lines WHERE workspace_id = :ws"), {"ws": ws}).scalar() or 0
    offset = max(0, int(total * depth_share) - page)
    base = """SELECT o.order_date, o.id, l.line_no, l.revenue FROM order_lines l JOIN orders o ON o.id = l.order_id
              WHERE o.workspace_id = :ws {extra} ORDER BY o.order_date DESC, o.id DESC, l.line_no LIMIT :limit {offset}"""
    with_offset = base.format(extra="", offset="OFFSET :offset")
    offset_ms, rows = best_of(lambda: s.execute(text(with_offset), {"ws": ws, "limit": page, "offset": offset}).all())
    if not rows:
        return {"total": total, "offset": offset, "offset_ms": offset_ms, "keyset_ms": None, "same": True}
    # The keyset starts after the row just before the page (found once, not timed).
    before = s.execute(text(with_offset), {"ws": ws, "limit": 1, "offset": max(offset - 1, 0)}).first()
    # "order_date <= the cursor's day" says nothing new, but it is simple enough for the index
    # to start right at the cursor; the OR part then finds the exact place.
    keyset = base.format(extra="AND o.order_date <= :c_day "
                               "AND (o.order_date < :c_day OR (o.order_date = :c_day AND o.id < :c_order) "
                               "OR (o.order_date = :c_day AND o.id = :c_order AND l.line_no > :c_line))", offset="")
    params = {"ws": ws, "limit": page, "c_day": sql.as_date(before[0]), "c_order": before[1], "c_line": before[2]}
    keyset_ms, keyset_rows = best_of(lambda: s.execute(text(keyset), params).all())
    same = offset == 0 or [tuple(r) for r in rows] == [tuple(r) for r in keyset_rows]
    return {"total": total, "offset": offset, "offset_ms": offset_ms, "keyset_ms": keyset_ms, "same": same}


def table_sizes(s, ws):
    out = []
    for table, what in (("orders", "one row per order"), ("order_lines", "one row per line"),
                        ("daily_order_stats", "day x region x channel"),
                        ("daily_product_stats", "day x product x region x channel")):
        out.append({"table": table, "what": what, "rows": s.execute(
            text(f"SELECT COUNT(*) FROM {table} WHERE workspace_id = :ws"), {"ws": ws}).scalar()})
    return out


def saved_benchmark():
    try:
        return json.loads(benchmark_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def benchmark_markdown(result):
    lines = ["# Benchmarks", "",
             f"Made by `flask --app tallyho benchmark` on {result['made_on']} ({result['database']}).",
             f"{result['lines']:,} order lines and {result['orders']:,} orders, {result['days']} days of sample sales.",
             f"Importing them took {result['import_seconds']:.1f} s ({result['lines_per_second']:,.0f} lines a second).",
             "", f"Questions about the last {result['range_days']} days, the best of 3 runs:", "",
             "| Question | Raw tables | Rollups | Faster | Rows read (raw / rollup) |", "|---|---:|---:|---:|---:|"]
    for row in result["sources"]:
        lines.append(f"| {row['name']} | {row['raw_ms']:.1f} ms | {row['rollup_ms']:.1f} ms | "
                     f"{row['speedup']:.0f}x | {row['raw_rows']:,} / {row['rollup_rows']:,} |")
    index = result["index"]
    paging_result = result["paging"]
    lines += ["", "## The index", "",
              f"The raw KPI query for the last {result.get('index_days', 30)} days, with its index: "
              f"{index['with_ms']:.1f} ms; without it: {index['without_ms']:.1f} ms.",
              "", "## Paging", "",
              f"Page {paging_result['offset'] // 50 + 1:,} of Records: OFFSET {paging_result['offset_ms']:.1f} ms, "
              f"keyset {paging_result['keyset_ms']:.1f} ms.", ""]
    return "\n".join(lines)

