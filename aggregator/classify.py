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


_SKILLS = [
    "python", "java", "javascript", "typescript", "react", "nextjs", "next.js", "vue", "angular",
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
