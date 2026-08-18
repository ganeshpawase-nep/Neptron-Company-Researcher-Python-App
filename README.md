
# Company Research Automation - Phase 4.3 Production

## Purpose

Batch company research for 100+ or 1000+ companies using deterministic
browser automation.

Research sources are restricted to:

1. DuckDuckGo search results
2. The company's own website
3. LinkedIn company page

Other social media and third-party directories are ignored.

## Production batch behavior

For each company:

1. Search DuckDuckGo.
2. Identify company website candidates.
3. Verify the strongest website candidates.
4. Open the selected company website.
5. Extract About, Industry, Sector, Revenue, Established Year,
   Employees, Email, Phone and Address.
6. Open same-domain About/Contact pages.
7. Open LinkedIn when available.
8. Build the result.
9. **Immediately append and save the result to Excel.**
10. **Close every company-specific browser tab.**
11. Start the next company.

The output Excel is therefore a checkpoint file, not a final-only export.

## Resume support

If `Resume from existing output Excel` is enabled, companies already present
in the output are skipped.

This means an interrupted 1000-company run can be restarted without repeating
companies that were already saved.

A company that failed is stored with:

`Status = RESEARCH_ERROR`

and can be identified for later retry.

## Browser lifecycle

One reusable main Chrome tab remains open while the batch runs.

Company-specific tabs are always closed after each company.

At the end:

- production default: browser closes
- optional "Keep main browser open after the entire batch": browser remains open

Use a dedicated persistent Chrome profile for LinkedIn login.

Do not use a Chrome profile that is currently open by another Chrome process.

## Incremental Excel

The workbook is saved after every company.

If the process crashes after company 437, rows 1-437 are already on disk.

The next run can resume from the existing output.

## Windows setup

