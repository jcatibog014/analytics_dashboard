"""Reports: the builder (a pivot table), saved reports, cohorts, the 80/20 list, downloads."""
import json
from flask import Blueprint, Response, abort, flash, g, redirect, render_template, request, url_for
from sqlalchemy import select

from .. import charts, db, ranges, reports, scope, workspaces
from ..auth import login_required
from ..formatting import date_range
from ..models import SavedReport

bp = Blueprint("reports", __name__)

REPORT_KEYS = ("rows", "columns", "measure") + ranges.RANGE_KEYS


def report_args():
    """The address parts that make the current report (to save or download it)."""
    return {k: request.args[k] for k in REPORT_KEYS if request.args.get(k)}


@bp.get("/reports")
@login_required
def index():
    saved = db.session().scalars(select(SavedReport).where(SavedReport.workspace_id == g.workspace.id)
                                 .order_by(SavedReport.name)).all()
    return render_template("reports/index.html", saved=saved, describe=describe)


def describe(report):
    """ "Revenue by Category and Month, Last 90 days" """
    params = json.loads(report.params or "{}")
    spec = reports.Spec.from_args(params)
    text = f"{reports.MEASURE_BY_KEY[spec.measure][1]} by {reports.DIMENSION_NAMES[spec.rows].lower()}"
    if spec.columns:
        text += f" and {reports.DIMENSION_NAMES[spec.columns].lower()}"
    choice = ranges.choose(params)
    return f"{text}, {choice.label.lower() if choice.key != 'custom' else date_range(choice.period.first, choice.period.last)}"


@bp.get("/reports/build")
@login_required
def builder():
    s = db.session()
    sc = scope.current()
    spec = reports.Spec.from_args(request.args)
    result = reports.pivot(s, sc.filters, spec)
    title = f"{reports.MEASURE_BY_KEY[spec.measure][1]} by {reports.DIMENSION_NAMES[spec.rows]}" + \
            (f" and {reports.DIMENSION_NAMES[spec.columns]}" if spec.columns else "")
    file_title = f"{g.workspace.name}: {title}, {date_range(sc.filters.first, sc.filters.last)}"
    wanted = request.args.get("format")
    if wanted == "csv":
        return Response(reports.to_csv(result, spec, file_title), mimetype="text/csv",
                        headers={"Content-Disposition": 'attachment; filename="tally-ho-report.csv"'})
    if wanted == "xlsx":
        return Response(reports.to_xlsx(result, spec, file_title),
                        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": 'attachment; filename="tally-ho-report.xlsx"'})
    table = reports.pivot_table(result, spec)
    # The biggest cell, for the shading that makes a pivot easy to scan.
    biggest = max([v or 0 for row in table["body"] for v in row["values"]] or [0])
    saved = None
    if request.args.get("saved"):
        try:
            saved = workspaces.get_in_workspace(s, SavedReport, int(request.args["saved"]), g.workspace)
        except ValueError:
            saved = None
    keep = {k: request.args.get(k) for k in ("rows", "columns", "measure")}
    return render_template("reports/builder.html", sc=sc, spec=spec, result=result, table=table, title=title,
                           biggest=biggest, heat=charts.heat_level, saved=saved, keep=keep,
                           dimensions=reports.DIMENSIONS, column_dimensions=reports.COLUMN_DIMENSIONS,
                           measures=reports.MEASURES, args=report_args())


@bp.post("/reports/save")
@login_required
def save():
    s = db.session()
    name = " ".join(request.form.get("name", "").split())[:80]
    try:
        params = json.loads(request.form.get("params", "{}"))
    except ValueError:
        abort(400, "That report can't be saved.")
    params = {k: str(v)[:40] for k, v in params.items() if k in REPORT_KEYS}
    if not name:
        flash("Give the report a name to save it.", "error")
        return redirect(url_for("reports.builder", **params))
    report = SavedReport(workspace_id=g.workspace.id, user_id=g.user.id, name=name, params=json.dumps(params))
    s.add(report)
    s.commit()
    flash(f"Saved “{name}”. It's under Reports.")
    return redirect(url_for("reports.show", report_id=report.id))


@bp.get("/reports/<int:report_id>")
@login_required
def show(report_id):
    report = workspaces.get_in_workspace(db.session(), SavedReport, report_id, g.workspace)
    return redirect(url_for("reports.builder", saved=report.id, **json.loads(report.params or "{}")))


@bp.post("/reports/<int:report_id>/delete")
@login_required
def delete(report_id):
    s = db.session()
    report = workspaces.get_in_workspace(s, SavedReport, report_id, g.workspace)
    s.delete(report)
    s.commit()
    flash(f"Deleted “{report.name}”.")
    return redirect(url_for("reports.index"))


@bp.get("/reports/cohorts")
@login_required
def cohorts():
    """Customers by their first month, and how many came back each month after."""
    s = db.session()
    args = request.args.to_dict()
    args.setdefault("range", "12m")
    choice = ranges.choose(args, analytics_bounds())
    rows = reports.cohorts(s, g.workspace.id, g.workspace.data_version, choice.period.first, choice.period.last)
    average = []
    for age in range(1, 13):
        shares = [row["ages"][age]["share"] for row in rows if row["ages"][age] is not None and row["size"]]
        average.append(sum(shares) / len(shares) if shares else None)
    return render_template("reports/cohorts.html", rows=rows, choice=choice, average=average)


def analytics_bounds():
    from .. import analytics
    return analytics.bounds(db.session(), g.workspace.id, g.workspace.data_version)


@bp.get("/reports/pareto")
@login_required
def pareto():
    """Which few products bring most of the revenue (the 80/20 rule)."""
    s = db.session()
    sc = scope.current()
    result = reports.pareto(s, sc.filters)
    return render_template("reports/pareto.html", sc=sc, result=result)

