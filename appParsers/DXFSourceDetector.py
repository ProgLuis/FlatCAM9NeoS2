# ########################################################## ##
# FlatCAM 9 Neo S2 - DXF source/profile detector              #
# Pure diagnostic helper. It does not modify DXF geometry.     #
# ########################################################## ##

import os
import re
from collections import Counter, defaultdict

try:
    import ezdxf
except Exception:
    ezdxf = None


DXF_VERSION_NAMES = {
    'AC1009': 'R12',
    'AC1014': 'R14',
    'AC1015': 'R2000',
    'AC1018': 'R2004',
    'AC1021': 'R2007',
    'AC1024': 'R2010',
    'AC1027': 'R2013',
    'AC1032': 'R2018'
}


ADOBE_TOKENS = (
    'Adobe', 'Adobe Illustrator', 'Illustrator', 'CreatorTool', 'Producer',
    'XMP', 'xpacket', 'rdf:RDF', 'xmp:', 'pdf:', 'xmlns:xmp',
    'xmlns:pdf', '%%Creator'
)

KICAD_TOKENS = (
    'KICAD', 'KICADB', 'KICADI', 'KICADBI', 'PCBNEW', 'Pcbnew',
    'GenerationSoftware', 'F.Cu', 'B.Cu', 'Edge.Cuts', 'F.Mask',
    'B.Mask', 'F.SilkS', 'B.SilkS'
)

INKSCAPE_TOKENS = (
    'Inkscape', 'DXF R12 Output', 'mydxf.blogspot', 'Sodipodi',
    'ISO-25'
)

PROTEUS_TOKENS = (
    'Proteus', 'Labcenter', 'ARES', 'EPAD', 'RPAD'
)


def _default_result(warnings=None):
    return {
        'source': 'unknown',
        'export_profile': 'Unknown DXF source',
        'confidence': 'low',
        'score': 0,
        'acad_version': None,
        'dxf_version': None,
        'supports': {
            'geometry': False,
            'gerber': False,
            'drill_recognition': False,
            'preferred_import': 'manual_review'
        },
        'evidence': [],
        'warnings': warnings or [],
        'recommendations': [],
        'drill_recognition_policy': 'manual'
    }


def _confidence(score):
    if score >= 70:
        return 'high'
    if score >= 40:
        return 'medium'
    return 'low'


def _read_raw_text(filename):
    if not filename:
        return ''
    try:
        with open(filename, 'rb') as f:
            return f.read().decode('latin-1', 'ignore')
    except Exception:
        return ''


def _load_doc(filename, doc, warnings):
    if doc is not None:
        return doc

    if not filename:
        return None

    if ezdxf is None:
        warnings.append('ezdxf is not available; DXF structure could not be inspected.')
        return None

    try:
        return ezdxf.readfile(filename)
    except Exception as err:
        warnings.append('Could not read DXF file: %s' % err)
        return None


def _token_hits(raw_text, tokens):
    if not raw_text:
        return []
    hits = []
    for token in tokens:
        if re.search(re.escape(token), raw_text, re.IGNORECASE):
            hits.append(token)
    return hits


def _safe_layer(entity):
    try:
        return entity.dxf.layer
    except Exception:
        return '0'


def _collect_doc_info(doc):
    info = {
        'acadver': None,
        'version_name': None,
        'insunits': None,
        'measurement': None,
        'entity_counts': Counter(),
        'layer_counts': defaultdict(Counter),
        'block_counts': {},
        'block_names': set(),
        'insert_block_names': Counter(),
        'layers': set(),
        'modelspace_count': 0,
        'rectangular_polylines': 0,
        'single_rectangular_polyline': False
    }

    if doc is None:
        return info

    try:
        info['acadver'] = doc.dxfversion
        info['version_name'] = DXF_VERSION_NAMES.get(doc.dxfversion, 'unknown')
    except Exception:
        pass

    try:
        info['insunits'] = doc.header.get('$INSUNITS', None)
    except Exception:
        pass

    try:
        info['measurement'] = doc.header.get('$MEASUREMENT', None)
    except Exception:
        pass

    try:
        modelspace = list(doc.modelspace())
    except Exception:
        modelspace = []

    info['modelspace_count'] = len(modelspace)

    for entity in modelspace:
        try:
            etype = entity.dxftype()
        except Exception:
            etype = 'UNKNOWN'
        layer = _safe_layer(entity)
        info['entity_counts'][etype] += 1
        info['layer_counts'][layer][etype] += 1
        info['layers'].add(layer)

        if etype == 'INSERT':
            try:
                info['insert_block_names'][entity.dxf.name] += 1
            except Exception:
                pass

        if etype in ('POLYLINE', 'LWPOLYLINE') and _is_rectangular_polyline(entity):
            info['rectangular_polylines'] += 1

    info['single_rectangular_polyline'] = (
        len(modelspace) == 1 and info['rectangular_polylines'] == 1
    )

    try:
        blocks = list(doc.blocks)
    except Exception:
        blocks = []

    for block in blocks:
        name = getattr(block, 'name', None)
        if not name:
            continue
        info['block_names'].add(name)
        counts = Counter()
        try:
            for entity in block:
                counts[entity.dxftype()] += 1
        except Exception:
            pass
        if counts:
            info['block_counts'][name] = counts

    return info


