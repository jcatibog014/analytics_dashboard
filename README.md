# Tally Ho!

**Every number, at a glance.**

Tally Ho! is an analytics dashboard for a small business. You **import your sales** (a CSV or Excel file from your till or online shop), and it shows your **KPIs** (revenue, orders, profit, customers), **trends** and **reports** for any dates you pick.

It is a learning project. The skills are **SQL aggregation**, **performance** and **data visualization**. The plan, stage by stage, is in [docs/PLAN.md](docs/PLAN.md).

![The dashboard](docs/screenshots/dashboard.png)

## What It Does

**Import your sales**

- Upload a **CSV** or **Excel** file. One row is one line of an order.
- Tally Ho! **guesses which column is which** from the names ("Receipt #" is the order number, "Sold On" is the date). You check it and press Import. The next file with the same columns is matched by itself.
- Dates can be written many ways (2026-03-25, 03/25/2026, 25/03/2026, with or without a time).
- Every row is checked. A row with a problem leaves its whole order out and says why. You can **download the problem rows**, fix them and import again.
- Orders that are already here are **skipped** (or **replaced**, if you choose). Importing the same file twice is safe.
- **Undo** an import: its orders go away again.
- No file yet? Download a **sample CSV**, or load **18 months of sample sales**.
- **Records** shows every line, newest first, with a search.

**See your numbers**

- The **Dashboard** shows ten KPIs for the dates you pick: revenue, orders, average order, gross profit and margin, units, customers, new customers, returning customers and the discount rate. Each one says how it changed, in green or red (and with an arrow), and has a small line of its trend.
- Pick the dates: today, 7, 30 or 90 days, this month, last month, this quarter, the year so far, 12 months, all time, or your own dates.
- **Compare** with the period before, or with the same dates last year.
- A **chart** of revenue, orders, profit, average order or customers, by day, week or month, with a **7-day average** and the earlier period behind it. Move the mouse (or the arrow keys) to read every day.
- Sales by **category**, **region** and **channel**, and the **top products**.
- **Filter** everything by region, channel or category. Every chart has **Show as table**.

**Look closer**

- **Products**: every product with its revenue, share, units, orders, profit and margin. Pick a category to see only its products, then a product to see its own page.
- **Customers**: how many, how many are new, revenue per customer, the top customers, and **when people order** (a weekday-by-hour heatmap). Each customer has a page with their orders.

**Reports**

- **Build a report**: pick the rows (category, product, region, channel, customer, month, quarter, week or weekday), the columns, and the number to show. **Save** it, **print** it, or download it as **CSV** or **Excel**.
- **Customer cohorts**: the customers who first ordered in each month, and how many came back 1, 2, 3 ... months later.
- **80/20 products**: the few products that bring in most of the money.
- A **weekly email** every Monday with last week's numbers (switch it on in Settings).

**Goals and alerts**

- Set a **goal** for a month (revenue, orders or profit, for everything or one region). See how much is done, how much is **expected by today**, and a **forecast** for the month. Both follow your usual week, so a slow Monday is no worry.
- **Alerts** tell you when a day was far from the usual for that weekday ("revenue 25% below the usual Monday"). They are checked after every import and every hour.

**Performance Lab**

