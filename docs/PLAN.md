# Tally Ho! — Analytics Dashboard ("Every number, at a glance.")

## Context
A new learning project: import sales records and show KPIs, trends and reports over any date range.
Skills to practise: **SQL aggregation, performance, data visualization**. Your choices: **sales orders**
as the records, the name **Tally Ho!**, the **Clean analyst** look, all four extra groups (Compare & drill
down, Reports & exports, Goals & alerts, Performance lab), **owner + per-visitor Try Demo**, **server-drawn
SVG charts + a little JS**, and **one PR with a commit per stage**.

Folder `C:\Users\USER\Documents\Workspace\analytics_dashboard`, cloned from
https://github.com/jcatibog014/analytics_dashboard (you made it; it only has a README on `main`). Work goes on
branch `build-tally-ho`, and this plan is copied into `docs/PLAN.md`.
Package `tallyho`, local port **5011** (Ticket Tock has 5010). Money is whole centavos (₱), timezone Asia/Manila.

## Stack (same as Ticket Tock / DFJC)
Flask 3 + SQLAlchemy 2.0 + Alembic, SQLite locally / Neon PostgreSQL live, psycopg 3, openpyxl (Excel in/out),
pytest, gunicorn on Render. Hand-written login, CSRF on every POST, strict CSP, every page works without JS.
`AGENTS.md` + `CLAUDE.md` pointing to it; README in A1-level English with screenshots.

**The analytics queries are hand-written SQL** (`text()` in `analytics.py`), not ORM, because SQL aggregation
is the skill: GROUP BY, CASE inside SUM, CTEs, window functions (moving average, running total, RANK, share
of total), COUNT(DISTINCT), date bucketing. One small `dialect.py` hides the SQLite/Postgres differences
(`strftime` vs `date_trunc`, date + N days). ORM is used only for app tables (users, imports, reports, goals).

