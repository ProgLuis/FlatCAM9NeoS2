# ##########################################################
# FlatCAM: 2D Post-processing for Manufacturing            #
# File Author: Marius Adrian Stanciu (c)                   #
# Date: 3/10/2019                                          #
# MIT Licence                                              #
# ##########################################################

from shapely.geometry import LineString, Point, Polygon
from shapely.affinity import rotate
from ezdxf.math import bulge_to_arc
from ezdxf.math.vector import Vector as ezdxf_vector

from appParsers.ParseFont import *
from appParsers.ParseDXF_Spline import *
from appParsers.DXFSourceDetector import detect_dxf_source
import math
import re
import logging

log = logging.getLogger('base2')


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

DXF_DIAGNOSTIC_TYPES = [
    'LINE', 'ARC', 'CIRCLE', 'POINT', 'ELLIPSE', 'LWPOLYLINE', 'POLYLINE', 'SPLINE', 'SOLID', 'TRACE',
    'INSERT', 'HATCH', 'IMAGE', 'TEXT', 'MTEXT', '3DFACE', 'DIMENSION', 'LEADER', 'MLINE', 'XLINE', 'RAY', 'MESH',
    'REGION', 'BODY', '3DSOLID'
]

DXF_SUPPORTED_TYPES = {
    'LINE', 'ARC', 'CIRCLE', 'POINT', 'ELLIPSE', 'LWPOLYLINE', 'POLYLINE', 'SOLID', 'TRACE'
}
DXF_PARTIAL_TYPES = {'SPLINE', 'INSERT', 'HATCH'}
DXF_IGNORED_TYPES = {
    'IMAGE', 'TEXT', 'MTEXT', '3DFACE', 'DIMENSION', 'LEADER', 'MLINE', 'XLINE', 'RAY', 'MESH', 'REGION',
    'BODY', '3DSOLID'
}
DXF_ALWAYS_3D_TYPES = {'3DFACE', 'MESH', 'REGION', 'BODY', '3DSOLID'}
DXF_SUPPORTED_VERSION_TEXT = 'R12, R14, R2000, R2004, R2007, R2010, R2013, R2018'
DXF_INSUNITS_NAMES = {
    0: 'Unitless', 1: 'Inches', 2: 'Feet', 3: 'Miles', 4: 'Millimeters', 5: 'Centimeters', 6: 'Meters',
    7: 'Kilometers', 8: 'Microinches', 9: 'Mils', 10: 'Yards', 11: 'Angstroms', 12: 'Nanometers',
    13: 'Microns', 14: 'Decimeters', 15: 'Decameters', 16: 'Hectometers', 17: 'Gigameters',
    18: 'Astronomical units', 19: 'Light years', 20: 'Parsecs', 21: 'US survey feet',
    22: 'US survey inches', 23: 'US survey yards', 24: 'US survey miles'
}
DXF_INSUNITS_TO_MM = {
    1: 25.4, 2: 304.8, 3: 1609344.0, 4: 1.0, 5: 10.0, 6: 1000.0, 7: 1000000.0,
    8: 0.0000254, 9: 0.0254, 10: 914.4, 11: 0.0000001, 12: 0.000001, 13: 0.001,
    14: 100.0, 15: 10000.0, 16: 100000.0, 17: 1000000000000.0, 18: 149597870700000.0,
    19: 9460730472580800000.0, 20: 30856775814913673000.0, 21: 304.8006096012192,
    22: 25.4000508001016, 23: 914.4018288036576, 24: 1609347.2186944373
}


def _dxf_has_nonzero_z(value, tolerance=1e-12):
    try:
        return abs(float(value[2])) > tolerance
    except (IndexError, TypeError, ValueError):
        return False


def _dxf_has_non_default_extrusion(entity, tolerance=1e-12):
    try:
        extrusion = entity.dxf.extrusion
        return abs(float(extrusion[0])) > tolerance or abs(float(extrusion[1])) > tolerance or \
            abs(float(extrusion[2]) - 1.0) > tolerance
    except (AttributeError, IndexError, TypeError, ValueError):
        return False


def _dxf_entity_has_bulge(entity, tolerance=1e-12):
    kind = entity.dxftype()
    try:
        if kind == 'LWPOLYLINE':
            return any(len(point) > 4 and abs(float(point[4])) > tolerance for point in entity)
        if kind == 'POLYLINE':
            return any(abs(float(vertex.dxf.bulge)) > tolerance for vertex in entity)
    except (AttributeError, IndexError, TypeError, ValueError):
        return False
    return False


def _dxf_entity_is_3d(entity):
    kind = entity.dxftype()
    if kind in DXF_ALWAYS_3D_TYPES:
        return True

    if _dxf_has_non_default_extrusion(entity):
        return True

    try:
        if kind == 'POLYLINE' and entity.is_3d_polyline:
            return True
        if kind == 'LWPOLYLINE' and abs(float(entity.dxf.elevation)) > 1e-12:
            return True
    except (AttributeError, TypeError, ValueError):
        pass

    vertices = []
    try:
        if kind == 'LINE':
            vertices = [entity.dxf.start, entity.dxf.end]
        elif kind == 'POINT':
            vertices = [entity.dxf.location]
        elif kind in ['CIRCLE', 'ARC', 'ELLIPSE']:
            vertices = [entity.dxf.center]
            if kind == 'ELLIPSE':
                vertices.append(entity.dxf.major_axis)
        elif kind in ['SOLID', 'TRACE', '3DFACE']:
            vertices = list(entity)
        elif kind == 'POLYLINE':
            vertices = [vertex.dxf.location for vertex in entity]
        elif kind == 'SPLINE':
            vertices = list(entity.control_points) + list(entity.fit_points)
        elif kind == 'INSERT':
            vertices = [entity.dxf.insert]
    except (AttributeError, TypeError):
        pass

    return any(_dxf_has_nonzero_z(vertex) for vertex in vertices)


def _dxf_entity_has_nonzero_z_coordinates(entity):
    kind = entity.dxftype()
    vertices = []
    try:
        if kind == 'LINE':
            vertices = [entity.dxf.start, entity.dxf.end]
        elif kind == 'POINT':
            vertices = [entity.dxf.location]
        elif kind in ['CIRCLE', 'ARC', 'ELLIPSE']:
            vertices = [entity.dxf.center]
        elif kind in ['SOLID', 'TRACE', '3DFACE']:
            vertices = list(entity)
        elif kind == 'POLYLINE':
            vertices = [vertex.dxf.location for vertex in entity]
        elif kind == 'LWPOLYLINE':
            return abs(float(entity.dxf.elevation)) > 1e-12
        elif kind == 'SPLINE':
            vertices = list(entity.control_points) + list(entity.fit_points)
        elif kind == 'INSERT':
            vertices = [entity.dxf.insert]
    except (AttributeError, TypeError, ValueError):
        return False
    return any(_dxf_has_nonzero_z(vertex) for vertex in vertices)


def _dxf_format_counts(counts, ordered_types=None):
    ordered_types = ordered_types if ordered_types is not None else sorted(counts)
    values = ['%s=%d' % (kind, counts[kind]) for kind in ordered_types if counts.get(kind, 0)]
    return ', '.join(values) if values else 'none'


def _dxf_source_header_values(dxf_object):
    """Read declarations from the source because ezdxf supplies defaults for missing HEADER variables."""

    cached = getattr(dxf_object, '_neo_s2_header_values_cache', None)
    if isinstance(cached, dict):
        return cached

    filename = getattr(dxf_object, 'filename', None)
    if not filename:
        return None
    try:
        with open(filename, 'r', encoding='latin-1', errors='ignore') as source_file:
            source_lines = []
            source_size = 0
            for line in source_file:
                source_lines.append(line)
                source_size += len(line)
                if line.strip().upper() == 'ENDSEC' or source_size >= 2097152:
                    break
            source = ''.join(source_lines)
    except (IOError, OSError, TypeError, ValueError):
        return None

    acadver_match = re.search(r'\$ACADVER\s*\r?\n\s*1\s*\r?\n\s*([^\r\n]+)', source, re.IGNORECASE)
    insunits_match = re.search(r'\$INSUNITS\s*\r?\n\s*70\s*\r?\n\s*(-?\d+)', source, re.IGNORECASE)
    result = {
        'acadver_declared': acadver_match is not None,
        'acadver': acadver_match.group(1).strip().upper() if acadver_match else None,
        'insunits_declared': insunits_match is not None,
        'insunits': int(insunits_match.group(1)) if insunits_match else None
    }
    try:
        dxf_object._neo_s2_header_values_cache = result
    except (AttributeError, TypeError):
        pass
    return result


