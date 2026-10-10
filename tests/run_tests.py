"""make test: replay a lifecycle fixture into an empty, isolated database."""
import json
import os
import sys
from decimal import Decimal

import psycopg

from crawler import config, store

FIXTURE = os.environ.get("FIXTURE_PATH", "tests/fixtures/lifecycle.json")
TEST_DB = "rotto_test"
TIME_FIELDS = {"first_seen_at", "last_seen_at", "removed_at", "posted_at"}


def fresh_database():
    base = config.DATABASE_URL.rsplit("/", 1)[0]
    real_db = config.DATABASE_URL.rsplit("/", 1)[1].split("?")[0]
    if real_db == TEST_DB:
        sys.exit("DATABASE_URL points at the test database; refusing to continue")
    admin = psycopg.connect(f"{base}/postgres", autocommit=True)
    admin.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
    admin.execute(f"CREATE DATABASE {TEST_DB}")
    admin.close()
    conn = store.connect(f"{base}/{TEST_DB}")
    store.init_schema(conn)
    return conn


def same(actual, expected, field):
    if field in TIME_FIELDS:
        return actual == store.when(expected)
    if isinstance(actual, Decimal) and isinstance(expected, (int, float)):
        return float(actual) == float(expected)
    return actual == expected


def main():
    with open(FIXTURE, encoding="utf-8") as f:
        fixture = json.load(f)
    conn = fresh_database()
    errors = []
    hashes = {}  # run name -> {row id: content_hash}

    for run in fixture["runs"]:
        name = run["name"]
        before_errors = len(errors)
        counts = store.apply_run(conn, run)

        for key, want in run["expected"]["report"].items():
            if counts.get(key) != want:
                errors.append(f"[{name}] report {key}: got {counts.get(key)}, want {want}")

        rows = {r["id"]: r for r in conn.execute("SELECT * FROM jobs")}
        want_rows = run["expected"]["rows"]
        if set(rows) != set(want_rows):
            errors.append(f"[{name}] row ids: got {sorted(rows)}, want {sorted(want_rows)}")

        for row_id, expect in want_rows.items():
            row = rows.get(row_id)
            if row is None:
                continue
            for field, want in expect.items():
                if field == "content_hash_equals_run":
                    if want is not None:
                        ref = hashes.get(want, {}).get(row_id)
                        if row["content_hash"] != ref:
                            errors.append(f"[{name}] {row_id} hash should equal run '{want}'")
                    else:
                        prev = [h[row_id] for h in hashes.values() if row_id in h]
                        if prev and row["content_hash"] == prev[-1]:
                            errors.append(f"[{name}] {row_id} hash should have changed")
                elif not same(row[field], want, field):
                    errors.append(f"[{name}] {row_id}.{field}: got {row[field]!r}, want {want!r}")
        hashes[name] = {i: r["content_hash"] for i, r in rows.items()}

        # second application of the same run: nothing may change
        snap = conn.execute("SELECT * FROM jobs ORDER BY id").fetchall()
        again = store.apply_run(conn, run)
        if any(again.values()):
            errors.append(f"[{name}] second application counts not zero: {again}")
        if conn.execute("SELECT * FROM jobs ORDER BY id").fetchall() != snap:
            errors.append(f"[{name}] second application changed rows")

        print(f"{'PASS' if len(errors) == before_errors else 'FAIL'}  {name}  {counts}")

    for e in errors:
        print("  -", e)
    print("ALL CHECKS PASSED" if not errors else f"{len(errors)} PROBLEM(S)")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()