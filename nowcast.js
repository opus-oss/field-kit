/* Field Kit nowcast.
   Projects the latest real radar forward using motion measured between the last three radar frames, the way
   short-term radar forecasts work. Everything runs in the browser from keyless tiles (NOAA NEXRAD composite via the
   Iowa Environmental Mesonet, CORS open). Shared by radar.html (map projection) and index.html (rain at your spot). */
(function () {
  'use strict';
  const IEM = 'https://mesonet.agron.iastate.edu';
  const tile = (name, z, x, y) => `${IEM}/cache/tile.py/1.0.0/${name}/${z}/${x}/${y}.png`;
  const pad = (n, w) => String(n).padStart(w, '0');
  const stamp = d => d.getUTCFullYear() + pad(d.getUTCMonth() + 1, 2) + pad(d.getUTCDate(), 2) + pad(d.getUTCHours(), 2) + pad(d.getUTCMinutes(), 2);
  const lon2x = (lon, z) => (lon + 180) / 360 * 256 * 2 ** z;
  const lat2y = (lat, z) => { const s = Math.sin(lat * Math.PI / 180); return (0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI)) * 256 * 2 ** z; };
  const kmPerPx = (lat, z) => 40075.016686 * Math.cos(lat * Math.PI / 180) / (256 * 2 ** z);
  const CONUS = (lat, lon) => lat > 23.5 && lat < 50.5 && lon > -126 && lon < -65.5;   // the composite covers the lower 48

  function loadImg(url) {
    return new Promise(res => { const i = new Image(); i.crossOrigin = 'anonymous'; i.onload = () => res(i); i.onerror = () => res(null); i.src = url; });
  }
  async function mosaic(name, z, tx0, ty0, span) {
    const W = span * 256, c = document.createElement('canvas'); c.width = c.height = W;
    const ctx = c.getContext('2d', { willReadFrequently: true });
    const jobs = [];
    for (let dy = 0; dy < span; dy++) for (let dx = 0; dx < span; dx++)
      jobs.push(loadImg(tile(name, z, tx0 + dx, ty0 + dy)).then(img => { if (img) ctx.drawImage(img, dx * 256, dy * 256); return !!img; }));
    const got = (await Promise.all(jobs)).filter(Boolean).length;
    return { data: ctx.getImageData(0, 0, W, W).data, got };
  }

  /* ---- palette: weak returns are blue/cyan/gray, light rain starts at green ---- */
  const weak = (r, g, b) => { const mx = Math.max(r, g, b), mn = Math.min(r, g, b); return b >= g || (mx - mn < 40 && mx < 210); };
  function level(r, g, b) {
    if (r > 150 && b > 150 && g < 170) return 5;          // magenta / purple: intense, hail possible
    if (r > 235 && g > 235 && b > 235) return 5;          // white top of scale
    if (r > 200 && g < 80) return 4;                      // red: very heavy
    if (r > 200 && g < 175) return 3;                     // orange: heavy
    if (r > 180 && g >= 175) return 2;                    // yellow: moderate
    return g > 175 ? 1 : 2;                               // bright green light, dark green moderate
  }
  const WORD = ['', 'light', 'moderate', 'heavy', 'very heavy', 'intense, hail possible'];

  /* level map (0 = no meaningful rain) + optional clutter removal written back into the RGBA */
  function prepare(d, W, clean) {
    const n = W * W, lv = new Uint8Array(n);
    for (let k = 0, i = 0; k < n; k++, i += 4) {
      if (!d[i + 3]) continue;
      const r = d[i], g = d[i + 1], b = d[i + 2];
      if (weak(r, g, b)) { if (clean) d[i + 3] = 0; continue; }
      lv[k] = level(r, g, b);
    }
    if (clean) {                                           // despeckle: rain is contiguous, clutter is scattered
      const keep = new Uint8Array(n);
      for (let y = 0; y < W; y++) for (let x = 0; x < W; x++) {
        const k = y * W + x; if (!lv[k]) continue; let c = 0;
        for (let dy = -1; dy <= 1; dy++) { const yy = y + dy; if (yy < 0 || yy >= W) continue;
          for (let dx = -1; dx <= 1; dx++) { if (!dx && !dy) continue; const xx = x + dx; if (xx < 0 || xx >= W) continue; if (lv[yy * W + xx]) c++; } }
        if (c >= 3) keep[k] = 1;
      }
      for (let k = 0; k < n; k++) if (!keep[k]) { lv[k] = 0; d[k * 4 + 3] = 0; }
    }
    return lv;
  }
  function down(lv, W, f) {                               // average level on a coarse grid for motion matching
    const G = W / f, out = new Float32Array(G * G);
    for (let y = 0; y < W; y++) for (let x = 0; x < W; x++) out[((y / f) | 0) * G + ((x / f) | 0)] += lv[y * W + x];
    for (let i = 0; i < out.length; i++) out[i] /= f * f;
    return out;
  }

  /* block matching: for each block of frame B find the shift that best matches frame A; motion = A -> B */
  function flow(A, B, G) {
    const BS = 16, nb = (G / BS) | 0, R = 6, vx = new Float32Array(nb * nb), vy = new Float32Array(nb * nb), ok = new Uint8Array(nb * nb);
    for (let by = 0; by < nb; by++) for (let bx = 0; bx < nb; bx++) {
      const x0 = bx * BS, y0 = by * BS; let echo = 0;
      for (let y = y0; y < y0 + BS; y++) for (let x = x0; x < x0 + BS; x++) if (B[y * G + x] > 0.3) echo++;
      if (echo < 14) continue;
      let best = Infinity, bdx = 0, bdy = 0;
      for (let dy = -R; dy <= R; dy++) for (let dx = -R; dx <= R; dx++) {
        let s = 0.6 * (dx * dx + dy * dy);                 // mild preference for the smaller shift on ties
        for (let y = y0; y < y0 + BS && s < best; y++) {
          const ya = y - dy;
          for (let x = x0; x < x0 + BS; x++) {
            const xa = x - dx, a = (ya < 0 || ya >= G || xa < 0 || xa >= G) ? 0 : A[ya * G + xa];
            s += Math.abs(B[y * G + x] - a);
          }
        }
        if (s < best) { best = s; bdx = dx; bdy = dy; }
      }
      const b = by * nb + bx; vx[b] = bdx; vy[b] = bdy; ok[b] = 1;
    }
    return { vx, vy, ok, nb };
  }
  function field(f1, f2, G, fallback) {
    const nb = f1.nb, n = nb * nb, vx = new Float32Array(n), vy = new Float32Array(n), ok = new Uint8Array(n);
    for (let i = 0; i < n; i++) {
      if (f1.ok[i] && f2.ok[i]) { vx[i] = (f1.vx[i] + f2.vx[i]) / 2; vy[i] = (f1.vy[i] + f2.vy[i]) / 2; ok[i] = 1; }
      else if (f2.ok[i]) { vx[i] = f2.vx[i]; vy[i] = f2.vy[i]; ok[i] = 1; }
      else if (f1.ok[i]) { vx[i] = f1.vx[i]; vy[i] = f1.vy[i]; ok[i] = 1; }
    }
    let any = 0, mx = 0, my = 0; for (let i = 0; i < n; i++) if (ok[i]) { any++; mx += vx[i]; my += vy[i]; }
    const mean = any ? [mx / any, my / any] : (fallback || [0, 0]);
    // fill blocks with no rain from their neighbours, then fall back to the regional mean
    for (let it = 0; it < nb; it++) {
      let changed = 0; const nok = ok.slice();
      for (let y = 0; y < nb; y++) for (let x = 0; x < nb; x++) { const i = y * nb + x; if (ok[i]) continue;
        let c = 0, sx = 0, sy = 0;
        for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) { const yy = y + dy, xx = x + dx; if (yy < 0 || xx < 0 || yy >= nb || xx >= nb) continue; const j = yy * nb + xx; if (ok[j]) { c++; sx += vx[j]; sy += vy[j]; } }
        if (c) { vx[i] = sx / c; vy[i] = sy / c; nok[i] = 1; changed++; } }
      ok.set(nok); if (!changed) break;
    }
    for (let i = 0; i < n; i++) if (!ok[i]) { vx[i] = mean[0]; vy[i] = mean[1]; }
    for (let pass = 0; pass < 2; pass++) {                // smooth so the projection moves as a sheet, not confetti
      const sx = vx.slice(), sy = vy.slice();
      for (let y = 0; y < nb; y++) for (let x = 0; x < nb; x++) { let c = 0, ax = 0, ay = 0;
        for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) { const yy = y + dy, xx = x + dx; if (yy < 0 || xx < 0 || yy >= nb || xx >= nb) continue; const j = yy * nb + xx; c++; ax += sx[j]; ay += sy[j]; }
        vx[y * nb + x] = ax / c; vy[y * nb + x] = ay / c; }
    }
    // bilinear up to the coarse grid G
    const dxg = new Float32Array(G * G), dyg = new Float32Array(G * G), BS = G / nb;
    for (let y = 0; y < G; y++) for (let x = 0; x < G; x++) {
      const fx = Math.min(nb - 1, Math.max(0, (x + 0.5) / BS - 0.5)), fy = Math.min(nb - 1, Math.max(0, (y + 0.5) / BS - 0.5));
      const x0 = Math.floor(fx), y0 = Math.floor(fy), x1 = Math.min(nb - 1, x0 + 1), y1 = Math.min(nb - 1, y0 + 1), tx = fx - x0, ty = fy - y0;
      const lerp = (a, b, c, d) => (a * (1 - tx) + b * tx) * (1 - ty) + (c * (1 - tx) + d * tx) * ty;
      dxg[y * G + x] = lerp(vx[y0 * nb + x0], vx[y0 * nb + x1], vx[y1 * nb + x0], vx[y1 * nb + x1]);
      dyg[y * G + x] = lerp(vy[y0 * nb + x0], vy[y0 * nb + x1], vy[y1 * nb + x0], vy[y1 * nb + x1]);
    }
    return { dxg, dyg, mean, measured: any };
  }

  /* names of the latest frame and the frames 10 and 20 minutes before it */
  async function latest() {
    const r = await fetch(`${IEM}/data/gis/images/4326/USCOMP/n0q_0.json`, { cache: 'no-store' });
    const valid = new Date((await r.json()).meta.valid);
    const at = m => `ridge::USCOMP-N0Q-${stamp(new Date(valid - m * 60000))}`;
    return { valid, names: [at(20), at(10), 'nexrad-n0q-900913'] };
  }

  /* build the engine for a square of tiles around a point */
  async function build({ lat, lon, z = 7, span = 3, clean = true, frames, fallback }) {
    const fr = frames || await latest();
    const tx0 = Math.floor(lon2x(lon, z) / 256) - Math.floor(span / 2), ty0 = Math.floor(lat2y(lat, z) / 256) - Math.floor(span / 2);
    const [m20, m10, m0] = await Promise.all(fr.names.map(n => mosaic(n, z, tx0, ty0, span)));
    if (!m0.got) throw new Error('radar tiles unavailable');
    const W = span * 256, F = 4, G = W / F;
    const l20 = prepare(m20.data, W, clean), l10 = prepare(m10.data, W, clean), l0 = prepare(m0.data, W, clean);
    const d20 = down(l20, W, F), d10 = down(l10, W, F), d0 = down(l0, W, F);
    const fl = field(flow(d20, d10, G), flow(d10, d0, G), G, fallback);
    const src = m0.data, ox = tx0 * 256, oy = ty0 * 256;
    const eng = {
      z, span, W, tx0, ty0, valid: fr.valid, measured: fl.measured,
      bounds: { x0: ox, y0: oy, x1: ox + W, y1: oy + W },     // in world pixels at zoom z
      /* mean motion in the region, km/h and compass heading (toward) */
      motion() {
        const vx = fl.mean[0] * F, vy = fl.mean[1] * F, px = Math.hypot(vx, vy);
        const heading = (Math.atan2(vx, -vy) * 180 / Math.PI + 360) % 360;
        return { px10: px, heading };
      },
      /* draw the projection tau minutes ahead into an ImageData of size W x W */
      render(tau, img, fade = 1) {
        const k = tau / 10, d = img.data; d.fill(0);
        for (let y = 0; y < W; y++) { const gy = (y / F) | 0;
          for (let x = 0; x < W; x++) { const gi = gy * G + ((x / F) | 0);
            const sx = Math.round(x - fl.dxg[gi] * F * k), sy = Math.round(y - fl.dyg[gi] * F * k);
            if (sx < 0 || sy < 0 || sx >= W || sy >= W) continue;
            const si = (sy * W + sx) * 4; if (!src[si + 3]) continue;
            const di = (y * W + x) * 4; d[di] = src[si]; d[di + 1] = src[si + 1]; d[di + 2] = src[si + 2]; d[di + 3] = src[si + 3] * fade; } }
        return img;
      },
      /* strongest rain level near a point, tau minutes ahead (0 = now) */
      levelAt(lat, lon, tau) {
        const x = Math.round(lon2x(lon, z) - ox), y = Math.round(lat2y(lat, z) - oy);
        if (x < 2 || y < 2 || x >= W - 2 || y >= W - 2) return null;
        const gi = ((y / F) | 0) * G + ((x / F) | 0), k = tau / 10;
        const cx = Math.round(x - fl.dxg[gi] * F * k), cy = Math.round(y - fl.dyg[gi] * F * k);
        let best = 0;
        for (let dy = -2; dy <= 2; dy++) for (let dx = -2; dx <= 2; dx++) { const xx = cx + dx, yy = cy + dy; if (xx < 0 || yy < 0 || xx >= W || yy >= W) continue; best = Math.max(best, l0[yy * W + xx]); }
        return best;
      },
      /* the next hour at a point, in 5-minute steps */
      spot(lat, lon) {
        const s = []; for (let t = 0; t <= 60; t += 5) { const v = this.levelAt(lat, lon, t); if (v == null) return null; s.push({ t, v }); }
        return s;
      }
    };
    return eng;
  }

  const mins = m => { m = Math.round(m); if (m < 60) return `${m} min`; const h = Math.floor(m / 60), r = m % 60; return r ? `${h} h ${r} min` : `${h} h`; };
  /* plain-language summary of a spot series */
  function describe(s) {
    if (!s) return null;
    const now = s[0].v;
    if (now > 0) {
      let end = null; for (let i = 1; i < s.length - 1; i++) if (!s[i].v && !s[i + 1].v) { end = s[i].t; break; }
      const peak = Math.max(...s.slice(0, end == null ? s.length : s.findIndex(x => x.t === end)).map(x => x.v));
      return { tone: 'rain', text: end == null ? `Raining at your spot now, ${WORD[peak]}, and it keeps going through the next hour.` : `Raining at your spot now, ${WORD[peak]}. Likely letting up in about ${mins(end)}.` };
    }
    const i0 = s.findIndex(x => x.v > 0);
    if (i0 < 0) return { tone: 'dry', text: 'No rain expected at your spot in the next hour.' };
    let i1 = i0; while (i1 + 1 < s.length && s[i1 + 1].v > 0) i1++;
    const peak = Math.max(...s.slice(i0, i1 + 1).map(x => x.v)), dur = (i1 - i0 + 1) * 5, last = i1 === s.length - 1;
    return { tone: 'soon', text: `Rain likely in about ${mins(s[i0].t)}, ${WORD[peak]}${last ? ', lasting past the hour.' : `, for about ${mins(dur)}.`}` };
  }

  window.Nowcast = { build, latest, describe, WORD, CONUS, lon2x, lat2y, kmPerPx, weak, level, loadImg, tile, stamp };
})();
