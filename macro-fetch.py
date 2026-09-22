# Macro figures for the Debt page, all from the IMF DataMapper API (World Economic Outlook + Global Debt Database).
# A browser can't call the IMF directly, so the Intel workflow writes macro.json. The IMF updates twice a year,
# so this only refetches when macro.json is more than a week old.
import json, os, subprocess, time
from datetime import datetime, timezone
def get(path):
    raw = subprocess.run(["curl", "-s", "--http1.1", "--max-time", "120", "--retry", "2",
                          f"https://www.imf.org/external/datamapper/api/v1/{path}"], capture_output=True, text=True).stdout
    return json.loads(raw[:raw.rfind("}") + 1])
try:
    old = json.load(open("macro.json"))
    if old.get("imf") and time.time() - datetime.fromisoformat(old["updated"].replace("Z", "+00:00")).timestamp() < 7 * 86400:
        print("macro.json is fresh, skipping"); raise SystemExit(0)
except (FileNotFoundError, ValueError, KeyError):
    pass
C = set(get("countries")["countries"])
vals = lambda ind: get(ind)["values"][ind]
G, Y, P, L = vals("GGXWDG_NGDP"), vals("NGDPD"), vals("PVD_LS"), vals("LP")
yr = datetime.now(timezone.utc).year
def world(ratio, y):
    s = g = 0.0; n = 0
    for c in C:
        r, v = ratio.get(c, {}).get(str(y)), Y.get(c, {}).get(str(y))
        if r is not None and v is not None: s += r / 100 * v * 1e9; g += v * 1e9; n += 1
    return s, g, n
gov = {}
for y in (yr - 2, yr - 1, yr, yr + 1):
    s, g, n = world(G, y); gov[str(y)] = {"usd": s, "gdp_covered": g, "countries": n}
py = max(int(y) for c in C for y in P.get(c, {}))                       # latest private-debt year
ps, pg, pn = world(P, py)
out = {
    "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "imf": True,
    "gdp": {"date": str(yr), "usd": Y["USA"][str(yr)] * 1e9, "note": "IMF World Economic Outlook, current-year projection"},
    "pop": {"date": str(yr), "people": L["USA"][str(yr)] * 1e6},
    "us_gov_ratio": G["USA"].get(str(yr)),
    "world": {
        "gdp": {y: Y["WEOWORLD"][y] * 1e9 for y in (str(yr - 2), str(yr - 1), str(yr), str(yr + 1)) if y in Y["WEOWORLD"]},
        "pop": sum(L[c][str(yr)] for c in C if c in L and str(yr) in L[c]) * 1e6,
        "gov": gov,
        "private": {"year": py, "usd": ps, "gdp_covered": pg, "countries": pn},
    },
}
json.dump(out, open("macro.json", "w"), separators=(",", ":"))
print("gov", {k: round(v["usd"] / 1e12, 1) for k, v in gov.items()}, "private", py, round(ps / 1e12, 1), "US GDP", round(out["gdp"]["usd"] / 1e12, 2))
