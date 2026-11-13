"""Poll quant-scraper README tables and match them to SimplifyJobs apply URLs.

The quant-scraper org publishes internship tables in README.md, not
``listings.json``. Rows are kept only when they are internships. Graduate
roles and insight programmes are omitted. A row is treated as already listed
in SimplifyJobs when an apply-URL key intersects that repo's listings.

When a country filter is on, country codes come from the same roles on
quant-scraper.com. Each location there is labeled ``US``, ``CA``, ``GB``,
and so on. The README only has the city name.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from internship_notifier import github_listings

# Public listing repos in https://github.com/orgs/quant-scraper/repositories.
# ``.github`` is the org profile repo and has no roles.
QUANT_SCRAPER_REPOS: tuple[str, ...] = (
    "quant-scraper/Quant-Internships-New-Grad-2027",
    "quant-scraper/AI-ML-Internships-New-Grad-2027",
    "quant-scraper/Big-Tech-Internships-New-Grad-2027",
)
QUANT_SCRAPER_REF = "main"
README_PATH = "README.md"
# Same three lists as the README repos, with a country code on every city.
QUANT_SCRAPER_SITES: tuple[str, ...] = (
    "https://quant-scraper.com/quant",
    "https://quant-scraper.com/ai",
    "https://quant-scraper.com/big-tech",
)

_README_TIMEOUT_SECONDS = 60
_SITE_TIMEOUT_SECONDS = 120
_JOB_OBJECT = '{"id":'
_HEADING_SPLIT = re.compile(r"\s+[—–-]\s+")
_SEPARATOR_CELL = re.compile(r":?-{3,}:?")
_MD_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_SUMMARY_LABEL = re.compile(r"<summary>\s*<b>(.*?)</b>", re.IGNORECASE)
_TAG_TEXT = re.compile(r"<[^>]+>")
_OFF_SEASON_TITLE = re.compile(r"\b(?:fall|winter|spring|off[-\s]?cycle)\b", re.IGNORECASE)
_SUMMER_TITLE = re.compile(r"\bsummer\b", re.IGNORECASE)
_UUID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)
_WORKDAY_REQ = re.compile(r"_(JR\d+|R\d{4,}|\d{5,})(?:-\d+)?$", re.IGNORECASE)
_LOCALE_PREFIX = re.compile(r"/[a-z]{2}-[a-z]{2}(?=/)", re.IGNORECASE)

_SOFTWARE_FOCUSES = frozenset(
    {
        "software",
        "systems",
        "security",
        "embedded & firmware",
        "forward deployed & solutions",
        "qa & testing",
    }
)
_AI_FOCUSES = frozenset(
    {
        "ai / ml",
        "data",
        "inference & performance",
        "post-training & rl",
        "agents",
        "multimodal",
        "robotics",
    }
)
_HARDWARE_FOCUSES = frozenset({"hardware", "mechanical & manufacturing"})
_QUANT_FOCUSES = frozenset({"quant"})
_PRODUCT_FOCUSES = frozenset({"product"})
_DROPPED_FOCUSES = frozenset({"support"})
_DROPPED_DISCIPLINES = frozenset(
    {"operations, risk & other", "design", "programmes & insight weeks"}
)
# Trading and research sections are traders or researchers. A software focus
# inside those sections is quant-dev or SWE and stays.
_TRADING_OR_RESEARCH = frozenset({"trading", "quantitative research"})
_DEV_DISCIPLINES = frozenset({"software & technology", "engineering & technology"})

_KEY_PRIORITY: tuple[str, ...] = (
    "greenhouse:",
    "ashby:",
    "lever:",
    "workday:",
    "amazon:",
    "meta:",
    "apple:",
    "smartrecruiters:",
    "url:",
)


@dataclass(frozen=True)
class QuantScraperPoll:
    """README blob SHAs and parsed internships from one quant-scraper check.

    Attributes:
        shas: Repo ``owner/name`` to README blob SHA.
        listings: Parsed internship rows. Empty when ``changed`` is false.
        changed: True when a README SHA changed or the caller forced a download.
    """

    shas: dict[str, str]
    listings: list[dict[str, Any]]
    changed: bool


def url_keys(url: str) -> set[str]:
    """Return identity keys for one apply URL.

    ATS job ids are preferred so a Greenhouse path and a ``gh_jid`` query for
    the same posting match. A normalized host-and-path key is always included.
    """
    text = url.strip()
    if not text:
        return set()
    parsed = urllib.parse.urlsplit(text)
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return set()
    path = urllib.parse.unquote(parsed.path).rstrip("/")
    query = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=False))
    keys: set[str] = set()

    greenhouse_id = ""
    if "greenhouse.io" in host:
        match = re.search(r"/jobs/(\d+)", path)
        if match:
            greenhouse_id = match.group(1)
    gh_jid = query.get("gh_jid", "")
    if gh_jid.isdigit():
        greenhouse_id = greenhouse_id or gh_jid
        keys.add(f"greenhouse:{gh_jid}")
    if greenhouse_id:
        keys.add(f"greenhouse:{greenhouse_id}")

    uuid_match = _UUID.search(path)
    if uuid_match and "ashbyhq.com" in host:
        keys.add(f"ashby:{uuid_match.group(0).lower()}")
    if uuid_match and "lever.co" in host:
        keys.add(f"lever:{uuid_match.group(0).lower()}")

    if "myworkdayjobs.com" in host:
        req = _WORKDAY_REQ.search(path)
        if req:
            keys.add(f"workday:{host}:{req.group(1).upper()}")

    if "amazon.jobs" in host:
        match = re.search(r"/jobs/(\d+)", path)
        if match:
            keys.add(f"amazon:{match.group(1)}")
    if "metacareers.com" in host:
        match = re.search(r"/jobs/(\d+)", path)
        if match:
            keys.add(f"meta:{match.group(1)}")
    if "jobs.apple.com" in host:
        match = re.search(r"/details/(\d+)", path, re.IGNORECASE)
        if match:
            keys.add(f"apple:{match.group(1)}")
    if "smartrecruiters.com" in host:
        match = re.search(r"/(\d{6,})$", path)
        if match:
            keys.add(f"smartrecruiters:{match.group(1)}")

    normalized_path = _LOCALE_PREFIX.sub("", path).lower()
    keys.add(f"url:{host}{normalized_path}")
    return keys


def canonical_apply_key(keys: set[str]) -> str:
    """Pick one stable key, preferring an ATS job id over the raw path."""

    def rank(key: str) -> tuple[int, str]:
        for index, prefix in enumerate(_KEY_PRIORITY):
            if key.startswith(prefix):
                return (index, key)
        return (len(_KEY_PRIORITY), key)

    if not keys:
        raise ValueError("apply keys must be non-empty")
    return min(keys, key=rank)


def collect_url_keys(listings: list[dict[str, Any]]) -> set[str]:
    """Collect apply-URL keys from listing ``url`` fields."""
    keys: set[str] = set()
    for listing in listings:
        keys |= url_keys(str(listing.get("url") or ""))
    return keys


def keep_for_source(listing: dict[str, Any], source: str) -> bool:
    """Return whether one parsed internship matches the configured season source.

    Titles with no season are treated as summer postings. ``all`` keeps every
    internship row the parser emitted.
    """
    if listing.get("listing_kind") != "internship":
        return False
    season = listing.get("season")
    if source == "all":
        return True
    if source == "summer2027":
        return season in {"summer", "unspecified"}
    if source == "offseason":
        return season == "offseason"
    return False


def parse_readme(markdown: str, *, repo: str) -> list[dict[str, Any]]:
    """Parse internship rows from one quant-scraper README.

    Graduate-role and programme sections are skipped. Operations, design, and
    support rows are skipped because they have no SimplifyJobs category.
    Company cells of ``↳`` continue the previous company.
    """
    listings: list[dict[str, Any]] = []
    section: str | None = None
    discipline = ""
    focus_group = ""
    columns: dict[str, int] = {}
    company = ""

    for line in markdown.splitlines():
        heading = _heading(line)
        if heading is not None:
            level, text = heading
            if level == 2:
                section = "internship" if text.casefold().startswith("internship") else None
                discipline = ""
                focus_group = ""
                columns = {}
                company = ""
            elif level == 3:
                discipline = _discipline_name(text)
                focus_group = ""
                columns = {}
                company = ""
            continue

        summary = _SUMMARY_LABEL.search(line)
        if summary:
            focus_group = _TAG_TEXT.sub("", summary.group(1)).strip()
            columns = {}
            continue
        if line.strip().lower().startswith("</details>"):
            focus_group = ""
            columns = {}
            continue
        if section != "internship" or not line.startswith("|"):
            continue

        cells = _split_row(line)
        if not cells or _is_separator(cells):
            continue
        if cells[0].casefold() == "company":
            columns = {cell.casefold(): index for index, cell in enumerate(cells)}
            company = ""
            continue
        if "role" not in columns or "company" not in columns:
            continue

        company_cell = _cell(cells, columns, "company")
        if _is_continuation(company_cell):
            if not company:
                continue
        else:
            parsed_company = _company_name(company_cell)
            if not parsed_company:
                continue
            company = parsed_company

        role = _role(_cell(cells, columns, "role"))
        if role is None:
            continue
        title, apply_url = role
        focus = _cell(cells, columns, "focus") or focus_group
        category = _category_for(discipline, focus)
        if category is None:
            continue
        keys = url_keys(apply_url)
        if not keys:
            continue
        season = _season_of(title)
        listings.append(
            {
                "id": f"qs:{canonical_apply_key(keys)}",
                "apply_keys": sorted(keys),
                "company_name": company,
                "title": title,
                "category": category,
                "url": apply_url,
                "locations": _locations(_cell(cells, columns, "location")),
                "terms": _terms_for(season),
                "season": season,
                "listing_kind": "internship",
                "active": True,
                "is_visible": True,
                "date_posted": 0,
                "company_url": "",
                "source": repo,
            }
        )
    return listings


def fetch_quant_scraper(
    previous_shas: dict[str, str],
    *,
    force: bool = False,
    annotate_countries: bool = False,
) -> QuantScraperPoll:
    """Fetch quant-scraper READMEs when their blob SHAs changed.

    Metadata is always requested. README bodies are downloaded only when a SHA
    differs from ``previous_shas`` or ``force`` is true. When
    ``annotate_countries`` is true, each parsed row also gets a ``countries``
    list from quant-scraper.com, matched on the apply URL.

    Args:
        previous_shas: Last saved README SHAs keyed by ``owner/name``.
        force: Download bodies even when every SHA matches. Used once, before
            the supplemental source has been bootstrapped.
        annotate_countries: Look up country codes for the parsed rows.

    Returns:
        SHAs, parsed internships, and whether bodies were read.

    Raises:
        RuntimeError: If GitHub metadata, a README, or the country pages fail,
            or if country data matches none of the parsed apply URLs.
    """
    metas = {repo: _readme_metadata(repo) for repo in QUANT_SCRAPER_REPOS}
    shas = {repo: meta["sha"] for repo, meta in metas.items()}
    if shas == previous_shas and not force:
        return QuantScraperPoll(shas=shas, listings=[], changed=False)

    listings: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for repo, meta in metas.items():
        raw = github_listings._http_get(
            meta["download_url"],
            timeout=_README_TIMEOUT_SECONDS,
        )
        for listing in parse_readme(raw.decode("utf-8"), repo=repo):
            listing_id = str(listing["id"])
            if listing_id in seen_ids:
                continue
            seen_ids.add(listing_id)
            listings.append(listing)
    if annotate_countries:
        _annotate_countries(listings, _fetch_country_index())
    return QuantScraperPoll(shas=shas, listings=listings, changed=True)


def country_index_from_html(html: str) -> dict[str, set[str]]:
    """Map apply-URL keys to country codes embedded in a QuantScraper page.

    The page is a Next.js document. Role objects inside it carry
    ``locations[].country`` (``US``, ``GB``, ``CA``, and so on). The returned
    index includes every role on the page, not only internships, so a README
    row still matches when the page uses a different level label.
    """
    start = html.find("self.__next_f")
    decoded = html[start if start >= 0 else 0 :].replace('\\"', '"')
    index: dict[str, set[str]] = {}
    for url, codes in _jobs_in_page(decoded):
        for key in url_keys(url):
            index.setdefault(key, set()).update(codes)
    return index


def countries_for_apply_url(url: str, index: dict[str, set[str]]) -> set[str]:
    """Return country codes for one apply URL.

    An ATS job id is preferred. The plain host-and-path key is used only when
    no ATS id from this URL is in the index.
    """
    keys = url_keys(url)
    specific = [key for key in keys if not key.startswith("url:")]
    found: set[str] = set()
    for key in specific:
        found |= index.get(key, set())
    if found:
        return found
    for key in keys:
        found |= index.get(key, set())
    return found


def _annotate_countries(
    listings: list[dict[str, Any]],
    index: dict[str, set[str]],
) -> None:
    """Set ``countries`` on each row from the website index."""
    matched = 0
    for listing in listings:
        codes = countries_for_apply_url(str(listing.get("url") or ""), index)
        listing["countries"] = sorted(codes)
        if codes:
            matched += 1
    if listings and matched == 0:
        raise RuntimeError(
            "QuantScraper country data did not match any internship apply URL"
        )


def _fetch_country_index() -> dict[str, set[str]]:
    """Download the three QuantScraper pages and merge their country indexes."""
    index: dict[str, set[str]] = {}
    for url in QUANT_SCRAPER_SITES:
        page_index = country_index_from_html(_site_html(url))
        if not page_index:
            raise RuntimeError(f"QuantScraper page {url} had no location countries")
        for key, codes in page_index.items():
            index.setdefault(key, set()).update(codes)
    return index


def _site_html(url: str) -> str:
    """GET one QuantScraper HTML page."""
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": github_listings.USER_AGENT,
            "Accept": "text/html",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=_SITE_TIMEOUT_SECONDS) as response:
            return response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        raise RuntimeError(f"HTTP {exc.code} for {url}: {body[:500]}") from exc


def _jobs_in_page(decoded: str) -> list[tuple[str, set[str]]]:
    """Yield apply URL and country codes for each role object in page text."""
    found: list[tuple[str, set[str]]] = []
    pos = 0
    while True:
        start = decoded.find(_JOB_OBJECT, pos)
        if start < 0:
            break
        end = _json_object_end(decoded, start)
        if end is None:
            pos = start + len(_JOB_OBJECT)
            continue
        try:
            obj = json.loads(decoded[start:end])
        except json.JSONDecodeError:
            pos = start + len(_JOB_OBJECT)
            continue
        pos = end
        if not isinstance(obj, dict):
            continue
        url = obj.get("url")
        locations = obj.get("locations")
        if not isinstance(url, str) or not isinstance(locations, list):
            continue
        codes = {
            loc.get("country")
            for loc in locations
            if isinstance(loc, dict)
            and isinstance(loc.get("country"), str)
            and loc.get("country")
        }
        if codes:
            found.append((url, codes))
    return found


def _json_object_end(text: str, start: int) -> int | None:
    """Return the index after the object that begins at ``start``."""
    depth = 0
    in_string = False
    escaped = False
    limit = min(len(text), start + 100_000)
    for index in range(start, limit):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index + 1
    return None


def _readme_metadata(repo: str) -> github_listings.ListingsMetadata:
    """Return the Contents API metadata for one repo's README."""
    owner, name = repo.split("/", 1)
    encoded = urllib.parse.quote(README_PATH)
    url = (
        f"https://api.github.com/repos/{owner}/{name}/contents/{encoded}"
        f"?ref={urllib.parse.quote(QUANT_SCRAPER_REF)}"
    )
    payload = github_listings._http_get(url, timeout=_README_TIMEOUT_SECONDS)
    data = json.loads(payload.decode("utf-8"))
    sha = data.get("sha")
    download_url = data.get("download_url")
    if not isinstance(sha, str) or not isinstance(download_url, str):
        raise RuntimeError(f"GitHub README metadata for {repo} is missing sha or download_url")
    return {"sha": sha, "size": int(data.get("size") or 0), "download_url": download_url}


