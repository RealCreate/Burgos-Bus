import csv, datetime, json, sys, os
from collections import defaultdict, Counter

G = sys.argv[1]
OUT = sys.argv[2]

def rd(name, optional=False):
    if optional and not os.path.exists(os.path.join(G, name)):
        return []
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
# Lines that exist only as internal codes in the feed, shown under the public line they belong to.
# 73: the 5:30 and 6:30 trips Gamonal -> Pol. Ind. Villalonquejar, printed as special trips of line 19.
MERGE = {'73': '19'}
_short = {r['route_id']: r['route_short_name'] for r in routes}
_target = {s: next((r['route_id'] for r in routes if r['route_short_name'] == t), None) for s, t in MERGE.items()}
_remap = {rid: _target[sn] for rid, sn in _short.items() if _target.get(sn)}
routes = [r for r in routes if r['route_id'] not in _remap]
stops = rd('stops.txt')
trips = rd('trips.txt')
for t in trips:
    t['route_id'] = _remap.get(t['route_id'], t['route_id'])
st = rd('stop_times.txt')
cal = rd('calendar.txt', optional=True)
cd = rd('calendar_dates.txt', optional=True)
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
    # GTFS lets non-timepoint stops leave the times blank; fall back to whichever is given
    arr, dep = r['arrival_time'] or r['departure_time'], r['departure_time'] or r['arrival_time']
    if not arr:
        sys.exit('stop_times.txt: trip %s has a stop with no time; interpolation is not supported' % r['trip_id'])
    tt[r['trip_id']].append((int(r['stop_sequence']), r['stop_id'], tsec(arr), tsec(dep), float(r['shape_dist_traveled'] or 0)))

# services
svc_idx = {}
SV = []
def sid(x):
    if x not in svc_idx:
        svc_idx[x] = len(SV); SV.append(x)
    return svc_idx[x]
# active services per date: weekly patterns from calendar.txt, then calendar_dates.txt
# additions (1) and removals (2)
day_svc = defaultdict(set)
WD = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
for r in cal:
    d = datetime.date(int(r['start_date'][:4]), int(r['start_date'][4:6]), int(r['start_date'][6:]))
    end = datetime.date(int(r['end_date'][:4]), int(r['end_date'][4:6]), int(r['end_date'][6:]))
    while d <= end:
        if r[WD[d.weekday()]] == '1':
            day_svc[d.strftime('%Y%m%d')].add(r['service_id'])
        d += datetime.timedelta(days=1)
for r in cd:
    if r['exception_type'] == '1':
        day_svc[r['date']].add(r['service_id'])
    elif r['exception_type'] == '2':
        day_svc[r['date']].discard(r['service_id'])
if not day_svc:
    sys.exit('no service dates: the feed has neither calendar.txt nor calendar_dates.txt entries')
dates = {k: sorted(sid(x) for x in sorted(v)) for k, v in sorted(day_svc.items()) if v}

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
with open(OUT, 'w', encoding='utf-8') as f:
    json.dump(data, f, separators=(',', ':'), ensure_ascii=False)
print('patterns', len(P), 'shapes', len(SH), 'profiles', sum(len(p[6]) for p in outP), 'size', os.path.getsize(OUT))
for p in outP:
    print(R[p[0]][0], p[1], p[2], len(p[4]), 'stops', len(p[7]) // 3, 'trips')
