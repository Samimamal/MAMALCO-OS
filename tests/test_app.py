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
