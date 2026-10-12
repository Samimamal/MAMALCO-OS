import datetime as dt
import re

import pytest
from fastapi.testclient import TestClient

from mamal_central.config import Settings
from mamal_central.main import create_app
from mamal_central.models import (
    Application,
    Category,
    Employee,
    Membership,
    Order,
    Product,
    RaiseRule,
    Role,
    StructurePhoto,
    StructureStatus,
    StructureTier,
    Unit,
    User,
    Workspace,
)
from mamal_central.rules import current_salary, investment_rule, next_raise_date
from mamal_central.security import hash_password

TODAY = dt.date(2026, 10, 12)
JPEG = b"\xff\xd8\xff\xe0fakejpeg"


@pytest.fixture
def app(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, secret_key="t"), today=lambda: TODAY)
    db = app.state.session_factory()
    ws = Workspace(slug="mamal-form", name="Form")
    tea = Unit(
        name="چای‌خانه مامال", code="F-02", category=Category.form, branch="چای‌خانه",
        latitude=37.04, longitude=45.1, is_public=True, status=StructureStatus.operating,
        phone="+98 900 000 0000",
    )
    hidden = Unit(name="Hidden", code="F-03", category=Category.form, is_public=True)
    ws.units = [tea, hidden]
    db.add_all([
        ws,
        StructureTier(category=Category.form, name="فرم‌دار", min_price=1_500_000_000,
                      max_price=3_000_000_000, price_updated_on=TODAY),
        StructureTier(category=Category.lodge, name="اقامتگاه", min_price=6_000_000_000,
                      max_price=10_000_000_000, price_updated_on=TODAY),
        User(username="sami", password_hash=hash_password("admin-pass-123"), is_admin=True),
        User(username="op", password_hash=hash_password("op-pass-1234")),
    ])
    db.flush()
    (tmp_path / "uploads").mkdir(exist_ok=True)
    (tmp_path / "uploads" / "p1.jpg").write_bytes(JPEG)
    db.add(StructurePhoto(unit_id=tea.id, stored_name="p1.jpg", content_type="image/jpeg"))
    db.add(Product(unit_id=tea.id, name="چای سیاه", unit_label="کیلو", retail_price=800_000,
                   wholesale_price=700_000, wholesale_min_qty=10, served_on_site=True))
    db.add(Product(unit_id=tea.id, name="قند", unit_label="کیلو", retail_price=120_000))
    op = db.query(User).filter_by(username="op").one()
    db.add(Membership(user_id=op.id, workspace_id=ws.id, role=Role.manager, unit_id=hidden.id))
    db.commit()
    app.state.ids = {"tea": tea.id, "hidden": hidden.id}
    db.close()
    return app


def csrf(client, url):
    return re.search(r'name="csrf" value="([^"]+)"', client.get(url).text).group(1)


def login(client, username, password):
    token = csrf(client, "/login")
    return client.post("/login", data={"username": username, "password": password, "csrf": token})


def test_public_listing_only_shows_complete_structures(app):
    c = TestClient(app)
    home = c.get("/").text
    assert "چای‌خانه مامال" in home and "Hidden" not in home
    assert c.get("/s/f-02").status_code == 200
    # Published flag but no GPS/photo -> not listed.
    assert c.get("/s/f-03").status_code == 404
    page = c.get("/s/F-02").text
    assert "openstreetmap.org" in page and "37.04" in page
    assert "عمده از ۱۰ کیلو" in page
    assert c.get("/photos/1").content == JPEG


def test_invest_application(app):
    c = TestClient(app)
    token = csrf(c, "/apply/invest?s=f-02")
    assert "۷۰۰٬۰۰۰٬۰۰۰" in c.get("/apply/invest?s=f-02").text
    bad = c.post("/apply/invest", data={"csrf": token, "s": "f-02", "full_name": "A", "phone": "x"})
    assert bad.status_code == 400 and "شماره تماس معتبر نیست" in bad.text
    ok = c.post("/apply/invest", data={
        "csrf": token, "s": "f-02", "full_name": "علی رضایی", "phone": "۰۹۱۲۱۲۳۴۵۶۷",
        "cash_amount": "۷۰۰۰۰۰۰۰۰", "cheque_amount": "800000000",
    })
    assert ok.status_code == 200 and "MC-" in ok.text
    db = app.state.session_factory()
    case = db.query(Application).one()
    assert case.phone == "09121234567" and case.cash_amount == 700_000_000
    assert case.category == Category.form and case.unit_id == app.state.ids["tea"]
    db.close()


