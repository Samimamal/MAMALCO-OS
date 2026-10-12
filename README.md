# MAMAL Central

دفتر مرکزی دیجیتال و داخلی گروه MAMAL (فاز ۱).
هر برند، شرکت یا پروژه یک **فضای کاری (Workspace)** جدا دارد و همه با یک هسته‌ی مشترک ساخته می‌شوند.

## وضعیت فعلی

| ماژول (طبق سند فاز ۱) | وضعیت |
|---|---|
| ورود، نقش‌ها و دسترسی به تفکیک فضای کاری | ✅ ساخته شد |
| گزارش روزانه‌ی فروش واحدها + عکس فاکتور | ✅ ساخته شد |
| داشبورد هر فضای کاری (۱۵ روز اخیر، هدف روزانه، هشدارها) | ✅ ساخته شد |
| مالی (هزینه، درآمد، پرداخت‌ها) | ⏳ بعدی |
| تسک‌ها | ⏳ |
| فایل‌ها و قراردادها (اتصال Google Drive) | ⏳ |
| مدیریت محتوا | ⏳ |
| اتصال‌ها (Sheets، n8n/Make، واتس‌اپ، تلگرام، …) | ⏳ |

### گزارش روزانه چه چیزی را ثبت می‌کند
- فروش نقدی، کارت با POS مامال، و **کارت با POS همسایه یا حساب شخصی** (جداگانه، چون این پول به حساب مامال نمی‌رسد)
- تعداد مشتری (میانگین فاکتور خودکار حساب می‌شود)، خرید جنس، ضایعات، توضیحات
- عکس فاکتورها و رسید POS (jpg / png / webp / heic / pdf، حداکثر ۱۰ مگابایت)

داشبورد این موارد را علامت می‌زند: روز بدون گزارش، گزارش بدون عکس، فروش خارج از POS مامال، و درصد رسیدن به هدف روزانه.

### نقش‌ها
- **ادمین (admin):** همه‌ی فضاهای کاری
- **مدیر (manager):** داشبورد و گزارش‌های فضای کاری خودش
- **اپراتور (staff):** فقط فرم گزارش؛ ثبت یا اصلاح فقط برای امروز و دیروز

## اجرا

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# فضای کاری اشنویه با MAMAL Bazaar، چای‌خانه و لبنیات (هدف ۳ میلیون تومان در روز)
.venv/bin/python -m mamal_central.cli init-mamal
.venv/bin/python -m mamal_central.cli create-user sami --full-name "سامی مامال" --admin

# اپراتور
.venv/bin/python -m mamal_central.cli create-user mehran --full-name "مهران"
.venv/bin/python -m mamal_central.cli grant mehran mamal-oshnavieh staff

.venv/bin/uvicorn --factory mamal_central.main:app_factory --host 0.0.0.0 --port 8000
```

دستورهای دیگر: `add-workspace`، `add-unit`، `set-password`، `list`.

### تنظیمات (متغیرهای محیطی)
| متغیر | پیش‌فرض |
|---|---|
| `MAMAL_DATA_DIR` | `./data` (دیتابیس، عکس‌ها، کلید session) |
| `MAMAL_DATABASE_URL` | SQLite داخل `MAMAL_DATA_DIR` |
| `MAMAL_SECRET_KEY` | به‌صورت خودکار ساخته و در `data/secret_key` نگه داشته می‌شود |

روی سرور (IONOS) پشت nginx با HTTPS اجرا شود و از پوشه‌ی `data/` روزانه بکاپ گرفته شود.

## تست

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

معماری: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
