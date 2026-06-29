# ##########################################################
# FlatCAM 9 Neo S2
# PDF Content Analyzer
# ##########################################################

from __future__ import annotations

import os
import re
import zlib


PDF_VECTOR_OPERATORS = (
    'm', 'l', 'c', 'v', 'y', 'h', 're', 'S', 's', 'f', 'F', 'B', 'b',
    'W', 'cm', 'w', 'RG', 'rg', 'BT', 'ET', 'Do'
)

PDF_COLOR_SPACES = ('DeviceRGB', 'DeviceCMYK', 'DeviceGray')


def _default_result(pdf_filename):
    return {
        'source': 'unknown',
        'content_type': 'unknown',
        'confidence': 0.0,
        'score': 0,
        'pages': None,
        'pdf_version': None,
        'producer': None,
        'creator': None,
        'mediabox': None,
        'cropbox': None,
        'rotate': None,
        'has_vector': False,
        'has_raster': False,
        'has_text': False,
        'has_clip_paths': False,
        'has_transparency': False,
        'has_xobjects': False,
        'image_count': 0,
        'xobject_count': 0,
        'vector_operator_counts': dict((op, 0) for op in PDF_VECTOR_OPERATORS),
        'color_spaces': [],
        'warnings': [],
        'recommendations': [],
        'file': pdf_filename,
    }


def _decode_pdf_literal(raw_value):
    if raw_value is None:
        return None

    raw_value = raw_value.strip()
    if raw_value.startswith(b'\xfe\xff'):
        try:
            return raw_value[2:].decode('utf-16-be', errors='replace')
        except Exception:
            pass

    try:
        return raw_value.decode('latin-1', errors='replace')
    except Exception:
        return repr(raw_value)


def _find_literal(data, key):
    match = re.search(rb'/' + key.encode('ascii') + rb'\s*\((.*?)\)', data, re.S)
    if not match:
        return None
    return _decode_pdf_literal(match.group(1))


def _find_array(data, key):
    match = re.search(rb'/' + key.encode('ascii') + rb'\s*\[([^\]]+)\]', data, re.S)
    if not match:
        return None
    value = re.sub(rb'\s+', b' ', match.group(1)).strip()
    return _decode_pdf_literal(value)


def _find_number(data, key):
    match = re.search(rb'/' + key.encode('ascii') + rb'\s+(-?\d+\.?\d*)', data)
    if not match:
        return None
    try:
        number = float(match.group(1))
        if number.is_integer():
            return int(number)
        return number
    except Exception:
        return _decode_pdf_literal(match.group(1))


def _iter_pdf_streams(data):
    pos = 0
    while True:
        stream_pos = data.find(b'stream', pos)
        if stream_pos < 0:
            break

        if stream_pos >= 3 and data[stream_pos - 3:stream_pos] == b'end':
            pos = stream_pos + 6
            continue

        end_pos = data.find(b'endstream', stream_pos)
        if end_pos < 0:
            break

        raw_start = stream_pos + len(b'stream')
        if data[raw_start:raw_start + 2] == b'\r\n':
            raw_start += 2
        elif data[raw_start:raw_start + 1] in (b'\r', b'\n'):
            raw_start += 1

        raw = data[raw_start:end_pos]
        if raw.endswith(b'\r\n'):
            raw = raw[:-2]
        elif raw.endswith(b'\r') or raw.endswith(b'\n'):
            raw = raw[:-1]

        header_start = data.rfind(b'<<', 0, stream_pos)
        header = data[header_start:stream_pos] if header_start >= 0 else b''

        yield header, raw
        pos = end_pos + len(b'endstream')


def _decode_stream(header, raw):
    if b'/FlateDecode' not in header and b'/Fl ' not in header:
        return raw, 'raw'

    for candidate in (raw, raw.strip()):
        try:
            return zlib.decompress(candidate), 'FlateDecode'
        except Exception:
            pass
        try:
            return zlib.decompress(candidate, -15), 'RawDeflate'
        except Exception:
            pass

    return b'', 'FlateDecode-failed'


