import random
import re
import time
from dataclasses import dataclass

from curl_cffi import requests

from . import config
from .requestlog import RequestLog

BLOCK_STATUSES = {401, 403, 429, 999}
GONE_STATUSES = {404, 410}
BLOCK_TITLE_WORDS = ("blocked", "security check", "just a moment",
                     "authenticating", "access denied", "verify you")


class BudgetExceeded(Exception):
    """Raised when the request or byte limit is reached."""


@dataclass
class FetchResult:
    url: str
    status: int | None
    text: str
    ok: bool          # real, validated content
    gone: bool        # server said 404/410 (a real answer, not a block)
    blocked: bool
    reason: str | None


def _page_title(text):
    m = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
    return m.group(1).strip().lower() if m else ""


def _block_reason(status, text):
    if status in BLOCK_STATUSES:
        return f"status {status}"
    title = _page_title(text)
    if len(text) < 60000 and any(w in title for w in BLOCK_TITLE_WORDS):
        return f"challenge page: {title[:60]}"
    return None


class Fetcher:
    def __init__(self, log=None):
        if not config.PROXY_URL:
            raise RuntimeError("Proxy not configured. Set PROXY_URL or "
                               "PROXY_HOST/PROXY_USERNAME/PROXY_PASSWORD.")
        self.log = log or RequestLog()
        self.session = None

    def _new_session(self):
        proxy = config.PROXY_URL
        self.session = requests.Session(
            impersonate="chrome",
            proxies={"http": proxy, "https": proxy},
            timeout=config.REQUEST_TIMEOUT,
        )

    def _check_budget(self):
        if self.log.total >= config.MAX_REQUESTS:
            raise BudgetExceeded(f"request limit {config.MAX_REQUESTS} reached")
        if self.log.total_bytes >= config.MAX_MEGABYTES * 1_000_000:
            raise BudgetExceeded(f"byte limit {config.MAX_MEGABYTES} MB reached")

    def get(self, source, kind, url, validate=None, headers=None):
        """Download one page. validate(text) -> True if the content is real."""
        last = FetchResult(url, None, "", False, False, False, "not attempted")
        for attempt in range(1, config.MAX_RETRIES + 2):
            self._check_budget()
            if self.session is None:
                self._new_session()
            start = time.time()
            try:
                r = self.session.get(url, headers=headers)
            except Exception as e:
                reason = f"error: {type(e).__name__}"
                self.log.record(source, kind, url, attempt, None, 0,
                                time.time() - start, False, reason)
                last = FetchResult(url, None, "", False, False, False, reason)
                self._retry_pause(attempt)
                continue

            status, text, size = r.status_code, r.text, len(r.content)
            secs = time.time() - start

            if status in GONE_STATUSES:
                self.log.record(source, kind, url, attempt, status, size, secs)
                return FetchResult(url, status, text, False, True, False,
                                   f"status {status}")

            reason = _block_reason(status, text)
            blocked = reason is not None
            if reason is None and status >= 500:
                reason = f"server error {status}"
            if reason is None and status == 200 and validate and not validate(text):
                reason, blocked = "unexpected content", True
            if reason is None and status != 200:
                reason = f"unexpected status {status}"

            self.log.record(source, kind, url, attempt, status, size, secs,
                            blocked, reason)

            if reason is None:
                return FetchResult(url, status, text, True, False, False, None)

            last = FetchResult(url, status, text, False, False, blocked, reason)
            if reason.startswith("unexpected status"):
                return last          # other 4xx: retrying won't help
            self._retry_pause(attempt)
        return last

    def _retry_pause(self, attempt):
        self.session = None          # new connection, likely a fresh IP
        if attempt <= config.MAX_RETRIES:
            time.sleep(min(2 ** attempt, 20) + random.random())