def dxf_physical_scale(dxf_object, target_units='MM'):
    """Resolve the uniform factor from declared DXF drawing units to FlatCAM MM/IN units."""

    source_header = _dxf_source_header_values(dxf_object)
    if source_header is not None:
        declared = source_header['insunits_declared']
        unit_code = source_header['insunits']
    else:
        try:
            declared = '$INSUNITS' in dxf_object.header
            unit_code = int(dxf_object.header.get('$INSUNITS', 0)) if declared else None
        except (AttributeError, TypeError, ValueError):
            declared = False
            unit_code = None

    target = str(target_units or 'MM').strip().upper()
    target_to_mm = {'MM': 1.0, 'IN': 25.4}.get(target)
    source_name = DXF_INSUNITS_NAMES.get(unit_code, 'Unknown units') if declared else 'Not declared'

    if target_to_mm is None:
        return {
            'factor': 1.0,
            'status': 'unsupported_target',
            'source_code': unit_code,
            'source_units': source_name,
            'target_units': target,
            'message': "DXF physical scale was not applied: unsupported FlatCAM target units '%s'." % target
        }
    if not declared:
        return {
            'factor': 1.0,
            'status': 'missing',
            'source_code': None,
            'source_units': source_name,
            'target_units': target,
            'message': 'DXF physical scale was not applied because $INSUNITS is not declared; verify dimensions.'
        }
    if unit_code == 0:
        return {
            'factor': 1.0,
            'status': 'unitless',
            'source_code': unit_code,
            'source_units': source_name,
            'target_units': target,
            'message': 'DXF uses unitless coordinates ($INSUNITS=0); factor 1.0 was used. Verify dimensions.'
        }

    source_to_mm = DXF_INSUNITS_TO_MM.get(unit_code)
    if source_to_mm is None:
        return {
            'factor': 1.0,
            'status': 'unsupported_source',
            'source_code': unit_code,
            'source_units': source_name,
            'target_units': target,
            'message': 'DXF physical scale was not applied: unsupported $INSUNITS=%s. Verify dimensions.' % unit_code
        }

    factor = source_to_mm / target_to_mm
    target_name = 'Millimeters' if target == 'MM' else 'Inches'
    return {
        'factor': factor,
        'status': 'reliable',
        'source_code': unit_code,
        'source_units': source_name,
        'target_units': target,
        'message': 'DXF physical scale applied: %s -> %s; factor=%s.' %
                   (source_name, target_name, format(factor, '.12g'))
    }


def _dxf_flatten_geometry(items):
    if items is None:
        return
    if isinstance(items, (list, tuple)):
        for item in items:
            for flat_item in _dxf_flatten_geometry(item):
                yield flat_item
    else:
        yield items


def dxf_geometry_extents(dxf_object):
    """Return bounds for the converted Shapely geometry without assuming physical units."""

    try:
        geometry = list(_dxf_flatten_geometry(getdxfgeo(dxf_object)))
    except Exception as e:
        log.warning("DXF geometry extents could not be calculated: %s" % str(e))
        return None

    bounds = []
    for geo in geometry:
        if geo is None:
            continue
        try:
            if geo.is_empty:
                continue
            geo_bounds = geo.bounds
            if len(geo_bounds) != 4:
                continue
            if all(math.isfinite(float(value)) for value in geo_bounds):
                bounds.append(tuple(float(value) for value in geo_bounds))
        except (AttributeError, TypeError, ValueError):
            continue

    if not bounds:
        return None

    minx = min(item[0] for item in bounds)
    miny = min(item[1] for item in bounds)
    maxx = max(item[2] for item in bounds)
    maxy = max(item[3] for item in bounds)
    return {
        'bounds': (minx, miny, maxx, maxy),
        'width': maxx - minx,
        'height': maxy - miny
    }


def detect_possible_proteus_outline_dxf(dxf_object, version=None, insunits=None):
    """Return a high-confidence, non-blocking hint for Proteus/ARES outline-only DXF exports."""

    message = (
        'Possible Proteus/ARES outline-only DXF detected. This file contains only a board outline although '
        'EPAD/RPAD blocks are defined but not inserted. Some Proteus/ARES versions may export incomplete copper '
        'geometry as DXF. If the PCB artwork appears incomplete, use Gerber X2/RS274X for manufacturing or '
        'SVG/PDF/Bitmap for visual artwork export.'
    )
    result = {
        'detected': False,
        'confidence': 'low',
        'source_hint': None,
        'message': message
    }

    source_header = _dxf_source_header_values(dxf_object)
    if version is None:
        version = str(getattr(dxf_object, 'dxfversion', '') or 'unknown')
        if source_header is not None and source_header.get('acadver'):
            version = source_header['acadver']
    if insunits is None:
        if source_header is not None:
            insunits = source_header.get('insunits') if source_header.get('insunits_declared') else None
        else:
            try:
                insunits = int(dxf_object.header.get('$INSUNITS', 0)) \
                    if '$INSUNITS' in dxf_object.header else None
            except (AttributeError, TypeError, ValueError):
                insunits = None

    if version != 'AC1009' or insunits != 5:
        return result

    try:
        entities = list(dxf_object.modelspace())
    except (AttributeError, TypeError):
        return result
    if len(entities) != 1 or entities[0].dxftype() != 'POLYLINE':
        return result

    polyline = entities[0]
    try:
        if str(polyline.dxf.layer) != '1':
            return result
        points = [(float(vertex.dxf.location[0]), float(vertex.dxf.location[1])) for vertex in polyline]
    except (AttributeError, IndexError, TypeError, ValueError):
        return result

    if len(points) != 5 or not (
            math.isclose(points[0][0], points[-1][0], abs_tol=1e-9) and
            math.isclose(points[0][1], points[-1][1], abs_tol=1e-9)):
        return result
    corners = points[:-1]
    rounded_corners = {(round(point[0], 9), round(point[1], 9)) for point in corners}
    x_values = {point[0] for point in rounded_corners}
    y_values = {point[1] for point in rounded_corners}
    expected_corners = {(x_value, y_value) for x_value in x_values for y_value in y_values}
    if len(rounded_corners) != 4 or len(x_values) != 2 or len(y_values) != 2 or \
            rounded_corners != expected_corners:
        return result

    closed_corners = corners + [corners[0]]
    for start, end in zip(closed_corners, closed_corners[1:]):
        same_x = math.isclose(start[0], end[0], abs_tol=1e-9)
        same_y = math.isclose(start[1], end[1], abs_tol=1e-9)
        if same_x == same_y:
            return result

    try:
        block_names = {str(block.name).upper() for block in dxf_object.blocks if not str(block.name).startswith('*')}
    except (AttributeError, TypeError):
        return result
    if not {'EPAD', 'RPAD'}.issubset(block_names):
        return result

    result.update({
        'detected': True,
        'confidence': 'high',
        'source_hint': 'possible Proteus/ARES outline-only DXF'
    })
    return result


