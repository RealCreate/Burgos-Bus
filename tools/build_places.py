"""Build map/places.json, the search index for the route planner, from the site's own map file.

From map/burgos.pmtiles (OpenStreetMap, zoom 15 tiles):
  * places: every named point of interest (shops, cafés, schools, hospitals, university buildings…)
  * addresses: every address point (house number), given the name of the nearest named street
  * streets: every named street, at the middle of its longest piece (to search a street without a number)
  * areas: the town, village or Burgos neighbourhood each of these is in, so the many "Plaza Mayor" and
    "Calle Real" of the villages around Burgos can be told apart (and their addresses aren't mixed up)

Output: {"l": [area names], "p": [[name, kind, lat, lon, area]], "s": [[street, area, lat, lon]],
         "a": [[street, area, [[number, lat, lon], ...]], ...]}   (area = index into "l")

Usage:  python3 tools/build_places.py [map/burgos.pmtiles] [map/places.json]
Needs:  pip install pmtiles
"""
import gzip, json, math, os, re, sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import match_shapes as M  # noqa: E402

BOX = (42.25, -3.86, 42.46, -3.52)      # south, west, north, east: same as MAP_BOUNDS
# kinds of point of interest worth offering as a destination (others: benches, bins, parking entrances…)
SKIP = {'bench', 'waste_basket', 'parking_entrance', 'bicycle_parking', 'recycling', 'vending_machine',
        'post_box', 'telephone', 'drinking_water', 'toilets', 'charging_station', 'atm', 'tree', 'tree_stump',
        'bus_stop', 'crossing', 'traffic_signals', 'street_lamp', 'fire_hydrant', 'bicycle_repair_station',
        'motorcycle_parking', 'sanitary_dump_station', 'fitness_station', 'shelter', 'information', 'footway',
        'parking_space', 'manhole', 'water_point', 'clock', 'waste_disposal', 'picnic_table', 'viewpoint',
        'grit_bin', 'hunting_stand', 'compressed_air', 'loading_dock', 'smoking_area', 'artwork'}


def features(tile):
    """{layer: [(props, geometry type, [[(x, y)...], ...])]} for a vector tile"""
    out = defaultdict(list)
    for f, _, layer in M.fields(tile):
        if f != 3: continue
        name, feats, keys, vals, extent = None, [], [], [], 4096
        for g, _, v in M.fields(layer):
            if g == 1: name = v.decode()
            elif g == 2: feats.append(v)
            elif g == 3: keys.append(v.decode())
            elif g == 4: vals.append(M.value(v))
            elif g == 5: extent = v
        if name not in ('pois', 'buildings', 'roads', 'places'): continue
        for fb in feats:
            tags, typ, geom = [], 0, []
            for g, _, v in M.fields(fb):
                if g == 2: tags = M.packed(v)
                elif g == 3: typ = v
                elif g == 4: geom = M.packed(v)
            props = {keys[tags[k]]: vals[tags[k + 1]] for k in range(0, len(tags), 2)}
            parts, cur, x, y, i = [], None, 0, 0, 0
            while i < len(geom):
                cmd, cnt = geom[i] & 7, geom[i] >> 3; i += 1
                if cmd == 7: continue
                for _ in range(cnt):
                    dx, dy = geom[i], geom[i + 1]; i += 2
                    x += (dx >> 1) ^ -(dx & 1); y += (dy >> 1) ^ -(dy & 1)
                    p = (x * 4096 / extent, y * 4096 / extent)
                    if cmd == 1: cur = [p]; parts.append(cur)
                    else: cur.append(p)
            out[name].append((props, typ, parts))
    return out


def centroid(ring):
    a = cx = cy = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        c = x1 * y2 - x2 * y1; a += c; cx += (x1 + x2) * c; cy += (y1 + y2) * c
    if abs(a) < 1e-9:
        return (sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring))
    return (cx / (3 * a), cy / (3 * a))


