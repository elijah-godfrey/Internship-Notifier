# internship-notifier

A small notifier that watches new internships from
[`SimplifyJobs/Summer2026-Internships`](https://github.com/SimplifyJobs/Summer2026-Internships)
and emails you only when new listing IDs appear.

## What this does

- Polls upstream `listings.json`
- Applies your filters from `notifier.toml`
- Optionally ranks company prestige and alerts only above your threshold
- Keeps state (`seen_ids` + `listings_sha`) so you do not get duplicates
- Optionally sends email via SMTP

## 1) Install

Requires Python 3.11+.

```bash
python -m pip install -e .
```

## 2) Configure filters (`notifier.toml`)

Set what you want to track:

- `source = "summer2026"`, `"offseason"`, or `"all"` for both
- `all_categories = true` to include all categories for that source
- `categories = [...]` only matters when `all_categories = false`

If you change filters later, run bootstrap again once.

### Optional company prestige filter

Choose exactly one threshold in `notifier.toml`.

Use a numeric score:

```toml
[prestige]
minimum_score = 75
```

Or use your current/baseline company:

```toml
[prestige]
benchmark_company = "Microsoft"
```

Scores range from 1 to 100 and measure only software-engineering career
prestige: technical reputation, selectivity, and career signal. Pay, work-life
balance, location, and return-offer odds are deliberately excluded.

The first time an unknown company appears, GPT-5.6 Terra ranks it and writes the
result to `.github/company-prestige-cache.json`. Unknown companies are grouped
into API requests of up to 20; later listings reuse the cached score.
Automatic scores are refreshed after four months, at most 25 per workflow run.
Entries with `"manual_override": true` are never refreshed automatically.

The workflow also generates a human-readable
[`docs/company-prestige-rankings.md`](docs/company-prestige-rankings.md), sorted
by score and capped at the top 500 cached companies. The JSON cache remains the
source of truth.

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

### OpenAI API

Prestige filtering needs an OpenAI API key only when a company is not already
cached. API billing is separate from a ChatGPT subscription.

For local runs, set:

```text
OPENAI_API_KEY=sk-your-key
OPENAI_PRESTIGE_MODEL=gpt-5.6-terra
```

Never commit a real API key.

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
2. Add the `OPENAI_API_KEY` repository secret if prestige filtering is enabled.
3. Add the `OPENAI_PRESTIGE_MODEL` repository variable with
   `gpt-5.6-terra` (optional; this is the default).
4. Add email secrets: `SMTP_HOST`, `SMTP_FROM`, `SMTP_TO`, and usually
   `SMTP_USER`, `SMTP_PASSWORD`.
5. Run the workflow manually once with `bootstrap=true`.

If logs say `No upstream change (listings.json blob sha unchanged).`,
upstream data has not changed since your saved SHA.

## Dev checks

```bash
python -m ruff check src tests
python -m pytest
```
