"""Opportunity type, seniority, and work-mode inference."""
from __future__ import annotations

import re

_TYPE_RULES: list[tuple[str, str]] = [
    ("internship", r"\b(intern(ship)?s?|interne|trainee|working student|werkstudent|"
                   r"co-?op|summer analyst|summer associate)\b"),
    ("apprenticeship", r"\bapprentice(ship)?\b"),
    ("research", r"\b(post-?doc(toral)?|research (assistant|intern|fellow|associate|scientist intern)|"
                 r"fellowship|phd (position|student|candidate)|doctoral|resident researcher|"
                 r"ai residency|research residency)\b"),
    ("freelance", r"\b(freelance|gig)\b"),
    ("contract", r"\b(contract(or)?|c2c|corp-?to-?corp|1099|fixed[- ]term|temporary|temp\b)"),
    ("parttime", r"\b(part[- ]?time)\b"),
    ("fulltime", r"\b(full[- ]?time|permanent)\b"),
]

_SOURCE_TYPE_MAP = {
    "full_time": "fulltime", "full-time": "fulltime", "fulltime": "fulltime",
    "part_time": "parttime", "part-time": "parttime", "parttime": "parttime",
    "contract": "contract", "contractor": "contract", "temporary": "contract",
    "freelance": "freelance", "internship": "internship", "intern": "internship",
    "volunteer": "gig", "other": "other", "permanent": "fulltime",
    "apprenticeship": "apprenticeship",
}

_LEVEL_RULES: list[tuple[str, str]] = [
    ("leadership", r"\b(chief|cto|ceo|cfo|coo|vp|vice president|head of|director|"
                   r"principal|distinguished|fellow)\b"),
    ("senior", r"\b(senior|sr\.?|lead|staff|iii|iv\b|\blevel 3|l5|l6|expert)\b"),
    ("mid", r"\b(mid[- ]?level|intermediate|ii\b|\blevel 2|l3|l4)\b"),
    ("entry", r"\b(junior|jr\.?|entry[- ]?level|graduate|new grad|associate|"
              r"early career|fresher|0-1 years|1-2 years|i\b)\b"),
]

_REMOTE_RE = re.compile(r"\b(remote|work from (home|anywhere)|wfh|anywhere|distributed|telecommute)\b", re.I)
_HYBRID_RE = re.compile(r"\bhybrid\b", re.I)


def infer_job_type(title: str, tags: list[str], text: str, source_type: str | None) -> str:
    st = (source_type or "").lower().replace(" ", "_")
    mapped = _SOURCE_TYPE_MAP.get(st)
    hay = " ".join([title or "", " ".join(tags or []), (text or "")[:800]]).lower()
    for typ, pat in _TYPE_RULES:
        if re.search(pat, hay):
            # explicit source types win over weak text matches for ft/pt
            if typ in ("fulltime", "parttime") and mapped in ("internship", "contract", "freelance"):
                continue
            return typ
    return mapped or "fulltime"


def infer_level(title: str, job_type: str) -> str:
    if job_type in ("internship", "apprenticeship"):
        return "entry"
    t = " ".join((title or "").lower().replace("-", " ").split())
    tokens = set(re.split(r"[^a-z0-9+#.]+", t))
    for lvl, pat in _LEVEL_RULES:
        if re.search(pat, t):
            # numeric roman numerals handled above; strip 'i' false friend: need word boundary care
            return lvl
    if tokens & {"senior", "sr", "lead", "staff"}:
        return "senior"
    if tokens & {"junior", "jr", "associate"}:
        return "entry"
    return "mid"


def infer_remote_mode(title: str, location: str, tags: list[str], text: str,
                      remote_flag: bool | None) -> str:
    hay = " ".join([title or "", location or "", " ".join(tags or []), (text or "")[:400]])
    if _HYBRID_RE.search(hay):
        return "hybrid"
    if remote_flag is True or _REMOTE_RE.search(hay):
        return "remote"
    if remote_flag is False:
        return "onsite"
    if location:
        return "onsite"
    return "unknown"


_SKILLS = [    "python", "java", "javascript", "typescript", "react", "nextjs", "next.js", "vue", "angular",
    "node", "node.js", "golang", "go", "rust", "c++", "c#", "ruby", "rails", "django", "flask",
    "fastapi", "spring", "kotlin", "swift", "php", "laravel", ".net", "scala",
    "aws", "gcp", "azure", "kubernetes", "k8s", "docker", "terraform", "linux", "ci/cd",
    "sql", "postgres", "postgresql", "mysql", "mongodb", "redis", "elasticsearch", "snowflake",
    "dbt", "airflow", "spark", "kafka", "hadoop", "pandas", "numpy",
    "machine learning", "deep learning", "nlp", "llm", "genai", "ai", "pytorch", "tensorflow",
    "data science", "data engineering", "analytics", "excel", "tableau", "power bi", "looker",
    "figma", "sketch", "photoshop", "illustrator", "ui/ux", "ux research", "product design",
    "product management", "project management", "agile", "scrum", "jira",
    "seo", "sem", "content marketing", "copywriting", "social media", "email marketing",
    "salesforce", "hubspot", "accounting", "finance", "recruiting", "customer success",
    "devops", "sre", "security", "blockchain", "web3", "solidity", "graphql", "rest api",
    "microservices", "embedded", "firmware", "robotics", "ios", "android", "flutter", "react native",
]
_SKILL_RE = re.compile(r"\b(" + "|".join(re.escape(s) for s in sorted(_SKILLS, key=len, reverse=True)) + r")\b", re.I)