def dxf_import_report(dxf_object):
    """Return a diagnostic report without modifying DXF entities or geometry."""

    filename = getattr(dxf_object, 'filename', None)
    source_header = _dxf_source_header_values(dxf_object)
    version = str(getattr(dxf_object, 'dxfversion', '') or 'unknown')
    if source_header is not None:
        version_declared = source_header['acadver_declared']
        if source_header['acadver']:
            version = source_header['acadver']
        insunits_declared = source_header['insunits_declared']
        insunits = source_header['insunits']
    else:
        try:
            version_declared = '$ACADVER' in dxf_object.header
        except (AttributeError, TypeError):
            version_declared = False
        try:
            insunits_declared = '$INSUNITS' in dxf_object.header
            insunits = int(dxf_object.header.get('$INSUNITS', 0)) if insunits_declared else None
        except (AttributeError, TypeError, ValueError):
            insunits_declared = False
            insunits = None
    release = DXF_VERSION_NAMES.get(version, 'Unknown')
    insunits_name = DXF_INSUNITS_NAMES.get(insunits, 'Unknown units') if insunits_declared else None
    counts = {kind: 0 for kind in DXF_DIAGNOSTIC_TYPES}
    other_counts = {}
    bulge_counts = {'LWPOLYLINE': 0, 'POLYLINE': 0}
    three_d_counts = {}
    layer_counts = {}
    entity_layers = {}
    bulge_layers = {}
    three_d_layers = {}
    nonzero_z_layers = {}
    extrusion_layers = {}
    insert_blocks = set()
    missing_insert_blocks = set()
    missing_insert_layers = {}
    non_uniform_insert_count = 0
    non_uniform_insert_layers = {}
    text_layers = set()
    image_references = []
    source_detection = detect_dxf_source(filename=filename, doc=dxf_object)

    for entity in dxf_object.modelspace():
        kind = entity.dxftype()
        try:
            layer = str(entity.dxf.layer)
        except AttributeError:
            layer = 'unknown'

        if kind in counts:
            counts[kind] += 1
        else:
            other_counts[kind] = other_counts.get(kind, 0) + 1

        layer_data = layer_counts.setdefault(layer, {})
        layer_data[kind] = layer_data.get(kind, 0) + 1
        kind_layers = entity_layers.setdefault(kind, {})
        kind_layers[layer] = kind_layers.get(layer, 0) + 1

        if kind in bulge_counts and _dxf_entity_has_bulge(entity):
            bulge_counts[kind] += 1
            bulge_layers[layer] = bulge_layers.get(layer, 0) + 1
        if _dxf_entity_is_3d(entity):
            three_d_counts[kind] = three_d_counts.get(kind, 0) + 1
            three_d_layers[layer] = three_d_layers.get(layer, 0) + 1
        if _dxf_entity_has_nonzero_z_coordinates(entity):
            nonzero_z_layers[layer] = nonzero_z_layers.get(layer, 0) + 1
        if _dxf_has_non_default_extrusion(entity):
            extrusion_layers[layer] = extrusion_layers.get(layer, 0) + 1
        if kind == 'INSERT':
            try:
                block_name = str(entity.dxf.name)
                insert_blocks.add(block_name)
                if block_name not in dxf_object.blocks:
                    missing_insert_blocks.add(block_name)
                    missing_insert_layers[layer] = missing_insert_layers.get(layer, 0) + 1

                xscale = float(entity.dxf.xscale)
                yscale = float(entity.dxf.yscale)
                if abs(abs(xscale) - abs(yscale)) > 1e-12:
                    non_uniform_insert_count += 1
                    non_uniform_insert_layers[layer] = non_uniform_insert_layers.get(layer, 0) + 1

                if block_name in dxf_object.blocks:
                    block_image_count = 0
                    for block_entity in dxf_object.blocks[block_name]:
                        if block_entity.dxftype() != 'IMAGE':
                            continue
                        block_image_count += 1
                        try:
                            image_layer = str(block_entity.dxf.layer)
                        except AttributeError:
                            image_layer = 'unknown'
                        image_references.append({
                            'kind': 'block',
                            'block': block_name,
                            'insert_layer': layer,
                            'image_layer': image_layer
                        })
                    if block_image_count:
                        layer_data['IMAGE'] = layer_data.get('IMAGE', 0) + block_image_count
                        image_layers = entity_layers.setdefault('IMAGE', {})
                        image_layers[layer] = image_layers.get(layer, 0) + block_image_count
                        counts['IMAGE'] += block_image_count
            except (AttributeError, TypeError, ValueError):
                pass
        elif kind == 'IMAGE':
            image_references.append({
                'kind': 'modelspace',
                'block': None,
                'insert_layer': None,
                'image_layer': layer
            })
        elif kind in ['TEXT', 'MTEXT']:
            text_layers.add(layer)

    supported = {kind: counts[kind] for kind in DXF_DIAGNOSTIC_TYPES if kind in DXF_SUPPORTED_TYPES and counts[kind]}
    partial = {kind: counts[kind] for kind in DXF_DIAGNOSTIC_TYPES if kind in DXF_PARTIAL_TYPES and counts[kind]}
    ignored = {kind: counts[kind] for kind in DXF_DIAGNOSTIC_TYPES if kind in DXF_IGNORED_TYPES and counts[kind]}

    for kind, bulge_count in bulge_counts.items():
        if bulge_count:
            supported.pop(kind, None)
            partial[kind] = counts[kind]
    ignored.update(other_counts)

    total_count = sum(counts.values()) + sum(other_counts.values())
    convertible_count = sum(supported.values()) + sum(partial.values())
    ignored_count = sum(ignored.values())

    image_only_insert_profile = bool(image_references) and not supported and not \
        {kind: count for kind, count in partial.items() if kind != 'INSERT'}

    if total_count == 0 or convertible_count == 0 or image_only_insert_profile:
        compatibility_status = 'Empty/unsupported'
    elif ignored_count or three_d_counts or release == 'Unknown':
        compatibility_status = 'Partially supported'
    elif partial or version in ['AC1027', 'AC1032']:
        compatibility_status = 'Compatible with warnings'
    else:
        compatibility_status = 'Compatible'

    if version == 'AC1009':
        compatibility_note = 'Best compatibility profile for CAM/CNC/PCB workflows.'
    elif version in ['AC1014', 'AC1015', 'AC1018', 'AC1021', 'AC1024']:
        compatibility_note = 'Supported format; verify dimensions and advanced entities.'
    elif version in ['AC1027', 'AC1032']:
        compatibility_note = 'Advanced DXF version; review warnings and imported geometry carefully.'
    else:
        compatibility_note = 'Unrecognized version; conversion may be incomplete.'

    simple_2d_types = {'LINE', 'ARC', 'CIRCLE', 'POLYLINE', 'LWPOLYLINE', 'VERTEX', 'SEQEND'}
    detected_types = {kind for kind, count in counts.items() if count}
    detected_types.update(kind for kind, count in other_counts.items() if count)
    simple_2d_profile = bool(detected_types) and detected_types.issubset(simple_2d_types) and not three_d_counts
    complex_profile = bool(
        detected_types.intersection({'SPLINE', 'HATCH', 'TEXT', 'MTEXT', 'INSERT'}) or
        ignored_count or three_d_counts
    )

    orientation_messages = []
    if not version_declared:
        orientation_messages.append(
            'DXF version not declared. Neo S2 will analyze entities and layers; simple 2D entities are usually '
            'compatible.'
        )
    elif version == 'AC1009':
        orientation_messages.append(
            'DXF R12 detected. This is the recommended profile for maximum CAM/CNC/PCB compatibility.'
        )
    elif version == 'AC1014':
        orientation_messages.append(
            'DXF R14 detected. Usually compatible, but verify splines, text and advanced entities.'
        )
    elif version == 'AC1015':
        orientation_messages.append(
            'DXF R2000 detected. Supported format; verify dimensions, bulges, hatches and blocks.'
        )
    elif version == 'AC1024':
        orientation_messages.append(
            'DXF R2010 detected. Supported format; for maximum compatibility consider exporting as DXF R12 ASCII.'
        )
    elif version in ['AC1027', 'AC1032']:
        orientation_messages.append(
            'Modern DXF detected. Supported with warnings; for CAM/CNC/PCB workflows, DXF R12 ASCII is recommended.'
        )
    elif version in ['AC1018', 'AC1021']:
        orientation_messages.append(
            'DXF %s detected. Supported format; verify dimensions and advanced entities.' % release
        )

    if simple_2d_profile:
        orientation_messages.append(
            'Simple 2D CAM/PCB-style DXF detected. This file is likely suitable for FlatCAM processing.'
        )
    if complex_profile:
        orientation_messages.append(
            'Complex DXF entities detected. Review the listed layers before generating toolpaths.'
        )
    if counts['TEXT'] or counts['MTEXT']:
        orientation_messages.append(
            'TEXT/MTEXT detected. For CAM use, convert text to outlines/paths before exporting when real engraving '
            'is required.'
        )
    if counts['SPLINE']:
        orientation_messages.append(
            'SPLINE detected. For best CAM compatibility, convert curves to polylines before exporting.'
        )
    if counts['HATCH']:
        orientation_messages.append(
            'HATCH detected. Neo S2 will try to convert supported boundaries, but verify filled areas before machining.'
        )
    if counts['IMAGE']:
        orientation_messages.append(
            'IMAGE/raster detected. Raster images linked or embedded in DXF are not converted to CAM geometry.'
        )
    if counts['INSERT']:
        orientation_messages.append(
            'BLOCK/INSERT detected. Neo S2 will expand supported blocks, but verify repeated geometry and scaling.'
        )

    if insunits_declared:
        units_message = 'DXF units: $INSUNITS=%d / %s.' % (insunits, insunits_name)
        geometry_extents = None
    else:
        units_message = 'DXF units not declared. Verify scale after import.'
        geometry_extents = dxf_geometry_extents(dxf_object)

    proteus_outline_detection = detect_possible_proteus_outline_dxf(
        dxf_object,
        version=version,
        insunits=insunits
    )
    if proteus_outline_detection['detected']:
        orientation_messages.append(
            'Source hint: %s.' % proteus_outline_detection['source_hint']
        )

    warnings = []
    if proteus_outline_detection['detected']:
        warnings.append(proteus_outline_detection['message'])
    if version in ['AC1027', 'AC1032']:
        warnings.append(
            'Modern DXF version detected: %s / %s. Verify imported dimensions and geometry.' % (version, release)
        )
    elif release == 'Unknown':
        warnings.append('Unrecognized DXF version detected: %s. Verify imported dimensions and geometry.' % version)

    for layer in sorted(layer_counts):
        layer_data = layer_counts[layer]
        if layer_data.get('SPLINE'):
            warnings.append(
                'SPLINE detected in layer %s. It will be approximated; verify geometry: SPLINE=%d.' %
                (layer, layer_data['SPLINE'])
            )
        if layer_data.get('HATCH'):
            warnings.append(
                'HATCH detected in layer %s. Converted when boundaries are supported; spline or open boundaries may '
                'be reduced to lines or ignored: HATCH=%d.' % (layer, layer_data['HATCH'])
            )
        if layer_data.get('IMAGE'):
            warnings.append(
                'IMAGE/raster entity detected in DXF layer %s. Raster images linked or embedded in DXF are not '
                'converted to CAM geometry. Use SVG/PDF vector output when possible, or vectorize the bitmap before '
                'importing: IMAGE=%d.' % (layer, layer_data['IMAGE'])
            )
        text_count = layer_data.get('TEXT', 0) + layer_data.get('MTEXT', 0)
        if text_count:
            warnings.append(
                'TEXT/MTEXT detected in layer %s. Text outlines are not converted: TEXT=%d, MTEXT=%d.' %
                (layer, layer_data.get('TEXT', 0), layer_data.get('MTEXT', 0))
            )
        if layer_data.get('INSERT'):
            warnings.append(
                'INSERT/BLOCK detected in layer %s. Block transformations are materialized; verify arrays and '
                'non-uniform scales: INSERT=%d.' % (layer, layer_data['INSERT'])
            )
        unsupported_layer = {
            kind: count for kind, count in layer_data.items()
            if kind in DXF_IGNORED_TYPES and kind not in ['IMAGE', 'TEXT', 'MTEXT']
        }
        unknown_layer = {
            kind: count for kind, count in layer_data.items()
            if kind not in DXF_DIAGNOSTIC_TYPES
        }
        unsupported_layer.update(unknown_layer)
        if unsupported_layer:
            warnings.append(
                'Unsupported entities detected in layer %s and ignored: %s.' %
                (layer, _dxf_format_counts(unsupported_layer))
            )
        if layer in bulge_layers:
            warnings.append(
                'Bulge detected in layer %s. Bulges are approximated as arc segments: count=%d.' %
                (layer, bulge_layers[layer])
            )
        if layer in three_d_layers:
            warnings.append(
                '3D entities detected in layer %s. FlatCAM uses 2D CAM geometry: count=%d.' %
                (layer, three_d_layers[layer])
            )
        if layer in nonzero_z_layers:
            warnings.append(
                'Non-zero Z coordinates detected in layer %s. Only the XY projection is used: count=%d.' %
                (layer, nonzero_z_layers[layer])
            )
        if layer in extrusion_layers:
            warnings.append(
                'Non-standard extrusion detected in layer %s. Verify the resulting XY projection: count=%d.' %
                (layer, extrusion_layers[layer])
            )

    if missing_insert_blocks:
        warnings.append(
            'INSERT references missing BLOCK definitions and those inserts will be ignored: %s.' %
            ', '.join(sorted(missing_insert_blocks))
        )
    if non_uniform_insert_count:
        warnings.append(
            'INSERT entities with non-uniform X/Y scale detected. Curves are transformed through ezdxf but should be '
            'verified: INSERT=%d.' % non_uniform_insert_count
        )
    for image_ref in image_references:
        if image_ref['kind'] == 'block':
            warnings.append(
                'IMAGE/raster entity detected inside BLOCK %s from INSERT layer %s. Raster images linked or embedded '
                'in DXF are not converted to CAM geometry. IMAGE layer: %s.' %
                (image_ref['block'], image_ref['insert_layer'], image_ref['image_layer'])
            )

    return {
        'version': version,
        'release': release,
        'filename': filename,
        'version_declared': version_declared,
        'insunits_declared': insunits_declared,
        'insunits': insunits,
        'insunits_name': insunits_name,
        'units_message': units_message,
        'geometry_extents': geometry_extents,
        'supported_versions': DXF_SUPPORTED_VERSION_TEXT,
        'recommended_version': 'DXF R12 ASCII',
        'compatibility_status': compatibility_status,
        'compatibility_note': compatibility_note,
        'simple_2d_profile': simple_2d_profile,
        'complex_profile': complex_profile,
        'orientation_messages': orientation_messages,
        'source_detection': source_detection,
        'source_hint': proteus_outline_detection['source_hint'],
        'proteus_outline_detection': proteus_outline_detection,
        'counts': counts,
        'other_counts': other_counts,
        'supported': supported,
        'partial': partial,
        'ignored': ignored,
        'total_count': total_count,
        'convertible_count': convertible_count,
        'layer_counts': layer_counts,
        'entity_layers': entity_layers,
        'image_references': image_references,
        'problematic': {
            'bulge': {kind: count for kind, count in bulge_counts.items() if count},
            'three_d': three_d_counts,
            'modern_version': version in ['AC1027', 'AC1032']
        },
        'bulge_counts': bulge_counts,
        'bulge_layers': bulge_layers,
        'three_d_counts': three_d_counts,
        'three_d_layers': three_d_layers,
        'nonzero_z_layers': nonzero_z_layers,
        'extrusion_layers': extrusion_layers,
        'insert_blocks': sorted(insert_blocks),
        'missing_insert_blocks': sorted(missing_insert_blocks),
        'missing_insert_layers': missing_insert_layers,
        'non_uniform_insert_count': non_uniform_insert_count,
        'non_uniform_insert_layers': non_uniform_insert_layers,
        'text_layers': sorted(text_layers),
        'warnings': warnings
    }


