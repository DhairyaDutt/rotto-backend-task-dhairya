import os


def _proxy_url():
    url = os.environ.get("PROXY_URL")
    if url:
        return url
    host = os.environ.get("PROXY_HOST")
    user = os.environ.get("PROXY_USERNAME")
    password = os.environ.get("PROXY_PASSWORD")
    port = os.environ.get("PROXY_PORT", "7777")
    if host and user and password:
        return f"http://{user}:{password}@{host}:{port}"
    return None


PROXY_URL = _proxy_url()
DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://rotto:rotto@localhost:5432/rotto"
)

MAX_REQUESTS = int(os.environ.get("MAX_REQUESTS", "900"))
MAX_MEGABYTES = int(os.environ.get("MAX_MEGABYTES", "1200"))
MAX_RETRIES = int(os.environ.get("MAX_RETRIES", "3"))
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "30"))