def _count_operator(data, operator):
    token = operator.encode('ascii')
    pattern = rb'(?<![/A-Za-z0-9_.+-])' + re.escape(token) + rb'(?![A-Za-z0-9_.+-])'
    return len(re.findall(pattern, data))


def _collect_color_spaces(data):
    spaces = set()
    for color_space in PDF_COLOR_SPACES:
        if re.search(rb'/' + color_space.encode('ascii') + rb'\b', data):
            spaces.add(color_space)
    return spaces


def _detect_source(producer, creator, xmp_text, data, pdf_filename):
    evidence = ' '.join([
        os.path.basename(pdf_filename or ''),
        producer or '',
        creator or '',
        xmp_text or '',
        _decode_pdf_literal(data[:4096]) or '',
    ]).lower()

    if 'illustrator' in evidence or 'adobe pdf library' in evidence:
        return 'illustrator'

    if 'coreldraw' in evidence or 'corel pdf engine' in evidence or 'corel' in evidence:
        return 'coreldraw'

    if 'proteus' in evidence or 'labcenter' in evidence:
        return 'proteus'

    return 'unknown'


def _classify_content(vector_total, image_count, content_stream_count):
    has_vector = vector_total > 20
    has_raster = image_count > 0

    if has_vector and has_raster:
        return 'mixed', 0.85

    if has_vector:
        confidence = 0.90 if vector_total > 100 else 0.70
        return 'vector', confidence

    if has_raster:
        return 'raster', 0.85

    if content_stream_count > 0:
        return 'unknown', 0.35

    return 'unknown', 0.10


def _build_recommendations(result):
    content_type = result['content_type']
    source = result['source']

    if content_type == 'vector':
        result['recommendations'].append(
            'Vector PDF detected. Current PDF Import Tool can be evaluated as PDF as Gerber Object.'
        )
    elif content_type == 'raster':
        result['recommendations'].append(
            'Raster PDF detected. Use future Raster Vectorization workflow or export vector PDF/SVG/Gerber when possible.'
        )
    elif content_type == 'mixed':
        result['recommendations'].append(
            'Mixed vector/raster PDF detected. Verify which content is intended for CAM before import.'
        )
    else:
        result['recommendations'].append(
            'PDF content is ambiguous. Inspect the file before using it for CAM.'
        )

    if source == 'illustrator':
        result['recommendations'].append(
            'Adobe Illustrator source detected. Prefer vector PDF or SVG Tiny 1.2 when CAM geometry is required.'
        )
    elif source == 'proteus':
        result['recommendations'].append(
            'Proteus source detected. Classify by content because Proteus may export vector or raster PDF.'
        )
    elif source == 'coreldraw':
        result['recommendations'].append(
            'CorelDRAW source detected. Verify pages, clipping and embedded raster images.'
        )


