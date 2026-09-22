"""HTTP helpers, HTML->text, date parsing, and text utilities."""
from __future__ import annotations

import gzip
import html
import json
import logging
import re
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser

from . import config

log = logging.getLogger("roleradar.util")

_ssl_ctx = ssl.create_default_context()
_ssl_ctx.check_hostname = True

_host_last_hit: dict[str, float] = {}

MAX_BODY = 6 * 1024 * 1024  # 6 MB per response


class FetchError(Exception):
    pass


def fetch(url: str, *, headers: dict | None = None, data: bytes | None = None,
          timeout: int | None = None, retries: int = 2) -> bytes:
    """Polite HTTP GET/POST returning raw bytes."""
    timeout = timeout or config.HTTP_TIMEOUT
    host = urllib.parse.urlsplit(url).netloc
    hdrs = {
        "User-Agent": config.USER_AGENT,
        "Accept": "application/json, text/html, application/rss+xml, application/xml;q=0.9, */*;q=0.8",
        "Accept-Encoding": "gzip",
        "Accept-Language": "en-US,en;q=0.9",
    }
    if headers:
        hdrs.update(headers)

    last_err: Exception | None = None
    for attempt in range(retries + 1):
        # per-host politeness delay
        gap = time.time() - _host_last_hit.get(host, 0)
        if gap < config.HTTP_DELAY_PER_HOST:
            time.sleep(config.HTTP_DELAY_PER_HOST - gap)
        _host_last_hit[host] = time.time()
        try:
            req = urllib.request.Request(url, data=data, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx) as resp:
                raw = resp.read(MAX_BODY + 1)
                if len(raw) > MAX_BODY:
                    raise FetchError(f"response too large from {host}")
                if resp.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return raw
        except urllib.error.HTTPError as e:
            last_err = FetchError(f"HTTP {e.code} for {url}")
            if e.code in (404, 410, 400, 401, 403):
                raise last_err
        except (urllib.error.URLError, socket.timeout, TimeoutError, ssl.SSLError, OSError) as e:
            last_err = FetchError(f"{type(e).__name__}: {e} for {url}")
        if attempt < retries:
            time.sleep(1.5 * (attempt + 1))
    raise last_err  # type: ignore[misc]


def fetch_json(url: str, **kw):
    raw = fetch(url, **kw)
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except json.JSONDecodeError as e:
        raise FetchError(f"bad JSON from {url}: {e}")


def post_json(url: str, payload: dict, **kw):
    body = json.dumps(payload).encode()
    headers = kw.pop("headers", {})
    headers["Content-Type"] = "application/json"
    return json.loads(fetch(url, data=body, headers=headers, **kw).decode("utf-8", "replace"))


# ---------------------------------------------------------------------------
# HTML -> text
# ---------------------------------------------------------------------------

_BLOCK_TAGS = {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5",
               "h6", "tr", "table", "blockquote", "pre", "section", "article",
               "header", "footer", "hr"}


class _HTML2Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "iframe"):
            self._skip += 1
        elif tag in ("br", "li"):
            self.parts.append("\n")
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "iframe"):
            self._skip = max(0, self._skip - 1)
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)

    def text(self) -> str:
        t = "".join(self.parts)
        t = html.unescape(t)
        t = re.sub(r"[ \t]+", " ", t)
        t = re.sub(r"\n[ \t]+", "\n", t)
        t = re.sub(r"\n{3,}", "\n\n", t)
        return t.strip()


def html_to_text(fragment: str | None) -> str:
    if not fragment:
        return ""
    p = _HTML2Text()
    try:
        p.feed(fragment[:500_000])
        p.close()
        return p.text()
    except Exception:
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", fragment)).strip()


def clean_html(fragment: str | None, max_len: int = 400_000) -> str:
    """Best-effort strip of dangerous tags while keeping formatting."""
    if not fragment:
        return ""
    fragment = fragment[:max_len]
    fragment = re.sub(r"(?is)<(script|style|iframe|object|embed|form|input|button|select|textarea)[^>]*>.*?</\1>", "", fragment)
    fragment = re.sub(r"(?is)<(script|style|iframe|object|embed|form)[^>]*/>", "", fragment)
    fragment = re.sub(r"(?i)\son\w+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", "", fragment)
    fragment = re.sub(r"(?i)(href|src)\s*=\s*(['\"])\s*javascript:[^'\"]*\2", r"\1=\2#\2", fragment)
    return fragment.strip()


