
import re, unicodedata

LEGAL = {
    "private","limited","pvt","ltd","llp","inc","incorporated","corp",
    "corporation","company","co","private limited","private ltd","pvt limited"
}
def normalize_text(s):
    s=unicodedata.normalize("NFKD",s or "").encode("ascii","ignore").decode()
    s=s.lower().replace("&"," and ")
    return re.sub(r"\s+"," ",re.sub(r"[^a-z0-9\s]"," ",s)).strip()

def tokens(name):
    return [x for x in normalize_text(name).split() if x not in LEGAL and len(x)>1]

def compact(name):
    return "".join(tokens(name))
