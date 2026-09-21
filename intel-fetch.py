# Grapevine fetcher. Pulls every feed in intel-feeds.json, drops vendor promotion, tags what is left,
# merges with the previous intel.json so stories outlive short feeds, and writes intel.json for intel.html (Grapevine).
# Standard library plus curl only, so it runs the same on a laptop and on the Actions runner.
import json, os, re, subprocess, time, html, hashlib, concurrent.futures as cf
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse, parse_qs, urlunparse

KEEP_DAYS, MAX_ITEMS, PER_FEED = 45, 900, 40
UA_READER  = "FieldKit-IntelWire/1.0 (+feed reader)"
UA_BROWSER = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"

# ---------- promo filter ----------
NEG_STRONG = r"""webinar|register (now|today)|save the date|join us|live event|on-demand|\bawards?\b|named an? |a leader in|gartner|forrester
|magic quadrant|marketscape|peer insights|customer (story|spotlight)|case study|success story|e-?book|white ?paper|data ?sheet|buyer'?s guide
|press release|\bannounc(es|ing|ement)\b|now available|general availability|\blaunch(es|ed|ing)?\b|partnership|partners with|teams up|to acquire
|\bacquir(es|ed)\b|acquisition|funding|series [a-e]\b|investors?\b|fiscal|earnings|quarterly results|appoints|joins as|we'?re hiring|careers?\b
|free trial|request a demo|book a demo|meet us|visit us|booth|sponsor|recognized (as|by)|certification|certified|achieves|milestone|anniversary"""
NEG_SOFT = r"""podcast|episode \d|newsletter|best practices|\btips\b|awareness month|compliance|cyber insurance|why you need|how to choose
|reasons (to|why)|introducing|what'?s new|product update|release notes|\bplatform\b|\bmsps?\b|\bmssp\b|\broi\b|\bciso\b|the board|budget
|predictions|year in review|resolution|thought leadership|checklist|explained|what is |101\b|guide to|roundup|recap|\bwhy\b|statistics
|enterprises|regulat|governance|resilience|spotlight|dashboard|strategy|cybersecurity plan|\| [A-Z][\w ]{2,24}$|trends report|\bsoc\b|agentic|\bai\b"""
POS = r"""malware|ransomware|stealer|loader|backdoor|\brat\b|botnet|trojan|dropper|implant|wiper|rootkit|bootkit|skimmer|cryptominer|campaign
|threat actor|intrusion|\bapt[- ]?\d*\b|cve-\d{4}-\d+|exploit|zero[- ]day|0-?day|vulnerab|\brce\b|auth(entication)? bypass|phish|smish|vish
|\bc2\b|command[- ]and[- ]control|\biocs?\b|indicators? of|\bttps?\b|initial access|access broker|lateral movement|persistence|privilege escalation
|exfiltrat|supply[- ]chain|deep dive|reverse engineer|unpack|deobfuscat|obfuscat|shellcode|injection|hunting|detection|sigma\b|yara|\bedr\b
|living off the land|lolbin|powershell|active directory|kerberos|entra|okta|oauth|token theft|session hijack|\baitm\b|adversary-in-the-middle
|clickfix|fake ?captcha|malvertis|seo poison|socgholish|byovd|vulnerable driver|esxi|hypervisor|linux|macos|android|\bbreach|leak site|extortion
|affiliate|\braas\b|espionage|dprk|north korea|china-nexus|russia|iran|telemetry|forensic|incident response|compromis|credential|brute[- ]?forc
|webshell|tunnel|proxy|decrypt|payload|infection chain|infostealer|attackers? (abuse|use|exploit)|abus(e|ing)|bypass|tradecraft|trojani[sz]ed|\(part \d|cobalt strike|sliver|mythic|havoc|brute ratel|\brmm\b|screenconnect|anydesk|impacket|mimikatz|bloodhound|certipy|adcs"""
NEG_STRONG, NEG_SOFT, POS = [re.compile(p.replace("\n", ""), re.I) for p in (NEG_STRONG, NEG_SOFT, POS)]

