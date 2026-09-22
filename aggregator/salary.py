"""Salary / compensation extraction and normalization."""
from __future__ import annotations

import re

from . import config

CURR_MAP = {
    "$": "USD", "US$": "USD", "USD": "USD", "CA$": "CAD", "C$": "CAD",
    "A$": "AUD", "AU$": "AUD", "CAD": "CAD", "AUD": "AUD",
    "€": "EUR", "EUR": "EUR", "£": "GBP", "GBP": "GBP",
    "₹": "INR", "INR": "INR", "Rs": "INR", "Rs.": "INR",
    "¥": "JPY", "JPY": "JPY", "SGD": "SGD", "S$": "SGD",
    "CHF": "CHF", "NZ$": "NZD", "NZD": "NZD", "PLN": "PLN", "zł": "PLN",
    "SEK": "SEK", "NOK": "NOK", "DKK": "DKK", "BRL": "BRL", "R$": "BRL",
    "ZAR": "ZAR", "AED": "AED",
}

_CURR_ALT = "|".join(sorted((re.escape(k) for k in CURR_MAP), key=len, reverse=True))

# e.g. "$120,000 - $150,000", "$120k–150k", "USD 90k to 110k", "₹8L - ₹12L"
_RANGE_RE = re.compile(
    rf"(?P<c1>{_CURR_ALT})?\s*"
    rf"(?P<n1>\d[\d,]*(?:\.\d+)?)\s*(?P<s1>[kKlL]|lakh?s?|lac)?\s*"
    rf"(?:-|–|—|to)\s*"
    rf"(?P<c2>{_CURR_ALT})?\s*"
    rf"(?P<n2>\d[\d,]*(?:\.\d+)?)\s*(?P<s2>[kKlL]|lakh?s?|lac)?",
    re.I)

# single value: "$150k", "€65,000", "INR 12,00,000", "up to $80/hr"
_SINGLE_RE = re.compile(
    rf"(?P<up>up to|upto|max(?:imum)?(?: of)?|starting (?:at|from)|from)?\s*"
    rf"(?P<c1>{_CURR_ALT})\s*(?P<n1>\d[\d,]*(?:\.\d+)?)\s*(?P<s1>[kKlL]|lakh?s?|lac)?",
    re.I)

_VALUE_CURR_RE = re.compile(
    rf"(?P<n1>\d[\d,]*(?:\.\d+)?)\s*(?P<s1>[kKlL]|lakh?s?|lac)?\s*"
    rf"(?P<c1>USD|EUR|GBP|INR|CAD|AUD|SGD)\b", re.I)

_LPA_RE = re.compile(r"(?P<n1>\d+(?:\.\d+)?)\s*(?:-|–|to)?\s*(?P<n2>\d+(?:\.\d+)?)?\s*(?:LPA|l\.p\.a|lakhs?\s+per\s+annum)", re.I)

_PERIOD_RE = re.compile(
    r"(per\s+year|yearly|annually|annual|p\.?\s?a\.?\b|/yr\b|/year|LPA|"
    r"per\s+month|monthly|/mo\b|/month|p\.m\b|"
    r"per\s+hour|hourly|/hr\b|/hour|ph\b|"
    r"per\s+day|daily|/day|"
    r"per\s+week|weekly|/week)", re.I)

_SALARY_HINT = re.compile(
    r"salary|compensation|pay|stipend|ctc|package|lpa|lakh|\$|€|£|₹|USD|EUR|GBP|INR|k/", re.I)


def _to_number(num: str, suffix: str | None, currency: str | None) -> float | None:
    if not num:
        return None
    if re.fullmatch(r"\d{1,3}(\.\d{3})+", num):   # European thousands: "65.000"
        num = num.replace(".", "")
    n = float(num.replace(",", ""))
    suf = (suffix or "").lower()
    if suf == "k":
        n *= 1_000
    elif suf in ("l", "lakh", "lakhs", "lac"):
        n *= 100_000
    # lakh scale: applies only when a lakh suffix was present, or a small bare
    # number sits directly next to INR
    if suf == "" and currency == "INR" and n < 200:
        n *= 100_000
    return n


def _detect_period(ctx: str) -> str | None:
    m = _PERIOD_RE.search(ctx)
    if not m:
        return None
    t = m.group(1).lower()
    if re.search(r"month|/mo\b|p\.m", t):
        return "month"
    if re.search(r"hour|/hr|ph\b", t):
        return "hour"
    if re.search(r"day", t) and "stipend" not in t:
        return "day"
    if re.search(r"week", t):
        return "week"
    return "year"


def _yearly(value: float, period: str) -> float:
    return {"year": 1, "month": 12, "week": 52, "day": 260, "hour": 2080}[period] * value


