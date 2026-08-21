from urllib.parse import urlparse
from difflib import SequenceMatcher
from company.normalizer import normalize_text, tokens

LINKEDIN_HOSTS={"linkedin.com"}
IGNORED_HOSTS={
# Social media & developer platforms
"github.com","facebook.com","instagram.com","youtube.com","x.com","twitter.com","threads.net","pinterest.com",
# Indian business directories & aggregators
"falconebiz.com","thecompanycheck.com","companydetails.in","indiafilings.com","tracxn.com","tofler.in","zaubacorp.com",
"internshala.com","deepenrich.com","theorg.com","yappe.in","mahapage.com","eindiabusiness.com","justdial.com",
"indiamart.com","tradeindia.com","crunchbase.com","glassdoor.com","ambitionbox.com",
# Additional Indian business directories
"exportersindia.com","go4worldbusiness.com","infyner.com","fundoodata.com","indiabizexpress.com",
"bizprofile.in","grotal.com","sulekha.com","exportgenius.in","zauba.com",
"indiancompanyinfo.com","companiesinindia.com","indiacompanyinfo.com","companyinfo.in",
"indiabusiness.nic.in","mouthshut.com","dealstreetasia.com",
# Fastener / niche directories that scrape company data
"fastenersweb.com","about.me","opencorpdata.com",
# Global business directories & aggregators
"dnb.com","manta.com","yellowpages.com","yelp.com","bbb.org",
"importgenius.com","panjiva.com","hoovers.com","owler.com","pitchbook.com",
"bloomberg.com","reuters.com","forbes.com","inc.com",
"zoominfo.com","apollo.io","clearbit.com","lusha.com","rocketreach.co",
# Job / review portals
"indeed.com","naukri.com","monsterindia.com","payscale.com","comparably.com",
# Wikipedia / reference
"wikipedia.org","wikidata.org",
# Government / registration databases (not the company's own site)
"mca.gov.in","roc.gov.in",
}

def host(u): return urlparse(u).netloc.lower().removeprefix("www.")
def root(u):
    h=host(u).split("."); return ".".join(h[-2:]) if len(h)>=2 else host(u)
def source_kind(u):
    h=host(u)
    if h=="linkedin.com" or h.endswith(".linkedin.com"): return "linkedin"
    if any(h==x or h.endswith("."+x) for x in IGNORED_HOSTS): return "ignored"
    return "website"
def is_linkedin(u): return source_kind(u)=="linkedin"
def ratio(a,b): return round(SequenceMatcher(None,normalize_text(a),normalize_text(b)).ratio()*100)
def score_candidate(company,c):
    c.kind=source_kind(c.url)
    if c.kind!="website": c.score=0; c.evidence=[c.kind]; return c
    ts=tokens(company); title=normalize_text(c.title); snip=normalize_text(c.snippet); h=normalize_text(host(c.url)); blob=title+" "+snip
    score=20; ev=["normal website candidate"]
    r=ratio(company,c.title)
    if r>=90: score+=30; ev.append(f"strong title match ({r})")
    elif r>=75: score+=24; ev.append(f"good title match ({r})")
    elif r>=60: score+=15; ev.append(f"partial title match ({r})")
    hits=sum(1 for t in ts if t in blob.split())
    if ts and hits==len(ts): score+=25; ev.append(f"all company tokens ({hits}/{len(ts)})")
    elif ts and hits>=max(1,len(ts)//2): score+=12; ev.append(f"partial company tokens ({hits}/{len(ts)})")
    dh=sum(1 for t in ts if len(t)>=3 and t in h)
    if dh: score+=min(25,12*dh); ev.append(f"company token in domain ({dh})")
    if any(x in blob for x in ("official website","contact us","about us","company website","software","technology","services")): score+=5; ev.append("company-site cue")
    c.score=min(score,100); c.evidence=ev; return c
