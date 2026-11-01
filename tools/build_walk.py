"""Build map/walk.json, the walking network the route planner measures walks on, from the site's map file.

Walks used to be measured as the crow flies (times a detour factor), which sends you across rivers, rail
lines, fenced sites and the airport. This keeps every street, path, footway, square and steps of the map
(not motorways, rail or runways), joined where they meet, with straight runs merged so the file stays small.

Output: {"lat": [...], "lon": [...], "e": [...]}
  lat/lon: node positions in 1e-5 degrees, each stored as the difference from the previous node
  e:       edges as pairs of node numbers, each number stored as the difference from the edge's first node
           of the previous pair (lengths are worked out in the app from the positions)

Usage:  python3 tools/build_walk.py [map/burgos.pmtiles] [index.html] [map/walk.json]
Needs:  pip install pmtiles
"""
import json, math, os, re, sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import match_shapes as M  # noqa: E402

NO_WALK = {'rail', 'ferry', 'aerialway', 'aeroway'}
NO_WALK_DETAIL = {'motorway', 'motorway_link', 'runway', 'taxiway'}


class WalkRoads(M.Roads):
    def add_tile(self, tx, ty, data):
        x0, y0 = tx * 4096, ty * 4096
        for props, lines in M.road_lines(data):
            if props.get('kind') in NO_WALK or props.get('kind_detail') in NO_WALK_DETAIL: continue
            for line in lines:
                pts = [(x0 + x, y0 + y) for x, y in line]
                for a, b in zip(pts, pts[1:]):
                    c = M.clip(a, b, x0, y0, x0 + 4096, y0 + 4096)
                    if not c: continue
                    u, v = self.node(*c[0]), self.node(*c[1])
                    if u == v: continue
                    d = math.dist(self.xy[u], self.xy[v])
                    self.adj[u][v] = self.adj[v][u] = d
                    self.L[(min(u, v), max(u, v))] = d
                    if props.get('is_bridge') or props.get('is_tunnel'):
                        self.level[(min(u, v), max(u, v))] = 'bridge' if props.get('is_bridge') else 'tunnel'


def off_line(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]; L2 = dx * dx + dy * dy
    t = max(0, min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2)) if L2 else 0
    return math.dist(p, (a[0] + t * dx, a[1] + t * dy))


def main(pm, page, out_path):
    html = open(page, encoding='utf-8').read()
    D = json.loads(re.search(r'id="gtfs">(.*?)</script>', html, re.S).group(1).replace('<\\/', '</'))
    lats = [s[2] for s in D['stops']]; lons = [s[3] for s in D['stops']]
    bbox = (min(lats) - .02, min(lons) - .025, max(lats) + .02, max(lons) + .025)  # the bus network + ~2 km
    R = WalkRoads(pm, bbox)
    adj = {n: set(v) for n, v in R.adj.items() if v}
    # keep the biggest connected piece, plus any piece of 30+ nodes (an estate reached by a path the map lacks)
    seen, keep = set(), set()
    for n in adj:
        if n in seen: continue
        comp, st = [], [n]; seen.add(n)
        while st:
            u = st.pop(); comp.append(u)
            for v in adj[u]:
                if v not in seen: seen.add(v); st.append(v)
        if len(comp) >= 30: keep.update(comp)
    # merge straight runs: drop a node joining exactly two pieces when it lies within 2.5 m of the line
    # between its neighbours (lengths barely change, the drawn walk still follows the street)
    xy = R.xy
    changed = True
    while changed:
        changed = False
        for n in list(keep):
            nb = adj.get(n)
            if not nb or len(nb) != 2: continue
            a, b = tuple(nb)
            if b in adj[a] or off_line(xy[n], xy[a], xy[b]) > 2.5: continue
            adj[a].discard(n); adj[b].discard(n); adj[a].add(b); adj[b].add(a); del adj[n]; keep.discard(n); changed = True
    nodes = sorted(keep, key=lambda n: (round(M.unm(*xy[n])[0], 3), M.unm(*xy[n])[1]))
    ix = {n: i for i, n in enumerate(nodes)}
    lat, lon, pl, po = [], [], 0, 0
    for n in nodes:
        a, o = M.unm(*xy[n]); a, o = round(a * 1e5), round(o * 1e5)
        lat.append(a - pl); lon.append(o - po); pl, po = a, o
    edges = sorted({(min(ix[u], ix[v]), max(ix[u], ix[v])) for u in nodes for v in adj[u] if v in ix})
    e, prev = [], 0
    for u, v in edges: e += [u - prev, v - u]; prev = u
    with open(out_path, 'w') as f:
        json.dump({'lat': lat, 'lon': lon, 'e': e}, f, separators=(',', ':'))
    print('walk network: %d nodes, %d links, %d KB' % (len(nodes), len(edges), os.path.getsize(out_path) // 1024))


if __name__ == '__main__':
    a = sys.argv[1:]
    main(a[0] if a else os.path.join(HERE, '..', 'map', 'burgos.pmtiles'),
         a[1] if len(a) > 1 else os.path.join(HERE, '..', 'index.html'),
         a[2] if len(a) > 2 else os.path.join(HERE, '..', 'map', 'walk.json'))