def judge(title, summary, url, trust):
    """Returns (is_promo, reason). Title evidence counts double; low-trust feeds must show a technical signal."""
    pt, ps = (len({m.group(0).lower() for m in POS.finditer(x)}) for x in (title, summary))
    strong = NEG_STRONG.search(title) or NEG_STRONG.search(urlparse(url).path.replace("-", " "))
    soft = NEG_SOFT.search(title)
    soft_n = len({m.group(0).lower() for m in NEG_SOFT.finditer(title)})
    score = 2 * pt + min(ps, 3) - (4 if strong else 0) - 2 * soft_n
    if trust == "high":
        return (bool(strong) and pt == 0, f"promo term '{strong.group(0).strip()}'" if strong and pt == 0 else "")
    if trust == "mid":
        bad = score < 0 or (strong and pt == 0) or (pt + ps == 0 and bool(summary))
    else:
        bad = score <= 0 or (pt + ps) == 0 or bool(strong)
    if not bad: return (False, "")
    hit = strong or soft
    return (True, f"promo term '{hit.group(0).strip()}'" if hit else "no technical signal")

# ---------- tagging ----------
TAGS = [("ransomware", r"ransomware|\braas\b|extortion|leak site|encryptor|locker\b"),
 ("stealer", r"stealer|infostealer|credential theft|cookie theft|token theft"),
 ("loader", r"\bloader\b|dropper|downloader|initial payload"),
 ("initial-access", r"initial access|access broker|clickfix|fake ?captcha|malvertis|seo poison|socgholish|drive-by|fake update"),
 ("phishing", r"phish|\baitm\b|adversary-in-the-middle|smish|vish|quish|bec\b"),
 ("vuln", r"cve-\d{4}-\d+|zero[- ]day|0-?day|\brce\b|auth(entication)? bypass|vulnerab|exploit"),
 ("identity", r"active directory|kerberos|entra|azure ad|okta|oauth|\bsso\b|saml|adcs|\bad cs\b|ntlm|session hijack|\bmfa\b"),
 ("cloud", r"\baws\b|azure|\bgcp\b|kubernetes|container|\bs3\b|cloud|saas|m365|microsoft 365|snowflake"),
 ("linux", r"linux|\belf\b|esxi|hypervisor|ssh\b|ebpf"), ("macos", r"macos|\bmac os\b|\bamos\b|launchagent"),
 ("mobile", r"android|\bios\b|\bapk\b|spyware"),
 ("apt", r"\bapt[- ]?\d+|espionage|state-sponsored|nation-state|dprk|north korea|china-nexus|lazarus|kimsuky|sandworm|turla|volt typhoon|salt typhoon"),
 ("detection", r"detection|hunting|sigma\b|yara|\bedr\b|telemetry|\bkql\b|\bxql\b|query|log source|threat hunt"),
 ("byovd", r"byovd|vulnerable driver|vulndriver|edr killer|edr-killer"), ("supply-chain", r"supply[- ]chain|\bnpm\b|pypi|typosquat|malicious package")]
TAGS = [(n, re.compile(p, re.I)) for n, p in TAGS]
ENTS = """LockBit|Akira|Qilin|Play ransomware|Black Basta|BlackSuit|Medusa|RansomHub|INC Ransom|Lynx|Rhysida|Cl0p|Clop|DragonForce|SafePay|Fog|Hunters International
|Interlock|Embargo|8Base|BianLian|Cactus|Scattered Spider|ShinyHunters|Lapsus|Lumma|LummaC2|Vidar|RedLine|StealC|Rhadamanthys|Raccoon|Atomic Stealer|AMOS|Poseidon|Meduza
|Gootloader|SocGholish|ClearFake|KongTuke|ClickFix|FileFix|Latrodectus|IcedID|Qakbot|QakBot|Emotet|Pikabot|Bumblebee|DarkGate|NetSupport|AsyncRAT|Remcos|XWorm|Agent Tesla
|SmokeLoader|Amadey|HijackLoader|GuLoader|Matanbuchus|Cobalt Strike|Sliver|Brute Ratel|Havoc|Mythic|Evilginx|Tycoon 2FA|Tycoon|Mamba 2FA|EvilProxy|Sneaky 2FA|Rockstar 2FA|Raccoon O365
|Lazarus|Kimsuky|APT28|APT29|APT41|Sandworm|Turla|Volt Typhoon|Salt Typhoon|Silk Typhoon|MuddyWater|Charming Kitten|FIN7|TA505|TA577|TA578|Storm-\\d{4}|UNC\\d{3,5}|UAT-\\d+
|ScreenConnect|AnyDesk|SimpleHelp|Ivanti|Fortinet|FortiGate|Citrix|NetScaler|SonicWall|Palo Alto|PAN-OS|Cisco ASA|SharePoint|Exchange|Veeam|ESXi|vCenter|CrushFTP|MOVEit|Cleo"""
ENTS = re.compile(r"\b(" + ENTS.replace("\n", "") + r")\b", re.I)
CVE = re.compile(r"CVE-\d{4}-\d{4,7}", re.I)
CANON = {}
def ents(text):
    out = []
    for m in ENTS.findall(text):
        k = m.lower().replace("lummac2", "lumma").replace("clop", "cl0p")
        CANON.setdefault(k, m)
        if CANON[k] not in out: out.append(CANON[k])
    return out[:6]

