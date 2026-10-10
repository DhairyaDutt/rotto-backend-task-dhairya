import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from ..textutil import content_hash, html_to_text

LOGO_BASE = "https://resources.reed.co.uk/profileimages/logos/thumbs/"  # unverified guess
PERIODS = {"per annum": "year", "per day": "day", "per hour": "hour"}
LONDON = ZoneInfo("Europe/London")


def _next_data(html):
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    return json.loads(m.group(1)) if m else None


def _dt(value):
    if not value:
        return None
    return datetime.fromisoformat(value).replace(tzinfo=LONDON).isoformat()


def _work_type(d):
    if d.get("isContract"):
        return "contract"
    if d.get("isTemp"):
        return "temporary"
    hours = d.get("jobEmploymentHours") or {}
    if hours.get("isPartTime"):
        return "part time"
    if hours.get("isFullTime"):
        return "full time"
    return None


def _salary(d):
    s = d.get("jobSalary") or {}
    period = PERIODS.get(((s.get("type") or {}).get("name") or "").lower())
    if not s.get("displaySalary") or period is None:
        return None, None, None
    return s.get("from"), s.get("to"), period


def parse_job(html, expected_id):
    """Returns (job_dict_or_None, state). state: ok | expired | not_uk | bad_page"""
    data = _next_data(html)
    try:
        d = data["props"]["pageProps"]["consolidatedJobDetails"]["jobDetails"]
    except (TypeError, KeyError):
        return None, "bad_page"
    if str(d.get("id")) != str(expected_id):
        return None, "bad_page"
    if not d.get("isLive"):
        return None, "expired"

    loc = d.get("jobLocation") or {}
    if loc.get("countryCode") != "GB":
        return None, "not_uk"

    owner = d.get("jobOwner") or {}
    company = owner.get("profileName") or owner.get("ouName") or None
    logo = owner.get("profileLogoImage")
    location = ", ".join(x for x in (loc.get("locationName"), loc.get("countyName")) if x)
    description = html_to_text(d.get("description"))
    sal_min, sal_max, sal_period = _salary(d)
    work_type = _work_type(d)
    title = (d.get("title") or "").strip()

    if not (title and location and description):
        return None, "bad_page"

    job = {
        "id": f"reed:{d['id']}",
        "source": "reed",
        "source_job_id": str(d["id"]),
        "url": data["props"]["pageProps"].get("canonicalUrl"),
        "title": title,
        "company": company,
        "company_logo_url": (LOGO_BASE + logo) if logo else None,
        "location": location,
        "work_type": work_type,
        "salary_min": sal_min,
        "salary_max": sal_max,
        "salary_period": sal_period,
         "posted_at": _dt(d.get("displayDate") or d.get("createdDate")),
        "description": description,
        "content_hash": content_hash(title, company, location, work_type,
                                     sal_min, sal_max, sal_period, description),
        "expiry_date": _dt(d.get("expiryDate")),
    }
    return job, "ok"