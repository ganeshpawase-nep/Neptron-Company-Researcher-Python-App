
from dataclasses import dataclass, field
from typing import List

@dataclass
class SearchCandidate:
    url: str
    title: str
    snippet: str
    query: str
    source: str = "DuckDuckGo"
    score: int = 0
    kind: str = ""
    evidence: List[str] = field(default_factory=list)

@dataclass
class CompanyResearchResult:
    company_name: str
    about_us: str = ""
    industry: str = ""
    sector: str = ""
    fiscal_revenue: str = ""
    established_year: str = ""
    employees: str = ""
    linkedin_url: str = ""
    website: str = ""
    emails: str = ""
    phones: str = ""
    address: str = ""
    contact_page: str = ""
    status: str = ""
    confidence: str = ""
    score: int = 0
    sources: str = ""
    notes: str = ""
