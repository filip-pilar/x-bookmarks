#!/usr/bin/env python3
"""Verify the selected ChatGPT plan with generated media; never import bookmarks."""
import json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
from store import HOME, VERSION, atomic
from sources import checksum
from PIL import Image, ImageDraw
import subscription


def main():
    selected = subscription.status().get('model')
    if not selected:
        raise SystemExit('Connect ChatGPT and select an available description model first.')
    target = HOME / 'description-preflight.json'
    report = json.loads(target.read_text()) if target.exists() else {}
    if report.get('version') != VERSION or report.get('model') != selected:
        report = {'version': VERSION, 'model': selected, 'ready': False, 'examples': {}}
    directory = HOME / 'cache/description-preflight'
    directory.mkdir(parents=True, exist_ok=True)
    for name in ('image', 'frames'):
        if name in report['examples']:
            continue
        paths = []
        for i in range(1 if name == 'image' else 2):
            im = Image.new('RGB', (640, 360), 'white')
            d = ImageDraw.Draw(im)
            d.text((30, 20), 'BOOKMARK LIBRARY CHECK', fill='black', font_size=26)
            d.rectangle((40 + 270*i, 100, 180 + 270*i, 240), fill='red')
            d.ellipse((480, 120, 580, 220), fill='blue')
            path = directory / f'{name}-{i}.jpg'
            im.save(path, quality=85)
            paths.append((path, None if name == 'image' else float(i)))
        result = subscription.describe(paths, 'Generated setup verification; describe only the supplied pixels.')
        if not all(word in result['text'].lower() for word in ('red', 'blue')):
            raise RuntimeError('Description did not identify the controlled red and blue objects; review the result before declaring ready.')
        report['examples'][name] = {**result, 'inputs': [checksum(p) for p, _ in paths]}
        atomic(target, report)
    report.update(ready=True, at=time.time())
    atomic(target, report)
    for path in directory.glob('*.jpg'):
        path.unlink()
    directory.rmdir()
    print(json.dumps({'ready': True, 'version': VERSION, 'model': selected, 'report': str(target), 'import_started': False}))


if __name__ == '__main__':
    main()
