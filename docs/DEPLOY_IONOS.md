# راه‌اندازی mamalco.com روی IONOS

زمان: حدود ۳۰ دقیقه. رمز حساب IONOS پیش خودتان می‌ماند؛ هیچ‌جا وارد نمی‌شود.

## ۱. خرید سرور (پنل IONOS)
1. **Server & Cloud → VPS** → کوچک‌ترین پلن لینوکس (۱ تا ۲ هسته، ۲ گیگ رم کافی است).
2. سیستم‌عامل: **Ubuntu 24.04**.
3. بعد از ساخته شدن، **IP سرور** و **رمز root** را از صفحه‌ی همان سرور یادداشت کنید.

## ۲. وصل کردن دامنه
1. **Domains & SSL → mamalco.com → DNS**.
2. رکورد **A** با نام `@` → مقدار: IP سرور.
3. رکورد **A** با نام `www` → مقدار: IP سرور.
4. اگر رکورد A دیگری برای `@` یا `www` هست، پاکش کنید. تغییر DNS ممکن است تا چند ساعت طول بکشد.

## ۳. ورود به سرور
- از کامپیوتر: `ssh root@IP-سرور`
- از گوشی: برنامه‌ی **Termius** → New Host → IP، کاربر `root`، رمز root.
- یا در پنل IONOS: دکمه‌ی **Remote Console / KVM**.

## ۴. کلید دسترسی به کد (فقط یک بار)
در سرور بزنید:
```bash
apt update && apt install -y git
ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519
cat ~/.ssh/id_ed25519.pub
```
خط خروجی (با `ssh-ed25519` شروع می‌شود) را کپی کنید، بعد در GitHub:
**مخزن MAMALCO-OS → Settings → Deploy keys → Add deploy key** → عنوان `ionos`، متن را بچسبانید، «Allow write access» را روشن **نکنید** → Add.

این کلید فقط اجازه‌ی خواندن همین مخزن را دارد و هر وقت بخواهید پاکش می‌کنید.

## ۵. نصب
```bash
ssh-keyscan github.com >> ~/.ssh/known_hosts
git clone -b claude/chi-darim-ta-injaa-l1s8jo git@github.com:Samimamal/MAMALCO-OS.git /opt/mamal
bash /opt/mamal/deploy/install.sh mamalco.com ایمیل-شما
```
در میانه‌ی نصب، نام کاربری و رمز ادمین سایت پرسیده می‌شود (حداقل ۱۰ حرف).

اسکریپت این‌ها را انجام می‌دهد: نصب برنامه، دیتابیس و داده‌ی اولیه (سازه‌ها، کاتالوگ، ۳۴ نقطه، زمین اقامتگاه)، nginx، فایروال، HTTPS رایگان (Let's Encrypt) و بکاپ روزانه‌ی ساعت ۳:۳۰ در `/var/backups/mamal` (۱۴ روز نگه داشته می‌شود).

پایان: سایت روی `https://mamalco.com` و ورود ادمین روی `https://mamalco.com/login`.

اگر HTTPS خطا داد، یعنی DNS هنوز به سرور نرسیده؛ چند ساعت بعد همان دستوری را بزنید که اسکریپت نشان می‌دهد.

## به‌روزرسانی‌های بعدی
```bash
bash /opt/mamal/deploy/update.sh
```

## امنیت
- رمز حساب IONOS را عوض کنید و ورود دو مرحله‌ای (2FA) را روشن کنید.
- رمز root سرور را جایی امن نگه دارید و در چت نفرستید.
- بکاپ‌ها روی همان سرورند؛ ماهی یک بار یک نسخه از `/var/backups/mamal` را جای دیگری (مثلاً Google Drive) هم نگه دارید.
