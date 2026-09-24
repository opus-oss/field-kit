#!/usr/bin/env python3
"""Builds raas.json for the Grapevine RaaS Volume widget.

ransomware.live's public API is fine from the shell, but its CORS headers block browsers
loading Chef from GitHub Pages. So a repo cron fetches the recent-victims list and commits
the resulting JSON, and the page reads that. Same pattern as pollen.json / congress.json.

Public source: https://api.ransomware.live/v2/recentvictims — leak-site claims, not
confirmed encryptions. Groups pad, dupe and reclaim.
"""
import json, os, sys, time, urllib.request

SRC = "https://api.ransomware.live/v2/recentvictims"
OUT = "raas.json"
MAX_AGE_H = 6                                  # leave a recent file alone
FIELDS = ("group_name", "group", "activity", "country", "attackdate", "post_title", "victim")


def fresh():
    try:
        with open(OUT) as f:
            j = json.load(f)
        return time.time() - j.get("generated", 0) < MAX_AGE_H * 3600
    except Exception:
        return False


def load_old():
    try:
        with open(OUT) as f:
            j = json.load(f)
        return j.get("items", [])
    except Exception:
        return []


def main():
    if fresh() and "--force" not in sys.argv:
        print("raas.json is recent, skipping")
        return
    req = urllib.request.Request(SRC, headers={"User-Agent": "field-kit"})
    raw = json.load(urllib.request.urlopen(req, timeout=90))
    # ransomware.live caps recentvictims at 100. Accumulate across cron runs so the file
    # eventually covers a full 90-day window, deduped by post_url or (group, victim, date).
    merged = {}
    def key(v):
        return v.get("post_url") or f"{v.get('group_name') or v.get('group','')}::{v.get('post_title') or v.get('victim','')}::{v.get('attackdate','')}"
    for v in load_old():
        merged[key(v)] = v
    for v in raw:
        row = {k: v[k] for k in FIELDS if v.get(k)}
        if not row.get("attackdate"):
            continue
        for k in ("post_url", "published", "discovered"):
            if v.get(k):
                row[k] = v[k]
        merged[key(row)] = row
    # Drop anything past 120 days to keep the file bounded, and sort newest first.
    cut = time.time() - 120 * 86400
    items = [v for v in merged.values() if _epoch(v.get("attackdate", "")) >= cut]
    items.sort(key=lambda v: _epoch(v.get("attackdate", "")), reverse=True)
    out = {"generated": int(time.time()), "source": SRC, "count": len(items), "items": items}
    with open(OUT, "w") as f:
        json.dump(out, f, separators=(",", ":"))
    fresh_ct = sum(1 for v in raw if v.get("attackdate"))
    print(f"raas.json: {len(items)} claims total ({fresh_ct} new from this fetch), {os.path.getsize(OUT)//1024} KB")


def _epoch(s):
    try:
        s = s.replace(" ", "T").split(".")[0]
        return time.mktime(time.strptime(s[:19], "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return 0


if __name__ == "__main__":
    main()