# ---------- fetch + parse ----------
def curl(url, ua):
    r = subprocess.run(["curl", "-sL", "--compressed", "--max-time", "40", "-A", ua, "-H",
        "Accept: application/rss+xml,application/atom+xml,application/xml,text/xml,*/*", url], capture_output=True)
    return r.stdout
def strip(s, n=None):
    s = html.unescape(re.sub(r"<[^>]+>", " ", html.unescape(s or "")))
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\s*(The post .* appeared first on .*|Continue reading.*|Read more\W*)$", "", s)
    return (s[:n].rsplit(" ", 1)[0] + "…") if n and len(s) > n else s
def when(s):
    if not s: return None
    s = s.strip()
    try: d = parsedate_to_datetime(s)
    except Exception:
        try: d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception: return None
    if d.tzinfo is None: d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)
def clean_url(u):
    u = (u or "").strip()
    if "google.com/url" in u:
        q = parse_qs(urlparse(u).query); u = (q.get("url") or q.get("q") or [u])[0]
    p = urlparse(u)
    q = "&".join(x for x in p.query.split("&") if x and not re.match(r"(utm_|source=rss|ref=|fbclid|gclid|mc_)", x, re.I))
    return urlunparse((p.scheme, p.netloc, p.path, "", q, ""))
def relay(url):
    """Same feed via Feedly's public stream API. Their crawler is allowlisted where datacenter IPs get a challenge page."""
    from urllib.parse import quote
    raw = curl("https://cloud.feedly.com/v3/streams/contents?count=%d&streamId=%s" % (PER_FEED, quote("feed/" + url, safe="")), UA_READER)
    out = []
    for i in json.loads(raw).get("items", []):
        link = (i.get("canonicalUrl") or ((i.get("alternate") or [{}])[0].get("href")) or i.get("originId") or "")
        ms = i.get("published") or i.get("crawled") or 0
        out.append({"title": i.get("title", ""), "link": link,
                    "pubdate": datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat() if ms else "",
                    "description": (i.get("summary") or i.get("content") or {}).get("content", "")})
    return out
# ---------- GitHub repos in "adds" mode ----------
# Noisy IOC repos (upload-button commits, the same note edited five times) are read through the API instead of
# the commit feed: a drop is a commit that ADDS files, titled from what it added. Edits, renames and deletes are ignored.
GH_CACHE, GH_OLD = {}, {}
SKIP_FILE = re.compile(r"(^|/)(readme[^/]*|license[^/]*|\.[^/]+)$|\.(png|jpe?g|gif|svg|webp)$", re.I)
KIND = [(re.compile(r"\.ya?ra?$", re.I), "YARA"), (re.compile(r"\.(rules|suricata)$", re.I), "Suricata"),
        (re.compile(r"\.(csv|txt|json|md5|sha1|sha256|ioc|stix|xml)$", re.I), "IOCs")]
def gh_api(path):
    hdr = {"Accept": "application/vnd.github+json", "User-Agent": UA_READER, "X-GitHub-Api-Version": "2022-11-28"}
    if os.environ.get("GITHUB_TOKEN"): hdr["Authorization"] = "Bearer " + os.environ["GITHUB_TOKEN"]
    import urllib.request
    with urllib.request.urlopen(urllib.request.Request("https://api.github.com/" + path, headers=hdr), timeout=25) as r:
        return json.load(r)
def pretty(name):
    name = re.sub(r"\.[A-Za-z0-9]{1,8}$", "", name).replace("_", " ")
    name = re.sub(r"(?<!\d)-|-(?!\d)", " ", name)                  # hyphens become spaces except inside dates/ranges
    name = re.sub(r"^\d{4}-\d{2}(-\d{2})?\s*", "", name).strip()     # leading date, the row already shows it
    return re.sub(r"\s+", " ", name) or "untitled"
