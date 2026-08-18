
import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

def b(v, default=False):
    if v is None:
        return default
    return str(v).strip().lower() in {"1","true","yes","y","on"}

@dataclass(frozen=True)
class Settings:
    browser_channel: str = os.getenv("BROWSER_CHANNEL", "chrome")
    browser_timeout_ms: int = int(os.getenv("BROWSER_TIMEOUT_MS", "12000"))
    search_timeout_ms: int = int(os.getenv("SEARCH_TIMEOUT_MS", "12000"))
    max_search_results: int = int(os.getenv("MAX_SEARCH_RESULTS", "15"))
    max_queries: int = int(os.getenv("MAX_QUERIES", "6"))
    max_website_domains: int = int(os.getenv("MAX_WEBSITE_DOMAINS", "5"))
    max_internal_pages: int = int(os.getenv("MAX_INTERNAL_PAGES", "6"))
    industry_search_results: int = int(os.getenv("INDUSTRY_SEARCH_RESULTS", "8"))
    industry_search_timeout_ms: int = int(os.getenv("INDUSTRY_SEARCH_TIMEOUT_MS", "18000"))
    # Required DDG Search Assist interaction timing.
    search_assist_open_wait_ms: int = int(os.getenv("SEARCH_ASSIST_OPEN_WAIT_MS", "15000"))
    # Adaptive Search Assist waits. These are maximum safety ceilings, not fixed sleeps.
    search_assist_max_wait_ms: int = int(os.getenv("SEARCH_ASSIST_MAX_WAIT_MS", "90000"))
    search_assist_expanded_wait_ms: int = int(os.getenv("SEARCH_ASSIST_EXPANDED_WAIT_MS", "120000"))
    company_timeout_ms: int = int(os.getenv("COMPANY_TIMEOUT_MS", "360000"))
    candidate_navigation_attempts: int = int(os.getenv("CANDIDATE_NAVIGATION_ATTEMPTS", "3"))
    industry_sector_timeout_ms: int = int(os.getenv("INDUSTRY_SECTOR_TIMEOUT_MS", "300000"))
    linkedin_login_enabled: bool = b(os.getenv("LINKEDIN_LOGIN_ENABLED", "true"))

    # Production default: company tabs are closed after every company.
    # This setting now controls only whether the main Chrome browser remains
    # running after the entire batch completes.
    keep_browser_open: bool = b(os.getenv("KEEP_BROWSER_OPEN", "false"))
    use_persistent_profile: bool = b(os.getenv("USE_PERSISTENT_PROFILE", "true"))
    profile_dir: str = os.getenv("PROFILE_DIR", "browser_profile")

settings = Settings()
