"""Snap the bus routes onto the roads of the site's own map.

The city's timetable (GTFS) draws each route as a rough outline: it cuts corners, floats beside the
road and turns roundabouts into polygons. This matches every route onto the road network in
map/burgos.pmtiles (OpenStreetMap), the way a sat-nav puts a GPS trace on the road (a hidden Markov
model: points along the route are matched to nearby roads, and consecutive matches must be joined by a
short drive along the roads).

Distances along each route are kept on the timetable's scale, so where stops sit on a route, and where
buses are drawn between them, don't change. Wherever a stretch can't be matched sensibly (no road
nearby, or the only way along the roads is a long detour), the original outline is kept for that stretch.

Usage:  python3 tools/match_shapes.py DATA.json [map/burgos.pmtiles]
        (rewrites DATA.json's shapes in place; build_site.py runs this for you)
Needs:  pip install pmtiles
"""
import gzip, heapq, json, math, os, sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
Z = 15                      # zoom of the map tiles read (the most detailed in the file)
STEP = 20                   # metres between points of a route that are matched
RADIUS = 30                 # metres: roads further than this from a route point aren't candidates
SIGMA = 9                   # metres: how far a route point is typically drawn from its real road
BETA = 6                    # metres: how much longer than the straight line a drive may usually be
MAX_CANDS = 6
SKIP_KINDS = {'path', 'rail', 'ferry', 'aerialway', 'runway', 'taxiway'}
# One-way streets: driving them the wrong way costs this much more (not forbidden: buses use contraflow
# bus lanes, which the map data doesn't mark). This keeps routes on the right side of dual carriageways
# and going the right way round roundabouts.
WRONG_WAY = lambda d: d * 15 + 200


# ---------- reading the map file ----------

def varint(b, i):
    r = s = 0
    while True:
        c = b[i]; i += 1
        r |= (c & 0x7f) << s; s += 7
        if c < 0x80:
            return r, i


def fields(b):
    """(field number, wire type, value) for each field of a protobuf message"""
    i, n = 0, len(b)
    while i < n:
        k, i = varint(b, i); f, w = k >> 3, k & 7
        if w == 0:
            v, i = varint(b, i)
        elif w == 2:
            l, i = varint(b, i); v = b[i:i + l]; i += l
        elif w == 1:
            v = b[i:i + 8]; i += 8
        elif w == 5:
            v = b[i:i + 4]; i += 4
        else:
            raise ValueError('wire type %d' % w)
        yield f, w, v


def packed(b):
    i, out = 0, []
    while i < len(b):
        v, i = varint(b, i); out.append(v)
    return out


def value(b):
    import struct
    for f, w, v in fields(b):
        if f == 1: return v.decode('utf-8', 'replace')
        if f == 2: return struct.unpack('<f', v)[0]
        if f == 3: return struct.unpack('<d', v)[0]
        if f in (4, 5): return v
        if f == 6: return (v >> 1) ^ -(v & 1)
        if f == 7: return bool(v)


def road_lines(tile):
    """[(props, [[(x, y), ...], ...])] for the line features of the 'roads' layer, in tile units"""
    out = []
    for f, w, layer in fields(tile):
        if f != 3: continue
        name, feats, keys, vals, extent = None, [], [], [], 4096
        for g, _, v in fields(layer):
            if g == 1: name = v.decode()
            elif g == 2: feats.append(v)
            elif g == 3: keys.append(v.decode())
            elif g == 4: vals.append(value(v))
            elif g == 5: extent = v
        if name != 'roads': continue
        for fb in feats:
            tags, typ, geom = [], 0, []
            for g, _, v in fields(fb):
                if g == 2: tags = packed(v)
                elif g == 3: typ = v
                elif g == 4: geom = packed(v)
            if typ != 2: continue
            props = {keys[tags[k]]: vals[tags[k + 1]] for k in range(0, len(tags), 2)}
            lines, cur, x, y, i = [], None, 0, 0, 0
            while i < len(geom):
                cmd, cnt = geom[i] & 7, geom[i] >> 3; i += 1
                for _ in range(cnt):
                    if cmd in (1, 2):
                        dx, dy = geom[i], geom[i + 1]; i += 2
                        x += (dx >> 1) ^ -(dx & 1); y += (dy >> 1) ^ -(dy & 1)
                        if cmd == 1:
                            cur = [(x * 4096 / extent, y * 4096 / extent)]; lines.append(cur)
                        else:
                            cur.append((x * 4096 / extent, y * 4096 / extent))
            out.append((props, lines))
    return out


