"""The Data pages: Import (upload, match columns, history, undo) and Records."""
import csv
import io
import json
from datetime import date, timedelta

import click
from flask import Blueprint, Response, abort, flash, g, redirect, render_template, request, url_for
from flask.cli import with_appcontext
from sqlalchemy import select, text

from .. import db, importer, sample, uploads, workspaces
from ..auth import login_required
from ..dates import today
from ..models import Import, Order, SavedMatch

bp = Blueprint("data", __name__)

RECORDS_PER_PAGE = 50
SAMPLE_DAYS = 540  # about 18 months


# ---------- Import ----------

@bp.get("/import")
@login_required
def imports():
    s = db.session()
    history = s.scalars(select(Import).where(Import.workspace_id == g.workspace.id)
                        .order_by(Import.id.desc()).limit(50)).all()
    has_sales = s.scalar(select(Order.id).where(Order.workspace_id == g.workspace.id).limit(1)) is not None
    return render_template("data/imports.html", history=history, has_sales=has_sales, fields=importer.FIELDS)


@bp.post("/import/upload")
@login_required
def upload():
    storage = request.files.get("file")
    if storage is None or not storage.filename:
        flash("Choose a file first.", "error")
        return redirect(url_for("data.imports"))
    try:
        token = uploads.save(storage, g.workspace.id)
    except ValueError as problem:
        flash(str(problem), "error")
        return redirect(url_for("data.imports"))
    return redirect(url_for("data.match", token=token))


def _load_upload(token):
    found = uploads.find(token, g.workspace.id)
    if found is None:
        flash("That file is gone (files wait one day). Please upload it again.", "error")
        return None
    path, filename = found
    try:
        headers, rows = importer.read_table(path)
    except importer.FileProblem as problem:
        uploads.remove(token, g.workspace.id)
        flash(str(problem), "error")
        return None
    if not rows:
        uploads.remove(token, g.workspace.id)
        flash("That file has a header row but no sales under it.", "error")
        return None
    return path, filename, headers, rows


@bp.route("/import/<token>", methods=["GET", "POST"])
@login_required
def match(token):
    loaded = _load_upload(token)
    if loaded is None:
        return redirect(url_for("data.imports"))
    _path, filename, headers, rows = loaded
    s = db.session()
    key = importer.headers_key(headers)
    saved = s.scalar(select(SavedMatch).where(SavedMatch.workspace_id == g.workspace.id,
                                              SavedMatch.headers_key == key))
    errors, result = [], None
    if request.method == "POST":
        mapping = {f.key: request.form.get(f.key) or None for f in importer.FIELDS}
        mapping = {k: v for k, v in mapping.items() if v in headers}
        date_format = request.form.get("date_format", "auto")
        if date_format not in dict(importer.DATE_FORMATS):
            date_format = "auto"
        mode = "replace" if request.form.get("mode") == "replace" else "skip"
        errors = importer.mapping_problems(mapping)
        if not errors:
            if date_format == "auto":
                position = headers.index(mapping["order_date"])
                date_format, _note = importer.detect_date_format([r[position] for _n, r in rows[:500]
                                                                  if position < len(r)])
            result = importer.parse_file(headers, rows, mapping, date_format)
            if not result.orders:
                errors.append("None of the rows could be imported. The first problems are below.")
            else:
                record = importer.run_import(s, g.workspace, g.user, filename, result, mode)
                if saved is None:
                    saved = SavedMatch(workspace_id=g.workspace.id, headers_key=key)
                    s.add(saved)
                saved.mapping = json.dumps(mapping)
                saved.date_format = request.form.get("date_format", "auto")
                s.commit()
                uploads.remove(token, g.workspace.id)
                flash(f"Imported {record.orders_added:,} orders.")
                return redirect(url_for("data.show_import", import_id=record.id))
    else:
        mapping = json.loads(saved.mapping) if saved else importer.auto_match(headers)
        mapping = {k: v for k, v in mapping.items() if v in headers}
        date_format = saved.date_format if saved else "auto"
        mode = "skip"
    date_note = None
    if mapping.get("order_date"):
        position = headers.index(mapping["order_date"])
        _found, date_note = importer.detect_date_format([r[position] for _n, r in rows[:500] if position < len(r)])
    preview = [[importer.text_of(cell) for cell in row] for _n, row in rows[:6]]
    return render_template("data/match.html", token=token, filename=filename, headers=headers, preview=preview,
                           row_count=len(rows), mapping=mapping, fields=importer.FIELDS, errors=errors,
                           date_format=date_format, date_formats=importer.DATE_FORMATS, date_note=date_note,
                           mode=mode, remembered=saved is not None and request.method == "GET",
                           problems=(result.problems[:20] if result else [])), 400 if errors else 200


