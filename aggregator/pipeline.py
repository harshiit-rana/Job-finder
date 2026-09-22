"""Refresh orchestration: fetch -> normalize -> dedupe -> persist."""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import config, db, models, sources, util

log = logging.getLogger("roleradar.pipeline")

_refresh_lock = threading.Lock()
_state = {
    "sync_running": False,
    "started_at": None,
    "progress": {},
    "last_result": None,
}
_scheduler_started = False


def sync_state() -> dict:
    return dict(_state)


def _fetch_source(src: sources.Source):
    started = util.utcnow()
    started_iso = util.iso(started)
    _state["progress"][src.name] = "running"
    try:
        items = src.fetch() or []
    except Exception as e:
        log.warning("source %s failed: %s", src.name, e)
        with db.get_conn() as c:
            db.record_run(c, src.name, src.platform, started_iso, 0, "error", str(e)[:500])
            c.commit()
        _state["progress"][src.name] = "error"
        return src, [], "error"

    jobs = []
    for raw in items:
        try:
            job = models.normalize_listing(raw)
            if job:
                jobs.append(job)
        except Exception as e:
            log.debug("normalize failed for %s: %s", src.name, e)

    with db.get_conn() as c:
        db.upsert_listings(c, src.name, items, util.iso(util.utcnow()))
        db.record_run(c, src.name, src.platform, started_iso, len(items), "ok", None)
        c.commit()
    _state["progress"][src.name] = f"ok:{len(items)}"
    return src, jobs, "ok"


def run_refresh(trigger: str = "manual") -> dict:
    if not _refresh_lock.acquire(blocking=False):
        return {"already_running": True, "progress": dict(_state["progress"])}
    try:
        db.init_db()
        _state.update({"sync_running": True, "started_at": util.iso(util.utcnow()),
                       "progress": {}})
        db.set_meta("last_sync_started", _state["started_at"])
        started = util.utcnow()

        srcs = sources.enabled_sources()
        all_jobs: list[dict] = []
        successful: set[str] = set()
        failures = {}
        counts = {}

        with ThreadPoolExecutor(max_workers=config.FETCH_WORKERS) as pool:
            futs = {pool.submit(_fetch_source, s): s for s in srcs}
            for fut in as_completed(futs):
                src, jobs, status = fut.result()
                counts[src.name] = len(jobs)
                if status == "ok":
                    successful.add(src.name)
                    all_jobs.extend(jobs)
                else:
                    failures[src.name] = "error"

        result = db.rebuild_canonical(all_jobs, successful, started,
                                      run_id=started.timestamp())
        db.set_meta("last_sync_finished", util.iso(util.utcnow()))
        db.set_meta("last_trigger", trigger)
        _state["last_result"] = {
            "finished_at": util.iso(util.utcnow()),
            "counts": counts,
            "failures": failures,
            **result,
        }
        _state["sync_running"] = False
        log.info("refresh complete: %s", result)
        return _state["last_result"]
    finally:
        _state["sync_running"] = False
        _refresh_lock.release()


def start_scheduler():
    """Background auto-refresh + initial sync if the DB is empty."""
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True

    def loop():
        if not db.has_jobs():
            log.info("database empty — running initial sync")
            run_refresh(trigger="initial")
        while True:
            time.sleep(config.REFRESH_MINUTES * 60)
            try:
                run_refresh(trigger="scheduled")
            except Exception:
                log.exception("scheduled refresh failed")

    t = threading.Thread(target=loop, daemon=True, name="refresh-scheduler")
    t.start()
    log.info("scheduler started (refresh every %d min)", config.REFRESH_MINUTES)
