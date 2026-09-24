"""SQLite persistence + FTS5 search with facets.

Canonical jobs are keyed by their content-derived `fingerprint` (16-char hex),
NOT by the auto-increment rowid. Rebuilds UPSERT instead of DELETE+reindex,
so every job keeps a permanent identity forever — bookmarked ?job=<fp> links,
saved items and tracker entries survive every 45-minute re-crawl, even full
database rebuilds.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
import time
from datetime import timedelta

from . import config, dedupe, util

log = logging.getLogger("roleradar.db")

_lock = threading.Lock()

SCHEMA_VERSION = 2

JOB_COLUMNS = [
    "title", "company", "location", "remote_mode", "geo", "job_type", "level",
    "salary_min", "salary_max", "salary_currency", "salary_period",
    "salary_text", "usd_min", "usd_max", "tags", "posted_at", "deadline",
    "apply_url", "url", "description_text", "description_html", "sources",
    "num_sources", "fingerprint", "status", "expired_reason", "extra",
    "first_seen", "last_seen", "updated_at",
]

# columns written on INSERT and UPDATE (fingerprint goes in the WHERE key)
_UPSERT_COLUMNS = [c for c in JOB_COLUMNS if c != "fingerprint"]

POSTED_WINDOWS = {
    "24h": timedelta(hours=24),
    "3d": timedelta(days=3),
    "7d": timedelta(days=7),
    "14d": timedelta(days=14),
    "30d": timedelta(days=30),
}


def get_conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(config.DB_PATH), exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _schema_version(c: sqlite3.Connection) -> int:
    try:
        r = c.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        return int(r["value"]) if r else 0
    except sqlite3.OperationalError:
        return 0


def init_db():
    with _lock, get_conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY, value TEXT
        );
        CREATE TABLE IF NOT EXISTS listings (
            source TEXT NOT NULL,
            source_id TEXT NOT NULL,
            payload TEXT,
            first_seen TEXT,
            last_seen TEXT,
            PRIMARY KEY (source, source_id)
        );
        CREATE TABLE IF NOT EXISTS fetch_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT, platform TEXT,
            started_at TEXT, finished_at TEXT,
            fetched INTEGER DEFAULT 0, status TEXT, error TEXT
        );
        """)

        if _schema_version(c) < SCHEMA_VERSION:
            # job-row schema changed (geo column, fingerprint UNIQUE) — the cache
            # is disposable; drop and let the next sync repopulate it.
            log.info("schema migration: rebuilding jobs tables")
            c.executescript("DROP TABLE IF EXISTS jobs; DROP TABLE IF EXISTS jobs_fts;")

        c.executescript("""
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY,
            title TEXT, company TEXT, location TEXT,
            remote_mode TEXT, geo TEXT,
            job_type TEXT, level TEXT,
            salary_min REAL, salary_max REAL, salary_currency TEXT,
            salary_period TEXT, salary_text TEXT,
            usd_min REAL, usd_max REAL,
            tags TEXT, posted_at TEXT, deadline TEXT,
            apply_url TEXT, url TEXT,
            description_text TEXT, description_html TEXT,
            sources TEXT, num_sources INTEGER DEFAULT 1,
            fingerprint TEXT UNIQUE,
            status TEXT DEFAULT 'active', expired_reason TEXT,
            extra TEXT,
            first_seen TEXT, last_seen TEXT, updated_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_jobs_posted ON jobs(posted_at DESC);
        CREATE INDEX IF NOT EXISTS idx_jobs_type ON jobs(job_type);
        CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
        CREATE INDEX IF NOT EXISTS idx_jobs_usd ON jobs(usd_max);
        CREATE INDEX IF NOT EXISTS idx_jobs_geo ON jobs(geo);
        CREATE INDEX IF NOT EXISTS idx_jobs_fp ON jobs(fingerprint);
        CREATE VIRTUAL TABLE IF NOT EXISTS jobs_fts USING fts5(
            title, company, location, tags, description,
            tokenize='porter unicode61');
        """)
        c.execute("INSERT INTO meta (key, value) VALUES ('schema_version', ?)"
                  " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                  (str(SCHEMA_VERSION),))
        c.commit()


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------

def upsert_listings(c: sqlite3.Connection, source: str, items: list[dict], now_iso: str):
    for it in items:
        sid = str(it.get("source_id") or "")
        if not sid:
            continue
        c.execute(
            """INSERT INTO listings (source, source_id, payload, first_seen, last_seen)
               VALUES (?,?,?,?,?)
               ON CONFLICT(source, source_id)
               DO UPDATE SET payload=excluded.payload, last_seen=excluded.last_seen""",
            (source, sid, json.dumps(it, default=str)[:600_000], now_iso, now_iso))


def record_run(c: sqlite3.Connection, source_name: str, platform: str,
               started_iso: str, count: int, status: str, error: str | None):
    c.execute(
        """INSERT INTO fetch_runs (source, platform, started_at, finished_at, fetched, status, error)
           VALUES (?,?,?,?,?,?,?)""",
        (source_name, platform, started_iso, util.iso(util.utcnow()), count, status, error))


def _serialize(job: dict, cols: list[str]) -> list:
    row = []
    for col in cols:
        v = job.get(col)
        if col in ("tags", "sources", "extra"):
            v = json.dumps(v if v is not None else ([] if col != "extra" else {}),
                           ensure_ascii=False)
        row.append(v)
    return row


def _fts_values(job: dict) -> tuple:
    return (job.get("title") or "", job.get("company") or "",
            job.get("location") or "", " ".join(job.get("tags") or []),
            (job.get("description_text") or "")[:20000])


def _expiry_for(posted_at: str | None, deadline: str | None, now_iso: str,
                stale_before: str) -> tuple[str, str | None]:
    if deadline and deadline < now_iso:
        return "expired", "Application deadline passed"
    if posted_at and posted_at < stale_before:
        return "expired", f"Posted more than {config.STALE_AFTER_DAYS} days ago"
    return "active", None


def rebuild_canonical(normalized_jobs: list[dict], successful_sources: set[str] | None,
                      run_started_at, run_id: int):
    """Deduplicate and UPSERT into the canonical jobs table.

    IDs (rowids) are stable: a job's fingerprint maps to the same row across
    every rebuild, so permalink URLs never break.
    """
    now = util.utcnow()
    now_iso = util.iso(now)
    stale_before = util.iso(now - timedelta(days=config.STALE_AFTER_DAYS))
    hard_delete_before = util.iso(now - timedelta(days=30))

    jobs = dedupe.dedupe(normalized_jobs)
    for j in jobs:
        j["fingerprint"] = j.get("fingerprint") or dedupe.fingerprint(
            j["company"], j["title"], j.get("location") or "")

    inserted = updated = expired_now = deleted = 0

    with _lock, get_conn() as c:
        existing: dict[str, sqlite3.Row] = {}
        for r in c.execute(
                "SELECT id, fingerprint, first_seen, last_seen, status, posted_at,"
                " deadline, sources FROM jobs"):
            if r["fingerprint"]:
                existing[r["fingerprint"]] = r

        delisted_pairs: set[tuple[str, str]] = set()
        if successful_sources:
            run_start_iso = util.iso(run_started_at)
            q_marks = ",".join("?" for _ in successful_sources)
            rows = c.execute(
                f"""SELECT source, source_id FROM listings
                    WHERE last_seen < ? AND source IN ({q_marks})""",
                [run_start_iso, *successful_sources]).fetchall()
            delisted_pairs = {(r["source"], r["source_id"]) for r in rows}
            c.execute(
                f"""DELETE FROM listings WHERE last_seen < ?
                    AND first_seen < ? AND source IN ({q_marks})""",
                [hard_delete_before, hard_delete_before, *successful_sources])

        seen_fps: set[str] = set()
        set_clause = ",".join(f"{col}=?" for col in _UPSERT_COLUMNS)
        ins_cols = ",".join(_UPSERT_COLUMNS + ["fingerprint"])
        ins_ph = ",".join("?" for _ in (_UPSERT_COLUMNS + ["fingerprint"]))

        for j in jobs:
            fp = j["fingerprint"]
            seen_fps.add(fp)
            status, reason = _expiry_for(j.get("posted_at"), j.get("deadline"),
                                         now_iso, stale_before)
            j["status"], j["expired_reason"] = status, reason
            j["last_seen"] = now_iso
            j["updated_at"] = now_iso

            old = existing.get(fp)
            if old is not None:
                j["first_seen"] = old["first_seen"] or now_iso
                jid = old["id"]
                c.execute(f"UPDATE jobs SET {set_clause} WHERE id=?",
                          _serialize(j, _UPSERT_COLUMNS) + [jid])
                c.execute("DELETE FROM jobs_fts WHERE rowid=?", (jid,))
                c.execute("INSERT INTO jobs_fts (rowid, title, company, location,"
                          " tags, description) VALUES (?,?,?,?,?,?)",
                          (jid,) + _fts_values(j))
                updated += 1
            else:
                j["first_seen"] = now_iso
                cur = c.execute(
                    f"INSERT INTO jobs ({ins_cols}) VALUES ({ins_ph})",
                    _serialize(j, _UPSERT_COLUMNS) + [fp])
                jid = cur.lastrowid
                c.execute("INSERT INTO jobs_fts (rowid, title, company, location,"
                          " tags, description) VALUES (?,?,?,?,?,?)",
                          (jid,) + _fts_values(j))
                inserted += 1
            if status != "active":
                expired_now += 1

        # jobs absent from this rebuild: expire them by the stale/delisted
        # rules, and hard-delete anything long gone.
        for fp, old in existing.items():
            if fp in seen_fps:
                continue
            status, reason = "active", None
            try:
                src_pairs = {(s.get("source"), str(s.get("source_id") or ""))
                             for s in json.loads(old["sources"] or "[]")}
            except json.JSONDecodeError:
                src_pairs = set()
            if old["status"] == "expired":
                status, reason = "expired", None
            else:
                status, reason = _expiry_for(old["posted_at"], old["deadline"],
                                             now_iso, stale_before)
                if status == "active" and successful_sources and src_pairs and \
                        all(p in delisted_pairs for p in src_pairs):
                    status, reason = "expired", "Removed from source"
            if status == "expired":
                if (old["last_seen"] or "") < hard_delete_before:
                    c.execute("DELETE FROM jobs WHERE id=?", (old["id"],))
                    c.execute("DELETE FROM jobs_fts WHERE rowid=?", (old["id"],))
                    deleted += 1
                elif old["status"] != "expired":
                    c.execute("UPDATE jobs SET status='expired', expired_reason=?,"
                              " updated_at=? WHERE id=?",
                              (reason or "Likely outdated", now_iso, old["id"]))
                    expired_now += 1
            # active stale records simply wait for their source's next run

        # FTS integrity self-heal (e.g. after a crash mid-rebuild)
        n_jobs = c.execute("SELECT COUNT(*) n FROM jobs").fetchone()["n"]
        n_fts = c.execute("SELECT COUNT(*) n FROM jobs_fts").fetchone()["n"]
        if n_jobs != n_fts:
            log.warning("FTS drift (%d jobs vs %d fts) — full reindex", n_jobs, n_fts)
            c.execute("DELETE FROM jobs_fts")
            for r in c.execute("SELECT id, title, company, location, tags,"
                               " description_text FROM jobs"):
                try:
                    tags = " ".join(json.loads(r["tags"] or "[]"))
                except json.JSONDecodeError:
                    tags = ""
                c.execute("INSERT INTO jobs_fts (rowid, title, company, location,"
                          " tags, description) VALUES (?,?,?,?,?,?)",
                          (r["id"], r["title"] or "", r["company"] or "",
                           r["location"] or "", tags,
                           (r["description_text"] or "")[:20000]))
        c.commit()

    total = inserted + updated
    log.info("rebuild: %d canonical (%d new, %d refreshed, %d expired, %d purged)",
             total, inserted, updated, expired_now, deleted)
    return {"total": total, "inserted": inserted, "updated": updated,
            "expired": expired_now, "purged": deleted}


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------

def _row_to_job(r: sqlite3.Row, full: bool = False) -> dict:
    d = dict(r)
    for jf in ("tags", "sources"):
        try:
            d[jf] = json.loads(d.get(jf) or "[]")
        except json.JSONDecodeError:
            d[jf] = []
    try:
        d["extra"] = json.loads(d.get("extra") or "{}")
    except json.JSONDecodeError:
        d["extra"] = {}
    if not full:
        txt = (d.get("description_text") or "").strip()
        d["excerpt"] = txt[:300] + ("…" if len(txt) > 300 else "")
        for heavy in ("description_text", "description_html", "extra"):
            d.pop(heavy, None)
    return d


def _build_fts_query(q: str) -> str | None:
    q = q.strip()
    if not q:
        return None
    neg = re.findall(r"(?:^|\s)-([A-Za-z0-9][A-Za-z0-9+#.\-]{1,})", q)
    q = re.sub(r"(?:^|\s)-[A-Za-z0-9][A-Za-z0-9+#.\-]{1,}", " ", q)
    phrase = re.findall(r'"([^"]+)"', q)
    q_wo_phrases = re.sub(r'"[^"]+"', " ", q)
    tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9+#.\-]{1,}", q_wo_phrases)
    parts = [f'"{p}"' for p in phrase]
    parts += [f"{t.replace(chr(34), '')}*" for t in tokens]
    expr = " AND ".join(parts) if parts else None
    if neg:
        neg_expr = " NOT ".join(f"{t.replace(chr(34), '')}*" for t in neg)
        expr = f"({expr}) NOT {neg_expr}" if expr else f"NOT {neg_expr}"
    return expr


def _filters(params, *, skip: str | None = None):
    where, args = [], []
    if skip != "status" and not params.get("include_expired"):
        where.append("jobs.status = 'active'")
    if skip != "type" and params.get("types"):
        marks = ",".join("?" for _ in params["types"])
        where.append(f"jobs.job_type IN ({marks})")
        args += params["types"]
    if skip != "remote" and params.get("remote"):
        marks = ",".join("?" for _ in params["remote"])
        where.append(f"jobs.remote_mode IN ({marks})")
        args += params["remote"]
    if skip != "geo" and params.get("geos"):
        marks = ",".join("?" for _ in params["geos"])
        where.append(f"jobs.geo IN ({marks})")
        args += params["geos"]
    if skip != "level" and params.get("levels"):
        marks = ",".join("?" for _ in params["levels"])
        where.append(f"jobs.level IN ({marks})")
        args += params["levels"]
    if skip != "sources" and params.get("sources"):
        marks = ",".join("?" for _ in params["sources"])
        where.append(
            f"EXISTS (SELECT 1 FROM json_each(jobs.sources) s"
            f" WHERE json_extract(s.value,'$.source') IN ({marks}))")
        args += params["sources"]
    if params.get("posted") and params["posted"] in POSTED_WINDOWS:
        cutoff = util.iso(util.utcnow() - POSTED_WINDOWS[params["posted"]])
        where.append("jobs.posted_at >= ?")
        args.append(cutoff)
    if params.get("min_salary"):
        where.append("jobs.usd_max IS NOT NULL AND jobs.usd_max >= ?")
        args.append(float(params["min_salary"]))
    if params.get("has_salary"):
        where.append("(jobs.usd_max IS NOT NULL OR jobs.salary_text IS NOT NULL)")
    if params.get("has_deadline"):
        where.append("jobs.deadline IS NOT NULL")
    if params.get("tag"):
        where.append("EXISTS (SELECT 1 FROM json_each(jobs.tags) t WHERE t.value = ?)")
        args.append(params["tag"])
    if params.get("company"):
        where.append("LOWER(jobs.company) = LOWER(?)")
        args.append(params["company"])
    return where, args


def search(params: dict) -> dict:
    t0 = time.perf_counter()
    q = (params.get("q") or "").strip()
    fts_expr = _build_fts_query(q)
    sort = params.get("sort") or "newest"
    per_page = max(1, min(int(params.get("per_page") or 20), 50))
    page = max(1, int(params.get("page") or 1))
    offset = (page - 1) * per_page

    if sort == "salary":
        order = "(jobs.usd_max IS NULL), jobs.usd_max DESC, jobs.posted_at DESC"
    elif sort == "deadline":
        order = "(jobs.deadline IS NULL), jobs.deadline ASC"
    else:
        order = "(jobs.posted_at IS NULL), jobs.posted_at DESC"

    with get_conn() as c:
        fts_where = ""
        rank_args: list = []
        rank_join = ""
        try:
            if fts_expr:
                ids = [r[0] for r in c.execute(
                    "SELECT rowid FROM jobs_fts WHERE jobs_fts MATCH ?", (fts_expr,))]
                c.execute("CREATE TEMP TABLE IF NOT EXISTS _rr_ids (id INTEGER PRIMARY KEY)")
                c.execute("DELETE FROM _rr_ids")
                if ids:
                    c.executemany("INSERT OR IGNORE INTO _rr_ids (id) VALUES (?)",
                                  [(i,) for i in ids])
                fts_where = "jobs.id IN (SELECT id FROM _rr_ids)"
                if sort == "relevance":
                    rank_join = ("JOIN (SELECT rowid, bm25(jobs_fts, 12.0, 7.0, 2.0, 4.0, 1.0)"
                                 " rank FROM jobs_fts WHERE jobs_fts MATCH ?)"
                                 " f ON f.rowid = jobs.id")
                    rank_args = [fts_expr]
                    order = "f.rank, " + order

            def run_query(skip=None):
                where, args = _filters(params, skip=skip)
                if fts_where:
                    where = [fts_where] + where
                sql_where = ("WHERE " + " AND ".join(where)) if where else ""
                return sql_where, args

            sql_where, args = run_query()
            total = c.execute(
                f"SELECT COUNT(*) n FROM jobs {sql_where}", args).fetchone()["n"]

            rows = c.execute(
                f"SELECT jobs.* FROM jobs {rank_join} {sql_where} ORDER BY {order}"
                f" LIMIT ? OFFSET ?",
                rank_args + args + [per_page, offset]).fetchall()

            facets = {}
            for dim, col in (("type", "jobs.job_type"),
                             ("remote", "jobs.remote_mode"),
                             ("geo", "jobs.geo"),
                             ("level", "jobs.level")):
                w, a = run_query(skip=dim)
                facets[dim] = {r["k"]: r["c"] for r in c.execute(
                    f"SELECT {col} k, COUNT(*) c FROM jobs {w}"
                    f" GROUP BY {col} ORDER BY c DESC", a)}
            w, a = run_query(skip="sources")
            facets["sources"] = {r["k"]: r["c"] for r in c.execute(
                f"SELECT json_extract(s.value,'$.source') k, COUNT(*) c"
                f" FROM jobs JOIN json_each(jobs.sources) s {w}"
                f" GROUP BY 1 ORDER BY c DESC LIMIT 60", a)}
            w, a = run_query()
            facets["tags"] = [r["k"] for r in c.execute(
                f"SELECT t.value k, COUNT(*) c FROM jobs"
                f" JOIN json_each(jobs.tags) t {w} GROUP BY t.value"
                f" ORDER BY c DESC LIMIT 18", a)]
            extra_and = " AND " if w else " WHERE "
            facets["with_salary"] = c.execute(
                f"SELECT COUNT(*) n FROM jobs {w}"
                f"{extra_and}(jobs.usd_max IS NOT NULL OR jobs.salary_text IS NOT NULL)",
                a).fetchone()["n"]
            facets["new_24h"] = c.execute(
                f"SELECT COUNT(*) n FROM jobs {w}"
                f"{extra_and}jobs.posted_at >= ?",
                a + [util.iso(util.utcnow() - timedelta(hours=24))]).fetchone()["n"]
        except sqlite3.OperationalError as e:
            log.warning("FTS query failed (%s); falling back to LIKE", e)
            like_terms = re.findall(r"[A-Za-z0-9+#.]{2,}", q)[:5]
            if not like_terms:
                raise
            like_where = " AND ".join(
                "(LOWER(jobs.title) LIKE ? OR LOWER(jobs.company) LIKE ?"
                " OR LOWER(jobs.description_text) LIKE ?)" for _ in like_terms)
            params2 = dict(params, q="")
            base_where, base_args = _filters(params2)
            final_where = "WHERE " + " AND ".join(base_where + [like_where]) if base_where else "WHERE " + like_where
            like_args = base_args + [f"%{t.lower()}%" for t in like_terms for _ in range(3)]
            total = c.execute(f"SELECT COUNT(*) n FROM jobs {final_where}", like_args).fetchone()["n"]
            rows = c.execute(
                f"SELECT jobs.* FROM jobs {final_where} ORDER BY {order.replace('f.rank, ', '')}"
                f" LIMIT ? OFFSET ?",
                like_args + [per_page, offset]).fetchall()
            facets = {}
        finally:
            try:
                c.execute("DROP TABLE IF EXISTS _rr_ids")
            except sqlite3.Error:
                pass

    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "took_ms": round((time.perf_counter() - t0) * 1000, 1),
        "facets": facets,
        "results": [_row_to_job(r) for r in rows],
    }


