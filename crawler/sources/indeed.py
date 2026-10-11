import json
import re
from datetime import datetime, timezone
from urllib.parse import quote

from ..textutil import content_hash, html_to_text

ALLOWED_TYPES = {
    "full-time": "full time",
    "part-time": "part time",
    "contract": "contract",
    "temporary": "temporary",
}
SALARY_PERIODS = {"YEARLY": "year", "DAILY": "day", "HOURLY": "hour"}


def _initial_data(html):
    m = re.search(r"window\._initialData\s*=\s*(\{)", html)
    if not m:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(html[m.start(1):])
        return obj
    except ValueError:
        return None


def _work_type(section):
    found = set()
    for t in (section or {}).get("jobTypes") or []:
        mapped = ALLOWED_TYPES.get((t.get("label") or "").strip().lower())
        if mapped:
            found.add(mapped)
    return found.pop() if len(found) == 1 else None


def _salary(info):
    if not info or info.get("salaryCurrency") != "GBP":
        return None, None, None
    period = SALARY_PERIODS.get(info.get("salaryType"))
    if period is None:
        return None, None, None
    lo, hi = info.get("salaryMin"), info.get("salaryMax")
    # Indeed uses -1 / 0 as "not given": "up to 50000" arrives as min 0, max 50000;
    # "from 27000" arrives as min 27000, max -1.
    lo = lo if lo is not None and lo > 0 else None
    hi = hi if hi is not None and hi > 0 else None
    if lo is None and hi is None:
        return None, None, None
    if lo is not None and hi is not None and hi < lo:
        hi = lo
    if hi is None:
        hi = lo            # a single stated figure: min = max (as on Reed)
    return lo, hi, period


def _posted_at(card):
    ms = (card or {}).get("createDate")
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat()


def parse_job(html, expected_jk, card=None):
    """Returns (job_dict_or_None, state). state: ok | expired | not_uk | bad_page.
    card: the search-result card for this job (gives the posted date)."""
    data = _initial_data(html)
    try:
        body = data["autoOpenTwoPaneViewjobResponse"]["body"]
        if body.get("jobKey") != expected_jk:
            return None, "bad_page"
        info = body["jobInfoWrapperModel"]["jobInfoModel"]
        header = info["jobInfoHeaderModel"]
        section = info["jobDescriptionSectionModel"]["jobDetailsSection"]
    except (TypeError, KeyError, AttributeError):
        return None, "bad_page"

    if section.get("isJobExpired") or info.get("showExpiredHeader"):
        return None, "expired"
    if body.get("jobCountry") != "GB":
        return None, "not_uk"

    title = (body.get("jobTitle") or "").strip()
    company = (header.get("companyName") or "").strip() or None
    location = (header.get("formattedLocation") or body.get("jobLocation") or "").strip()
    description = html_to_text(info.get("sanitizedJobDescription"))
    if not (title and location and description):
        return None, "bad_page"

    sal_min, sal_max, sal_period = _salary(body.get("salaryInfoModel"))
    work_type = _work_type(section)
    logo = (header.get("companyImagesModel") or {}).get("logoUrl") or None

    job = {
        "id": f"indeed:{expected_jk}",
        "source": "indeed",
        "source_job_id": expected_jk,
        "url": f"https://uk.indeed.com/viewjob?jk={expected_jk}",
        "title": title,
        "company": company,
        "company_logo_url": logo,
        "location": location,
        "work_type": work_type,
        "salary_min": sal_min,
        "salary_max": sal_max,
        "salary_period": sal_period,
        "posted_at": _posted_at(card),
        "description": description,
        "content_hash": content_hash(title, company, location, work_type,
                                     sal_min, sal_max, sal_period, description),
    }
    return job, "ok"

def discover(html):
    """Search page -> {jobkey: {"createDate": ms}} from the embedded job cards.
    Only the posted date is kept from each card (the rest is not needed)."""
    cards = {}
    m = re.search(r'window\.mosaic\.providerData\["mosaic-provider-jobcards"\]\s*=\s*(\{)', html)
    if not m:
        return cards
    try:
        obj, _ = json.JSONDecoder().raw_decode(html[m.start(1):])
        results = obj["metaData"]["mosaicProviderJobCardsModel"]["results"]
    except (ValueError, KeyError, TypeError):
        return cards
    for r in results:
        jk = r.get("jobkey")
        if jk and not r.get("expired"):
            cards[jk] = {"createDate": r.get("createDate")}
    return cards


def next_page(html):
    """Relative link to the next results page, or None on the last page."""
    data = _initial_data(html)
    try:
        current = data.get("pageNum") or 1
        for link in data["pageLinks"]:
            if link.get("label") == current + 1 and link.get("href"):
                href = link["href"]
                if link.get("pp"):
                    href += "&pp=" + quote(link["pp"], safe="")
                return href
    except (TypeError, KeyError, AttributeError):
        pass
    return None