def _is_rectangular_polyline(entity):
    try:
        if entity.dxftype() == 'POLYLINE':
            points = [(round(v.dxf.location.x, 6), round(v.dxf.location.y, 6))
                      for v in entity.vertices]
        elif entity.dxftype() == 'LWPOLYLINE':
            points = [(round(p[0], 6), round(p[1], 6)) for p in entity.get_points()]
        else:
            return False
    except Exception:
        return False

    if len(points) < 4:
        return False

    unique = list(dict.fromkeys(points))
    if len(unique) != 4:
        return False

    xs = set(x for x, _ in unique)
    ys = set(y for _, y in unique)
    return len(xs) == 2 and len(ys) == 2


def _has_block_entity(info, entity_type):
    for counts in info['block_counts'].values():
        if counts.get(entity_type, 0) > 0:
            return True
    return False


def _block_defined_not_inserted(info, name):
    return name in info['block_names'] and info['insert_block_names'].get(name, 0) == 0


def _score_illustrator(filename, raw_text, info):
    score = 0
    evidence = []
    warnings = []

    adobe_hits = _token_hits(raw_text, ADOBE_TOKENS)
    if adobe_hits:
        score += 40
        evidence.append('Adobe metadata tokens found: %s.' % ', '.join(adobe_hits[:5]))

    layers = set(info['layers'])
    illustrator_layers = {'Capa 1', 'CAPA_1', 'Capa_1'}
    if layers & illustrator_layers:
        score += 25
        evidence.append('Illustrator-like layer names found: %s.' % ', '.join(sorted(layers & illustrator_layers)))

    counts = info['entity_counts']
    if counts.get('HATCH', 0) and counts.get('SPLINE', 0):
        score += 25
        evidence.append('HATCH + SPLINE entity pattern found.')
    elif counts.get('SPLINE', 0) >= 4:
        score += 15
        evidence.append('Many SPLINE entities found.')

    known_blocks = {'_CLOSEDFILLED', '_DOTBLANK', 'block 2', 'BLOCK_2'}
    found_blocks = info['block_names'] & known_blocks
    if found_blocks or _has_block_entity(info, 'IMAGE'):
        score += 20
        if found_blocks:
            evidence.append('Illustrator-like blocks found: %s.' % ', '.join(sorted(found_blocks)))
        if _has_block_entity(info, 'IMAGE'):
            evidence.append('IMAGE entity found inside BLOCK, common in Illustrator mask exports.')

    if info['acadver'] in ('AC1014', 'AC1015', 'AC1024'):
        score += 10
        evidence.append('DXF version compatible with Illustrator export profile: %s/%s.' %
                        (info['acadver'], info['version_name']))

    if layers & illustrator_layers and info['measurement'] == 0 and not _token_hits(raw_text, INKSCAPE_TOKENS):
        score += 15
        evidence.append('Illustrator-like layer naming with $MEASUREMENT=0 and no Inkscape metadata.')

    if _token_hits(raw_text, KICAD_TOKENS) or any(layer in ('F.Cu', 'B.Cu') for layer in layers):
        score -= 30
        evidence.append('Contradiction: KiCad evidence found.')

    if _token_hits(raw_text, INKSCAPE_TOKENS):
        score -= 30
        evidence.append('Contradiction: Inkscape evidence found.')

    return score, evidence, warnings


