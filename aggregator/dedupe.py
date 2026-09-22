"""Cross-source deduplication and canonical record merging."""
from __future__ import annotations

import hashlib
import re
import urllib.parse

from . import util

_COMPANY_SUFFIXES = re.compile(
    r"\b(inc|incorporated|llc|llp|ltd|limited|corp|corporation|co|company|"
    r"gmbh|ag|ug|sarl|sas|spa|bv|b v|oy|ab|as|pte|pty|plc|pvt|private|holdings|"
    r"technologies inc|group)\.?\b", re.I)

_TITLE_NOISE = re.compile(
    r"\((remote|all genders|m/f/d|m/w/d|f/m/d|w/m/d|anywhere|wfh)[^)]*\)|"
    r"\[(remote|hiring)\]|"
    r"\b(remote|work from home|wfh)\b(?:\s*-?\s*(anywhere|worldwide|global))?|"
    r"[-–—|]\s*remote$",
    re.I)


def norm_company(c: str) -> str:
    s = util.slug_text(c)
    s = _COMPANY_SUFFIXES.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def norm_title(t: str) -> str:
    s = util.slug_text(t)
    s = _TITLE_NOISE.sub(" ", s)
    # drop trailing location fragments like "- berlin" / "- san francisco ca"
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip()


_fuzz_stop = {"engineer", "developer", "the", "a", "of", "and", "for", "in",
              "manager", "specialist", "analyst", "job", "hiring", "urgent"}


def _title_tokens(t: str) -> frozenset[str]:
    toks = [w for w in norm_title(t).split() if w not in _fuzz_stop and len(w) > 2]
    return frozenset(toks or norm_title(t).split())


def _jaccard(a: frozenset, b: frozenset) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / (len(a) + len(b) - inter) if (len(a) + len(b) - inter) else 0.0


def norm_url(u: str | None) -> str:
    if not u:
        return ""
    try:
        p = urllib.parse.urlsplit(u.strip())
        host = p.netloc.lower().removeprefix("www.")
        path = re.sub(r"/+$", "", p.path)
        query = urllib.parse.parse_qsl(p.query)
        drop = lambda k: (k.lower().startswith("utm_")
                          or k.lower() in ("ref", "tracking", "gh_src", "fbclid",
                                           "gclid", "mc_cid", "mc_eid", "ssg_id"))
        query = [(k, v) for k, v in query if not drop(k)]
        q = urllib.parse.urlencode(query)
        return urllib.parse.urlunsplit(("", host, path, q, "")).lower()
    except ValueError:
        return u.strip().lower()


_LOC_IGNORE = re.compile(r"\b(remote|worldwide|anywhere|global|multiple|various|"
                         r"work from home|distributed|flexible|emea|amer|apac|"
                         r"the world|usa|united states|us-based)\b", re.I)


def fingerprint(company: str, title: str, location: str = "") -> str:
    loc = util.slug_text(location)
    loc_key = ""
    if loc and not _LOC_IGNORE.search(loc) and "remote" not in loc:
        # keep only the city-ish head so "Berlin, Germany" ~ "Berlin"
        loc_key = loc.split(",")[0][:30].strip()
    key = f"{norm_company(company)}|{norm_title(title)}|{loc_key}"
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def _richness(j: dict) -> float:
    return (len(j.get("description_text") or "")
            + 1200 * bool(j.get("usd_max") or j.get("salary_text"))
            + 400 * bool(j.get("deadline"))
            + 40 * len(j.get("tags") or [])
            + 250 * bool(j.get("apply_url")))


