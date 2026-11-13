"""Validate retained real notebook image payloads; never execute SVG or image content."""
import base64
import hashlib
import struct
import xml.etree.ElementTree as ET


def image_payloads(cell):
    records = []
    for output in cell.get('outputs', []):
        data = output.get('data', {})
        if 'image/png' in data:
            text = data['image/png']
            assert isinstance(text, str) and len(text) <= 12 * 1024**2
            encoded = text.encode('ascii').translate(None, b' \t\r\n')
            raw = base64.b64decode(encoded, validate=True)
            assert 24 <= len(raw) <= 8 * 1024**2 and raw.startswith(b'\x89PNG\r\n\x1a\n')
            assert raw[12:16] == b'IHDR'
            width, height = struct.unpack('>II', raw[16:24])
            assert width > 1 and height > 1
            records.append({'mime': 'image/png', 'bytes': len(raw), 'width': width, 'height': height,
                            'sha256': hashlib.sha256(raw).hexdigest()})
        if 'image/svg+xml' in data:
            text = data['image/svg+xml']
            assert isinstance(text, str)
            raw = text.encode('utf-8')
            assert 0 < len(raw) <= 8 * 1024**2
            root = ET.fromstring(raw)
            assert root.tag == '{http://www.w3.org/2000/svg}svg'
            records.append({'mime': 'image/svg+xml', 'bytes': len(raw),
                            'sha256': hashlib.sha256(raw).hexdigest()})
    return records


def require_images(cell, png=0, svg=0):
    rows = image_payloads(cell)
    assert sum(r['mime'] == 'image/png' for r in rows) >= png, 'REQUIRED_NOTEBOOK_PNG_OUTPUT_MISSING'
    assert sum(r['mime'] == 'image/svg+xml' for r in rows) >= svg, 'REQUIRED_NOTEBOOK_SVG_OUTPUT_MISSING'
    return rows


def candidate_visual_contract(notebook):
    # Source-ordered real contribution cells, not the trailing private evidence probe.
    expected = {3: (1, 0), 5: (0, 3), 7: (0, 1), 13: (4, 0),
                15: (1, 0), 17: (2, 0), 21: (1, 0)}
    assert len(notebook.cells) == 26
    result = []
    for index, (png, svg) in expected.items():
        rows = require_images(notebook.cells[index], png=png, svg=svg)
        result.append({'cell': index, 'requiredPNG': png, 'requiredSVG': svg, 'images': rows})
    return {'status': 'PASS', 'requiredRasterPlots': 9, 'requiredModelGraphs': 4,
            'cells': result, 'actualPixelAppearance': 'REQUIRES_LATER_REAL_VISUAL_INSPECTION'}