def _score_kicad(filename, raw_text, info):
    score = 0
    evidence = []
    warnings = []

    filename_l = os.path.basename(filename or '').lower()
    layers = set(info['layers'])
    kicad_layers = {'F.Cu', 'B.Cu', 'Edge.Cuts', 'F.Mask', 'B.Mask', 'F.SilkS', 'B.SilkS'}

    token_hits = _token_hits(raw_text, KICAD_TOKENS)
    if token_hits:
        score += 40
        evidence.append('KiCad textual tokens found: %s.' % ', '.join(token_hits[:6]))

    if layers & kicad_layers:
        score += 25
        evidence.append('KiCad PCB layers found: %s.' % ', '.join(sorted(layers & kicad_layers)))

    counts = info['entity_counts']
    if counts.get('LINE', 0) > 100 and counts.get('SPLINE', 0) == 0:
        score += 25
        evidence.append('KiCad-like dense LINE pattern without SPLINE.')
    if counts.get('CIRCLE', 0) > 0:
        score += 5
        evidence.append('CIRCLE entities found, common in KiCad copper/drill-map DXF.')

    if info['insunits'] == 4 and info['measurement'] == 1:
        score += 10
        evidence.append('$INSUNITS=4 and $MEASUREMENT=1.')

    if 'drl_map' in filename_l or 'pth-drl_map' in filename_l or 'npth-drl_map' in filename_l:
        score += 35
        evidence.append('Filename indicates KiCad drill map DXF.')
    if layers == {'BLACK'} and counts.get('LINE', 0) > 100:
        score += 15
        evidence.append('BLACK layer with dense LINE entities, consistent with KiCad drill map.')

    if _token_hits(raw_text, INKSCAPE_TOKENS):
        score -= 30
        evidence.append('Contradiction: Inkscape evidence found.')

    return score, evidence, warnings


def _score_inkscape(filename, raw_text, info):
    score = 0
    evidence = []
    warnings = []

    token_hits = _token_hits(raw_text, INKSCAPE_TOKENS)
    if token_hits:
        score += 40
        evidence.append('Inkscape textual tokens found: %s.' % ', '.join(token_hits[:5]))

    counts = info['entity_counts']
    if info['acadver'] == 'AC1009' and counts.get('POLYLINE', 0) > 0 and not counts.get('HATCH', 0) and not counts.get('SPLINE', 0):
        score += 35
        evidence.append('R12 POLYLINE-dominant export without HATCH/SPLINE.')
    elif info['acadver'] == 'AC1015' and counts.get('LWPOLYLINE', 0) and counts.get('SPLINE', 0) and not counts.get('HATCH', 0):
        score += 35
        evidence.append('R2000/R14-style LWPOLYLINE + SPLINE export without HATCH.')

    if 'Capa_1' in info['layers'] or 'Capa 1' in info['layers']:
        score += 10
        evidence.append('Inkscape-compatible layer naming found.')

    if info['acadver'] in ('AC1009', 'AC1015'):
        score += 10
        evidence.append('DXF version compatible with Inkscape export profile: %s/%s.' %
                        (info['acadver'], info['version_name']))

    if _token_hits(raw_text, KICAD_TOKENS):
        score -= 30
        evidence.append('Contradiction: KiCad evidence found.')

    if info['entity_counts'].get('HATCH', 0) and info['entity_counts'].get('SPLINE', 0):
        score -= 20
        evidence.append('Contradiction: Illustrator-like HATCH + SPLINE pattern found.')

    return score, evidence, warnings


def _score_proteus(filename, raw_text, info):
    score = 0
    evidence = []
    warnings = []

    token_hits = _token_hits(raw_text, PROTEUS_TOKENS)
    if token_hits:
        score += 20
        evidence.append('Proteus/ARES-related block or text tokens found: %s.' % ', '.join(token_hits[:5]))

    counts = info['entity_counts']
    if info['acadver'] == 'AC1009':
        score += 10
        evidence.append('R12 DXF detected.')

    if info['insunits'] == 5:
        score += 10
        evidence.append('$INSUNITS=5 detected.')

    if counts.get('POLYLINE', 0) == 1 and info['modelspace_count'] == 1 and info['single_rectangular_polyline']:
        score += 25
        evidence.append('Single closed rectangular POLYLINE in modelspace.')

    if info['layers'] == {'1'}:
        score += 10
        evidence.append('Only layer "1" found in modelspace.')

    epad = _block_defined_not_inserted(info, 'EPAD')
    rpad = _block_defined_not_inserted(info, 'RPAD')
    if epad and rpad:
        score += 20
        evidence.append('EPAD/RPAD blocks are defined but not inserted.')

    if info['modelspace_count'] > 0 and not counts.get('INSERT', 0) and not counts.get('HATCH', 0) and not counts.get('SPLINE', 0):
        score += 10
        evidence.append('No INSERT/HATCH/SPLINE in modelspace.')

    if counts.get('LINE', 0) > 1000 or any(layer in ('F.Cu', 'B.Cu') for layer in info['layers']):
        score -= 30
        evidence.append('Contradiction: dense PCB copper geometry or KiCad layer evidence found.')

    return score, evidence, warnings


