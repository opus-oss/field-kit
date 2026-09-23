#!/usr/bin/env python3
"""Builds congress.json for the Markets board.

The public House disclosure mirror is an 11 MB file. Downloading that in the browser is slow
enough that the panel was hidden behind a button, so nobody saw it. This trims the same data to
the recent, meaningful filings and writes a small file the page can load on sight.

Source: github.com/TattooedHead/house-stock-watcher-data (public mirror of the Clerk's filings).
"""
import json, os, re, sys, time, urllib.request

SRC = "https://raw.githubusercontent.com/TattooedHead/house-stock-watcher-data/main/data/all_transactions.json"
OUT = "congress.json"
MAX_AGE_H = 12          # leave a recent file alone
WINDOW_D = 150          # how far back a filing still counts as recent
CAP = 1200              # hard ceiling on rows written
FLOOR = 15000           # drop the $1k to $15k noise tier

# cash and bond parking, no stock-specific signal
CASHBOND = {"BIL","SGOV","SHV","SHY","USFR","TFLO","ICSH","JPST","GBIL","CLTL","MINT","NEAR","FLOT",
    "VGSH","VGIT","VGLT","BND","AGG","BNDX","GOVT","IEF","IEI","TLH","TLT","MUB","TIP","VTIP","SCHO",
    "SCHR","SPTL","SPTS","STIP","SUB","BSV","BIV","BLV","MBB","VCSH","VCIT","VCLT","IGSB","IGIB",
    "USHY","HYG","JNK","LQD","SPAXX","FDRXX","VMFXX"}
# broad index funds, same reason
BROAD = {"SPY","QQQ","QQQM","VOO","VTI","IVV","DIA","IWM","VUG","VTV","ITOT","SPLG","RSP","SCHB",
    "SCHX","SCHG","SCHD","VEA","VWO","VXUS","IEFA","IEMG","VYM","VIG","VT","ACWI","EFA","EEM","MDY",
    "IJH","IJR","VB","VO","VGT","XLK","XLF","XLE","XLV"}
TK = re.compile(r"^[A-Z]{1,5}$")
D = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")


def epoch(s):
    m = D.match(str(s or ""))
    if not m:
        return 0
    mm, dd, yy = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        return int(time.mktime((yy, mm, dd, 12, 0, 0, 0, 0, -1)))
    except Exception:
        return 0


def fresh():
    try:
        with open(OUT) as f:
            j = json.load(f)
        return time.time() - j.get("generated", 0) < MAX_AGE_H * 3600
    except Exception:
        return False


def main():
    if fresh() and "--force" not in sys.argv:
        print("congress.json is recent, skipping")
        return
    req = urllib.request.Request(SRC, headers={"User-Agent": "field-kit"})
    raw = json.load(urllib.request.urlopen(req, timeout=180))
    rows = []
    for x in raw:
        tk = x.get("ticker") or ""
        if not TK.match(tk) or tk in CASHBOND or tk in BROAD:
            continue
        ty = str(x.get("type") or "")
        if not re.search(r"purchase|sale|sell", ty, re.I):
            continue
        mid = x.get("amount_mid")
        if not mid or mid <= FLOOR:
            continue
        s = epoch(x.get("disclosure_date"))
        if not s:
            continue
        rows.append({"tk": tk, "ty": "buy" if re.search(r"purchase", ty, re.I) else "sell",
                     "who": x.get("representative") or "", "mid": int(mid),
                     "dd": x.get("disclosure_date"), "s": s})
    rows.sort(key=lambda r: -r["s"])
    cut = time.time() - WINDOW_D * 86400
    recent = [r for r in rows if r["s"] >= cut][:CAP]
    if len(recent) < 120:                    # a quiet stretch still deserves a populated panel
        recent = rows[:400]
    out = {"generated": int(time.time()),
           "latest": recent[0]["dd"] if recent else None,
           "window_days": WINDOW_D, "floor": FLOOR,
           "items": [{k: r[k] for k in ("tk", "ty", "who", "mid", "dd")} for r in recent]}
    with open(OUT, "w") as f:
        json.dump(out, f, separators=(",", ":"))
    buys = sum(1 for r in recent if r["ty"] == "buy")
    print(f"congress.json: {len(recent)} filings, {buys} buys, latest {out['latest']}, "
          f"{os.path.getsize(OUT)//1024} KB")


if __name__ == "__main__":
    main()