# folder names that describe a container, not a subject ("yara/rules/X.yar" should title from X, not "rules")
GENERIC = {"rules", "rule", "yara", "yar", "behavior", "iocs", "ioc", "indicators", "samples", "data", "windows", "linux",
           "macos", "cross-platform", "hunting", "queries", "detections", "signatures", "src", "files", "misc"}
def kind_of(p):
    e = p.rsplit(".", 1)[-1].lower() if "." in p.rsplit("/", 1)[-1] else ""
    if e in ("yar", "yara"): return "YARA rule"
    if e == "toml" and "behavior" in p: return "behavior rule"
    if e in ("toml", "yml", "yaml"): return "detection rule"
    if e in ("csv", "txt", "json", "md5", "sha1", "sha256", "ioc", "stix", "xml"): return "IOC file"
    return "file"
def kind_label(paths):
    from collections import Counter
    c = Counter(kind_of(p) for p in paths)
    if len(c) > 1: c.pop("file", None)                      # stray docs don't get a mention next to real rules
    parts = [f"{n} {k}{'s' if n > 1 else ''}" for k, n in c.most_common()]
    return " + ".join(parts)
def adds_title(repo, sha, files):
    keep = [p for p in files if not SKIP_FILE.search(p)]
    if not keep: return None
    names = [pretty(p.rsplit("/", 1)[-1]) for p in keep]
    names = [n[:1].upper() + n[1:] for n in names]
    folders = {p.rsplit("/", 1)[0] if "/" in p else "" for p in keep}
    leaf = next(iter(folders)).rsplit("/", 1)[-1] if len(folders) == 1 else ""
    kinds = []
    for p in keep:
        for rx, k in KIND:
            if rx.search(p) and k not in kinds: kinds.append(k)
    if leaf and leaf.lower() not in GENERIC and len(keep) > 1:
        # one subject folder with several files (Volexity: "2026-09-09 Chrome/iocs.csv + rules.yar")
        name = pretty(leaf)
        title = name + (" · " + " + ".join(kinds) if kinds and not re.search(r"\bIOCs?\b|\bYARA\b", name, re.I) else "")
    elif len(keep) == 1:
        title = names[0]
    elif len(keep) == 2:
        title = names[0] + " · " + names[1]
    else:
        # rule drops (Elastic adds 3 to 87 rules per commit): count + kind, the names go in the summary
        title = kind_label(keep) if len(set(kind_of(p) for p in keep) - {"file"}) > 1 else f"{len(keep)} new {kind_label(keep).split(' ', 1)[1]}"
    from urllib.parse import quote
    url = f"https://github.com/{repo}/blob/{sha}/{quote(keep[0])}" if len(keep) == 1 else f"https://github.com/{repo}/commit/{sha}"
    shown = names[:6] if len(keep) > 2 else [p.rsplit("/", 1)[-1] for p in keep[:4]]
    summary = ("Added " if len(keep) <= 2 else "") + ", ".join(shown) + (f", and {len(keep) - len(shown)} more" if len(keep) > len(shown) else "")
    return [title, url, summary]
def gh_adds(feed):
    repo, rows = feed["gh"], []
    for c in gh_api(f"repos/{repo}/commits?per_page=20"):
        sha, date = c["sha"], c["commit"]["author"]["date"]
        hit = GH_OLD.get(sha)
        if hit is None:
            files = [f["filename"] for f in gh_api(f"repos/{repo}/commits/{sha}").get("files", []) if f.get("status") == "added"]
            hit = adds_title(repo, sha, files) or 0                   # 0 = looked at, added nothing worth showing
        GH_CACHE[sha] = hit
        if hit: rows.append({"title": hit[0], "link": hit[1], "pubdate": date, "description": hit[2]})
    return rows

