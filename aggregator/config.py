"""Central configuration for the RoleRadar aggregation engine."""
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.environ.get("ROLERADAR_DB", os.path.join(BASE_DIR, "data", "roleradar.db"))
STATIC_DIR = os.path.join(BASE_DIR, "static")

HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", "8000"))

# Re-crawl all sources every N minutes in the background.
REFRESH_MINUTES = int(os.environ.get("ROLERADAR_REFRESH_MINUTES", "45"))

# A posting is considered likely-outdated after this many days since posting.
STALE_AFTER_DAYS = int(os.environ.get("ROLERADAR_STALE_DAYS", "75"))

HTTP_TIMEOUT = 25            # seconds per request
HTTP_DELAY_PER_HOST = 0.35   # polite delay between hits to the same host
FETCH_WORKERS = 6            # concurrent source fetches

USER_AGENT = (
    "Mozilla/5.0 (compatible; RoleRadarBot/1.0; +https://roleradar.local/bot; "
    "opportunity-aggregator)"
)

# ---------------------------------------------------------------------------
# Source configuration
# ---------------------------------------------------------------------------

WE_WORK_REMOTELY_CATEGORIES = [
    "remote-programming-jobs",
    "remote-devops-sysadmin-jobs",
    "remote-design-jobs",
    "remote-customer-support-jobs",
    "remote-product-jobs",
]

# Public Greenhouse board slugs (company career-page data via their open API).
GREENHOUSE_BOARDS = [
    "stripe", "coinbase", "figma", "datadog", "databricks", "cloudflare",
]

# Public Ashby job-board slugs.
ASHBY_BOARDS = [
    "openai", "perplexity", "notion", "ramp", "cohere",
]

# Public Lever posting slugs. Many companies migrate away from Lever, so the
# adapter is enabled but tolerates 404s silently.
LEVER_COMPANIES = [
    # add slugs here, e.g. "exampleco"
]

# GitHub community lists of internships & new-grad roles (HTML tables in README).
# (name, default job type, candidate raw urls)
GITHUB_INTERNSHIP_REPOS = [
    ("SimplifyJobs / Summer Internships", "internship", [
        "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/README.md",
        "https://raw.githubusercontent.com/pittcsc/Summer2027-Internships/dev/README.md",
    ]),
    ("SimplifyJobs / Off-Season Internships", "internship", [
        "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/README-Off-Season.md",
    ]),
    ("SimplifyJobs / New Grad Positions", "fulltime", [
        "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/README.md",
    ]),
]

# Optional API-key integrations. Leave the env var unset and they stay disabled.
ADZUNA_APP_ID = os.environ.get("ADZUNA_APP_ID", "")
ADZUNA_APP_KEY = os.environ.get("ADZUNA_APP_KEY", "")
ADZUNA_COUNTRY = os.environ.get("ADZUNA_COUNTRY", "us")
JOOBLE_API_KEY = os.environ.get("JOOBLE_API_KEY", "")
THEMUSE_API_KEY = os.environ.get("THEMUSE_API_KEY", "")

# Adzuna keyword sweeps keep the free-tier calls bounded.
ADZUNA_QUERIES = ["software engineer", "data scientist", "internship", "designer", "marketing"]
JOOBLE_QUERIES = ["software engineer", "internship", "designer", "analyst"]

# Extra arbitrary RSS/Atom feeds can be dropped in here.
EXTRA_RSS_FEEDS = [
    # {"name": "Example", "url": "https://example.com/jobs.rss"},
]

# Rough FX rates to USD, only used to make salaries sortable/filterable.
FX_TO_USD = {
    "USD": 1.0, "EUR": 1.08, "GBP": 1.27, "INR": 0.0119, "CAD": 0.72,
    "AUD": 0.65, "SGD": 0.74, "JPY": 0.0067, "CHF": 1.12, "NZD": 0.59,
    "BRL": 0.18, "PLN": 0.25, "SEK": 0.095, "NOK": 0.093, "DKK": 0.145,
    "ZAR": 0.055, "AED": 0.27, "IDR": 0.000061, "PHP": 0.0176,
}
