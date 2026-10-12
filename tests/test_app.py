import datetime as dt
import re

import pytest
from fastapi.testclient import TestClient

from mamal_central.config import Settings
from mamal_central.jalali import format_jalali, to_jalali
from mamal_central.main import create_app
from mamal_central.models import Membership, Role, Unit, User, Workspace
from mamal_central.security import hash_password, verify_password

TODAY = dt.date(2026, 10, 12)


@pytest.fixture
def app(tmp_path):
    settings = Settings(data_dir=tmp_path, secret_key="test-secret")
    app = create_app(settings, today=lambda: TODAY)
    db = app.state.session_factory()
    kiosks = Workspace(slug="kiosks", name="Kiosks")
    kiosks.units = [Unit(name="Bazaar", daily_target=3_000_000)]
    other = Workspace(slug="beauty", name="Beauty")
    other.units = [Unit(name="Shop", daily_target=0)]
    admin = User(username="sami", password_hash=hash_password("admin-pass-123"), is_admin=True)
    mehran = User(username="mehran", password_hash=hash_password("staff-pass-123"))
    db.add_all([kiosks, other, admin, mehran])
    db.flush()
    db.add(Membership(user_id=mehran.id, workspace_id=kiosks.id, role=Role.staff))
    db.commit()
    app.state.ids = {"bazaar": kiosks.units[0].id, "shop": other.units[0].id}
    db.close()
    return app


def login(client, username, password):
    page = client.get("/login")
    token = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
    resp = client.post(
        "/login", data={"username": username, "password": password, "csrf": token}
    )
    return resp, token


def csrf_from(client, url):
    return re.search(r'name="csrf" value="([^"]+)"', client.get(url).text).group(1)


def submit(client, unit_id, date, files=None, **numbers):
    token = csrf_from(client, "/w/kiosks/report")
    data = {"unit_id": unit_id, "report_date": date.isoformat(), "csrf": token, **numbers}
    return client.post("/w/kiosks/report", data=data, files=files or [])


def test_password_hashing():
    stored = hash_password("correct horse")
    assert verify_password("correct horse", stored)
    assert not verify_password("wrong", stored)


def test_jalali():
    assert to_jalali(dt.date(2024, 3, 20)) == (1403, 1, 1)
    assert to_jalali(dt.date(2026, 10, 12)) == (1405, 7, 20)
    assert format_jalali(dt.date(2026, 10, 12)) == "۱۴۰۵/۰۷/۲۰"


def test_requires_login(app):
    client = TestClient(app)
    resp = client.get("/w/kiosks", follow_redirects=False)
    assert resp.status_code == 303 and resp.headers["location"] == "/login"


def test_bad_login(app):
    client = TestClient(app)
    resp, _ = login(client, "sami", "nope")
    assert resp.status_code == 401


def test_login_requires_csrf(app):
    client = TestClient(app)
    client.get("/login")
    resp = client.post("/login", data={"username": "sami", "password": "admin-pass-123"})
    assert resp.status_code == 403


def test_staff_flow_and_dashboard(app):
    staff = TestClient(app)
    login(staff, "mehran", "staff-pass-123")
    # Staff land on the report form, not the dashboard.
    resp = staff.get("/w/kiosks", follow_redirects=False)
    assert resp.headers["location"] == "/w/kiosks/report"
    # Staff cannot see other workspaces.
    assert staff.get("/w/beauty").status_code == 404

    bazaar = app.state.ids["bazaar"]
    resp = submit(
        staff,
        bazaar,
        TODAY,
        files=[("photos", ("invoice.jpg", b"\xff\xd8fakejpeg", "image/jpeg"))],
        cash_sales=1_000_000,
        card_sales_own_pos=500_000,
        card_sales_other=200_000,
        customer_count=40,
        waste=50_000,
    )
    assert resp.status_code == 200 and "گزارش ذخیره شد" in resp.text

    # Resubmitting the same day updates rather than duplicates.
    submit(staff, bazaar, TODAY, cash_sales=1_100_000, card_sales_own_pos=500_000,
           card_sales_other=200_000, customer_count=45, waste=50_000)

    # Staff cannot back-date beyond the edit window.
    assert submit(staff, bazaar, TODAY - dt.timedelta(days=5), cash_sales=1).status_code == 403

    admin = TestClient(app)
    login(admin, "sami", "admin-pass-123")
    page = admin.get("/w/kiosks").text
    assert "۱٬۸۰۰٬۰۰۰" in page  # 1.1M + 0.5M + 0.2M
    assert "۶۰٪ هدف" in page  # 1.8M / 3M
    assert "POS دیگر" in page
    assert "۱۴ روز بدون گزارش" in page

    form = admin.get(f"/w/kiosks/report?unit_id={bazaar}&date={TODAY}").text
    att_url = re.search(r'href="(/attachments/\d+)"', form).group(1)
    assert admin.get(att_url).content == b"\xff\xd8fakejpeg"

    # A user outside the workspace can't fetch the attachment.
    db = app.state.session_factory()
    db.add(User(username="other", password_hash=hash_password("other-pass-123")))
    db.commit()
    db.close()
    outsider = TestClient(app)
    login(outsider, "other", "other-pass-123")
    assert outsider.get(att_url).status_code == 404


def test_rejects_bad_upload_and_future_date(app):
    admin = TestClient(app)
    login(admin, "sami", "admin-pass-123")
    bazaar = app.state.ids["bazaar"]
    bad = submit(admin, bazaar, TODAY, files=[("photos", ("x.exe", b"MZ", "application/x"))])
    assert bad.status_code == 400
    future = submit(admin, bazaar, TODAY + dt.timedelta(days=1), cash_sales=1)
    assert future.status_code == 403
    negative = submit(admin, bazaar, TODAY, cash_sales=-5)
    assert negative.status_code == 422


