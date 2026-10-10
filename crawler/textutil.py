import hashlib
import re

from selectolax.lexbor import LexborHTMLParser as HTMLParser


def html_to_text(html):
    """Turn an HTML fragment into plain text, keeping line breaks."""
    if not html:
        return ""
    html = re.sub(r"(?i)<br\s*/?>", "\n", html)
    html = re.sub(r"(?i)<li[^>]*>", "\n- ", html)
    html = re.sub(r"(?i)</(p|ul|ol|div|h[1-6]|tr)>", "\n", html)
    text = HTMLParser(html).text(strip=False)
    text = text.replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.split("\n")]
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def content_hash(*parts):
    """Fingerprint of the content fields. Dates are never passed in."""
    joined = "\x1f".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()