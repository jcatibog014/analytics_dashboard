"""The Alembic migrations make exactly the tables that models.py describes."""
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import PrimaryKeyConstraint, UniqueConstraint, inspect

from tallyho import create_app, db
from tallyho.db import Base


def test_upgrade_on_an_empty_database_matches_the_models(tmp_path):
    app = create_app({"TESTING": True, "DATABASE": str(tmp_path / "fresh.sqlite"), "DATABASE_URL": None})
    with app.app_context():
        db.upgrade_db()
        db.upgrade_db()  # running it again changes nothing
        with db.engine().connect() as connection:
            context = MigrationContext.configure(connection, opts={"compare_type": True})
            differences = compare_metadata(context, Base.metadata)
            tables = set(inspect(connection).get_table_names())
    # Nothing left for "alembic revision --autogenerate" to write: the
    # migrations and the models agree. If this fails after changing
    # models.py, make a new migration (see AGENTS.md).
    assert differences == []
    assert {"workspaces", "users", "alembic_version"} <= tables


def test_upgrade_db_command(tmp_path):
    app = create_app({"TESTING": True, "DATABASE": str(tmp_path / "cli.sqlite"), "DATABASE_URL": None})
    result = app.test_cli_runner().invoke(args=["upgrade-db"])
    assert result.exit_code == 0, result.output
    assert "up to date" in result.output


# Parents with only a handful of rows per workspace: checking them reads the child table a
# few times at most, so their links don't need an index of their own.
FEW_ROWS = {"regions", "channels", "users", "categories", "alert_rules", "imports"}


def test_links_to_many_rows_have_an_index():
    """Deleting a workspace (a demo) deletes thousands of customers and products. For each one
    the database looks for rows that point at it, which reads the whole child table unless an
    index starts with that column. That once held the database locked for minutes."""
    missing = []
    for table in Base.metadata.sorted_tables:
        firsts = {next(iter(index.columns)).name for index in table.indexes}
        firsts |= {next(iter(c.columns)).name for c in table.constraints
                   if isinstance(c, (UniqueConstraint, PrimaryKeyConstraint)) and c.columns}
        for key in table.foreign_keys:
            if key.column.table.name not in FEW_ROWS and key.parent.name not in firsts:
                missing.append(f"{table.name}.{key.parent.name}")
    assert missing == []