_ws = re.compile(r"\s+")
_punct = re.compile(r"[^\w\s+#./-]", re.UNICODE)


def norm_space(s: str | None) -> str:
    return _ws.sub(" ", (s or "")).strip()


def slug_text(s: str | None) -> str:
    """Aggressive normalization used for matching/fingerprints."""
    s = html.unescape(s or "").lower()
    s = _punct.sub(" ", s)
    return _ws.sub(" ", s).strip()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Date parsing
# ---------------------------------------------------------------------------

_REL_RE = re.compile(
    r"(?:(?:posted|updated|listed)\s+)?(\d+)\s*\+\s*(hour|day|week|month|yr|year)s?\s*ago|"
    r"(\d+)([hdwmy])\s*ago|"
    r"(?:posted\s+)?(\d+)\s*(hour|day|week|month|year)s?\s*ago",
    re.I)

_DATE_FORMATS = [
    "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S",
    "%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z", "%d %b %Y", "%B %d, %Y",
    "%b %d, %Y", "%d %B %Y", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d", "%B %d %Y",
    "%d-%m-%Y", "%m-%d-%Y", "%d.%m.%Y", "%Y.%m.%d",
]


def parse_date(value, *, now: datetime | None = None) -> datetime | None:
    """Parse epochs, ISO strings, RSS dates and relative dates such as
    '3 days ago' / '30+ days ago' / '2w ago'. Returns tz-aware UTC datetime."""
    if value is None:
        return None
    now = now or utcnow()
    if isinstance(value, (int, float)):
        v = float(value)
        if v > 1e12:
            v /= 1000.0
        try:
            return datetime.fromtimestamp(v, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    s = str(value).strip()
    if not s:
        return None
    if re.fullmatch(r"\d{9,13}", s):
        return parse_date(float(s), now=now)

    low = s.lower()
    if low in ("today", "just now", "new"):
        return now
    if low == "yesterday":
        return now - timedelta(days=1)
    m = _REL_RE.search(low)
    if m:
        num = int(next(g for g in (m.group(1), m.group(3), m.group(5)) if g))
        unit = (m.group(2) or m.group(6) or "").lower()
        if not unit and m.group(4):
            unit = {"h": "hour", "d": "day", "w": "week", "m": "month", "y": "year"}[m.group(4).lower()]
        days = {"hour": 0, "day": 1, "week": 7, "month": 30, "year": 365, "yr": 365}.get(unit, 1)
        if unit == "hour":
            return now - timedelta(hours=num)
        return now - timedelta(days=days * num)

    for fmt in _DATE_FORMATS:
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            continue
    try:
        dt = parsedate_to_datetime(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError):
        pass
    try:
        iso_s = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(iso_s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


_MONTHS = ("january february march april may june july august september "
           "october november december jan feb mar apr jun jul aug sep sept oct "
           "nov dec").split()

_DEADLINE_KW = re.compile(
    r"(apply by|apply before|application deadline|deadline[:\s]|last date|"
    r"applications? close[sd]?|closing date|submit (?:your application )?by|"
    r"apply no later than)", re.I)

_DEADLINE_DATE = re.compile(
    r"(\d{4}-\d{2}-\d{2}|"
    r"(?:\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?(?:%s)\b[,\s]*\d{4}|"
    r"(?:%s)\s+\d{1,2}(?:st|nd|rd|th)?[,\s]*\d{4}|"
    r"\d{1,2}/\d{1,2}/\d{4})" % ("|".join(_MONTHS), "|".join(_MONTHS)),
    re.I)


def extract_deadline(text: str | None, *, now: datetime | None = None) -> datetime | None:
    """Find 'apply by <date>'-style deadlines inside free text."""
    if not text:
        return None
    now = now or utcnow()
    for kw in _DEADLINE_KW.finditer(text):
        window = text[kw.end(): kw.end() + 90]
        m = _DEADLINE_DATE.search(window)
        if m:
            cand = m.group(1).replace(" of ", " ")
            cand = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", cand, flags=re.I)
            dt = parse_date(cand, now=now)
            if dt and dt > now - timedelta(days=2) and dt < now + timedelta(days=730):
                return dt
    return None
