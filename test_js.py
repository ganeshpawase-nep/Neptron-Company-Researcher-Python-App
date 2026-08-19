import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        
        await page.goto("https://duckduckgo.com/?q=YASH+CORPORATION+company+profile+industry%2C+sector%2C+address%2C+employees+count%2C+founded%2C+contact+email%2C+website%2C+LinkedIn%2C+Contact+number")
        
        print("Waiting 10s for you to click Search Assist and More...")
        await page.wait_for_timeout(10000)
        
        print("Evaluating JS...")
        try:
            result = await page.locator("div,section,article").evaluate_all(
                r"""els => {
                    const candidates = [];
                    for (const e of els) {
                        const text=(e.innerText||'').trim();
                        const hasProfile = /company profile/i.test(text) || /general information/i.test(text);
                        const hasLabels = /\bIndustry\s*:/i.test(text) || /\bSector\s*:/i.test(text);
                        if (!hasProfile && !hasLabels) continue;
                        const r=e.getBoundingClientRect();
                        if (r.width < 250 || r.height < 100 || text.length < 150 || text.length > 30000) continue;
                        candidates.push({tagName: e.tagName, className: e.className, textLen: text.length, textSnippet: text.substring(0, 100).replace(/\n/g, ' ')});
                    }
                    candidates.sort((a,b)=>a.textLen-b.textLen);
                    
                    if (!candidates.length) return "NO CANDIDATES";
                    
                    return candidates;
                }"""
            )
            print("CANDIDATES:")
            for i, c in enumerate(result):
                print(f"[{i}] {c}")
                
            # Now simulate what gets extracted from candidate 0
            extract = await page.locator("div,section,article").evaluate_all(
                r"""els => {
                    const candidates = [];
                    for (const e of els) {
                        const text=(e.innerText||'').trim();
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

                    e.querySelectorAll('tr').forEach(tr=>{
                        const cells=[...tr.querySelectorAll('th,td')].map(x=>(x.innerText||'').trim()).filter(Boolean);
                        if(cells.length>=2) push(cells[0]+': '+cells.slice(1).join(' '));
                    });
                    e.querySelectorAll('li').forEach(li=>{
                        const t=(li.innerText||'').trim();
                        if(/^[\s\-\•\*]*(Industry|Sectors?(?:\s+Served)?|Founded|Established|Employees|Employee Count|Company Size|Headquarters|Address|Email|Website|LinkedIn|Phone|Mobile)\s*:/i.test(t)) {
                            push(t.replace(/\n+/g, ', '));
                        }
                    });

                    if(out.length < 2) push(e.innerText||'');
                    return out;
                }"""
            )
            print("\nEXTRACTED:")
            print(extract)
            
        except Exception as e:
            print(f"Error: {e}")
            
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
