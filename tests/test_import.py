"""Importing sales: reading values, matching columns, grouping rows into orders, skip/replace, undo."""
import io
import re
from datetime import date, datetime

import pytest
from sqlalchemy import func, select

from tallyho import db, importer, workspaces
from tallyho.models import Customer, Import, Order, OrderLine, Product, Region

from .conftest import main_workspace_id, post

CSV = """Receipt #,Sold On,Item Code,Item,Category,Qty,Price,Cost,Customer ID,Customer Name,Branch,Channel
R-1,2026-03-01 09:15,BN-1,House Beans,Coffee Beans,2,320.00,150,C-1,Maria Santos,Metro Manila,Store
R-1,2026-03-01 09:15,PS-1,Ensaymada,Pastries,1,75,28,C-1,Maria Santos,Metro Manila,Store
R-2,2026-03-02 14:00,BN-1,House Beans,Coffee Beans,1,"1,320.50",150,C-2,Jose Cruz,Cebu,Website
R-3,2026-03-03,PS-1,Ensaymada,Pastries,3,75,28,C-1,Maria Santos,Metro Manila,Store
"""


def upload(client, content, filename="sales.csv"):
    data = content.encode("utf-8") if isinstance(content, str) else content
    response = post(client, "/import/upload", {"file": (io.BytesIO(data), filename)},
                    content_type="multipart/form-data")
    assert response.status_code == 302, response.get_data(as_text=True)
    return response.headers["Location"].rsplit("/", 1)[-1]


def import_csv(client, content=CSV, mode="skip", **mapping):
    token = upload(client, content)
    page = client.get(f"/import/{token}")
    assert page.status_code == 200
    # Send what the page pre-selected (the auto match), plus any changes.
    form = {}
    for key, options in re.findall(r'<select name="(\w+)">(.*?)</select>', page.get_data(as_text=True), re.S):
        chosen = re.search(r'<option value="([^"]*)" selected', options)
        if chosen:
            form[key] = chosen.group(1)
    form.update(mode=mode, date_format="auto", action="import", **mapping)
    return post(client, f"/import/{token}", form)


# ---------- Reading values ----------

def test_dates_in_many_shapes():
    assert importer.parse_date("2026-03-25", "ymd") == (date(2026, 3, 25), None)
    assert importer.parse_date("2026-03-25T14:05:00", "ymd") == (date(2026, 3, 25), 14)
    assert importer.parse_date("03/25/2026 2:30 PM", "mdy") == (date(2026, 3, 25), 14)
    assert importer.parse_date("25/03/2026", "dmy") == (date(2026, 3, 25), None)
    assert importer.parse_date("25/03/26", "dmy") == (date(2026, 3, 25), None)
    assert importer.parse_date(datetime(2026, 3, 25, 8, 30), "ymd") == (date(2026, 3, 25), 8)
    assert importer.parse_date("46106", "ymd") == (date(2026, 3, 25), None)  # an Excel day number
    for bad in ("", "yesterday", "2026-02-30", "13/13/2026"):
        with pytest.raises(importer.Problem):
            importer.parse_date(bad, "mdy")


def test_date_format_is_found_from_the_samples():
    assert importer.detect_date_format(["2026-03-25"]) == ("ymd", None)
    assert importer.detect_date_format(["03/04/2026", "25/03/2026"])[0] == "dmy"
    assert importer.detect_date_format(["03/04/2026", "03/25/2026"])[0] == "mdy"
    fmt, note = importer.detect_date_format(["03/04/2026", "05/06/2026"])
    assert fmt == "mdy" and "two ways" in note


def test_money_and_quantity():
    assert importer.parse_money("₱1,234.50", "Price") == 123450
    assert importer.parse_money("PHP 99.999", "Price") == 10000
    assert importer.parse_money(12.5, "Price") == 1250
    assert importer.parse_money("", "Price") is None
    with pytest.raises(importer.Problem):
        importer.parse_money("-5", "Price")
    with pytest.raises(importer.Problem):
        importer.parse_money("free", "Price")
    assert importer.parse_quantity("2") == 2
    assert importer.parse_quantity(3.0) == 3
    for bad in ("0", "1.5", "two", ""):
        with pytest.raises(importer.Problem):
            importer.parse_quantity(bad)


