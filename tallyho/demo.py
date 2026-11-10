"""Try Demo: every visitor gets their own copy of Kape Corner's sample sales.

Making 10,000 sample orders in Python for every visitor would be slow on the
live site, so the sales are made once, into a hidden template workspace, and
each demo copies them inside the database with INSERT ... SELECT: one
statement per table, no rows travelling to Python and back.

The copy has new ids, so every row's links (an order's region, a line's
product) are found again by their natural keys: the region's name, the
product's code, the order's number. That is a join from the template's row
to the template's region, and on to the demo's region with the same name.

The dates move forward by whole weeks, so Saturdays stay Saturdays and the
newest sale is today. A demo lives for 2 hours; the timer and the next
Try Demo delete old ones (deleting the workspace deletes everything in it).
"""
import secrets
from datetime import timedelta

from flask import Blueprint, flash, g, redirect, url_for
from sqlalchemy import delete, func, select, text

from . import auth, db, goals, importer, rollups, sample, tick
from . import dialect as sql
from .dates import today, utcnow
from .models import AlertRule, Goal, SavedReport, Workspace

bp = Blueprint("demo", __name__)

DEMO_LIFETIME = timedelta(hours=2)
MAX_DEMOS = 20         # demos alive at once, so a script can't fill the free database
TEMPLATE_DAYS = 456    # about 15 months: enough for "same dates last year" and a year of cohorts
TEMPLATE_PER_DAY = 20
VISITOR_NAME = "Demo Visitor"


def template(s):
    return s.scalar(select(Workspace).where(Workspace.is_template.is_(True)).order_by(Workspace.id).limit(1))


def make_template(s, now_day):
    """The sample sales, made once. They run 6 days past today, so a copy shifted by whole weeks
    can always end on (or after) the visitor's today."""
    workspace = Workspace(name=sample.BUSINESS, is_template=True, created_at=utcnow())
    s.add(workspace)
    s.flush()
    sample.load(s, workspace, None, now_day - timedelta(days=TEMPLATE_DAYS - 1), now_day + timedelta(days=6),
                TEMPLATE_PER_DAY, seed=23)
    s.commit()
    return workspace


