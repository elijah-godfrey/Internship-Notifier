# internship-notifier

Polls the upstream [`SimplifyJobs/Summer2026-Internships`](https://github.com/SimplifyJobs/Summer2026-Internships) `listings.json` on branch `dev`, applies the same **summer** or **off-season** rules as the repo READMEs, optionally filters by **category**, and tracks **seen listing IDs** so you only get **new** rows on stdout (and optionally by **email**).

## Install

Requires **Python 3.11+**.

```bash
cd "Internship Notifications"
python -m pip install -e .
```

Run `internship-notifier --help` or `python -m internship_notifier --help`.

## What to track (notifier.toml)

Edit **[`notifier.toml`](notifier.toml)** at the repo root to choose:

- **`source`**: `summer2026` or `offseason` (same meaning as the upstream READMEs).
- **`all_categories`**: `true` to include every category for that source, or `false` to use **`categories`**.
- **`categories`**: list of exact upstream names, e.g. `"Software Engineering"`, `"Product Management"`, `"Data Science, AI & Machine Learning"`, `"Quantitative Finance"`, `"Hardware Engineering"`.

The committed file is the default for both **local runs** (from the repo directory) and **GitHub Actions** (the workflow does not pass `--source` / `--category`).

**Overrides:** `--source`, `--category` (repeatable), or `--all-categories` on the CLI replace the TOML values for that run. **`--no-config-file`** ignores TOML and **`NOTIFIER_CONFIG`**. **`--config PATH`** loads a specific file (must exist). **`NOTIFIER_CONFIG`** in the environment points at a TOML when you do not want `./notifier.toml` in the current working directory.

**After you change filters** in `notifier.toml`, run **`--bootstrap`** again (or use the workflow’s bootstrap checkbox) so existing listings under the new rules are not all emailed as “new.”

## Environment variables

See [`env.example`](env.example) for names and short comments. Copy it to **`.env`** in the working directory (same folder you use as “Start in” for Task Scheduler, or the repo root when you run from a shell).

On startup the CLI loads **`.env`** in the current working directory via **`python-dotenv`** (same idea as npm’s `dotenv`). Existing real environment variables are **not** overwritten. If **`DOTENV_PATH`** is set (in the real environment or inside `.env`), a **second** file is loaded the same way, filling only variables that are still unset.

- **`GITHUB_TOKEN`** (optional): GitHub fine-grained or classic PAT with read access to public repos. Improves rate limits if you poll often.
- **SMTP_*** (optional): If **`SMTP_HOST`** is set, **`SMTP_FROM`** and **`SMTP_TO`** are required; see `--help` epilog. Leave **`SMTP_HOST`** empty to disable email and only print new lines.

## First-time bootstrap

From the repo root (so **`notifier.toml`** is picked up), mark everything that currently matches your TOML filters as already seen:

```bash
python -m internship_notifier --bootstrap
```

Without `notifier.toml` (or with **`--no-config-file`**), pass flags explicitly, for example:

```bash
python -m internship_notifier --source summer2026 --category "Software Engineering" --bootstrap
```

Then on a schedule (or manually), from the repo root:

```bash
python -m internship_notifier
```

Use **`--dry-run`** to print what would count as new and whether an email would be sent, **without** writing state.

State defaults to a JSON file under your OS app data directory (see `internship_notifier.state.default_state_path`). Override with **`--state-path`**.

## Windows Task Scheduler

1. Open Task Scheduler → **Create Task** (not Basic), General tab: name the task, choose “Run whether user is logged on or not” if you want it unattended.
2. **Triggers**: New → Daily or “Repeat task every” 15–30 minutes (your choice).
3. **Actions**: Start a program  
   - **Program**: `python` (or full path to `python.exe`)  
   - **Add arguments**: `-m internship_notifier` (uses `notifier.toml` in **Start in**)  
   - **Start in**: path to this repo (folder containing `pyproject.toml`).
4. Set the task’s **Start in** to the repo folder so **`.env`** is found when the CLI loads it. Alternatively set system/user **`DOTENV_PATH`** to your env file, or define variables in the task’s **Environment** tab if your Windows edition exposes it.

Ensure the account running the task can reach the internet and that SMTP credentials are available if you use email.

## GitHub Actions (scheduled email)

This repo includes [`.github/workflows/notifier.yml`](.github/workflows/notifier.yml), which runs the notifier on a **cron** (every **20 minutes** in the default file; edit `cron` to taste) and **commits** [`.github/internship-notifier-state.json`](.github/internship-notifier-state.json) when it changes so `seen_ids` and `listings_sha` survive between runs. Pushes from `github-actions[bot]` using the default `GITHUB_TOKEN` do **not** re-trigger workflows, so you avoid infinite loops. **Which roles to watch** comes from committed [`notifier.toml`](notifier.toml) at the repo root—edit that file and push; no Actions “variables” are required for filters.

### One-time setup

1. Push this repo to GitHub and ensure **Actions are enabled** (forks default to disabled for scheduled workflows).
2. Edit **`notifier.toml`** on the default branch so `source` / `categories` / `all_categories` match what you want, then push.
3. **Repository secrets** (Settings → Secrets and variables → Actions → *Secrets*) for email — same names as in [`env.example`](env.example): `SMTP_HOST`, `SMTP_FROM`, `SMTP_TO`, and usually `SMTP_USER` / `SMTP_PASSWORD`. Optional: `SMTP_PORT`, `SMTP_SUBJECT_PREFIX`. If `SMTP_HOST` is unset, the job still runs but only prints new lines in the log (no email).
4. **Manual bootstrap** (recommended after changing `notifier.toml`): Actions → *Internship notifier* → **Run workflow**, enable **bootstrap**, run once. The workflow also **auto-bootstraps** when `seen_ids` is empty (including the initial checked-in file).

### Changing the schedule

Edit the `cron` line under `schedule` in [`.github/workflows/notifier.yml`](.github/workflows/notifier.yml). GitHub allows frequent schedules, but high-frequency runs can queue during load spikes.

### Branch protection

If `main` requires pull requests and blocks direct pushes, the “Commit state” step will fail until you allow **GitHub Actions** to push (for example bypass rules for `github-actions[bot]`) or use a **Personal Access Token** secret with `contents: write` checked out instead of the default token (more setup).

### Local vs Actions

You can still use **Task Scheduler** and the default user state path on your PC; the Actions workflow is independent and uses only the committed `.github/internship-notifier-state.json`.
