# Fetches the pollen.com forecast for one ZIP per 3-digit prefix (zipgrid.json) and writes pollen.json.
# pollen.com only answers with its own referrer set, so this runs in the repo cron, not the browser.
import json, time, urllib.request, concurrent.futures as cf
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/128 Safari/537.36"
def fetch(zip_):
    req = urllib.request.Request(f"https://www.pollen.com/api/forecast/current/pollen/{zip_}",
        headers={"Referer": "https://www.pollen.com/", "User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r).get("Location") or {}
def region(cell):
    for z in cell["zips"]:
        try:
            L = fetch(z)
        except Exception:
            continue
        if not L.get("periods"):
            return None                     # outside pollen.com coverage (AK, HI, PR)
        return {"z": z, "loc": L.get("DisplayLocation", ""), "lat": cell["lat"], "lon": cell["lon"],
                "p": [[q.get("Type"), round(q.get("Index") or 0, 1), [t.get("Name") for t in q.get("Triggers", [])]]
                      for q in L["periods"]]}
    return None
grid = json.load(open("zipgrid.json"))
t = time.time()
with cf.ThreadPoolExecutor(4) as ex:
    regions = [r for r in ex.map(region, grid) if r]
print(f"{len(regions)} of {len(grid)} prefixes in {time.time()-t:.0f}s")
if len(regions) < 200:
    raise SystemExit("too few regions fetched, keeping the previous pollen.json")
json.dump({"updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "regions": regions},
          open("pollen.json", "w"), separators=(",", ":"))
