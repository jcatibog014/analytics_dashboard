"""The workspace a page is about.

There is one real workspace, the owner's (made by "flask create-owner").
Every Try Demo makes its own, so a visitor's imports never reach the owner's
numbers. Every query starts from a workspace id, and a row from another
workspace gives 404.
"""
from flask import abort
from sqlalchemy import select

from .models import Workspace

MAIN_NAME = "My Business"


def main_workspace(s):
    """The real workspace (the oldest one that isn't a demo or the demo template), or None."""
    return s.scalar(select(Workspace).where(Workspace.is_demo.is_(False), Workspace.is_template.is_(False))
                    .order_by(Workspace.id).limit(1))


def create_main_workspace(s, name=MAIN_NAME):
    workspace = Workspace(name=name)
    s.add(workspace)
    s.flush()
    return workspace


def get_in_workspace(s, model, row_id, workspace):
    """A row by id, but only from this workspace: anything else is "not found"."""
    row = s.get(model, row_id)
    if row is None or workspace is None or row.workspace_id != workspace.id:
        abort(404)
    return row


def bump_data_version(workspace):
    """New numbers arrived (or left): every saved answer for this workspace is now old."""
    workspace.data_version = (workspace.data_version or 0) + 1
