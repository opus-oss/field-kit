# GDP (quarterly, seasonally adjusted annual rate) and population (monthly) for the Debt page's ratios.
# FRED's public graph CSVs need no key but can't be read from a browser, so the Intel workflow writes macro.json.
import csv, io, json, subprocess, time
def series(sid):
    raw = subprocess.run(["curl", "-s", "--http1.1", "--max-time", "120", "--retry", "2", "-A", "FieldKit/1.0",
                          f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}"], capture_output=True, text=True).stdout
    rows = [r for r in csv.reader(io.StringIO(raw)) if r and r[0][:1].isdigit() and len(r) > 1 and r[1] not in ("", ".")]
    return rows[-1][0], float(rows[-1][1])
out = {"updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
try: d, v = series("GDP");    out["gdp"] = {"date": d, "usd": v * 1e9, "note": "nominal GDP, seasonally adjusted annual rate"}
except Exception as e: print("GDP failed:", e)
try: d, v = series("POPTHM"); out["pop"] = {"date": d, "people": v * 1e3}
except Exception as e: print("population failed:", e)
if "gdp" in out or "pop" in out:
    json.dump(out, open("macro.json", "w"), separators=(",", ":")); print(out)
