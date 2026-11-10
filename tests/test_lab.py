"""The Performance Lab, the query timer, and deleting a whole workspace."""
import json
from datetime import timedelta

import pytest
from sqlalchemy import func, select, text

from tallyho import analytics, db, lab, sample, workspaces
from tallyho.dates import today
from tallyho.models import Order, Workspace

from .conftest import post


@pytest.fixture
def owner(app, owner):
    """The owner, with Developer mode on (the Lab and the timings show only then)."""
    from tallyho.models import User
    from .conftest import owner_id
    with app.app_context():
        db.session().get(User, owner_id(app)).developer_mode = True
        db.session().commit()
    return owner


@pytest.fixture
def stocked(app):
    with app.app_context():
        s = db.session()
        sample.load(s, workspaces.main_workspace(s), None, today() - timedelta(days=90), today(), orders_per_day=8)
        s.commit()
    return app


def test_every_page_says_how_hard_the_database_worked(stocked, owner):
    page = owner.get("/").get_data(as_text=True)
    assert "queries" in page and "ms in the database" in page and "Performance Lab" in page


def test_lab_page(stocked, owner):
    page = owner.get("/lab?range=90d").get_data(as_text=True)
    for text_value in ("Raw Tables or Rollups?", "KPI totals", "Top 10 products", "What an Index Does",
                       "OFFSET", "Keyset", "The Cache", "Import Speed", "daily_order_stats"):
        assert text_value in page, text_value
    assert "pill-bad" not in page  # every rollup answer is the same as the raw one
    assert "USING INDEX" in page  # SQLite's plan with the index
    assert "SCAN o" in page or "SCAN orders" in page  # ... and without it


def test_lab_measurements(stocked):
    with stocked.app_context():
        s = db.session()
        workspace = workspaces.main_workspace(s)
        f = analytics.Filters(workspace.id, workspace.data_version, today() - timedelta(days=60), today())
        for row in lab.sources(s, f, repeat=1):
            assert row["same"], row["name"]
            assert row["rollup_rows"] < row["raw_rows"]
        paging = lab.paging(s, workspace.id)
        assert paging["same"] and paging["offset"] > 0
        experiment = lab.index_experiment(s, f)
        assert any("USING INDEX" in line for line in experiment["with_plan"])
        assert not any("USING INDEX ix_orders" in line for line in experiment["without_plan"])


def test_clear_the_cache(stocked, owner):
    owner.get("/")
    post(owner, "/lab/cache/clear")
    page = owner.get("/lab").get_data(as_text=True)
    assert "The cache is empty" in page


def test_empty_lab(owner):
    assert "Nothing to measure yet" in owner.get("/lab").get_data(as_text=True)


def test_benchmark_command(app, tmp_path):
    app.config["BENCHMARK_FILE"] = str(tmp_path / "benchmarks.json")
    result = app.test_cli_runner().invoke(args=["benchmark", "--rows", "3000", "--days", "60"])
    assert result.exit_code == 0, result.output
    saved = json.loads((tmp_path / "benchmarks.json").read_text(encoding="utf-8"))
    assert saved["lines"] > 1000 and len(saved["sources"]) == 4
    assert "| KPI totals |" in (tmp_path / "BENCHMARKS.md").read_text(encoding="utf-8")
    with app.app_context():
        s = db.session()
        assert s.scalar(select(func.count()).select_from(Workspace).where(Workspace.name == "Benchmark")) == 0
        assert s.scalar(select(func.count()).select_from(Order)) == 0  # deleted with its workspace


def test_a_workspace_with_sales_can_be_deleted_whole(stocked):
    with stocked.app_context():
        s = db.session()
        workspace = workspaces.main_workspace(s)
        s.execute(text("DELETE FROM workspaces WHERE id = :id"), {"id": workspace.id})
        s.commit()
        for table in ("orders", "order_lines", "products", "regions", "daily_order_stats", "imports"):
            assert s.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() == 0, table


def test_without_developer_mode_the_lab_and_timings_hide(app, client):
    from .conftest import log_in_as, owner_id
    plain = log_in_as(client, owner_id(app))
    page = plain.get("/").get_data(as_text=True)
    assert "ms in the database" not in page and "Performance Lab" not in page
    response = plain.get("/lab")
    assert response.status_code == 302 and response.headers["Location"].endswith("/settings")
    post(plain, "/settings", {"action": "developer", "developer_mode": "1"})
    assert plain.get("/lab").status_code == 200
    assert "Performance Lab" in plain.get("/").get_data(as_text=True)
