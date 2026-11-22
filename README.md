# AIROST Finance Hub

A Flask web application for the AIROST club to manage reimbursement claims, event budgets, payments and audit records in one place.

**Live site:** https://airost-finance-hub-4jd2.onrender.com
(free Render instance: the first visit after a quiet period can take up to a minute to wake up)

## Features

### Members
- Submit claims with a receipt (JPG, PNG or PDF, up to 5 MB), save them as drafts, edit and resubmit rejected claims
- Optional AI receipt analysis (Google Gemini) that fills in the title, amount and description
- Track each claim's progress: submitted, approved or rejected (with the reason), paid, and view the payment proof
- Search and filter their own claims (server-side search, 20 per page)
- See each claim's history: submitted, approved or rejected (with the reason), paid
- Email when a claim is approved, rejected (with the reason) or paid (with the payment details)

### Treasurers
- Dashboard with total allocated, committed and remaining budget, plus claims that would exceed their event's budget
- Review claims with search and status filter; approve (blocked if the event budget is insufficient) or reject with a reason
- See the member's bank details when paying, record the payment method and reference, and attach a payment proof (transfer screenshot or receipt), also later
- Set a budget for each event; the dashboard bars also show pending amounts and warn before a budget would be exceeded
- Event expense report per event (web page, printable as PDF, and CSV download)
- Email when a claim is submitted or resubmitted, plus a daily reminder for claims waiting more than 3 days
- Export all claims as CSV (protected against spreadsheet formula injection)

### Admins
- Create, edit, deactivate and delete user accounts (accounts with history can only be deactivated)
- Reset a forgotten password with a temporary one; the user must choose a new password at next sign-in
- New accounts must confirm their email and add a phone number (and, for members, bank details) at first sign-in
- Manage events, and close an event for new claims (claims already submitted can still be reviewed and paid)
- Browse the audit log with action and user filters

### Everyone
- Profile page for phone and bank details, and password changes
- Appearance setting per user: light (default), dark or follow the device
- Responsive layout, keyboard accessible, respects reduced-motion settings

## Claim workflow

```
draft ──submit──> pending ──approve──> approved ──mark paid──> paid
                     │
                     └──reject──> rejected ──edit & resubmit──> pending
```

Committed budget = approved + paid claims. Drafts are visible only to the member who created them.

## Tech stack

- **Backend:** Python, Flask, Flask-Login, Flask-WTF (CSRF), Flask-SQLAlchemy
- **Database:** SQLite locally, PostgreSQL on Render
- **Frontend:** Jinja2 templates, Bootstrap 5 (CSS), custom design tokens, vanilla JavaScript
- **AI:** Google Gemini API for receipt analysis (optional)
- **Email:** Brevo transactional email API (optional)
- **Hosting:** Render (gunicorn)

## Project structure

```
app.py                  App setup, dashboard, audit log, error pages, CLI commands
models.py               SQLAlchemy models
database.py             Shared SQLAlchemy instance
routes/
  auth.py               Sign in and sign out
  claims.py             Claims, receipts, review, budgets, payments, CSV export
  admin.py              User and event management, password reset
  profile.py            Profile, password, appearance
utils/                  Budget maths, dashboard statistics, file upload checks, audit logging,
                        CSV safety, time zone, AI receipt analysis, email notifications
templates/              Jinja2 templates
static/                 style.css (tokens), theme-dark.css, apple.css (components), app.js
tests/                  pytest test suite
```

## Run locally

Developed and tested with Python 3.14. In PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
Copy-Item .env.example .env
```

Fill in `.env` (see the table below), then start the development server:

```powershell
flask --app app run --debug
```

Open http://127.0.0.1:5000. Tables are created automatically on first start in `instance/airost.db`.

### Create the first admin

```powershell
flask --app app create-admin
```

You will be asked for the AIROST ID, name, email and password.

## Environment variables

| Variable | Required | Purpose |
|---|---|---|
| `SECRET_KEY` | On Render | Signs session cookies. The app refuses to start on Render without it; locally a random key is used if it is missing. |
| `DATABASE_URL` | On Render | PostgreSQL connection string. Leave unset locally to use SQLite. |
| `GEMINI_API_KEY` | No | Enables AI receipt analysis. Without it the rest of the app works normally. |
| `GEMINI_MODEL` | No | Gemini model name (default `gemini-3.6-flash`). |
| `BREVO_API_KEY` | No | Enables email notifications. Without it no emails are sent and everything else works. |
| `MAIL_FROM` | With Brevo | Sender address; must be a verified sender in Brevo. |
| `MAIL_FROM_NAME` | No | Sender name (default `AIROST Finance Hub`). |
| `TASK_TOKEN` | No | Secret for `/tasks/pending-reminders`, called daily by GitHub Actions. Without it the reminder is off. |
| `ADMIN_SETUP_KEY` | No | Enables `/setup-admin` for creating the first admin on a server without shell access. The page closes once an admin exists. |

Never commit `.env`; it is git-ignored.

## Run the tests

Each test file creates its own temporary SQLite database and must run on its own:

```powershell
Get-ChildItem tests\test_*.py | ForEach-Object { python -m pytest $_.FullName -q }
```

## Deploy on Render

1. Create a PostgreSQL database and a web service from this repository (`render.yaml` describes the web service).
2. Set `DATABASE_URL`, `SECRET_KEY` and, if needed, `GEMINI_API_KEY` and `ADMIN_SETUP_KEY`.
3. Every push to `main` deploys automatically. New tables are created on startup; existing tables are never altered, so new per-user data goes into new tables.
4. Create the first admin through `/setup-admin`, then remove `ADMIN_SETUP_KEY` if you like.

Render's free plan blocks outgoing SMTP, so emails are sent through Brevo's HTTPS API.

Render's free plan has no cron jobs, so the daily reminder runs in GitHub Actions (`.github/workflows/pending-reminders.yml`). Set the same random `TASK_TOKEN` on Render and as a repository secret (Settings → Secrets and variables → Actions).

Receipt files are stored in the database as well as on disk, so they survive redeploys on Render's temporary disk.

## Security

- Passwords are hashed (Werkzeug); sessions are signed with `SECRET_KEY`
- CSRF protection on every form
- Role checks on every route; members can only see their own claims and receipts
- Receipt uploads are checked by their real content, not only their extension
- Bank account numbers are shown to treasurers only once a claim is approved
- CSV export neutralises spreadsheet formulas
- Important actions (approvals, payments, budget changes, account changes) are written to the audit log
