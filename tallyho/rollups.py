"""Keeping the daily rollup tables (models.DailyOrderStat, DailyProductStat) up to date.

After an import or undo, only the days that changed are worked out again:
DELETE those days, then INSERT ... SELECT ... GROUP BY from the raw tables.
Both run in the import's transaction, so the rollups and the raw sales can
never disagree (tests/test_analytics.py checks that they don't).
"""
import click
from flask.cli import with_appcontext
from sqlalchemy import text

from . import dialect as sql_dialect

ORDER_STATS = """
    INSERT INTO daily_order_stats
        (workspace_id, day, region_id, channel_id, orders, units, gross, discount, revenue, cost, new_customers)
    SELECT o.workspace_id, o.order_date, o.region_id, o.channel_id,
           COUNT(*), SUM(o.units), SUM(o.gross), SUM(o.discount), SUM(o.revenue), SUM(o.cost),
           -- CASE inside SUM counts only the rows where something is true.
           SUM(CASE WHEN c.first_order_id = o.id THEN 1 ELSE 0 END)
    FROM orders o
    LEFT JOIN customers c ON c.id = o.customer_id
    WHERE o.workspace_id = :ws AND o.order_date BETWEEN {first} AND {last}
    GROUP BY o.workspace_id, o.order_date, o.region_id, o.channel_id
"""

PRODUCT_STATS = """
    INSERT INTO daily_product_stats
        (workspace_id, day, product_id, region_id, channel_id, orders, units, discount, revenue, cost)
    SELECT o.workspace_id, o.order_date, l.product_id, o.region_id, o.channel_id,
           COUNT(DISTINCT l.order_id), SUM(l.quantity), SUM(l.discount), SUM(l.revenue), SUM(l.cost)
    FROM order_lines l
    JOIN orders o ON o.id = l.order_id
    WHERE o.workspace_id = :ws AND o.order_date BETWEEN {first} AND {last}
    GROUP BY o.workspace_id, o.order_date, l.product_id, o.region_id, o.channel_id
"""


def rebuild(s, workspace_id, first, last):
    """Work out the rollups again for the days first..last (both included)."""
    if first is None or last is None:
        return
    d = sql_dialect.name_of(s)
    bounds = {"first": sql_dialect.date_param(d, "first"), "last": sql_dialect.date_param(d, "last")}
    params = {"ws": workspace_id, "first": first, "last": last}
    for table, insert_sql in (("daily_order_stats", ORDER_STATS), ("daily_product_stats", PRODUCT_STATS)):
        s.execute(text(f"DELETE FROM {table} WHERE workspace_id = :ws AND day BETWEEN {bounds['first']} "
                       f"AND {bounds['last']}"), params)
        s.execute(text(insert_sql.format(**bounds)), params)


def rebuild_all(s, workspace_id):
    """Every day of a workspace (flask rebuild-rollups, and after copying the demo)."""
    first, last = s.execute(text("SELECT MIN(order_date), MAX(order_date) FROM orders WHERE workspace_id = :ws"),
                            {"ws": workspace_id}).one()
    for table in ("daily_order_stats", "daily_product_stats"):
        s.execute(text(f"DELETE FROM {table} WHERE workspace_id = :ws"), {"ws": workspace_id})
    if first is not None:
        rebuild(s, workspace_id, sql_dialect.as_date(first), sql_dialect.as_date(last))


@click.command("rebuild-rollups")
@with_appcontext
def rebuild_rollups_command():
    """Work out every workspace's daily rollups again from the raw sales."""
    from . import db
    s = db.session()
    ids = [row[0] for row in s.execute(text("SELECT id FROM workspaces"))]
    for workspace_id in ids:
        rebuild_all(s, workspace_id)
        s.execute(text("UPDATE workspaces SET data_version = data_version + 1 WHERE id = :ws"), {"ws": workspace_id})
    s.commit()
    click.echo(f"Rebuilt the rollups of {len(ids)} workspaces.")


def init_app(app):
    app.cli.add_command(rebuild_rollups_command)