def test_columns_are_matched_by_their_names():
    headers = ["Receipt #", "Sold On", "Item Code", "Item", "Qty", "Price", "Cost", "Customer ID", "Branch", "Via"]
    matched = importer.auto_match(headers)
    assert matched["order_no"] == "Receipt #"
    assert matched["order_date"] == "Sold On"
    assert matched["sku"] == "Item Code"
    assert matched["product"] == "Item"
    assert matched["quantity"] == "Qty"
    assert matched["unit_price"] == "Price"
    assert matched["unit_cost"] == "Cost"
    assert matched["customer"] == "Customer ID"
    assert matched["region"] == "Branch"
    assert matched["channel"] == "Via"
    assert importer.mapping_problems(matched) == []
    assert importer.mapping_problems({"order_no": "A"})  # lots missing


def test_rows_become_orders_and_a_bad_row_leaves_its_order_out():
    headers = ["Order", "Date", "SKU", "Qty", "Price"]
    rows = [(2, ["A-1", "2026-01-05", "X", "2", "10"]),
            (3, ["A-1", "2026-01-05", "Y", "1", "5"]),
            (4, ["A-2", "2026-01-06", "X", "two", "10"]),   # bad quantity
            (5, ["A-2", "2026-01-06", "Y", "1", "5"]),      # fine, but its order has a bad row
            (6, ["A-3", "2026-01-07", "X", "1", "10"]),
            (7, ["A-3", "2026-01-08", "Y", "1", "10"])]     # a different date for the same order
    result = importer.parse_file(headers, rows, importer.auto_match(headers), "ymd")
    assert [o.order_no for o in result.orders] == ["A-1"]
    assert [line.line_no for line in result.orders[0].lines] == [1, 2]
    assert result.orders[0].lines[0].revenue == 2000
    assert result.orders_left_out == 2
    assert {p["row"] for p in result.problems} == {4, 7}
    assert all("was left out" in p["problem"] for p in result.problems)


def test_line_total_and_percent_discount():
    headers = ["Order", "Date", "Product", "Qty", "Total", "Discount"]
    rows = [(2, ["A-1", "2026-01-05", "Mug", "3", "300", "10%"])]
    result = importer.parse_file(headers, rows, importer.auto_match(headers), "ymd")
    line = result.orders[0].lines[0]
    assert (line.unit_price, line.discount, line.revenue) == (10000, 3000, 27000)
    assert line.sku == "Mug"  # no code: the name is the code
    assert result.no_cost


# ---------- The web pages ----------

def test_import_a_csv(app, owner):
    response = import_csv(owner)
    assert response.status_code == 302
    with app.app_context():
        s = db.session()
        orders = {o.order_no: o for o in s.scalars(select(Order))}
        assert set(orders) == {"R-1", "R-2", "R-3"}
        r1 = orders["R-1"]
        assert (r1.line_count, r1.units, r1.revenue, r1.cost, r1.order_hour) == (2, 3, 71500, 32800, 9)
        assert orders["R-2"].revenue == 132050
        assert orders["R-3"].order_hour is None
        assert s.scalar(select(func.count()).select_from(OrderLine)) == 4
        assert {r.name for r in s.scalars(select(Region))} == {"Metro Manila", "Cebu"}
        maria = s.scalar(select(Customer).where(Customer.code == "C-1"))
        assert maria.first_order_date == date(2026, 3, 1)
        assert maria.first_order_id == r1.id
        record = s.scalar(select(Import))
        assert (record.orders_added, record.lines_added, record.rows_failed) == (3, 4, 0)
        assert (record.first_day, record.last_day) == (date(2026, 3, 1), date(2026, 3, 3))
        assert s.get(workspaces.Workspace, main_workspace_id(app)).data_version == 2
    page = owner.get(response.headers["Location"]).get_data(as_text=True)
    assert "Orders added" in page and "Revert import" in page


def test_the_same_file_twice_skips_or_replaces(app, owner):
    import_csv(owner)
    import_csv(owner)
    with app.app_context():
        s = db.session()
        assert s.scalar(select(func.count()).select_from(Order)) == 3
        second = s.scalars(select(Import).order_by(Import.id.desc())).first()
        assert (second.orders_added, second.orders_skipped) == (0, 3)
    changed = CSV.replace("R-3,2026-03-03,PS-1,Ensaymada,Pastries,3,", "R-3,2026-03-03,PS-1,Ensaymada,Pastries,5,")
    import_csv(owner, changed, mode="replace")
    with app.app_context():
        s = db.session()
        assert s.scalar(select(func.count()).select_from(Order)) == 3
        assert s.scalar(select(Order.units).where(Order.order_no == "R-3")) == 5
        assert s.scalar(select(func.count()).select_from(OrderLine)) == 4  # the old lines went with their orders