def shift_for(template_last, now_day):
    """Whole weeks to move the template's dates so they reach today."""
    behind = (now_day - template_last).days
    return max(0, -(-behind // 7)) * 7  # rounded up to a whole week


def copy_sales(s, source, target, now_day):
    """Copy every sale of source into target, dates moved forward, nothing after today."""
    d = sql.name_of(s)
    template_last = sql.as_date(s.execute(text("SELECT MAX(order_date) FROM orders WHERE workspace_id = :t"),
                                          {"t": source.id}).scalar())
    shift = shift_for(template_last, now_day)
    shifted = sql.add_days(d, "o.order_date", shift)
    p = {"t": source.id, "n": target.id, "today": now_day}
    record = importer.new_record(s, target, None, f"Sample data ({sample.BUSINESS}).csv")
    p["imp"] = record.id
    for table in ("categories", "regions", "channels"):
        s.execute(text(f"INSERT INTO {table} (workspace_id, name) SELECT :n, name FROM {table} WHERE workspace_id = :t"), p)
    s.execute(text("""
        INSERT INTO products (workspace_id, sku, name, category_id)
        SELECT :n, p.sku, p.name, c2.id
        FROM products p
        LEFT JOIN categories c1 ON c1.id = p.category_id
        LEFT JOIN categories c2 ON c2.workspace_id = :n AND c2.name = c1.name
        WHERE p.workspace_id = :t"""), p)
    s.execute(text("INSERT INTO customers (workspace_id, code, name) SELECT :n, code, name FROM customers "
                   "WHERE workspace_id = :t"), p)
    s.execute(text(f"""
        INSERT INTO orders (workspace_id, import_id, order_no, order_date, order_hour, customer_id, region_id,
                            channel_id, line_count, units, gross, discount, revenue, cost)
        SELECT :n, :imp, o.order_no, {shifted}, o.order_hour, cu2.id, r2.id, ch2.id,
               o.line_count, o.units, o.gross, o.discount, o.revenue, o.cost
        FROM orders o
        JOIN regions r1 ON r1.id = o.region_id
        JOIN regions r2 ON r2.workspace_id = :n AND r2.name = r1.name
        JOIN channels ch1 ON ch1.id = o.channel_id
        JOIN channels ch2 ON ch2.workspace_id = :n AND ch2.name = ch1.name
        LEFT JOIN customers cu1 ON cu1.id = o.customer_id
        LEFT JOIN customers cu2 ON cu2.workspace_id = :n AND cu2.code = cu1.code
        WHERE o.workspace_id = :t AND {shifted} <= {sql.date_param(d, 'today')}"""), p)
    s.execute(text("""
        INSERT INTO order_lines (order_id, workspace_id, line_no, product_id, quantity, unit_price, unit_cost,
                                 discount, revenue, cost)
        SELECT o2.id, :n, l.line_no, p2.id, l.quantity, l.unit_price, l.unit_cost, l.discount, l.revenue, l.cost
        FROM order_lines l
        JOIN orders o1 ON o1.id = l.order_id
        JOIN orders o2 ON o2.workspace_id = :n AND o2.order_no = o1.order_no
        JOIN products p1 ON p1.id = l.product_id
        JOIN products p2 ON p2.workspace_id = :n AND p2.sku = p1.sku
        WHERE l.workspace_id = :t"""), p)
    importer.update_first_orders(s, target.id)
    rollups.rebuild_all(s, target.id)
    counts = s.execute(text("SELECT COUNT(*), COALESCE(SUM(line_count), 0), MIN(order_date), MAX(order_date) "
                            "FROM orders WHERE workspace_id = :n"), p).one()
    record.orders_added, record.lines_added, record.rows_read = counts[0], counts[1], counts[1]
    record.first_day, record.last_day = sql.as_date(counts[2]), sql.as_date(counts[3])
    record.seconds = 0.4
    target.data_version = (target.data_version or 0) + 1
    return record


PROBLEM_FILE = """Order No,Date,SKU,Product,Category,Quantity,Unit Price,Unit Cost,Customer ID,Customer Name,Region,Channel
WEB-9001,{d1},BN-HSE-250,House Blend Beans 250g,Coffee Beans,2,320.00,150.00,C-00007,{name},Metro Manila,Website
WEB-9001,{d1},MR-MUG-001,Kape Corner Mug,Merchandise,1,390.00,150.00,C-00007,{name},Metro Manila,Website
WEB-9002,{d2},GR-DRP-V60,Pour-over Dripper,Brewing Gear,one,890.00,420.00,C-00012,Bea Ramos,Cebu,Website
WEB-9003,{d2},BN-SAG-250,Sagada Peaberry Beans 250g,Coffee Beans,1,480.00,250.00,C-00020,Luis Tan,Davao,Website
WEB-9004,31/02/2026,SN-MNG-200,Dried Mango 200g,Snacks,3,180.00,95.00,C-00021,Joy Lim,Cebu,Website
"""


def fill_extras(s, workspace, user, now_day):
    """The rest of the demo, so no page is empty: a small import with problem rows,
    goals, alert rules (and the alerts they find), and saved reports."""
    customer = s.execute(text("SELECT name FROM customers WHERE workspace_id = :w AND code = 'C-00007'"),
                         {"w": workspace.id}).scalar() or "Maria Santos"
    content = PROBLEM_FILE.format(d1=(now_day - timedelta(days=1)).isoformat(),
                                  d2=(now_day - timedelta(days=2)).isoformat(), name=customer)
    import csv
    import io
    reader = csv.reader(io.StringIO(content))
    headers = next(reader)
    rows = [(n, row) for n, row in enumerate(reader, start=2) if row]
    result = importer.parse_file(headers, rows, importer.auto_match(headers), "ymd")
    importer.run_import(s, workspace, user, "Website orders (fix 2 rows).csv", result)
    month = now_day.replace(day=1)
    last_month = (month - timedelta(days=1)).replace(day=1)
    typical = s.execute(text("SELECT COALESCE(SUM(revenue), 0), COUNT(*) FROM orders WHERE workspace_id = :w "
                             "AND order_date >= :first AND order_date < :month"),
                        {"w": workspace.id, "first": last_month, "month": month}).one()
    month_revenue = max(int(typical[0] * 1.08) // 100000 * 100000, 100000)
    region = s.execute(text("SELECT id FROM regions WHERE workspace_id = :w AND name = 'Cebu'"),
                       {"w": workspace.id}).scalar()
    s.add_all([
        Goal(workspace_id=workspace.id, measure="revenue", month=month, target=month_revenue),
        Goal(workspace_id=workspace.id, measure="orders", month=month, target=max(int(typical[1] * 1.05), 10)),
        Goal(workspace_id=workspace.id, measure="revenue", month=month, region_id=region,
             target=max(int(typical[0] * 0.17) // 100000 * 100000, 100000)),
        Goal(workspace_id=workspace.id, measure="revenue", month=last_month, target=max(int(typical[0] * 0.95), 100000)),
        AlertRule(workspace_id=workspace.id, measure="revenue", direction="drop", percent=25),
        AlertRule(workspace_id=workspace.id, measure="orders", direction="rise", percent=35),
        SavedReport(workspace_id=workspace.id, user_id=user.id, name="Monthly sales by category",
                    params='{"rows": "category", "columns": "month", "measure": "revenue", "range": "90d"}'),
        SavedReport(workspace_id=workspace.id, user_id=user.id, name="Orders by region and weekday",
                    params='{"rows": "region", "columns": "weekday", "measure": "orders", "range": "30d"}'),
    ])
    s.flush()
    goals.check(s, workspace)


def purge(s, moment):
    """Delete finished demos (everything in them goes too: ON DELETE CASCADE)."""
    found = s.execute(delete(Workspace).where(Workspace.is_demo.is_(True), Workspace.demo_expires_at <= moment)
                      .execution_options(synchronize_session=False))
    return found.rowcount or 0


def start_demo(s, now=None):
    """A fresh demo workspace with its visitor. Returns the visitor (the caller commits)."""
    moment = now or utcnow()
    now_day = today()
    source = template(s)
    if source is None:
        source = make_template(s, now_day)
    workspace = Workspace(name=sample.BUSINESS, is_demo=True, demo_expires_at=moment + DEMO_LIFETIME,
                          created_at=moment)
    s.add(workspace)
    s.flush()
    visitor = auth.create_user(s, workspace, VISITOR_NAME, f"visitor-{secrets.token_hex(6)}@demo.invalid",
                               secrets.token_urlsafe(24))
    # A random session_version: an old cookie for a deleted demo can never fit a new one.
    visitor.session_version = secrets.randbelow(10 ** 9) + 2
    copy_sales(s, source, workspace, now_day)
    fill_extras(s, workspace, visitor, now_day)
    return visitor


@bp.post("/demo")
def start():
    s = db.session()
    if g.user is not None and g.user.is_demo:
        return redirect(url_for("main.home"))
    moment = utcnow()
    purge(s, moment)
    s.commit()
    live = s.scalar(select(func.count()).select_from(Workspace).where(Workspace.is_demo.is_(True))) or 0
    if live >= MAX_DEMOS:
        flash("The demo is very busy right now. Please try again in a little while.", "error")
        return redirect(url_for("auth.login"))
    visitor = start_demo(s, moment)
    s.commit()
    auth.log_in(visitor)
    flash("Welcome! This is Kape Corner, a made-up coffee business with 15 months of sales. Look around, "
          "import a file, set a goal: it's all yours for 2 hours.")
    return redirect(url_for("main.home"))


def tick_job(s, moment):
    deleted = purge(s, moment or utcnow())
    s.commit()
    return {"demos_deleted": deleted}


def init_app(app):
    if tick_job not in tick.JOBS:
        tick.JOBS.insert(0, tick_job)