def test_unit_must_belong_to_workspace(app):
    admin = TestClient(app)
    login(admin, "sami", "admin-pass-123")
    resp = submit(admin, app.state.ids["shop"], TODAY, cash_sales=1)
    assert resp.status_code == 404


# ------------------------------------------------------------ settlement

from mamal_central.models import PartnershipContract
from mamal_central.settlement import MonthInput, compute_statement, split_month


def coffee_bus(**kw):
    defaults = dict(
        code="C-BUS03", title="Coffee Bus 03", investor_name="X",
        capital_structure=1_850_000_000, capital_launch=850_000_000,
        start_date=dt.date(2026, 3, 21), term_years=3, max_term_years=5,
        investor_pct_before=60, investor_pct_after=30, management_fee_pct=10,
        exit_penalties="7,5,3", buyout_multiplier=2,
    )
    defaults.update(kw)
    return PartnershipContract(**defaults)


def test_split_month_crossing_capital():
    # 600 of capital left; 60% of 2000 = 1200 would overshoot.
    # 1000 of profit returns the 600 at 60%; the other 1000 splits 30% -> 300.
    assert split_month(2000, 9400, 10_000, 60, 30) == 900
    assert split_month(1000, 0, 10_000, 60, 30) == 600
    assert split_month(1000, 10_000, 10_000, 60, 30) == 300
    assert split_month(-500, 0, 10_000, 60, 30) == 0


def test_statement_numbers():
    c = coffee_bus(capital_structure=1_000, capital_launch=0)
    months = [
        MonthInput(1405, 1, revenue=10_000, materials=3_000, operating=4_000, has_costs=True),
        MonthInput(1405, 2, revenue=5_000, materials=3_000, operating=4_000, has_costs=True),
        MonthInput(1405, 3, revenue=10_000, materials=3_000, operating=4_000, has_costs=True),
    ]
    s = compute_statement(c, months, dt.date(2026, 7, 1))
    # fee 1000, net 2000 -> investor 60% = 1200 > capital 1000:
    # 1667 of profit returns 1000, remaining 333 at 30% -> 99.
    first = s.lines[0]
    assert (first.management_fee, first.net_profit, first.investor_share) == (1_000, 2_000, 1_099)
    assert first.manager_share == 901 and first.recovered
    # Loss month: nothing distributed, not carried forward.
    assert s.lines[1].net_profit == -2_500 and s.lines[1].investor_share == 0
    assert s.lines[2].investor_share == 600  # 30% of 2000
    assert s.is_recovered and s.current_investor_pct == 30
    assert s.exit_penalty_pct is None
    # Buyout: avg net (2000 - 2500 + 2000)/3 = 500 x 2 x 30%
    assert s.buyout_value == 300


def test_statement_penalty_and_projection():
    c = coffee_bus()
    months = [
        MonthInput(1405, m, revenue=1_000_000_000, materials=400_000_000,
                   operating=200_000_000, has_costs=True)
        for m in (1, 2, 3)
    ]
    s = compute_statement(c, months, dt.date(2026, 7, 1))
    # net = 1000 - 400 - 200 - 100 = 300M; investor 180M/month
    assert s.investor_total == 540_000_000
    assert s.remaining_capital == 2_160_000_000
    assert s.months_to_recover == 12
    assert s.contract_year == 1 and s.exit_penalty_pct == 7
    assert s.exit_penalty_amount == 151_200_000
    assert s.end_date == dt.date(2029, 3, 21)


def test_contract_pages(app):
    admin = TestClient(app)
    login(admin, "sami", "admin-pass-123")
    token = csrf_from(admin, "/w/kiosks/contracts/new")
    resp = admin.post("/w/kiosks/contracts/save", data={
        "csrf": token, "code": "c-bus03", "title": "Coffee Bus 03",
        "unit_id": app.state.ids["bazaar"], "investor_name": "Investor",
        "capital_structure": 1_850_000_000, "capital_launch": 850_000_000,
        "start_date": "2026-09-23", "status": "active",
    })
    assert resp.status_code == 200
    assert "C-BUS03" in resp.text and "۲٬۷۰۰٬۰۰۰٬۰۰۰" in resp.text

    submit(admin, app.state.ids["bazaar"], TODAY, cash_sales=2_000_000, purchases=500_000)
    token = csrf_from(admin, "/w/kiosks/costs")
    admin.post("/w/kiosks/costs", data={
        "csrf": token, "unit_id": app.state.ids["bazaar"], "month": "1405-7", "salaries": 300_000,
    })
    page = admin.get(resp.url).text
    # 2,000,000 - 500,000 - 300,000 - 200,000 fee = 1,000,000 net; 60% to investor
    assert "۱٬۰۰۰٬۰۰۰" in page and "۶۰۰٬۰۰۰" in page and "مهر ۱۴۰۵" in page

    # Duplicate code is rejected; staff can't see contracts.
    token = csrf_from(admin, "/w/kiosks/contracts/new")
    dup = admin.post("/w/kiosks/contracts/save", data={
        "csrf": token, "code": "C-BUS03", "title": "x", "unit_id": app.state.ids["bazaar"],
        "investor_name": "y",
    })
    assert dup.status_code == 400
    staff = TestClient(app)
    login(staff, "mehran", "staff-pass-123")
    assert staff.get("/w/kiosks/contracts").status_code == 404
    assert staff.get("/w/kiosks/costs").status_code == 404
    assert staff.get("/catalog").status_code == 404
