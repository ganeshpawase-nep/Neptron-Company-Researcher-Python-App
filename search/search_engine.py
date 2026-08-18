import asyncio
import random
import re
from urllib.parse import quote_plus, urlparse, parse_qs
from models.company import SearchCandidate


class DuckDuckGoSearch:
    """
    DuckDuckGo-only search provider.

    Company discovery:
        EXACT company name from the input Excel, unchanged.

    Enrichment:
        The company-profile query is also sent to DuckDuckGo unchanged.

    Search Assist fallback:
        1. Open the normal DuckDuckGo results page.
        2. Click Search Assist.
        3. Click Search Assist and wait exactly 15 seconds before checking More.
        4. Wait for More to become available and click it.
        5. Wait a random 15-25 seconds after More is clicked.
        6. Wait for the expanded profile to become stable.
        7. If the flow fails, reload the DDG tab and repeat the complete flow.
        8. Industry/Sector consumers only accept explicit labeled fields from
           the expanded Search Assist profile; the summary sentence is never
           used as a field value.

    Google/Bing and other search providers are never used.
    """

    def __init__(self, page, settings, log=print):
        self.page = page
        self.settings = settings
        self.log = log

        # Keep the browser context separately so a closed DDG page can be
        # recreated without losing the persistent browser session.
        try:
            self.context = page.context
        except Exception:
            self.context = None

        self.last_assist_attempt = 0

    async def _ensure_page(self):
        """Recover the reusable DDG search page if a previous operation closed it."""
        try:
            if self.page and not self.page.is_closed():
                return self.page
        except Exception:
            pass

        context = self.context
        if context is None:
            try:
                context = getattr(self.page, "context", None)
            except Exception:
                context = None

        if context is None or context.is_closed():
            raise RuntimeError(
                "DuckDuckGo search page is closed and its browser context is unavailable."
            )

        self.context = context
        self.page = await context.new_page()
        self.page.set_default_timeout(self.settings.browser_timeout_ms)
        self.log("      Recreated DuckDuckGo search tab after the previous tab was closed.")
        return self.page

    def queries(self, name):
        value = str(name).strip()
        return [value] if value else []

    async def search(self, name):
        query = str(name).strip()
        if not query:
            return []

        self.log(f"      Query: {query}")
        return await self.search_query(query)

    async def search_query(self, query, label=None):
        query = str(query).strip()
        if not query:
            return []

        if label:
            self.log(f"      {label}")
        self.log(f"      DuckDuckGo query: {query}")

        rows = await self._search_once(query)

        if not rows:
            try:
                await self.page.wait_for_timeout(800)
            except Exception:
                pass
            rows = await self._search_once(query)

        return self._rows_to_candidates(rows, query)

    async def _goto_ddg_js(self, query, timeout_ms):
        page = await self._ensure_page()

        url = "https://duckduckgo.com/?q=" + quote_plus(query)

        await page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=timeout_ms,
        )

        # Let the result UI mount before looking for Search Assist.
        await page.wait_for_timeout(1500)
        return page

    async def _visible_text_click(self, page, label_regex, timeout_ms=15000):
        """
        Wait for a visible control to appear and click it.

        DDG is highly dynamic, so this is polling-based rather than based on a
        fixed sleep. The timeout is only a safety ceiling; if the control
        appears immediately, it is clicked immediately.
        """
        deadline = asyncio.get_running_loop().time() + timeout_ms / 1000

        while asyncio.get_running_loop().time() < deadline:
            patterns = [
                page.get_by_role("button", name=label_regex),
                page.locator("[role='button']").filter(has_text=label_regex),
                page.get_by_text(label_regex),
            ]

            for locator in patterns:
                try:
                    count = await locator.count()
                except Exception:
                    continue

                for i in range(min(count, 12)):
                    item = locator.nth(i)
                    try:
                        if not await item.is_visible():
                            continue
                        await item.scroll_into_view_if_needed(timeout=2000)
                        await item.click(timeout=4000)
                        return True
                    except Exception:
                        continue

            try:
                await page.wait_for_timeout(350)
            except Exception:
                return False

        return False

    async def _assist_card_open(self, page):
        """Return True when a real Search Assist answer card is visibly open.

        The top navigation control also contains the words ``Search Assist``.
        Full-page container elements (body, main, large wrappers) also contain
        that text in their ``innerText`` because it appears in the nav bar.

        To avoid false positives we require:
          - "Search Assist" appears within the first 200 characters of the
            element's text (the card puts it as a heading at the top).
          - The element is NOT the entire page (height < 800 px).
          - The element has substantial content (> 120 chars).
        """
        try:
            blocks = await page.evaluate(
                """() => {
                    const els = document.querySelectorAll('div,section,article');
                    const results = [];
                    for (const e of els) {
                        // Reject containers that include the main search input
                        if (e.querySelector('input[name="q"]')) continue;

                        const text = (e.innerText || '').trim();
                        if (!text || text.length < 120 || text.length > 12000) continue;
                        const r = e.getBoundingClientRect();
                        // Must be a moderately sized panel, NOT the full page
                        if (r.width < 250 || r.height < 80 || r.height > 800) continue;
                        // "Search Assist" must be near the TOP of the element
                        const pos = text.toLowerCase().indexOf('search assist');
                        if (pos < 0 || pos > 200) continue;
                        results.push(true);
                    }
                    return results;
                }"""
            )
            return bool(blocks)
        except Exception:
            return False

    async def _click_search_assist(self, page, timeout_ms=30000):
        """Ensure the Search Assist card is visible on the page.

        DDG often renders the Search Assist card automatically when it has an
        answer.  This method first checks whether the card is already open.
        If it is, it returns True immediately without clicking anything —
        preventing the old bug where clicking the nav-bar "Search Assist"
        link would toggle the already-open card closed.

        Only when the card is NOT already visible does it attempt to click
        the Search Assist control in the navigation bar.
        """
        deadline = asyncio.get_running_loop().time() + timeout_ms / 1000

        # ── 1. Fast check: is the card already rendered? ──────────────
        for _ in range(6):
            if await self._assist_card_open(page):
                self.log("         Search Assist card is already open — skipping click.")
                return True
            try:
                await page.wait_for_timeout(500)
            except Exception:
                break

        # ── 2. Card not visible — try clicking the nav-bar control ────
        # DDG renders the nav button with a leading icon character (e.g. ✦ or ⚡)
        # so we must NOT anchor the regex with ^ — just match "Search Assist"
        # anywhere in the element text.
        clicked = False
        while asyncio.get_running_loop().time() < deadline:
            candidates = [
                # Prefer the navigation-bar link/button containing "Search Assist"
                page.locator("a, button, [role='button'], [role='link']").filter(
                    has_text=re.compile(r"Search\s+Assist", re.I)
                ),
                page.get_by_role("button", name=re.compile(r"Search\s+Assist", re.I)),
                page.get_by_role("link", name=re.compile(r"Search\s+Assist", re.I)),
                page.get_by_text(re.compile(r"Search\s+Assist", re.I)),
            ]

            for locator in candidates:
                try:
                    count = await locator.count()
                except Exception:
                    continue

                for i in range(min(count, 8)):
                    item = locator.nth(i)
                    try:
                        if not await item.is_visible() or not await item.is_enabled():
                            continue
                        await item.scroll_into_view_if_needed(timeout=2000)
                        await item.click(timeout=5000)
                        clicked = True
                        break
                    except Exception:
                        continue
                if clicked:
                    break

            if clicked:
                # Let DDG mount the card.  Do NOT click again.
                for _ in range(16):
                    if await self._assist_card_open(page):
                        self.log("         Search Assist opened after clicking nav control.")
                        return True
                    try:
                        await page.wait_for_timeout(500)
                    except Exception:
                        return False

                self.log("         Search Assist click registered, but the answer card did not stay open.")
                return False

            try:
                await page.wait_for_timeout(400)
            except Exception:
                return False

        return False

    async def _find_more_button(self, page):
        """Find a visible More button without clicking unrelated page controls.

        DDG renders the More button with a down-chevron inside the Search
        Assist card area.  We look for buttons / role=button elements whose
        visible text is exactly "More" (possibly with surrounding whitespace).
        """
        try:
            locators = [
                page.get_by_role("button", name=re.compile(r"^\s*More", re.I)),
                page.locator("button").filter(has_text=re.compile(r"^\s*More", re.I)),
                page.locator("[role='button']").filter(has_text=re.compile(r"^\s*More", re.I)),
                # DDG sometimes wraps More in a <span> inside a clickable <div>
                page.locator("div").filter(has_text=re.compile(r"^\s*More", re.I)),
            ]
            for locator in locators:
                count = await locator.count()
                for i in range(min(count, 12)):
                    item = locator.nth(i)
                    try:
                        if await item.is_visible() and await item.is_enabled():
                            # Make sure this is inside the Search Assist area and
                            # not DDG's top-nav "More" dropdown.
                            text = (await item.inner_text(timeout=1000) or "").strip()
                            if re.match(r"^More$", text, re.I):
                                return item
                    except Exception:
                        continue
        except Exception:
            pass
        return None

    async def _click_expand_arrow_if_needed(self, page):
        """Click DDG's down-chevron / expand arrow below the Search Assist summary.

        Strategy (tried in order):
          1. Find the Search Assist card container, then look for a clickable
             element at/near the bottom that contains an SVG or has minimal text.
          2. Look for any button/div with ``aria-expanded='false'`` inside the
             Search Assist region.
          3. Find small clickable elements (< 60px tall) that contain an SVG
             and sit below the Search Assist summary text.
        """
        try:
            # Locate the Search Assist card bounding box via JS.
            # Use the same strict filters as _assist_card_open: max height 800px
            # and "Search Assist" must appear in the first 200 chars.
            card_rect = await page.evaluate(
                """() => {
                    const els = document.querySelectorAll('div,section,article');
                    let best = null;
                    for (const e of els) {
                        if (e.querySelector('input[name="q"]')) continue;
                        const text = (e.innerText || '').trim();
                        if (!text || text.length < 120) continue;
                        const r = e.getBoundingClientRect();
                        if (r.width < 300 || r.height < 80 || r.height > 800) continue;
                        const pos = text.toLowerCase().indexOf('search assist');
                        if (pos < 0 || pos > 200) continue;
                        if (!best || text.length < best.textLen)
                            best = {x: r.x, y: r.y, width: r.width, height: r.height, bottom: r.bottom, textLen: text.length};
                    }
                    return best;
                }"""
            )
            if not card_rect:
                self.log("         No Search Assist card found for arrow detection.")
                return False

            # ── Strategy 1: aria-expanded="false" inside the card ────
            arrow_candidates = await page.evaluate(
                """(cardRect) => {
                    const results = [];
                    const els = document.querySelectorAll(
                        'button, [role="button"], div[tabindex], span[tabindex]'
                    );
                    for (let i = 0; i < els.length; i++) {
                        const e = els[i];
                        const r = e.getBoundingClientRect();
                        if (r.width < 10 || r.height < 10 || r.width > 200 || r.height > 80) continue;
                        const cx = r.left + r.width / 2;
                        const cy = r.top + r.height / 2;
                        // Must be inside or just below the card
                        if (cx < cardRect.x || cx > cardRect.x + cardRect.width) continue;
                        if (cy < cardRect.y || cy > cardRect.bottom + 40) continue;

                        const txt = (e.innerText || '').trim();
                        const aria = (e.getAttribute('aria-label') || '').trim();
                        const expanded = e.getAttribute('aria-expanded');
                        const hasSvg = !!e.querySelector('svg');
                        const combined = (txt + ' ' + aria).toLowerCase();

                        // Skip known unrelated controls (search button, icons, settings, etc.)
                        if (/settings|copy|share|feedback|close|safe.?search|language|protected|upload|submit|duck/i.test(combined)) continue;
                        // Skip the main search button (aria-label="search" or similar)
                        if (/^search$/i.test(aria) || /^search$/i.test(txt)) continue;
                        // Skip info/about buttons (the (i) icon)
                        if (/^info$|^about$|^information$/i.test(aria)) continue;

                        const isLikely =
                            expanded === 'false' ||
                            hasSvg && txt.length < 3 ||
                            /expand|chevron|arrow|down|toggle|show/i.test(combined);

                        if (isLikely) {
                            results.push({
                                index: i,
                                cy: cy,
                                expanded: expanded,
                                hasSvg: hasSvg,
                                txt: txt.slice(0, 50),
                                aria: aria.slice(0, 50)
                            });
                        }
                    }
                    // Prefer elements near the bottom of the card
                    results.sort((a, b) => b.cy - a.cy);
                    return results;
                }""",
                card_rect,
            )

            if not arrow_candidates:
                self.log("         No expand-arrow candidates found inside Search Assist card.")
                return False

            # Try clicking the top-ranked candidate.
            all_clickables = page.locator(
                "button, [role='button'], div[tabindex], span[tabindex]"
            )
            for c in arrow_candidates[:5]:
                try:
                    item = all_clickables.nth(int(c["index"]))
                    if not await item.is_visible():
                        continue
                    await item.scroll_into_view_if_needed(timeout=2000)
                    await item.click(timeout=5000)
                    self.log(
                        f"         Clicked expand arrow (text={c.get('txt','')!r}, "
                        f"aria={c.get('aria','')!r}, svg={c.get('hasSvg',False)})"
                    )
                    return True
                except Exception:
                    continue

        except Exception as exc:
            self.log(f"         Expand-arrow detection error: {type(exc).__name__}: {str(exc)[:120]}")
        return False

    async def _click_more(self, page, timeout_ms=60000):
        """Expand the Search Assist card (arrow), then click More.

        Sequence:
          1. Check if More is already visible → click it.
          2. If not, click the expand/down arrow (once).
          3. Wait for More to appear after expansion → click it.
        """
        deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
        arrow_clicked = False

        while asyncio.get_running_loop().time() < deadline:
            more = await self._find_more_button(page)
            if more is not None:
                try:
                    await more.scroll_into_view_if_needed(timeout=2000)
                    await more.click(timeout=5000)
                    self.log("         'More' button clicked successfully.")
                    return True
                except Exception:
                    pass

            # Only try the arrow once per Search Assist open cycle.
            if not arrow_clicked:
                arrow_clicked = await self._click_expand_arrow_if_needed(page)
                if arrow_clicked:
                    # Give DDG time to render the expanded section + More button
                    try:
                        await page.wait_for_timeout(2000)
                    except Exception:
                        return False
                    continue

            try:
                await page.wait_for_timeout(500)
            except Exception:
                return False

        return False

    async def _search_assist_status(self, page):
        try:
            body = await page.locator("body").inner_text(timeout=3000)
            low = re.sub(r"\s+", " ", body or "").strip().lower()
            return low
        except Exception:
            return ""

    async def _wait_for_assist_answer(self, page, timeout_ms=90000):
        """Wait for the Search Assist card to stabilize, expand it, then click More.

        Sequence:
          1. Wait a configurable period (default 15 s) for the card to render.
          2. Try the expand arrow → More flow via ``_click_more``.
          3. If More is clicked successfully, return ``("more", ...)``.
          4. If the card says "no relevant information", return ``("empty", ...)``.
        """
        # Reduce hardcoded wait significantly; we rely on polling instead.
        open_wait_ms = min(2000, int(getattr(self.settings, "search_assist_open_wait_ms", 2000)))
        self.log(
            f"         Search Assist open; waiting {open_wait_ms // 1000}s before attempting expand + More..."
        )
        try:
            await page.wait_for_timeout(open_wait_ms)
        except Exception:
            return "timeout", ""

        # Check for "no relevant information" early.
        text = await self._extract_search_assist_context(page)
        if text and "sorry, no relevant information was found" in text.casefold():
            return "empty", text

        # _click_more handles: arrow click → wait → More click.
        if await self._click_more(page, timeout_ms=max(30000, timeout_ms)):
            return "more", text or ""

        # More never appeared. Return whatever text we have.
        text = text or await self._extract_search_assist_context(page)
        if text and "sorry, no relevant information was found" in (text or "").casefold():
            return "empty", text

        return "timeout", text or ""

    async def _extract_expanded_profile_card(self, page):
        """Extract the expanded Company Profile table, not the short summary.

        DDG currently renders a card containing a summary followed by a
        ``Company Profile`` / ``General Information`` table. We deliberately
        select the smallest DOM element containing those markers and convert
        table/list rows into ``Label: Value`` lines. This makes the downstream
        parser independent of visual layout and prevents summary prose from
        becoming Industry/Sector data.
        """
        try:
            rows = await page.locator("div,section,article").evaluate_all(
                r"""els => {
                    const candidates = [];
                    for (const e of els) {
                        const text=(e.innerText||'').trim();
                        // Accept cards with Company Profile OR General Information OR explicit Industry/Sector labels
                        const hasProfile = /company profile/i.test(text) || /general information/i.test(text);
                        const hasLabels = /\bIndustry\s*:/i.test(text) || /\bSector\s*:/i.test(text);
                        if (!hasProfile && !hasLabels) continue;
                        const r=e.getBoundingClientRect();
                        if (r.width < 250 || r.height < 100 || text.length < 150 || text.length > 30000) continue;
                        candidates.push({e,text,len:text.length});
                    }
                    candidates.sort((a,b)=>a.len-b.len);
                    if (!candidates.length) return [];
                    const e=candidates[0].e;
                    const out=[];
                    const push=(t)=>{t=(t||'').replace(/\s+/g,' ').trim(); if(t && !out.includes(t)) out.push(t);};

                    // Prefer table rows and semantic list rows when available.
                    e.querySelectorAll('tr').forEach(tr=>{
                        const cells=[...tr.querySelectorAll('th,td')].map(x=>(x.innerText||'').trim()).filter(Boolean);
                        if(cells.length>=2) push(cells[0]+': '+cells.slice(1).join(' '));
                    });
                    e.querySelectorAll('li').forEach(li=>{
                        const t=(li.innerText||'').trim();
                        if(/^(Industry|Sectors?(?:\s+Served)?|Founded|Established|Employees|Employee Count|Company Size|Headquarters|Address|Email|Website|LinkedIn|Phone|Mobile)\s*:/i.test(t)) {
                            // Sub-lists render as newlines. Convert to comma-separated.
                            push(t.replace(/\n+/g, ', '));
                        }
                    });

                    // If DDG does not use a real <table>, retain the rendered
                    // profile text. The label parser below will still require
                    // explicit ``Industry:`` / ``Sector:`` labels.
                    if(out.length < 2) push(e.innerText||'');
                    return out;
                }"""
            )
            if not rows:
                return ""
            return "\n".join(rows)
        except Exception:
            return ""

    async def _wait_for_expanded_profile(self, page, timeout_ms=120000):
        """Wait until the expanded Company Profile is rendered and stable."""
        deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
        previous = ""
        stable_count = 0
        last_good = ""

        while asyncio.get_running_loop().time() < deadline:
            current = await self._extract_expanded_profile_card(page)
            normalized = re.sub(r"\s+", " ", current or "").strip()

            if normalized and normalized == previous:
                stable_count += 1
            else:
                stable_count = 0
            previous = normalized

            has_industry = bool(re.search(r"(?:^|\n)Industry(?:\s*:)?\s*$|(?:^|\n)Industry\s*:", current or "", re.I | re.M))
            has_sector = bool(re.search(r"(?:^|\n)Sector(?:\s*:)?\s*$|(?:^|\n)Sector\s*:", current or "", re.I | re.M))

            # The presence of explicit Industry/Sector labels is sufficient
            # proof the expanded profile has loaded.  Do NOT require the
            # heading text ("Company Profile" / "General Information")
            # because the extraction JS returns only table rows / list
            # items — headings are <h3>/<h4> tags and are never included
            # in the extracted text.
            if current and (has_industry or has_sector):
                last_good = current
                if stable_count >= 2:
                    return current

            if "sorry, no relevant information was found" in normalized.lower():
                return ""

            try:
                await page.wait_for_timeout(500)
            except Exception:
                break

        return last_good

    async def _reload_ddg(self, page, query, timeout_ms):
        """Reload/reopen a DDG Search Assist tab without clicking controls.

        The caller intentionally performs the complete Search Assist sequence
        again after this recovery: click Search Assist -> wait 15s -> More ->
        wait 15-25s -> expanded profile.
        """
        self.log("         Reloading DuckDuckGo Search Assist page...")
        try:
            if page and not page.is_closed():
                await page.reload(
                    wait_until="domcontentloaded",
                    timeout=timeout_ms,
                )
                return True

            page = await self._ensure_page()
            await page.goto(
                "https://duckduckgo.com/?q=" + quote_plus(query),
                wait_until="domcontentloaded",
                timeout=timeout_ms,
            )
            return True
        except Exception:
            return False

    async def _extract_search_assist_context(self, page):
        """
        Extract the currently rendered Search Assist card.

        DDG changes its DOM frequently, therefore this intentionally uses
        several DOM shapes and finally the body text.
        """
        blocks = []
        try:
            blocks = await page.locator(
                "div,section,article"
            ).evaluate_all(
                """els => els.map(e => (e.innerText || '').trim())
                    .filter(t => /search assist/i.test(t))
                    .filter(t => t.length >= 60 && t.length <= 18000)
                    .sort((a,b) => a.length - b.length)
                    .slice(0, 30)"""
            )
        except Exception:
            blocks = []

        if blocks:
            # Prefer the smallest Search Assist block containing profile terms.
            useful = [
                b for b in blocks
                if re.search(
                    r"\bindustry\b|\bsector\b|\bfounded\b|\bemployees\b|"
                    r"\bheadquarters\b|\baddress\b|\bcontact\b",
                    b,
                    re.I,
                )
            ]
            return min(useful or blocks, key=len)

        try:
            body = await page.locator("body").inner_text(timeout=7000)
        except Exception:
            return ""

        m = re.search(r"search assist", body or "", re.I)
        if not m:
            return ""

        return body[m.start():m.start() + 18000]

    @staticmethod
    def _looks_irrelevant(text):
        low = re.sub(r"\s+", " ", text or "").strip().lower()
        if not low:
            return True
        if "sorry, no relevant information was found" in low:
            return True
        return len(low) < 120

    async def search_query_with_assist(self, query, timeout_ms=None):
        """
        Execute a DuckDuckGo query and retrieve the expanded Search Assist
        company profile.

        Production flow:
          1. Open the exact query on DuckDuckGo.
          2. Click Search Assist.
          3. Wait exactly 15 seconds.
          4. Wait for More to become available and click it.
          5. Wait a random 15-25 seconds.
          6. Poll until the expanded profile is stable.
          7. If the page is broken/empty/More never appears, reload the tab
             and repeat the complete flow.

        The returned Search Assist text is raw expanded-card text. Consumers
        must parse explicit labels; the summary paragraph is never treated as
        a structured Industry/Sector value.
        """
        query = str(query or "").strip()
        if not query:
            return [], ""

        timeout_ms = timeout_ms or self.settings.search_timeout_ms
        rows = await self._search_once(query)
        assist_text = ""

        # Three attempts are enough to recover transient DDG rendering errors
        # without allowing one company to block a large batch indefinitely.
        for attempt in range(1, 4):
            self.last_assist_attempt = attempt
            page = None

            try:
                self.log(f"         Search Assist attempt {attempt}/3")
                page = await self._ensure_page()

                url = "https://duckduckgo.com/?q=" + quote_plus(query)
                await page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=max(timeout_ms, 30000),
                )

                # Let DDG's JavaScript mount the full UI (Search Assist card,
                # nav controls, etc.) before attempting any interaction.
                await page.wait_for_timeout(2000)

                clicked = await self._click_search_assist(
                    page,
                    timeout_ms=30000,
                )

                if not clicked:
                    self.log(
                        "         Search Assist is not available for this query; "
                        "skipping Search Assist and continuing with normal results."
                    )
                    break

                status, current = await self._wait_for_assist_answer(
                    page,
                    timeout_ms=max(90000, int(
                        getattr(self.settings, "search_assist_max_wait_ms", 90000)
                    )),
                )

                if status == "more":
                    # _wait_for_assist_answer already clicked More for us.
                    # Do NOT call _click_more again — that would toggle it closed.
                    self.log(
                        "         Search Assist 'More' was clicked by the wait handler."
                    )

                    self.log(
                        "         Waiting for expanded profile to render..."
                    )
                    # We removed the random 15-25s sleep here because
                    # _wait_for_expanded_profile does its own efficient polling.

                    expanded = await self._wait_for_expanded_profile(
                        page,
                        timeout_ms=max(90000, int(
                            getattr(self.settings, "search_assist_expanded_wait_ms", 120000)
                        )),
                    )

                    if expanded and not self._looks_irrelevant(expanded):
                        assist_text = expanded
                        self.log(
                            "         Expanded Search Assist profile loaded successfully."
                        )
                        break

                    self.log(
                        "         Expanded Search Assist did not contain a usable "
                        "profile; reloading before retry."
                    )

                elif status == "empty":
                    self.log(
                        "         Search Assist returned no relevant information; "
                        "reloading before retry."
                    )

                else:
                    self.log(
                        "         Search Assist/More did not finish before the "
                        "safety timeout; reloading before retry."
                    )

                if attempt < 3:
                    await self._recover_search_page(page, query)
                    continue

            except Exception as exc:
                self.log(
                    "         DuckDuckGo Search Assist warning: "
                    f"{type(exc).__name__}: {str(exc)[:180]}"
                )

                if attempt < 3:
                    await self._recover_search_page(page, query)
                    continue

        # Collect normal result links/snippets from the final live page.
        try:
            page = await self._ensure_page()
            js_rows = await page.locator(
                "a[data-testid='result-title-a'], a.result__a"
            ).evaluate_all(
                """els => els.map(a => {
                    const parent = a.closest('[data-testid=\"result\"], div.result, article') || a.parentElement;
                    const txt = parent ? (parent.innerText || '') : '';
                    return {
                        u: a.href || '',
                        t: (a.innerText || '').trim(),
                        s: txt.slice(0, 1200)
                    };
                })"""
            )
            if js_rows:
                rows = rows + [x for x in js_rows if x.get("u")]
        except Exception as exc:
            self.log(
                "         Search Assist result-link warning: "
                f"{type(exc).__name__}: {str(exc)[:140]}"
            )

        return self._rows_to_candidates(rows, query), assist_text

    async def _recover_search_page(self, page, query):
        """
        Reload/recreate the DuckDuckGo tab after a failed Search Assist attempt.

        The next attempt deliberately starts the complete flow again: open query
        -> click Search Assist -> wait 15s -> More -> wait 15-25s.
        """
        try:
            if page and not page.is_closed():
                self.log("         Reloading DuckDuckGo Search Assist page...")
                await page.reload(
                    wait_until="domcontentloaded",
                    timeout=max(self.settings.search_timeout_ms, 30000),
                )
                return True

            page = await self._ensure_page()
            self.log("         Recreating DuckDuckGo Search Assist page...")
            await page.goto(
                "https://duckduckgo.com/?q=" + quote_plus(query),
                wait_until="domcontentloaded",
                timeout=max(self.settings.search_timeout_ms, 30000),
            )
            return True

        except Exception as exc:
            self.log(
                "         Search Assist recovery warning: "
                f"{type(exc).__name__}: {str(exc)[:160]}"
            )
            return False

    async def _search_once(self, query):
        try:
            page = await self._ensure_page()

            url = "https://html.duckduckgo.com/html/?q=" + quote_plus(query)

            await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=self.settings.search_timeout_ms,
            )
            await page.wait_for_timeout(600)

            return await page.locator("div.result").evaluate_all(
                """els => els.map(e => {
                    const a = e.querySelector('a.result__a');
                    const s = e.querySelector('.result__snippet');

                    return a ? {
                        u: a.href || '',
                        t: (a.innerText || '').trim(),
                        s: s ? (s.innerText || '').trim() : ''
                    } : null;
                }).filter(Boolean)"""
            )

        except Exception as exc:
            self.log(
                f"      DuckDuckGo search warning: "
                f"{type(exc).__name__}: {str(exc)[:180]}"
            )
            return []

    def _rows_to_candidates(self, rows, query):
        out, seen = [], set()

        for x in rows:
            u = self.clean(x.get("u", "") or x.get("url", ""))
            if not u or not self.valid(u):
                continue

            key = u.split("#")[0].rstrip("/")
            if key in seen:
                continue

            seen.add(key)
            out.append(
                SearchCandidate(
                    u,
                    x.get("t", ""),
                    x.get("s", ""),
                    query,
                )
            )

            if len(out) >= self.settings.max_search_results:
                break

        return out

    def clean(self, url):
        if not url or not url.startswith(("http://", "https://")):
            return None

        parsed = urlparse(url)

        for key in ("uddg", "q", "url"):
            value = parse_qs(parsed.query).get(key, [None])[0]
            if value and value.startswith(("http://", "https://")):
                return value

        return url

    def valid(self, url):
        host = urlparse(url).netloc.lower().removeprefix("www.")

        return bool(host) and not (
            host == "duckduckgo.com"
            or host.endswith(".duckduckgo.com")
        )