def analyze_pdf_source(pdf_filename):
    """
    Analyze a PDF file without modifying it or importing geometry.

    :param pdf_filename: Path to a PDF file.
    :return: Dictionary with diagnostic information.
    """
    result = _default_result(pdf_filename)

    try:
        with open(pdf_filename, 'rb') as pdf_file:
            data = pdf_file.read()
    except Exception as exc:
        result['warnings'].append('Could not read PDF file: %s' % exc)
        return result

    version_match = re.search(rb'%PDF-(\d+\.\d+)', data)
    if version_match:
        result['pdf_version'] = _decode_pdf_literal(version_match.group(1))

    result['producer'] = _find_literal(data, 'Producer')
    result['creator'] = _find_literal(data, 'Creator')
    result['mediabox'] = _find_array(data, 'MediaBox')
    result['cropbox'] = _find_array(data, 'CropBox')
    result['rotate'] = _find_number(data, 'Rotate')
    result['pages'] = len(re.findall(rb'/Type\s*/Page\b', data))

    result['xobject_count'] = len(re.findall(rb'/XObject\b', data))
    result['has_xobjects'] = result['xobject_count'] > 0

    raw_image_count = len(re.findall(rb'/Subtype\s*/Image\b', data))
    xmp_matches = re.findall(rb'<x:xmpmeta.*?</x:xmpmeta>', data, re.S | re.I)
    xmp_text = '\n'.join(_decode_pdf_literal(xmp) or '' for xmp in xmp_matches)

    result['has_transparency'] = bool(
        re.search(rb'/SMask\b|/ExtGState\b|/ca\b|/CA\b|/Group\s*<<[^>]*?/S\s*/Transparency', data, re.S)
    )

    color_spaces = _collect_color_spaces(data)
    content_stream_count = 0
    image_stream_count = 0
    decode_failures = 0

    for header, raw in _iter_pdf_streams(data):
        stream_is_image = bool(re.search(rb'/Subtype\s*/Image\b', header))
        decoded, filter_name = _decode_stream(header, raw)

        if stream_is_image:
            image_stream_count += 1
            color_spaces.update(_collect_color_spaces(header))
            continue

        if filter_name.endswith('failed'):
            decode_failures += 1
            continue

        if not decoded:
            continue

        content_stream_count += 1
        color_spaces.update(_collect_color_spaces(decoded))

        for operator in PDF_VECTOR_OPERATORS:
            result['vector_operator_counts'][operator] += _count_operator(decoded, operator)

        if result['vector_operator_counts']['BT'] or result['vector_operator_counts']['ET']:
            result['has_text'] = True

        if result['vector_operator_counts']['W']:
            result['has_clip_paths'] = True

    result['image_count'] = max(raw_image_count, image_stream_count)
    result['has_raster'] = result['image_count'] > 0

    vector_total = sum(
        result['vector_operator_counts'][op]
        for op in ('m', 'l', 'c', 'v', 'y', 'h', 're', 'S', 's', 'f', 'F', 'B', 'b', 'W')
    )
    result['has_vector'] = vector_total > 20

    result['content_type'], result['confidence'] = _classify_content(
        vector_total=vector_total,
        image_count=result['image_count'],
        content_stream_count=content_stream_count
    )
    result['score'] = vector_total - (result['image_count'] * 10)

    result['color_spaces'] = sorted(color_spaces)
    result['source'] = _detect_source(result['producer'], result['creator'], xmp_text, data, pdf_filename)

    if decode_failures:
        result['warnings'].append('Some compressed PDF streams could not be decoded: %d' % decode_failures)

    if result['pages'] == 0:
        result['warnings'].append('No explicit /Page objects were detected by the lightweight analyzer.')

    if result['content_type'] == 'raster':
        result['warnings'].append('PDF appears to be raster/image based; no vector CAM geometry is guaranteed.')
    elif result['content_type'] == 'mixed':
        result['warnings'].append('PDF contains both vector operators and raster/XObject usage.')
    elif result['content_type'] == 'unknown':
        result['warnings'].append('Insufficient evidence to classify PDF content.')

    if result['has_text']:
        result['warnings'].append('Text operators detected. Text may need conversion to outlines for CAM.')

    if result['has_clip_paths']:
        result['warnings'].append('Clip paths detected. Visible geometry may differ from raw paths.')

    if result['has_transparency']:
        result['warnings'].append('Transparency resources detected. Verify rendered output before machining.')

    _build_recommendations(result)
    return result


if __name__ == '__main__':
    import json
    import sys

    if len(sys.argv) < 2:
        print('Usage: python -m appParsers.PDFContentAnalyzer <pdf-file> [<pdf-file> ...]')
        sys.exit(1)

    for filename in sys.argv[1:]:
        if not os.path.isfile(filename):
            print(json.dumps(_default_result(filename), indent=2, sort_keys=True))
            continue
        print(json.dumps(analyze_pdf_source(filename), indent=2, sort_keys=True))
