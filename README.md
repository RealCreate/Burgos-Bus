# Burgos Bus

A phone-friendly guide to the Burgos urban bus network (SMyT): every line in its official colour, each direction with all its stops, scheduled times at every stop, departure boards, and a street map of the routes.

Live site: https://realcreate.github.io/Burgos-Bus/

## What it does

- **Lines**: pick a line and direction to see every stop in order, the next buses at each stop, and where buses should be right now.
- **Stops**: search by name or stop code for a departure board of every line.
- **Map**: routes over a street map of Burgos. Tap a stop to see which lines stop there and their next times. The location button finds your nearest stop.
- Favourite lines and stops are saved on your device.
- **Links**: the address always matches what's open (`#stop=PA00305`, `#line=05&dir=1`), and the share button sends it, so you can share or bookmark a stop or line.
- The last bus of the day on each line and direction is labelled, and once the location button has found you, lines open scrolled to your nearest stop.
- **Install it**: in Safari tap Share → Add to Home Screen. It opens full-screen with the bus icon, and lines, stops and timetables keep working offline, and so do the parts of the street map you've already looked at.
- **Live buses**: buses on the map and on a line's stop list show their real GPS position, and stops show live arrival times (minutes, distance and bus number), from the city's bus tracking. Live buses have a green ring on the map. When live data isn't available, positions and countdowns are estimated from the timetable as before.

## Live data

Live positions and arrival times come from the city's bus tracking, the same data its Bonobur app uses (`https://bonoburservicios.aytoburgos.es/app/api`). It isn't a published open-data feed, so it may change or stop working without notice; the site then falls back to the timetable by itself.

The city's server only lets its own website read that data, so the site fetches it through a small relay: a Cloudflare Worker at `https://burgos-bus-api.filiplung.workers.dev`. Its code is in `tools/live-relay-worker.js`; to change it, open the Worker in the Cloudflare dashboard (Workers & Pages → burgos-bus-api → Edit code), paste the file and deploy. It only answers this site, and caches live data for 5 seconds so the city's server sees no more traffic than from its own app.

What the site uses (paths after the relay address):

- `/estadobus/obtenervehiculosenlinea/{line}`: GPS position of every bus on a line, e.g. `05` or `C1`
- `/estadobus/ObtenerTiemposRutaPorParada/{stop}`: next buses at a stop, by stop number (`PA00500` → `500`)

## Updating the timetable

The city publishes timetables as a GTFS file that covers a few months at a time. Its server only answers from Spain, so GitHub can't fetch it by itself. To update:

1. On your phone or iPad, download https://www.aytoburgos.es/GTFS/Google_transit.zip
2. On github.com, open the `gtfs` folder of this repository, tap **Add file → Upload files**, pick the zip and commit.
3. A GitHub Action (`.github/workflows/update-timetable.yml`) rebuilds the site from it and publishes it within a minute or two, but only if the timetable actually changed. A file that looks broken (empty, or ending earlier than the current one) is never published; the run fails and GitHub emails you.

The same Action also tries to download the file every Monday, in case the city's server ever opens up.

To rebuild by hand instead: `python3 tools/build_site.py Google_transit.zip`, then commit `index.html`.

After changing only `tools/template.html` or `tools/i18n.js`, run `python3 tools/build_site.py --keep-data` to rebuild with the timetable already in `index.html`.

## Data

Timetables: Ayuntamiento de Burgos open data (GTFS). Map: © OpenStreetMap contributors, © CARTO.