def local(tag): return tag.rsplit("}", 1)[-1].lower()
def parse(raw):
    raw = re.sub(rb"[\x00-\x08\x0b\x0c\x0e-\x1f]", b"", raw)
    try: root = ET.fromstring(raw)
    except ET.ParseError:
        root = ET.fromstring(re.sub(rb"&(?!(#\d+|#x[0-9a-fA-F]+|amp|lt|gt|quot|apos);)", b"&amp;", raw))
    out = []
    for el in root.iter():
        if local(el.tag) not in ("item", "entry"): continue
        f = {}
        for c in el:
            k = local(c.tag)
            if k == "link":
                href = c.get("href")
                if href:
                    if c.get("rel", "alternate") == "alternate" or "link" not in f: f["link"] = href
                elif c.text: f.setdefault("link", c.text)
            elif k in ("title", "pubdate", "published", "updated", "date", "description", "summary", "encoded", "content"):
                txt = "".join(c.itertext()) if k in ("title", "summary", "content") else (c.text or "")
                f.setdefault(k, txt)
        out.append(f)
    return out

def run(feed):
    res = {"id": feed["id"], "name": feed.get("show_as") or feed["name"], "cat": feed["cat"], "ok": False, "n": 0, "err": ""}
    rows = []
    if feed.get("type") == "wp":                                # WordPress REST (sites with no RSS for their blog)
        try:
            raw = curl(feed["url"], UA_BROWSER)
            posts = json.loads(raw)
            rows = [{"title": p["title"]["rendered"], "link": p["link"], "pubdate": (p.get("date_gmt") or "") + "Z",
                     "description": (p.get("excerpt") or {}).get("rendered", "")} for p in posts
                    if feed.get("path", "") in p.get("link", "")]
            res["ok"] = True; res["via"] = "site API"
        except Exception as e:
            res["err"] = "site API: " + str(e)[:70]
        return finish(feed, res, rows)
    if feed.get("mode") == "adds":
        try:
            rows = gh_adds(feed); res["ok"] = True; res["via"] = "adds only"
        except Exception as e:
            res["err"] = "GitHub API: " + str(e)[:70]
        return finish(feed, res, rows)
    for url in [feed["url"]] + feed.get("alt", []):
        for ua in (UA_READER, UA_BROWSER):
            try:
                raw = curl(url, ua)
                if not raw: res["err"] = "no response"; continue
                if raw.lstrip()[:15].lower().startswith((b"<!doctype html", b"<html")): res["err"] = "got a web page, not a feed"; continue
                rows = parse(raw); res["ok"] = True; res["err"] = ""; break
            except Exception as e:
                res["err"] = str(e)[:80]
        if res["ok"]: break
    if (not res["ok"] or os.environ.get("FORCE_RELAY", "").find(feed["id"]) >= 0) and "google.com/alerts" not in feed["url"]:
        for url in [feed["url"]] + feed.get("alt", []):
            try:
                got = relay(url)
                if got: rows = got; res["ok"] = True; res["err"] = ""; res["via"] = "feedly"; break
            except Exception as e:
                res["err"] = (res["err"] + "; relay: " + str(e))[:110]
    return finish(feed, res, rows)

def finish(feed, res, rows):
    items = []
    for f in rows[:200]:
        u = clean_url(f.get("link"))
        t = strip(f.get("title"))
        if not u or not t: continue
        if feed.get("exclude_url") and re.search(feed["exclude_url"], u): continue
        if feed["cat"] == "ioc" and feed.get("mode") != "adds" and re.search(r"^(merge |initial commit|add files via upload|update \S+$|create \S+$|delete |fix|rename|remove)|readme|typo|formatting", t, re.I): continue
        d = when(f.get("pubdate") or f.get("published") or f.get("date") or f.get("updated"))
        x = strip(f.get("description") or f.get("summary") or f.get("encoded") or f.get("content"), 210)
        if x.lower().startswith(t.lower()[:40]): x = x[len(t):].lstrip(" .:-–—…")
        items.append({"u": u, "t": t[:200], "s": feed["id"], "d": d, "x": x})
    items.sort(key=lambda i: i["d"] or datetime(1970, 1, 1, tzinfo=timezone.utc), reverse=True)
    items = items[:PER_FEED]
    for i in items:
        blob = i["t"] + " " + i["x"]
        promo, why = (False, "") if feed["cat"] == "ioc" else judge(i["t"], i["x"], i["u"], feed.get("trust", "mid"))
        if promo and feed.get("keep") and re.search(feed["keep"], i["t"], re.I): promo, why = False, ""
        if feed.get("drop") and re.search(feed["drop"], i["t"], re.I): promo, why = True, "newsletter or marketing series"
        i["t"] = re.sub(r"\s+\|\s+[A-Z][\w ]{2,24}$", "", i["t"])
        i["g"] = [n for n, rx in TAGS if rx.search(blob)][:5]
        i["c"] = sorted({c.upper() for c in CVE.findall(blob)})[:4]
        i["e"] = ents(blob)
        if promo: i["p"] = why
    res["n"] = len(items)
    return res, items

