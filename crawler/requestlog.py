import json
import os
from collections import Counter
from datetime import datetime, timezone


class RequestLog:
    def __init__(self, path="logs/requests.jsonl"):
        self.path = path
        self.records = []
        self.started_at = datetime.now(timezone.utc)
        os.makedirs(os.path.dirname(path), exist_ok=True)

    def record(self, source, kind, url, attempt, status, size_bytes,
               seconds, blocked=False, reason=None):
        rec = {
            "time": datetime.now(timezone.utc).isoformat(),
            "source": source,        # reed, linkedin or indeed
            "kind": kind,            # search or detail
            "url": url,
            "attempt": attempt,      # 1 = first try, 2 = first retry, ...
            "status": status,        # None if the request failed completely
            "bytes": size_bytes,
            "seconds": round(seconds, 2),
            "blocked": blocked,
            "reason": reason,        # why it counts as blocked or failed
        }
        self.records.append(rec)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")

    @property
    def total(self):
        return len(self.records)

    @property
    def total_bytes(self):
        return sum(r["bytes"] or 0 for r in self.records)

    def summary(self):
        by_status = Counter(
            str(r["status"]) if r["status"] is not None else "error"
            for r in self.records
        )
        return {
            "requests_total": self.total,
            "requests_blocked": sum(1 for r in self.records if r["blocked"]),
            "requests_by_status": dict(by_status),
            "bytes_total": self.total_bytes,
        }