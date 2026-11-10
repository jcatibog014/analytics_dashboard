"""The Performance Lab page, and flask benchmark."""
from datetime import timedelta

import click
from flask import Blueprint, flash, g, redirect, render_template, url_for
from flask.cli import with_appcontext
from sqlalchemy import delete, select

from .. import analytics, cache, db, lab, sample, scope
from ..auth import login_required
from ..dates import today, utcnow
from ..models import Import, Workspace

bp = Blueprint("lab", __name__)


def need_developer_mode():
    """The Lab is a tool for developers: it shows only with Developer mode on (Settings)."""
    if not g.user.developer_mode:
        flash("The Performance Lab is a tool for developers. Switch on Developer mode to see it.")
        return redirect(url_for("auth.settings"))
    return None


@bp.get("/lab")
@login_required
def index():
    blocked = need_developer_mode()
    if blocked:
        return blocked
    s = db.session()
    sc = scope.current()
    has_sales = sc.bounds[0] is not None
    results = {}
    if has_sales:
        results = {
            "sources": lab.sources(s, sc.filters),
            "index": lab.index_experiment(s, sc.filters),
            "paging": lab.paging(s, g.workspace.id),
        }
    imports = s.scalars(select(Import).where(Import.workspace_id == g.workspace.id, Import.lines_added > 0)
                        .order_by(Import.id.desc()).limit(8)).all()
    return render_template("lab.html", sc=sc, has_sales=has_sales, results=results, cache=cache.current(),
                           sizes=lab.table_sizes(s, g.workspace.id), imports=imports,
                           benchmark=lab.saved_benchmark())


@bp.post("/lab/cache/clear")
@login_required
def clear_cache():
    blocked = need_developer_mode()
    if blocked:
        return blocked
    cache.current().clear()
    flash("The cache is empty. The next pages work everything out again.")
    return redirect(url_for("lab.index"))


@click.command("benchmark")
@with_appcontext
@click.option("--rows", default=1_000_000, show_default=True, help="About how many order lines.")
@click.option("--days", default=730, show_default=True)
@click.option("--keep", is_flag=True, help="Keep the benchmark workspace afterwards.")
def benchmark_command(rows, days, keep):
    """Time the Lab's questions on a big sample (your own computer only: too big for Neon's free plan).
    Writes docs/BENCHMARKS.md and docs/benchmarks.json, which the Lab page shows."""
    s = db.session()
    moment = utcnow()
    # A workspace of its own, marked as a demo that ends tomorrow, so it can never mix with real sales.
    workspace = Workspace(name="Benchmark", is_demo=True, demo_expires_at=moment + timedelta(days=1))
    s.add(workspace)
    s.flush()
    last = today()
    first = last - timedelta(days=days - 1)
    per_day = max(1, round(rows / days / 2.1 / 1.08))
    click.echo(f"Making about {rows:,} lines...")
    record = sample.load(s, workspace, None, first, last, per_day, filename="Benchmark sample")
    s.commit()
    click.echo(f"Imported {record.lines_added:,} lines in {record.seconds:.1f} s.")
    cache.current().enabled = False
    f = analytics.Filters(workspace.id, workspace.data_version, last - timedelta(days=364), last)
    result = {
        "made_on": today().isoformat(), "database": s.get_bind().dialect.name, "days": days,
        "lines": record.lines_added, "orders": record.orders_added, "import_seconds": record.seconds,
        "lines_per_second": record.lines_added / max(record.seconds, 0.001), "range_days": 365,
        "sources": lab.sources(s, f),
        # An index pays off when it skips most of the table: test it on the last 30 days.
        "index": lab.index_experiment(s, analytics.Filters(workspace.id, workspace.data_version,
                                                           last - timedelta(days=29), last)),
        "index_days": 30, "paging": lab.paging(s, workspace.id),
    }
    result["index"].pop("with_plan")
    result["index"].pop("without_plan")
    import json
    lab.benchmark_file().write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    lab.benchmark_file().with_name("BENCHMARKS.md").write_text(lab.benchmark_markdown(result), encoding="utf-8")
    click.echo(lab.benchmark_markdown(result))
    if not keep:
        s.execute(delete(Workspace).where(Workspace.id == workspace.id).execution_options(synchronize_session=False))
        s.commit()


def init_app(app):
    app.cli.add_command(benchmark_command)