@bp.post("/import/<token>/cancel")
@login_required
def cancel(token):
    uploads.remove(token, g.workspace.id)
    return redirect(url_for("data.imports"))


@bp.post("/import/sample")
@login_required
def load_sample():
    """18 months of Kape Corner's sales, as one import (Undo takes them away again)."""
    s = db.session()
    last = today()
    record = sample.load(s, g.workspace, g.user, last - timedelta(days=SAMPLE_DAYS - 1), last)
    s.commit()
    flash(f"Loaded {record.orders_added:,} sample orders. Undo the import to take them away again.")
    return redirect(url_for("data.show_import", import_id=record.id))


@bp.get("/import/sample.csv")
@login_required
def sample_csv():
    return Response(sample.small_csv(today()), mimetype="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="tally-ho-sample.csv"'})


@bp.get("/imports/<int:import_id>")
@login_required
def show_import(import_id):
    record = workspaces.get_in_workspace(db.session(), Import, import_id, g.workspace)
    problems = importer.problems_of(record)
    return render_template("data/import.html", record=record, problems=problems[:50],
                           more_problems=max(0, record.rows_failed - 50))


@bp.get("/imports/<int:import_id>/problems.csv")
@login_required
def problems_csv(import_id):
    """The rows that couldn't be read, with the reason first, to fix and import again."""
    record = workspaces.get_in_workspace(db.session(), Import, import_id, g.workspace)
    problems = importer.problems_of(record)
    columns = []
    for problem in problems:
        for column in problem.get("values", {}):
            if column not in columns:
                columns.append(column)
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["Row", "Problem", *columns])
    for problem in problems:
        values = problem.get("values", {})
        writer.writerow([problem["row"], problem["problem"], *(safe_cell(values.get(c, "")) for c in columns)])
    return Response("﻿" + out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="import-{record.id}-problems.csv"'})


@bp.post("/imports/<int:import_id>/undo")
@login_required
def undo(import_id):
    s = db.session()
    record = workspaces.get_in_workspace(s, Import, import_id, g.workspace)
    if record.status != "done":
        abort(409, "That import was already undone.")
    removed = record.orders_added
    importer.undo_import(s, g.workspace, record)
    s.commit()
    flash(f"Undone: {removed:,} orders from {record.filename} were removed.")
    return redirect(url_for("data.imports"))


def safe_cell(value):
    """A spreadsheet runs a cell that starts with = + - @ as a formula. A quote in front stops that."""
    value = str(value)
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


# ---------- Records ----------

def _parse_day(value):
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


@bp.get("/records")
@login_required
def records():
    """Every line, newest first, 50 at a time.

    Paging uses a "keyset" (the last row's date, order and line), not OFFSET.
    OFFSET 100000 makes the database read and throw away 100,000 rows; a keyset
    jumps straight to the right place with the index. The Performance Lab times both.
    """
    s = db.session()
    q = " ".join(request.args.get("q", "").split())[:80]
    first, last = _parse_day(request.args.get("from")), _parse_day(request.args.get("to"))
    after = request.args.get("after", "")
    where = ["o.workspace_id = :ws"]
    params = {"ws": g.workspace.id, "limit": RECORDS_PER_PAGE + 1}
    if q:
        where.append("(o.order_no = :exact OR p.sku = :exact OR LOWER(p.name) LIKE :like OR LOWER(c.name) LIKE :like"
                     " OR LOWER(c.code) = :lower)")
        params.update(exact=q, like=f"%{q.lower()}%", lower=q.lower())
    if first:
        where.append("o.order_date >= :first")
        params["first"] = first
    if last:
        where.append("o.order_date <= :last")
        params["last"] = last
    try:
        cursor_day, cursor_order, cursor_line = after.split("_")
        params.update(c_day=date.fromisoformat(cursor_day), c_order=int(cursor_order), c_line=int(cursor_line))
        where.append("(o.order_date < :c_day OR (o.order_date = :c_day AND o.id < :c_order)"
                     " OR (o.order_date = :c_day AND o.id = :c_order AND l.line_no > :c_line))")
    except ValueError:
        after = ""
    sql = f"""
        SELECT o.id AS order_id, o.order_no, o.order_date, o.order_hour, l.line_no, p.sku, p.name AS product,
               l.quantity, l.unit_price, l.discount, l.revenue, c.name AS customer, r.name AS region,
               ch.name AS channel
        FROM order_lines l
        JOIN orders o ON o.id = l.order_id
        JOIN products p ON p.id = l.product_id
        LEFT JOIN customers c ON c.id = o.customer_id
        JOIN regions r ON r.id = o.region_id
        JOIN channels ch ON ch.id = o.channel_id
        WHERE {' AND '.join(where)}
        ORDER BY o.order_date DESC, o.id DESC, l.line_no
        LIMIT :limit
    """
    rows = s.execute(text(sql), params).mappings().all()
    more = len(rows) > RECORDS_PER_PAGE
    rows = rows[:RECORDS_PER_PAGE]
    next_after = None
    if more:
        tail = rows[-1]
        next_after = f"{as_date(tail['order_date']).isoformat()}_{tail['order_id']}_{tail['line_no']}"
    totals = s.execute(text("SELECT COUNT(*) AS orders, COALESCE(SUM(line_count), 0) AS lines FROM orders "
                            "WHERE workspace_id = :ws"), {"ws": g.workspace.id}).mappings().one()
    return render_template("data/records.html", rows=rows, q=q, first=first, last=last, after=after,
                           next_after=next_after, totals=totals, as_date=as_date)


def as_date(value):
    """SQLite hands back dates from hand-written SQL as text; PostgreSQL as dates."""
    return value if isinstance(value, date) or value is None else date.fromisoformat(str(value)[:10])


@bp.get("/orders/<path:order_no>")
@login_required
def order(order_no):
    s = db.session()
    found = s.scalar(select(Order).where(Order.workspace_id == g.workspace.id, Order.order_no == order_no))
    if found is None:
        abort(404)
    record = s.get(Import, found.import_id) if found.import_id else None
    return render_template("data/order.html", order=found, record=record)


# ---------- Command line ----------

@click.command("make-sample")
@with_appcontext
@click.option("--rows", default=50_000, show_default=True, help="About how many lines to make.")
@click.option("--days", default=SAMPLE_DAYS, show_default=True, help="How many days, ending today.")
@click.option("--out", type=click.Path(dir_okay=False), help="Write a CSV file instead of the database.")
def make_sample_command(rows, days, out):
    """Make Kape Corner sample sales: into the main workspace, or into a CSV file with --out.

    A million rows is for your own computer only: it is too big for Neon's free plan."""
    last = today()
    first = last - timedelta(days=days - 1)
    # About 2.1 lines per order and an average day factor of about 1.1.
    per_day = max(1, round(rows / days / 2.1 / 1.08))
    if out:
        with open(out, "w", encoding="utf-8", newline="") as handle:
            handle.write(sample.to_csv(sample.generate(first, last, per_day)))
        click.echo(f"Wrote {out}.")
        return
    s = db.session()
    workspace = workspaces.main_workspace(s)
    if workspace is None:
        raise click.ClickException("Run create-owner first.")
    record = sample.load(s, workspace, None, first, last, per_day, filename=f"Sample data ({rows:,} rows)")
    s.commit()
    click.echo(f"Added {record.orders_added:,} orders with {record.lines_added:,} lines "
               f"in {record.seconds:.1f} s ({record.lines_added / max(record.seconds, 0.001):,.0f} lines a second).")


@click.command("import-sales")
@with_appcontext
@click.argument("path", type=click.Path(exists=True, dir_okay=False))
@click.option("--replace", is_flag=True, help="Swap orders that are already here for the file's.")
@click.option("--date-format", type=click.Choice([k for k, _ in importer.DATE_FORMATS]), default="auto")
def import_sales_command(path, replace, date_format):
    """Import a big CSV or Excel file into the main workspace (columns are matched by their names)."""
    s = db.session()
    workspace = workspaces.main_workspace(s)
    if workspace is None:
        raise click.ClickException("Run create-owner first.")
    headers, rows = importer.read_table(path)
    mapping = importer.auto_match(headers)
    problems = importer.mapping_problems(mapping)
    if problems:
        raise click.ClickException(" ".join(problems) + " (Rename the columns, or import on the web page.)")
    if date_format == "auto":
        position = headers.index(mapping["order_date"])
        date_format, note = importer.detect_date_format([r[position] for _n, r in rows[:500]])
        if note:
            click.echo(note)
    result = importer.parse_file(headers, rows, mapping, date_format)
    record = importer.run_import(s, workspace, None, path.replace("\\", "/").rsplit("/", 1)[-1], result,
                                 "replace" if replace else "skip")
    s.commit()
    click.echo(f"Added {record.orders_added:,} orders ({record.lines_added:,} lines) in {record.seconds:.1f} s. "
               f"Skipped {record.orders_skipped:,}, replaced {record.orders_replaced:,}, "
               f"{record.rows_failed:,} rows had problems.")


def init_app(app):
    for command in (make_sample_command, import_sales_command):
        app.cli.add_command(command)
