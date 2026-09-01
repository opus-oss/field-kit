# Fast quotes — your own free Cloudflare Worker

The market pages pull quotes through public CORS relays, which are slow and flaky
(corsproxy.io started requiring auth, allorigins is intermittent). A tiny Cloudflare
Worker — free, yours, on Cloudflare's edge — makes quotes fast and reliable. ~5 minutes,
all in the browser, no card required.

## 1. Deploy the Worker
1. Sign in / sign up at **https://dash.cloudflare.com** (free).
2. Left sidebar → **Workers & Pages** → **Create** → **Create Worker**.
3. Give it a name (e.g. `field-kit-proxy`) → **Deploy** (this deploys a placeholder).
4. Click **Edit code** → select-all and delete the placeholder → paste the entire
   contents of **`worker.js`** from this repo → **Deploy**.
5. Copy the Worker's URL from the top of the page — it looks like
   `https://field-kit-proxy.YOURNAME.workers.dev`.

## 2. Point the app at it
1. Open **Market Board** → tap the **⚡** button in the top bar.
2. Paste your Worker URL → **OK**. The page reloads; quotes now go through your proxy
   (it's tried first, with the free relays kept as a fallback). The ⚡ turns amber when set.

To revert to the free relays, tap **⚡** again and clear the field.

## Notes
- The Worker only proxies a small allowlist of hosts (Yahoo Finance, GitHub raw), so it
  can't be abused as an open proxy if someone finds the URL.
- Free tier is 100,000 requests/day — you'll never come close.
- The same Worker URL works for the Watch Deck too if you set it there.