def extract_skills(title: str, text: str, limit: int = 8) -> list[str]:
    hay = f"{title or ''} {(text or '')[:6000]}"
    found: list[str] = []
    seen = set()
    for m in _SKILL_RE.finditer(hay):
        s = m.group(1).lower()
        canon = {"node": "node.js"}.get(s, s)
        if canon not in seen:
            seen.add(canon)
            found.append(canon)
        if len(found) >= limit:
            break
    return found


# ---------------------------------------------------------------------------
# Geographic bucket inference (facet-ready country/region standardization)
# ---------------------------------------------------------------------------

GEO_WORLDWIDE_REMOTE = "worldwide_remote"
GEO_US = "us"
GEO_UK = "uk"
GEO_EU = "eu"
GEO_INDIA = "in"
GEO_CANADA = "ca"
GEO_OTHER_REMOTE = "other_remote"
GEO_OTHER = "other"

GEO_CODES = {GEO_WORLDWIDE_REMOTE, GEO_US, GEO_UK, GEO_EU, GEO_INDIA,
             GEO_CANADA, GEO_OTHER_REMOTE, GEO_OTHER}

_WORLDWIDE_RE = re.compile(
    r"\b(anywhere|worldwide|around the world|the world|globally|global(?:ly)?|"
    r"work from anywhere|earth)\b", re.I)

_INDIA_RE = re.compile(
    r"\bindia\b|bengaluru|bangalore|mumbai|new delhi|\bdelhi\b|\bncr\b|hyderabad|"
    r"pune|chennai|gurgaon|gurugram|noida|kolkata|ahmedabad|kochi|coimbatore|"
    r"jaipur|indore|chandigarh|thiruvananthapuram|bhubaneswar|mysore|mysuru", re.I)

_UK_NAME_RE = re.compile(
    r"united kingdom|great britain|\bengland\b|\bscotland\b|\bwales\b|london|"
    r"manchester|edinburgh|glasgow|birmingham|bristol|leeds|cambridge|oxford|"
    r"liverpool|sheffield|newcastle|belfast|cardiff|nottingham", re.I)
_UK_TOKEN_RE = re.compile(r"\b(UK|U\.K\.)\b")

_CANADA_RE = re.compile(
    r"\bcanada\b|toronto|vancouver|montreal|ottawa|calgary|waterloo|edmonton|"
    r"quebec|winnipeg|victoria, bc", re.I)

_EU_RE = re.compile(
    r"\beurope\b|\beuropean\b|\bemea\b|\beu\b|germany|berlin|munich|hamburg|"
    r"cologne|frankfurt|france|paris|lyon|netherlands|amsterdam|rotterdam|"
    r"eindhoven|utrecht|\bspain\b|barcelona|madrid|valencia|portugal|lisbon|"
    r"porto|ireland|dublin|\bcork\b|poland|warsaw|krakow|wroclaw|gdansk|"
    r"czech|prague|brno|austria|vienna|switzerland|zurich|geneva|basel|sweden|"
    r"stockholm|gothenburg|denmark|copenhagen|norway|oslo|finland|helsinki|"
    r"estonia|tallinn|latvia|riga|lithuania|vilnius|hungary|budapest|romania|"
    r"bucharest|bulgaria|sofia|greece|athens|italy|milan|\brome\b|turin|"
    r"belgium|brussels|antwerp|luxembourg|slovakia|bratislava|slovenia|"
    r"ljubljana|croatia|zagreb|\bmalta\b|cyprus", re.I)

_US_NAME_RE = re.compile(
    r"united states|u\.s\.-based|us-based|usa\b|north america")
_US_TOKEN_RE = re.compile(r"\b(US|USA|U\.S\.A?)\b")
_US_CITY_RE = re.compile(
    r"bay area|silicon valley|\bsf\b|\bnyc\b|new york|san francisco|seattle|"
    r"austin|boston|chicago|los angeles|san diego|denver|boulder|atlanta|miami|"
    r"dallas|houston|portland|philadelphia|phoenix|washington|raleigh|nashville|"
    r"salt lake|pittsburgh|minneapolis|detroit|baltimore|san jose|palo alto|"
    r"mountain view|menlo park|sunnyvale|cupertino|redwood city|santa monica|"
    r"irvine|sacramento|kansas city|st\.? louis|columbus|charlotte|indianapolis|"
    r"cincinnati|cleveland|madison|ann arbor|arlington, va|jersey city|brooklyn|"
    r"long island|scottsdale|tempe|durham|somerville")

_US_STATES = set("""AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA
MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV
WI WY DC""".split())
_STATE_TAIL_RE = re.compile(r",\s*([A-Z]{2})\b")


def infer_geo(location: str, remote_mode: str, tags: list[str] | None = None,
              text: str = "") -> str:
    """Map free-text locations onto canonical facet buckets."""
    loc = (location or "").strip()
    low = loc.lower()
    if remote_mode == "remote" and (
            not loc or low in ("remote", "anywhere", "worldwide")
            or _WORLDWIDE_RE.search(low)):
        return GEO_WORLDWIDE_REMOTE

    # primary: the location string; fallback: tags + description head
    hay = loc or " ".join((tags or [])[:6]) + " " + (text or "")[:300]
    hay_low = hay.lower()

    if _INDIA_RE.search(hay_low):
        return GEO_INDIA
    if _UK_NAME_RE.search(hay_low) or _UK_TOKEN_RE.search(hay):
        return GEO_UK
    if _CANADA_RE.search(hay_low):
        return GEO_CANADA
    if _EU_RE.search(hay_low):
        return GEO_EU
    if _US_NAME_RE.search(hay_low) or _US_TOKEN_RE.search(hay):
        return GEO_US
    if any(s in _US_STATES for s in _STATE_TAIL_RE.findall(loc)):
        return GEO_US
    if _US_CITY_RE.search(hay_low):
        return GEO_US
    return GEO_OTHER_REMOTE if remote_mode == "remote" else GEO_OTHER