If PowerShell activation is blocked by Group Policy, use:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
copy .env.example .env
.\.venv\Scripts\python.exe main.py
```


## Important search behavior - exact company name

The primary DuckDuckGo query is the company name exactly as supplied in the
input Excel.

Example input:

`D.G.REAL ESTATE CONSULTANTS PVT.LTD.`

Primary search:

`D.G.REAL ESTATE CONSULTANTS PVT.LTD.`

The application does NOT turn this into:

`site:linkedin.com/company "D.G.REAL ESTATE CONSULTANTS PVT.LTD."`

and it does NOT normalize, remove punctuation, abbreviate, or rewrite the
company name for the primary search.

Additional queries are based on the same original string:

`D.G.REAL ESTATE CONSULTANTS PVT.LTD. official website`

`D.G.REAL ESTATE CONSULTANTS PVT.LTD. contact`

`D.G.REAL ESTATE CONSULTANTS PVT.LTD. LinkedIn`

`D.G.REAL ESTATE CONSULTANTS PVT.LTD. about`

`D.G.REAL ESTATE CONSULTANTS PVT.LTD. company`

This is intentional because punctuated Indian company names can produce better
DuckDuckGo results when searched exactly as entered by the user.

LinkedIn is identified from the normal company search results instead of using
a restrictive `site:linkedin.com` query.


## Research workflow rule

The DuckDuckGo stage performs **one search only**:

`<exact company name from Excel>`

No `official website`, `contact`, `about`, `site:linkedin.com`, or other
modified company-name searches are used.

After search results are returned:

1. classify only company website and LinkedIn candidates;
2. select/verify the best company website;
3. research About, Contact, Footer, Industry, Sector, Revenue, Founded,
   Employees, email, phone and address **inside that website**;
4. open LinkedIn only when a company LinkedIn URL was found;
5. extract available LinkedIn company information;
6. save the result;
7. close all company-specific browser tabs;
8. continue to the next company.

## Long-batch stability

A navigation failure such as:

`Execution context was destroyed`

is treated as a candidate-level failure. The candidate is skipped or retried,
rather than terminating the entire company batch.

Each company is also wrapped by the batch-level error handler. Therefore a
problem with one company should result in a `RESEARCH_ERROR` row and processing
continues with the next company.


## Industry/Sector priority

Industry and Sector are high-priority fields.

For each company:

1. The exact Excel company name is searched in DuckDuckGo.
2. If a LinkedIn company page is returned, it is opened using the persistent
   browser profile. If the user has completed the one-time manual LinkedIn
   login, the application can read the public/company information available
   in that logged-in session.
3. LinkedIn Industry is preferred when available.
4. LinkedIn company size, founded year and About are also extracted when
   available.
5. If Industry/Sector is missing after LinkedIn, DuckDuckGo Search Assist is
   used with the company-profile query:

   `<company name> company profile industry, sector, address, employees count,
   founded, contact email, website, LinkedIn, Contact number`

Google is not used anywhere in the application.

## LinkedIn login

Click `Setup LinkedIn Login (One-Time)` before the first production batch.

A dedicated persistent Chrome profile opens LinkedIn. Log in manually and
complete any verification. The application does not collect the password.
After login, click OK in the application.

The saved browser profile is reused in subsequent runs, so the user normally
does not need to log in again unless LinkedIn expires the session.

## No AI

The tool remains deterministic and rule-based. It uses Playwright/browser
automation, DuckDuckGo discovery, website parsing, LinkedIn page parsing, and
DuckDuckGo Search Assist for company-profile enrichment. No AI/LLM API is used.


## Search engine policy

All search-engine queries use DuckDuckGo.

Company discovery:
`<exact company name from Excel>`

Industry/Sector and profile enrichment:
`<company name> company profile industry, sector, address, employees count, founded, contact email, website, LinkedIn, Contact number`

Google and Bing are not used. The HTML DuckDuckGo endpoint is used for normal
result discovery to reduce CAPTCHA/security interruptions, while the normal
DuckDuckGo web page is used for the Search Assist browser interaction.

Industry/Sector is a strict structured-field extraction task. The short Search
Assist summary is never copied into the Industry or Sector columns. The expanded
Search Assist Company Profile is opened through `More`, and only explicit
`Industry:` / `Sector:` values are accepted.


## Industry/Sector — mandatory behavior

Whenever Industry OR Sector is empty after website/LinkedIn extraction, the
application MUST execute this exact DuckDuckGo company-profile query using the
company name exactly as supplied:

    <COMPANY NAME> company profile industry, sector, address, employees count,
    founded, contact email, website, LinkedIn, Contact number

Search Assist interaction:

1. Open the exact DuckDuckGo query.
2. Click `Search Assist`.
3. Wait exactly 15 seconds.
4. Wait for the `More` button and click it.
5. Wait a random 15-25 seconds.
6. Poll until the expanded Company Profile is stable.
7. Extract the explicit `Industry:` and `Sector:` values from that expanded
   profile.
8. Also use the expanded profile for missing founded year, employees, address,
   email, website, LinkedIn and contact number.
9. If Search Assist is empty, More is unavailable, the page is stale, or a
   browser target closes, reload/recreate the tab and repeat the complete flow.

The summary paragraph is never used as a structured Industry/Sector value.
This prevents incorrect values such as `transformer manufacturing industry under
the electrical equipment` from being written into the Sector column.

The application uses up to three recovery attempts for a company.

Environment variables:
- `SEARCH_ASSIST_OPEN_WAIT_MS=15000`
- `SEARCH_ASSIST_MAX_WAIT_MS=90000`
- `SEARCH_ASSIST_EXPANDED_WAIT_MS=120000`
- `INDUSTRY_SECTOR_TIMEOUT_MS=300000`


## Phase 4.15 – Strict DuckDuckGo Search Assist

The company-profile enrichment workflow now follows the exact browser sequence
required for reliable structured extraction:

1. Open the exact company-profile query on DuckDuckGo.
2. Click `Search Assist`.
3. Wait exactly 15 seconds.
4. Wait for `More` and click it.
5. Wait a random 15-25 seconds.
6. Poll the expanded Search Assist card until its content is stable.
7. Extract only explicit labeled fields from the expanded Company Profile.
8. Never use the short Search Assist summary sentence as an Industry or Sector
   value.
9. If the Search Assist page is empty, stale, the More button disappears, or
   the browser target closes, reload/recreate the tab and repeat the complete
   sequence.

Industry/Sector parsing is deliberately label-driven. For example:

    Industry: Transformer Manufacturing
    Sector: Electrical Equipment

is written to Excel exactly as:

    Industry = Transformer Manufacturing
    Sector   = Electrical Equipment

A sentence such as `transformer manufacturing industry under the electrical
equipment sector` is never accepted as a structured field.

The same expanded profile is used as a fallback for missing founded year,
employee count, address, contact email, website, LinkedIn and contact number.
Website and LinkedIn data already verified by the company researcher remain the
preferred sources.

The normal result-discovery query uses DuckDuckGo's HTML endpoint to reduce
CAPTCHA/security interruptions. Search Assist uses the normal DuckDuckGo web UI.
No Google or Bing requests are made by the application.

## Phase 4.16 Search Assist fix

The Search Assist browser automation now keeps the opened Search Assist card active, waits 15 seconds, handles both direct `More` and the down-arrow → `More` layout, waits a random 15–25 seconds after `More`, and extracts from the expanded `Company Profile / General Information` section only. If Search Assist is not available for a query, that step is skipped and the normal research workflow continues.