def test_general_lodge_preregistration_and_honeypot(app):
    c = TestClient(app)
    token = csrf(c, "/apply/invest?category=lodge")
    assert "۳٬۰۰۰٬۰۰۰٬۰۰۰" in c.get("/apply/invest?category=lodge").text  # 50% of 6B
    resp = c.post("/apply/invest", data={
        "csrf": token, "category": "lodge", "full_name": "Investor X", "phone": "09120000000",
        "cash_amount": "3000000000",
    })
    assert resp.status_code == 200
    spam = c.post("/apply/job", data={
        "csrf": token, "full_name": "Bot", "phone": "09120000000", "desired_role": "x",
        "website": "http://spam",
    })
    assert spam.status_code == 400


def test_order_uses_wholesale_price(app):
    c = TestClient(app)
    token = csrf(c, "/s/f-02")
    db = app.state.session_factory()
    tea_id, sugar_id = [p.id for p in db.query(Product).order_by(Product.id)]
    db.close()
    resp = c.post("/s/f-02/order", data={
        "csrf": token, f"qty_{tea_id}": "12", f"qty_{sugar_id}": "2",
        "full_name": "Customer", "phone": "09120000000",
    })
    assert resp.status_code == 200 and "MO-00001" in resp.text
    db = app.state.session_factory()
    order = db.query(Order).one()
    assert order.total == 12 * 700_000 + 2 * 120_000 and order.is_wholesale
    db.close()
    missing_address = c.post("/s/f-02/order", data={
        "csrf": token, f"qty_{tea_id}": "1", "full_name": "Customer",
        "phone": "09120000000", "delivery": "1",
    })
    assert missing_address.status_code == 400


def test_unit_scoped_manager(app):
    c = TestClient(app)
    login(c, "op", "op-pass-1234")
    page = c.get("/w/mamal-form/structures").text
    assert "Hidden" in page and "چای‌خانه مامال" not in page
    assert c.get(f"/w/mamal-form/structures/{app.state.ids['tea']}").status_code == 404
    assert c.get(f"/w/mamal-form/products?unit_id={app.state.ids['tea']}").status_code == 404


def test_admin_publishes_structure(app):
    c = TestClient(app)
    login(c, "sami", "admin-pass-123")
    hid = app.state.ids["hidden"]
    token = csrf(c, f"/w/mamal-form/structures/{hid}")
    c.post(f"/w/mamal-form/structures/{hid}", data={
        "csrf": token, "name": "کافه مامال", "latitude": "۳۷٫۰۵", "longitude": "45.11",
        "status": "building", "build_start_date": "2026-10-01", "build_days": "45",
        "is_public": "true", "open_for_investment": "true",
    })
    resp = c.post(f"/w/mamal-form/structures/{hid}/photos", data={"csrf": token},
                  files=[("photos", ("a.jpg", JPEG, "image/jpeg"))])
    assert resp.status_code == 200
    public = TestClient(app).get("/s/f-03").text
    assert "کافه مامال" in public and "در حال ساخت" in public and "۲۴٪" in public
    bad = c.post(f"/w/mamal-form/structures/{hid}/photos", data={"csrf": token},
                 files=[("photos", ("a.pdf", b"%PDF", "application/pdf"))])
    assert bad.status_code == 400


def test_staff_salary_rules(app):
    e = Employee(start_date=dt.date(2025, 1, 1), base_salary=15_000_000,
                 raise_rule=RaiseRule.six_months)
    assert current_salary(e, dt.date(2025, 6, 30)) == 15_000_000
    assert current_salary(e, dt.date(2025, 7, 1)) == 18_000_000
    assert current_salary(e, dt.date(2030, 1, 1)) == 30_000_000  # capped
    assert next_raise_date(e, dt.date(2030, 1, 1)) is None
    y = Employee(start_date=dt.date(2025, 1, 1), base_salary=20_000_000, raise_rule=RaiseRule.yearly)
    assert current_salary(y, dt.date(2026, 10, 12)) == 25_000_000

    c = TestClient(app)
    login(c, "sami", "admin-pass-123")
    token = csrf(c, "/w/mamal-form/staff")
    base = {"csrf": token, "unit_id": app.state.ids["tea"], "full_name": "Mehran",
            "start_date": "2026-01-01", "raise_rule": "six_months", "active": "true"}
    assert c.post("/w/mamal-form/staff", data={**base, "base_salary": 10_000_000}).status_code == 422
    assert c.post("/w/mamal-form/staff", data={**base, "base_salary": 15_000_000,
                                               "contract_months": 6}).status_code == 422
    ok = c.post("/w/mamal-form/staff", data={**base, "base_salary": 15_000_000})
    assert ok.status_code == 200 and "۱۵٬۰۰۰٬۰۰۰" in ok.text


def test_investment_rules():
    assert investment_rule(Category.form, 1_500_000_000).min_cash == 700_000_000
    assert investment_rule(Category.village, 3_000_000_000).min_cash == 1_500_000_000
    assert investment_rule(Category.lodge, 6_000_000_000).min_cash == 3_000_000_000