# ---------- geometry ----------

N = 4096 * 2 ** Z           # world size in map units at zoom Z
LAT0 = 42.345
KX, KY = 111320 * math.cos(math.radians(LAT0)), 110574


def world_to_ll(X, Y):
    lon = X / N * 360 - 180
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * Y / N))))
    return lat, lon


def ll_to_world(lat, lon):
    x = (lon + 180) / 360 * N
    s = math.sin(math.radians(lat))
    y = (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * N
    return x, y


def m(lat, lon):            # metres on a local flat grid around Burgos
    return (lon * KX, lat * KY)


def unm(x, y):
    return (y / KY, x / KX)


def clip(a, b, x0, y0, x1, y1):
    """the part of segment a-b inside the box (Liang-Barsky), or None"""
    t0, t1 = 0.0, 1.0
    dx, dy = b[0] - a[0], b[1] - a[1]
    for p, q in ((-dx, a[0] - x0), (dx, x1 - a[0]), (-dy, a[1] - y0), (dy, y1 - a[1])):
        if p == 0:
            if q < 0: return None
        else:
            t = q / p
            if p < 0:
                if t > t1: return None
                t0 = max(t0, t)
            else:
                if t < t0: return None
                t1 = min(t1, t)
    return (a[0] + t0 * dx, a[1] + t0 * dy), (a[0] + t1 * dx, a[1] + t1 * dy)


def dec_poly(s):
    i = lat = lon = 0; out = []
    while i < len(s):
        vals = []
        for _ in range(2):
            r = sh = 0
            while True:
                b = ord(s[i]) - 63; i += 1; r |= (b & 0x1f) << sh; sh += 5
                if b < 0x20: break
            vals.append(~(r >> 1) if r & 1 else r >> 1)
        lat += vals[0]; lon += vals[1]; out.append((lat / 1e5, lon / 1e5))
    return out


def enc_poly(pts, prec=1e5):
    out = []; pl = pg = 0
    for lat, lon in pts:
        a, b = int(round(lat * prec)), int(round(lon * prec))
        for v in (a - pl, b - pg):
            v = ~(v << 1) if v < 0 else (v << 1)
            while v >= 0x20:
                out.append(chr((0x20 | (v & 0x1f)) + 63)); v >>= 5
            out.append(chr(v + 63))
        pl, pg = a, b
    return ''.join(out)


# ---------- the road network ----------

class Roads:
    def __init__(self, pmtiles_path, bbox):
        from pmtiles.reader import Reader, MmapSource
        self.adj = defaultdict(dict)      # node -> {node: cost of driving that way, metres}
        self.L = {}                       # (node, node) sorted -> length, metres
        self.slow = {}                    # (node, node) sorted -> extra cost factor (service roads)
        self.level = {}                   # (node, node) sorted -> 'bridge' / 'tunnel' (never joined to crossings)
        self._link = []
        self.at = {}                      # rounded point -> junction node made by add_junctions
        self.xy = {}                      # node -> (x, y) metres
        lat0, lon0, lat1, lon1 = bbox
        xa, ya = ll_to_world(lat1, lon0); xb, yb = ll_to_world(lat0, lon1)
        with open(pmtiles_path, 'rb') as f:
            reader = Reader(MmapSource(f))
            for tx in range(int(xa // 4096), int(xb // 4096) + 1):
                for ty in range(int(ya // 4096), int(yb // 4096) + 1):
                    data = reader.get(Z, tx, ty)
                    if not data: continue
                    if data[:2] == b'\x1f\x8b': data = gzip.decompress(data)
                    self.add_tile(tx, ty, data)
        self.join_tile_edges()
        self.add_junctions()
        self.grid = defaultdict(list)     # 40 m cells -> edges
        for (u, v) in self.L:
                    (x1, y1), (x2, y2) = self.xy[u], self.xy[v]
                    for cx in range(int(min(x1, x2) // 40), int(max(x1, x2) // 40) + 1):
                        for cy in range(int(min(y1, y2) // 40), int(max(y1, y2) // 40) + 1):
                            self.grid[(cx, cy)].append((u, v))

    def split(self, u, v, pts):
        """split street piece u-v at points pts (sorted from u to v); returns the new nodes"""
        key = (min(u, v), max(u, v)); cf, cb = self.adj[u].pop(v), self.adj[v].pop(u)
        L = self.L.pop(key); sl = self.slow.pop(key, None); lv = self.level.pop(key, None)
        fu, fb = cf / L if L else 1, cb / L if L else 1
        chain = [u]
        for p in pts:
            n = self.at.get((round(p[0], 1), round(p[1], 1)))
            if n is None:                              # a new junction: keys after every map node
                n = (10 ** 9 + len(self.at), 0); self.xy[n] = p; self.at[(round(p[0], 1), round(p[1], 1))] = n
            if n != chain[-1]: chain.append(n)
        if v != chain[-1]: chain.append(v)
        for a, b in zip(chain, chain[1:]):
            d = math.dist(self.xy[a], self.xy[b]); k = (min(a, b), max(a, b))
            self.adj[a][b] = min(d * fu, self.adj[a].get(b, 1e18)); self.adj[b][a] = min(d * fb, self.adj[b].get(a, 1e18))
            self.L[k] = d
            if sl: self.slow[k] = sl
            if lv: self.level[k] = lv
        return chain[1:-1]

    def add_junctions(self):
        """Simplifying a road's shape for the map can drop the point where it meets another road, so roads
        that visibly cross or meet aren't connected. Join (except bridges and tunnels):
          * pieces that cross each other, at the crossing point;
          * a dead end within 4 m of another road, to the nearest point of that road."""
        cell = defaultdict(set)
        for (u, v) in self.L:
            (x1, y1), (x2, y2) = self.xy[u], self.xy[v]
            for cx in range(int(min(x1, x2) // 25), int(max(x1, x2) // 25) + 1):
                for cy in range(int(min(y1, y2) // 25), int(max(y1, y2) // 25) + 1):
                    cell[(cx, cy)].add((u, v))
        cuts = defaultdict(list)                       # piece -> [(t, point)]
        seen = set()
        for es in cell.values():
            es = list(es)
            for i in range(len(es)):
                a = es[i]
                if a in self.level: continue
                for b in es[i + 1:]:
                    if b in self.level or (a, b) in seen: continue
                    seen.add((a, b))
                    if set(a) & set(b): continue
                    p1, p2, p3, p4 = self.xy[a[0]], self.xy[a[1]], self.xy[b[0]], self.xy[b[1]]
                    d = (p2[0] - p1[0]) * (p4[1] - p3[1]) - (p2[1] - p1[1]) * (p4[0] - p3[0])
                    if abs(d) < 1e-9: continue
                    t = ((p3[0] - p1[0]) * (p4[1] - p3[1]) - (p3[1] - p1[1]) * (p4[0] - p3[0])) / d
                    w = ((p3[0] - p1[0]) * (p2[1] - p1[1]) - (p3[1] - p1[1]) * (p2[0] - p1[0])) / d
                    if 0 < t < 1 and 0 < w < 1:
                        q = (p1[0] + t * (p2[0] - p1[0]), p1[1] + t * (p2[1] - p1[1]))
                        cuts[a].append((t, q)); cuts[b].append((w, q))
        # dead ends close to another road
        for n in [n for n in self.adj if len(self.adj[n]) == 1]:
            x, y = self.xy[n]; own = next(iter(self.adj[n])); best = None
            for e in cell.get((int(x // 25), int(y // 25)), ()):
                if n in e or own in e or e in self.level: continue
                (x1, y1), (x2, y2) = self.xy[e[0]], self.xy[e[1]]; dx, dy = x2 - x1, y2 - y1; L2 = dx * dx + dy * dy
                t = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / L2)) if L2 else 0
                q = (x1 + t * dx, y1 + t * dy); dd = math.dist(q, (x, y))
                if dd <= 4 and (best is None or dd < best[0]): best = (dd, e, t, q)
            if best:
                dd, e, t, q = best
                if 0.02 < t < 0.98: cuts[e].append((t, q)); self._link.append((n, q))
                else: self._link.append((n, self.xy[e[0] if t <= .02 else e[1]]))
        made = 0
        for e, ps in cuts.items():
            if e not in self.L: continue
            ps.sort(); self.split(e[0], e[1], [q for _, q in ps]); made += len(ps)
        # connect the dead ends to the points made for them
        index = dict(self.at)
        for n, p in self.xy.items(): index.setdefault((round(p[0], 1), round(p[1], 1)), n)
        for n, q in self._link:
            k = index.get((round(q[0], 1), round(q[1], 1)))
            if k is None or k == n or n not in self.adj or k not in self.adj: continue
            d = math.dist(self.xy[n], self.xy[k]); key = (min(n, k), max(n, k))
            self.adj[n][k] = self.adj[k][n] = max(d, .1); self.L[key] = max(d, .1); made += 1
        self.junctions = made

    def join_tile_edges(self, tol=2.0):
        """Each map tile stores its own copy of a road, rounded to its own grid, so where a road crosses from
        one tile to the next the two halves end a few centimetres apart and the road is broken there. Ends
        that lie on a tile edge are joined to any other end within `tol` metres."""
        def on_edge(n):
            x, y = n[0] / 2 % 4096, n[1] / 2 % 4096
            return min(x, 4096 - x, y, 4096 - y) < 2
        ends = [n for n in self.adj if on_edge(n)]
        cell = defaultdict(list)
        for n in ends:
            x, y = self.xy[n]; cell[(int(x // tol), int(y // tol))].append(n)
        parent = {}
        def find(n):
            while parent.get(n, n) != n: n = parent[n]
            return n
        for n in ends:
            x, y = self.xy[n]
            for gx in range(int(x // tol) - 1, int(x // tol) + 2):
                for gy in range(int(y // tol) - 1, int(y // tol) + 2):
                    for k in cell.get((gx, gy), ()):
                        if k != n and math.dist(self.xy[k], (x, y)) <= tol:
                            a, b = find(n), find(k)
                            if a != b: parent[b] = a
        moved = 0
        for n in [n for n in list(self.adj) if find(n) != n]:
            r = find(n); moved += 1
            for v in list(self.adj[n]):                    # every street piece n-v becomes r-v
                out_c, in_c = self.adj[n].get(v), self.adj[v].pop(n, None)
                key = (min(n, v), max(n, v)); self.L.pop(key, None); sl = self.slow.pop(key, None)
                if v == r: continue
                nk = (min(r, v), max(r, v)); self.L[nk] = math.dist(self.xy[r], self.xy[v])
                if sl: self.slow[nk] = sl
                if out_c is not None: self.adj[r][v] = min(out_c, self.adj[r].get(v, 1e18))
                if in_c is not None: self.adj[v][r] = min(in_c, self.adj[v].get(r, 1e18))
            del self.adj[n]
        # Ends further apart: each tile also simplifies a road's shape a little differently, so the two halves
        # can cross the tile edge several metres apart. Connect a remaining dead end on a tile edge to the
        # nearest point within 12 m on the far side of that edge (or on the edge itself).
        near = defaultdict(list)
        for n in self.adj:
            x, y = self.xy[n]; near[(int(x // 12), int(y // 12))].append(n)
        def side(n, axis, line):
            return (n[axis] / 2 - line)
        for n in [n for n in self.adj if len(self.adj[n]) == 1 and on_edge(n)]:
            X, Y = n[0] / 2, n[1] / 2
            ex, ey = X % 4096, Y % 4096
            axis = 0 if min(ex, 4096 - ex) < min(ey, 4096 - ey) else 1
            line = round((X if axis == 0 else Y) / 4096) * 4096
            mine = side(next(iter(self.adj[n])), axis, line)       # which side n's own road comes from
            x, y = self.xy[n]; best = None
            for gx in range(int(x // 12) - 1, int(x // 12) + 2):
                for gy in range(int(y // 12) - 1, int(y // 12) + 2):
                    for k in near.get((gx, gy), ()):
                        if k == n or k in self.adj[n]: continue
                        sk = side(k, axis, line)
                        if sk * mine > 0 and abs(sk) > 1: continue     # same side: a different road
                        d = math.dist(self.xy[k], (x, y))
                        if d <= 12 and (best is None or d < best[0]): best = (d, k)
            if best:
                d, k = best
                self.adj[n][k] = self.adj[k][n] = d; self.L[(min(n, k), max(n, k))] = d; moved += 1
        self.joined = moved

    def node(self, X, Y):
        k = (round(X * 2), round(Y * 2))   # half a map unit (~10 cm): tiles meet exactly
        if k not in self.xy:
            self.xy[k] = m(*world_to_ll(X, Y))
        return k

    def add_tile(self, tx, ty, data):
        x0, y0 = tx * 4096, ty * 4096
        for props, lines in road_lines(data):
            if props.get('kind') in SKIP_KINDS: continue
            ow = str(props.get('oneway', '')).lower()
            fwd_only, back_only = ow in ('yes', 'true', '1'), ow == '-1'
            for line in lines:
                pts = [(x0 + x, y0 + y) for x, y in line]
                for a, b in zip(pts, pts[1:]):
                    c = clip(a, b, x0, y0, x0 + 4096, y0 + 4096)   # each tile only up to its own edge
                    if not c: continue
                    # long straight streets are cut into pieces of at most ~20 m, so every piece can be
                    # judged by how close it runs to a route
                    n = max(1, int(math.dist(m(*world_to_ll(*c[0])), m(*world_to_ll(*c[1]))) // 20))
                    ps = [(c[0][0] + (c[1][0] - c[0][0]) * k / n, c[0][1] + (c[1][1] - c[0][1]) * k / n) for k in range(n + 1)]
                    for pa, pb in zip(ps, ps[1:]):
                        u, v = self.node(*pa), self.node(*pb)
                        if u == v: continue
                        d = math.dist(self.xy[u], self.xy[v])
                        cf = WRONG_WAY(d) if back_only else d
                        cb = WRONG_WAY(d) if fwd_only else d
                        self.adj[u][v] = min(cf, self.adj[u].get(v, 1e18))
                        self.adj[v][u] = min(cb, self.adj[v].get(u, 1e18))
                        self.L[(min(u, v), max(u, v))] = d
                        if props.get('kind_detail') == 'service': self.slow[(min(u, v), max(u, v))] = 1.5
                        if props.get('is_bridge') or props.get('is_tunnel'):
                            self.level[(min(u, v), max(u, v))] = 'bridge' if props.get('is_bridge') else 'tunnel'

    def f(self, a, b):
        """cost per metre of driving the street segment a -> b"""
        return self.adj[a][b] / (self.L[(min(a, b), max(a, b))] or 1)

    def candidates(self, p):
        cx, cy = int(p[0] // 40), int(p[1] // 40); seen = set(); out = []
        for gx in range(cx - 1, cx + 2):
            for gy in range(cy - 1, cy + 2):
                for e in self.grid.get((gx, gy), ()):
                    if e in seen: continue
                    seen.add(e)
                    (x1, y1), (x2, y2) = self.xy[e[0]], self.xy[e[1]]
                    dx, dy = x2 - x1, y2 - y1; L2 = dx * dx + dy * dy
                    t = max(0.0, min(1.0, ((p[0] - x1) * dx + (p[1] - y1) * dy) / L2)) if L2 else 0.0
                    q = (x1 + t * dx, y1 + t * dy); d = math.dist(p, q)
                    if d <= RADIUS: out.append((d, e, t, q))
        out.sort(key=lambda c: c[0])
        # one candidate per road: keep the nearest point of each nearby street
        best, used = [], []
        for c in out:
            if any(math.dist(c[3], o[3]) < 4 for o in used): continue
            used.append(c); best.append(c)
            if len(best) == MAX_CANDS: break
        return best

    def dijkstra(self, sources, targets, cutoff):
        """shortest distances and paths from sources {node: start metres} to any of targets"""
        dist = dict(sources); prev = {}; h = [(d, n) for n, d in sources.items()]; heapq.heapify(h)
        left = set(targets); found = {}
        while h and left:
            d, n = heapq.heappop(h)
            if d > dist.get(n, 1e18) or d > cutoff: continue
            if n in left: left.discard(n); found[n] = d
            for v, w in self.adj[n].items():
                nd = d + w
                if nd < dist.get(v, 1e18):
                    dist[v] = nd; prev[v] = n; heapq.heappush(h, (nd, v))
        return found, prev


def path_to(prev, n):
    out = [n]
    while n in prev: n = prev[n]; out.append(n)
    return out[::-1]


# ---------- matching one route ----------
#
# A route is found the way a bus would drive it, following these rules:
#   * one continuous drive along the roads: never jumping between roads or across a block;
#   * no U-turns in the street (only at a dead end, or by going round a roundabout);
#   * one-way streets and roundabouts in the legal direction (the opposite way costs so much it is only
#     used where the city's outline leaves no other choice, e.g. a contraflow bus lane);
#   * sharp turns cost a little, so the natural line along a road is preferred over zig-zags;
#   * service roads (car parks, lay-bys) cost a little more than streets.
# Within those rules, it takes the drive that best follows the city's outline: the outline is cut into
# stretches of about ANCHOR metres; each stretch end ("anchor") gets candidate points on nearby roads,
# facing the way the outline goes; consecutive anchors are joined by the cheapest legal drive, where a
# street costs more the further it strays from the outline and when it runs against the outline's
# direction; the best chain is picked with the Viterbi algorithm. Where no drive fits a narrow corridor
# around the outline, wider ones are tried before the outline itself is used.

BREAKS = []                 # where no legal drive was found (for checking)
ANCHOR = 120                # metres between anchors
CORRIDORS = (45, 110, 260)  # metres: corridors tried in turn
DEV = 11                    # metres: straying this far from the outline doubles a street's cost
AGAINST = 6                 # cost factor for driving against the outline's direction


def resample(xy, cum, step):
    """points every `step` metres along a polyline (plus the ends): [(point, timetable distance, index)]"""
    geo = [0.0]
    for a, b in zip(xy, xy[1:]): geo.append(geo[-1] + math.dist(a, b))
    out = [(xy[0], cum[0], 0)]; k = 1; s = step
    while s < geo[-1] - step * .4:
        while geo[k] < s: k += 1
        f = (s - geo[k - 1]) / ((geo[k] - geo[k - 1]) or 1)
        p = (xy[k - 1][0] + f * (xy[k][0] - xy[k - 1][0]), xy[k - 1][1] + f * (xy[k][1] - xy[k - 1][1]))
        out.append((p, cum[k - 1] + f * (cum[k] - cum[k - 1]), k)); s += step
    out.append((xy[-1], cum[-1], len(xy) - 1))
    return out


def seg_near(segs, p):
    best = (1e18, (0.0, 0.0))
    for a, b in segs:
        dx, dy = b[0] - a[0], b[1] - a[1]; L2 = dx * dx + dy * dy
        t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2)) if L2 else 0.0
        d = math.dist(p, (a[0] + t * dx, a[1] + t * dy))
        if d < best[0]: best = (d, (dx, dy))
    return best


class Corridor:
    """cost of driving each street piece along one stretch of outline, within `width` metres of it"""
    def __init__(self, roads, segs, width):
        self.r, self.segs, self.w, self.cache = roads, segs, width, {}

    def cost(self, u, v):
        k = (u, v)
        if k in self.cache: return self.cache[k]
        r = self.r; pu, pv = r.xy[u], r.xy[v]; key = (min(u, v), max(u, v)); L = r.L[key]
        mid = ((pu[0] + pv[0]) / 2, (pu[1] + pv[1]) / 2)
        dm, (ox, oy) = seg_near(self.segs, mid)
        d = max(seg_near(self.segs, pu)[0], dm, seg_near(self.segs, pv)[0])
        if d > self.w:
            c = None
        else:
            ex, ey = pv[0] - pu[0], pv[1] - pu[1]; on, en = math.hypot(ox, oy), math.hypot(ex, ey)
            dot = (ox * ex + oy * ey) / (on * en) if on and en else 1
            c = L * (1 + (min(d, 45) / DEV) ** 2 + max(0, d - 45) / 10) * (AGAINST if dot < -0.3 else 1) \
                * (r.adj[u][v] / L if L else 1) * r.slow.get(key, 1)
        self.cache[k] = c
        return c


def turn_cost(roads, a, b, c):
    """extra cost (metres) of turning at b, coming from a and going on to c"""
    (ax, ay), (bx, by), (cx, cy) = roads.xy[a], roads.xy[b], roads.xy[c]
    ux, uy, vx, vy = bx - ax, by - ay, cx - bx, cy - by; lu, lv = math.hypot(ux, uy), math.hypot(vx, vy)
    if not lu or not lv: return 0
    cosang = (ux * vx + uy * vy) / (lu * lv)
    return 0 if cosang > 0.87 else 6 if cosang > 0.2 else 20 if cosang > -0.6 else 60


def drives(roads, cor, a, bs, cutoff):
    """cheapest legal drives from candidate a to each candidate in bs, as [(cost, [points]) or None].
    Candidates are (distance, (from, to), t, point): a point at fraction t along the directed piece from->to.
    The search runs over directed pieces, so a U-turn (from->to followed by to->from) can be refused."""
    _, (u1, v1), t1, q1 = a
    c0 = cor.cost(u1, v1)
    out = [None] * len(bs)
    if c0 is None: return out
    start = (u1, v1); rest = c0 * (1 - t1)
    want = {}
    for i, (_, e, t2, q2) in enumerate(bs):
        if e == start and t2 >= t1 - 1e-9:            # further along the same piece
            out[i] = (c0 * (t2 - t1), [q1, q2])
        else:
            want.setdefault(e, []).append(i)
    if not want: return out
    dist = {start: rest}; prev = {}; h = [(rest, start)]; left = set(want)
    while h and left:
        d, e = heapq.heappop(h)
        if d > dist.get(e, 1e18) or d > cutoff: continue
        u, v = e
        if e in left: left.discard(e)
        nbrs = roads.adj[v]
        for w in nbrs:
            if w == u and len(nbrs) > 1: continue        # no U-turns except at a dead end
            c = cor.cost(v, w)
            if c is None: continue
            nd = d + c + turn_cost(roads, u, v, w)
            ne = (v, w)
            if nd < dist.get(ne, 1e18): dist[ne] = nd; prev[ne] = e; heapq.heappush(h, (nd, ne))
    for e, idx in want.items():
        if e not in dist: continue
        ce = cor.cost(*e)
        chain = [e]
        while chain[-1] in prev: chain.append(prev[chain[-1]])
        chain.reverse()                                    # start ... e
        nodes = [x[1] for x in chain[:-1]]                 # the junctions passed through
        for i in idx:
            _, _, t2, q2 = bs[i]
            # reaching e means arriving at its start node; then drive t2 of it (minus the full piece counted)
            cost = dist[e] - ce + ce * t2
            out[i] = (cost, [q1] + [roads.xy[n] for n in nodes] + [q2])
    return out


def match(roads, pts, cum):
    xy = [m(*p) for p in pts]
    anchors = resample(xy, cum, ANCHOR)
    fine = resample(xy, cum, 8)

    def heading(dc):                                       # outline direction around timetable distance dc
        near = [p for p, d, _ in fine if abs(d - dc) <= 25]
        if len(near) < 2: return (1.0, 0.0)
        return (near[-1][0] - near[0][0], near[-1][1] - near[0][1])

    def cands(i):
        p, dc, _ = anchors[i]; hx, hy = heading(dc); out = []
        for d, (u, v), t, q in roads.candidates(p):
            ex, ey = roads.xy[v][0] - roads.xy[u][0], roads.xy[v][1] - roads.xy[u][1]
            fwd = ((u, v), t) if ex * hx + ey * hy >= 0 else ((v, u), 1 - t)
            out.append((d,) + fwd + (q,))
        return out

    C = [cands(i) for i in range(len(anchors))]
    out_pts, out_d = [], []

    def emit(seq, d0, d1):
        L = [0.0]
        for a, b in zip(seq, seq[1:]): L.append(L[-1] + math.dist(a, b))
        for p, l in zip(seq, L):
            if out_pts and math.dist(out_pts[-1], p) < 0.3: continue
            out_pts.append(p); out_d.append(d0 + (d1 - d0) * (l / L[-1] if L[-1] else 0))

    def stretch(i, margin):
        d0, d1 = anchors[i][1] - margin, anchors[i + 1][1] + margin
        p_ = [p for p, d, _ in fine if d0 <= d <= d1]
        return list(zip(p_, p_[1:])) or [(anchors[i][0], anchors[i + 1][0])]

    def step(j, score):
        """best transitions from anchor j's candidates to anchor j+1's, widening the corridor if needed"""
        straight = math.dist(anchors[j][0], anchors[j + 1][0])
        for w in CORRIDORS:
            cor = Corridor(roads, stretch(j, 60 + w), w); nxt = C[j + 1]
            ns, nb, nl = [1e18] * len(nxt), [None] * len(nxt), [None] * len(nxt)
            for k, c1 in enumerate(C[j]):
                if score[k] >= 1e17: continue
                for q, r in enumerate(drives(roads, cor, c1, nxt, straight * 10 + 600 + w * 8)):
                    if not r: continue
                    sc = score[k] + r[0] + (nxt[q][0] / DEV) ** 2 * 3
                    if sc < ns[q]: ns[q], nb[q], nl[q] = sc, k, r[1]
            if min(ns) < 1e17: return ns, nb, nl, w
        return None

    matched = total = 0.0; wide = 0
    i = 0
    while i < len(anchors) - 1:
        if not C[i]:
            seq = [p for p, d, _ in fine if anchors[i][1] <= d <= anchors[i + 1][1]] or [anchors[i][0], anchors[i + 1][0]]
            emit(seq, anchors[i][1], anchors[i + 1][1]); total += anchors[i + 1][1] - anchors[i][1]; i += 1; continue
        score = [(c[0] / DEV) ** 2 * 3 for c in C[i]]; back, legs, j = [], [], i
        while j + 1 < len(anchors) and C[j + 1]:
            r = step(j, score)
            if not r: BREAKS.append(unm(*anchors[j][0])); break
            ns, nb, nl, w = r
            if w != CORRIDORS[0]: wide += 1
            score, back, legs, j = ns, back + [nb], legs + [nl], j + 1
        k = min(range(len(score)), key=lambda q: score[q]); chain = [k]
        for nb in reversed(back): k = nb[k]; chain.append(k)
        chain.reverse()
        if j == i: emit([C[i][chain[0]][3]], anchors[i][1], anchors[i][1])
        for s_ in range(len(chain) - 1):
            emit(legs[s_][chain[s_ + 1]], anchors[i + s_][1], anchors[i + s_ + 1][1])
        matched += anchors[j][1] - anchors[i][1]; total += anchors[j][1] - anchors[i][1]
        if j + 1 < len(anchors):                           # no legal drive at all: outline until the next anchor
            seq = [out_pts[-1]] + [p for p, d, _ in fine if anchors[j][1] < d < anchors[j + 1][1]] + [anchors[j + 1][0]]
            emit(seq, out_d[-1], anchors[j + 1][1]); total += anchors[j + 1][1] - anchors[j][1]
        i = j + 1
    return out_pts, out_d, matched / max(1e-9, total)


def despike(pts, ds):
    """remove short out-and-back spikes (a match that pokes into a side street and returns)"""
    changed = True
    while changed and len(pts) > 2:  # noqa
        changed = False; P, D = [pts[0]], [ds[0]]
        for k in range(1, len(pts) - 1):
            a, b, c = P[-1], pts[k], pts[k + 1]
            ux, uy, vx, vy = b[0] - a[0], b[1] - a[1], c[0] - b[0], c[1] - b[1]
            la, lb = math.hypot(ux, uy), math.hypot(vx, vy)
            if la and lb and (ux * vx + uy * vy) / (la * lb) < -0.7 and min(la, lb) < 60:
                changed = True; continue
            P.append(b); D.append(ds[k])
        P.append(pts[-1]); D.append(ds[-1]); pts, ds = P, D
    return pts, ds


def simplify(pts, ds, tol=1.6):
    """Douglas-Peucker, keeping each kept point's distance"""
    keep = [False] * len(pts); keep[0] = keep[-1] = True; stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop(); (x1, y1), (x2, y2) = pts[a], pts[b]; dx, dy = x2 - x1, y2 - y1; L = math.hypot(dx, dy)
        far, fd = None, tol
        for k in range(a + 1, b):
            x, y = pts[k]
            d = abs(dy * (x - x1) - dx * (y - y1)) / L if L else math.dist(pts[k], pts[a])
            if d > fd: far, fd = k, d
        if far is not None:
            keep[far] = True; stack += [(a, far), (far, b)]
    return [p for p, k in zip(pts, keep) if k], [d for d, k in zip(ds, keep) if k]


def main(data_path, pm_path):
    data = json.load(open(data_path, encoding='utf-8'))
    shapes = [(dec_poly(p), dd) for p, dd in data['shapes']]
    allp = [q for pts, _ in shapes for q in pts]
    bbox = (min(p[0] for p in allp) - .005, min(p[1] for p in allp) - .007,
            max(p[0] for p in allp) + .005, max(p[1] for p in allp) + .007)
    roads = Roads(pm_path, bbox)
    print('road network: %d junctions and bends, %d street segments' % (len(roads.xy), len(roads.L)))
    out, fr = [], []
    for n, (pts, dd) in enumerate(shapes):
        cum = []; a = 0
        for d in dd: a += d; cum.append(a)
        mp, md, f = match(roads, pts, cum)
        mp, md = simplify(*despike(mp, md))
        md = [max(0, round(d)) for d in md]
        for k in range(1, len(md)): md[k] = max(md[k], md[k - 1])   # never backwards
        out.append([enc_poly([unm(*p) for p in mp]), [md[0]] + [md[k] - md[k - 1] for k in range(1, len(md))]])
        fr.append(f)
        print('route outline %2d: %4d -> %4d points, %3d%% on the roads' % (n, len(pts), len(mp), round(f * 100)))
    data['shapes'] = out
    data['matched'] = 1
    json.dump(data, open(data_path, 'w', encoding='utf-8'), separators=(',', ':'), ensure_ascii=False)
    print('matched %d route outlines, %.0f%% of their length on the roads' % (len(out), 100 * sum(fr) / len(fr)))


if __name__ == '__main__':
    if len(sys.argv) < 2: sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, '..', 'map', 'burgos.pmtiles'))
