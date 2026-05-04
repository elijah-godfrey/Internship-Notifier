# internship-notifier

A small notifier that watches new internships from
[`SimplifyJobs/Summer2026-Internships`](https://github.com/SimplifyJobs/Summer2026-Internships)
and emails you only when new listing IDs appear.

## What this does

- Polls upstream `listings.json`
- Applies your filters from `notifier.toml`
- Keeps state (`seen_ids` + `listings_sha`) so you do not get duplicates
- Optionally sends email via SMTP

## 1) Install

Requires Python 3.11+.

```bash
python -m pip install -e .
```

## 2) Configure filters (`notifier.toml`)

Set what you want to track:

- `source = "summer2026"` or `"offseason"`
- `all_categories = true` to include all categories for that source
- `categories = [...]` only matters when `all_categories = false`

If you change filters later, run bootstrap again once.

## 3) Configure environment (`.env`)

Copy `env.example` to `.env` and fill in values.

```bash
cp env.example .env
```

### Gmail users

For Gmail, `SMTP_PASSWORD` is **not** your normal Gmail login password.
Use a Google **App Password** (16 characters).

1. Turn on **2-Step Verification** for the Gmail account.
2. Open Google Account -> Security -> **App passwords**.
3. Create a new app password.
4. Copy the 16-character password and save it in `.env` as `SMTP_PASSWORD`
   (no spaces).

Typical Gmail values:

- `SMTP_HOST=smtp.gmail.com`
- `SMTP_PORT=587`
- `SMTP_USER=yourgmail@gmail.com`
- `SMTP_FROM=yourgmail@gmail.com`
- `SMTP_TO=yourgmail@gmail.com`
- `SMTP_PASSWORD=16-char-app-password`

## 4) Bootstrap once

Marks all currently matching listings as already seen.

```bash
python -m internship_notifier --bootstrap
```

## 5) Run normally

```bash
python -m internship_notifier
```

Useful flags:

- `--dry-run` : preview without saving state
- `--help` : show all options

## GitHub Actions mode

This repo includes `.github/workflows/notifier.yml`.
It currently runs every **10 minutes** and persists state in
`.github/internship-notifier-state.json`.

Setup:

1. Push repo and enable Actions.
2. Add repo secrets: `SMTP_HOST`, `SMTP_FROM`, `SMTP_TO`, and usually
   `SMTP_USER`, `SMTP_PASSWORD`.
3. Run the workflow manually once with `bootstrap=true`.

If logs say `No upstream change (listings.json blob sha unchanged).`,
upstream data has not changed since your saved SHA.

## Dev checks

```bash
python -m ruff check src tests
python -m pytest
```