def main():
    feeds = json.load(open("intel-feeds.json"))
    now = datetime.now(timezone.utc); stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    try: old = json.load(open("intel.json"))
    except Exception: old = {"items": [], "feeds": []}
    known = {i["u"]: i for i in old.get("items", [])}
    norm = lambda t: re.sub(r"\W+", " ", t.lower()).strip()
    known_t = {}
    for i in old.get("items", []): known_t.setdefault((i["s"], norm(i["t"])), []).append(i)
    oldest = {}                                                         # oldest post date already covered per source
    for i in old.get("items", []): oldest[i["s"]] = min(i["d"], oldest.get(i["s"], i["d"]))
    replaced = set()
    # when each source was first read, so a post that shows up in a feed days after its date can be flagged as late
    since = {f["id"]: f.get("since") for f in old.get("feeds", []) if f.get("since")}
    for i in old.get("items", []):
        if i["s"] not in since or i["f"] < since[i["s"]]: since[i["s"]] = min(i["f"], since.get(i["s"], i["f"]))
    last_ok = {f["id"]: f.get("last_ok") for f in old.get("feeds", [])}
    GH_OLD.update(old.get("gh", {}))
    with cf.ThreadPoolExecutor(8) as ex: results = list(ex.map(run, feeds))
    live_ids = {f["id"] for f in feeds}; fresh = {}
    for res, items in results:
        res["last_ok"] = stamp if res["ok"] else last_ok.get(res["id"])
        res["since"] = since.get(res["id"]) or (stamp if res["ok"] else None)
        grace = None
        if res["since"]:
            grace = (when(res["since"]) + timedelta(hours=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
        print(f'{"ok " if res["ok"] else "ERR"} {res["n"]:>3}  {res["id"]:<18} {"via " + res["via"] if res.get("via") else ""} {res["err"]}')
        for i in items:
            prev = known.get(i["u"])
            if not prev:                                                 # same post under a new link: same title AND same date
                near = [k for k in known_t.get((res["id"], norm(i["t"])), [])
                        if i["d"] and abs((when(k["d"]) - (i["d"] if isinstance(i["d"], datetime) else when(i["d"]))).total_seconds()) < 36 * 3600]
                prev = near[0] if near else None
                if prev and prev["u"] != i["u"]: replaced.add(prev["u"])
            i["f"] = prev["f"] if prev else stamp                       # first seen by this cron
            d = i["d"] or when(i["f"]); i["d"] = d.strftime("%Y-%m-%dT%H:%M:%SZ")
            # late post: it first appeared a day or more after its own date, after we were already reading this
            # source, and inside the date range we had already covered (so it wasn't simply out of reach before)
            if grace and i["f"] >= grace and when(i["f"]) - d > timedelta(hours=36) and i["d"] >= oldest.get(res["id"], "9999"):
                i["l"] = 1
            if d > now + timedelta(hours=12): i["d"] = i["f"]           # feeds that post-date entries
            fresh.setdefault(i["u"], i)
    for u, i in known.items():                                          # keep history the feeds have rotated out
        if u not in fresh and u not in replaced and i.get("s") in live_ids: fresh[u] = i
    cutoff = (now - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    items = sorted((i for i in fresh.values() if i["d"] >= cutoff), key=lambda i: i["d"], reverse=True)[:MAX_ITEMS]
    for i in items:
        for k in ("g", "c", "e", "x"):
            if not i.get(k): i.pop(k, None)
    ok = sum(1 for r, _ in results if r["ok"])
    if ok < len(feeds) // 3: raise SystemExit("most feeds failed, keeping the previous intel.json")
    json.dump({"updated": stamp, "feeds": [r for r, _ in results], "items": items, "gh": GH_CACHE}, open("intel.json", "w"),
              separators=(",", ":"), ensure_ascii=False)
    promo = sum(1 for i in items if "p" in i)
    print(f"{ok}/{len(feeds)} feeds ok, {len(items)} stories kept, {promo} flagged as promo")
main()