def get_job(identifier) -> dict | None:
    """Fetch by numeric row id OR by the permanent content fingerprint."""
    s = str(identifier).strip()
    with get_conn() as c:
        if s.isdigit():
            r = c.execute("SELECT * FROM jobs WHERE id = ?", (int(s),)).fetchone()
        else:
            r = c.execute("SELECT * FROM jobs WHERE fingerprint = ?",
                          (s[:16],)).fetchone()
        return _row_to_job(r, full=True) if r else None


def stats() -> dict:
    with get_conn() as c:
        def one(q, a=()):
            return c.execute(q, a).fetchone()["n"]

        out = {
            "total_active": one("SELECT COUNT(*) n FROM jobs WHERE status='active'"),
            "total_expired": one("SELECT COUNT(*) n FROM jobs WHERE status='expired'"),
            "new_24h": one("SELECT COUNT(*) n FROM jobs WHERE status='active' AND posted_at >= ?",
                           [util.iso(util.utcnow() - timedelta(hours=24))]),
            "new_7d": one("SELECT COUNT(*) n FROM jobs WHERE status='active' AND posted_at >= ?",
                          [util.iso(util.utcnow() - timedelta(days=7))]),
            "remote": one("SELECT COUNT(*) n FROM jobs WHERE status='active' AND remote_mode='remote'"),
            "internships": one("SELECT COUNT(*) n FROM jobs WHERE status='active' AND job_type IN ('internship','apprenticeship')"),
            "with_salary": one("SELECT COUNT(*) n FROM jobs WHERE status='active' AND (usd_max IS NOT NULL OR salary_text IS NOT NULL)"),
            "with_deadline": one("SELECT COUNT(*) n FROM jobs WHERE status='active' AND deadline IS NOT NULL"),
            "multi_source": one("SELECT COUNT(*) n FROM jobs WHERE status='active' AND num_sources > 1"),
            "companies": one("SELECT COUNT(DISTINCT LOWER(company)) n FROM jobs WHERE status='active'"),
        }
        out["by_type"] = {r["k"]: r["c"] for r in c.execute(
            "SELECT job_type k, COUNT(*) c FROM jobs WHERE status='active' GROUP BY 1")}
        out["by_geo"] = {r["k"]: r["c"] for r in c.execute(
            "SELECT geo k, COUNT(*) c FROM jobs WHERE status='active' GROUP BY 1"
            " ORDER BY c DESC")}
        out["by_platform"] = {r["k"]: r["c"] for r in c.execute(
            """SELECT json_extract(s.value,'$.source') k, COUNT(*) c
               FROM jobs JOIN json_each(jobs.sources) s
               WHERE jobs.status='active' GROUP BY 1 ORDER BY c DESC""")}

        runs = {}
        for r in c.execute(
                """SELECT f.* FROM fetch_runs f
                   JOIN (SELECT source s2, MAX(id) mid FROM fetch_runs GROUP BY s2) s
                   ON f.id = s.mid ORDER BY f.source"""):
            runs[r["source"]] = dict(r)
        out["source_runs"] = runs

        out["top_tags"] = [r["k"] for r in c.execute(
            """SELECT t.value k, COUNT(*) c FROM jobs
               JOIN json_each(jobs.tags) t WHERE jobs.status='active'
               GROUP BY t.value ORDER BY c DESC LIMIT 16""")]

        meta = {r["key"]: r["value"] for r in c.execute("SELECT key, value FROM meta")}
        out["meta"] = meta
        out["last_sync"] = meta.get("last_sync_finished")
        return out


def set_meta(key: str, value: str):
    with _lock, get_conn() as c:
        c.execute("INSERT INTO meta (key, value) VALUES (?,?) "
                  "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        c.commit()


def has_jobs() -> bool:
    with get_conn() as c:
        return c.execute("SELECT COUNT(*) n FROM jobs").fetchone()["n"] > 0
