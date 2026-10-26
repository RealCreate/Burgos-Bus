// Relay for Burgos Bus's live data, deployed as the Cloudflare Worker "burgos-bus-api"
// (https://burgos-bus-api.filiplung.workers.dev). The city's bus-tracking server only lets its own
// Bonobur site read its data, so the page fetches it through here instead.
// To change it: Cloudflare dashboard -> Workers & Pages -> burgos-bus-api -> Edit code, paste, Deploy.
const API = "https://bonoburservicios.aytoburgos.es/app/api";
const ALLOWED_ORIGINS = ["https://realcreate.github.io"];

export default {
  async fetch(request) {
    const origin = request.headers.get("Origin");
    const cors = {
      "Access-Control-Allow-Origin": ALLOWED_ORIGINS.includes(origin) ? origin : ALLOWED_ORIGINS[0],
      "Access-Control-Allow-Methods": "GET, OPTIONS",
      "Vary": "Origin",
    };

    if (request.method === "OPTIONS") return new Response(null, { headers: cors });
    if (request.method !== "GET") return new Response("Method not allowed", { status: 405, headers: cors });

    const path = new URL(request.url).pathname;
    // Only simple API paths like /estadobus/obtenervehiculosenlinea/05
    if (!/^\/[A-Za-z0-9\/_-]+$/.test(path)) {
      return new Response("Not found", { status: 404, headers: cors });
    }

    // Live data: cache 5 s (so many users don't hammer the city's server).
    // Routes/lines: cache 1 day.
    const live = path.toLowerCase().startsWith("/estadobus/");
    const upstream = await fetch(API + path, {
      headers: { Accept: "application/json" },
      cf: { cacheTtl: live ? 5 : 86400, cacheEverything: true },
    });

    return new Response(await upstream.text(), {
      status: upstream.status,
      headers: {
        ...cors,
        "Content-Type": "application/json; charset=utf-8",
        "Cache-Control": live ? "no-store" : "public, max-age=3600",
      },
    });
  },
};
