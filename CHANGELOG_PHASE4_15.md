# Phase 4.15 – Strict DuckDuckGo Search Assist

Implemented the requested production changes:

- Uses DuckDuckGo for all search operations.
- Normal discovery uses DuckDuckGo HTML results to reduce CAPTCHA/security interruptions.
- Company-profile fallback query is:
  `<COMPANY NAME> company profile industry, sector, address, employees count, founded, contact email, website, LinkedIn, Contact number`
- Search Assist flow:
  1. Click Search Assist.
  2. Wait exactly 15 seconds.
  3. Wait for More and click it.
  4. Wait a random 20–25 seconds.
  5. Poll for stable expanded profile content.
- If Search Assist is empty, More is unavailable, the page becomes stale, or a browser target closes, the DDG tab is reloaded/recreated and the complete Search Assist flow is retried.
- Industry and Sector are extracted only from explicit structured fields in the expanded profile:
  `Industry: ...`
  `Sector: ...`
- The short Search Assist summary is never used as the Industry or Sector field.
- Expanded Search Assist profile can fill missing founded year, employee count, address, email, website, LinkedIn and contact number.
- Website and LinkedIn data already verified by the researcher remain preferred.
- Fixed the LinkedIn one-time setup crash caused by the removed `search_assist_more_wait_ms` setting.
- Added parser tests for exact labeled Industry/Sector extraction and protection against summary-text contamination.
- Removed stale Google references from the project documentation.
