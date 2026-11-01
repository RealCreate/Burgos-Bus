"""Find places where the bus routes drawn on the map look wrong.

Checks every route outline in the built site (index.html) against the roads of map/burgos.pmtiles:
  off-road   a stretch of route more than 7 m from any road (it's cutting a corner or across a block)
  spike      the route turns back on itself (out and back along the same street)
  step       a sideways jog: two sharp opposite turns close together (a zig-zag between lanes)
  strays     the route is far from the city's own outline (matched to the wrong street)

Usage:  python3 tools/check_routes.py [index.html] [--orig ORIGINAL.json]
Prints one line per problem place (with a link to look at it), most serious first, and a summary.
"""
import json, math, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import match_shapes as M  # noqa: E402

OFFROAD = 7       # metres
STRAY = 45        # metres


def load(path):
    s = open(path, encoding='utf-8').read()
    if path.endswith('.json'):
        return json.loads(s)
    return json.loads(re.search(r'<script type="application/json" id="gtfs">(.*?)</script>', s, re.S).group(1).replace('<\\/', '</'))


def road_dist(roads, p):
    cx, cy = int(p[0] // 40), int(p[1] // 40); best = 1e9
    for gx in range(cx - 1, cx + 2):
        for gy in range(cy - 1, cy + 2):
            for u, v in roads.grid.get((gx, gy), ()):
                a, b = roads.xy[u], roads.xy[v]; dx, dy = b[0] - a[0], b[1] - a[1]; L2 = dx * dx + dy * dy
                t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2)) if L2 else 0
                best = min(best, math.dist(p, (a[0] + t * dx, a[1] + t * dy)))
    return best


def poly_dist(segs, p):
    best = 1e9
    for a, b in segs:
        dx, dy = b[0] - a[0], b[1] - a[1]; L2 = dx * dx + dy * dy
        t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2)) if L2 else 0
        best = min(best, math.dist(p, (a[0] + t * dx, a[1] + t * dy)))
    return best


def turns_back(xy, p, r=60):
    """whether polyline xy reverses direction within r metres of p"""
    for a, b, c in zip(xy, xy[1:], xy[2:]):
        if math.dist(b, p) > r: continue
        u, v = (b[0] - a[0], b[1] - a[1]), (c[0] - b[0], c[1] - b[1]); lu, lv = math.hypot(*u), math.hypot(*v)
        if lu and lv and (u[0] * v[0] + u[1] * v[1]) / (lu * lv) < -0.5: return True
    return False


def check(data, roads, orig=None):
    lines_of = {}
    for pt in data['patterns']:
        lines_of.setdefault(pt[3], set()).add(data['routes'][pt[0]][0])
    found = []
    for n, (poly, _) in enumerate(data['shapes']):
        xy = [M.m(*p) for p in M.dec_poly(poly)]
        tag = '/'.join(sorted(lines_of.get(n, {'?'})))
        # dense samples
        sm = [xy[0]]
        for a, b in zip(xy, xy[1:]):
            k = max(1, int(math.dist(a, b) // 5))
            sm += [(a[0] + (b[0] - a[0]) * i / k, a[1] + (b[1] - a[1]) * i / k) for i in range(1, k + 1)]
        run = []
        for p in sm + [None]:
            if p is not None and road_dist(roads, p) > OFFROAD: run.append(p); continue
            if len(run) >= 3:                       # 15 m or more off the road
                found.append(('off-road', len(run) * 5, n, tag, run[len(run) // 2]))
            run = []
        orig_xy = [M.m(*p) for p in M.dec_poly(orig['shapes'][n][0])] if orig is not None else None
        # turns
        segs = [(a, b) for a, b in zip(xy, xy[1:]) if math.dist(a, b) > 1]
        def ang(s): return math.atan2(s[1][1] - s[0][1], s[1][0] - s[0][0])
        def turn(s1, s2): return (ang(s2) - ang(s1) + math.pi) % (2 * math.pi) - math.pi
        for k in range(len(segs) - 1):
            t = turn(segs[k], segs[k + 1])
            l1, l2 = math.dist(*segs[k]), math.dist(*segs[k + 1])
            if abs(t) > math.radians(150) and min(l1, l2) > 3:
                # a turnaround the city's own outline also makes (end of the line) is real
                if not (orig is not None and turns_back(orig_xy, segs[k][1])):
                    found.append(('spike', min(l1, l2), n, tag, segs[k][1]))
            # step: a sharp turn, a short sideways bit, a sharp turn back
            if k + 2 < len(segs) and l2 < 15:
                t2 = turn(segs[k + 1], segs[k + 2])
                if abs(t) > math.radians(55) and abs(t2) > math.radians(55) and t * t2 < 0 \
                        and abs(t + t2) < math.radians(35) and min(l1, math.dist(*segs[k + 2])) > 8:
                    found.append(('step', l2, n, tag, segs[k + 1][0]))
        if orig is not None:
            oxy = [M.m(*p) for p in M.dec_poly(orig['shapes'][n][0])]
            osegs = list(zip(oxy, oxy[1:]))
            run = []
            for p in sm[::4] + [None]:
                if p is not None and poly_dist(osegs, p) > STRAY: run.append(p); continue
                if run: found.append(('strays', len(run) * 20, n, tag, run[len(run) // 2]))
                run = []
    # one entry per place: merge problems within 40 m
    rank = {'strays': 0, 'off-road': 1, 'spike': 2, 'step': 3}
    found.sort(key=lambda f: (rank[f[0]], -f[1]))
    places = []
    for f in found:
        if any(math.dist(f[4], g[4]) < 40 for g in places): continue
        places.append(f)
    return places


def main():
    args = sys.argv[1:]
    orig = None
    if '--orig' in args:
        i = args.index('--orig'); orig = load(args[i + 1]); del args[i:i + 2]
    data = load(args[0] if args else os.path.join(HERE, '..', 'index.html'))
    allp = [q for p, _ in data['shapes'] for q in M.dec_poly(p)]
    bbox = (min(p[0] for p in allp) - .005, min(p[1] for p in allp) - .007, max(p[0] for p in allp) + .005, max(p[1] for p in allp) + .007)
    roads = M.Roads(os.path.join(HERE, '..', 'map', 'burgos.pmtiles'), bbox)
    places = check(data, roads, orig)
    for kind, size, n, tag, p in places:
        lat, lon = M.unm(*p)
        print('%-8s %4.0f m  outline %2d (lines %s)  %.5f,%.5f' % (kind, size, n, tag, lat, lon))
    from collections import Counter
    print('---\n%d places: %s' % (len(places), dict(Counter(p[0] for p in places))))
    return places


if __name__ == '__main__':
    main()