def test_undo_removes_the_orders_and_mends_first_orders(app, owner):
    import_csv(owner)
    later = "Order,Date,SKU,Qty,Price,Customer\nE-1,2026-02-01,BN-1,1,320,C-1\n"
    import_csv(owner, later)
    with app.app_context():
        s = db.session()
        assert s.scalar(select(Customer.first_order_date).where(Customer.code == "C-1")) == date(2026, 2, 1)
        record = s.scalars(select(Import).order_by(Import.id.desc())).first()
        record_id = record.id
    response = post(owner, f"/imports/{record_id}/undo")
    assert response.status_code == 302
    with app.app_context():
        s = db.session()
        assert s.scalar(select(Order.id).where(Order.order_no == "E-1")) is None
        assert s.scalar(select(Customer.first_order_date).where(Customer.code == "C-1")) == date(2026, 3, 1)
        assert s.get(Import, record_id).status == "undone"
    assert post(owner, f"/imports/{record_id}/undo").status_code == 409


def test_problem_rows_are_reported_and_downloadable(app, owner):
    content = CSV + "R-9,not a date,BN-1,House Beans,Coffee Beans,1,320,150,C-3,Ana,Davao,Store\n" \
                    "R-10,2026-03-04,BN-1,House Beans,Coffee Beans,1,=1+1,150,C-3,Ana,Davao,Store\n"
    response = import_csv(owner, content)
    page = owner.get(response.headers["Location"]).get_data(as_text=True)
    assert "Rows With Problems" in page and "not a date" in page
    with app.app_context():
        record_id = db.session().scalar(select(Import.id))
    download = owner.get(f"/imports/{record_id}/problems.csv").get_data(as_text=True)
    assert download.startswith("﻿Row,Problem")
    assert ",'=1+1," in download  # a formula is made harmless for spreadsheets


def test_nothing_good_shows_the_problems_on_the_match_page(owner):
    response = import_csv(owner, "Order,Date,SKU,Qty,Price\nA,never,X,1,5\n")
    assert response.status_code == 400
    assert "None of the rows could be imported" in response.get_data(as_text=True)


def test_missing_columns_are_explained(owner):
    response = import_csv(owner, "Order,Date,SKU,Qty\nA,2026-01-01,X,1\n")
    assert response.status_code == 400
    assert "Unit price or Line total" in response.get_data(as_text=True)


def test_excel_files(app, owner):
    from openpyxl import Workbook
    book = Workbook()
    sheet = book.active
    sheet.append(["Order No", "Date", "SKU", "Quantity", "Unit Price"])
    sheet.append(["X-1", datetime(2026, 4, 2, 10, 30), "MUG", 2, 390])
    sheet.append([1002, datetime(2026, 4, 3), "MUG", 1, 390.5])
    buffer = io.BytesIO()
    book.save(buffer)
    token = upload(owner, buffer.getvalue(), "sales.xlsx")
    page = owner.get(f"/import/{token}").get_data(as_text=True)
    assert "X-1" in page
    post(owner, f"/import/{token}", {"order_no": "Order No", "order_date": "Date", "sku": "SKU",
                                     "quantity": "Quantity", "unit_price": "Unit Price", "action": "import"})
    with app.app_context():
        orders = {o.order_no: o for o in db.session().scalars(select(Order))}
        assert orders["X-1"].order_hour == 10 and orders["X-1"].revenue == 78000
        assert orders["1002"].revenue == 39050  # 1002.0 from Excel is "1002"


def test_unsupported_files_are_refused(owner):
    for name, problem in (("old.xls", "Old Excel"), ("photo.png", "Choose a CSV")):
        response = post(owner, "/import/upload", {"file": (io.BytesIO(b"x"), name)},
                        content_type="multipart/form-data", follow_redirects=True)
        assert problem in response.get_data(as_text=True)