def _finalize(lo, hi, currency, period, raw):
    if not lo and hi:
        lo, hi = hi, hi
    if lo and hi and hi < lo:
        lo, hi = hi, lo
    currency = currency or "USD"
    period = period or "year"
    fx = config.FX_TO_USD.get(currency)
    usd_lo = usd_hi = None
    if fx:
        usd_lo = round(_yearly(lo, period) * fx) if lo else None
        usd_hi = round(_yearly(hi, period) * fx) if hi else None
    # plausibility guard-rails
    if usd_hi and usd_hi > 2_000_000:
        return None
    if period == "year" and usd_hi and usd_hi < 500:
        return None
    return {
        "salary_min": lo, "salary_max": hi, "salary_currency": currency,
        "salary_period": period, "salary_text": raw.strip(" .,-–—"),
        "usd_min": usd_lo, "usd_max": usd_hi,
    }


def build_salary(lo, hi, currency="USD", period="year", text=None):
    """Public helper for sources that ship structured salary fields."""
    try:
        lo = float(lo) if lo else None
        hi = float(hi) if hi else None
    except (TypeError, ValueError):
        return None
    if not lo and not hi:
        return None
    return _finalize(lo, hi, currency, period, text or "")


def extract_salary(text: str | None):
    """Extract a normalized salary range from free text.

    Returns dict with min/max/currency/period + yearly USD estimate, or None.
    """
    if not text or not _SALARY_HINT.search(text):
        return None
    snippet = text[:60_000]

    m = _RANGE_RE.search(snippet)
    if m:
        cur = CURR_MAP.get(m.group("c1") or "") or CURR_MAP.get(m.group("c2") or "") or None
        lo = _to_number(m.group("n1"), m.group("s1"), cur)
        hi = _to_number(m.group("n2"), m.group("s2"), cur)
        ctx = snippet[max(0, m.start() - 60): m.end() + 40]
        if re.search(r"LPA|lakhs?\s+per\s+annum", ctx, re.I) and (not cur or cur == "INR"):
            cur = "INR"
            lo = (lo or 0) * 100_000 if lo and lo < 1000 else lo
            hi = (hi or 0) * 100_000 if hi and hi < 1000 else hi
        if lo and hi and hi / max(lo, 1) < 6:  # sane ratio
            period = _detect_period(ctx)
            if period is None and hi < 400 and cur in (None, "USD", "EUR", "GBP", "CAD", "AUD"):
                period = "hour"
            out = _finalize(lo, hi, cur, period, m.group(0))
            if out:
                return out

    m = _LPA_RE.search(snippet)
    if m:
        lo = float(m.group("n1")) * 100_000
        hi = float(m.group("n2")) * 100_000 if m.group("n2") else lo
        out = _finalize(lo, hi, "INR", "year", m.group(0))
        if out:
            return out

    m = _VALUE_CURR_RE.search(snippet)
    if m:
        lo = _to_number(m.group("n1"), m.group("s1"), m.group("c1"))
        cur = CURR_MAP.get(m.group("c1"))
        ctx = snippet[max(0, m.start() - 60): m.end() + 60]
        out = _finalize(lo, lo, cur, _detect_period(ctx), m.group(0))
        if out:
            return out

    m = _SINGLE_RE.search(snippet)
    if m:
        cur = CURR_MAP.get(m.group("c1") or "")
        lo = _to_number(m.group("n1"), m.group("s1"), cur)
        if lo:
            ctx = snippet[max(0, m.start() - 60): m.end() + 60]
            period = _detect_period(ctx)
            if period is None:
                if lo < 400 and cur in ("USD", "EUR", "GBP", "CAD", "AUD", None):
                    period = "hour"
                elif cur == "INR" and lo < 300_000:
                    period = "month"
            out = _finalize(lo, lo, cur, period, m.group(0))
            if out:
                return out
    return None


def format_salary(job: dict) -> str | None:
    """Human friendly label."""
    if job.get("salary_text"):
        return job["salary_text"]
    lo, hi, cur = job.get("salary_min"), job.get("salary_max"), job.get("salary_currency") or "USD"
    if not lo and not hi:
        return None
    sym = {"USD": "$", "EUR": "€", "GBP": "£", "INR": "₹"}.get(cur, cur + " ")

    def short(v):
        return f"{v/1000:.0f}k" if v >= 1000 else f"{v:g}"

    per = {"year": "/yr", "month": "/mo", "hour": "/hr", "week": "/wk", "day": "/day"}.get(
        job.get("salary_period") or "year", "")
    if lo and hi and lo != hi:
        return f"{sym}{short(lo)}–{sym}{short(hi)}{per}"
    v = lo or hi
    return f"{sym}{short(v)}{per}"
