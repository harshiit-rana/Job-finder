"""Raw-listing schema + normalization into job records.

Every source adapter returns a list of *raw listing* dicts:

    source, source_id, url, apply_url, title, company, location,
    description_html, description_text, tags, source_type,
    posted_at, salary (optional pre-parsed dict), remote_flag, extra

`normalize_listing` converts them into fully-structured job records.
"""
from __future__ import annotations

import logging

from . import classify, salary as salary_mod, util

log = logging.getLogger("roleradar.models")

REQUIRED = ("source", "source_id", "title", "company")


def normalize_listing(raw: dict, *, now=None):
    """Turn a raw listing into a normalized job dict (single-source)."""
    now = now or util.utcnow()
    title = util.norm_space(raw.get("title"))
    company = util.norm_space(raw.get("company"))
    if not title or not company:
        return None

    desc_html = util.clean_html(raw.get("description_html"))
    desc_text = raw.get("description_text") or util.html_to_text(desc_html)
    desc_text = desc_text[:100_000]

    tags = []
    seen = set()
    for t in raw.get("tags") or []:
        t2 = util.norm_space(str(t)).lower()
        if t2 and len(t2) <= 40 and t2 not in seen:
            seen.add(t2)
            tags.append(t2)
    for sk in classify.extract_skills(title, desc_text):
        if sk not in seen and len(tags) < 12:
            seen.add(sk)
            tags.append(sk)

    posted = util.parse_date(raw.get("posted_at"), now=now)
    posted_iso = util.iso(posted)

    sal = raw.get("salary")
    if not (sal and (sal.get("salary_min") or sal.get("salary_max"))):
        try:
            sal = salary_mod.extract_salary(
                f"{title}\n{raw.get('salary_hint') or ''}\n{desc_text[:8000]}") or {}
        except Exception as e:
            log.debug("salary extraction failed (%s): %s", raw.get("source"), e)
            sal = {}

    deadline = raw.get("deadline")
    deadline_dt = util.parse_date(deadline, now=now) if deadline else None
    if not deadline_dt:
        deadline_dt = util.extract_deadline(desc_text, now=now)
    if deadline_dt and posted and deadline_dt < posted:
        deadline_dt = None

    location = util.norm_space(raw.get("location"))
    job_type = classify.infer_job_type(title, tags, desc_text, raw.get("source_type"))
    level = classify.infer_level(title, job_type)
    remote_mode = classify.infer_remote_mode(
        title, location, tags, desc_text, raw.get("remote_flag"))

    job = {
        "title": title,
        "company": company,
        "location": location,
        "remote_mode": remote_mode,
        "job_type": job_type,
        "level": level,
        "salary_min": sal.get("salary_min"),
        "salary_max": sal.get("salary_max"),
        "salary_currency": sal.get("salary_currency"),
        "salary_period": sal.get("salary_period"),
        "salary_text": sal.get("salary_text"),
        "usd_min": sal.get("usd_min"),
        "usd_max": sal.get("usd_max"),
        "tags": tags,
        "posted_at": posted_iso,
        "deadline": util.iso(deadline_dt),
        "apply_url": raw.get("apply_url") or raw.get("url"),
        "url": raw.get("url"),
        "description_text": desc_text,
        "description_html": desc_html,
        "extra": raw.get("extra") or {},
        "sources": [{
            "source": raw["source"],
            "source_id": str(raw.get("source_id") or ""),
            "url": raw.get("url"),
            "apply_url": raw.get("apply_url") or raw.get("url"),
            "posted_at": posted_iso,
        }],
    }
    return job
