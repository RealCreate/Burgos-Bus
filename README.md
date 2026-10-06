# Burgos Bus

A phone-friendly guide to the Burgos urban bus network (SMyT): every line in its official colour, each direction with all its stops, scheduled times at every stop, departure boards, and a street map of the routes.

Live site: https://realcreate.github.io/Burgos-Bus/

## What it does

- **Lines**: pick a line and direction to see every stop in order, the next buses at each stop, and where buses should be right now.
- **Stops**: search by name or stop code for a departure board of every line.
- **Map**: routes over a street map of Burgos. Tap a stop to see which lines stop there and their next times. The location button finds your nearest stop.
- Favourite lines and stops are saved on your device.

Bus positions are estimated from the timetable. Burgos does not currently publish a public live GPS feed.

## Updating the timetable

The city publishes timetables as a GTFS file that covers a few months at a time. When it runs out:

1. Download https://www.aytoburgos.es/GTFS/Google_transit.zip
2. Run `python3 tools/build_site.py Google_transit.zip`
3. Commit the new `index.html`.

## Data

Timetables: Ayuntamiento de Burgos open data (GTFS). Map: © OpenStreetMap contributors, © CARTO.