def dxf_import_report_summary(report):
    """Format the compact Shell summary for a report returned by dxf_import_report()."""

    return (
        'DXF Import Report: Version: %s / %s; Status: %s; Supported: %s; Partial: %s; Ignored: %s.' % (
            report['version'],
            report['release'],
            report['compatibility_status'],
            _dxf_format_counts(report['supported'], DXF_DIAGNOSTIC_TYPES),
            _dxf_format_counts(report['partial'], DXF_DIAGNOSTIC_TYPES),
            _dxf_format_counts(report['ignored'], DXF_DIAGNOSTIC_TYPES + sorted(report['other_counts']))
        )
    )


def dxf_raster_summary_messages(report):
    """Return a compact inventory block for DXF IMAGE/raster entities."""

    image_refs = report.get('image_references', [])
    image_count = report['counts'].get('IMAGE', 0)
    if not image_refs and not image_count:
        return []

    filename = report.get('filename') or 'unknown'
    layers = sorted(set(ref.get('image_layer') or 'unknown' for ref in image_refs))
    blocks = sorted(set(ref.get('block') for ref in image_refs if ref.get('block')))
    insertions = sorted(set(ref.get('insert_layer') for ref in image_refs if ref.get('insert_layer')))

    if not layers and report.get('entity_layers', {}).get('IMAGE'):
        layers = sorted(report['entity_layers']['IMAGE'])

    return [
        'DXF Raster Summary:',
        '- Raster IMAGE detected.',
        '- File: %s' % filename,
        '- DXF Version: %s / %s' % (report['version'], report['release']),
        '- IMAGE entities: %d' % image_count,
        '- Layers: %s' % (', '.join(layers) if layers else 'unknown'),
        '- Blocks: %s' % (', '.join(blocks) if blocks else 'none'),
        '- Insertions: %s' % (', '.join(insertions) if insertions else 'none'),
        'IMAGE detected.',
        '- Status: Future Feature',
        '- Reason: Raster image detected.',
        '- Planned module: Raster Vectorization.',
        '- Current behavior: Raster IMAGE entities are identified correctly but are intentionally ignored because '
        'they do not contain vector CAM geometry.',
        '- Recommendation: If CAM geometry is required today, export the design as SVG, PDF (vector), or Gerber '
        'whenever possible.',
        '- Affected layer(s): %s' % (', '.join(layers) if layers else 'unknown'),
        '- Source block(s): %s' % (', '.join(blocks) if blocks else 'none'),
        '- This feature has been identified and documented for a future Raster Vectorization module of '
        'FlatCAM 9 Neo S2.',
        '- Future compatibility: Raster Image -> Raster Vectorization -> Geometry -> Isolation Geometry -> CNCJob -> '
        'Gerber (optional).'
    ]


def dxf_geometry_extents_messages(report):
    """Return dimensions of converted geometry when the DXF does not declare units."""

    extents = report.get('geometry_extents')
    if not extents:
        return []

    return [
        'Geometry extents detected:',
        '- Width: %.3f units' % extents['width'],
        '- Height: %.3f units' % extents['height'],
        '- Units are not declared by this DXF file.',
        '- These dimensions correspond to the imported geometry only.',
        '- Verify physical units before machining.'
    ]


def dxf_import_report_messages(report):
    """Return consistent informational Shell messages for Geometry and Gerber DXF imports."""

    messages = [
        'DXF Compatibility:',
        '- Opened version: %s / %s' % (report['version'], report['release'])
    ]

    source_detection = report.get('source_detection') or {}
    if source_detection:
        source_names = {
            'illustrator': 'Adobe Illustrator',
            'kicad': 'KiCad',
            'inkscape': 'Inkscape',
            'proteus': 'Proteus/ARES',
            'unknown': 'Unknown'
        }
        source = source_names.get(
            str(source_detection.get('source', 'unknown')).lower(),
            str(source_detection.get('source', 'unknown')).capitalize()
        )
        confidence = str(source_detection.get('confidence', 'low')).capitalize()
        messages.extend([
            'DXF Source Detection:',
            '- Source: %s' % source,
            '- Profile: %s' % source_detection.get('export_profile', 'Unknown DXF source'),
            '- Confidence: %s' % confidence
        ])
        recommendations = source_detection.get('recommendations', [])
        # Proteus outline-only already has a detailed warning in the DXF report; avoid repeating it here.
        if recommendations and not report.get('proteus_outline_detection', {}).get('detected'):
            messages.append('- Recommendation: %s' % recommendations[0])

    messages.extend(report['orientation_messages'])
    messages.extend([
        report['units_message']
    ])
    messages.extend(dxf_geometry_extents_messages(report))
    messages.extend([
        '- Compatibility note: %s' % report['compatibility_note'],
        '- Recommended for best compatibility: %s' % report['recommended_version'],
        '- Supported versions: %s' % report['supported_versions'],
        '- Current file status: %s' % report['compatibility_status'],
        dxf_import_report_summary(report),
        'DXF Layer Report:'
    ])

    layer_counts = report['layer_counts']
    if not layer_counts:
        messages.append('- none')
        messages.extend(dxf_raster_summary_messages(report))
        return messages

    warning_layers = set(report['bulge_layers']) | set(report['three_d_layers']) | \
        set(report['nonzero_z_layers']) | set(report['extrusion_layers'])
    for layer in sorted(layer_counts):
        layer_data = layer_counts[layer]
        ordered = DXF_DIAGNOSTIC_TYPES + sorted(kind for kind in layer_data if kind not in DXF_DIAGNOSTIC_TYPES)
        layer_text = '- %s: %s' % (layer, _dxf_format_counts(layer_data, ordered))
        kinds = set(layer_data)
        if kinds and kinds.issubset({'IMAGE', 'TEXT', 'MTEXT'}):
            layer_text += ' [ignored]'
        elif kinds.intersection(DXF_PARTIAL_TYPES | DXF_IGNORED_TYPES) or layer in warning_layers or \
                any(kind not in DXF_DIAGNOSTIC_TYPES for kind in kinds):
            layer_text += ' [warning]'
        messages.append(layer_text)
    messages.extend(dxf_raster_summary_messages(report))
    return messages


def dxf_export_recommendation_messages():
    """Return the common non-invasive export guidance shown after DXF warnings."""

    return [
        'Recommended export settings:',
        '- DXF: AutoCAD R12 ASCII',
        '- SVG: SVG Tiny 1.2 / plain SVG compatible',
        '- Text: convert to outlines if engraving is needed',
        '- Curves: convert splines/beziers to polylines when possible'
    ]


def distance(pt1, pt2):
    return math.sqrt((pt1[0] - pt2[0]) ** 2 + (pt1[1] - pt2[1]) ** 2)