def main(pm, out_path):
    from pmtiles.reader import Reader, MmapSource
    s, w, n, e = BOX
    xa, ya = M.ll_to_world(n, w); xb, yb = M.ll_to_world(s, e)
    pois, houses, streets, towns, hoods = {}, [], defaultdict(list), {}, {}
    road_cells = defaultdict(list)
    with open(pm, 'rb') as f:
        reader = Reader(MmapSource(f))
        for tx in range(int(xa // 4096), int(xb // 4096) + 1):
            for ty in range(int(ya // 4096), int(yb // 4096) + 1):
                data = reader.get(M.Z, tx, ty)
                if not data: continue
                if data[:2] == b'\x1f\x8b': data = gzip.decompress(data)
                F = features(data)
                ll = lambda p: M.world_to_ll(tx * 4096 + p[0], ty * 4096 + p[1])
                inside = lambda p: 0 <= p[0] <= 4096 and 0 <= p[1] <= 4096
                for props, typ, parts in F['pois']:
                    nm = str(props.get('name:es') or props.get('name') or '').strip()
                    kind = str(props.get('kind') or '')
                    if not nm or kind in SKIP or typ != 1 or not parts: continue
                    lat, lon = ll(parts[0][0])
                    key = (nm.lower(), kind, round(lat, 3), round(lon, 3))
                    pois.setdefault(key, [nm, kind, round(lat, 5), round(lon, 5)])
                for props, typ, parts in F['places']:
                    nm = str(props.get('name:es') or props.get('name') or '').strip()
                    det = props.get('kind_detail')
                    if not nm or typ != 1 or not parts: continue
                    p = M.m(*ll(parts[0][0]))
                    if det in ('city', 'town', 'village', 'hamlet'): towns.setdefault(nm, (det, p))
                    elif det in ('suburb', 'neighbourhood', 'quarter'): hoods.setdefault(nm, p)
                for props, typ, parts in F['buildings']:
                    hn = props.get('addr_housenumber')
                    if not hn or not parts: continue
                    c = parts[0][0] if typ == 1 else centroid(parts[0])   # address points, or buildings
                    if not inside(c): continue          # each building once: in the tile holding its centre
                    lat, lon = ll(c); houses.append((str(hn).strip(), lat, lon))
                for props, typ, parts in F['roads']:
                    nm = props.get('name')
                    if not nm or typ != 2 or props.get('kind') == 'path' and props.get('kind_detail') != 'pedestrian': continue
                    for line in parts:
                        pts = [M.m(*ll(p)) for p in line]
                        if len(pts) < 2: continue
                        L = sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))
                        streets[nm].append((L, pts))
                        for a, b in zip(pts, pts[1:]):
                            for cx in range(int(min(a[0], b[0]) // 50), int(max(a[0], b[0]) // 50) + 1):
                                for cy in range(int(min(a[1], b[1]) // 50), int(max(a[1], b[1]) // 50) + 1):
                                    road_cells[(cx, cy)].append((nm, a, b))

    def nearest_street(p):
        best = (61, None)
        for cx in range(int(p[0] // 50) - 1, int(p[0] // 50) + 2):
            for cy in range(int(p[1] // 50) - 1, int(p[1] // 50) + 2):
                for nm, a, b in road_cells.get((cx, cy), ()):
                    dx, dy = b[0] - a[0], b[1] - a[1]; L2 = dx * dx + dy * dy
                    t = max(0, min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2)) if L2 else 0
                    d = math.dist(p, (a[0] + t * dx, a[1] + t * dy))
                    if d < best[0]: best = (d, nm)
        return best[1]

    def label(nm):
        if nm not in area_ix: area_ix[nm] = len(areas); areas.append(nm)
        return area_ix[nm]

    # The area of a point: a village or hamlet within 1.2 km (they are small), else Burgos if within 7 km of
    # its centre, else the nearest town. (OSM's neighbourhood names in Burgos are too patchy to show.)
    areas, area_ix = [], {}
    city = next(((nm, p) for nm, (det, p) in towns.items() if det == 'city'), None)
    small = [(nm, p) for nm, (det, p) in towns.items() if det in ('village', 'hamlet')]
    big = [(nm, p) for nm, (det, p) in towns.items() if det in ('city', 'town')]
    def area(p):
        d, nm = min(((math.dist(p, q), n) for n, q in small), default=(1e9, None))
        if d > 1200:
            nm = None
            if city and math.dist(p, city[1]) < 7000: nm = city[0]
            if nm is None:
                nm = min(((math.dist(p, q), n) for n, q in big), default=(0, ''))[1]
        return label(nm)

    def middle(pts, L):
        acc, half = 0, L / 2
        for a, b in zip(pts, pts[1:]):
            d = math.dist(a, b)
            if acc + d >= half:
                f = (half - acc) / d if d else 0; return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)
            acc += d
        return pts[-1]

    # a street name used in several villages is several streets: group its pieces by the town they are in
    # (in Burgos itself, one street crosses several neighbourhoods, so the town decides, not the neighbourhood)
    def town_of(p):
        d, nm = min(((math.dist(p, q), n) for n, q in small), default=(1e9, None))
        return nm if d <= 1200 else (city[0] if city and math.dist(p, city[1]) < 7000 else '')
    st_out, st_group = [], {}
    for nm, pieces in streets.items():
        groups = defaultdict(list)
        for L, pts in pieces: groups[town_of(middle(pts, L))].append((L, pts))
        for town, ps in groups.items():
            L, pts = max(ps); mid = middle(pts, L); q = M.unm(*mid)
            st_out.append([nm, area(mid), round(q[0], 5), round(q[1], 5)]); st_group[(nm, town)] = st_out[-1][1]
    addr = defaultdict(dict)
    for hn, lat, lon in houses:
        p = M.m(lat, lon); st = nearest_street(p)
        if not st or not re.match(r'^\d+[a-zA-Z]?$', hn): continue
        key = (st, st_group.get((st, town_of(p)), area(p)))
        addr[key].setdefault(hn, [hn, round(lat, 5), round(lon, 5)])
    # a place is shown with the street it's on (within 60 m) and its village, or just the street in Burgos
    for v in pois.values():
        p = M.m(v[2], v[3]); st, town = nearest_street(p), areas[area(p)]
        if st == v[0]: st = None              # a square or park named after itself
        v.append(label(st if st and town == (city[0] if city else '') else (st + ', ' + town if st else town)))
    # neighbourhoods, villages and towns are places too ("Gamonal", "Villímar", "Tardajos")
    for nm, p in hoods.items():
        q = M.unm(*p); pois[('area', nm)] = [nm, 'neighbourhood', round(q[0], 5), round(q[1], 5), label(areas[area(p)])]
    for nm, (det, p) in towns.items():
        q = M.unm(*p); pois[('area', nm)] = [nm, det, round(q[0], 5), round(q[1], 5), label('')]
    out = {'l': areas, 'p': sorted(pois.values()), 's': sorted(st_out),
           'a': [[k[0], k[1], sorted(v.values(), key=lambda x: (int(re.match(r'\d+', x[0]).group()), x[0]))] for k, v in sorted(addr.items())]}
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, separators=(',', ':'))
    print('places %d, streets %d, addresses %d on %d streets, %d KB' % (
        len(out['p']), len(out['s']), sum(len(x[2]) for x in out['a']), len(out['a']), os.path.getsize(out_path) // 1024))


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, '..', 'map', 'burgos.pmtiles'),
         sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, '..', 'map', 'places.json'))