def _merge_two(a: dict, b: dict) -> dict:
    """Merge b into the richer of a/b and return the merged record."""
    primary, secondary = (a, b) if _richness(a) >= _richness(b) else (b, a)
    merged = dict(primary)

    # salary: prefer whichever record actually has one
    if not (primary.get("usd_max") or primary.get("salary_text")):
        for k in ("salary_min", "salary_max", "salary_currency", "salary_period",
                  "salary_text", "usd_min", "usd_max"):
            merged[k] = secondary.get(k)

    # earliest posted date, soonest upcoming deadline
    dates = [d for d in (a.get("posted_at"), b.get("posted_at")) if d]
    merged["posted_at"] = min(dates) if dates else None
    dls = [d for d in (a.get("deadline"), b.get("deadline")) if d]
    merged["deadline"] = min(dls) if dls else None

    if len(secondary.get("description_text") or "") > len(primary.get("description_text") or ""):
        merged["description_text"] = secondary["description_text"]
        merged["description_html"] = secondary.get("description_html") or primary.get("description_html")

    loc = primary.get("location") or secondary.get("location")
    merged["location"] = loc
    if (not loc or "remote" in loc.lower()) and secondary.get("location") and "remote" not in secondary["location"].lower():
        merged["location"] = secondary["location"]

    tags, seen = [], set()
    for t in (primary.get("tags") or []) + (secondary.get("tags") or []):
        if t not in seen and len(tags) < 15:
            seen.add(t)
            tags.append(t)
    merged["tags"] = tags

    apply_url = primary.get("apply_url") or secondary.get("apply_url")
    url = primary.get("url") or secondary.get("url")
    merged["apply_url"], merged["url"] = apply_url, url
    # for level/type prefer more specific non-default value
    if primary.get("level") == "mid" and secondary.get("level") != "mid":
        merged["level"] = secondary["level"]
    if primary.get("remote_mode") in ("unknown",) :
        merged["remote_mode"] = secondary.get("remote_mode")

    extra = dict(secondary.get("extra") or {})
    extra.update(primary.get("extra") or {})
    merged["extra"] = extra

    srcs, seen_src = [], set()
    for s in (primary.get("sources") or []) + (secondary.get("sources") or []):
        key = (s.get("source"), s.get("url"))
        if key not in seen_src:
            seen_src.add(key)
            srcs.append(s)
    merged["sources"] = srcs
    return merged


def dedupe(jobs: list[dict]) -> list[dict]:
    """Group near-identical jobs (across sources) into canonical records."""
    by_fp: dict[str, dict] = {}
    by_url: dict[str, str] = {}

    for job in jobs:
        fp = fingerprint(job["company"], job["title"], job.get("location") or "")
        url_keys = [norm_url(job.get("apply_url")), norm_url(job.get("url"))]
        existing_fp = None
        for uk in url_keys:
            if uk and uk in by_url and by_url[uk] in by_fp:
                existing_fp = by_url[uk]
                break
        if existing_fp is None and fp in by_fp:
            existing_fp = fp
        if existing_fp is not None:
            by_fp[existing_fp] = _merge_two(by_fp[existing_fp], job)
            fp = existing_fp   # keep one canonical key; avoids duplicate merged records
        else:
            by_fp[fp] = job
        for uk in url_keys:
            if uk:
                by_url[uk] = fp

    # fuzzy pass: same normalized company + high title similarity
    fps = list(by_fp.keys())
    comp_groups: dict[str, list[str]] = {}
    for fp in fps:
        comp_groups.setdefault(norm_company(by_fp[fp]["company"]), []).append(fp)

    merge_into: dict[str, str] = {}
    for comp, group in comp_groups.items():
        if not comp or len(group) < 2 or len(group) > 60:
            continue
        toks = {fp: _title_tokens(by_fp[fp]["title"]) for fp in group}
        for i in range(len(group)):
            if group[i] in merge_into:
                continue
            for j in range(i + 1, len(group)):
                if group[j] in merge_into:
                    continue
                sim = _jaccard(toks[group[i]], toks[group[j]])
                if sim >= 0.82:
                    a, b = by_fp[group[i]], by_fp[group[j]]
                    same_place = (a.get("remote_mode") == "remote" and b.get("remote_mode") == "remote"
                                  or util.slug_text(a.get("location")) == util.slug_text(b.get("location")))
                    if same_place:
                        by_fp[group[i]] = _merge_two(a, b)
                        merge_into[group[j]] = group[i]

    for dead in merge_into:
        by_fp.pop(dead, None)

    out = list(by_fp.values())
    for j in out:
        j["fingerprint"] = fingerprint(j["company"], j["title"], j.get("location") or "")
        j["num_sources"] = len(j.get("sources") or [])
    return out