def _heading(line: str) -> tuple[int, str] | None:
    """Return ``(level, text)`` for an ATX heading line."""
    stripped = line.strip()
    if not stripped.startswith("#"):
        return None
    marks, _, text = stripped.partition(" ")
    if not marks or any(char != "#" for char in marks) or not text.strip():
        return None
    return len(marks), text.strip()


def _discipline_name(heading: str) -> str:
    """Return the discipline portion of a ``Trading — internships`` heading."""
    return _HEADING_SPLIT.split(heading, maxsplit=1)[0].strip().casefold()


def _split_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _is_separator(cells: list[str]) -> bool:
    return bool(cells) and all(_SEPARATOR_CELL.fullmatch(cell.replace(" ", "")) for cell in cells)


def _cell(cells: list[str], columns: dict[str, int], name: str) -> str:
    index = columns.get(name)
    if index is None or index >= len(cells):
        return ""
    return cells[index]


def _is_continuation(cell: str) -> bool:
    token = cell.replace("*", "").strip()
    return token in {"", "↳"}


def _company_name(cell: str) -> str | None:
    match = _MD_LINK.search(cell)
    if match:
        return match.group(1).strip() or None
    plain = cell.replace("**", "").strip()
    return plain or None


def _role(cell: str) -> tuple[str, str] | None:
    match = _MD_LINK.search(cell)
    if not match:
        return None
    title = match.group(1).strip()
    url = match.group(2).strip()
    if not title or not url.startswith("http"):
        return None
    return title, url


