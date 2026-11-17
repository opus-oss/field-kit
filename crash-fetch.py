#!/usr/bin/env python3
# Crash Watch data: pulls the public series behind crash.html (27 from FRED's no-key graph CSVs,
# plus daily closes for the bank index, regional banks, the chip fund and Nvidia), then writes
# crash.json with each reading, its date and source, a verdict against a written trip line, and
# history lines computed from the same series. Raw CSVs go to a temp folder and are not committed.
# A browser can't read FRED directly (no CORS), so the Crash workflow runs this and commits crash.json.
import csv, datetime as dt, io, json, os, subprocess, sys, tempfile, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.environ.get("CRASH_RAW") or os.path.join(tempfile.gettempdir(), "crash-raw")
OUT = os.path.join(HERE, "crash.json")
D = dt.date.fromisoformat
UA = {"User-Agent": "field-kit-crash/1.0 (+https://github.com/opus-oss/field-kit)"}
FRED_URL = "https://fred.stlouisfed.org/series/{}"
YAHOO_URL = "https://finance.yahoo.com/quote/{}/history"

FRED = [
    "DFF", "DGS10", "DGS30", "DFII10", "T10YIE", "T10Y3M", "DRTSCILM",
    "SP500", "NASDAQ100",
    "BAMLH0A3HYC", "BAMLH0A0HYM2", "BAMLH0A1HYBB", "BAMLC0A0CM",
    "DCPF3M", "DTB3",
    "WLCFLPCL", "SOFR", "IORB", "RPONTSYD", "WRESBAL", "NFCI",
    "VIXCLS", "STLFSI4", "DEXJPUS", "ICSA", "SAHMREALTIME", "WALCL",
]
YAHOO = {"BKX": "^BKX", "KRE": "KRE", "SMH": "SMH", "RSP": "RSP", "SPY": "SPY", "NVDA": "NVDA"}


def get(url, headers=UA, tries=2):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.read().decode("utf-8", "replace"), dict(r.headers)
        except Exception as e:
            err = e
            time.sleep(2)
    raise err


def fred(sid):
    txt, _ = get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}")
    rows = []
    for r in list(csv.reader(io.StringIO(txt)))[1:]:
        try:
            rows.append((r[0], float(r[1])))
        except (ValueError, IndexError):
            pass
    return rows


def yahoo(sym):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.request.quote(sym)}?range=30y&interval=1d"
    txt, _ = get(url)
    j = json.loads(txt)["chart"]["result"][0]
    ts = j["timestamp"]; cl = j["indicators"]["quote"][0]["close"]
    return [(dt.datetime.utcfromtimestamp(t).date().isoformat(), round(c, 4)) for t, c in zip(ts, cl) if c is not None]


