import json
import re
from datetime import datetime, timezone

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
    if period is None or info.get("salaryMin") is None:
        return None, None, None
    return info["salaryMin"], info.get("salaryMax") or info["salaryMin"], period


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