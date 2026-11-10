"""Files waiting between "upload" and "import".

Importing has two pages: pick a file, then match its columns. The file waits
in instance/uploads/ in between, under a random name that starts with the
workspace id, so nobody can reach another workspace's file by guessing.
Files are deleted after the import, and any left behind after a day.
"""
import json
import re
import secrets
import time
from pathlib import Path

from flask import current_app

from .importer import ALLOWED_SUFFIXES

LIFETIME_SECONDS = 24 * 3600
TOKEN_PATTERN = re.compile(r"^[a-f0-9]{24}$")


def folder():
    path = Path(current_app.instance_path) / "uploads"
    path.mkdir(parents=True, exist_ok=True)
    return path


def clean_old():
    cutoff = time.time() - LIFETIME_SECONDS
    for path in folder().iterdir():
        if path.is_file() and path.stat().st_mtime < cutoff:
            path.unlink(missing_ok=True)


def save(storage, workspace_id):
    """Keep an uploaded file (a werkzeug FileStorage). Returns its token, or raises ValueError."""
    name = Path(storage.filename or "").name
    suffix = Path(name).suffix.lower()
    if suffix == ".xls":
        raise ValueError("Old Excel files (.xls) can't be read. In Excel, choose Save As and pick .xlsx or CSV.")
    if suffix not in ALLOWED_SUFFIXES:
        raise ValueError("Choose a CSV file (.csv) or an Excel file (.xlsx).")
    clean_old()
    token = secrets.token_hex(12)
    base = folder() / f"{workspace_id}-{token}"
    storage.save(base.with_suffix(suffix))
    if base.with_suffix(suffix).stat().st_size == 0:
        base.with_suffix(suffix).unlink()
        raise ValueError("That file is empty.")
    base.with_suffix(".json").write_text(json.dumps({"filename": name, "suffix": suffix}), encoding="utf-8")
    return token


def find(token, workspace_id):
    """(path, original filename) for this workspace's token, or None."""
    if not TOKEN_PATTERN.match(token or ""):
        return None
    base = folder() / f"{workspace_id}-{token}"
    info_path = base.with_suffix(".json")
    if not info_path.exists():
        return None
    info = json.loads(info_path.read_text(encoding="utf-8"))
    path = base.with_suffix(info["suffix"])
    return (path, info["filename"]) if path.exists() else None


def remove(token, workspace_id):
    found = find(token, workspace_id)
    if found:
        found[0].unlink(missing_ok=True)
    (folder() / f"{workspace_id}-{token}.json").unlink(missing_ok=True)
