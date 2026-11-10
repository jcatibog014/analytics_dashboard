# Tally Ho! — notes for coding agents

An analytics dashboard: import sales orders (CSV or Excel), then see KPIs, trends and reports over any
date range. Learning goals: **SQL aggregation, performance, data visualization**. The plan and the
progress list are in `docs/PLAN.md`.

## Commands (Windows, PowerShell, from the project folder)

```powershell
py -3.13 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\flask --app tallyho upgrade-db          # make or update the tables
.venv\Scripts\flask --app tallyho create-owner        # the owner's login (asks for a password)
.venv\Scripts\flask --app tallyho run --debug --port 5011
.venv\Scripts\python -m pytest                        # all tests (fresh SQLite per test)
.venv\Scripts\alembic revision --autogenerate -m "what changed" --rev-id 000N   # after changing models.py
```

`TEST_DATABASE_URL` runs the tests on PostgreSQL instead. Point it only at an **empty** Neon branch: every
test drops and makes the tables again.

## Architecture

- Flask app factory in `tallyho/__init__.py`; blueprints in `tallyho/views/`.
- `db.py`: one SQLAlchemy engine, one session per request (`db.session()`), Alembic migrations in
  `tallyho/migrations/` run by `flask upgrade-db` (it turns SQLite foreign keys off while migrating, so a
  table rebuild can't cascade-delete rows).
- `models.py`: the ORM tables. **Money is whole centavos (int).** Dates people see are Asia/Manila
  (`dates.py`); moments in the database are naive UTC.
- Every row belongs to a **workspace**: the owner's one (`workspaces.main_workspace`), one per Try Demo,
  and the hidden demo template. Every query filters by `workspace_id`.
- `auth.py`: login, the 5-tries lock, CSRF on every POST, Settings, `create-owner` / `set-password`.
- Templates: `base.html`, `_sidebar.html` (a link appears only when its endpoint exists: `has_page`),
  `_macros.html`, `_icons.html` (inline SVG icons and the logo). `static/style.css` holds the
  "Clean analyst" tokens for light and dark (two dark blocks: device setting and Settings choice — change both).

## Rules

- Strict CSP: no inline `<script>`, no `style=""` attributes, no outside files. Every page works without JS.
- Title Case for page names and headings, sentence case for buttons. Aligned, equal-height controls; the
  main button is last on the right. Theme and Log out live in Settings only. Minimal emoji.
- Never put user input into SQL text: hand-written SQL uses bound parameters (`:name`).
- A new model change needs a migration; `tests/test_migrations.py` fails if they differ.
- Commit messages and PR bodies are written to files (`git commit -F`, `gh pr create --body-file`).
