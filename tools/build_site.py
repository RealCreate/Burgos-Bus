"""Rebuild index.html from a fresh Burgos GTFS zip.

Usage:  python3 tools/build_site.py Google_transit.zip
        python3 tools/build_site.py --keep-data      (template or translation change only:
                                                      reuse the timetable already in index.html)
Get the zip from https://www.aytoburgos.es/GTFS/Google_transit.zip
"""
import datetime, json, os, re, subprocess, sys, tempfile, zipfile
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

HEAD = ('<!doctype html>\n<html lang="es">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">\n'
        '<meta name="apple-mobile-web-app-capable" content="yes">\n'
        '<meta name="theme-color" content="#0D5DA6">\n'
        '<meta name="apple-mobile-web-app-title" content="Burgos Bus">\n'
        '<meta name="apple-mobile-web-app-status-bar-style" content="default">\n'
        '<link rel="apple-touch-icon" href="apple-touch-icon.png">\n'
        '<link rel="icon" type="image/png" sizes="192x192" href="icons/icon-192.png">\n'
        '<link rel="manifest" href="manifest.webmanifest">\n'
        '<script>if(\'serviceWorker\' in navigator)addEventListener(\'load\',function(){'
        'navigator.serviceWorker.register(\'sw.js\').catch(function(){})})</script>\n'
        '<style>:root{padding:env(safe-area-inset-top,0px) 0 env(safe-area-inset-bottom,0px)}'
        'body{margin:0}[hidden]{display:none!important}</style>\n')


def current_data():
    """The timetable JSON embedded in the current index.html (already escaped for a <script>)."""
    page = open(os.path.join(ROOT, 'index.html'), encoding='utf-8').read()
    m = re.search(r'<script type="application/json" id="gtfs">(.*?)</script>', page, re.S)
    if not m:
        sys.exit('index.html has no timetable data; build from a GTFS zip instead')
    return m.group(1)


def gtfs_data(zip_path):
    with tempfile.TemporaryDirectory() as tmp:
        gtfs = os.path.join(tmp, 'gtfs')
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(gtfs)
        data_json = os.path.join(tmp, 'data.json')
        subprocess.run([sys.executable, '-I', os.path.join(HERE, 'build_data.py'), gtfs, data_json], check=True)
        return open(data_json, encoding='utf-8').read().replace('</', '<\\/')


def main(src):
    data = current_data() if src == '--keep-data' else gtfs_data(src)
    css = open(os.path.join(HERE, 'leaflet.css'), encoding='utf-8').read()
    css = re.sub(r'/\*.*?\*/', '', css, flags=re.S)
    css = re.sub(r'[^{}]*\{[^{}]*url\([^)]*\)[^{}]*\}', '', css)

    page = open(os.path.join(HERE, 'template.html'), encoding='utf-8').read()
    page = page.replace('__LEAFLET_CSS__', css).replace('__STANDALONE__', 'true')
    i18n = open(os.path.join(HERE, 'i18n.js'), encoding='utf-8').read()
    i18n = json.loads(re.sub(r'^\s*/\*.*?\*/', '', i18n, flags=re.S))
    keys = set(i18n['en'])
    for code, d in i18n.items():
        missing = keys - set(d)
        if missing:
            print('warning: %s is missing %s' % (code, ', '.join(sorted(missing))))
    page = page.replace('__I18N__', json.dumps(i18n, ensure_ascii=False).replace('</', '<\\/'))
    now = datetime.datetime.now(ZoneInfo('Europe/Madrid'))   # CEST in summer, CET in winter
    page = page.replace('__BUILD_ID__', str(int(now.timestamp())))
    page = page.replace('__BUILD__', now.strftime('%d %b %H:%M'))
    # timetable last, so nothing in it can be mistaken for a placeholder
    page = page.replace('__DATA__', data)
    i = page.index('<div class="app"')
    out = HEAD + page[:i] + '</head>\n<body>\n' + page[i:] + '\n</body>\n</html>\n'
    with open(os.path.join(ROOT, 'index.html'), 'w', encoding='utf-8') as f:
        f.write(out)
    print('Wrote index.html (%d KB)' % (len(out.encode()) // 1024))


if __name__ == '__main__':
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