def _recommendations(source, export_profile):
    if source == 'illustrator':
        return [
            'Adobe Illustrator DXF profile detected. DXF geometry is supported.',
            'Circular DXF drill recognition may be used when drills were intentionally drawn as circles.'
        ], 'allow'

    if source == 'kicad' and export_profile == 'KiCad Drill Map DXF':
        return [
            'Use the real .drl Excellon file instead of the drill map DXF for drilling.'
        ], 'hide'

    if source == 'kicad':
        return [
            'Use dedicated KiCad Excellon .drl files for drilling when available.',
            'DXF drill recognition should be used only as a fallback.'
        ], 'fallback'

    if source == 'inkscape':
        return [
            'Use SVG for filled copper, pads or PCB artwork.',
            'DXF is suitable mainly for linear geometry from Inkscape.'
        ], 'hide'

    if source == 'proteus':
        return [
            'Use Gerber + Excellon for manufacturing, or SVG/PDF export for visual artwork.',
            'This DXF profile is suitable only for outline/mechanical reference.'
        ], 'hide'

    return [
        'No high-confidence DXF source was detected. Verify geometry, dimensions and layers before machining.'
    ], 'manual'


def _supports_for_source(source, export_profile, drill_policy):
    supports = {
        'geometry': True,
        'gerber': True,
        'drill_recognition': drill_policy in ('allow', 'fallback'),
        'preferred_import': 'dxf'
    }

    if source == 'illustrator':
        supports['preferred_import'] = 'dxf'
    elif source == 'kicad':
        supports['preferred_import'] = 'dxf'
        if export_profile == 'KiCad Drill Map DXF':
            supports['drill_recognition'] = False
            supports['preferred_import'] = 'excellon'
    elif source == 'inkscape':
        supports['drill_recognition'] = False
        supports['preferred_import'] = 'svg'
    elif source == 'proteus':
        supports['drill_recognition'] = False
        supports['preferred_import'] = 'gerber_excellon'
    elif source == 'unknown':
        supports['drill_recognition'] = False
        supports['preferred_import'] = 'manual_review'

    return supports


def _profile_for_source(source, info, filename):
    filename_l = os.path.basename(filename or '').lower()

    if source == 'illustrator':
        return 'Adobe Illustrator DXF'

    if source == 'kicad':
        if 'drl_map' in filename_l or info['layers'] == {'BLACK'}:
            return 'KiCad Drill Map DXF'
        return 'KiCad Copper DXF'

    if source == 'inkscape':
        if info['acadver'] == 'AC1009':
            return 'Inkscape DXF R12'
        return 'Inkscape DXF R2000/R14-style'

    if source == 'proteus':
        return 'Possible Proteus/ARES outline-only DXF'

    return 'Unknown DXF source'


def detect_dxf_source(filename=None, doc=None, raw_text=None):
    """
    Detect probable DXF source/export profile without modifying the file or geometry.

    :param filename: Optional DXF filename.
    :param doc: Optional already loaded ezdxf document.
    :param raw_text: Optional raw DXF text.
    :return: Detection dictionary.
    """
    warnings = []
    if raw_text is None:
        raw_text = _read_raw_text(filename)

    doc = _load_doc(filename, doc, warnings)
    if doc is None and not raw_text:
        result = _default_result(warnings)
        if not result['warnings']:
            result['warnings'].append('No DXF document or raw text was available for detection.')
        return result

    info = _collect_doc_info(doc)

    scorers = {
        'illustrator': _score_illustrator(filename, raw_text, info),
        'kicad': _score_kicad(filename, raw_text, info),
        'inkscape': _score_inkscape(filename, raw_text, info),
        'proteus': _score_proteus(filename, raw_text, info)
    }

    source = 'unknown'
    score = 0
    evidence = []
    source_warnings = []

    for candidate, candidate_result in scorers.items():
        candidate_score, candidate_evidence, candidate_warnings = candidate_result
        if candidate_score > score:
            source = candidate
            score = candidate_score
            evidence = candidate_evidence
            source_warnings = candidate_warnings

    if score < 25:
        source = 'unknown'
        evidence = []

    export_profile = _profile_for_source(source, info, filename)
    recommendations, drill_policy = _recommendations(source, export_profile)

    # KiCad drill maps should not expose normal drill recognition even when the generic KiCad score is high.
    if source == 'kicad' and export_profile == 'KiCad Drill Map DXF':
        drill_policy = 'hide'

    if source == 'proteus' and export_profile == 'Possible Proteus/ARES outline-only DXF':
        score = max(score, 70)

    return {
        'source': source,
        'export_profile': export_profile,
        'confidence': _confidence(score),
        'score': int(score),
        'acad_version': info.get('acadver'),
        'dxf_version': info.get('version_name'),
        'supports': _supports_for_source(source, export_profile, drill_policy),
        'evidence': evidence,
        'warnings': warnings + source_warnings,
        'recommendations': recommendations,
        'drill_recognition_policy': drill_policy
    }
