"""Source adapters + registry: everything RoleRadar knows how to pull from.

Single-module design (the whole adapter layer lives here):
  * Source base class
  * Aggregators & feeds   (RemoteOK, Remotive, Arbeitnow, Jobicy, WWR, HN, GitHub lists)
  * Career-page boards    (Greenhouse, Ashby, Lever, generic RSS)
  * Keyed APIs            (Adzuna, Jooble, The Muse — auto-enabled via env)
  * enabled_sources() / available_but_disabled()
"""
from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

from . import config, salary as salary_mod, util

log = logging.getLogger("roleradar.sources")


# ===========================================================================
# Base
# ===========================================================================

class Source:
    """One opportunity source. `fetch()` returns raw listing dicts."""

    name: str = "base"          # short unique id, e.g. "remoteok"
    platform: str = "base"      # platform family, e.g. "Greenhouse"
    label: str = ""             # human label for the UI
    kind: str = "aggregator"    # aggregator | board | feed | community | api
    requires_key: bool = False

    def fetch(self) -> list[dict]:
        raise NotImplementedError

    def info(self) -> dict:
        return {
            "name": self.name,
            "platform": self.platform,
            "label": self.label or self.name,
            "kind": self.kind,
        }


# ===========================================================================
# Aggregators & feeds (no key required)
# ===========================================================================

class RemoteOKSource(Source):
    name = "remoteok"
    platform = "RemoteOK"
    label = "RemoteOK"
    kind = "aggregator"
    URL = "https://remoteok.com/api"

    def fetch(self):
        data = util.fetch_json(self.URL, headers={"Accept": "application/json"})
        out = []
        for item in data:
            if not isinstance(item, dict) or "position" not in item:
                continue
            sal = None
            try:
                lo, hi = float(item.get("salary_min") or 0), float(item.get("salary_max") or 0)
                if lo > 0 or hi > 0:
                    sal = salary_mod.build_salary(lo or hi, hi or lo, "USD", "year")
            except (TypeError, ValueError):
                pass
            jid = item.get("id") or item.get("slug") or item.get("url")
            out.append({
                "source": self.name, "source_id": str(jid),
                "title": item.get("position"), "company": item.get("company"),
                "location": item.get("location") or "Worldwide",
                "description_html": item.get("description"),
                "tags": item.get("tags") or [],
                "posted_at": item.get("date") or item.get("epoch"),
                "url": item.get("url"), "apply_url": item.get("apply_url") or item.get("url"),
                "remote_flag": True, "salary": sal,
            })
        return out


class RemotiveSource(Source):
    name = "remotive"
    platform = "Remotive"
    label = "Remotive"
    kind = "aggregator"
    URL = "https://remotive.com/api/remote-jobs?limit=300"

    def fetch(self):
        data = util.fetch_json(self.URL)
        out = []
        for item in data.get("jobs", []):
            out.append({
                "source": self.name, "source_id": str(item.get("id")),
                "title": item.get("title"), "company": item.get("company_name"),
                "location": item.get("candidate_required_location") or "Worldwide",
                "description_html": item.get("description"),
                "tags": [item.get("category") or ""] + (item.get("tags") or []),
                "source_type": item.get("job_type"),
                "posted_at": item.get("publication_date"),
                "url": item.get("url"), "apply_url": item.get("url"),
                "remote_flag": True,
                "salary_hint": item.get("salary") or "",
            })
        return out


class ArbeitnowSource(Source):
    name = "arbeitnow"
    platform = "Arbeitnow"
    label = "Arbeitnow"
    kind = "aggregator"
    PAGES = 4
    URL = "https://www.arbeitnow.com/api/job-board-api"

    def fetch(self):
        out = []
        url = self.URL
        for _ in range(self.PAGES):
            if not url:
                break
            data = util.fetch_json(url)
            for item in data.get("data", []):
                out.append({
                    "source": self.name, "source_id": str(item.get("slug")),
                    "title": item.get("title"), "company": item.get("company_name"),
                    "location": item.get("location") or "",
                    "description_html": item.get("description"),
                    "tags": item.get("tags") or [],
                    "source_type": (item.get("job_types") or [None])[0],
                    "posted_at": item.get("created_at"),
                    "url": item.get("url"), "apply_url": item.get("url"),
                    "remote_flag": bool(item.get("remote")) or None,
                    "extra": {"visa_sponsorship": bool(item.get("visa_sponsorship"))},
                })
            url = (data.get("links") or {}).get("next")
        return out


