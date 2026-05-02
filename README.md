# internship-notifier

Polls the upstream [`SimplifyJobs/Summer2026-Internships`](https://github.com/SimplifyJobs/Summer2026-Internships) `listings.json` on branch `dev`, applies the same **summer** or **off-season** rules as the repo READMEs, optionally filters by **category**, and tracks **seen listing IDs** so you only get **new** rows on stdout (and optionally by **email**).

## Install

Requires **Python 3.11+**.

```bash
cd "Internship Notifications"
python -m pip install -e .
```

Run `internship-notifier --help` or `python -m internship_notifier --help`.

## Environment variables

See [`env.example`](env.example) for names and short comments. Copy it to **`.env`** in the working directory (same folder you use as “Start in” for Task Scheduler, or the repo root when you run from a shell).

On startup the CLI loads **`.env`** in the current working directory via **`python-dotenv`** (same idea as npm’s `dotenv`). Existing real environment variables are **not** overwritten. If **`DOTENV_PATH`** is set (in the real environment or inside `.env`), a **second** file is loaded the same way, filling only variables that are still unset.

- **`GITHUB_TOKEN`** (optional): GitHub fine-grained or classic PAT with read access to public repos. Improves rate limits if you poll often.
- **SMTP_*** (optional): If **`SMTP_HOST`** is set, **`SMTP_FROM`** and **`SMTP_TO`** are required; see `--help` epilog. Leave **`SMTP_HOST`** empty to disable email and only print new lines.

## First-time bootstrap

Before normal runs, record every listing that currently matches your filters as already seen (no “new” lines, updates state):

```bash
python -m internship_notifier --source summer2026 --category "Software Engineering" --bootstrap
```

Use `--all-categories` instead of repeated `--category` if you want every category for that source.

Then on a schedule (or manually):

```bash
python -m internship_notifier --source summer2026 --category "Software Engineering"
```

Use **`--dry-run`** to print what would count as new and whether an email would be sent, **without** writing state.

State defaults to a JSON file under your OS app data directory (see `internship_notifier.state.default_state_path`). Override with **`--state-path`**.

## Windows Task Scheduler

1. Open Task Scheduler → **Create Task** (not Basic), General tab: name the task, choose “Run whether user is logged on or not” if you want it unattended.
2. **Triggers**: New → Daily or “Repeat task every” 15–30 minutes (your choice).
3. **Actions**: Start a program  
   - **Program**: `python` (or full path to `python.exe`)  
   - **Add arguments**: `-m internship_notifier --source summer2026 --category "Software Engineering"`  
   - **Start in**: path to this repo (folder containing `pyproject.toml`).
4. Set the task’s **Start in** to the repo folder so **`.env`** is found when the CLI loads it. Alternatively set system/user **`DOTENV_PATH`** to your env file, or define variables in the task’s **Environment** tab if your Windows edition exposes it.

Ensure the account running the task can reach the internet and that SMTP credentials are available if you use email.

## GitHub Actions (optional)

The same CLI can run on a **schedule** in GitHub Actions, but the runner disk is ephemeral: you need a strategy for **persisting `seen_ids` and `last_sha`** between runs (for example a gist, a small object store, or commits to a branch). Local Task Scheduler plus the default state file is simpler for a personal setup.
