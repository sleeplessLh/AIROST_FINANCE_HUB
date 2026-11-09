# AIROST Finance Hub

A Flask web application for managing club expenses, reimbursement claims, event budgets, payments, and audit records.

## Features

- Role-based access for members, treasurers, and admins
- Submit, edit, and review reimbursement claims
- Upload and validate receipt files
- Manage event budgets and record payments
- Export claim records as CSV
- Keep audit logs for important actions

## Tech stack

- Python and Flask
- Flask-SQLAlchemy and SQLite for local development
- PostgreSQL for Render deployment
- Bootstrap for the frontend

## Run locally

Create and activate a virtual environment in PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1