def test_column_matches_are_remembered(owner):
    content = "Ref,When,Thing,How Many,Each\nA,2026-01-01,X,1,5\n"
    token = upload(owner, content)
    post(owner, f"/import/{token}", {"order_no": "Ref", "order_date": "When", "product": "Thing",
                                     "quantity": "How Many", "unit_price": "Each", "action": "import"})
    token = upload(owner, content.replace("A,", "B,"))
    page = owner.get(f"/import/{token}").get_data(as_text=True)
    assert "columns are matched the same way" in page
    assert '<option value="How Many" selected>' in page


def test_another_workspace_cannot_see_an_import(app, owner):
    import_csv(owner)
    from tallyho import auth
    from tallyho.models import Workspace
    with app.app_context():
        s = db.session()
        other = Workspace(name="Other")
        s.add(other)
        s.flush()
        other_user = auth.create_user(s, other, "Other Person", "other@example.com", "correct horse")
        s.commit()
        record_id = s.scalar(select(Import.id))
        other_id = other_user.id
    from .conftest import log_in_as
    stranger = log_in_as(app.test_client(), other_id)
    assert stranger.get(f"/imports/{record_id}").status_code == 404
    assert post(stranger, f"/imports/{record_id}/undo").status_code == 404
    assert stranger.get("/orders/R-1").status_code == 404


def test_upload_tokens_belong_to_their_workspace(owner):
    token = upload(owner, CSV)
    assert owner.get(f"/import/{token[:-1]}x", follow_redirects=True).status_code == 200  # gone, back to Import
    assert owner.get("/import/../../etc", follow_redirects=True).status_code in (200, 404)


# ---------- Records ----------

def test_records_search_and_keyset_paging(app, owner):
    from tallyho.views import data
    lines = ["Order,Date,SKU,Qty,Price"] + [f"O-{n:03d},2026-01-{1 + n % 28:02d},SKU-{n % 3},1,10"
                                           for n in range(1, 121)]
    import_csv(owner, "\n".join(lines) + "\n")
    first = owner.get("/records").get_data(as_text=True)
    assert first.count("<tr>") == data.RECORDS_PER_PAGE + 1  # + the header row
    import re
    after = re.search(r'after=([\w-]+)', first).group(1)
    second = owner.get(f"/records?after={after}").get_data(as_text=True)
    third_after = re.search(r'after=([\w-]+)', second).group(1)
    third = owner.get(f"/records?after={third_after}").get_data(as_text=True)
    seen = set(re.findall(r">(O-\d{3})<", first + second + third))
    assert len(seen) == 120  # every line once, none twice
    found = owner.get("/records?q=O-007").get_data(as_text=True)
    assert "O-007" in found and "O-008" not in found
    dated = owner.get("/records?from=2026-01-05&to=2026-01-05").get_data(as_text=True)
    assert set(re.findall(r">(O-\d{3})<", dated)) == {"O-004", "O-032", "O-060", "O-088", "O-116"}


def test_order_page(owner):
    import_csv(owner)
    page = owner.get("/orders/R-1").get_data(as_text=True)
    assert "House Beans" in page and "Ensaymada" in page and "Maria Santos" in page
    assert "₱715.00" in page


# ---------- Sample data ----------

def test_sample_csv_downloads_and_imports(app, owner):
    content = owner.get("/import/sample.csv").get_data(as_text=True)
    assert content.startswith("Order No,Line,Date,SKU")
    response = import_csv(owner, content)
    assert response.status_code == 302
    with app.app_context():
        record = db.session().scalar(select(Import))
        assert record.rows_failed == 0 and record.orders_added > 1000


def test_load_sample_data_and_undo_it(app, owner):
    response = post(owner, "/import/sample")
    assert response.status_code == 302
    with app.app_context():
        s = db.session()
        orders = s.scalar(select(func.count()).select_from(Order))
        assert orders > 10000
        assert s.scalar(select(func.count()).select_from(Product)) == 30
        record_id = s.scalar(select(Import.id))
    post(owner, f"/imports/{record_id}/undo")
    with app.app_context():
        assert db.session().scalar(select(func.count()).select_from(Order)) == 0


def test_the_sample_is_the_same_every_time():
    from tallyho import sample
    a = list(sample.generate(date(2026, 1, 1), date(2026, 1, 31)))
    b = list(sample.generate(date(2026, 1, 1), date(2026, 1, 31)))
    assert [(o.order_no, o.customer_code, len(o.lines)) for o in a] == \
           [(o.order_no, o.customer_code, len(o.lines)) for o in b]
    saturdays = [o for o in a if o.order_date.weekday() == 5]
    mondays = [o for o in a if o.order_date.weekday() == 0]
    assert len(saturdays) > len(mondays)  # weekends are busier


