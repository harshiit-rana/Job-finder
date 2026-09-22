#!/usr/bin/env python3
"""RoleRadar — HTTP server: JSON API + static frontend (stdlib only)."""
from __future__ import annotations

import json
import logging
import mimetypes
import os
import re
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from aggregator import config, db, pipeline, salary as salary_mod, sources

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("roleradar.server")

VALID_TYPES = {"fulltime", "parttime", "contract", "internship", "freelance",
               "apprenticeship", "research", "gig", "other"}
VALID_REMOTE = {"remote", "hybrid", "onsite", "unknown", "flexible"}
VALID_LEVELS = {"entry", "mid", "senior", "leadership"}
VALID_SORT = {"newest", "relevance", "salary", "deadline"}


def parse_search_params(qs: dict) -> dict:
    def list_param(key, valid=None):
        vals = [v.strip() for v in qs.get(key, "").split(",") if v.strip()]
        if valid:
            vals = [v for v in vals if v in valid]
        return vals

    p = {
        "q": qs.get("q", "")[:300],
        "sort": qs.get("sort") if qs.get("sort") in VALID_SORT else "newest",
        "page": _int(qs.get("page"), 1),
        "per_page": _int(qs.get("per_page"), 20),
        "types": list_param("type", VALID_TYPES),
        "remote": list_param("remote", VALID_REMOTE),
        "levels": list_param("level", VALID_LEVELS),
        "sources": [s for s in list_param("source") if re.fullmatch(r"[A-Za-z0-9:_\-]{1,60}", s)],
        "posted": qs.get("posted") if qs.get("posted") in db.POSTED_WINDOWS else None,
        "min_salary": _int(qs.get("min_salary"), 0),
        "has_salary": qs.get("has_salary") == "1",
        "has_deadline": qs.get("has_deadline") == "1",
        "include_expired": qs.get("include_expired") == "1",
    }
    if qs.get("tag"):
        p["tag"] = qs["tag"][:40].lower()
    if qs.get("company"):
        p["company"] = qs["company"][:120]
    return p


def _int(v, default):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


class Handler(BaseHTTPRequestHandler):
    server_version = "RoleRadar/1.0"
    protocol_version = "HTTP/1.1"

    # -- helpers ------------------------------------------------------------
    def _send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_404(self):
        self._send_json({"error": "not found"}, 404)

    def _serve_static(self, path: str):
        if path in ("/", ""):
            path = "/index.html"
        path = path.lstrip("/")
        # prevent path traversal
        full = os.path.normpath(os.path.join(config.STATIC_DIR, path))
        if not full.startswith(os.path.abspath(config.STATIC_DIR) + os.sep) and \
           os.path.abspath(full) != os.path.join(os.path.abspath(config.STATIC_DIR), "index.html"):
            return self._send_404()
        if not os.path.isfile(full):
            return self._send_404()
        ctype, _ = mimetypes.guess_type(full)
        ctype = ctype or "application/octet-stream"
        if full.endswith(".js"):
            ctype = "application/javascript; charset=utf-8"
        with open(full, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    # -- routing ------------------------------------------------------------
    def do_GET(self):
        try:
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path.rstrip("/") or "/"
            qs = dict(urllib.parse.parse_qsl(parsed.query))

            if path == "/api/jobs":
                p = parse_search_params(qs)
                result = db.search(p)
                for job in result["results"]:
                    if job.get("salary_text") is None:
                        job["salary_label"] = salary_mod.format_salary(job)
                    else:
                        job["salary_label"] = salary_mod.format_salary(job)
                return self._send_json(result)

            m = re.fullmatch(r"/api/jobs/(\d+)", path)
            if m:
                job = db.get_job(int(m.group(1)))
                if not job:
                    return self._send_404()
                job["salary_label"] = salary_mod.format_salary(job)
                return self._send_json(job)

            if path == "/api/stats":
                s = db.stats()
                s["sync"] = pipeline.sync_state()
                return self._send_json(s)

            if path == "/api/sources":
                enabled = [s.info() for s in sources.enabled_sources()]
                stats = db.stats()
                for e in enabled:
                    run = stats["source_runs"].get(e["name"], {})
                    e["last_count"] = run.get("fetched")
                    e["last_status"] = run.get("status")
                    e["last_finished"] = run.get("finished_at")
                    e["jobs"] = stats["by_platform"].get(e["name"], 0)
                return self._send_json({
                    "enabled": enabled,
                    "disabled": sources.available_but_disabled(),
                })

            if path == "/api/health":
                return self._send_json({"status": "ok",
                                        "sync": pipeline.sync_state()["sync_running"]})
            if path == "/api/sync":
                st = pipeline.sync_state()
                st["last_sync"] = db.stats().get("last_sync")
                return self._send_json(st)

            return self._serve_static(parsed.path)
        except BrokenPipeError:
            pass
        except Exception as e:
            log.exception("request failed: %s", self.path)
            try:
                self._send_json({"error": f"{type(e).__name__}: {e}"}, 500)
            except Exception:
                pass

    def do_POST(self):
        try:
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path.rstrip("/") or "/"
            if path == "/api/refresh":
                threading.Thread(target=pipeline.run_refresh,
                                 kwargs={"trigger": "manual"}, daemon=True).start()
                return self._send_json({"started": True, "state": pipeline.sync_state()})
            return self._send_404()
        except BrokenPipeError:
            pass
        except Exception as e:
            log.exception("POST failed")
            self._send_json({"error": str(e)}, 500)

    def log_message(self, fmt, *args):
        msg = fmt % args
        if "/api/sync" not in msg and "/api/stats" not in msg:
            log.info("%s %s", self.address_string(), msg)


def main():
    db.init_db()
    pipeline.start_scheduler()
    httpd = ThreadingHTTPServer((config.HOST, config.PORT), Handler)
    httpd.daemon_threads = True
    log.info("RoleRadar listening on http://%s:%d", config.HOST, config.PORT)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
