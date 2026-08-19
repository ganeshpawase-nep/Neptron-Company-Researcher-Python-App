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

        if context is None:
            raise RuntimeError(
                "DuckDuckGo search page is closed and its browser context is unavailable."
            )

        # BrowserContext does not have is_closed(); probe with a lightweight op.
        try:
            _ = context.pages
        except Exception:
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

        # Dismiss the introductory Search Assist popup if it appears.
        await self._dismiss_search_assist_popup(page)

        return page

    async def _dismiss_search_assist_popup(self, page):
        """Dismiss the DuckDuckGo Search Assist introductory/welcome popup if present.

        DuckDuckGo shows a one-time modal explaining what Search Assist is.
        This modal blocks interaction with the actual Search Assist card and
        must be closed before the automation can proceed.

        The popup contains text like:
          - "Search Assist is an optional feature that anonymously generates
            answers for search queries"
          - "scans the web for relevant content"
          - "Search Settings"
        And has a close button (× icon) in the top-right corner.
        """
        for attempt in range(3):
            try:
                dismissed = await page.evaluate(r"""() => {
                    // Look for modal/dialog/overlay containing Search Assist intro text
                    const allEls = document.querySelectorAll('*');
                    for (const el of allEls) {
                        const text = (el.innerText || '').trim();
                        const r = el.getBoundingClientRect();
                        // Skip tiny or page-sized elements
                        if (r.width < 150 || r.height < 100) continue;
                        // Skip elements larger than a modal popup
                        if (r.width > 900 && r.height > 800) continue;
                        // Must contain the intro/explanation text about Search Assist
                        const isIntroPopup = (
                            /optional feature/i.test(text) ||
                            (/anonymously/i.test(text) && /generates/i.test(text)) ||
                            (/scans the web/i.test(text) && /Search Assist/i.test(text))
                        );
                        if (!isIntroPopup) continue;

                        // Found the intro popup — try every strategy to close it

                        // Strategy 1: aria-label close/dismiss button
                        const closeByAria = el.querySelector(
                            '[aria-label*="close" i], [aria-label*="dismiss" i], ' +
                            '[aria-label*="Close" i], [aria-label*="Dismiss" i]'
                        );
                        if (closeByAria) { closeByAria.click(); return 'closed-via-aria'; }

                        // Strategy 2: Any clickable with × / X / ✕ / close text
                        const clickables = el.querySelectorAll(
                            'button, [role="button"], a, span[tabindex], div[tabindex]'
                        );
                        for (const b of clickables) {
                            const bt = (b.innerText || b.textContent || '').trim();
                            if (/^[\u00d7\u2715\u2716\u2717\u2718Xx]$/.test(bt) || /^close$/i.test(bt)) {
                                b.click(); return 'closed-via-text';
                            }
                        }

                        // Strategy 3: SVG close icon (small clickable with SVG)
                        for (const b of clickables) {
                            const br = b.getBoundingClientRect();
                            if (b.querySelector('svg') && br.width < 60 && br.height < 60 &&
                                br.top >= r.top && br.top <= r.top + 80) {
                                b.click(); return 'closed-via-svg';
                            }
                        }

                        // Strategy 4: Look for close button OUTSIDE the popup (sibling/parent)
                        const parent = el.parentElement;
                        if (parent) {
                            const siblingBtns = parent.querySelectorAll(
                                'button, [role="button"]'
                            );
                            for (const b of siblingBtns) {
                                const bt = (b.innerText || b.textContent || '').trim();
                                const ba = (b.getAttribute('aria-label') || '').trim();
                                if (/^[\u00d7\u2715Xx]$/.test(bt) || /close|dismiss/i.test(ba)) {
                                    b.click(); return 'closed-via-parent';
                                }
                            }
                        }

                        return 'found-but-no-close-button';
                    }
                    return null;
                }""")

                if dismissed and dismissed != 'found-but-no-close-button':
                    self.log(f"         Dismissed Search Assist intro popup ({dismissed}).")
                    await page.wait_for_timeout(500)
                    return True

                if dismissed == 'found-but-no-close-button':
                    # Popup found but no close button matched — try Escape key
                    self.log("         Search Assist intro popup found; trying Escape key...")
                    await page.keyboard.press("Escape")
                    await page.wait_for_timeout(500)

                    # Check if popup is gone
                    still_present = await page.evaluate(r"""() => {
                        const allEls = document.querySelectorAll('*');
                        for (const el of allEls) {
                            const text = (el.innerText || '').trim();
                            const r = el.getBoundingClientRect();
                            if (r.width < 150 || r.height < 100 || (r.width > 900 && r.height > 800)) continue;
                            if (/optional feature/i.test(text) || (/scans the web/i.test(text) && /Search Assist/i.test(text)))
                                return true;
                        }
                        return false;
                    }""")
                    if not still_present:
                        self.log("         Dismissed Search Assist intro popup (closed-via-escape).")
                        return True
                    # Try clicking outside the popup to dismiss it
                    self.log("         Escape did not close popup; clicking outside...")
                    await page.mouse.click(50, 50)
                    await page.wait_for_timeout(500)
                    continue
                else:
                    # No popup found at all
                    return False
            except Exception as exc:
                self.log(f"         Popup dismissal attempt {attempt+1} error: {type(exc).__name__}")
        return False

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

    async def _click_search_assist(self, page, timeout_ms=30000):
        """Click the Search Assist nav button using raw physical mouse at exact pixel coordinates.

        Strategy:
          1. Wait 5 seconds for the page to fully render.
          2. Use JavaScript to find the exact <A> tag whose href contains 'assist=true'.
          3. Get that element's center pixel coordinates.
          4. Use raw page.mouse.move/down/up to physically click at those exact coordinates.
          5. If no <A> tag found, fall back to finding any small element with 'Search Assist' text.
        """
        # Wait exactly 5 seconds for page to fully render
        try:
            await page.wait_for_timeout(5000)
        except Exception:
            pass

        self.log("         Waiting 5s completed. Attempting to click Search Assist...")

        deadline = asyncio.get_running_loop().time() + timeout_ms / 1000

        while asyncio.get_running_loop().time() < deadline:
            # Use JavaScript to find the EXACT clickable element and its center coordinates
            coords = await page.evaluate("""() => {
                // Strategy 1: Find the <A> tag with assist=true in href (most reliable)
                const links = document.querySelectorAll('a');
                for (const a of links) {
                    const href = a.getAttribute('href') || '';
                    if (href.includes('assist=true')) {
                        const r = a.getBoundingClientRect();
                        if (r.width > 0 && r.height > 0) {
                            return {
                                x: Math.round(r.x + r.width / 2),
                                y: Math.round(r.y + r.height / 2),
                                method: 'href-assist-true'
                            };
                        }
                    }
                }

                // Strategy 2: Find small elements whose own text is exactly "Search Assist"
                const all = document.querySelectorAll('a, button, span, li, div');
                for (const el of all) {
                    const r = el.getBoundingClientRect();
                    // Must be a small, visible element in the nav area (not a wrapper)
                    if (r.width < 30 || r.width > 200 || r.height < 10 || r.height > 50) continue;
                    if (r.top < 0 || r.top > 150) continue;  // nav bar is near the top

                    const text = (el.innerText || '').trim();
                    if (/^(\\S+\\s+)?Search\\s+Assist$/i.test(text)) {
                        return {
                            x: Math.round(r.x + r.width / 2),
                            y: Math.round(r.y + r.height / 2),
                            method: 'text-match'
                        };
                    }
                }

                return null;
            }""")

            if coords:
                self.log(f"         Found Search Assist at ({coords['x']}, {coords['y']}) via {coords['method']}")

                # Raw physical mouse click at exact pixel coordinates
                await page.mouse.move(coords['x'], coords['y'])
                await asyncio.sleep(0.2)
                await page.mouse.down()
                await asyncio.sleep(0.1)
                await page.mouse.up()

                self.log("         Search Assist physically clicked.")
                return True

            # Not found yet, wait and retry
            self.log("         Search Assist button not found yet, retrying...")
            try:
                await page.wait_for_timeout(1000)
            except Exception:
                return False

        self.log("         Search Assist button not found within timeout.")
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
                        // Exclude the intro/welcome popup
                        const low = text.toLowerCase();
                        if (low.includes('optional feature') ||
                            low.includes('anonymously generates') ||
                            (low.includes('scans the web') && low.includes('search settings'))) continue;
                        // Exclude the DDG navigation bar
                        const head = low.slice(0, 300);
                        const navTerms = ['images', 'videos', 'news', 'maps'];
                        const navHits = navTerms.filter(t => head.includes(t)).length;
                        if (navHits >= 3) continue;
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
                        // Skip the intro popup's "Learn More About Assist" button
                        if (/learn more|about assist|optional feature/i.test(combined)) continue;

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
                    
                    # Human-like click requested by user
                    await item.hover(timeout=2000)
                    await page.mouse.down()
                    await asyncio.sleep(0.1)
                    await page.mouse.up()
                    
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

    async def _click_more(self, page, timeout_ms=20000):
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
                    
                    # Human-like click requested by user
                    await more.hover(timeout=2000)
                    await page.mouse.down()
                    await asyncio.sleep(0.1)
                    await page.mouse.up()
                    
                    self.log("         'More' button clicked successfully.")
                    return True
                except Exception:
                    pass

            # Only try the arrow once per Search Assist open cycle.
            if not arrow_clicked:
                result = await self._click_expand_arrow_if_needed(page)
                # Mark as attempted regardless of success to prevent
                # spamming retries when no arrow exists on the page.
                arrow_clicked = True
                if result:
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

    async def _wait_for_assist_answer(self, page, timeout_ms=30000):
        """Wait for the Search Assist card to stabilize, expand it, then click More.

        Sequence:
          1. Wait a configurable period (default 2 s) for the card to render.
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
        if await self._click_more(page, timeout_ms=max(20000, timeout_ms)):
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
                        const r=e.getBoundingClientRect();
                        if (r.width < 250 || r.height < 100 || text.length < 150 || text.length > 30000) continue;
                        
                        const low = text.toLowerCase();
                        // Must actually be the Search Assist card!
                        const pos = low.indexOf('search assist');
                        if (pos < 0 || pos > 200) continue;
                        
                        // Exclude the DDG navigation bar.
                        const head = low.slice(0, 300);
                        const navTerms = ['images', 'videos', 'news', 'maps'];
                        const navHits = navTerms.filter(t => head.includes(t)).length;
                        if (navHits >= 3) continue;
                        
                        // Exclude the main search results wrapper.
                        if (e.querySelectorAll('article').length > 1) continue;
                        if (e.querySelectorAll('[data-testid="result"]').length > 1) continue;
                        
                        // Look for Company Profile markers
                        const hasProfile = /company profile/i.test(text) || /general information/i.test(text);
                        const hasLabels = /\bIndustry\b/i.test(text) && (/\bSector\b/i.test(text) || /Founded|Employees|Headquarters/i.test(text));
                        const hasSearchAssist = low.indexOf('search assist') >= 0 && low.indexOf('search assist') < 200;

                        if (!hasProfile && !hasLabels && !hasSearchAssist) continue;
                        
                        candidates.push({e, text, len: text.length, hasLabels, hasSearchAssist});
                    }
                    
                    // Prefer elements that definitely have labels or the exact Search Assist header
                    candidates.sort((a, b) => {
                        let scoreA = (a.hasLabels ? 2 : 0) + (a.hasSearchAssist ? 1 : 0);
                        let scoreB = (b.hasLabels ? 2 : 0) + (b.hasSearchAssist ? 1 : 0);
                        if (scoreA !== scoreB) return scoreB - scoreA;
                        // Otherwise prefer the smaller containing element
                        return a.len - b.len;
                    });
                    
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
                        // Allow leading bullets, dashes, spaces before the label
                        if(/^[\s\-\•\*]*(Industry|Sectors?(?:\s+Served)?|Founded|Established|Employees|Employee Count|Company Size|Headquarters|Address|Email|Website|LinkedIn|Phone|Mobile)\s*:/i.test(t)) {
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

    async def _wait_for_expanded_profile(self, page, timeout_ms=35000):
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

            # Allow leading bullets, dashes, or whitespace before the labels
            has_industry = bool(re.search(r"(?:^|\n)[\s\-\•\*]*Industry(?:\s*:)?\s*$|(?:^|\n)[\s\-\•\*]*Industry\s*:", current or "", re.I | re.M))
            has_sector = bool(re.search(r"(?:^|\n)[\s\-\•\*]*Sector(?:\s*:)?\s*$|(?:^|\n)[\s\-\•\*]*Sector\s*:", current or "", re.I | re.M))

            # The presence of explicit Industry/Sector labels is sufficient
            # proof the expanded profile has loaded.
            if current and (has_industry or has_sector):
                last_good = current
                if has_industry and has_sector:
                    return current
                if stable_count >= 1:
                    return current
            elif current and stable_count == 0:
                # Log the extracted text so we can see what's being rejected
                self.log(f"         [DEBUG] extracted text: {repr(current[:300])}")

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

                # Dismiss the introductory Search Assist popup if it appears.
                await self._dismiss_search_assist_popup(page)

                clicked = await self._click_search_assist(
                    page,
                    timeout_ms=15000,
                )

                if not clicked:
                    self.log(
                        "         Search Assist is not available for this query; "
                        "skipping Search Assist and continuing with normal results."
                    )
                    break

                status, current = await self._wait_for_assist_answer(
                    page,
                    timeout_ms=max(20000, int(
                        getattr(self.settings, "search_assist_max_wait_ms", 30000)
                    )),
                )

                if status == "more":
                    # _wait_for_assist_answer already clicked More for us.
                    # Do NOT call _click_more again — that would toggle it closed.
                    self.log(
                        "         Search Assist 'More' was clicked by the wait handler."
                    )

                    # Add random 10-15s wait before polling to ensure DDG AI has time to generate the answer.
                    import random
                    wait_secs = random.uniform(10.0, 15.0)
                    self.log(f"         Waiting {wait_secs:.1f}s for expanded profile to render...")
                    try:
                        await page.wait_for_timeout(int(wait_secs * 1000))
                    except Exception:
                        pass

                    expanded = await self._wait_for_expanded_profile(
                        page,
                        timeout_ms=max(20000, int(
                            getattr(self.settings, "search_assist_expanded_wait_ms", 35000)
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
        -> click Search Assist -> wait -> More -> expanded profile.
        """
        try:
            page_alive = False
            try:
                page_alive = page is not None and not page.is_closed()
            except Exception:
                pass

            if page_alive:
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
