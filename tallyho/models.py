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
from datetime import date, datetime

from sqlalchemy import (BigInteger, Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, SmallInteger, String,
                        Text, UniqueConstraint)
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
    # The Monday email with last week's numbers (tick.py). last_summary_week is the
    # Monday of the week it was last sent for, so it goes out once per week.
    weekly_email: Mapped[bool] = mapped_column(Boolean, default=False)
    last_summary_week: Mapped[date | None] = mapped_column(Date)
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


# ---------- The sales: dimensions (who, what, where) ----------
#
# "Dimensions" are what you group by; "measures" are what you add up.
# Each dimension is its own small table, and an order points at it by id:
# grouping by an integer is faster than by a long name, and renaming a
# product changes one row instead of thousands.

NOT_SET = "Not set"  # the region or channel of an order whose file didn't say


class Category(Base):
    __tablename__ = "categories"
    __table_args__ = (UniqueConstraint("workspace_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(80))


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("workspace_id", "sku"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    sku: Mapped[str] = mapped_column(String(64))  # the product code: the same code is the same product
    name: Mapped[str] = mapped_column(String(160))
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id", ondelete="SET NULL"), index=True)

    category: Mapped[Category | None] = relationship()


class Region(Base):
    __tablename__ = "regions"
    __table_args__ = (UniqueConstraint("workspace_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(80))


class Channel(Base):
    __tablename__ = "channels"
    __table_args__ = (UniqueConstraint("workspace_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(80))


class Customer(Base):
    __tablename__ = "customers"
    __table_args__ = (UniqueConstraint("workspace_id", "code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(120))
    # Kept up to date after every import and undo (importer.update_first_orders).
    # "New customers" in a period are the ones whose first order is in it.
    # (No foreign key on purpose: orders also point here, and two tables
    # pointing at each other make creating and deleting them awkward.)
    first_order_id: Mapped[int | None] = mapped_column(Integer)
    first_order_date: Mapped[date | None] = mapped_column(Date)

    @property
    def initial(self):
        return (self.name.strip()[:1] or "?").upper()


# ---------- The sales: facts (what happened) ----------

class Import(Base):
    """One file brought in (or the sample data). Undo deletes the orders it added."""
    __tablename__ = "imports"
    __table_args__ = {"sqlite_autoincrement": True}

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    filename: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), default="done")  # done / undone
    mode: Mapped[str] = mapped_column(String(16), default="skip")    # skip / replace (orders already here)
    rows_read: Mapped[int] = mapped_column(Integer, default=0)
    rows_failed: Mapped[int] = mapped_column(Integer, default=0)
    orders_added: Mapped[int] = mapped_column(Integer, default=0)
    lines_added: Mapped[int] = mapped_column(Integer, default=0)
    orders_skipped: Mapped[int] = mapped_column(Integer, default=0)   # already here, left alone
    orders_replaced: Mapped[int] = mapped_column(Integer, default=0)  # already here, swapped for the file's
    first_day: Mapped[date | None] = mapped_column(Date)
    last_day: Mapped[date | None] = mapped_column(Date)
    no_cost: Mapped[bool] = mapped_column(Boolean, default=False)  # the file had no cost column
    # The rows that couldn't be read, as JSON (the first few hundred): [{"row": 7, "problem": "...", "values": {...}}]
    problems: Mapped[str] = mapped_column(Text, default="[]")
    seconds: Mapped[float] = mapped_column(Float, default=0)  # how long it took (the Performance Lab shows it)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    undone_at: Mapped[datetime | None] = mapped_column(DateTime)

    user: Mapped[User | None] = relationship()


class Order(Base):
    """One order (a receipt). Its totals are the sum of its lines, worked out once at import.

    Storing the totals here repeats information that the lines already have. That is on
    purpose ("denormalized"): order counts, revenue and average order value are then a
    simple SUM over one row per order, instead of a join with every line.
    """
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("workspace_id", "order_no"),
        # The indexes follow the questions we ask. Almost every query says
        # "this workspace, between these dates", so that pair comes first.
        Index("ix_orders_workspace_day", "workspace_id", "order_date"),
        # New and returning customers: "this customer's orders, by date".
        Index("ix_orders_workspace_customer_day", "workspace_id", "customer_id", "order_date"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    import_id: Mapped[int | None] = mapped_column(ForeignKey("imports.id", ondelete="SET NULL"), index=True)
    order_no: Mapped[str] = mapped_column(String(64))
    order_date: Mapped[date] = mapped_column(Date)
    order_hour: Mapped[int | None] = mapped_column(SmallInteger)  # 0-23, when the file had times
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id", ondelete="SET NULL"))
    region_id: Mapped[int] = mapped_column(ForeignKey("regions.id", ondelete="RESTRICT"))
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id", ondelete="RESTRICT"))
    line_count: Mapped[int] = mapped_column(Integer, default=0)
    units: Mapped[int] = mapped_column(Integer, default=0)
    gross: Mapped[int] = mapped_column(BigInteger, default=0)     # quantity x price, before discounts
    discount: Mapped[int] = mapped_column(BigInteger, default=0)
    revenue: Mapped[int] = mapped_column(BigInteger, default=0)   # gross - discount: what the customer paid
    cost: Mapped[int] = mapped_column(BigInteger, default=0)

    customer: Mapped[Customer | None] = relationship()
    region: Mapped[Region] = relationship()
    channel: Mapped[Channel] = relationship()
    lines: Mapped[list["OrderLine"]] = relationship(order_by="OrderLine.line_no", passive_deletes=True)


class OrderLine(Base):
    __tablename__ = "order_lines"
    __table_args__ = (
        UniqueConstraint("order_id", "line_no"),
        Index("ix_order_lines_workspace_product", "workspace_id", "product_id"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"))
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    line_no: Mapped[int] = mapped_column(Integer)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="RESTRICT"))
    quantity: Mapped[int] = mapped_column(Integer)
    unit_price: Mapped[int] = mapped_column(BigInteger)
    unit_cost: Mapped[int] = mapped_column(BigInteger, default=0)
    discount: Mapped[int] = mapped_column(BigInteger, default=0)
    revenue: Mapped[int] = mapped_column(BigInteger)  # quantity x price - discount
    cost: Mapped[int] = mapped_column(BigInteger)     # quantity x unit cost

    product: Mapped[Product] = relationship()


class SavedMatch(Base):
    """How a file's columns were matched last time, so the same file layout is matched by itself next time."""
    __tablename__ = "saved_matches"
    __table_args__ = (UniqueConstraint("workspace_id", "headers_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    headers_key: Mapped[str] = mapped_column(String(64))  # a fingerprint of the header row
    mapping: Mapped[str] = mapped_column(Text)            # JSON: {"order_no": "Receipt #", ...}
    date_format: Mapped[str] = mapped_column(String(8), default="auto")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# ---------- Rollups: daily totals, kept next to the raw sales ----------
#
# A dashboard for "the last 12 months" would add up every order line in those
# months, every time the page loads. A rollup adds them up once, per day, when
# the sales arrive (rollups.py). The dashboard then adds 365 small rows per
# region and channel instead of hundreds of thousands of lines.
#
# Only "additive" numbers can be rolled up: sums and counts that can be added
# again (orders on Monday + orders on Tuesday = orders on both days). "How many
# different customers" can't: a customer who came on both days would count twice.
# So customer counts always come from the orders table.

class DailyOrderStat(Base):
    """One row per day, region and channel."""
    __tablename__ = "daily_order_stats"

    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    region_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    orders: Mapped[int] = mapped_column(Integer)
    units: Mapped[int] = mapped_column(Integer)
    gross: Mapped[int] = mapped_column(BigInteger)
    discount: Mapped[int] = mapped_column(BigInteger)
    revenue: Mapped[int] = mapped_column(BigInteger)
    cost: Mapped[int] = mapped_column(BigInteger)
    new_customers: Mapped[int] = mapped_column(Integer)  # orders that were a customer's very first


class DailyProductStat(Base):
    """One row per day, product, region and channel."""
    __tablename__ = "daily_product_stats"
    __table_args__ = (Index("ix_daily_product_stats_product", "workspace_id", "product_id", "day"),)

    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    product_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    region_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    orders: Mapped[int] = mapped_column(Integer)  # orders with this product: adds up over days, not over products
    units: Mapped[int] = mapped_column(Integer)
    discount: Mapped[int] = mapped_column(BigInteger)
    revenue: Mapped[int] = mapped_column(BigInteger)
    cost: Mapped[int] = mapped_column(BigInteger)


# ---------- Reports ----------

class SavedReport(Base):
    """A report from the builder, kept with a name: its rows, columns, measure, dates and filters."""
    __tablename__ = "saved_reports"
    __table_args__ = {"sqlite_autoincrement": True}

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(80))
    params: Mapped[str] = mapped_column(Text)  # JSON: the address parts that make the report
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    user: Mapped[User | None] = relationship()