- Every page says how many SQL queries it ran and how long they took.
- The **Performance Lab** asks the same questions in different ways and times them on your own data: from the raw sales or from the daily **rollups**, **with or without an index** (with the database's own plan), and deep pages with **OFFSET** or a **keyset**.

## How It Is Fast

This project practises **SQL aggregation** and **performance**:

- **Hand-written SQL.** The numbers come from SQL written out in full: `GROUP BY`, `SUM(CASE ...)`, `COUNT(DISTINCT ...)`, `WITH RECURSIVE` (a row for every day, so a day with no sales is a zero, not a gap), and window functions: `SUM() OVER ()` for a share of the total, `RANK()` for places, a 7-day moving average with `ROWS BETWEEN 6 PRECEDING AND CURRENT ROW`, and a running total for the 80/20 list.
- **Rollups.** When sales are imported, they are also added up **once per day** (per region and channel, and per product). A year of revenue then reads a few thousand small rows instead of hundreds of thousands of orders. Only numbers that add up can be rolled up: "different customers" can't (a customer who came on two days is still one customer), so those always read the orders.
- **The right source.** For each question, the code picks the fastest table that can answer it correctly, and reads the raw sales when no rollup can (like "orders in a category": an order with two kinds of beans is one order). Tests check that rollups and raw sales always give the same answers.
- **Totals that don't add up wrong.** A report's row, column and grand totals are asked of the database (four `GROUP BY`s joined with `UNION ALL`: the portable version of `GROUPING SETS`), so a customer who bought in two regions counts once in the total.
- **Indexes that follow the questions.** Almost every query asks for "this business, these dates", so the main index starts with those two. The customer is in it too, so "how many customers" is read from the index alone.
- **Keyset paging.** Records pages continue "after the last row you saw" instead of `OFFSET`, which reads and throws away every row before the page.
- **A cache.** Answers are remembered until new sales arrive (each import adds 1 to a version number that is part of every key).
- **Fast imports.** Batches of 5,000 orders with one `INSERT ... RETURNING`, products and customers looked up in memory, and one transaction: all or nothing.

On a million order lines ([docs/BENCHMARKS.md](docs/BENCHMARKS.md), SQLite on a laptop), questions about a whole year take 3 to 80 ms from the rollups and 0.6 to 1 second from the raw sales. The first benchmark run also found two slow spots, which are now fixed: counting different customers (1.6 s, now 81 ms with the customer in the index) and the keyset page (225 ms, now under 1 ms).

## Pictures

| Page | Picture |
|---|---|
| Log in | ![Log in, or try the demo](docs/screenshots/login.png) |
| Dashboard | ![KPIs, the trend with its hover readout](docs/screenshots/dashboard.png) |
| The whole dashboard | ![KPIs, trend, sales by category, region and channel, top products](docs/screenshots/dashboard-full.png) |
| Dashboard (dark mode), 12 months vs last year | ![The dashboard in dark mode](docs/screenshots/dashboard-dark.png) |
| Filtered by a region | ![The dashboard for Metro Manila only](docs/screenshots/dashboard-filtered.png) |
| On a phone | ![The dashboard on a phone](docs/screenshots/phone-dashboard.png) |
| The menu on a phone | ![The menu open on a phone](docs/screenshots/phone-menu.png) |
| Products | ![Categories and every product](docs/screenshots/products.png) |
| One category | ![The products of Coffee Beans](docs/screenshots/category.png) |
| One product | ![A product's KPIs, trend, regions, channels and buyers](docs/screenshots/product.png) |
| Customers | ![Customers, new customers and the weekday-by-hour heatmap](docs/screenshots/customers.png) |
| One customer | ![A customer's orders and favourite products](docs/screenshots/customer.png) |
| Reports | ![Build a report, cohorts, 80/20, saved reports](docs/screenshots/reports.png) |
| Report builder | ![Revenue by category and quarter, with totals](docs/screenshots/report-builder.png) |
| Customer cohorts | ![First-order months and how many came back](docs/screenshots/cohorts.png) |
| 80/20 products | ![The products that make 80% of revenue](docs/screenshots/pareto.png) |
| Goals | ![Goals with pace, forecast and the month's chart](docs/screenshots/goals.png) |
| Alerts | ![Days far from the usual, and the rules](docs/screenshots/alerts.png) |
| Import | ![Upload a file, and the import history](docs/screenshots/import.png) |
| Match columns | ![The file's first rows, and which column is which](docs/screenshots/match-columns.png) |
| An import with problem rows | ![What was added, and the rows that were left out](docs/screenshots/import-problems.png) |
| Records | ![Every line, newest first](docs/screenshots/records.png) |
| One order | ![An order and its lines](docs/screenshots/order.png) |
| Performance Lab | ![Raw vs rollups, the index, paging, the cache](docs/screenshots/lab.png) |
| Settings | ![Theme, business name, weekly email, password](docs/screenshots/settings.png) |

## Try the Demo

Press **Try the demo** on the login page. You get your own copy of **Kape Corner**, a made-up coffee business with 15 months of sales, for 2 hours. Nobody else sees it. Import a file, undo it, set goals, save reports: it is all yours. All names in it are made up.

## Put It on Your Phone

Tally Ho! installs like an app: on Android use Chrome's menu, **Install app**; on an iPhone open it in Safari, press Share, then **Add to Home Screen**. Without internet it shows a small "You're offline" page; the numbers always come fresh.

## Put It Online

Step-by-step notes for Neon, Render, the hourly timer and Gmail are in [docs/DEPLOY.md](docs/DEPLOY.md).

## Tools We Use

| Tool | What it is | What it does here |
|---|---|---|
| Python 3.13 | A programming language | All the code on the server |
| Flask | A small web framework | Makes the pages |
| SQLAlchemy | A database library | Runs the hand-written SQL, and saves people, imports, reports and goals |
| Alembic | A migration tool | Changes the database tables safely |
| SQLite | A small database in one file | The database on your own computer and in tests |
| PostgreSQL (Neon) | A big database in the cloud | The database of the live site |
| openpyxl | An Excel library | Reads Excel files and writes Excel reports |
| SVG | Pictures made of shapes | Every chart, drawn on the server |
| HTML, CSS, JavaScript | The languages of web pages | The look of the pages, and the chart tooltips |
| Gmail | Email | The weekly summary |
| GitHub Actions | A free timer | Calls the site every hour (weekly email, alerts, old demos) |
| pytest | A test tool | Checks that everything works |
| Playwright | A browser robot | Takes the pictures in this README, and draws the app icons |
| Render | A web host | Runs the live site |

## Run It on Your Computer

```
py -3.13 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\flask --app tallyho upgrade-db
.venv\Scripts\flask --app tallyho create-owner
.venv\Scripts\flask --app tallyho run --debug --port 5011
```

Then open http://127.0.0.1:5011. Without Gmail settings, emails are printed in the terminal.

More commands:

```
.venv\Scripts\python -m pytest                                 (run the tests)
.venv\Scripts\flask --app tallyho make-sample --rows 50000      (sample sales into your workspace)
.venv\Scripts\flask --app tallyho import-sales big-file.csv     (import a big file)
.venv\Scripts\flask --app tallyho benchmark                     (the million-line benchmark)
.venv\Scripts\flask --app tallyho tick                          (run the hourly timer once)
```