def _locations(cell: str) -> list[str]:
    text = cell.strip()
    if not text or text == "—":
        return []
    return [part.strip() for part in text.split(",") if part.strip()]


def _season_of(title: str) -> str:
    off_season = _OFF_SEASON_TITLE.search(title) is not None
    summer = _SUMMER_TITLE.search(title) is not None
    if off_season and not summer:
        return "offseason"
    if summer:
        return "summer"
    return "unspecified"


def _terms_for(season: str) -> list[str]:
    if season == "summer":
        return ["Summer 2027"]
    if season == "offseason":
        return ["Off-season"]
    return ["Internship"]


def _category_for(discipline: str, focus: str) -> str | None:
    """Map a quant-scraper discipline and focus onto a SimplifyJobs category.

    Trader and quant-researcher rows are omitted here. SimplifyJobs still
    applies its own category filter, so its Quant listings are unchanged.
    A software focus in a trading or research section, and a Quant focus
    under software or engineering, is quant dev or SWE.
    """
    if discipline in _DROPPED_DISCIPLINES:
        return None
    focus_name = focus.strip().casefold()
    if focus_name in _DROPPED_FOCUSES:
        return None
    if discipline in _TRADING_OR_RESEARCH:
        if focus_name in _SOFTWARE_FOCUSES:
            return "Software Engineering"
        if focus_name in _HARDWARE_FOCUSES:
            return "Hardware Engineering"
        return None
    if focus_name in _HARDWARE_FOCUSES:
        return "Hardware Engineering"
    if focus_name in _PRODUCT_FOCUSES:
        return "Product Management"
    if focus_name in _AI_FOCUSES:
        return "Data Science, AI & Machine Learning"
    if focus_name in _QUANT_FOCUSES and discipline in _DEV_DISCIPLINES:
        return "Software Engineering"
    if focus_name in _QUANT_FOCUSES:
        return None
    if focus_name in _SOFTWARE_FOCUSES:
        return "Software Engineering"
    if discipline == "research":
        return "Data Science, AI & Machine Learning"
    if discipline in _DEV_DISCIPLINES:
        return "Software Engineering"
    if discipline == "product":
        return "Product Management"
    return None