def fetch_all():
    os.makedirs(RAW, exist_ok=True)
    log = {"fetched_utc": dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"), "ok": {}, "failed": {}}
    jobs = [(s, "fred", s) for s in FRED] + [(k, "yahoo", v) for k, v in YAHOO.items()]
    for key, kind, sym in jobs:
        try:
            rows = fred(sym) if kind == "fred" else yahoo(sym)
            if not rows:
                raise ValueError("no rows")
            with open(os.path.join(RAW, f"{key}.csv"), "w", newline="") as f:
                w = csv.writer(f); w.writerow(["date", "value"]); w.writerows(rows)
            log["ok"][key] = {"source": kind, "last": rows[-1], "n": len(rows)}
        except Exception as e:
            log["failed"][key] = f"{kind}: {e}"
        print(key, "ok" if key in log["ok"] else log["failed"][key], flush=True)
        time.sleep(0.4)
    with open(os.path.join(RAW, "_fetch_log.json"), "w") as f:
        json.dump(log, f, indent=1)
    print(json.dumps({"ok": len(log["ok"]), "failed": log["failed"]}, indent=1))


TOPS = {"2000": D("2000-03-24"), "2008": D("2007-10-09"), "2020": D("2020-02-19")}
LOWS = {"2000": D("2002-10-09"), "2008": D("2009-03-09"), "2020": D("2020-03-23")}


# ---------- series helpers ----------
def load(key):
    p = os.path.join(RAW, f"{key}.csv")
    if not os.path.exists(p):
        return None
    rows = []
    with open(p) as f:
        for r in list(csv.reader(f))[1:]:
            try:
                rows.append((D(r[0]), float(r[1])))
            except (ValueError, IndexError):
                pass
    return rows or None


def at(s, day):
    """Last value on or before day."""
    best = None
    for d, v in s:
        if d > day:
            break
        best = (d, v)
    return best


def window(s, a, b):
    return [(d, v) for d, v in s if a <= d <= b]


def peak(s, a, b, lo=False):
    w = window(s, a, b)
    if not w:
        return None
    return (min if lo else max)(w, key=lambda x: x[1])


def avg_last(s, n):
    tail = s[-n:]
    return sum(v for _, v in tail) / len(tail)


def drawdown(s, day=None, look=365):
    """% under the highest close of the prior `look` days, on `day` (default last)."""
    day = day or s[-1][0]
    now = at(s, day)
    hi = peak(s, day - dt.timedelta(days=look), day)
    return (now[1] / hi[1] - 1) * 100, hi, now


def max_drop(s, a, b):
    """Worst peak-to-trough fall inside a window, in %."""
    w = window(s, a, b)
    if not w:
        return None
    hi, worst = w[0][1], 0
    for _, v in w:
        hi = max(hi, v)
        worst = min(worst, v / hi - 1)
    return worst * 100


def covers(s, day):
    return s and s[0][0] <= day


def spark(s, years=3, n=150, f=lambda v: v):
    if not s:
        return []
    start = s[-1][0] - dt.timedelta(days=int(365.25 * years))
    w = [(d, v) for d, v in s if d >= start]
    step = max(1, len(w) // n)
    pts = w[::step]
    if pts[-1] != w[-1]:
        pts.append(w[-1])
    return [[d.isoformat(), round(f(v), 3)] for d, v in pts]


def diff(a, b):
    """a minus b on matching dates."""
    bd = dict(b)
    return [(d, v - bd[d]) for d, v in a if d in bd]


def mon(d):
    return d.strftime("%b %Y")


def day(d):
    return d.strftime("%b %-d, %Y")


def pct(x):
    return f"{abs(x):.0f}%"


# ---------- indicator records ----------
def card(id, chapter, name, what, value, unit_text, as_of, verdict, rule, history, src, src_label,
         spark_pts=None, trip=None, trip_label=None, note=None, invert=False):
    return {
        "id": id, "chapter": chapter, "name": name, "what": what,
        "value": value, "reading": unit_text if not unit_text or unit_text[-1] in ".!?" else unit_text + ".", "as_of": as_of.isoformat() if as_of else None,
        "verdict": verdict, "rule": rule, "history": history,
        "source": src, "source_label": src_label,
        "spark": spark_pts or [], "trip": trip, "trip_label": trip_label, "note": note,
    }


def nodata(id, chapter, name, what, rule, src, src_label, why):
    c = card(id, chapter, name, what, None, "No live reading", None, "nodata", rule,
             "", src, src_label)
    c["note"] = why
    return c


def grade(x, fired, warming, higher_is_worse=True):
    if x is None:
        return "nodata"
    if higher_is_worse:
        return "fired" if x >= fired else "warming" if x >= warming else "quiet"
    return "fired" if x <= fired else "warming" if x <= warming else "quiet"


def joined(parts):
    """'a', 'a and b', 'a, b, and c' -> one clause."""
    parts = [p for p in parts if p]
    if len(parts) <= 1:
        return "".join(parts)
    sep = "; " if any(" and " in p or "," in p for p in parts) else ", "
    if len(parts) == 2:
        return f"{parts[0]}{sep}and {parts[1]}" if sep == "; " else f"{parts[0]} and {parts[1]}"
    return sep.join(parts[:-1]) + sep + "and " + parts[-1]


def cap(s):
    return s[:1].upper() + s[1:] if s else s


TOP_NAME = {"2000": "the 2000 top", "2008": "the 2007 top", "2020": "the 2020 top"}


# ---------- charts: each sign drawn in the form its trip line uses ----------
import bisect


def dd_series(s, look=365):
    """% below the highest close of the prior `look` days, for every day."""
    out, dates = [], [d for d, _ in s]
    from collections import deque
    q = deque()  # indices with decreasing values
    for i, (d, v) in enumerate(s):
        while q and s[q[-1]][1] <= v:
            q.pop()
        q.append(i)
        while dates[q[0]] < d - dt.timedelta(days=look):
            q.popleft()
        out.append((d, (v / s[q[0]][1] - 1) * 100))
    return out


def chg_series(s, days):
    dates = [d for d, _ in s]
    out = []
    for d, v in s:
        j = bisect.bisect_right(dates, d - dt.timedelta(days=days)) - 1
        if j >= 0:
            out.append((d, v - s[j][1]))
    return out


def pct_obs(s, k):
    return [(s[i][0], (s[i][1] / s[i - k][1] - 1) * 100) for i in range(k, len(s)) if s[i - k][1]]


def yen_strength(s, k=20):
    return [(s[i][0], (s[i - k][1] / s[i][1] - 1) * 100) for i in range(k, len(s))]


def avg_series(s, k):
    out, acc = [], 0.0
    for i, (d, v) in enumerate(s):
        acc += v
        if i >= k:
            acc -= s[i - k][1]
        if i >= k - 1:
            out.append((d, acc / k))
    return out


def thin(w, n=180):
    if not w:
        return []
    step = max(1, len(w) // n)
    pts = w[::step]
    if pts[-1] != w[-1]:
        pts.append(w[-1])
    return [[d.isoformat(), round(v, 3)] for d, v in pts]


def in_danger(v, line, dir_):
    return v >= line if dir_ == "up" else v <= line


def crossings(w, line, dir_):
    """Dates where the series moves into the danger side of `line` and stays there.
    Daily series must stay over the line for five readings, so one-day blips don't count."""
    if len(w) < 2:
        return []
    gap = (w[-1][0] - w[0][0]).days / max(1, len(w) - 1)
    k = 5 if gap < 4 else 1
    out = []
    for i in range(1, len(w)):
        if not in_danger(w[i - 1][1], line, dir_) and all(in_danger(v, line, dir_) for _, v in w[i:i + k]) and len(w[i:i + k]) == k:
            out.append(w[i][0])
    return out


EXTRA_TOPS = {D("2022-01-03"): "Stocks topped"}


def add_charts(S, cards):
    sp = lambda a, b: diff(a, b) if a and b else None
    ccc = S.get("BAMLH0A3HYC")
    ccc_lo = None
    if ccc:
        ccc_lo = peak(ccc, ccc[-1][0] - dt.timedelta(days=365), ccc[-1][0], lo=True)[1]
    wl = S.get("WLCFLPCL")
    walcl = S.get("WALCL")
    spec = {
        "fed": dict(s=S.get("DFF"), lines=[], dir="up", yrs=3, past=("2004-01-01", "2008-12-31", "from 2004 to 2008, when the Fed raised rates into the housing boom")),
        "long": dict(s=chg_series(S["DGS30"], 365) if S.get("DGS30") else None, lines=[(0.25, "warm", "warming"), (0.75, "fire", "fired")], dir="up", yrs=3,
                     past=("2021-06-01", "2023-06-30", "from 2021 to 2023, through the 2022 bond rout"), unit="pts"),
        "curve": dict(s=S.get("T10Y3M"), lines=[(0, "warm", "zero")], dir="down", yrs=4, past=("2005-06-01", "2009-03-31", "from 2006 to 2009"), curve=True),
        "loans": dict(s=S.get("DRTSCILM"), lines=[(0, "warm", "warming"), (20, "fire", "fired")], dir="up", yrs=8, past=("2006-01-01", "2010-06-30", "from 2006 to 2010")),
        "leaders": dict(s=dd_series(S["NASDAQ100"]) if S.get("NASDAQ100") else None, lines=[(-8, "warm", "warming"), (-15, "fire", "fired")], dir="down", yrs=2,
                        past=("1999-06-01", "2001-06-30", "from 1999 to 2001, when the Nasdaq broke"), unit="%"),
        "chips": dict(s=dd_series(S["SMH"]) if S.get("SMH") else None, lines=[(-10, "warm", "warming"), (-20, "fire", "fired")], dir="down", yrs=2,
                      past=("2007-06-01", "2009-06-30", "from 2007 to 2009"), unit="%"),
        "ccc": dict(s=ccc, lines=([(round(ccc_lo + 1.5, 2), "warm", "warming"), (round(ccc_lo + 3, 2), "fire", "fired")] if ccc else []), dir="up", yrs=3, past=None),
        "hy": dict(s=S.get("BAMLH0A0HYM2"), lines=[(4.5, "warm", "warming"), (6, "fire", "fired")], dir="up", yrs=3, past=None),
        "cp": dict(s=avg_series(sp(S.get("DCPF3M"), S.get("DTB3")), 5) if S.get("DCPF3M") and S.get("DTB3") else None,
                   lines=[(0.5, "warm", "warming"), (1.0, "fire", "fired")], dir="up", yrs=3, past=("2007-01-01", "2009-03-31", "from 2007 to 2009")),
        "banks": dict(s=dd_series(S["BKX"]) if S.get("BKX") else None, lines=[(-10, "warm", "warming"), (-20, "fire", "fired")], dir="down", yrs=2,
                      past=("2006-06-01", "2009-03-31", "from 2006 to 2009, when banks led the way down"), unit="%"),
        "window": dict(s=[(d, v / 1000) for d, v in wl] if wl else None, lines=[(10, "warm", "warming"), (22, "fire", "go-time")], dir="up", yrs=3,
                       past=("2022-10-01", "2023-06-30", "in 2022 and 2023, around the Silicon Valley Bank run"), events={D("2023-03-10"): "SVB failed"}),
        "sofr": dict(s=[(d, v * 100) for d, v in avg_series(sp(S.get("SOFR"), S.get("IORB")), 5)] if S.get("SOFR") and S.get("IORB") else None,
                     lines=[(0, "warm", "warming"), (10, "fire", "fired")], dir="up", yrs=2, past=None),
        "repo": dict(s=avg_series(S["RPONTSYD"], 5) if S.get("RPONTSYD") else None, lines=[(10, "warm", "warming"), (25, "fire", "fired")], dir="up", yrs=2,
                     past=("2019-07-01", "2020-06-30", "in 2019 and 2020, through the repo squeeze and then Covid")),
        "nfci": dict(s=S.get("NFCI"), lines=[(-0.25, "warm", "warming"), (0, "fire", "fired")], dir="up", yrs=4, past=("2007-01-01", "2009-06-30", "from 2007 to 2009")),
        "vix": dict(s=avg_series(S["VIXCLS"], 5) if S.get("VIXCLS") else None, lines=[(20, "warm", "warming"), (30, "fire", "fired")], dir="up", yrs=2,
                    past=("2007-06-01", "2009-06-30", "from 2007 to 2009")),
        "stress": dict(s=S.get("STLFSI4"), lines=[(0, "warm", "warming"), (1, "fire", "fired")], dir="up", yrs=4, past=("2007-01-01", "2009-06-30", "from 2007 to 2009")),
        "yen": dict(s=yen_strength(S["DEXJPUS"]) if S.get("DEXJPUS") else None, lines=[(3, "warm", "warming"), (7, "fire", "fired")], dir="up", yrs=2,
                    past=("2008-06-01", "2009-03-31", "in 2008 and 2009, when the carry trade unwound"), unit="%"),
        "jobs": dict(s=S.get("SAHMREALTIME"), lines=[(0.3, "warm", "warming"), (0.5, "fire", "fired")], dir="up", yrs=4, past=("2006-06-01", "2009-12-31", "from 2006 to 2009")),
        "cuts": dict(s=chg_series(S["DFF"], 91) if S.get("DFF") else None, lines=[(-0.2, "warm", "warming"), (-0.5, "fire", "fired")], dir="down", yrs=3,
                     past=("2007-06-01", "2009-03-31", "from 2007 to 2009"), unit="pts"),
        "printing": dict(s=pct_obs([(d, v / 1e6) for d, v in walcl], 8) if walcl else None, lines=[(2, "warm", "warming"), (5, "fire", "fired")], dir="up", yrs=4,
                         past=("2008-06-01", "2009-06-30", "in 2008 and 2009"), unit="%"),
    }
    tops = {t: "Stocks topped" for t in TOPS.values()}
    tops.update(EXTRA_TOPS)
    for c in cards:
        sp_ = spec.get(c["id"])
        if not sp_ or not sp_["s"]:
            continue
        s, dir_, lines = sp_["s"], sp_["dir"], sp_["lines"]
        fire = next((v for v, k, _ in lines if k == "fire"), None)
        start = s[-1][0] - dt.timedelta(days=int(365.25 * sp_["yrs"]))
        w = [(d, v) for d, v in s if d >= start]
        marks = []
        if sp_.get("curve"):
            # the danger signal is the curve coming back above zero after a long inversion
            ups = crossings([(d, -v) for d, v in w], 0, "down")
            if ups:
                marks.append({"d": ups[-1].isoformat(), "label": "Back above zero", "kind": "fire"})
        elif fire is not None and in_danger(w[-1][1], fire, dir_):
            xs = crossings(w, fire, dir_)
            if xs:
                marks.append({"d": xs[-1].isoformat(), "label": f"Fired {xs[-1]:%b %-d}", "kind": "fire"})
        now = {"pts": thin(w), "lines": [{"v": v, "kind": k, "label": l} for v, k, l in lines], "dir": dir_, "marks": marks}
        past = None
        if sp_["past"]:
            a, b, label = D(sp_["past"][0]), D(sp_["past"][1]), sp_["past"][2]
            pw = [(d, v) for d, v in s if a <= d <= b]
            if len(pw) > 3:
                pm = []
                for t, lab in tops.items():
                    if a <= t <= b:
                        pm.append({"d": t.isoformat(), "label": lab, "kind": "top"})
                for t, lab in (sp_.get("events") or {}).items():
                    pm.append({"d": t.isoformat(), "label": lab, "kind": "top"})
                if sp_.get("curve"):
                    ups = crossings([(d, -v) for d, v in pw], 0, "down")
                    if ups:
                        pm.append({"d": ups[-1].isoformat(), "label": "Back above zero", "kind": "fire"})
                elif fire is not None:
                    xs = crossings(pw, fire, dir_)
                    if xs:
                        pm.append({"d": xs[0].isoformat(), "label": f"Crossed {xs[0]:%b %Y}", "kind": "fire"})
                past = {"label": label, "pts": thin(pw, 140), "marks": pm}
        c["chart"] = {"now": now, "past": past, "unit": sp_.get("unit", "")}
        c["spark"] = []


def build():
    S = {k[:-4]: load(k[:-4]) for k in os.listdir(RAW) if k.endswith(".csv")}
    log = json.load(open(os.path.join(RAW, "_fetch_log.json"))) if os.path.exists(os.path.join(RAW, "_fetch_log.json")) else {}
    cards, why_missing = [], log.get("failed", {})

    def miss(k):
        return f"This couldn't be pulled on the last refresh ({why_missing.get(k, 'not fetched')}), so there's no reading rather than a guess."

    def add(c, trigger):
        c["trigger"] = trigger
        cards.append(c)

    # ===== Chapter 1: money gets dear =====
    s = S.get("DFF")
    if s:
        d0, v0 = s[-1]
        v12 = at(s, d0 - dt.timedelta(days=365))[1]
        ch12 = v0 - v12
        v6 = at(s, d0 - dt.timedelta(days=182))[1]
        w6 = window(s, d0 - dt.timedelta(days=182), d0)
        last_cut = any(b_ < a_ - 0.2 for (_, a_), (_, b_) in zip(w6, w6[1:]))
        verdict = "fired" if v0 - v6 >= 0.2 and not last_cut else "warming" if ch12 > 0 else "quiet"
        parts = [f"from {at(s, t - dt.timedelta(days=730))[1]:.2f}% to {at(s, t)[1]:.2f}% in {t.year}" for y, t in TOPS.items()]
        add(card("fed", 1, "The Fed's rate",
            "This is the overnight rate the Fed sets for banks, and most busts begin with the Fed raising it while a boom is still running.",
            v0, f"The rate is {'up' if v0 - v6 >= 0 else 'down'} {abs(v0 - v6):.2f} points from six months ago", d0, verdict,
            "It counts as fired once the Fed has raised in the last six months without cutting since, and as warming if the rate is simply higher than a year ago.",
            f"In the two years before each top, the Fed's rate went {joined(parts)}.",
            FRED_URL.format("DFF"), "FRED DFF, daily", spark(s)),
            "fires on a hike in the last six months with no cut since")
    else:
        add(nodata("fed", 1, "The Fed's rate", "", "", FRED_URL.format("DFF"), "FRED DFF", miss("DFF")), "")

    s = S.get("DGS30")
    if s:
        d0, v0 = s[-1]
        ch = v0 - at(s, d0 - dt.timedelta(days=365))[1]
        add(card("long", 1, "Long-term borrowing cost",
            "This is what the US government pays to borrow for 30 years, and when it climbs quickly, mortgages, company loans and stock prices all come under pressure.",
            v0, f"The 30-year yield is {'up' if ch >= 0 else 'down'} {abs(ch):.2f} points from a year ago", d0, grade(ch, 0.75, 0.25),
            "It counts as fired when the yield is up three quarters of a point in a year, and as warming at a quarter point.",
            "Before the 1987 crash it rose from about 7.5% to over 10% in seven months, and when Britain's long bonds broke in 2022, pension funds were hit with margin calls within three days. These figures come from the project's earlier crash study and are rounded.",
            FRED_URL.format("DGS30"), "FRED DGS30, daily", spark(s)),
            "fires at a rise of three quarters of a point in a year")

    s = S.get("T10Y3M")
    if s:
        d0, v0 = s[-1]
        eps, cur = [], None
        for d, v in s:
            if v < 0:
                if cur and (d - cur[1]).days < 90:
                    cur[1] = d
                else:
                    cur = [d, d]; eps.append(cur)
        eps = [e for e in eps if (e[1] - e[0]).days > 60]
        last_ep = eps[-1] if eps else None
        if v0 < 0:
            verdict, state = "warming", "The curve is below zero right now, which means it's inverted"
        elif last_ep and (d0 - last_ep[1]).days <= 730:
            verdict, state = "fired", f"The curve went back above zero in {mon(last_ep[1])}, after {((last_ep[1] - last_ep[0]).days / 30.4):.0f} months below it"
        else:
            verdict, state = "quiet", "Above zero, with no recent stretch below it"
        parts = []
        for y, t in TOPS.items():
            near = [e for e in eps if t - dt.timedelta(days=730) <= e[1] <= t + dt.timedelta(days=400)]
            if near:
                e = max(near, key=lambda e: (e[1] - e[0]).days)
                parts.append(f"from {mon(e[0])} to {mon(e[1])} {'ahead of' if e[1] < t else 'around'} the {mon(t)} top")
        add(card("curve", 1, "The yield curve",
            "This is the 10-year Treasury rate minus the 3-month rate. It drops below zero when the bond market expects trouble, and the crash has usually come after it climbs back above zero rather than while it's negative.",
            v0, state, d0, verdict,
            "It counts as warming while it's below zero, and as fired once it's back above zero within two years of a long stretch below, which is the window where recessions have usually begun.",
            f"It was below zero {joined(parts)}." if parts else "",
            FRED_URL.format("T10Y3M"), "FRED T10Y3M, daily", spark(s, years=4), 0, "zero"),
            "fires when it climbs back above zero after a long inversion")

    s = S.get("DRTSCILM")
    if s:
        d0, v0 = s[-1]
        parts = []
        for y, t in TOPS.items():
            a = at(s, t)
            pk = peak(s, t, LOWS[y] + dt.timedelta(days=200))
            if a and pk:
                parts.append(f"{a[1]:.0f}% at {TOP_NAME[y]} before climbing to {pk[1]:.0f}%")
        add(card("loans", 1, "Banks getting stingy",
            "Every quarter the Fed asks bank loan officers whether they're making it harder for businesses to borrow, and this number is the share saying yes minus the share saying they've eased up.",
            v0, "That's the net share of banks tightening their rules for business loans", d0, grade(v0, 20, 0.01),
            "It counts as fired when 20% or more of banks are tightening on net, and as warming at anything above zero.",
            f"The net share tightening was {joined(parts)}. The survey comes out quarterly, and the next one is due in early February.",
            FRED_URL.format("DRTSCILM"), "FRED DRTSCILM, Fed loan officer survey", spark(s, years=8), 20, "fired line"),
            "fires at 20% tightening")

    thesis = None
    r, b = S.get("DFII10"), S.get("T10YIE")
    if r and b:
        thesis = {
            "real": r[-1][1], "real_date": r[-1][0].isoformat(),
            "be": b[-1][1], "be_date": b[-1][0].isoformat(),
            "f1": r[-1][1] > 3.0 and b[-1][1] < 2.0,
            "f4": r[-1][1] < 2.0 and b[-1][1] > 2.6,
            "src": [FRED_URL.format("DFII10"), FRED_URL.format("T10YIE")],
        }

    # ===== Chapter 2: leaders break =====
    ndx, spx = S.get("NASDAQ100"), S.get("SP500")
    if ndx and spx:
        dd_n, hi_n, now_n = drawdown(ndx)
        dd_s, hi_s, now_s = drawdown(spx, now_n[0])
        verdict = "fired" if dd_n <= -15 else "warming" if dd_n <= -8 else "quiet"
        spy = S.get("SPY")
        h = f"From March to May 2000 the Nasdaq-100 fell {pct(max_drop(ndx, D('2000-03-01'), D('2000-05-31')))}"
        if spy and covers(spy, D("2000-01-01")):
            h += f", yet on Sept 1 the S&P 500 fund was only {pct(drawdown(spy, D('2000-09-01'), look=400)[0])} below its high"
        h += (f". It went on to fall {pct(max_drop(ndx, TOPS['2008'], LOWS['2008']))} from the 2007 top to the 2009 bottom, "
              f"and {pct(max_drop(ndx, TOPS['2020'], LOWS['2020']))} in five weeks in 2020.")
        add(card("leaders", 2, "Tech leaders vs the whole market",
            "The Nasdaq-100 holds the giant companies that led the boom, and in a mania they tend to fall first while the broader S&P 500 hides the damage.",
            -dd_n, f"The Nasdaq-100 is {pct(dd_n)} below its {day(hi_n[0])} high, while the S&P 500 is only {pct(dd_s)} below its own", now_n[0], verdict,
            "It counts as fired when the Nasdaq-100 is 15% or more below its high of the past year, and as warming at 8%.",
            h, FRED_URL.format("NASDAQ100"), "FRED NASDAQ100 and SP500, daily", spark(ndx, years=2)),
            "fires at 15% below its high")
    chips, nv = S.get("SMH"), S.get("NVDA")
    if chips:
        dd_c, hi_c, now_c = drawdown(chips)
        rd = f"The chip fund is {pct(dd_c)} below its {day(hi_c[0])} high"
        if nv:
            rd += f", and Nvidia (NVDA) is {pct(drawdown(nv)[0])} below its own"
        parts = []
        if covers(chips, D("2000-07-01")):
            parts.append(f"{pct(max_drop(chips, D('2000-06-01'), LOWS['2000']))} from 2000 to 2002")
        parts.append(f"{pct(max_drop(chips, TOPS['2008'], LOWS['2008']))} from the 2007 top")
        parts.append(f"{pct(max_drop(chips, TOPS['2020'], LOWS['2020']))} in the 2020 crash")
        add(card("chips", 2, "Chip stocks, the AI leaders",
            "The semiconductor fund (SMH) holds Nvidia, Broadcom and the other chip makers behind the AI build-out, so it's the best single read on this boom's leaders.",
            -dd_c, rd, now_c[0], grade(-dd_c, 20, 10),
            "It counts as fired at 20% or more below its high of the past year, and as warming at 10%.",
            f"The chip fund fell {joined(parts)}.", YAHOO_URL.format("SMH"), "Yahoo Finance daily closes", spark(chips, years=2)),
            "fires at 20% below its high")
    else:
        add(nodata("chips", 2, "Chip stocks, the AI leaders",
            "The semiconductor fund (SMH) holds Nvidia, Broadcom and the other chip makers behind the AI build-out.",
            "It counts as fired at 20% below its high of the past year.", YAHOO_URL.format("SMH"), "Yahoo Finance", miss("SMH")), "")

    # ===== Chapter 3: credit cracks =====
    ccc, hy, bb = S.get("BAMLH0A3HYC"), S.get("BAMLH0A0HYM2"), S.get("BAMLH0A1HYBB")
    if ccc:
        d0, v0 = ccc[-1]
        lo = peak(ccc, d0 - dt.timedelta(days=365), d0, lo=True)
        hi = peak(ccc, d0 - dt.timedelta(days=365), d0)
        rise = v0 - lo[1]
        rd = f"That's how many points over Treasuries the weakest junk bonds pay, up {rise:.2f} since {lo[0]:%B} and just under the {hi[0]:%b %-d} high of {hi[1]:.2f}"
        if bb:
            rd += f", while the safest junk (BB) pays only {bb[-1][1]:.2f}"
        add(card("ccc", 3, "The weakest junk bonds",
            "CCC bonds are issued by the companies most likely to default, so the extra interest they have to pay over Treasuries tends to jump first when lenders start pulling back.",
            v0, rd, d0, grade(rise, 3, 1.5),
            "It counts as fired when the spread is up 3 points or more from its low of the past year, and as warming at 1.5.",
            "This pattern, with the weakest bonds blowing out while the rest of junk stays calm, showed up before 1990 and 2007, though it also gave false alarms in 1998, 2011 and 2016. That record comes from the project's earlier study, because the Fed's site now only shows three years of this series.",
            FRED_URL.format("BAMLH0A3HYC"), "FRED BAMLH0A3HYC (ICE BofA), daily", spark(ccc), 13.56, "Oct 23 high"),
            "fires at 3 points above its low of the past year")
    if hy:
        d0, v0 = hy[-1]
        lo = peak(hy, d0 - dt.timedelta(days=365), d0, lo=True)
        rise = v0 - lo[1]
        verdict = "fired" if v0 >= 6 else "warming" if (v0 >= 4.5 or rise >= 1.5) else "quiet"
        add(card("hy", 3, "All junk bonds",
            "This is the extra interest that all below-investment-grade companies pay over Treasuries, and when it widens, the whole market for risky borrowers is starting to shut.",
            v0, f"That's how many points of extra interest all junk bonds pay over Treasuries, up {rise:.2f} from the low of the past year", d0, verdict,
            "It counts as fired at 6 points or more, and as warming at 4.5 or after a rise of 1.5 from its low of the past year.",
            "From memory and rounded, it sat around 2.4 at the 2007 low and reached about 22 at the December 2008 peak, went from about 3.5 to about 11 in March 2020, and rose from roughly 5 to around 9 during 2000. The Fed's site only shows three years now, so these can't be checked live.",
            FRED_URL.format("BAMLH0A0HYM2"), "FRED BAMLH0A0HYM2 (ICE BofA), daily", spark(hy), 6, "fired line"),
            "fires at 6 points")
    cp, tb = S.get("DCPF3M"), S.get("DTB3")
    if cp and tb:
        sp = diff(cp, tb)
        d0 = sp[-1][0]
        v0 = avg_last(sp, 5)
        parts = []
        for y, (a, b_) in {"2000": (D("2000-01-01"), D("2001-12-31")), "2008": (D("2007-07-01"), D("2008-12-31")), "2020": (TOPS["2020"], D("2020-04-30"))}.items():
            pk = peak(sp, a, b_)
            if pk:
                parts.append(f"{pk[1]:.2f} in {mon(pk[0])}")
        add(card("cp", 3, "Short-term company IOUs",
            "Commercial paper is how banks and large companies borrow for a few weeks at a time, and its gap over Treasury bills jumps when lenders stop trusting the borrowers. This was the first thing to break in August 2007.",
            v0, "That's how many points company IOUs pay over Treasury bills, on a five-day average", d0, grade(v0, 1.0, 0.5),
            "It counts as fired at 1 point or more, and as warming at half a point.",
            f"The gap peaked at {joined(parts)}.", FRED_URL.format("DCPF3M"), "FRED DCPF3M minus DTB3, daily", spark(sp), 1.0, "fired line"),
            "fires at 1 point")

    # ===== Chapter 4: banks and the money pipes =====
    bkx = S.get("BKX")
    if bkx:
        dd_b, hi_b, now_b = drawdown(bkx)
        parts = []
        for y, t in TOPS.items():
            if covers(bkx, t - dt.timedelta(days=365)):
                ddt, hit, _ = drawdown(bkx, t)
                parts.append(f"{pct(ddt)} below their {mon(hit[0])} high in {y if y != '2008' else '2007'}")
        kre = S.get("KRE")
        rd = f"The bank index is {pct(dd_b)} below its {day(hi_b[0])} high"
        if kre:
            rd += f", and regional banks (KRE) are {pct(drawdown(kre)[0])} below theirs"
        add(card("banks", 4, "Bank stocks",
            "Banks feel bad loans before anyone else, which is why their stocks usually roll over while the broader market is still near its high.",
            -dd_b, rd, now_b[0], grade(-dd_b, 20, 10),
            "It counts as fired at 20% or more below its high of the past year, and as warming at 10%.",
            f"On the day the S&P 500 topped, bank stocks were already {joined(parts)}.",
            YAHOO_URL.format("%5EBKX"), "KBW bank index, Yahoo Finance daily closes", spark(bkx, years=2)),
            "fires at 20% below its high")
    else:
        add(nodata("banks", 4, "Bank stocks",
            "Banks feel bad loans before anyone else, which is why their stocks usually roll over while the broader market is still near its high.",
            "It counts as fired at 20% below its high of the past year.", YAHOO_URL.format("%5EBKX"), "Yahoo Finance", miss("BKX")), "")
    s = S.get("WLCFLPCL")
    if s:
        sb = [(d, v / 1000) for d, v in s]
        d0, v0 = sb[-1]
        two = sb[-2][1] if len(sb) > 1 else v0
        verdict = "fired" if v0 >= 22 and two >= 22 else "warming" if v0 >= 10 else "quiet"
        parts = []
        for y, (a, b_) in {"2008": (D("2007-08-01"), D("2009-06-30")), "2020": (D("2020-02-01"), D("2020-06-30")), "2023": (D("2023-03-01"), D("2023-05-31"))}.items():
            pk = peak(sb, a, b_)
            if pk:
                parts.append(f"${pk[1]:.0f} billion in {mon(pk[0])}")
        add(card("window", 4, "Banks at the Fed's emergency window",
            "The discount window is where banks borrow from the Fed when nobody else will lend to them, so a jump in it usually means a bank is losing its funding.",
            v0, f"Banks borrowed that many billions in the week of {day(d0)}, after ${two:.1f} billion the week before", d0, verdict,
            "It counts as fired at $22 billion or more two weeks running, which is the project's go-time line, and as warming at $10 billion.",
            f"It peaked at {joined(parts)}, and before Silicon Valley Bank failed it had been sitting at $5 to $10 billion for 15 weeks.",
            FRED_URL.format("WLCFLPCL"), "FRED WLCFLPCL, Fed weekly balance sheet", spark(sb), 22, "go-time line"),
            "fires at $22 billion two weeks running")
    so, io_ = S.get("SOFR"), S.get("IORB")
    if so and io_:
        sp = diff(so, io_)
        d0 = sp[-1][0]
        v0 = avg_last(sp, 5)
        pk19 = peak(so, D("2019-09-01"), D("2019-09-30"))
        add(card("sofr", 4, "Overnight lending vs the Fed's floor",
            "SOFR is the cost of borrowing cash overnight against Treasuries, and when it rises above the rate the Fed pays banks on their reserves, cash is getting scarce in the system.",
            v0, "Overnight cash costs that many basis points more than the Fed pays banks, on a five-day average, where 100 basis points is 1%", d0, grade(v0, 0.10, 0.0),
            "It counts as fired at 10 basis points or more above the Fed's rate, and as warming at anything above it.",
            (f"In September 2019 overnight cash jumped to {pk19[1]:.2f}% for a day, more than double the normal rate, and the Fed had to step in. " if pk19 else "") +
            "SOFR only started in 2018, so there's nothing to show for 2000 or 2008.",
            FRED_URL.format("SOFR"), "FRED SOFR minus IORB, daily", spark([(d, v * 100) for d, v in sp], years=2), 10, "fired line"),
            "fires at 10 basis points above the Fed's rate")
    s = S.get("RPONTSYD")
    if s:
        d0 = s[-1][0]
        v0 = avg_last(s, 5)
        pk = peak(s, D("2019-09-01"), D("2020-06-30"))
        add(card("repo", 4, "Banks borrowing from the Fed's repo desk",
            "This is the cash the Fed lends overnight against Treasuries, and heavy use of it means private lenders are pulling back.",
            v0, "Dealers borrowed that many billions a day from the Fed, on a five-day average", d0, grade(v0, 25, 10),
            "It counts as fired at $25 billion a day or more on average, and as warming at $10 billion. Spikes at the end of a month are normal, which is why it's averaged.",
            (f"It peaked at ${pk[1]:.0f} billion in {mon(pk[0])}, and " if pk else "") + "the Fed only restarted this tool in September 2019.",
            FRED_URL.format("RPONTSYD"), "FRED RPONTSYD, daily", spark(s, years=2), 25, "fired line"),
            "fires at $25 billion a day")
    s = S.get("NFCI")
    if s:
        d0, v0 = s[-1]
        parts = [f"from {at(s, t)[1]:+.2f} at {TOP_NAME[y]} to a peak of {peak(s, t, LOWS[y])[1]:+.2f}" for y, t in TOPS.items()]
        add(card("nfci", 4, "Overall money conditions",
            "The Chicago Fed rolls 105 measures of credit, funding and markets into one number, where zero is average and anything above zero means money is tighter than normal.",
            v0, "Zero is average, and anything above zero means money is tighter than normal", d0, grade(v0, 0, -0.25),
            "It counts as fired above zero, and as warming above minus 0.25.",
            f"It went {joined(parts)}.", FRED_URL.format("NFCI"), "FRED NFCI, weekly", spark(s, years=4), 0, "average"),
            "fires above zero")

    # ===== Chapter 5: panic =====
    s = S.get("VIXCLS")
    if s:
        d0, v0 = s[-1]
        a5 = avg_last(s, 5)
        parts = []
        for y, t in TOPS.items():
            pk = peak(s, t, LOWS[y])
            parts.append(f"from {at(s, t)[1]:.0f} at {TOP_NAME[y]} to a peak of {pk[1]:.0f} in {mon(pk[0])}")
        add(card("vix", 5, "The fear gauge",
            "The VIX is the price of insurance on the S&P 500. It climbs on the way down rather than before, so it confirms a panic instead of warning about one.",
            v0, f"The fear gauge's five-day average is {a5:.1f}", d0, grade(a5, 30, 20),
            "It counts as fired when the five-day average reaches 30, and as warming at 20.",
            f"It went {joined(parts)}.", FRED_URL.format("VIXCLS"), "FRED VIXCLS (CBOE), daily", spark(s, years=2), 30, "fired line"),
            "fires at a five-day average of 30")
    s = S.get("STLFSI4")
    if s:
        d0, v0 = s[-1]
        parts = [f"from {at(s, t)[1]:+.1f} at {TOP_NAME[y]} to a peak of {peak(s, t, LOWS[y])[1]:+.1f}" for y, t in TOPS.items() if covers(s, t)]
        add(card("stress", 5, "Financial stress index",
            "This is the St. Louis Fed's stress gauge, built from 18 market measures, where zero is normal and anything above it means stress.",
            v0, "Zero is normal, and anything above zero means stress", d0, grade(v0, 1, 0),
            "It counts as fired above 1, and as warming above zero.",
            f"It went {joined(parts)}.", FRED_URL.format("STLFSI4"), "FRED STLFSI4, weekly", spark(s, years=4), 0, "normal"),
            "fires above 1")
    s = S.get("DEXJPUS")
    if s:
        d0, v0 = s[-1]
        base = s[-21][1] if len(s) > 21 else s[0][1]
        stronger = (base / v0 - 1) * 100
        parts = []
        for label, a, b_ in (("October 1998", D("1998-09-15"), D("1998-10-31")), ("2008", D("2008-08-01"), D("2008-12-31")), ("August 2024", D("2024-07-01"), D("2024-08-31"))):
            w = window(s, a, b_)
            if len(w) > 21:
                best = max((w[i - 20][1] / w[i][1] - 1) * 100 for i in range(20, len(w)))
                parts.append(f"{best:.0f}% in {label}")
        add(card("yen", 5, "The yen jumps",
            "Traders borrow cheap yen to buy everything else, and when they're forced to sell, they have to buy the yen back, which makes it spike. A stronger yen shows up as fewer yen per dollar.",
            v0, f"That's how many yen buy a dollar, and the yen is {'stronger' if stronger >= 0 else 'weaker'} by {abs(stronger):.1f}% over the last 20 trading days", d0,
            grade(stronger, 7, 3),
            "It counts as fired when the yen is 7% stronger over 20 trading days, and as warming at 3%.",
            f"The yen gained as much as {joined(parts)}, each within about a month. The Fed posts this series weekly, so it can trail the market by a few days.",
            FRED_URL.format("DEXJPUS"), "FRED DEXJPUS (Fed H.10), daily, posted weekly", spark(s, years=2)),
            "fires when the yen is 7% stronger in a month")
    s = S.get("SAHMREALTIME")
    if s:
        d0, v0 = s[-1]
        parts = []
        for y, t in TOPS.items():
            hit = next((d for d, v in s if d >= t - dt.timedelta(days=200) and v >= 0.5), None)
            if hit:
                m = round((hit - t).days / 30.4)
                parts.append(f"in {mon(hit)}, {abs(m)} month{'s' if abs(m) != 1 else ''} {'after' if m >= 0 else 'before'} {TOP_NAME[y]}")
        add(card("jobs", 5, "The job market cracks",
            "The Sahm rule says a recession has started once the three-month average jobless rate is half a point above its low of the past year. It confirms a downturn late rather than warning early.",
            v0, "The three-month average jobless rate is that many points above its low of the past year", d0, grade(v0, 0.5, 0.3),
            "It counts as fired at 0.5 or more, and as warming at 0.3.",
            f"It crossed 0.5 {joined(parts)}.", FRED_URL.format("SAHMREALTIME"), "FRED SAHMREALTIME, monthly", spark(s, years=4), 0.5, "fired line"),
            "fires at 0.5")

    # ===== Chapter 6: rescue =====
    s = S.get("DFF")
    if s:
        d0, v0 = s[-1]
        ch3 = v0 - at(s, d0 - dt.timedelta(days=91))[1]
        add(card("cuts", 6, "The Fed cuts fast",
            "This tracks emergency or rapid rate cuts, which come after the panic and never before it.",
            ch3, f"The Fed's rate has moved {'down' if ch3 < 0 else 'up'} {abs(ch3):.2f} points over the last three months", d0, grade(ch3, -0.5, -0.2, higher_is_worse=False),
            "It counts as fired after cuts of half a point or more in three months, and as warming after a fifth of a point.",
            "The Fed made a surprise cut in January 2001, an emergency three-quarter-point cut on Jan 22, 2008, and two emergency cuts to zero on March 3 and 15, 2020.",
            FRED_URL.format("DFF"), "FRED DFF, daily", spark(s, years=2)),
            "fires on half a point of cuts in three months")
    s = S.get("WALCL")
    if s:
        sb = [(d, v / 1e6) for d, v in s]
        d0, v0 = sb[-1]
        ch = (v0 / sb[-9][1] - 1) * 100 if len(sb) > 9 else 0
        h08 = (at(sb, D("2008-12-31"))[1] / at(sb, D("2008-09-10"))[1] - 1) * 100
        h20 = (at(sb, D("2020-06-10"))[1] / at(sb, D("2020-03-11"))[1] - 1) * 100
        add(card("printing", 6, "The Fed's balance sheet jumps",
            "When the Fed buys bonds or lends on a huge scale its balance sheet balloons, and that money-printing turn is usually where the inflation after a crash begins.",
            ch, f"That's how much the Fed's balance sheet changed over the last eight weeks, and it now stands at ${v0:.2f} trillion", d0, grade(ch, 5, 2),
            "It counts as fired when the balance sheet grows 5% or more in eight weeks, and as warming at 2%.",
            f"It grew {h08:.0f}% from September to December 2008 and {h20:.0f}% from March to June 2020.",
            FRED_URL.format("WALCL"), "FRED WALCL, weekly", spark(sb, years=4)),
            "fires at 5% growth in eight weeks")

    # ===== go-time watch lines (from the Nov 8 plan; watch lines, not rules) =====
    watch = []
    c = next((x for x in cards if x["id"] == "window" and x["value"] is not None), None)
    if c:
        sb = [(d, v / 1000) for d, v in S["WLCFLPCL"]]
        watch.append({"name": "Fed emergency window at $22 billion two weeks running",
                      "now": f"${sb[-1][1]:.1f} billion last week and ${sb[-2][1]:.1f} billion the week before", "as_of": c["as_of"],
                      "fired": sb[-1][1] >= 22 and sb[-2][1] >= 22})
    c = next((x for x in cards if x["id"] == "yen"), None)
    if c:
        watch.append({"name": "The yen strengthens fast while stocks fall", "now": f"The yen is at {c['value']:.2f} per dollar and " + c["reading"].split("and the yen is ", 1)[-1].rstrip("."),
                      "as_of": c["as_of"], "fired": c["verdict"] == "fired"})
    if ccc and hy:
        hy_rise = hy[-1][1] - hy[-21][1]
        watch.append({"name": "Weakest junk above 13.56 while all junk widens",
                      "now": f"The weakest junk is at {ccc[-1][1]:.2f}, and all junk is at {hy[-1][1]:.2f}, {'up' if hy_rise >= 0 else 'down'} {abs(hy_rise):.2f} in a month",
                      "as_of": ccc[-1][0].isoformat(), "fired": ccc[-1][1] > 13.56 and hy_rise > 0.25})

    add_charts(S, cards)
    fmt = {
        "fed": "{:.2f}%", "long": "{:.2f}%", "curve": "{:+.2f}", "loans": "{:+.1f}%",
        "leaders": "-{:.0f}%", "chips": "-{:.0f}%", "banks": "-{:.0f}%",
        "ccc": "{:.2f}", "hy": "{:.2f}", "cp": "{:.2f}", "window": "${:.1f}B", "repo": "${:.1f}B",
        "nfci": "{:+.2f}", "vix": "{:.1f}", "stress": "{:+.2f}", "yen": "{:.2f}", "jobs": "{:.2f}",
        "cuts": "{:+.2f}", "printing": "{:+.1f}%",
    }
    for c in cards:
        v = c["value"]
        if v is None:
            c["big"] = "No reading"
        elif c["id"] == "sofr":
            c["big"] = f"{v * 100:+.0f} bp"
        else:
            c["big"] = fmt.get(c["id"], "{}").format(v)
    return {
        "generated_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "fetched_utc": log.get("fetched_utc"),
        "failed": why_missing,
        "cards": cards, "watch": watch, "thesis": thesis,
    }



if __name__ == "__main__":
    if "--no-fetch" not in sys.argv:
        fetch_all()
    data = build()
    with open(OUT, "w") as fh:
        json.dump(data, fh, separators=(",", ":"))
    print("wrote crash.json:", {c["id"]: c["verdict"] for c in data["cards"]})