def test_command_line_sample_and_import(app, tmp_path):
    runner = app.test_cli_runner()
    out = tmp_path / "big.sample.csv"
    result = runner.invoke(args=["make-sample", "--rows", "3000", "--days", "30", "--out", str(out)])
    assert result.exit_code == 0, result.output
    result = runner.invoke(args=["import-sales", str(out)])
    assert result.exit_code == 0, result.output
    assert "orders" in result.output
    result = runner.invoke(args=["make-sample", "--rows", "2000", "--days", "20"])
    assert result.exit_code == 0, result.output
    assert "lines a second" in result.output


def test_an_import_shows_its_own_dates(app, owner):
    import_csv(owner)  # March 1-3, customers C1, C2, C3
    import_csv(owner, "Order,Date,SKU,Qty,Price,Customer\nN-1,2026-04-20,BN-1,1,320,C-1\n")
    with app.app_context():
        record = db.session().scalars(select(Import).order_by(Import.id.desc())).first()
        # Not March 1 (the returning customer's first day): only this file's own date.
        assert (record.first_day, record.last_day) == (date(2026, 4, 20), date(2026, 4, 20))


def test_review_step_says_what_will_happen_before_saving(app, owner):
    import_csv(owner)  # R-1, R-2, R-3 are now here
    content = CSV + "R-4,2026-03-04,BN-1,House Beans,Coffee Beans,1,320,150,C-4,Ana Cruz,Davao,Store\n" \
                    "R-5,yesterday,BN-1,House Beans,Coffee Beans,1,320,150,C-4,Ana Cruz,Davao,Store\n"
    token = upload(owner, content)
    page = owner.get(f"/import/{token}").get_data(as_text=True)
    form = {}
    for key, options in re.findall(r'<select name="(\w+)">(.*?)</select>', page, re.S):
        chosen = re.search(r'<option value="([^"]*)" selected', options)
        if chosen:
            form[key] = chosen.group(1)
    review = post(owner, f"/import/{token}", form)
    html = review.get_data(as_text=True)
    assert review.status_code == 200 and "Review the Import" in html and 'aria-current="step"' in html
    assert ">1</strong>" in html and ">3</strong>" in html  # 1 new order, 3 already here
    assert "yesterday" in html and "Import 1 order" in html
    with app.app_context():
        assert db.session().scalar(select(func.count()).select_from(Order)) == 3  # nothing saved yet
    back = post(owner, f"/import/{token}", {**form, "action": "edit"}).get_data(as_text=True)
    assert "Map Columns" in back and '<option value="Receipt #" selected>' in back
    done = post(owner, f"/import/{token}", {**form, "action": "import"})
    assert done.status_code == 302 and "done=1" in done.headers["Location"]
    assert "Done" in owner.get(done.headers["Location"]).get_data(as_text=True)
    with app.app_context():
        assert db.session().scalar(select(func.count()).select_from(Order)) == 4


def test_records_pages_go_both_ways_and_say_where_you_are(app, owner):
    lines = ["Order,Date,SKU,Qty,Price"] + [f"O-{n:03d},2026-01-{1 + n % 28:02d},SKU-{n % 3},1,10"
                                           for n in range(1, 121)]
    import_csv(owner, "\n".join(lines) + "\n")
    first = owner.get("/records").get_data(as_text=True)
    assert "Showing <strong>1&ndash;50</strong> of <strong>120</strong> lines" in first
    after = re.search(r'after=([\w-]+)', first).group(1)
    second = owner.get(f"/records?after={after}").get_data(as_text=True)
    assert "Showing <strong>51&ndash;100</strong>" in second
    before = re.search(r'before=([\w-]+)', second).group(1)
    back = owner.get(f"/records?before={before}").get_data(as_text=True)
    assert "Showing <strong>1&ndash;50</strong>" in back
    assert re.findall(r">(O-\d{3})<", back) == re.findall(r">(O-\d{3})<", first)  # the same page again
    third = owner.get("/records?after=" + re.search(r'after=([\w-]+)', second).group(1)).get_data(as_text=True)
    assert "Showing <strong>101&ndash;120</strong>" in third
    assert 'aria-disabled="true">Older' in third
