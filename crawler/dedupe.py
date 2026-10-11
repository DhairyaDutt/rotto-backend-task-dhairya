"""Link copies of the same job (same or different board) with one canonical_job_id.

Method, in plain words:
1. Normalise company, title and location.
2. Only compare jobs inside the same company block (cheap, and avoids wild merges).
3. Two jobs match when the title is close, the place is compatible and the
   descriptions share most of their wording.
4. Matches are joined transitively (union-find). The canonical id of a group is
   kept stable: an id already used by the group wins, otherwise the smallest id.
"""
import re
from collections import defaultdict

COMPANY_NOISE = {"ltd", "limited", "plc", "llp", "inc", "llc", "uk", "the", "group",
                 "holdings", "company", "co", "and", "services"}
TITLE_NOISE = {"urgent", "urgently", "required", "wanted", "immediate", "start",
               "hiring", "vacancy", "job", "new", "apply", "now"}
POSTCODE = re.compile(r"\b[a-z]{1,2}\d[a-z\d]?\s*\d?[a-z]{0,2}\b")
COUNTRY_WORDS = {"united", "kingdom", "uk", "england", "scotland", "wales",
                 "northern", "ireland", "gb", "great", "britain"}

TITLE_STRONG, TITLE_WEAK = 0.80, 0.60
DESC_STRONG, DESC_WEAK = 0.50, 0.75


def norm_company(name):
    if not name:
        return ""
    words = re.findall(r"[a-z0-9]+", name.lower())
    kept = [w for w in words if w not in COMPANY_NOISE]
    return " ".join(kept or words)


def title_tokens(title):
    if not title:
        return frozenset()
    t = re.sub(r"[\(\[][^)\]]*[\)\]]", " ", title.lower())   # drop (m/f), [urgent]
    words = re.findall(r"[a-z0-9]+", t)
    return frozenset(w for w in words if w not in TITLE_NOISE)


def location_tokens(location):
    if not location:
        return frozenset()
    t = POSTCODE.sub(" ", location.lower())
    words = re.findall(r"[a-z]+", t)
    return frozenset(w for w in words if w not in COUNTRY_WORDS and len(w) > 1)


def jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def locations_compatible(a, b):
    """Same place, or one is a broader description of the other
    ('Surrey' vs 'Farnham, Surrey'). Unknown on either side is not a match."""
    if not a or not b:
        return False
    return bool(a & b)


def shingles(text, k=5):
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    if len(words) < k:
        return frozenset([" ".join(words)]) if words else frozenset()
    return frozenset(" ".join(words[i:i + k]) for i in range(len(words) - k + 1))


def containment(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def is_duplicate(x, y):
    """x, y: dicts with title_tokens, loc_tokens, shingles."""
    if not locations_compatible(x["loc"], y["loc"]):
        return False
    title_sim = jaccard(x["title"], y["title"])
    if title_sim < TITLE_WEAK:
        return False
    desc_sim = containment(x["shingles"], y["shingles"])
    if title_sim >= TITLE_STRONG:
        return desc_sim >= DESC_STRONG
    return desc_sim >= DESC_WEAK


class _UnionFind:
    def __init__(self):
        self.parent = {}
        self.srcs = {}

    def find(self, x):
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)

    def sources(self, root):
        return self.srcs.setdefault(root, {root.split(":", 1)[0]})

    def union_cross_board(self, a, b):
        """Join only if the two groups have no board in common: the same employer
        posting one role in several places on ONE board is several listings."""
        ra, rb = self.find(a), self.find(b)
        if ra == rb or (self.sources(ra) & self.sources(rb)):
            return
        merged = self.sources(ra) | self.sources(rb)
        self.union(ra, rb)
        self.srcs[self.find(ra)] = merged


def block_key(row):
    company = norm_company(row.get("company"))
    if company:
        return ("c", company)
    # hidden company: only compare jobs with the same title and place
    return ("t", " ".join(sorted(title_tokens(row.get("title")))),
            " ".join(sorted(location_tokens(row.get("location")))))


def cluster(rows, descriptions):
    """rows: list of dicts (id, company, title, location, canonical_job_id).
    descriptions: {id: text}. Returns {id: canonical_job_id} for every row."""
    blocks = defaultdict(list)
    for r in rows:
        blocks[block_key(r)].append(r)

    uf = _UnionFind()
    for r in rows:
        uf.find(r["id"])

    for key, members in blocks.items():
        if len(members) < 2:
            continue
        feats = {}
        for r in members:
            feats[r["id"]] = {
                "title": title_tokens(r.get("title")),
                "loc": location_tokens(r.get("location")),
                "shingles": shingles(descriptions.get(r["id"])),
            }
        # a hidden-company block must also match very closely on text
        strict = key[0] == "t"
        ids = sorted(r["id"] for r in members)   # same order every run
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                x, y = feats[ids[i]], feats[ids[j]]
                if strict and containment(x["shingles"], y["shingles"]) < 0.85:
                    continue
                if is_duplicate(x, y):
                    uf.union_cross_board(ids[i], ids[j])

    groups = defaultdict(list)
    for r in rows:
        groups[uf.find(r["id"])].append(r)

    result = {}
    for members in groups.values():
        ids = {m["id"] for m in members}
        # Keep the canonical id the most members already share (stable);
        # a brand-new row points at itself, so it only counts once.
        votes = {}
        for m in members:
            c = m.get("canonical_job_id")
            if c in ids:
                votes[c] = votes.get(c, 0) + 1
        canonical = (min(votes, key=lambda c: (-votes[c], c)) if votes
                     else min(ids))
        for m in members:
            result[m["id"]] = canonical
    return result


def run(conn):
    """Recompute canonical ids for the whole table and save any that changed.
    Returns the number of rows that are copies of another listing."""
    rows = conn.execute(
        "SELECT id, company, title, location, canonical_job_id FROM jobs").fetchall()

    blocks = defaultdict(list)
    for r in rows:
        blocks[block_key(r)].append(r["id"])
    needed = [i for ids in blocks.values() if len(ids) > 1 for i in ids]
    descriptions = {}
    for start in range(0, len(needed), 500):
        chunk = needed[start:start + 500]
        for d in conn.execute("SELECT id, description FROM jobs WHERE id = ANY(%s)", (chunk,)):
            descriptions[d["id"]] = d["description"]

    mapping = cluster(rows, descriptions)
    current = {r["id"]: r["canonical_job_id"] for r in rows}
    changes = defaultdict(list)
    for job_id, canonical in mapping.items():
        if current.get(job_id) != canonical:
            changes[canonical].append(job_id)
    with conn.transaction():
        for canonical, ids in changes.items():
            conn.execute("UPDATE jobs SET canonical_job_id = %s WHERE id = ANY(%s)",
                         (canonical, ids))
    return sum(1 for job_id, canonical in mapping.items() if job_id != canonical)
