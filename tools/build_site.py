"""Rebuild index.html from a fresh Burgos GTFS zip.

Usage:  python3 tools/build_site.py Google_transit.zip
Get the zip from https://www.aytoburgos.es/GTFS/Google_transit.zip
"""
import os, re, subprocess, sys, tempfile, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

HEAD = ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">\n'
        '<meta name="apple-mobile-web-app-capable" content="yes">\n'
        '<meta name="theme-color" content="#0D5DA6">\n'
        '<style>:root{padding:env(safe-area-inset-top,0px) 0 env(safe-area-inset-bottom,0px)}'
        'body{margin:0}[hidden]{display:none!important}</style>\n')


def main(zip_path):
    with tempfile.TemporaryDirectory() as tmp:
        gtfs = os.path.join(tmp, 'gtfs')
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(gtfs)
        data_json = os.path.join(tmp, 'data.json')
        subprocess.run([sys.executable, '-I', os.path.join(HERE, 'build_data.py'), gtfs, data_json], check=True)
        data = open(data_json, encoding='utf-8').read().replace('</', '<\\/')

    css = open(os.path.join(HERE, 'leaflet.css'), encoding='utf-8').read()
    css = re.sub(r'/\*.*?\*/', '', css, flags=re.S)
    css = re.sub(r'[^{}]*\{[^{}]*url\([^)]*\)[^{}]*\}', '', css)

    page = open(os.path.join(HERE, 'template.html'), encoding='utf-8').read()
    page = page.replace('__LEAFLET_CSS__', css).replace('__DATA__', data).replace('__STANDALONE__', 'true')
    i = page.index('<div class="app"')
    out = HEAD + page[:i] + '</head>\n<body>\n' + page[i:] + '\n</body>\n</html>\n'
    with open(os.path.join(ROOT, 'index.html'), 'w', encoding='utf-8') as f:
        f.write(out)
    print('Wrote index.html (%d KB)' % (len(out.encode()) // 1024))


if __name__ == '__main__':
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
