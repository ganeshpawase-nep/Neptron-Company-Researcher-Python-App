import asyncio
import json
import re
from urllib.parse import quote_plus, urljoin

from models.company import CompanyResearchResult
from company.normalizer import normalize_text, tokens
from company.scoring import score_candidate, source_kind, host, root, is_linkedin
from search.search_engine import DuckDuckGoSearch

EMAIL_RE = re.compile(r"\b[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+\b")
PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d{1,3}[\s().-]*)?(?:\d[\s().-]*){8,14}\d(?!\d)")


class Researcher:
    """Production company researcher.

    Discovery policy:
      1. Main discovery uses the exact company name from Excel only.
      2. After a company website is selected, website pages are crawled for
         About/Contact/company data.
      3. LinkedIn is used only when its company URL was returned by the exact
         company-name search.
      4. If Industry/Sector is missing, a DuckDuckGo company-profile query
         is executed and its Search Assist answer is explicitly opened,
         expanded and parsed.

    Search Assist is used only as a browser feature; no AI/LLM API is
    integrated into this application.
    """

    def __init__(self, context, settings, log=print):
        self.context, self.settings, self.log = context, settings, log

    async def _goto(self, page, url, attempts=None):
        attempts = attempts or self.settings.candidate_navigation_attempts
        last = None
        for attempt in range(attempts):
            try:
                if page.is_closed():
                    page = await self.context.new_page()
                    page.set_default_timeout(self.settings.browser_timeout_ms)

                await page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=self.settings.browser_timeout_ms,
                )
                await page.wait_for_timeout(250)
                return True, page
            except Exception as exc:
                last = exc
                if attempt < attempts - 1:
                    try:
                        await asyncio.wait_for(
                            page.wait_for_timeout(500 * (attempt + 1)),
                            timeout=2,
                        )
                    except Exception:
                        pass

        self.log(
            f"         navigation skipped: {type(last).__name__}: "
            f"{str(last)[:180]}"
        )
        return False, page

    async def _close_page(self, page):
        try:
            if page and not page.is_closed():
                await asyncio.wait_for(page.close(), timeout=5)
        except Exception:
            pass

    async def enrich_industry_sector(self, company_name):
        """
        Dedicated Industry/Sector research using the expanded DuckDuckGo
        Search Assist company profile.

        Only explicit labeled values are accepted:
            Industry: <value>
            Sector: <value>

        The natural-language Search Assist summary is deliberately ignored for
        Industry/Sector because it can produce values such as:
            "transformer manufacturing industry under the electrical equipment"
        instead of the actual structured fields.
        """
        query = (
            str(company_name).strip()
            + " company profile industry, sector, address, employees count, "
            "founded, contact email, website, LinkedIn, Contact number"
        )

        # This method is kept for compatibility with earlier callers. It uses
        # the same strict Search Assist path as the main researcher.
        page = None
        try:
            page = await self.context.new_page()
            ddg = DuckDuckGoSearch(page, self.settings, self.log)
            candidates, assist = await ddg.search_query_with_assist(
                query,
                timeout_ms=max(
                    self.settings.industry_search_timeout_ms,
                    30000,
                ),
            )
        except Exception as exc:
            self.log(
                f"         Industry/Sector Search Assist warning: "
                f"{type(exc).__name__}: {str(exc)[:180]}"
            )
            return "", "", []
        finally:
            await self._close_page(page)

        fields = self._parse_search_assist_profile(assist)
        industry = fields.get("industry", "")
        sector = fields.get("sector", "")

        if not self._valid_classification(industry, "industry"):
            industry = ""
        if not self._valid_classification(sector, "sector"):
            sector = ""

        self.log(f"         Industry: {industry or '-'}")
        self.log(f"         Sector: {sector or '-'}")
        return industry, sector, candidates

    @staticmethod
    def _clean_profile_value(value):
        value = re.sub(r"\s+", " ", str(value or "")).strip()
        value = value.strip(" -:;,.|•")
        return value

    def _parse_search_assist_profile(self, text):
        """Parse structured fields from the expanded Search Assist card.

        This parser is intentionally label-driven. It never derives Industry or
        Sector from prose. That is the key protection against the bad Excel value
        seen previously (for example, using the whole summary sentence as Sector).
        """
        result = {
            "industry": "",
            "sector": "",
            "year": "",
            "employees": "",
            "address": "",
            "email": "",
            "website": "",
            "linkedin": "",
            "phone": "",
            "about": "",
        }

        if not text:
            return result

        raw = str(text).replace("\r\n", "\n").replace("\r", "\n")
        lines = [
            re.sub(r"^[\s•·▪◦*-]+", "", line).strip()
            for line in raw.split("\n")
        ]
        lines = [line for line in lines if line]

        labels = (
            "industry", "sector", "founded", "founded year",
            "established", "incorporated", "employees", "employees count",
            "employee count", "headcount", "head count", "company size",
            "headquarters", "indian address", "address", "contact number",
            "phone", "mobile", "email", "website", "linkedin",
        )
        label_alt = "|".join(re.escape(x) for x in labels)

        def assign(label, value):
            value = self._clean_profile_value(value)
            if not value:
                return
            key = label.casefold()
            if key in {"founded", "founded year", "established", "incorporated"}:
                m = re.search(r"\b(?:19|20)\d{2}\b", value)
                if m and not result["year"]:
                    result["year"] = m.group(0)
                return
            if key in {"employees", "employees count", "employee count", "headcount", "head count", "company size"}:
                if not result["employees"]:
                    result["employees"] = value
                return
            mapping = {
                "industry": "industry",
                "sector": "sector",
                "headquarters": "address",
                "indian address": "address",
                "address": "address",
                "contact number": "phone",
                "phone": "phone",
                "mobile": "phone",
                "email": "email",
                "website": "website",
                "linkedin": "linkedin",
            }
            target = mapping.get(key)
            if target and not result[target]:
                result[target] = value

        # Parse inline labeled fields first. DDG sometimes renders the whole
        # profile as one DOM text node, e.g.
        # ``Industry: X Sector: Y Founded: 1984 Employees: 51-200``.
        # This must run before the one-field-per-line parser so a multi-field
        # line is not accidentally captured as one giant Industry value.
        inline_re = re.compile(
            rf"(?P<label>{label_alt})\s*:\s*(?P<value>.*?)(?=\s+(?:{label_alt})\s*:|$)",
            re.I | re.S,
        )
        for line in lines:
            for m in inline_re.finditer(line):
                assign(m.group("label"), m.group("value"))

        # Preferred form: one field per line, exactly as shown in DDG's
        # expanded Company Profile section.
        line_re = re.compile(
            rf"^(?P<label>{label_alt})\s*:\s*(?P<value>.+?)\s*$",
            re.I,
        )
        for line in lines:
            m = line_re.match(line)
            if m:
                assign(m.group("label"), m.group("value"))

        # Rare layout: label on one line and value on the next.
        label_only = re.compile(rf"^(?P<label>{label_alt})\s*$", re.I)
        for i, line in enumerate(lines[:-1]):
            m = label_only.match(line)
            if m:
                nxt = lines[i + 1]
                if not re.match(rf"^(?:{label_alt})\s*:", nxt, re.I):
                    assign(m.group("label"), nxt)

        # Emails/phones are extracted from the expanded profile as fallback
        # values only. The website/LinkedIn/company-page sources remain primary.
        if not result["email"]:
            m = EMAIL_RE.search(raw)
            if m:
                result["email"] = m.group(0)
        if not result["phone"]:
            m = PHONE_RE.search(raw)
            if m:
                result["phone"] = m.group(0)

        # Do not let a summary sentence leak into a structured field. The value
        # must be a direct labeled value and must pass the classification filter.
        if not self._valid_classification(result["industry"], "industry"):
            result["industry"] = ""
        if not self._valid_classification(result["sector"], "sector"):
            result["sector"] = ""

        return result

    def _valid_classification(self, value, kind):
        """
        Validate Industry/Sector values before they reach Excel.

        This prevents common false positives such as:
          - "65 Noida-U"
          - "N/A"
          - postal codes / phone numbers
          - "company size"
          - long navigation/contact text
        """
        value = re.sub(r"\s+", " ", str(value or "")).strip(" .,:;|-")
        if len(value) < 3 or len(value) > 220:
            return False

        low = value.casefold()

        invalid_exact = {
            "industry", "sector", "company", "business",
            "company profile", "business profile", "n/a", "na",
            "not specified", "not available", "unknown", "-",
            "headquarters", "website", "contact", "contact details",
            "company size", "employees",
        }
        if low in invalid_exact:
            return False

        # Values beginning with a number are normally postal/location data
        # rather than a classification.
        if re.match(r"^\d", value):
            return False

        if re.search(
            r"\b(?:headquarters|address|contact|email|website|linkedin|"
            r"company size|employees|founded|established)\b",
            low,
        ):
            return False

        digits = sum(ch.isdigit() for ch in value)
        if digits > max(3, len(value) // 4):
            return False

        # Prevent accidental UI/navigation fragments.
        if any(x in low for x in (
            "noida-u", "mumbai 400", "thane 400", "navi mumbai",
            "view employees", "followers on linkedin",
        )):
            return False

        return True

    def _sanitize_classification(self, d):
        for key, kind in (("industry", "industry"), ("sector", "sector")):
            if d.get(key) and not self._valid_classification(d[key], kind):
                self.log(
                    f"         Rejected invalid {kind} value: {d[key]!r}"
                )
                d[key] = ""

    async def run(self, company, candidates):
        """
        Research order:

        1. Select and verify company website from exact-name DuckDuckGo results.
        2. Extract website About/Contact/Footer/company data.
        3. Open the LinkedIn company page if it was returned by the exact-name
           search. A persistent browser profile allows the user to log in once.
        4. Use LinkedIn Industry/Company Size/Founded/About when available.
        5. If Industry/Sector is still missing (or invalid), run the
           DuckDuckGo company-profile fallback query and explicitly use
           Search Assist + More.
        """
        website, linkedin, score, note = await self.choose(company, candidates)

        if not website:
            return CompanyResearchResult(
                company_name=company,
                linkedin_url=linkedin,
                status="NO_VERIFIED_WEBSITE",
                confidence="LOW",
                score=0,
                notes=note,
            )

        self.log(f"      SELECTED WEBSITE: {website}")
        self.log(f"      SCORE: {score}")

        d = {
            "about": "", "industry": "", "sector": "", "revenue": "",
            "year": "", "employees": "", "emails": [], "phones": [],
            "address": "", "contact": "", "sources": []
        }

        # ---------------- WEBSITE ----------------
        p = await self.context.new_page()
        links = []
        try:
            ok, p = await self._goto(p, website)
            if ok:
                await self.extract_page(p, d, "website")
                links = await self.same_domain_links(p)
        except Exception as e:
            self.log(
                f"      Website extraction warning: "
                f"{type(e).__name__}: {str(e)[:160]}"
            )
        finally:
            await self._close_page(p)

        # Discover About/Contact pages INSIDE the selected company domain.
        likely = [
            ("about", urljoin(website, "/about")),
            ("about", urljoin(website, "/about-us")),
            ("about", urljoin(website, "/company")),
            ("contact", urljoin(website, "/contact")),
            ("contact", urljoin(website, "/contact-us")),
            ("contact", urljoin(website, "/contact.html")),
        ]

        discovered = []
        discovered_seen = set()

        for href, text in links:
            t = normalize_text((text or "") + " " + href)
            typ = (
                "contact"
                if any(
                    x in t
                    for x in ("contact", "enquiry", "inquiry", "get in touch")
                )
                else "about"
                if any(
                    x in t
                    for x in ("about", "who we are", "company profile")
                )
                else ""
            )

            if typ:
                href = urljoin(website, href).rstrip("/")
                if root(href) == root(website) and href not in discovered_seen:
                    discovered_seen.add(href)
                    discovered.append((typ, href))

        unique = []
        used = set()

        for item in discovered + likely:
            typ, href = item
            href = href.rstrip("/")
            if href not in used and root(href) == root(website):
                used.add(href)
                unique.append((typ, href))

        # Keep this bounded. One bad internal route must not hold the batch.
        max_pages = max(2, self.settings.max_internal_pages)

        for typ, href in unique[:max_pages]:
            tab = await self.context.new_page()
            try:
                self.log(f"         Opening {typ} page: {href}")
                ok, tab = await self._goto(tab, href)
                if not ok:
                    continue

                if typ == "contact" and not d["contact"]:
                    d["contact"] = tab.url

                await asyncio.wait_for(
                    self.extract_page(tab, d, typ),
                    timeout=max(8, self.settings.browser_timeout_ms / 1000 + 3),
                )

            except Exception as e:
                self.log(
                    f"         {typ} page warning: "
                    f"{type(e).__name__}: {str(e)[:140]}"
                )
            finally:
                await self._close_page(tab)

        # ---------------- LINKEDIN ----------------
        linkedin_ok = False

        if linkedin:
            self.log(f"      Opening LinkedIn: {linkedin}")

            lp = await self.context.new_page()

            try:
                ok, lp = await self._goto(
                    lp,
                    linkedin,
                    attempts=self.settings.candidate_navigation_attempts,
                )

                if ok:
                    linkedin_ok = True
                    await asyncio.wait_for(
                        self.extract_linkedin_page(lp, d),
                        timeout=15,
                    )

                    self.log(
                        f"         LinkedIn Industry: "
                        f"{d['industry'] or '-'}"
                    )
                    self.log(
                        f"         LinkedIn Sector: "
                        f"{d['sector'] or '-'}"
                    )
                    self.log(
                        f"         LinkedIn Employees: "
                        f"{d['employees'] or '-'}"
                    )

            except Exception as e:
                self.log(
                    f"         LinkedIn warning: "
                    f"{type(e).__name__}: {str(e)[:160]}"
                )
            finally:
                await self._close_page(lp)

        # Never carry a malformed Industry/Sector value into the fallback.
        # Example: LinkedIn pages can contain a location such as "Sector 65
        # Noida-U"; that is NOT a business sector.
        self._sanitize_classification(d)

        # ---------------- DUCKDUCKGO COMPANY-PROFILE ENRICHMENT ----------------
        #
        # IMPORTANT:
        # If Website/LinkedIn did not provide Industry or Sector, use the
        # exact company-profile query requested by the user:
        #
        #   <COMPANY> company profile industry, sector, address,
        #   employees count, founded, contact email, website, LinkedIn,
        #   Contact number
        #
        # This query is executed ONLY on DuckDuckGo.
        #
        # Search Assist interaction:
        #   1. Click Search Assist.
        #   2. Wait 15 seconds.
        #   3. Click More.
        #   4. Wait 15-25 seconds.
        #   5. Extract the expanded profile.
        #
        # If Search Assist says no relevant information, the page is reloaded
        # once and the complete interaction is repeated.
        if not d["industry"] or not d["sector"]:
            self.log(
                "      [Industry/Sector] Mandatory DuckDuckGo company-profile research"
            )

            gp = await self.context.new_page()
            gp.set_default_timeout(self.settings.browser_timeout_ms)

            try:
                ddg = DuckDuckGoSearch(gp, self.settings, self.log)

                profile_query = (
                    str(company).strip()
                    + " company profile industry, sector, address, "
                    "employees count, founded, contact email, website, "
                    "LinkedIn, Contact number"
                )

                self.log(
                    f"      Industry/Sector profile query: {profile_query}"
                )

                try:
                    candidates2, assist = await asyncio.wait_for(
                        ddg.search_query_with_assist(
                            profile_query,
                            timeout_ms=max(
                                self.settings.industry_search_timeout_ms,
                                30000,
                            ),
                        ),
                        timeout=max(
                            60,
                            self.settings.industry_sector_timeout_ms / 1000 + 20,
                        ),
                    )
                except asyncio.TimeoutError:
                    self.log(
                        "         Search Assist profile research timed out; "
                        "continuing with website/LinkedIn data."
                    )
                    candidates2, assist = [], ""
                except Exception as exc:
                    self.log(
                        "         Search Assist profile research warning: "
                        f"{type(exc).__name__}: {str(exc)[:180]}"
                    )
                    candidates2, assist = [], ""

                evidence_texts = []

                # Search Assist expanded profile is the authoritative DDG
                # enrichment source for structured profile fields. Normal result
                # snippets remain secondary corroboration only.
                if assist:
                    evidence_texts.append(("Search Assist", assist))

                for row in candidates2[:15]:
                    title = str(getattr(row, "title", "") or "").strip()
                    snippet = str(getattr(row, "snippet", "") or "").strip()
                    if title or snippet:
                        evidence_texts.append((
                            "DDG result",
                            re.sub(r"\s+", " ", f"{title} {snippet}").strip(),
                        ))

                self.log(
                    f"      Search Assist evidence blocks: {len(evidence_texts)}"
                )

                # ---------------------------------------------------------
                # STRICT Search Assist parsing
                # ---------------------------------------------------------
                if assist:
                    profile = self._parse_search_assist_profile(assist)

                    # Industry and Sector are accepted ONLY from explicit
                    # labeled fields in the expanded Search Assist profile.
                    # Never parse prose such as:
                    #   "transformer manufacturing industry under the
                    #    electrical equipment sector"
                    if not d["industry"] and profile["industry"]:
                        d["industry"] = profile["industry"]
                        d["sources"].append(
                            "industry:duckduckgo:search-assist-expanded"
                        )

                    if not d["sector"] and profile["sector"]:
                        d["sector"] = profile["sector"]
                        d["sources"].append(
                            "sector:duckduckgo:search-assist-expanded"
                        )

                    # Additional company-profile fields from the expanded
                    # section are used only when Website/LinkedIn did not
                    # already provide them.
                    if not d["year"] and profile["year"]:
                        d["year"] = profile["year"]
                        d["sources"].append("year:duckduckgo:search-assist-expanded")

                    if not d["employees"] and profile["employees"]:
                        d["employees"] = profile["employees"][:120]
                        d["sources"].append("employees:duckduckgo:search-assist-expanded")

                    if not d["address"] and profile["address"]:
                        d["address"] = profile["address"][:500]
                        d["sources"].append("address:duckduckgo:search-assist-expanded")

                    if not d["emails"] and profile["email"]:
                        for email in EMAIL_RE.findall(profile["email"]):
                            self.add(
                                d, "emails", email.lower().rstrip(".,;"),
                                "email:duckduckgo:search-assist-expanded",
                            )

                    if not d["phones"] and profile["phone"]:
                        self.add_phone(
                            d, profile["phone"],
                            "phone:duckduckgo:search-assist-expanded",
                        )

                    # Search Assist website/LinkedIn are fallback references,
                    # never replacements for a verified company URL.
                    if not linkedin and profile["linkedin"]:
                        linkedin = profile["linkedin"].rstrip(".,);")
                    if not d.get("website_fallback") and profile["website"]:
                        d["website_fallback"] = profile["website"].rstrip(".,);")

                    if not d["about"]:
                        m = re.search(
                            r"(?is)(?:company profile|company overview)\s*(?:of\s+[^.\n]+)?"
                            r"\s*(.{100,1800}?)(?=\n\s*(?:general information|industry|sector|contact details)\b)",
                            assist,
                        )
                        if m:
                            about = re.sub(r"\s+", " ", m.group(1)).strip()
                            if len(about) >= 100:
                                d["about"] = about[:1800]
                                d["sources"].append(
                                    "about:duckduckgo:search-assist-expanded"
                                )

                # Explicitly labeled fields in normal DDG snippets can be used
                # only as a last-resort fallback when Search Assist did not
                # provide the field. Natural-language inference is prohibited.
                for source, evidence in evidence_texts:
                    if source == "Search Assist":
                        continue

                    if not d["industry"]:
                        m = re.search(
                            r"(?im)^\s*(?:[-•]\s*)?Industry\s*:\s*(.+?)\s*$",
                            evidence,
                        )
                        if m:
                            candidate = self._clean_profile_value(m.group(1))
                            if self._valid_classification(candidate, "industry"):
                                d["industry"] = candidate
                                d["sources"].append("industry:duckduckgo:explicit-snippet")

                    if not d["sector"]:
                        m = re.search(
                            r"(?im)^\s*(?:[-•]\s*)?Sector\s*:\s*(.+?)\s*$",
                            evidence,
                        )
                        if m:
                            candidate = self._clean_profile_value(m.group(1))
                            if self._valid_classification(candidate, "sector"):
                                d["sector"] = candidate
                                d["sources"].append("sector:duckduckgo:explicit-snippet")

                    if not d["year"]:
                        m = re.search(
                            r"\b(?:founded|established|incorporated)(?:\s+year)?\s*[:\-]\s*((?:19|20)\d{2})\b",
                            evidence, re.I,
                        )
                        if m:
                            d["year"] = m.group(1)
                            d["sources"].append("year:duckduckgo:explicit-snippet")

                self._sanitize_classification(d)

                self.log(
                    f"         DuckDuckGo Industry: {d['industry'] or 'NOT FOUND'}"
                )
                self.log(
                    f"         DuckDuckGo Sector: {d['sector'] or 'NOT FOUND'}"
                )

                if assist:
                    self.log(
                        "         Search Assist expanded profile was used for "
                        "structured enrichment."
                    )
                else:
                    self.log(
                        "         Search Assist expanded profile was not available."
                    )

                self._sanitize_classification(d)

                self.log(
                    f"         DuckDuckGo Industry: "
                    f"{d['industry'] or 'NOT FOUND'}"
                )
                self.log(
                    f"         DuckDuckGo Sector: "
                    f"{d['sector'] or 'NOT FOUND'}"
                )

                if assist:
                    self.log(
                        "         Search Assist expanded profile was used."
                    )
                else:
                    self.log(
                        "         Search Assist did not provide a usable answer."
                    )

            except Exception as exc:
                self.log(
                    "         DuckDuckGo company-profile warning: "
                    f"{type(exc).__name__}: {str(exc)[:180]}"
                )
            finally:
                try:
                    if gp and not gp.is_closed():
                        await asyncio.wait_for(gp.close(), timeout=5)
                except Exception:
                    pass

        # Final validation before writing Excel.
        self._sanitize_classification(d)

        conf = "HIGH" if score >= 80 else "MEDIUM" if score >= 55 else "LOW"

        return CompanyResearchResult(
            company_name=company,
            about_us=d["about"],
            industry=d["industry"],
            sector=d["sector"],
            fiscal_revenue=d["revenue"],
            established_year=d["year"],
            employees=d["employees"],
            linkedin_url=linkedin or "",
            website=website,
            emails="; ".join(d["emails"]),
            phones="; ".join(d["phones"]),
            address=d["address"],
            contact_page=d["contact"],
            status="VERIFIED",
            confidence=conf,
            score=score,
            sources="; ".join(dict.fromkeys(d["sources"])),
            notes=(
                "DuckDuckGo exact-name discovery. Website is the primary "
                "company source. LinkedIn is used when a company page is "
                "returned by the exact-name search and the persistent browser "
                "profile can be logged in manually. Industry/Sector prefers "
                "LinkedIn and falls back to the exact Google query "
                "'<company name> company profile industry, sector, address, employees count, founded, contact email, website, LinkedIn, Contact number'. "
                "No AI/LLM used."
            ),
        )

    async def choose(self, company, candidates):
        websites = []
        linkedin = ""
        self.log("\n      Candidate results:")

        for i, c in enumerate(candidates, 1):
            k = source_kind(c.url)
            if k == "linkedin":
                if not linkedin and "/company/" in c.url:
                    linkedin = c.url
                self.log(
                    f"      {i}. {c.title[:110]}\n         {c.url}\n         [LinkedIn - retained]"
                )
            elif k == "ignored":
                self.log(f"      {i}. {c.title[:110]}\n         {c.url}\n         [ignored]")
            else:
                score_candidate(company, c)
                websites.append(c)
                self.log(
                    f"      {i}. {c.title[:110]}\n         {c.url}\n"
                    f"         [company website candidate] search score={c.score}"
                )
                if c.snippet:
                    self.log(f"         {c.snippet[:220]}")

        # One candidate per domain. Do not require a high search score.
        by = {}
        for c in websites:
            by.setdefault(root(c.url), []).append(c)

        top = []
        for items in by.values():
            items.sort(key=lambda x: x.score, reverse=True)
            top.append(items[0])
        top.sort(key=lambda x: x.score, reverse=True)
        top = top[: self.settings.max_website_domains]

        self.log(
            f"\n      Opening {len(top)} company website domain(s) for verification/extraction."
        )

        verified = []
        for c in top:
            p = await self.context.new_page()
            try:
                self.log(
                    f"      Opening website candidate: {c.url} | search score={c.score}"
                )
                ok, p = await self._goto(p, c.url)
                if not ok:
                    continue
                title = await asyncio.wait_for(p.title(), timeout=5)
                text = await self.body(p)
                vs, evidence = self.page_score(company, c.url, text, title)
                self.log(f"         company match score={vs} | {evidence}")
                if vs >= 30:
                    verified.append((vs, c))
            except Exception as e:
                self.log(
                    f"         verification warning: {type(e).__name__}: {str(e)[:150]}"
                )
            finally:
                await self._close_page(p)

        if not verified:
            return (
                None,
                linkedin,
                0,
                "Search results were found, but no website contained sufficient company-name evidence.",
            )

        verified.sort(key=lambda x: x[0], reverse=True)
        vs, c = verified[0]
        return c.url, linkedin, vs, "Selected by actual company-name/domain/content match."

    def page_score(self, company, url, text, title):
        n = normalize_text(company)
        tn = normalize_text(title)
        blob = normalize_text(title + " " + text[:60000])
        ts = tokens(company)
        score = 0
        ev = []
        if n and n in tn:
            score += 55
            ev.append("exact company name in title")
        hits = sum(1 for t in ts if re.search(r"\b" + re.escape(t) + r"\b", blob))
        if ts and hits == len(ts):
            score += 30
            ev.append(f"all name tokens in content {hits}/{len(ts)}")
        elif ts and hits >= max(1, len(ts) // 2):
            score += 18
            ev.append(f"partial name tokens {hits}/{len(ts)}")
        h = normalize_text(host(url))
        dh = sum(1 for t in ts if len(t) >= 3 and t in h)
        if dh:
            score += 25
            ev.append(f"name token in domain {dh}")
        if EMAIL_RE.search(text):
            score += 3
        if PHONE_RE.search(text):
            score += 3
        return min(score, 100), "; ".join(ev) or "weak content evidence"

    def extract_labeled(self, text, d, source):
        patterns = {
            "industry": r"(?i)(?:industry|industries)\s*[:\-]\s*([^|\n.;]{2,120})",
            "sector": r"(?i)sector\s*[:\-]\s*([^|\n.;]{2,120})",
            "revenue": r"(?i)(?:fiscal revenue|annual revenue|revenue)\s*[:\-]\s*([^|\n.;]{2,120})",
            "year": r"(?i)(?:established|founded|incorporated|year founded)\s*(?:in|year|:|-)\s*((?:19|20)\d{2})",
            "employees": r"(?i)(?:employees|company size|employee count|team size)\s*[:\-]?\s*([^|\n.;]{1,100})",
        }
        for f, p in patterns.items():
            m = re.search(p, text)
            if m and not d[f]:
                v = re.sub(r"\s+", " ", m.group(1)).strip()
                if v:
                    d[f] = v
                    d["sources"].append(f"{f}:{source}")

    def extract_search_evidence(self, company, candidates, d):
        # Kept for compatibility; discovery snippets are weak evidence and are
        # not used to invent About/Industry/Sector values.
        for c in candidates:
            blob = (c.title + " " + c.snippet).strip()
            if blob:
                self.extract_labeled(blob, d, "DuckDuckGo snippet")

    async def extract_page(self, p, d, source):
        text = await self.body(p)
        try:
            metas = await p.locator(
                'meta[name="description"],meta[property="og:description"]'
            ).evaluate_all(
                "els=>els.map(x=>x.content||'').filter(Boolean)"
            )
            for m in metas:
                if len(m.strip()) >= 60 and not d["about"]:
                    d["about"] = re.sub(r"\s+", " ", m).strip()[:1800]
                    d["sources"].append(f"about:{source}:meta")
        except Exception:
            pass

        try:
            sections = await p.locator("section,article,main").evaluate_all(
                """els=>els.map(e=>({t:(e.innerText||'').trim(),h:(e.querySelector('h1,h2,h3,h4')||{}).innerText||''})).filter(x=>x.t)"""
            )
            for s in sections:
                h = normalize_text(s.get("h", ""))
                if not d["about"] and any(
                    x in h for x in (
                        "about us", "about the company", "who we are",
                        "company overview", "about"
                    )
                ):
                    v = re.sub(r"\s+", " ", s["t"]).strip()
                    if len(v) >= 80:
                        d["about"] = v[:1800]
                        d["sources"].append(f"about:{source}:section")
                self.extract_labeled(s["t"], d, source)
        except Exception:
            pass

        self.extract_fields(text, d, source)

        try:
            for e in await p.locator('a[href^="mailto:"]').evaluate_all(
                "els=>els.map(a=>a.href)"
            ):
                self.add(
                    d,
                    "emails",
                    e.split(":", 1)[-1].split("?")[0].lower(),
                    f"email:{source}:mailto",
                )
        except Exception:
            pass

        try:
            for ph in await p.locator('a[href^="tel:"]').evaluate_all(
                "els=>els.map(a=>a.getAttribute('href')||'')"
            ):
                self.add_phone(d, ph.split(":", 1)[-1], f"phone:{source}:tel")
        except Exception:
            pass

        try:
            for footer in await p.locator("footer").all_inner_texts():
                self.extract_fields(footer, d, source + "#footer")
        except Exception:
            pass

        try:
            for raw in await p.locator(
                'script[type="application/ld+json"]'
            ).all_inner_texts():
                try:
                    self.jsonld(json.loads(raw), d, source)
                except Exception:
                    pass
        except Exception:
            pass

    async def extract_linkedin_page(self, p, d):
        text = await self.body(p)
        self.extract_fields(text, d, "linkedin")
        self.extract_labeled(text, d, "LinkedIn")

        lines = [
            re.sub(r"\s+", " ", x).strip()
            for x in text.splitlines() if x.strip()
        ]
        for i, line in enumerate(lines):
            n = normalize_text(line)
            if n == "industry" and i + 1 < len(lines) and not d["industry"]:
                d["industry"] = lines[i + 1]
                d["sources"].append("industry:linkedin")
            elif n in ("company size", "company size:") and i + 1 < len(lines) and not d["employees"]:
                d["employees"] = lines[i + 1]
                d["sources"].append("employees:linkedin")
            elif n == "founded" and i + 1 < len(lines) and not d["year"]:
                m = re.search(r"(19|20)\d{2}", lines[i + 1])
                if m:
                    d["year"] = m.group(0)
                    d["sources"].append("year:linkedin")
            # LinkedIn "Specialties" are NOT the company's sector.
            # Keep them out of the Sector field to avoid false classifications.

        about = self.linkedin_about(text)
        if about and not d["about"]:
            d["about"] = about
            d["sources"].append("about:linkedin")

    def linkedin_about(self, text):
        patterns = [
            r"(?is)(?:About|Overview)\s*\n(.{60,1800}?)(?=\n\s*(?:Website|Industry|Company size|Headquarters|Founded|Specialties)\b)",
            r"(?is)(?:About|Overview)\s+(.{60,1800}?)(?=\s+(?:Website|Industry|Company size|Headquarters|Founded|Specialties)\b)",
        ]
        for p in patterns:
            m = re.search(p, text)
            if m:
                v = re.sub(r"\s+", " ", m.group(1)).strip()
                if len(v) >= 60:
                    return v[:1800]
        return ""

    def extract_fields(self, text, d, source):
        for e in EMAIL_RE.findall(text):
            self.add(d, "emails", e.lower().rstrip(".,;"), f"email:{source}")
        for ph in PHONE_RE.findall(text):
            self.add_phone(d, ph, f"phone:{source}")
        self.extract_labeled(text, d, source)

        if not d["about"]:
            a = self.about(text)
            if a:
                d["about"] = a
                d["sources"].append(f"about:{source}")

        if not d["address"]:
            for p in [
                r"(?is)(?:address|registered office|corporate office|office address)\s*[:\-]?\s*(.{25,400}?)(?:\n\s*\n|phone|email|contact)",
                r"(?i)(.{20,280}(?:navi mumbai|mumbai|pune|thane|delhi|bangalore|bengaluru|hyderabad|chennai|kolkata).{0,100}(?:\d{6}|india))",
            ]:
                m = re.search(p, text)
                if m:
                    d["address"] = re.sub(r"\s+", " ", m.group(1)).strip()
                    d["sources"].append(f"address:{source}")
                    break

    def about(self, text):
        for p in [
            r"(?is)(?:about us|about the company|who we are|company overview)\s*[:\-]?\s*(.{80,2000}?)(?=\n\s*(?:our mission|our vision|services|products|contact|careers|why us|our values)\b)",
            r"(?is)(?:about us|about the company|who we are|company overview)\s*[:\-]?\s*(.{80,2000})",
        ]:
            m = re.search(p, text)
            if m:
                v = re.sub(r"\s+", " ", m.group(1)).strip()
                if len(v) >= 80:
                    return v[:1800]
        return ""

    def jsonld(self, obj, d, source):
        if isinstance(obj, list):
            for x in obj:
                self.jsonld(x, d, source)
            return
        if not isinstance(obj, dict):
            return
        if not d["industry"] and isinstance(obj.get("industry"), str):
            d["industry"] = obj["industry"]
            d["sources"].append(f"industry:{source}:jsonld")
        if not d["year"] and isinstance(obj.get("foundingDate"), str):
            m = re.search(r"(19\d{2}|20\d{2})", obj["foundingDate"])
            if m:
                d["year"] = m.group(1)
                d["sources"].append(f"year:{source}:jsonld")
        if not d["address"] and isinstance(obj.get("address"), dict):
            a = obj["address"]
            d["address"] = ", ".join(
                str(a.get(k)) for k in (
                    "streetAddress", "addressLocality", "addressRegion",
                    "postalCode", "addressCountry"
                ) if a.get(k)
            )
            if d["address"]:
                d["sources"].append(f"address:{source}:jsonld")
        for v in obj.values():
            if isinstance(v, (dict, list)):
                self.jsonld(v, d, source)

    def add(self, d, key, val, src):
        if val and val not in d[key]:
            d[key].append(val)
            d["sources"].append(src)

    def add_phone(self, d, val, src):
        digits = re.sub(r"\D", "", val)
        existing = {re.sub(r"\D", "", x) for x in d["phones"]}
        if 8 <= len(digits) <= 15 and digits not in existing:
            self.add(d, "phones", re.sub(r"\s+", " ", val).strip(), src)

    async def body(self, p):
        return await asyncio.wait_for(
            p.locator("body").inner_text(timeout=min(self.settings.browser_timeout_ms, 8000)),
            timeout=10,
        )

    async def same_domain_links(self, p):
        cur = root(p.url)
        arr = await asyncio.wait_for(
            p.locator("a").evaluate_all(
                """els=>els.map(a=>({h:a.href||'',t:(a.innerText||a.textContent||'').trim()})).filter(x=>x.h)"""
            ),
            timeout=8,
        )
        out = []
        for x in arr:
            try:
                if root(x["h"]) == cur and x["h"].startswith(("http://", "https://")):
                    out.append((x["h"], x["t"]))
            except Exception:
                pass
        return list(dict.fromkeys(out))