def dxfpoint2shapely(point):

    geo = Point(point.dxf.location).buffer(0.01)
    return geo


def dxfline2shapely(line):

    try:
        start = (line.dxf.start[0], line.dxf.start[1])
        stop = (line.dxf.end[0], line.dxf.end[1])

    except Exception as e:
        log.debug(str(e))
        return None

    geo = LineString([start, stop])

    return geo


def dxfcircle2shapely(circle, n_points=100):

    ocs = circle.ocs()
    # if the extrusion attribute is not (0, 0, 1) then we have to change the coordinate system from OCS to WCS
    if circle.dxf.extrusion != (0, 0, 1):
        center_pt = ocs.to_wcs(circle.dxf.center)
    else:
        center_pt = circle.dxf.center

    radius = circle.dxf.radius
    geo = Point(center_pt).buffer(radius, int(n_points / 4))

    return geo


def dxfarc2shapely(arc, n_points=100):
    # ocs = arc.ocs()
    # # if the extrusion attribute is not (0, 0, 1) then we have to change the coordinate system from OCS to WCS
    # if arc.dxf.extrusion != (0, 0, 1):
    #     arc_center = ocs.to_wcs(arc.dxf.center)
    #     start_angle = math.radians(arc.dxf.start_angle) + math.pi
    #     end_angle = math.radians(arc.dxf.end_angle) + math.pi
    #     dir = 'CW'
    # else:
    #     arc_center = arc.dxf.center
    #     start_angle = math.radians(arc.dxf.start_angle)
    #     end_angle = math.radians(arc.dxf.end_angle)
    #     dir = 'CCW'
    #
    # center_x = arc_center[0]
    # center_y = arc_center[1]
    # radius = arc.dxf.radius
    #
    # point_list = []
    #
    # if start_angle > end_angle:
    #     start_angle +=  2 * math.pi
    #
    # line_seg = int((n_points * (end_angle - start_angle)) / math.pi)
    # step_angle = (end_angle - start_angle) / float(line_seg)
    #
    # angle = start_angle
    # for step in range(line_seg + 1):
    #     if dir == 'CCW':
    #         x = center_x + radius * math.cos(angle)
    #         y = center_y + radius * math.sin(angle)
    #     else:
    #         x = center_x + radius * math.cos(-angle)
    #         y = center_y + radius * math.sin(-angle)
    #     point_list.append((x, y))
    #     angle += step_angle
    #
    #
    # log.debug("X = %.4f, Y = %.4f, Radius = %.4f, start_angle = %.1f, stop_angle = %.1f, step_angle = %.4f, dir=%s" %
    #           (center_x, center_y, radius, start_angle, end_angle, step_angle, dir))
    #
    # geo = LineString(point_list)
    # return geo

    ocs = arc.ocs()
    # if the extrusion attribute is not (0, 0, 1) then we have to change the coordinate system from OCS to WCS
    if arc.dxf.extrusion != (0, 0, 1):
        arc_center = ocs.to_wcs(arc.dxf.center)
        start_angle = arc.dxf.start_angle + 180
        end_angle = arc.dxf.end_angle + 180
        direction = 'CW'
    else:
        arc_center = arc.dxf.center
        start_angle = arc.dxf.start_angle
        end_angle = arc.dxf.end_angle
        direction = 'CCW'

    center_x = arc_center[0]
    center_y = arc_center[1]
    radius = arc.dxf.radius

    point_list = []

    if start_angle > end_angle:
        start_angle = start_angle - 360
    angle = start_angle

    step_angle = float(abs(end_angle - start_angle) / n_points)

    while angle <= end_angle:
        if direction == 'CCW':
            x = center_x + radius * math.cos(math.radians(angle))
            y = center_y + radius * math.sin(math.radians(angle))
        else:
            x = center_x + radius * math.cos(math.radians(-angle))
            y = center_y + radius * math.sin(math.radians(-angle))
        point_list.append((x, y))
        angle += abs(step_angle)

    # in case the number of segments do not cover everything until the end of the arc
    if angle != end_angle:
        if direction == 'CCW':
            x = center_x + radius * math.cos(math.radians(end_angle))
            y = center_y + radius * math.sin(math.radians(end_angle))
        else:
            x = center_x + radius * math.cos(math.radians(- end_angle))
            y = center_y + radius * math.sin(math.radians(- end_angle))
        point_list.append((x, y))

    # log.debug("X = %.4f, Y = %.4f, Radius = %.4f, start_angle = %.1f, stop_angle = %.1f, step_angle = %.4f" %
    #           (center_x, center_y, radius, start_angle, end_angle, step_angle))

    geo = LineString(point_list)
    return geo


def dxfellipse2shapely(ellipse, ellipse_segments=100):
    # center = ellipse.dxf.center
    # start_angle = ellipse.dxf.start_param
    # end_angle = ellipse.dxf.end_param

    ocs = ellipse.ocs()
    # if the extrusion attribute is not (0, 0, 1) then we have to change the coordinate system from OCS to WCS
    if ellipse.dxf.extrusion != (0, 0, 1):
        center = ocs.to_wcs(ellipse.dxf.center)
        start_angle = ocs.to_wcs(ellipse.dxf.start_param)
        end_angle = ocs.to_wcs(ellipse.dxf.end_param)
        direction = 'CW'
    else:
        center = ellipse.dxf.center
        start_angle = ellipse.dxf.start_param
        end_angle = ellipse.dxf.end_param
        direction = 'CCW'

    # print("Dir = %s" % dir)
    major_axis = ellipse.dxf.major_axis
    ratio = ellipse.dxf.ratio

    points_list = []
    major_axis = Vector(list(major_axis))

    major_x = major_axis[0]
    major_y = major_axis[1]

    if start_angle >= end_angle:
        end_angle += 2.0 * math.pi

    line_seg = int((ellipse_segments * (end_angle - start_angle)) / math.pi)
    step_angle = abs(end_angle - start_angle) / float(line_seg)

    angle = start_angle
    for step in range(line_seg + 1):
        if direction == 'CW':
            major_dim = normalize_2(major_axis)
            minor_dim = normalize_2(Vector([ratio * k for k in major_axis]))
            vx = (major_dim[0] + major_dim[1]) * math.cos(angle)
            vy = (minor_dim[0] - minor_dim[1]) * math.sin(angle)
            x = center[0] + major_x * vx - major_y * vy
            y = center[1] + major_y * vx + major_x * vy
            angle += step_angle
        else:
            major_dim = normalize_2(major_axis)
            minor_dim = (Vector([ratio * k for k in major_dim]))
            vx = (major_dim[0] + major_dim[1]) * math.cos(angle)
            vy = (minor_dim[0] + minor_dim[1]) * math.sin(angle)
            x = center[0] + major_x * vx + major_y * vy
            y = center[1] + major_y * vx + major_x * vy
            angle += step_angle

        points_list.append((x, y))

    geo = LineString(points_list)
    return geo


def dxfbulge2points(start, end, bulge, segments=32):
    """Approximate one DXF bulge segment while preserving start-to-end orientation."""

    start = (float(start[0]), float(start[1]))
    end = (float(end[0]), float(end[1]))
    if start == end:
        return [start]

    try:
        bulge = float(bulge)
        if math.isfinite(bulge) is False:
            raise ValueError("non-finite bulge")
    except (TypeError, ValueError) as e:
        log.warning("DXF bulge ignored; straight segment used: %s" % str(e))
        return [start, end]

    if abs(bulge) <= 1e-12:
        return [start, end]

    try:
        center, start_angle, end_angle, radius = bulge_to_arc(start, end, bulge)
        if radius <= 0 or math.isfinite(radius) is False:
            raise ValueError("invalid bulge radius")

        while end_angle <= start_angle:
            end_angle += 2.0 * math.pi
        sweep = end_angle - start_angle
        segment_count = max(2, int(math.ceil(max(4, int(segments)) * sweep / (2.0 * math.pi))))

        points = [
            (
                float(center[0]) + radius * math.cos(start_angle + sweep * index / segment_count),
                float(center[1]) + radius * math.sin(start_angle + sweep * index / segment_count)
            )
            for index in range(segment_count + 1)
        ]
        if bulge < 0:
            points.reverse()

        points[0] = start
        points[-1] = end
        return [point for index, point in enumerate(points) if index == 0 or point != points[index - 1]]
    except Exception as e:
        log.warning("DXF bulge approximation failed; straight segment used: %s" % str(e))
        return [start, end]


def _dxf_vertices2linestring(vertices, closed=False):
    if len(vertices) < 2:
        return None

    final_pts = []
    segment_count = len(vertices) if closed else len(vertices) - 1
    for index in range(segment_count):
        start, bulge = vertices[index]
        end = vertices[(index + 1) % len(vertices)][0]
        for point in dxfbulge2points(start, end, bulge):
            if not final_pts or point != final_pts[-1]:
                final_pts.append(point)

    if len(final_pts) < 2:
        log.warning("DXF POLYLINE/LWPOLYLINE ignored: fewer than two distinct points.")
        return None

    return LineString(final_pts)


def dxfpolyline2shapely(polyline):
    vertices = [
        ((vertex.dxf.location[0], vertex.dxf.location[1]), vertex.dxf.bulge)
        for vertex in polyline
    ]
    return _dxf_vertices2linestring(vertices, closed=polyline.is_closed)


def dxflwpolyline2shapely(lwpolyline):
    vertices = [((point[0], point[1]), point[4]) for point in lwpolyline]
    return _dxf_vertices2linestring(vertices, closed=lwpolyline.closed)


