"""The app's tables, as Python classes (SQLAlchemy ORM).

Every row belongs to a workspace. There is one real workspace (the owner's,
made by "flask create-owner"), and every Try Demo gets its own, so a visitor
never sees or changes the owner's sales.

Money is always whole centavos (an int): 123450 means P1,234.50. Floats are
never used for money, because 0.1 + 0.2 isn't 0.3 for a computer.

The numbers on the dashboard don't come from these classes: analytics.py asks
the database with hand-written SQL (GROUP BY, window functions...). The ORM is
for the app's own rows: people, imports, saved reports, goals.
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base
from .dates import utcnow


class Workspace(Base):
    """One business's sales: the owner's, or one visitor's demo."""
    __tablename__ = "workspaces"
    # AUTOINCREMENT: SQLite would give a deleted demo's id to the next demo, and an
    # old demo cookie could then open someone else's. Now an id is never reused.
    __table_args__ = {"sqlite_autoincrement": True}

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    demo_expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    # The demo's sample sales live in one hidden template workspace. Each Try Demo
    # copies it with INSERT ... SELECT (demo.py), which is far faster than making it again.
    is_template: Mapped[bool] = mapped_column(Boolean, default=False)
    # +1 after every import or undo. Saved answers (cache.py) carry the number
    # they were made with, so an old answer is never shown after new data arrives.
    data_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    users: Mapped[list["User"]] = relationship(back_populates="workspace", passive_deletes=True)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("workspace_id", "email"), {"sqlite_autoincrement": True})

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    email: Mapped[str] = mapped_column(String(254))
    password_hash: Mapped[str] = mapped_column(String(255))
    # In the login cookie too. Changing the password adds 1, so every old cookie stops working.
    session_version: Mapped[int] = mapped_column(Integer, default=1)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    workspace: Mapped[Workspace] = relationship(back_populates="users")

    @property
    def is_demo(self):
        return self.workspace.is_demo

    @property
    def initial(self):
        return (self.name.strip()[:1] or "?").upper()

    @property
    def first_name(self):
        return self.name.split()[0] if self.name.strip() else self.name