Copy and adapt (don't re-invent):
- `expense_tracker/gastos/csv_io.py` → `decode()` (BOM), `safe_cell()` (CSV-injection guard), date parsing.
- `ecommerce_store/dfjc/insights.py` → `change()`, `nice_ceiling()`, `window()`.
- `appointment_booking_system/shiromoto/insights.py` → `column_path()`, `row_path()`, `nice_top()` SVG helpers.
- `appointment_booking_system/shiromoto/tick.py` + its GitHub Actions cron, `notices.py` outbox (weekly email).
- `auth.py`, `db.py` (Hire Wire's fixed `upgrade_db`), `formatting.py`, demo with random `session_version`
  + `sqlite_autoincrement`, `docs/take_screenshots.py`, `docs/make_icons.py`, `tests/conftest.py`.

## Data model
- `workspaces` (owner's real one + one per Try Demo), `users`, `data_version` on the workspace (bumped on every
  import/undo; used as the cache key).
- Dimensions per workspace: `products` (sku, name, category), `categories`, `regions`, `channels`, `customers`
  (code, name, `first_order_date`).
- Facts: **`orders`** (one row per order: order_no, order_date, ordered_at time if given, customer, region,
  channel, revenue, cost, discount, units, import_id) and **`order_lines`** (order, line_no, product, qty,
  unit price, unit cost, discount). UNIQUE(workspace, order_no) and (order, line_no) make re-imports safe.
- **Rollups** (additive measures only — a lesson: COUNT(DISTINCT customers) can't be summed, so it always
  comes from `orders`): `daily_order_stats` (day × region × channel: orders, revenue, cost, units,
  new customers) and `daily_product_stats` (day × product × region × channel: units, revenue, cost, orders).
  After an import or undo, only the touched days are rebuilt with `DELETE` + `INSERT … SELECT … GROUP BY`
  in the same transaction.
- Indexes chosen from the queries: `(workspace_id, order_date)`, `(workspace_id, customer_id, order_date)`,
  `(order_id)` on lines, rollup primary keys starting with `(workspace_id, day)`.

## Pages
- **Dashboard** — date range bar (Today, 7/30/90 days, This/Last month, Quarter, Year to date, 12 months, All,
  Custom; kept in the URL so links can be shared), compare to previous period or last year. KPI tiles:
  Revenue, Orders, Average order, Gross profit + margin %, Units, Customers, New customers, Repeat rate —
  each with change ▲/▼ (green/red *and* arrow; "good direction" per KPI) and a sparkline. Revenue trend
  (day/week/month picked from the range length, 7-day moving average, faint previous-period line), sales by
  category / region / channel, top 10 products.
- **Products / Customers** — drill down: category → products → product page (its trend, regions, channels,
  top customers); customers: new vs returning, top customers, weekday × hour heatmap (when times are given).
- **Reports** — report builder (rows × optional columns/time × measure, filters) as a pivot table with totals;
  saved reports; cohort retention (first-order month × months later); 80/20 (Pareto) products;
  CSV + Excel export; print view.
- **Goals & Alerts** — monthly targets (revenue/orders/profit, optional per region) with progress and pace
  ("expected by today"), month-end forecast (run rate weighted by weekday), alert rules ("revenue drops
  more than 20% vs the same weekday average") checked after imports and by the tick; Alerts list.
- **Import** — upload CSV or Excel → preview + column matching (auto by header name, saved mappings) →
  check every row (reasons per row, error CSV download) → insert in batches of 5,000 → import history with
  **Undo import**. Sample CSV download. Records page to browse raw lines (keyset pagination, search).
- **Performance Lab** — every page footer shows "N queries · X ms" (SQLAlchemy cursor events). The lab shows:
  the same KPI from raw tables vs rollups (time + rows read), `EXPLAIN QUERY PLAN` / `EXPLAIN ANALYZE`,
  "pretend the index is missing" (`NOT INDEXED` / `SET LOCAL enable_indexscan = off`), OFFSET vs keyset
  paging, cache hits/misses, and the saved 1-million-row benchmark (`flask make-sample --rows 1000000`
  run locally only — too big for Neon's free plan; results committed in `docs/BENCHMARKS.md`).
- **Settings** — theme, log out, password, weekly email summary on/off, currency label (your rules).

## Charts (server SVG + small JS)
`charts.py` turns query rows into SVG: line (with moving average + compare line), columns, horizontal bars,
sparklines, heatmap, progress bars. Every chart has a "Show as table" view and an `aria-label` summary.
A small `charts.js` adds hover tooltips and a crosshair. I load the **dataviz skill** before writing chart
code and check the palette with its validator in light and dark.

## Stages (one PR, a commit per stage)
1. **Foundation** — setup, models + first migration, owner login (`flask create-owner`), workspaces,
   Settings, app shell + Clean analyst theme, logo/icons.
2. **Import** — CSV/Excel upload, column matching, row checks + error CSV, batch insert, dimension upsert,
   import history + undo, Records page (keyset paging), sample CSV + `flask make-sample`.
3. **Aggregation core** — `analytics.py` (Filters, date ranges, buckets, `dialect.py`), rollups + rebuild,
   KPI/trend/breakdown queries, cache keyed by `data_version`; test that raw and rollup answers are equal.
4. **Dashboard** — `charts.py` + `charts.js`, KPI tiles, trend, breakdowns, top products, range bar.
5. **Compare & drill down** — compare modes, filter chips (region/channel/category), Products, product page,
   Customers, heatmap.
6. **Reports & exports** — report builder, saved reports, cohorts, Pareto, CSV/Excel export, print view,
   weekly summary email (outbox + `/tasks/tick` via GitHub Actions cron).
7. **Goals & alerts** — targets, pace, forecast, alert rules, Alerts list.
8. **Performance Lab** — query timer, EXPLAIN, raw vs rollup, index on/off, keyset vs offset, cache stats,
   1M-row benchmark + `docs/BENCHMARKS.md`.
9. **Demo, PWA, deploy, README** — Try Demo copies a "Kape Corner" template workspace (~18 months, ~14k
   orders) with `INSERT … SELECT` and dates shifted to today; 2-hour life, a cap on live demos; every demo
   page shows an example (`tests/test_demo.py`); PWA; `render.yaml` (`tally-ho`) + `docs/DEPLOY.md`;
   README screenshots for every page (`tests/test_readme.py`).

## Look: Clean analyst
| Token | Light | Dark |
|---|---|---|
| `--page` | `#f4f6fa` soft grey | `#0f141c` |
| `--card` | `#ffffff` | `#171e29` |
| `--ink` | `#0f1b33` navy | `#e6eaf2` |
| `--muted` | `#5b6475` | `#9aa4b5` |
| `--accent` | `#4f46e5` indigo | `#818cf8` |
| Up / down | green / red + arrows, validated | lighter versions |

Logo: four tally marks with a slash, white on an indigo tile (no tick, no emoji). Inter with tabular numbers.
Your UI rules: Title Case page names and headings, sentence-case buttons, aligned equal-height controls,
main button last on the right, theme and Log out in Settings only, sidebar from 56rem, ☰ below.

## Defaults I picked (tell me to change any)
- One currency (₱) and one timezone (Manila); no multi-currency.
- Web import up to 20 MB / ~100k rows; bigger files use `flask import-csv`.
- Weekly email is optional (Gmail app password); in-app Alerts always work.

## Verification
- `.venv\Scripts\python -m pytest`: import checks + undo, raw == rollup on random data, every KPI against a
  small hand-counted dataset, date ranges and compare periods (month ends, leap day), window-function
  results, cohort table, pivot totals, EXPLAIN shows the index is used, demo pages non-empty, migrations
  match models, README pictures match.
- Run `flask --app tallyho run --debug --port 5011`, import the sample CSV, try every page in light and dark
  with screenshots; run the 1M-row benchmark locally and record it.
- Push, confirm `origin` has the commit, open the PR with `gh pr create --body-file` (screenshots as raw URLs).
- Afterwards (your steps): run the tests on an empty Neon branch, go live on Render + Neon.

## Progress

All 9 stages are in one pull request, a commit per stage.

- [x] 1. Foundation
- [x] 2. Import
- [x] 3. Aggregation core
- [x] 4. Dashboard
- [x] 5. Compare & drill down
- [x] 6. Reports & exports
- [x] 7. Goals & alerts
- [x] 8. Performance Lab (the first benchmark found two slow spots: distinct customers and the keyset
      condition; migration 0006 and a sargable keyset fixed them, see docs/BENCHMARKS.md)
- [x] 9. Demo, PWA, deploy, README

Changes from the plan while building:
- The demo is about 15 months at 20 orders a day (about 10,000 orders), at most 20 at once, to stay
  well inside Neon's free 0.5 GB.
- Profit Margin sits under Gross Profit instead of being a tile of its own (10 places, two full rows).
- Still to do by the owner: run the tests on an empty Neon branch, go live (docs/DEPLOY.md).