def dxfsolid2shapely(solid):
    try:
        corner_list = [(float(vertex[0]), float(vertex[1])) for vertex in solid]
    except (IndexError, TypeError, ValueError) as e:
        log.warning("DXF SOLID/TRACE ignored: invalid vertices: %s" % str(e))
        return None

    if len(corner_list) == 4 and corner_list[3] == corner_list[2]:
        corner_list.pop()

    if len(set(corner_list)) < 3:
        log.warning("DXF SOLID/TRACE ignored: fewer than three unique vertices.")
        return None

    geo = Polygon(corner_list)
    if geo.is_empty or geo.is_valid is False or geo.area <= 0:
        log.warning("DXF SOLID/TRACE ignored: invalid or zero-area polygon.")
        return None

    return geo


def dxfspline2shapely(spline):
    # for old version of ezdxf
    # with spline.edit_data() as spline_data:
    #     ctrl_points = spline_data.control_points
    #     try:
    #         # required if using old version of ezdxf
    #         knot_values = spline_data.knot_values
    #     except AttributeError:
    #         knot_values = spline_data.knots

    ctrl_points = spline.control_points

    try:
        construction_tool = spline.construction_tool()
        segment_count = max(20, 20 * len(ctrl_points))
        points_list = [(point.x, point.y) for point in construction_tool.approximate(segment_count)]
        if len(points_list) >= 2:
            return LineString(points_list)
    except Exception as e:
        log.debug("DXF SPLINE construction_tool() fallback --> %s" % str(e))

    knot_values = spline.knots
    is_closed = spline.closed
    degree = spline.dxf.degree

    x_list, y_list, _ = spline2Polyline(ctrl_points, degree=degree, closed=is_closed, segments=20, knots=knot_values)
    points_list = zip(x_list, y_list)

    geo = LineString(points_list)
    return geo


def dxftrace2shapely(trace):
    return dxfsolid2shapely(trace)


def _dxf_hatch_points_close(first, second, tolerance=1e-8):
    return abs(float(first[0]) - float(second[0])) <= tolerance and \
        abs(float(first[1]) - float(second[1])) <= tolerance


def _dxf_hatch_arc_points(edge, segments=64):
    center = edge.center
    radius = float(edge.radius)
    if radius <= 0 or math.isfinite(radius) is False:
        return []

    start = math.radians(float(edge.start_angle))
    end = math.radians(float(edge.end_angle))
    if bool(edge.ccw):
        while end <= start:
            end += 2.0 * math.pi
    else:
        while end >= start:
            end -= 2.0 * math.pi

    sweep = end - start
    count = max(2, int(math.ceil(max(8, int(segments)) * abs(sweep) / (2.0 * math.pi))))
    return [
        (
            float(center[0]) + radius * math.cos(start + sweep * index / count),
            float(center[1]) + radius * math.sin(start + sweep * index / count)
        )
        for index in range(count + 1)
    ]


def _dxf_hatch_ellipse_points(edge, segments=64):
    try:
        ellipse = edge.construction_tool()
        span = abs(float(ellipse.param_span))
        count = max(2, int(math.ceil(max(8, int(segments)) * span / (2.0 * math.pi))))
        return [(float(point[0]), float(point[1])) for point in ellipse.vertices(ellipse.params(count + 1))]
    except Exception as e:
        log.warning("DXF HATCH ellipse edge ignored: %s" % str(e))
        return []


def _dxf_hatch_spline_points(edge, segments=20):
    """Approximate a HATCH SplineEdge only when ezdxf exposes safe spline control data."""

    try:
        degree = int(edge.degree)
        control_points = list(edge.control_points)
        knot_values = list(edge.knot_values) if edge.knot_values else None
        is_closed = bool(edge.periodic)
    except (AttributeError, TypeError, ValueError) as e:
        log.warning("DXF HATCH spline edge ignored: %s" % str(e))
        return []

    if len(control_points) < max(2, degree + 1):
        log.warning("DXF HATCH spline edge ignored: not enough control points.")
        return []

    try:
        spline_points = [
            (
                float(point[0]),
                float(point[1]),
                float(point[2]) if len(point) > 2 else 0.0
            )
            for point in control_points
        ]
        x_list, y_list, _ = spline2Polyline(
            spline_points,
            degree=degree,
            closed=is_closed,
            segments=segments,
            knots=knot_values
        )
    except Exception as e:
        log.warning("DXF HATCH spline edge approximation failed: %s" % str(e))
        return []

    if not x_list or not y_list or len(x_list) != len(y_list):
        log.warning("DXF HATCH spline edge ignored: approximation returned no usable points.")
        return []

    points = [(float(x), float(y)) for x, y in zip(x_list, y_list)]
    if len(points) >= 2 and _dxf_hatch_points_close(points[0], points[-1]) is False:
        first_control = control_points[0]
        last_control = control_points[-1]
        control_points_closed = _dxf_hatch_points_close(first_control, last_control)
        if is_closed or control_points_closed:
            points.append(points[0])

    return points


def _dxf_hatch_edge_points(edge):
    kind = getattr(edge, 'EDGE_TYPE', edge.__class__.__name__)
    try:
        if kind == 'LineEdge':
            return [
                (float(edge.start[0]), float(edge.start[1])),
                (float(edge.end[0]), float(edge.end[1]))
            ]
        if kind == 'ArcEdge':
            return _dxf_hatch_arc_points(edge)
        if kind == 'EllipseEdge':
            return _dxf_hatch_ellipse_points(edge)
        if kind == 'SplineEdge':
            return _dxf_hatch_spline_points(edge)
    except (AttributeError, IndexError, TypeError, ValueError) as e:
        log.warning("DXF HATCH %s ignored: %s" % (kind, str(e)))
        return []

    log.warning("DXF HATCH edge type '%s' is not supported." % kind)
    return []


def _dxf_hatch_edge_path(path):
    segments = []
    unsupported_edge = False
    for edge in path.edges:
        points = _dxf_hatch_edge_points(edge)
        if len(points) < 2:
            unsupported_edge = True
            continue
        segments.append(LineString(points))

    if not segments:
        return None, []

    points = list(segments[0].coords)
    connected = True
    for segment in segments[1:]:
        edge_points = list(segment.coords)
        if _dxf_hatch_points_close(points[-1], edge_points[0]):
            points.extend(edge_points[1:])
        elif _dxf_hatch_points_close(points[-1], edge_points[-1]):
            edge_points.reverse()
            points.extend(edge_points[1:])
        else:
            connected = False
            break

    closed = connected and len(points) >= 4 and _dxf_hatch_points_close(points[0], points[-1])
    if closed and unsupported_edge is False:
        points[-1] = points[0]
        return points, []

    log.warning("DXF HATCH edge path is open, disconnected or incomplete; boundary lines were preserved.")
    return None, segments


def _dxf_hatch_path(path):
    path_kind = getattr(path, 'PATH_TYPE', path.__class__.__name__)
    if path_kind == 'PolylinePath':
        vertices = [((vertex[0], vertex[1]), vertex[2]) for vertex in path.vertices]
        line = _dxf_vertices2linestring(vertices, closed=bool(path.is_closed))
        if line is None:
            return None, []
        if bool(path.is_closed) is False:
            log.warning("DXF HATCH open polyline boundary was preserved as a LineString.")
            return None, [line]
        return list(line.coords), []

    if path_kind == 'EdgePath':
        return _dxf_hatch_edge_path(path)

    log.warning("DXF HATCH boundary path type '%s' is not supported." % path_kind)
    return None, []


def _dxf_hatch_polygon(points):
    if points is None or len(points) < 4:
        return None
    try:
        polygon = Polygon(points)
    except Exception as e:
        log.warning("DXF HATCH boundary could not create a Polygon: %s" % str(e))
        return None

    if polygon.is_empty or polygon.area <= 0:
        return None
    if polygon.is_valid:
        return polygon

    try:
        repaired = polygon.buffer(0)
    except Exception as e:
        log.warning("DXF HATCH invalid boundary could not be repaired: %s" % str(e))
        return None

    if repaired.geom_type == 'Polygon' and repaired.is_valid and not repaired.is_empty and repaired.area > 0:
        log.warning("DXF HATCH invalid boundary was repaired with buffer(0).")
        return repaired

    log.warning("DXF HATCH invalid boundary was not filled because a safe single-Polygon repair was unavailable.")
    return None


def _dxf_hatch_compose_polygons(candidates):
    if not candidates:
        return []

    ordered = sorted(candidates, key=lambda item: item['polygon'].area, reverse=True)
    for index, item in enumerate(ordered):
        parent = None
        parent_area = None
        for possible_parent in ordered[:index]:
            try:
                contains = possible_parent['polygon'].covers(item['polygon'])
            except Exception:
                contains = False
            if contains and (parent_area is None or possible_parent['polygon'].area < parent_area):
                parent = possible_parent
                parent_area = possible_parent['polygon'].area
        item['parent'] = parent
        item['depth'] = 0 if parent is None else parent['depth'] + 1

    result = []
    for shell in ordered:
        if shell['depth'] % 2:
            continue

        holes = [
            child['polygon'].exterior.coords
            for child in ordered
            if child.get('parent') is shell and child['depth'] == shell['depth'] + 1
        ]
        try:
            polygon = Polygon(shell['polygon'].exterior.coords, holes)
        except Exception as e:
            log.warning("DXF HATCH shell/island composition failed: %s" % str(e))
            continue

        if polygon.is_valid and not polygon.is_empty and polygon.area > 0:
            result.append(polygon)
            continue

        try:
            repaired = polygon.buffer(0)
        except Exception:
            repaired = None
        if repaired is not None and repaired.geom_type == 'Polygon' and repaired.is_valid and \
                not repaired.is_empty and repaired.area > 0:
            log.warning("DXF HATCH shell/island composition was repaired with buffer(0).")
            result.append(repaired)
        else:
            log.warning("DXF HATCH shell/island composition was ignored because it remained invalid.")

    return result