class JobicySource(Source):
    name = "jobicy"
    platform = "Jobicy"
    label = "Jobicy"
    kind = "aggregator"
    URL = "https://jobicy.com/api/v2/remote-jobs?count=50"

    def fetch(self):
        data = util.fetch_json(self.URL)
        out = []
        for item in data.get("jobs", []):
            sal = None
            try:
                sal = salary_mod.build_salary(
                    item.get("annualSalaryMin"), item.get("annualSalaryMax"),
                    item.get("salaryCurrency") or "USD", "year")
            except Exception:
                pass
            out.append({
                "source": self.name, "source_id": str(item.get("id")),
                "title": item.get("jobTitle"), "company": item.get("companyName"),
                "location": item.get("jobGeo") or "Worldwide",
                "description_html": item.get("jobDescription") or item.get("jobExcerpt"),
                "tags": [item.get("jobIndustry") or "", item.get("jobLevel") or ""],
                "source_type": item.get("jobType"),
                "posted_at": item.get("pubDate"),
                "url": item.get("url"), "apply_url": item.get("url"),
                "remote_flag": True, "salary": sal,
            })
        return out


class WeWorkRemotelySource(Source):
    name = "weworkremotely"
    platform = "We Work Remotely"
    label = "We Work Remotely"
    kind = "feed"

    def fetch(self):
        out = []
        seen_ids = set()
        for cat in config.WE_WORK_REMOTELY_CATEGORIES:
            url = f"https://weworkremotely.com/categories/{cat}.rss"
            try:
                raw = util.fetch(url, timeout=20, retries=1)
            except Exception as e:
                log.info("WWR category %s failed: %s", cat, e)
                continue
            try:
                root = ET.fromstring(raw)
            except ET.ParseError:
                continue
            for item in root.iter("item"):
                def txt(tag):
                    el = item.find(tag)
                    return el.text if el is not None and el.text else ""

                link = txt("link").strip()
                if link in seen_ids:
                    continue
                seen_ids.add(link)
                full_title = txt("title")
                company, title = "", full_title
                if ":" in full_title:
                    company, title = full_title.split(":", 1)
                title = re.sub(r"\s*\([^)]*\)\s*$", "", title).strip()
                region = txt("region") or ""
                desc = txt("description")
                apply_url = self._extract_apply(desc) or link
                out.append({
                    "source": self.name, "source_id": link.rsplit("/", 1)[-1] or link,
                    "title": title, "company": company.strip(),
                    "location": region,
                    "description_html": desc,
                    "tags": [txt("category"),
                             cat.replace("remote-", "").replace("-jobs", "").replace("-", " ")],
                    "posted_at": txt("pubDate"),
                    "url": link, "apply_url": apply_url,
                    "remote_flag": True,
                })
        return out

    @staticmethod
    def _extract_apply(desc: str) -> str | None:
        if not desc:
            return None
        m = re.findall(r'href="([^"]+)"[^>]*>\s*(?:Apply|apply)', desc[-4000:])
        if m:
            return m[-1]
        return None


class HackerNewsWhoIsHiring(Source):
    name = "hn_hiring"
    platform = "Hacker News"
    label = "HN: Who is Hiring"
    kind = "community"
    SEARCH = "https://hn.algolia.com/api/v1/search_by_date?tags=story,author_whoishiring"

    def fetch(self):
        data = util.fetch_json(self.SEARCH)
        story_id = None
        for hit in data.get("hits", []):
            t = (hit.get("title") or "").lower()
            if t.startswith("ask hn: who is hiring"):
                story_id = hit.get("objectID")
                break
        if not story_id:
            log.warning("HN: could not find current 'Who is hiring' thread")
            return []
        thread = util.fetch_json(f"https://hn.algolia.com/api/v1/items/{story_id}")
        out = []
        for child in thread.get("children", []) or []:
            parsed = self._parse_comment(child)
            if parsed:
                out.append(parsed)
        return out

    def _parse_comment(self, node):
        cid = node.get("id")
        html = node.get("text") or ""
        if not html or not cid:
            return None
        text = util.html_to_text(html)
        if not text or "seeking work" in text[:200].lower():
            return None
        first = " ".join(text.split("\n", 1)[0].split())
        if "|" not in first:
            return None
        parts = [p.strip() for p in first.split("|") if p.strip()]
        if not parts or len(text) < 60:
            return None
        company = parts[0]
        title = parts[1] if len(parts) > 1 else "Multiple roles"
        location = parts[2] if len(parts) > 2 else ""
        if len(company) < 2 or len(company) > 80:
            return None
        salary_hint = ""
        m = re.search(r"(\$[\d,]+k?.{0,30}(?:-|–|to).{0,30}\$?[\d,]+k?)", text)
        if m:
            salary_hint = m.group(1)
        return {
            "source": self.name, "source_id": str(cid),
            "title": title[:140], "company": company,
            "location": location[:120],
            "description_html": html,
            "posted_at": node.get("created_at_i") or node.get("created_at"),
            "url": f"https://news.ycombinator.com/item?id={cid}",
            "apply_url": f"https://news.ycombinator.com/item?id={cid}",
            "remote_flag": "remote" in first.lower() or None,
            "salary_hint": salary_hint,
            "tags": ["hacker news", "startup"],
            "extra": {"hn_thread": True},
        }


