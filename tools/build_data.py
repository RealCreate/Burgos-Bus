import csv, json, sys, os
from collections import defaultdict, Counter

G = sys.argv[1]
OUT = sys.argv[2]

def rd(name):
    with open(os.path.join(G, name), encoding='utf-8-sig') as f:
        r = csv.DictReader(f)
        r.fieldnames = [h.strip() for h in r.fieldnames]
        return [{k.strip(): (v or '').strip() for k, v in row.items()} for row in r]

def tsec(t):
    h, m, s = t.split(':')
    return int(h) * 3600 + int(m) * 60 + int(s)

def enc_poly(pts, prec=1e5):
    out = []
    pl = pg = 0
    for lat, lon in pts:
        a, b = int(round(lat * prec)), int(round(lon * prec))
        for v in (a - pl, b - pg):
            v = ~(v << 1) if v < 0 else (v << 1)
            while v >= 0x20:
                out.append(chr((0x20 | (v & 0x1f)) + 63)); v >>= 5
            out.append(chr(v + 63))
        pl, pg = a, b
    return ''.join(out)

routes = rd('routes.txt')
stops = rd('stops.txt')
trips = rd('trips.txt')
st = rd('stop_times.txt')
cd = rd('calendar_dates.txt')
shapes = rd('shapes.txt')

# stops
stop_idx = {}
S = []
for s in stops:
    stop_idx[s['stop_id']] = len(S)
    S.append([s['stop_code'], s['stop_name'], round(float(s['stop_lat']), 6), round(float(s['stop_lon']), 6)])

# shapes: point list + cumulative distance
shp = defaultdict(list)
for p in shapes:
    shp[p['shape_id']].append((int(p['shape_pt_sequence']), float(p['shape_pt_lat']), float(p['shape_pt_lon']), float(p['shape_dist_traveled'] or 0)))

# stop_times per trip
tt = defaultdict(list)
for r in st:
    tt[r['trip_id']].append((int(r['stop_sequence']), r['stop_id'], tsec(r['arrival_time']), tsec(r['departure_time']), float(r['shape_dist_traveled'] or 0)))

# services
svc_idx = {}
SV = []
def sid(x):
    if x not in svc_idx:
        svc_idx[x] = len(SV); SV.append(x)
    return svc_idx[x]
dates = defaultdict(list)
for r in cd:
    if r['exception_type'] == '1':
        dates[r['date']].append(sid(r['service_id']))

# patterns
route_ids = {r['route_id']: i for i, r in enumerate(routes)}
pat_key = {}
P = []  # [routeIdx, dir, headsign, shapeKey, [stopIdx...], [dist...], profiles[], trips[]]
used_shapes = {}
for t in trips:
    rows = sorted(tt.get(t['trip_id'], []))
    if len(rows) < 2:
        continue
    sids = tuple(stop_idx[x[1]] for x in rows)
    key = (t['route_id'], t['direction_id'], sids, t['shape_id'])
    if key not in pat_key:
        if t['shape_id'] not in used_shapes:
            used_shapes[t['shape_id']] = len(used_shapes)
        pat_key[key] = len(P)
        P.append({'r': route_ids[t['route_id']], 'd': int(t['direction_id'] or 0), 'h': Counter(), 'sh': used_shapes[t['shape_id']],
                  's': list(sids), 'dist': [round(x[4]) for x in rows], 'prof': {}, 'trips': []})
    p = P[pat_key[key]]
    p['h'][t['trip_headsign']] += 1
    start = rows[0][3]
    prof = tuple(x[2] - start for x in rows)
    if prof not in p['prof']:
        p['prof'][prof] = len(p['prof'])
    p['trips'].append([start, p['prof'][prof], sid(t['service_id'])])

SH = [None] * len(used_shapes)
for k, i in used_shapes.items():
    pts = sorted(shp[k])
    SH[i] = [enc_poly([(a[1], a[2]) for a in pts]), [round(a[3]) for a in pts]]
    # delta-encode distances
    d = SH[i][1]
    SH[i][1] = [d[0]] + [d[j] - d[j - 1] for j in range(1, len(d))]

outP = []
for p in P:
    p['trips'].sort()
    profs = [None] * len(p['prof'])
    for pr, i in p['prof'].items():
        profs[i] = list(pr)
    outP.append([p['r'], p['d'], p['h'].most_common(1)[0][0], p['sh'], p['s'], p['dist'], profs,
                 [x for tr in p['trips'] for x in tr]])

R = [[r['route_short_name'], r['route_long_name'], r['route_color'], r['route_text_color']] for r in routes]
data = {'routes': R, 'stops': S, 'shapes': SH, 'patterns': outP, 'dates': dict(sorted(dates.items())),
        'nsvc': len(SV)}
with open(OUT, 'w') as f:
    json.dump(data, f, separators=(',', ':'), ensure_ascii=False)
print('patterns', len(P), 'shapes', len(SH), 'profiles', sum(len(p[6]) for p in outP), 'size', os.path.getsize(OUT))
for p in outP:
    print(R[p[0]][0], p[1], p[2], len(p[4]), 'stops', len(p[7]) // 3, 'trips')
