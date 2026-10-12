# MAMAL Central — Architecture (Phase 1)

Source of truth for scope: *MAMAL_Central_Phase_1_Scope_Definition* (Google Drive).

## Stack
- Python 3.12+, FastAPI, SQLAlchemy 2, Jinja2 server-rendered pages (RTL, mobile-first, no JS build step).
- SQLite by default; any SQLAlchemy URL (e.g. PostgreSQL) via `MAMAL_DATABASE_URL`.
- No third-party services or CDNs: everything runs on a MAMAL-owned server, per the ownership requirement.

## Layout
```
mamal_central/
  config.py     settings from env
  models.py     Workspace, Unit, User, Membership, DailyReport, Attachment
  db.py         engine + session factory
  security.py   scrypt password hashes, CSRF tokens
  dashboard.py  per-unit scoreboard
  settlement.py monthly investor statement for partnership contracts
  jalali.py     Jalali dates and Persian digits for display
  main.py       routes
  cli.py        admin commands
```

## Workspace model
- `Workspace` = one brand / company / project. New business = new row, no new system.
- `Unit` = a selling point inside a workspace, with a daily sales target (toman).
- `Membership` links a user to a workspace with role `manager` or `staff`; `User.is_admin` sees everything.
- A user without a membership gets 404 (not 403) for that workspace, so workspace slugs can't be probed.

## Daily report rules
- One report per unit per day (unique constraint); resubmitting updates it, attachments accumulate.
- Sales are split into cash / own-POS card / other-POS card; the total is derived, never typed.
- Staff can only write today and yesterday; nobody can write future dates.
- Uploads are whitelisted by extension, size-capped, stored under random names and served only to members of the workspace.

## Partnership contracts
- `PartnershipContract` holds the terms of the Coffee Bus 03 template (split before/after capital recovery, management fee, exit penalties, buy-out multiplier).
- `MonthlyCost` holds a unit's operating costs per Jalali month; materials come from daily-report purchases.
- `settlement.compute_statement` is pure (no DB) and unit-tested; `load_month_inputs` gathers the month inputs.
- Investor national IDs are not stored; `documents_url` points to the restricted Drive folder.

## Next modules
1. Actual payouts to investors, other income/expenses — replaces *MAMALCO Master Sheet*.
2. CRM cases (Case ID) — replaces *MAMALCO Automation Platform* sheet.
3. Google Sheets export of daily reports, then n8n/Make webhooks.