# ---------------------------------------------------------------------------
# GitHub community internship lists (README HTML tables)
# ---------------------------------------------------------------------------

class _RowParser(HTMLParser):
    """Parse one <tr> worth of HTML into cells of {text, links}."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.cells: list[dict] = []
        self._cell = None
        self._cur_href = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "td":
            self._cell = {"text": [], "links": []}
        elif tag == "br" and self._cell is not None:
            self._cell["text"].append(";")
        elif tag == "a" and self._cell is not None:
            self._cur_href = attrs.get("href")
        elif tag == "img" and self._cell is not None and self._cur_href:
            alt = (attrs.get("alt") or "").lower()
            self._cell["links"].append((alt, self._cur_href))

    def handle_endtag(self, tag):
        if tag == "td" and self._cell is not None:
            text = re.sub(r"\s+", " ", " ".join(self._cell["text"]))
            self.cells.append({"text": text.strip(" ;"), "links": self._cell["links"]})
            self._cell = None
        elif tag == "a":
            self._cur_href = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell["text"].append(data)


class GitHubInternships(Source):
    name = "github_internships"
    platform = "GitHub Lists"
    label = "SimplifyJobs Lists"
    kind = "community"

    AGE_RE = re.compile(r"^(\d+)\s*([dmoy]|wk|mo)s?$", re.I)
    SECTION_RE = re.compile(r"^#{2,4}\s+(.*?)\s*$")

    def fetch(self):
        from datetime import timedelta
        out = []
        for repo_label, job_type, urls in config.GITHUB_INTERNSHIP_REPOS:
            md = None
            for u in urls:
                try:
                    md = util.fetch(u, timeout=25, retries=1).decode("utf-8", "replace")
                    break
                except Exception:
                    continue
            if not md:
                log.info("GitHub list %s unreachable", repo_label)
                continue
            out.extend(self._parse_readme(md, repo_label, job_type, timedelta))
        return out

    def _parse_readme(self, md: str, repo_label: str, job_type: str, timedelta):
        rows = []
        cur_html: list[str] = []
        in_row = False
        line_sections: list[tuple[int, str]] = []

        for i, line in enumerate(md.splitlines()):
            m = self.SECTION_RE.match(line)
            if m and re.search(r"roles|internship|jobs|positions", m.group(1), re.I):
                line_sections.append((i, re.sub(r"[^\w\s/&-]", "", m.group(1)).strip()))
            if "<tr" in line:
                in_row = True
                cur_html = [line]
            elif in_row:
                cur_html.append(line)
            if in_row and "</tr>" in line:
                rows.append("\n".join(cur_html))
                in_row = False

        row_lines = [i for i, l in enumerate(md.splitlines()) if "<tr" in l]
        out = []
        prev_company = None
        for idx, row_html in enumerate(rows):
            section = ""
            li = row_lines[idx] if idx < len(row_lines) else 0
            for si, sname in line_sections:
                if si < li:
                    section = sname
                else:
                    break
            parsed = self._parse_row(row_html, prev_company, repo_label, job_type,
                                     section, timedelta)
            if parsed:
                prev_company = parsed["company"]
                out.append(parsed)
        return out

    def _parse_row(self, row_html, prev_company, repo_label, job_type, section, timedelta):
        p = _RowParser()
        try:
            p.feed(row_html)
        except Exception:
            return None
        cells = p.cells
        if len(cells) < 4:
            return None
        joined = " ".join(c["text"] for c in cells)
        if "company" in cells[0]["text"].lower() and "role" in joined.lower():
            return None
        if "🔒" in joined or re.search(r"\bclosed\b", cells[-1]["text"], re.I):
            return None

        apply_url = None
        for alt, href in cells[3]["links"]:
            if "apply" in alt and "simplify.jobs" not in href:
                apply_url = href
                break
        if not apply_url:
            for alt, href in cells[3]["links"]:
                if "simplify.jobs" not in href:
                    apply_url = href
                    break
        if not apply_url:
            return None

        company = re.sub(r"[\*_⛔️🇺🇸🛂🔒🎓🔥]", "", cells[0]["text"]).strip()
        if company in ("↳", "", "same", "-") and prev_company:
            company = prev_company
        title = re.sub(r"[\*_⛔️🇺🇸🛂🔒🎓🔥]", "", cells[1]["text"]).strip()
        if not company or not title or len(title) > 150 or len(company) > 80:
            return None

        location = re.sub(r"[\*_⛔️🇺🇸🛂🔒🎓🔥📍]", "", cells[2]["text"]).strip(" ;")
        location = re.sub(r"(\s*;\s*)+", "; ", location)

        posted = None
        if len(cells) > 4:
            am = self.AGE_RE.match(cells[4]["text"].strip())
            if am:
                n, unit = int(am.group(1)), am.group(2).lower()
                days = {"d": 1, "wk": 7, "w": 7, "mo": 30, "m": 30, "y": 365}.get(unit, 1)
                posted = util.iso(util.utcnow() - timedelta(days=n * days))

        notes = []
        if "🛂" in joined:
            notes.append("No visa sponsorship")
        if "🇺🇸" in joined:
            notes.append("Requires U.S. citizenship")
        if "🎓" in joined:
            notes.append("Advanced degree required")

        tags = ["student" if job_type == "internship" else "new grad", section][:2]
        tags = [t for t in tags if t] + [job_type]

        return {
            "source": self.name,
            "source_id": re.sub(r"\W+", "", apply_url[-60:]).lower() or
                         f"{company[:20]}{title[:20]}".replace(" ", "").lower(),
            "title": title, "company": company,
            "location": location,
            "description_text": (
                f"{title} at {company} ({location or 'location varies'}) — curated by {repo_label}.\n\n"
                f"Category: {section or 'general'}.\n" +
                ("\n".join(notes) + "\n" if notes else "") +
                "This listing comes from a community-maintained list that is updated daily; "
                "apply on the company's official page via the link."),
            "posted_at": posted,
            "url": apply_url, "apply_url": apply_url,
            "source_type": job_type,
            "remote_flag": True if "remote" in location.lower() else None,
            "tags": tags,
            "extra": {"curated_list": repo_label,
                      "no_sponsorship": "🛂" in joined, "us_citizenship": "🇺🇸" in joined},
        }


# ===========================================================================
# Career-page boards: Greenhouse, Ashby, Lever, generic RSS
# (the direct ATS data behind LinkedIn/Indeed company listings)
# ===========================================================================

def _pretty(slug: str) -> str:
    special = {
        "openai": "OpenAI", "notion": "Notion", "cohere": "Cohere", "figma": "Figma",
        "databricks": "Databricks", "cloudflare": "Cloudflare", "datadog": "Datadog",
        "stripe": "Stripe", "coinbase": "Coinbase", "perplexity": "Perplexity",
        "ramp": "Ramp", "airbnb": "Airbnb", "pinterest": "Pinterest",
        "dropbox": "Dropbox", "roblox": "Roblox", "instacart": "Instacart",
        "lyft": "Lyft", "twilio": "Twilio", "okta": "Okta", "gitlab": "GitLab",
        "mongodb": "MongoDB", "reddit": "Reddit", "robinhood": "Robinhood",
        "chime": "Chime", "brex": "Brex", "duolingo": "Duolingo",
        "discord": "Discord", "elastic": "Elastic", "twitch": "Twitch",
        "medium": "Medium", "asana": "Asana", "udemy": "Udemy",
        "coursera": "Coursera", "khanacademy": "Khan Academy",
        "pagerduty": "PagerDuty", "mixpanel": "Mixpanel", "newrelic": "New Relic",
        "sumologic": "Sumo Logic", "launchdarkly": "LaunchDarkly",
        "elevenlabs": "ElevenLabs", "langchain": "LangChain", "temporal": "Temporal",
        "supabase": "Supabase", "linear": "Linear", "modal": "Modal",
        "render": "Render", "encord": "Encord", "causal": "Causal",
        "brightwheel": "Brightwheel", "workos": "WorkOS", "resend": "Resend",
        "neon": "Neon", "hex": "Hex", "motherduck": "MotherDuck", "dune": "Dune",
        "nansen": "Nansen", "clerk": "Clerk", "llamaindex": "LlamaIndex",
    }
    return special.get(slug, slug.replace("-", " ").replace("_", " ").title())


class GreenhouseBoard(Source):
    platform = "Greenhouse"
    kind = "board"

    def __init__(self, slug: str):
        self.slug = slug
        self.name = f"gh:{slug}"
        self.label = f"{_pretty(slug)} Careers"

    def fetch(self):
        url = f"https://boards-api.greenhouse.io/v1/boards/{self.slug}/jobs?content=true"
        try:
            data = util.fetch_json(url, timeout=25, retries=1)
        except Exception as e:
            log.info("Greenhouse board %s failed: %s", self.slug, e)
            return []
        out = []
        for item in data.get("jobs", []):
            loc = item.get("location") or {}
            depts = item.get("departments") or []
            out.append({
                "source": self.name,
                "source_id": f"{self.slug}:{item.get('id')}",
                "title": item.get("title"),
                "company": _pretty(self.slug),
                "location": loc.get("name") if isinstance(loc, dict) else str(loc or ""),
                "description_html": item.get("content"),
                "tags": [d.get("name", "") for d in depts if isinstance(d, dict)],
                "posted_at": item.get("updated_at"),
                "url": item.get("absolute_url"), "apply_url": item.get("absolute_url"),
                "remote_flag": True if "remote" in str(
                    loc.get("name") if isinstance(loc, dict) else loc).lower() else None,
                "extra": {"ats": "greenhouse"},
            })
        return out


class AshbyBoard(Source):
    platform = "Ashby"
    kind = "board"

    def __init__(self, slug: str):
        self.slug = slug
        self.name = f"ashby:{slug}"
        self.label = f"{_pretty(slug)} Careers"

    def fetch(self):
        url = f"https://api.ashbyhq.com/posting-api/job-board/{self.slug}?includeCompensation=true"
        try:
            data = util.fetch_json(url, timeout=25, retries=1)
        except Exception as e:
            log.info("Ashby board %s failed: %s", self.slug, e)
            return []
        out = []
        for item in data.get("jobs", []):
            if item.get("isListed") is False:
                continue
            loc = item.get("location")
            if isinstance(loc, dict):
                loc = loc.get("name") or loc.get("locationName") or ""
            loc = str(loc or item.get("locationName") or "")
            desc = item.get("descriptionHtml") or item.get("descriptionPlain") or ""
            comp = item.get("compensation")
            salary_hint = ""
            if isinstance(comp, dict):
                salary_hint = comp.get("compensationTierSummary") or comp.get("summary") or ""
            elif isinstance(comp, str):
                salary_hint = comp
            out.append({
                "source": self.name,
                "source_id": f"{self.slug}:{item.get('id')}",
                "title": item.get("title"),
                "company": _pretty(self.slug),
                "location": loc,
                "description_html": desc if "<" in desc else "",
                "description_text": "" if "<" in desc else desc,
                "tags": [item.get("department") or "", item.get("team") or ""],
                "source_type": (item.get("employmentType") or "").lower() or None,
                "posted_at": item.get("publishedAt"),
                "url": item.get("jobUrl"),
                "apply_url": item.get("applyUrl") or item.get("jobUrl"),
                "remote_flag": True if "remote" in loc.lower() else None,
                "salary_hint": salary_hint,
                "extra": {"ats": "ashby"},
            })
        return out


class LeverPostings(Source):
    platform = "Lever"
    kind = "board"

    def __init__(self, slug: str):
        self.slug = slug
        self.name = f"lever:{slug}"
        self.label = f"{_pretty(slug)} Careers"

    def fetch(self):
        url = f"https://api.lever.co/v0/postings/{self.slug}?mode=json"
        try:
            data = util.fetch_json(url, timeout=25, retries=1)
        except Exception as e:
            log.info("Lever postings %s failed: %s", self.slug, e)
            return []
        if not isinstance(data, list):
            return []
        out = []
        for item in data:
            cats = item.get("categories") or {}
            parts = [item.get("description") or ""]
            for lst in item.get("lists") or []:
                if lst.get("text"):
                    parts.append(f"<h4>{lst['text']}</h4>")
                if lst.get("content"):
                    parts.append(lst["content"])
            if item.get("additional"):
                parts.append(item["additional"])
            out.append({
                "source": self.name,
                "source_id": f"{self.slug}:{item.get('id')}",
                "title": item.get("text"),
                "company": _pretty(self.slug),
                "location": cats.get("location") or "",
                "description_html": "\n".join(parts),
                "tags": [cats.get("team") or ""],
                "source_type": (cats.get("commitment") or "").lower() or None,
                "posted_at": item.get("createdAt"),
                "url": item.get("hostedUrl"),
                "apply_url": item.get("applyUrl") or item.get("hostedUrl"),
                "remote_flag": True if "remote" in str(cats.get("location") or "").lower() else None,
                "extra": {"ats": "lever"},
            })
        return out


class GenericRSSFeed(Source):
    platform = "RSS"
    kind = "feed"

    def __init__(self, name: str, url: str):
        self.name = f"rss:{re.sub(r'[^a-z0-9]+', '-', name.lower())[:30]}"
        self.feed_name = name
        self.url = url
        self.label = name

    def fetch(self):
        try:
            raw = util.fetch(self.url, timeout=20, retries=1)
            root = ET.fromstring(raw)
        except Exception as e:
            log.info("RSS %s failed: %s", self.feed_name, e)
            return []
        out = []
        for item in root.iter("item"):
            def txt(tag):
                el = item.find(tag)
                return el.text if el is not None and el.text else ""
            link = txt("link").strip()
            title = txt("title")
            company = self.feed_name
            if ":" in title:
                company, title = [p.strip() for p in title.split(":", 1)]
            if not link or not title:
                continue
            out.append({
                "source": self.name, "source_id": link,
                "title": title, "company": company,
                "location": "", "description_html": txt("description"),
                "posted_at": txt("pubDate"),
                "url": link, "apply_url": link, "remote_flag": None,
            })
        return out


# ===========================================================================
# Keyed APIs (enabled automatically when env keys are present)
# ===========================================================================

class AdzunaSource(Source):
    name = "adzuna"
    platform = "Adzuna"
    label = "Adzuna"
    kind = "api"
    requires_key = True

    def __init__(self, app_id: str, app_key: str, country: str = "us"):
        self.app_id, self.app_key, self.country = app_id, app_key, country

    def fetch(self):
        out = []
        for q in config.ADZUNA_QUERIES:
            url = (f"https://api.adzuna.com/v1/api/jobs/{self.country}/search/1"
                   f"?app_id={self.app_id}&app_key={self.app_key}"
                   f"&results_per_page=50&what={util.urllib.parse.quote(q)}")
            try:
                data = util.fetch_json(url, timeout=20, retries=1)
            except Exception as e:
                log.info("Adzuna query %s failed: %s", q, e)
                continue
            for item in data.get("results", []):
                sal = None
                if item.get("salary_min") or item.get("salary_max"):
                    sal = salary_mod.build_salary(
                        item.get("salary_min"), item.get("salary_max"),
                        "USD" if self.country == "us" else "GBP", "year")
                out.append({
                    "source": self.name, "source_id": str(item.get("id")),
                    "title": item.get("title"),
                    "company": (item.get("company") or {}).get("display_name"),
                    "location": (item.get("location") or {}).get("display_name") or "",
                    "description_text": item.get("description"),
                    "tags": [(item.get("category") or {}).get("label", "")],
                    "posted_at": item.get("created"),
                    "url": item.get("redirect_url"), "apply_url": item.get("redirect_url"),
                    "remote_flag": None, "salary": sal,
                })
        return out


class JoobleSource(Source):
    name = "jooble"
    platform = "Jooble"
    label = "Jooble"
    kind = "api"
    requires_key = True

    def __init__(self, api_key: str):
        self.key = api_key

    def fetch(self):
        out = []
        for q in config.JOOBLE_QUERIES:
            try:
                data = util.post_json(f"https://jooble.org/api/{self.key}",
                                      {"keywords": q, "page": "1"},
                                      timeout=20, retries=1)
            except Exception as e:
                log.info("Jooble query %s failed: %s", q, e)
                continue
            for item in data.get("jobs", [])[:40]:
                out.append({
                    "source": self.name, "source_id": str(item.get("id") or item.get("link")),
                    "title": item.get("title"), "company": item.get("company") or "Unknown",
                    "location": item.get("location") or "",
                    "description_text": item.get("snippet"),
                    "source_type": item.get("type"),
                    "posted_at": item.get("updated"),
                    "url": item.get("link"), "apply_url": item.get("link"),
                    "salary_hint": item.get("salary") or "",
                    "remote_flag": None,
                })
        return out


class TheMuseSource(Source):
    name = "themuse"
    platform = "The Muse"
    label = "The Muse"
    kind = "api"
    requires_key = True

    def __init__(self, api_key: str):
        self.key = api_key

    def fetch(self):
        out = []
        for page in (1, 2):
            try:
                data = util.fetch_json(
                    f"https://www.themuse.com/api/public/jobs?api_key={self.key}&page={page}",
                    timeout=20, retries=1)
            except Exception as e:
                log.info("The Muse page %s failed: %s", page, e)
                break
            for item in data.get("results", []):
                locs = item.get("locations") or []
                loc = ", ".join(l.get("name", "") for l in locs[:2] if isinstance(l, dict))
                out.append({
                    "source": self.name, "source_id": str(item.get("id")),
                    "title": item.get("name"),
                    "company": (item.get("company") or {}).get("name"),
                    "location": loc,
                    "description_html": item.get("contents"),
                    "tags": [c.get("name", "") for c in (item.get("categories") or [])[:3]
                             if isinstance(c, dict)],
                    "source_type": item.get("type"),
                    "posted_at": item.get("publication_date"),
                    "url": (item.get("refs") or {}).get("landing_page"),
                    "apply_url": (item.get("refs") or {}).get("landing_page"),
                    "remote_flag": True if "remote" in loc.lower() else None,
                })
        return out


# ===========================================================================
# Registry
# ===========================================================================

__all__ = ["Source", "enabled_sources", "available_but_disabled"]


def enabled_sources() -> list[Source]:
    srcs: list[Source] = [
        RemoteOKSource(),
        RemotiveSource(),
        ArbeitnowSource(),
        JobicySource(),
        WeWorkRemotelySource(),
        HackerNewsWhoIsHiring(),
        GitHubInternships(),
    ]
    srcs += [GreenhouseBoard(slug=s) for s in config.GREENHOUSE_BOARDS]
    srcs += [AshbyBoard(slug=s) for s in config.ASHBY_BOARDS]
    srcs += [LeverPostings(slug=s) for s in config.LEVER_COMPANIES]
    srcs += [GenericRSSFeed(f["name"], f["url"]) for f in config.EXTRA_RSS_FEEDS]
    if config.ADZUNA_APP_ID and config.ADZUNA_APP_KEY:
        srcs.append(AdzunaSource(config.ADZUNA_APP_ID, config.ADZUNA_APP_KEY,
                                 config.ADZUNA_COUNTRY))
    if config.JOOBLE_API_KEY:
        srcs.append(JoobleSource(config.JOOBLE_API_KEY))
    if config.THEMUSE_API_KEY:
        srcs.append(TheMuseSource(config.THEMUSE_API_KEY))
    return srcs


def available_but_disabled() -> list[dict]:
    """Sources that activate as soon as credentials/config are provided."""
    out = []
    if not (config.ADZUNA_APP_ID and config.ADZUNA_APP_KEY):
        out.append({"name": "adzuna", "label": "Adzuna",
                    "reason": "set ADZUNA_APP_ID / ADZUNA_APP_KEY"})
    if not config.JOOBLE_API_KEY:
        out.append({"name": "jooble", "label": "Jooble",
                    "reason": "set JOOBLE_API_KEY"})
    if not config.THEMUSE_API_KEY:
        out.append({"name": "themuse", "label": "The Muse",
                    "reason": "set THEMUSE_API_KEY"})
    out.append({"name": "linkedin", "label": "LinkedIn / Indeed",
                "reason": "no public API — ingested indirectly via company ATS boards "
                          "(Greenhouse/Ashby/Lever) which carry the identical postings"})
    return out
