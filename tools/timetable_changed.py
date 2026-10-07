"""Compare the timetable embedded in two builds of index.html.

Usage:  python3 tools/timetable_changed.py OLD_index.html NEW_index.html
Exit 0 and print 'changed' when the new timetable differs and looks sane,
exit 0 and print 'same' when nothing changed, exit 1 when the new one looks broken.
Only the timetable is compared: every build has a new build stamp, which on its own
is no reason to publish.
"""
import json, re, sys


def data(path):
    page = open(path, encoding='utf-8').read()
    m = re.search(r'<script type="application/json" id="gtfs">(.*?)</script>', page, re.S)
    return json.loads(m.group(1).replace('<\\/', '</'))


def trips_by_day(d):
    """Every scheduled trip per day, independent of how services happen to be numbered."""
    out = {}
    for day, svcs in d['dates'].items():
        s = set(svcs)
        out[day] = sorted((p[0], p[1], p[2], tuple(p[4]), tf[j], tuple(p[6][tf[j + 1]]))
                          for p in d['patterns'] for tf in [p[7]] for j in range(0, len(tf), 3)
                          if tf[j + 2] in s)
    return out


old, new = data(sys.argv[1]), data(sys.argv[2])
same = (old['routes'] == new['routes'] and old['stops'] == new['stops']
        and old['shapes'] == new['shapes'] and trips_by_day(old) == trips_by_day(new))
if same:
    print('same')
    sys.exit(0)

problems = []
if len(new['routes']) < 10 or len(new['stops']) < 100 or len(new['patterns']) < 20:
    problems.append('far fewer lines, stops or routes than expected')
if not new['dates']:
    problems.append('no service dates')
elif max(new['dates']) < max(old['dates']):
    problems.append('ends earlier (%s) than the current one (%s)' % (max(new['dates']), max(old['dates'])))
if problems:
    print('new timetable looks wrong: ' + '; '.join(problems))
    sys.exit(1)
print('changed: now runs %s - %s' % (min(new['dates']), max(new['dates'])))
