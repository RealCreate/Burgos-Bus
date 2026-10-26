// Relay for Burgos Bus's live data, deployed as the Cloudflare Worker "burgos-bus-api"
// (https://burgos-bus-api.filiplung.workers.dev). The city's bus-tracking server only lets its own
// Bonobur site read its data, so the page fetches it through here instead.
// To change it: Cloudflare dashboard -> Workers & Pages -> burgos-bus-api -> Edit code, paste, Deploy.
//
// Two kinds of request:
//   /estadobus/...                    one answer from the city, passed through (e.g. a stop's arrivals)
//   /batch/lines/01_02_C1             the buses on several lines in one go:
//                                     {"lines": {"01": {"age": 0, "data": [...]}, "02": null, ...}}
//                                     (null for a line the city couldn't answer)
// The batch means the page makes one request per round instead of one per line, which keeps it well
// inside Cloudflare's free 100,000 requests a day.
//
// Answers are shared between everyone using the site: an answer from the city is reused for 5 seconds,
// and if several people ask for the same thing at once only one request goes to the city. Shared
// answers live in the Worker's memory (Cloudflare's cache store does nothing on a workers.dev address),
// so they are shared by everyone this copy of the Worker is serving, which for a small group is
// usually everyone.
//
// The city's server sometimes answers slowly, with an error, or with something that isn't JSON. The
// relay then tries once more, and if that fails too it hands back the last good answer it has (up to a
// minute old) rather than an error, so a hiccup doesn't make the live buses disappear. Every answer
// carries the page's permission header, errors included; without it the browser hides the reply.
// Headers: X-Relay = fresh / stale (last good answer) / error; X-Relay-Age = how many seconds old the
// answer is, so arrival countdowns can allow for it.
const API = "https://bonoburservicios.aytoburgos.es/app/api";
const ALLOWED_ORIGINS = ["https://realcreate.github.io"];
const TIMEOUT_MS = 6000;      // per try
const SHARE_S = 5;            // an answer from the city is reused for this long
const STALE_MAX_S = 60;       // how old a kept answer may be when the city's server fails
const BATCH_MAX = 40;         // lines in one batch request
const BATCH_PARALLEL = 4;     // requests to the city at a time for a batch (bursts made it drop some)
const SUBREQ_BUDGET = 45;     // Cloudflare's free plan allows 50 outgoing requests per incoming one

const kept = new Map();       // path -> {text, at}: the last good answer from the city
const pending = new Map();    // path -> promise: a request to the city already on its way

// One answer from the city for path, shared and with retries: {text, relay, status, age} or null.
async function fromCity(path, live, budget) {
  const k = kept.get(path), now = Date.now();
  if (k && live && now - k.at < SHARE_S * 1000) {
    return { text: k.text, relay: "fresh", status: 200, age: Math.round((now - k.at) / 1000) };
  }
  if (pending.has(path)) return pending.get(path);
  const job = (async () => {
    let status = 0;
    for (let attempt = 0; attempt < 2 && budget.left > 0; attempt++) {
      budget.left--;
      try {
        const r = await fetch(API + path, {
          headers: { Accept: "application/json" },
          signal: AbortSignal.timeout(TIMEOUT_MS),
          cf: live ? { cacheTtl: 0 } : { cacheTtlByStatus: { "200-299": 86400, "300-599": 0 }, cacheEverything: true },
        });
        status = r.status;
        if (r.ok) {
          const text = await r.text();
          JSON.parse(text);   // throws if it isn't JSON (an error page, say)
          if (live) {
            kept.set(path, { text, at: Date.now() });
            if (kept.size > 500) kept.delete(kept.keys().next().value);
          }
          return { text, relay: "fresh", status, age: 0 };
        }
      } catch (e) {
        // timed out, connection failed, or not JSON: try again, then fall back
      }
    }
    const k2 = kept.get(path), age = k2 ? Math.round((Date.now() - k2.at) / 1000) : 0;
    if (live && k2 && age <= STALE_MAX_S) return { text: k2.text, relay: "stale", status, age };
    return { text: null, relay: "error", status, age: 0 };
  })();
  pending.set(path, job);
  try { return await job; } finally { pending.delete(path); }
}

export default {
  async fetch(request) {
    const origin = request.headers.get("Origin");
    const cors = {
      "Access-Control-Allow-Origin": ALLOWED_ORIGINS.includes(origin) ? origin : ALLOWED_ORIGINS[0],
      "Access-Control-Allow-Methods": "GET, OPTIONS",
      "Access-Control-Expose-Headers": "X-Relay, X-Relay-Age, X-Upstream-Status",
      "Vary": "Origin",
    };
    if (request.method === "OPTIONS") return new Response(null, { headers: cors });
    if (request.method !== "GET") return new Response("Method not allowed", { status: 405, headers: cors });

    const path = new URL(request.url).pathname;
    // Only simple API paths like /estadobus/obtenervehiculosenlinea/05
    if (!/^\/[A-Za-z0-9\/_-]+$/.test(path)) return new Response("Not found", { status: 404, headers: cors });
    const json = (body, status, extra) => new Response(body, {
      status,
      headers: { ...cors, "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store", ...extra },
    });
    const budget = { left: SUBREQ_BUDGET };

    // several lines' buses in one go
    const batch = path.match(/^\/batch\/lines\/([A-Za-z0-9_]+)$/);
    if (batch) {
      const codes = [...new Set(batch[1].split("_").filter(Boolean))].slice(0, BATCH_MAX);
      const out = {};
      let next = 0;
      const worker = async () => {
        while (next < codes.length) {
          const c = codes[next++];
          const a = await fromCity("/estadobus/obtenervehiculosenlinea/" + c, true, budget);
          out[c] = a && a.text != null ? { age: a.age, data: JSON.parse(a.text) } : null;
        }
      };
      await Promise.all(Array.from({ length: Math.min(BATCH_PARALLEL, codes.length) }, worker));
      return json(JSON.stringify({ lines: out }), 200, { "X-Relay": "batch" });
    }

    // one answer from the city
    const live = path.toLowerCase().startsWith("/estadobus/");
    const a = await fromCity(path, live, budget);
    if (a.text == null) {
      return json(JSON.stringify({ error: "upstream unavailable" }), 502, { "X-Relay": "error", "X-Upstream-Status": String(a.status) });
    }
    return json(a.text, 200, {
      "X-Relay": a.relay, "X-Relay-Age": String(a.age), "X-Upstream-Status": String(a.status),
      ...(live ? {} : { "Cache-Control": "public, max-age=3600" }),
    });
  },
};