def dxfhatch2shapely(hatch):
    """Convert safe HATCH boundaries to polygons and retain ambiguous boundaries as lines."""

    candidates = []
    fallback_lines = []
    try:
        paths = hatch.paths.paths
    except AttributeError:
        log.warning("DXF HATCH ignored: boundary paths are unavailable.")
        return None

    for path in paths:
        points, lines = _dxf_hatch_path(path)
        fallback_lines.extend(lines)
        polygon = _dxf_hatch_polygon(points)
        if polygon is None:
            if points is not None and len(points) >= 2:
                fallback_lines.append(LineString(points))
            continue
        candidates.append({
            'polygon': polygon,
            'flags': int(getattr(path, 'path_type_flags', 0)),
            'parent': None,
            'depth': 0
        })

    geometry = _dxf_hatch_compose_polygons(candidates)
    geometry.extend(line for line in fallback_lines if line is not None and not line.is_empty)
    if not geometry:
        log.warning("DXF HATCH did not contain a usable closed boundary.")
        return None
    return geometry


def getdxfgeo(dxf_object):

    msp = dxf_object.modelspace()
    geos = get_geo(dxf_object, msp)

    # geo_block = get_geo_from_block(dxf_object)

    return geos


def _get_geo_from_insert_fallback(dxf_object, insert):
    """Legacy INSERT conversion retained for older or incompatible ezdxf objects."""
    geo_block_transformed = []

    phi = insert.dxf.rotation
    tr = insert.dxf.insert
    sx = insert.dxf.xscale
    sy = insert.dxf.yscale
    r_count = insert.dxf.row_count
    r_spacing = insert.dxf.row_spacing
    c_count = insert.dxf.column_count
    c_spacing = insert.dxf.column_spacing

    # print(phi, tr)

    # identify the block given the 'INSERT' type entity name
    block = dxf_object.blocks[insert.dxf.name]
    block_coords = (block.block.dxf.base_point[0], block.block.dxf.base_point[1])

    # get a list of geometries found in the block
    geo_block = get_geo(dxf_object, block)

    # iterate over the geometries found and apply any transformation found in the 'INSERT' entity attributes
    for geo in geo_block:

        # get the bounds of the geometry
        # minx, miny, maxx, maxy = geo.bounds

        if tr[0] != 0 or tr[1] != 0:
            geo = translate(geo, (tr[0] - block_coords[0]), (tr[1] - block_coords[1]))

        # support for array block insertions
        if r_count > 1:
            for r in range(r_count):
                geo_block_transformed.append(translate(geo, (tr[0] + (r * r_spacing) - block_coords[0]), 0))
        if c_count > 1:
            for c in range(c_count):
                geo_block_transformed.append(translate(geo, 0, (tr[1] + (c * c_spacing) - block_coords[1])))

        if sx != 1 or sy != 1:
            geo = scale(geo, sx, sy)
        if phi != 0:
            if isinstance(tr, str) and tr.lower() == 'c':
                tr = 'center'
            elif isinstance(tr, ezdxf_vector):
                tr = list(tr)
            geo = rotate(geo, phi, origin=tr)

        geo_block_transformed.append(geo)
    return geo_block_transformed


def get_geo_from_insert(dxf_object, insert):
    """Expand INSERT/MINSERT with ezdxf transformations and use the legacy converter as fallback."""

    try:
        block_name = str(insert.dxf.name)
    except AttributeError:
        log.warning("DXF INSERT ignored: missing BLOCK name.")
        return []

    if block_name not in dxf_object.blocks:
        log.warning("DXF INSERT ignored: BLOCK '%s' does not exist." % block_name)
        return []

    try:
        xscale = float(insert.dxf.xscale)
        yscale = float(insert.dxf.yscale)
        if abs(abs(xscale) - abs(yscale)) > 1e-12:
            log.warning(
                "DXF INSERT '%s' uses non-uniform scale X=%s, Y=%s; transformed curves should be verified." %
                (block_name, xscale, yscale)
            )
    except (AttributeError, TypeError, ValueError):
        pass

    def legacy_fallback(reason):
        log.warning("DXF INSERT '%s': %s Using legacy transformation fallback." % (block_name, reason))
        try:
            return _get_geo_from_insert_fallback(dxf_object, insert)
        except Exception as fallback_error:
            log.warning(
                "DXF INSERT '%s' ignored because both virtual and fallback conversion failed: %s" %
                (block_name, str(fallback_error))
            )
            return []

    inserts = [insert]
    try:
        insert_count = int(insert.mcount)
    except (AttributeError, TypeError, ValueError):
        try:
            insert_count = max(1, int(insert.dxf.row_count)) * max(1, int(insert.dxf.column_count))
        except (AttributeError, TypeError, ValueError):
            insert_count = 1

    if insert_count > 1:
        try:
            inserts = list(insert.multi_insert())
            if not inserts:
                return legacy_fallback(
                    "multi_insert() returned no array elements; the INSERT array may be incomplete."
                )
        except Exception as e:
            return legacy_fallback(
                "multi_insert() failed and the INSERT array may be incomplete: %s." % str(e)
            )

    geo = []
    for array_index, array_insert in enumerate(inserts):
        skipped = []

        def skipped_entity_callback(entity, reason):
            try:
                kind = entity.dxftype()
            except AttributeError:
                kind = 'unknown'
            skipped.append((kind, str(reason)))

        try:
            virtual_entities = list(
                array_insert.virtual_entities(skipped_entity_callback=skipped_entity_callback)
            )
        except Exception as e:
            if insert_count > 1:
                reason = "virtual_entities() failed for array item %d/%d: %s." % \
                         (array_index + 1, len(inserts), str(e))
            else:
                reason = "virtual_entities() failed: %s." % str(e)
            return legacy_fallback(reason)

        unsupported_skips = []
        for kind, reason in skipped:
            log.warning("DXF INSERT '%s' skipped %s while creating virtual entities: %s" %
                        (block_name, kind, reason))
            if kind in DXF_SUPPORTED_TYPES or kind in DXF_PARTIAL_TYPES:
                unsupported_skips.append(kind)

        if unsupported_skips:
            return legacy_fallback(
                "virtual_entities() skipped supported geometry: %s." % ', '.join(sorted(set(unsupported_skips)))
            )

        try:
            geo.extend(get_geo(dxf_object, virtual_entities))
        except Exception as e:
            return legacy_fallback("conversion of virtual entities failed: %s." % str(e))

    return geo


def get_geo(dxf_object, container):
    # store shapely geometry here
    geo = []

    for dxf_entity in container:
        g = []
        # print("Entity", dxf_entity.dxftype())
        if dxf_entity.dxftype() == 'POINT':
            g = dxfpoint2shapely(dxf_entity,)
        elif dxf_entity.dxftype() == 'LINE':
            g = dxfline2shapely(dxf_entity,)
        elif dxf_entity.dxftype() == 'CIRCLE':
            g = dxfcircle2shapely(dxf_entity)
        elif dxf_entity.dxftype() == 'ARC':
            g = dxfarc2shapely(dxf_entity)
        elif dxf_entity.dxftype() == 'ELLIPSE':
            g = dxfellipse2shapely(dxf_entity)
        elif dxf_entity.dxftype() == 'LWPOLYLINE':
            g = dxflwpolyline2shapely(dxf_entity)
        elif dxf_entity.dxftype() == 'POLYLINE':
            g = dxfpolyline2shapely(dxf_entity)
        elif dxf_entity.dxftype() == 'SOLID':
            g = dxfsolid2shapely(dxf_entity)
        elif dxf_entity.dxftype() == 'TRACE':
            g = dxftrace2shapely(dxf_entity)
        elif dxf_entity.dxftype() == 'SPLINE':
            g = dxfspline2shapely(dxf_entity)
        elif dxf_entity.dxftype() == 'HATCH':
            g = dxfhatch2shapely(dxf_entity)
        elif dxf_entity.dxftype() in ['TEXT', 'MTEXT']:
            # Text is intentionally excluded from CAM geometry; getdxftext(mode='bbox') is diagnostic only.
            g = None
        elif dxf_entity.dxftype() == 'INSERT':
            g = get_geo_from_insert(dxf_object, dxf_entity)
        else:
            log.debug(" %s is not supported yet." % dxf_entity.dxftype())

        if g is not None:
            if type(g) == list:
                for subg in g:
                    geo.append(subg)
            else:
                geo.append(g)

    return geo


def _dxf_text_content(entity):
    try:
        if entity.dxftype() == 'MTEXT':
            lines = entity.plain_text(split=True)
            return [str(line) for line in lines] if lines else ['']
        return [str(entity.plain_text())]
    except Exception:
        try:
            return [str(entity.dxf.text)]
        except AttributeError:
            return ['']


