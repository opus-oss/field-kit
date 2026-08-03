# Field Kit suite — GitHub Pages setup

Three installable, offline-capable web apps: **Field Kit** (weather), **Watch Deck** (markets), **Scrub** (link cleaner). You can do this entirely in the browser — no git, no terminal.

## 1. Put the files in a repo (browser only)

1. Go to <https://github.com/new>. Name the repo `field-kit` (any name works — it becomes part of the URL). Set it **Public**. Create.
2. On the empty repo page, click **uploading an existing file**.
3. Drag in **everything in this folder** — all `.html`, `.png`, `.webmanifest`, `sw.js`, and the `.nojekyll` file. (If the `.nojekyll` file is hard to select, it's fine to create it on GitHub: "Add file → Create new file", name it `.nojekyll`, leave it empty, commit.)
4. Click **Commit changes**.

## 2. Turn on Pages

1. In the repo: **Settings → Pages**.
2. Under **Source**, pick **Deploy from a branch** → branch **main** → folder **/ (root)** → **Save**.
3. Wait ~1 minute. The page shows your live URL: `https://YOURNAME.github.io/field-kit/`.

## 3. Install on your phone

Open these on your phone, then use the browser menu → **Add to Home Screen** for each you want as an app:

- Field Kit — `https://YOURNAME.github.io/field-kit/` (or `/index.html`)
- Watch Deck — `https://YOURNAME.github.io/field-kit/tickers.html`
- Scrub — `https://YOURNAME.github.io/field-kit/scrub.html`

Each installs as its own icon and launches full-screen. After the first visit, the app shell is cached — it **opens with no signal**. (Weather/market data still needs a connection; Scrub and the sun/moon math work fully offline.)

## Updating later

Change a file → in the repo, open that file → pencil (Edit) or re-upload → Commit. Pages redeploys in ~1 min.

**Important:** whenever you change any app file, also bump the cache version in `sw.js` — open it, change `const CACHE = 'fieldkit-v1'` to `'fieldkit-v2'` (then `v3`, etc.). That's the signal that tells already-installed phones to pull the new version instead of serving the old cached one.

## What's in the folder

| File | What it is |
|---|---|
| `index.html` | Field Kit (weather) — this is the landing page |
| `tickers.html` | Watch Deck (markets) |
| `scrub.html` | Scrub (link cleaner) |
| `sw.js` | Service worker — the offline caching |
| `manifest-*.webmanifest` | Install metadata for each app |
| `*-192/512/180.png` | App icons |
| `.nojekyll` | Tells GitHub Pages to serve files as-is |
