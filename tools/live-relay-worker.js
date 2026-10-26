// Relay for Burgos Bus's live data, deployed as the Cloudflare Worker "burgos-bus-api"
// (https://burgos-bus-api.filiplung.workers.dev). The city's bus-tracking server only lets its own
// Bonobur site read its data, so the page fetches it through here instead.
// To change it: Cloudflare dashboard -> Workers & Pages -> burgos-bus-api -> Edit code, paste, Deploy.
//
// The city's server sometimes answers slowly, with an error, or with something that isn't JSON. The
// relay then tries once more, and if that fails too it hands back the last good answer it has for the
// same address (up to a minute old) rather than an error, so a hiccup doesn't make the live buses
// disappear. Every answer carries the page's permission header, errors included; without it the
// browser hides the reply and the page can't tell what happened.
// The X-Relay header says which it was: fresh, stale (last good answer) or error; X-Relay-Age says how
// many seconds old a stale answer is, so arrival countdowns can allow for it.
const API = "https://bonoburservicios.aytoburgos.es/app/api";
const ALLOWED_ORIGINS = ["https://realcreate.github.io"];
const TIMEOUT_MS = 6000;      // per try
const STALE_MAX_S = 60;       // how old a kept answer may be when the city's server fails
// The last good answer for each address. Kept in the Worker's memory: Cloudflare's cache store does
// nothing on a workers.dev address. Memory lasts as long as Cloudflare keeps this copy of the Worker
// running, which is usually long enough to cover a hiccup.
const kept = new Map();       // path -> {text, at}

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
    if (!/^\/[A-Za-z0-9\/_-]+$/.test(path)) {
      return new Response("Not found", { status: 404, headers: cors });
    }

    // Live data: cache 5 s (so many users don't hammer the city's server). Routes/lines: cache 1 day.
    const live = path.toLowerCase().startsWith("/estadobus/");
    const reply = (body, relay, upstreamStatus, status = 200, age = 0) => new Response(body, {
      status,
      headers: {
        ...cors,
        "Content-Type": "application/json; charset=utf-8",
        "Cache-Control": live && status === 200 ? "no-store" : status === 200 ? "public, max-age=3600" : "no-store",
        "X-Relay": relay,
        "X-Upstream-Status": String(upstreamStatus),
        "X-Relay-Age": String(age),   // seconds since the city sent this answer (for a stale one)
      },
    });

    let lastStatus = 0;
    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        const upstream = await fetch(API + path, {
          headers: { Accept: "application/json" },
          signal: AbortSignal.timeout(TIMEOUT_MS),
          // only good answers are cached: an error must not be handed out for the next 5 s
          cf: { cacheTtlByStatus: live ? { "200-299": 5, "300-599": 0 } : { "200-299": 86400, "300-599": 0 }, cacheEverything: true },
        });
        lastStatus = upstream.status;
        if (upstream.ok) {
          const text = await upstream.text();
          JSON.parse(text);   // throws if it isn't JSON (an error page, say)
          if (live) {
            kept.set(path, { text, at: Date.now() });
            if (kept.size > 500) kept.delete(kept.keys().next().value);
          }
          return reply(text, "fresh", upstream.status);
        }
      } catch (e) {
        // timed out, connection failed, or not JSON: try again, then fall back
      }
    }

    if (live) {
      const k = kept.get(path), age = k ? Math.round((Date.now() - k.at) / 1000) : 0;
      if (k && age <= STALE_MAX_S) return reply(k.text, "stale", lastStatus, 200, age);
    }
    return reply(JSON.stringify({ error: "upstream unavailable" }), "error", lastStatus, 502);
  },
};
