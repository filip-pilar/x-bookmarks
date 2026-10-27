"""Bounded SVG rasterization without following referenced resources."""
import base64, io, re, subprocess, sys, time
from pathlib import Path


def convert_svg(path):
    path = Path(path)
    with path.open('rb') as source:
        head = source.read(1024).lstrip()
    if b'<svg' not in head:
        return False
    if path.stat().st_size > 2 * 1024**2:
        raise ValueError('svg_size_limit')
    process = subprocess.Popen([sys.executable, __file__, str(path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    deadline = time.monotonic() + 30
    try:
        while True:
            try:
                output, _ = process.communicate(timeout=.5)
                if process.returncode:
                    raise ValueError('svg_unsupported_or_invalid')
                from PIL import Image
                with Image.open(io.BytesIO(output)) as image:
                    image.verify()
                path.write_bytes(output)
                return True
            except subprocess.TimeoutExpired:
                if time.monotonic() >= deadline:
                    raise ValueError('svg_render_timeout')
                memory = subprocess.run(['/bin/ps', '-o', 'rss=', '-p', str(process.pid)], capture_output=True, text=True)
                if process.poll() is None and (memory.returncode or not memory.stdout.strip() or int(memory.stdout) > 768 * 1024):
                    raise ValueError('svg_resource_limit')
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate()


def checked_svg(data, depth=0):
    from lxml import etree
    if depth > 3 or len(data) > 2 * 1024**2 or re.search(rb'<!DOCTYPE|<!ENTITY|@import', data, re.I):
        raise SystemExit('Unsupported SVG')
    root = etree.fromstring(data, etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False))
    if etree.QName(root).localname != 'svg':
        raise SystemExit('Not SVG')
    for element in root.iter():
        if not isinstance(element.tag, str):
            continue
        if etree.QName(element).localname in ('script', 'foreignObject'):
            raise SystemExit('Unsupported SVG content')
        for attribute, value in element.attrib.items():
            if etree.QName(attribute).localname == 'href':
                if value.startswith('data:image/svg+xml;base64,'):
                    nested = checked_svg(base64.b64decode(value.split(',', 1)[1], validate=True), depth + 1)
                    element.set(attribute, 'data:image/svg+xml;base64,' + base64.b64encode(nested).decode())
                elif not value.startswith(('#', 'data:image/png;base64,', 'data:image/jpeg;base64,', 'data:image/webp;base64,')):
                    raise SystemExit('SVG references an external resource')
    for value in re.findall(r'url\(([^)]*)\)', data.decode('utf-8'), re.I):
        if not value.strip(' \t\r\n\"\'').startswith('#'):
            raise SystemExit('SVG references an external resource')
    return etree.tostring(root)


if __name__ == '__main__':
    from resvg_py import svg_to_bytes
    data = checked_svg(Path(sys.argv[1]).read_bytes())
    # Both dimensions bound the raster even for extreme SVG aspect ratios.
    output = svg_to_bytes(svg_string=data.decode(), width=1024, height=1024)
    sys.stdout.buffer.write(output)
