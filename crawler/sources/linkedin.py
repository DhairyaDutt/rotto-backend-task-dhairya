import re
from datetime import datetime, timedelta, timezone

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from ..textutil import content_hash, html_to_text

ALLOWED_TYPES = {
    "full-time": "full time",
    "part-time": "part time",
    "contract": "contract",
    "temporary": "temporary",
}
UNITS = {"minute": timedelta(minutes=1), "hour": timedelta(hours=1),
         "day": timedelta(days=1), "week": timedelta(weeks=1),
         "month": timedelta(days=30), "year": timedelta(days=365)}


def _text(node):
    return node.text(strip=True) if node else None


def _relative_date(text, fetched_at):
    m = re.search(r"(\d+)\s+(minute|hour|day|week|month|year)s?\s+ago", text or "")
    if not m:
        return None
    return (fetched_at - int(m.group(1)) * UNITS[m.group(2)]).isoformat()


def _employment_type(tree):
    for item in tree.css("li.description__job-criteria-item"):
        head = _text(item.css_first("h3"))
        if head and head.lower() == "employment type":
            value = (_text(item.css_first("span")) or "").lower()
            return ALLOWED_TYPES.get(value)
    return None


def parse_job(html, expected_id, fetched_at=None, posted_date=None):
    """Returns (job_dict_or_None, state). state: ok | not_uk | bad_page.
    posted_date: 'YYYY-MM-DD' from the search card, if we have it."""
    fetched_at = fetched_at or datetime.now(timezone.utc)
    tree = HTMLParser(html)
    if str(expected_id) not in html:
        return None, "bad_page"

    title = _text(tree.css_first(".top-card-layout__title"))
    markup = tree.css_first(".show-more-less-html__markup")
    if not title or not markup:
        return None, "bad_page"

    company = _text(tree.css_first("a.topcard__org-name-link"))
    location = _text(tree.css_first("span.topcard__flavor--bullet"))
    if not location:
        return None, "bad_page"
    if "united kingdom" not in location.lower():
        return None, "not_uk"

    description = html_to_text(markup.inner_html)
    if not description:
        return None, "bad_page"

    logo_node = tree.css_first("img.artdeco-entity-image")
    logo = None
    if logo_node:
        logo = logo_node.attributes.get("data-delayed-url") or None

    if posted_date:
        posted_at = datetime.fromisoformat(posted_date).replace(
            tzinfo=timezone.utc).isoformat()
    else:
        posted_at = _relative_date(
            _text(tree.css_first(".posted-time-ago__text")), fetched_at)

    m = re.search(
        r'https://[a-z.]*linkedin\.com/jobs/view/[^"?\s]*-%s' % re.escape(str(expected_id)),
        html)
    url = m.group(0) if m else f"https://www.linkedin.com/jobs/view/{expected_id}"

    work_type = _employment_type(tree)
    job = {
        "id": f"linkedin:{expected_id}",
        "source": "linkedin",
        "source_job_id": str(expected_id),
        "url": url,
        "title": title,
        "company": company,
        "company_logo_url": logo,
        "location": location,
        "work_type": work_type,
        "salary_min": None,      # TODO: check a salaried job
        "salary_max": None,
        "salary_period": None,
        "posted_at": posted_at,
        "description": description,
        "content_hash": content_hash(title, company, location, work_type,
                                     None, None, None, description),
    }
    return job, "ok"

def discover(html):
    """Search page -> {job_id: 'YYYY-MM-DD' or None}, read card by card,
    so each date belongs to the same card as its job id."""
    tree = HTMLParser(html)
    found = {}
    for card in tree.css("div.base-search-card"):
        m = re.search(r"jobPosting:(\d+)", card.attributes.get("data-entity-urn") or "")
        if not m:
            continue
        t = card.css_first("time")
        date = (t.attributes.get("datetime") or "") if t else ""
        found[m.group(1)] = date if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date) else None
    return found