def _dxf_text_box(entity):
    kind = entity.dxftype()
    lines = _dxf_text_content(entity)
    longest_line = max([len(line.expandtabs(4)) for line in lines] + [1])

    try:
        if kind == 'MTEXT':
            height = abs(float(entity.dxf.char_height))
            line_spacing = abs(float(entity.get_dxf_attrib('line_spacing_factor', 1.0)))
            line_spacing = line_spacing if line_spacing > 0 else 1.0
            total_height = height * max(1, len(lines)) * 1.2 * line_spacing
            declared_width = abs(float(entity.get_dxf_attrib('width', 0.0)))
            width = declared_width if declared_width > 0 else height * 0.6 * longest_line
            position = entity.dxf.insert
            rotation = float(entity.get_rotation())
            attachment = int(entity.get_dxf_attrib('attachment_point', 1))

            column = (attachment - 1) % 3
            row = min(2, max(0, (attachment - 1) // 3))
            x0 = 0.0 if column == 0 else (-width / 2.0 if column == 1 else -width)
            if row == 0:
                y0 = -total_height
            elif row == 1:
                y0 = -total_height / 2.0
            else:
                y0 = 0.0
        else:
            height = abs(float(entity.dxf.height))
            width_factor = abs(float(entity.get_dxf_attrib('width', 1.0)))
            width_factor = width_factor if width_factor > 0 else 1.0
            width = height * 0.6 * longest_line * width_factor
            total_height = height
            align, position, second_point = entity.get_pos()
            rotation = float(entity.get_dxf_attrib('rotation', 0.0))

            if align in ['ALIGNED', 'FIT'] and second_point is not None:
                dx = float(second_point[0]) - float(position[0])
                dy = float(second_point[1]) - float(position[1])
                fitted_width = math.hypot(dx, dy)
                if fitted_width > 0:
                    width = fitted_width
                    rotation = math.degrees(math.atan2(dy, dx))
                x0 = 0.0
            elif align in ['CENTER', 'MIDDLE', 'BOTTOM_CENTER', 'MIDDLE_CENTER', 'TOP_CENTER']:
                x0 = -width / 2.0
            elif align in ['RIGHT', 'BOTTOM_RIGHT', 'MIDDLE_RIGHT', 'TOP_RIGHT']:
                x0 = -width
            else:
                x0 = 0.0

            if align.startswith('TOP_'):
                y0 = -total_height
            elif align.startswith('MIDDLE_') or align == 'MIDDLE':
                y0 = -total_height / 2.0
            else:
                y0 = 0.0
    except (AttributeError, TypeError, ValueError) as e:
        log.warning("DXF %s diagnostic bounding box ignored: %s" % (kind, str(e)))
        return None

    if height <= 0 or width <= 0 or total_height <= 0:
        log.warning("DXF %s diagnostic bounding box ignored: zero width or height." % kind)
        return None

    px = float(position[0])
    py = float(position[1])
    box = Polygon([
        (px + x0, py + y0),
        (px + x0 + width, py + y0),
        (px + x0 + width, py + y0 + total_height),
        (px + x0, py + y0 + total_height)
    ])
    if rotation:
        box = rotate(box, rotation, origin=(px, py))
    return box if box.is_valid and not box.is_empty and box.area > 0 else None


def dxftext2shapely(entity, mode='ignore'):
    """Return an optional diagnostic box; never create font outlines or automatic CAM geometry."""

    normalized_mode = str(mode).strip().lower()
    if normalized_mode == 'ignore':
        return None
    if normalized_mode != 'bbox':
        log.warning("Unknown DXF text mode '%s'; TEXT/MTEXT ignored." % mode)
        return None
    return _dxf_text_box(entity)


def _dxf_text_from_container(dxf_object, container, mode, depth=0):
    if depth > 16:
        log.warning("DXF TEXT diagnostic stopped: nested INSERT depth exceeded 16 levels.")
        return []

    geometry = []
    for entity in container:
        kind = entity.dxftype()
        if kind in ['TEXT', 'MTEXT']:
            box = dxftext2shapely(entity, mode=mode)
            if box is not None:
                geometry.append(box)
            continue
        if kind != 'INSERT':
            continue

        try:
            block_name = str(entity.dxf.name)
        except AttributeError:
            log.warning("DXF TEXT diagnostic skipped an INSERT without a BLOCK name.")
            continue
        if block_name not in dxf_object.blocks:
            log.warning("DXF TEXT diagnostic skipped missing BLOCK '%s'." % block_name)
            continue

        try:
            insert_count = int(entity.mcount)
        except (AttributeError, TypeError, ValueError):
            insert_count = 1
        if insert_count > 1:
            try:
                inserts = list(entity.multi_insert())
            except Exception as e:
                log.warning("DXF TEXT diagnostic could not expand INSERT array '%s': %s" % (block_name, str(e)))
                continue
        else:
            inserts = [entity]

        for block_insert in inserts:
            skipped = []

            def skipped_entity_callback(skipped_entity, reason):
                skipped.append((skipped_entity.dxftype(), str(reason)))

            try:
                virtual_entities = list(
                    block_insert.virtual_entities(skipped_entity_callback=skipped_entity_callback)
                )
            except Exception as e:
                log.warning("DXF TEXT diagnostic could not transform BLOCK '%s': %s" % (block_name, str(e)))
                continue
            for skipped_kind, reason in skipped:
                if skipped_kind in ['TEXT', 'MTEXT']:
                    log.warning("DXF %s in BLOCK '%s' could not create a diagnostic box: %s" %
                                (skipped_kind, block_name, reason))
            geometry.extend(_dxf_text_from_container(dxf_object, virtual_entities, mode, depth + 1))

    return geometry


def getdxftext(dxf_object, object_type=None, units=None, mode='ignore'):
    """Return optional diagnostic TEXT/MTEXT boxes without adding them to imported CAM geometry."""

    del object_type, units  # retained for compatibility with the historical, unfinished API
    normalized_mode = str(mode).strip().lower()
    if normalized_mode == 'ignore':
        return []
    if normalized_mode != 'bbox':
        log.warning("Unknown DXF text mode '%s'; expected 'ignore' or 'bbox'." % mode)
        return []

    geometry = _dxf_text_from_container(dxf_object, dxf_object.modelspace(), normalized_mode)
    if geometry:
        log.warning(
            "DXF TEXT/MTEXT optional diagnostic bounding boxes generated: %d. These are approximations, not font "
            "outlines, and are not added automatically to CAM geometry." % len(geometry)
        )
    return geometry

# def get_geo_from_block(dxf_object):
#     geo_block_transformed = []
#
#     msp = dxf_object.modelspace()
#     # iterate through all 'INSERT' entities found in modelspace msp
#     for insert in msp.query('INSERT'):
#         phi = insert.dxf.rotation
#         tr = insert.dxf.insert
#         sx = insert.dxf.xscale
#         sy = insert.dxf.yscale
#         r_count = insert.dxf.row_count
#         r_spacing = insert.dxf.row_spacing
#         c_count = insert.dxf.column_count
#         c_spacing = insert.dxf.column_spacing
#
#         # print(phi, tr)
#
#         # identify the block given the 'INSERT' type entity name
#         print(insert.dxf.name)
#         block = dxf_object.blocks[insert.dxf.name]
#         block_coords = (block.block.dxf.base_point[0], block.block.dxf.base_point[1])
#
#         # get a list of geometries found in the block
#         # store shapely geometry here
#         geo_block = []
#
#         for dxf_entity in block:
#             g = []
#             # print("Entity", dxf_entity.dxftype())
#             if dxf_entity.dxftype() == 'POINT':
#                 g = dxfpoint2shapely(dxf_entity, )
#             elif dxf_entity.dxftype() == 'LINE':
#                 g = dxfline2shapely(dxf_entity, )
#             elif dxf_entity.dxftype() == 'CIRCLE':
#                 g = dxfcircle2shapely(dxf_entity)
#             elif dxf_entity.dxftype() == 'ARC':
#                 g = dxfarc2shapely(dxf_entity)
#             elif dxf_entity.dxftype() == 'ELLIPSE':
#                 g = dxfellipse2shapely(dxf_entity)
#             elif dxf_entity.dxftype() == 'LWPOLYLINE':
#                 g = dxflwpolyline2shapely(dxf_entity)
#             elif dxf_entity.dxftype() == 'POLYLINE':
#                 g = dxfpolyline2shapely(dxf_entity)
#             elif dxf_entity.dxftype() == 'SOLID':
#                 g = dxfsolid2shapely(dxf_entity)
#             elif dxf_entity.dxftype() == 'TRACE':
#                 g = dxftrace2shapely(dxf_entity)
#             elif dxf_entity.dxftype() == 'SPLINE':
#                 g = dxfspline2shapely(dxf_entity)
#             elif dxf_entity.dxftype() == 'INSERT':
#                 log.debug("Not supported yet.")
#             else:
#                 log.debug("Not supported yet.")
#
#             if g is not None:
#                 if type(g) == list:
#                     for subg in g:
#                         geo_block.append(subg)
#                 else:
#                     geo_block.append(g)
#
#         # iterate over the geometries found and apply any transformation found in the 'INSERT' entity attributes
#         for geo in geo_block:
#             if tr[0] != 0 or tr[1] != 0:
#                 geo = translate(geo, (tr[0] - block_coords[0]), (tr[1] - block_coords[1]))
#
#             # support for array block insertions
#             if r_count > 1:
#                 for r in range(r_count):
#                     geo_block_transformed.append(translate(geo, (tr[0] + (r * r_spacing) - block_coords[0]), 0))
#
#             if c_count > 1:
#                 for c in range(c_count):
#                     geo_block_transformed.append(translate(geo, 0, (tr[1] + (c * c_spacing) - block_coords[1])))
#
#             if sx != 1 or sy != 1:
#                 geo = scale(geo, sx, sy)
#             if phi != 0:
#                 geo = rotate(geo, phi, origin=tr)
#
#             geo_block_transformed.append(geo)
#     return geo_block_transformed
