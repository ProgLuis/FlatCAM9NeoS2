# ##########################################################
# FlatCAM: 2D Post-processing for Manufacturing            #
# http://flatcam.org                                       #
# Author: Juan Pablo Caram (c)                             #
# Date: 12/18/2015                                         #
# MIT Licence                                              #
#                                                          #
# SVG Features supported:                                  #
#  * Groups                                                #
#  * Rectangles (w/ rounded corners)                       #
#  * Circles                                               #
#  * Ellipses                                              #
#  * Polygons                                              #
#  * Polylines                                             #
#  * Lines                                                 #
#  * Paths                                                 #
#  * All transformations                                   #
#                                                          #
#  Reference: www.w3.org/TR/SVG/Overview.html              #
# ##########################################################

# ########################################################## ##
# FlatCAM 9 Neo S2                                            #
# Shapely 2.x Friendly Edition                                #
# Community modernized fork                                   #
# Maintained by Luis Enrique Yacupoma Aguirre                 #
# Date: 01/06/2026                                            #
# https://github.com/ProgLuis/FlatCAM9NeoS2                   #
# ########################################################## ##

# Neo S2 - 2026/06/18
# Proteus SVG Compatibility MVP
# Added support for:
# - inherited SVG styles (stroke, stroke-width, fill)
# - stroke-to-solid conversion
# - improved SVG point parsing
# - Proteus SVG drill extraction

# import xml.etree.ElementTree as ET
from lxml import etree as ET
from svg.path import Line, Arc, CubicBezier, QuadraticBezier, parse_path
# from svg.path.path import Move
# from svg.path.path import Close
import svg.path
from shapely.geometry import LineString, MultiLineString, Point, Polygon
from shapely.affinity import skew, affine_transform, rotate
from shapely.ops import polygonize, unary_union
import numpy as np

from appParsers.ParseFont import *

log = logging.getLogger('base2')


def svgparselength(lengthstr):
    """
    Parse an SVG length string into a float and a units
    string, if any.

    :param lengthstr:   SVG length string.
    :return:            Number and units pair.
    :rtype:             tuple(float, str|None)
    """

    integer_re_str = r'[+-]?[0-9]+'
    number_re_str = r'(?:[+-]?[0-9]*\.[0-9]+(?:[Ee]' + integer_re_str + ')?' + r')|' + \
                    r'(?:' + integer_re_str + r'(?:[Ee]' + integer_re_str + r')?)'
    length_re_str = r'(' + number_re_str + r')(em|ex|px|in|cm|mm|pt|pc|%)?'

    if lengthstr:
        match = re.search(length_re_str, lengthstr)
        if match:
            return float(match.group(1)), match.group(2)
    else:
        return 0, 0

    return


def svgparse_viewbox(root):
    return svg_physical_scale(root)['factor']


def svg_read_xmp_max_page_size(svg_root):
    """
    Read Illustrator XMP MaxPageSize metadata when present.

    This is useful for Illustrator SVG files that omit root width/height but
    preserve the physical page size in XMP metadata.

    :param svg_root:    SVG root element
    :return:            MaxPageSize dictionary or None
    :rtype:             dict|None
    """

    def local_name(node):
        if not isinstance(node.tag, str):
            return ''
        return node.tag.rsplit('}', 1)[-1]

    for node in svg_root.iter():
        if local_name(node) != 'MaxPageSize':
            continue

        data = {}
        for child in node:
            name = local_name(child)
            value = (child.text or '').strip()
            if name:
                data[name] = value

        try:
            return {
                'width': float(data.get('w')),
                'height': float(data.get('h')),
                'unit': data.get('unit')
            }
        except Exception as e:
            log.debug("ParseSVG.svg_read_xmp_max_page_size() --> %s" % str(e))
            return None

    return None


def svg_physical_scale(svg_root, tolerance=0.005):
    """
    Determine SVG physical scale factor and physical height.

    Scale priority:
    1. root width + viewBox
    2. Illustrator XMP MaxPageSize + viewBox
    3. factor 1.0 with missing-scale status

    :param svg_root:    SVG root element
    :param tolerance:   Relative tolerance for non-uniform XMP scale
    :type tolerance:    float
    :return:            Scale information
    :rtype:             dict
    """

    viewbox = svg_root.get('viewBox')
    default = {
        'scale_status': 'missing',
        'factor': 1.0,
        'height': 0.0,
        'viewbox': None,
        'xmp_max_page_size': None
    }

    if viewbox is None:
        default['scale_status'] = 'reliable'
        return default

    try:
        viewbox_values = [float(x) for x in viewbox.replace(',', ' ').split()]
        if len(viewbox_values) < 4:
            return default
    except Exception as e:
        log.debug("ParseSVG.svg_physical_scale() viewBox --> %s" % str(e))
        return default

    vb_w = viewbox_values[2]
    vb_h = viewbox_values[3]
    default['height'] = vb_h
    default['viewbox'] = viewbox_values[:4]

    width_value, width_units = svgparselength(svg_root.get('width'))
    height_value, height_units = svgparselength(svg_root.get('height'))

    if width_value and vb_w:
        default.update({
            'scale_status': 'reliable',
            'factor': width_value / vb_w,
            'height': height_value if height_value else vb_h * (width_value / vb_w),
            'width_units': width_units,
            'height_units': height_units
        })
        return default

    xmp_size = svg_read_xmp_max_page_size(svg_root)
    default['xmp_max_page_size'] = xmp_size
    if xmp_size and xmp_size.get('unit') == 'Millimeters' and vb_w and vb_h:
        factor_x = xmp_size['width'] / vb_w
        factor_y = xmp_size['height'] / vb_h
        factor_avg = (factor_x + factor_y) / 2.0
        rel_diff = abs(factor_x - factor_y) / factor_avg if factor_avg else 0

        if rel_diff <= tolerance:
            default.update({
                'scale_status': 'xmp_fallback',
                'factor': factor_avg,
                'height': xmp_size['height'],
                'factor_x': factor_x,
                'factor_y': factor_y,
                'relative_diff': rel_diff
            })
        else:
            default.update({
                'scale_status': 'non_uniform',
                'factor': factor_avg if factor_avg else 1.0,
                'height': xmp_size['height'],
                'factor_x': factor_x,
                'factor_y': factor_y,
                'relative_diff': rel_diff
            })
        return default

    return default


def svg_geometry_decimal_precision(svg_root):
    """Return the maximum decimal precision observed in SVG geometry coordinates.

    Illustrator does not store the export precision setting explicitly, so
    this reports evidence from serialized coordinates rather than certifying
    the original export option.
    """

    geometry_attributes = {
        'path': ['d'],
        'polyline': ['points'],
        'polygon': ['points'],
        'line': ['x1', 'y1', 'x2', 'y2'],
        'rect': ['x', 'y', 'width', 'height', 'rx', 'ry'],
        'circle': ['cx', 'cy', 'r'],
        'ellipse': ['cx', 'cy', 'rx', 'ry']
    }
    number_re = re.compile(r'[+-]?(?:\d+\.\d+|\d+|\.\d+)(?:[Ee][+-]?\d+)?')
    max_decimals = None

    for node in svg_root.iter():
        if not isinstance(node.tag, str):
            continue

        kind = node.tag.rpartition('}')[-1]
        for attribute in geometry_attributes.get(kind, []):
            value = node.get(attribute)
            if value is None:
                continue

            for match in number_re.finditer(value):
                mantissa = re.split('[Ee]', match.group(0))[0]
                decimals = len(mantissa.rpartition('.')[2]) if '.' in mantissa else 0
                max_decimals = decimals if max_decimals is None else max(max_decimals, decimals)

    return max_decimals


def svg_source_advisor(svg_filename):
    """
    Analyze SVG source and physical scale reliability.

    Pure helper: no GUI and no PyQt dependencies.

    :param svg_filename:    SVG filename
    :type svg_filename:     str
    :return:                SVG source advisory information
    :rtype:                 dict
    """

    try:
        svg_tree = ET.parse(svg_filename)
        svg_root = svg_tree.getroot()
        with open(svg_filename, 'r', encoding='utf-8', errors='ignore') as svg_file:
            svg_text = svg_file.read()
    except Exception as e:
        log.debug("ParseSVG.svg_source_advisor() --> %s" % str(e))
        return {
            'source': 'unknown',
            'category': 'unknown',
            'scale_status': 'missing',
            'factor': 1.0,
            'height': 0.0,
            'title': 'SVG scale warning',
            'message': 'This SVG could not be analyzed for reliable physical size information.',
            'message_level': 'warning'
        }

    text_lower = svg_text.lower()
    source = 'unknown'
    category = 'unknown'

    if 'adobe illustrator' in text_lower or 'creatortool' in text_lower:
        source = 'illustrator'
        category = 'vector'
    elif 'proteus design suite' in text_lower or 'proteus' in text_lower:
        source = 'proteus'
        category = 'pcb'
    elif 'inkscape' in text_lower:
        source = 'inkscape'
        category = 'vector'
    elif 'coreldraw' in text_lower or 'corel' in text_lower:
        source = 'coreldraw'
        category = 'vector'
    elif 'affinity' in text_lower:
        source = 'affinity'
        category = 'vector'

    scale_info = svg_physical_scale(svg_root)
    scale_status = scale_info['scale_status']

    uses_css_styles = any(
        str(node.tag).rpartition('}')[-1] == 'style' or
        node.get('class') is not None or
        node.get('style') is not None
        for node in svg_root.iter()
    )
    xmp_size = scale_info.get('xmp_max_page_size')
    svg_tiny_12 = (
        svg_root.get('version') == '1.2' and
        str(svg_root.get('baseProfile', '')).lower() == 'tiny'
    )
    uses_presentation_attributes = uses_css_styles is False
    has_xmp_physical_size = xmp_size is not None and xmp_size.get('unit') == 'Millimeters'
    geometry_decimal_precision = svg_geometry_decimal_precision(svg_root)
    decimal_precision_ok = geometry_decimal_precision is not None and geometry_decimal_precision >= 3
    illustrator_profile_compliant = (
        svg_tiny_12 and
        uses_presentation_attributes and
        has_xmp_physical_size
    )
    illustrator_profile_advice = (
        ' Recommended Illustrator export profile: SVG Tiny 1.2, Presentation Attributes, '
        'Include XMP Metadata.'
    )

    title = 'SVG scale warning'
    message_level = 'warning' if scale_status in ['missing', 'non_uniform'] else 'info'

    if source == 'proteus':
        title = 'Proteus SVG detected'
        message = (
            'This SVG appears to have been exported from Proteus Design Suite. '
            'FlatCAM 9 Neo S2 includes experimental Proteus SVG compatibility. '
            'Please verify geometry, dimensions and generated CNC paths before manufacturing.'
        )
    elif source == 'illustrator' and scale_status == 'xmp_fallback':
        title = 'Adobe Illustrator SVG detected'
        message = (
            'This SVG appears to have been exported from Adobe Illustrator. '
            'Physical scale information was recovered from XMP MaxPageSize metadata.'
        )
        if illustrator_profile_compliant:
            message += (
                ' Recommended Illustrator export profile detected: SVG Tiny 1.2, Presentation Attributes, '
                'Include XMP Metadata.'
            )
        else:
            message += illustrator_profile_advice
    elif source == 'illustrator' and scale_status in ['missing', 'non_uniform']:
        title = 'Adobe Illustrator SVG detected - scale warning'
        message = (
            'This SVG appears to have been exported from Adobe Illustrator, but it does not contain reliable '
            'physical size information. FlatCAM will use a fallback scale. The resulting geometry may not match '
            'the intended physical size.'
        )
        if illustrator_profile_compliant is False:
            message += illustrator_profile_advice
    elif source == 'illustrator':
        title = 'Adobe Illustrator SVG detected'
        message = (
            'This SVG appears to have been exported from Adobe Illustrator. '
            'Physical scale information was read from the SVG dimensions.'
        )
        if illustrator_profile_compliant:
            message += (
                ' Recommended Illustrator export profile detected: SVG Tiny 1.2, Presentation Attributes, '
                'Include XMP Metadata.'
            )
        else:
            message += illustrator_profile_advice
    elif category == 'vector' and scale_status in ['missing', 'non_uniform']:
        title = 'Vector SVG scale warning'
        message = (
            'This SVG appears to come from a vector graphics editor, but it does not contain reliable physical '
            'size information. FlatCAM will use a fallback scale. Please verify dimensions before generating CNC jobs.'
        )
    else:
        message = (
            'This SVG does not contain reliable physical size information. FlatCAM will use a fallback scale. '
            'Please verify object dimensions before generating CNC jobs.'
        )

    shell_message = message
    illustrator_shell_profile_compliant = (
        illustrator_profile_compliant and decimal_precision_ok
    )
    if source == 'illustrator':
        if scale_status == 'xmp_fallback':
            scale_message = 'Physical scale recovered from XMP MaxPageSize metadata.'
        elif scale_status == 'reliable':
            scale_message = 'Physical scale read from the SVG dimensions.'
        elif scale_status == 'non_uniform':
            scale_message = 'XMP physical scale is non-uniform; verify the imported dimensions.'
        else:
            scale_message = 'Physical scale metadata is missing; fallback scale is in use.'

        precision_text = 'not detected' if geometry_decimal_precision is None else str(geometry_decimal_precision)
        detected_settings = (
            'Detected settings: SVG Tiny 1.2=%s; Presentation Attributes=%s; XMP Metadata=%s; '
            'observed coordinate decimals=%s (%s, minimum 3, recommended 4).'
        ) % (
            'OK' if svg_tiny_12 else 'NOT DETECTED',
            'OK' if uses_presentation_attributes else 'NOT DETECTED',
            'OK' if has_xmp_physical_size else 'NOT DETECTED',
            precision_text,
            'OK' if decimal_precision_ok else 'LOW'
        )
        recommended_profile = (
            'Recommended Illustrator export profile: SVG Tiny 1.2 with Presentation Attributes; '
            'Include XMP Metadata; Decimals: minimum 3, recommended 4; '
            'Do not preserve Illustrator Editing Capabilities.'
        )

        if illustrator_shell_profile_compliant:
            shell_message = '%s Recommended detectable Illustrator export configuration detected. %s %s' % (
                scale_message, detected_settings,
                'Recommendation: Do not preserve Illustrator Editing Capabilities.'
            )
        else:
            shell_message = '%s Recommended Illustrator export configuration is incomplete. %s %s' % (
                scale_message, detected_settings, recommended_profile
            )

    advisor = {
        'source': source,
        'category': category,
        'scale_status': scale_status,
        'factor': scale_info['factor'],
        'height': scale_info['height'],
        'title': title,
        'message': message,
        'shell_message': shell_message,
        'message_level': message_level,
        'illustrator_profile_compliant': illustrator_profile_compliant,
        'illustrator_shell_profile_compliant': illustrator_shell_profile_compliant,
        'svg_tiny_12': svg_tiny_12,
        'uses_presentation_attributes': uses_presentation_attributes,
        'has_xmp_physical_size': has_xmp_physical_size,
        'geometry_decimal_precision': geometry_decimal_precision,
        'scale_info': scale_info
    }
    return advisor


def path2shapely(path, object_type, res=1.0, units='MM', factor=1.0):
    """
    Converts an svg.path.Path into a Shapely
    Polygon or LinearString.

    :param path:        svg.path.Path instance
    :param object_type:
    :param res:         Resolution (minimum step along path)
    :param units:       FlatCAM units
    :type units:        str
    :param factor:      correction factor due of virtual units
    :type factor:       float
    :return:            Shapely geometry object
    :rtype :            Polygon
    :rtype :            LineString
    """

    points = []
    geometry = []

    rings = []
    closed = False

    for component in path:
        # Line
        if isinstance(component, Line):
            start = component.start
            x, y = factor * start.real, factor * start.imag
            if len(points) == 0 or points[-1] != (x, y):
                points.append((x, y))
            end = component.end
            points.append((factor * end.real, factor * end.imag))
            continue

        # Arc, CubicBezier or QuadraticBezier
        if isinstance(component, Arc) or \
           isinstance(component, CubicBezier) or \
           isinstance(component, QuadraticBezier):

            # How many points to use in the discrete representation.
            length = component.length(res / 10.0)
            # steps = int(length / res + 0.5)
            steps = int(length) * 2

            if units == 'IN':
                steps *= 25

            # solve error when step is below 1,
            # it may cause other problems, but LineString needs at least two points
            # later edit: made the minimum nr of steps to be 10; left it like that to see that steps can be 0
            if steps == 0 or steps < 10:
                steps = 10

            frac = 1.0 / steps

            # print length, steps, frac
            for i in range(steps):
                point = component.point(i * frac)
                x, y = point.real, point.imag
                if len(points) == 0 or points[-1] != (x, y):
                    points.append((factor * x, factor * y))
            end = component.point(1.0)
            points.append((factor * end.real, factor * end.imag))
            continue

        # Move
        if isinstance(component, svg.path.Move):
            if not points:
                continue
            else:
                rings.append(points)
                if closed is False:
                    points = []
                else:
                    closed = False
                    start = component.start
                    x, y = start.real, start.imag
                    points = [(factor * x, factor * y)]
            continue

        closed = False

        # Close
        if isinstance(component, svg.path.Close):
            if not points:
                continue
            else:
                rings.append(points)
                points = []
                closed = True
            continue
        log.warning("I don't know what this is: %s" % str(component))
        continue

    # if there are still points in points then add them to the last ring

    if points:
        rings.append(points)

    try:
        rings = list(MultiLineString(rings).geoms)
    except Exception as e:
        log.debug("ParseSVG.path2shapely() MString --> %s" % str(e))
        return None

    if len(rings) > 0:
        if len(rings) == 1:
            ring_coords = list(rings[0].coords)
            # Polygons are closed and require more than 2 points
            if ring_coords[0] == ring_coords[-1] and len(ring_coords) > 2:
                geo_element = Polygon(ring_coords)
            else:
                geo_element = LineString(ring_coords)
        else:
            try:
                geo_element = Polygon(list(rings[0].coords), [list(line.coords) for line in rings[1:]])
            except Exception:
                coords = []
                for line in rings:
                    coords += list(line.coords)
                try:
                    geo_element = Polygon(coords)
                except Exception:
                    geo_element = LineString(coords)
        geometry.append(geo_element)
    return geometry


def svgrect2shapely(rect, n_points=32, factor=1.0):
    """
    Converts an SVG rect into Shapely geometry.

    :param rect:        Rect Element
    :type rect:         xml.etree.ElementTree.Element
    :param n_points:    number of points to approximate rectangles corners when having rounded corners
    :type n_points:     int
    :param factor:      correction factor due of virtual units
    :type factor:       float
    :return:            shapely.geometry.polygon.LinearRing
    """
    w = svgparselength(rect.get('width'))[0] * factor
    h = svgparselength(rect.get('height'))[0] * factor

    x_obj = rect.get('x')
    if x_obj is not None:
        x = svgparselength(x_obj)[0] * factor
    else:
        x = 0

    y_obj = rect.get('y')
    if y_obj is not None:
        y = svgparselength(y_obj)[0] * factor
    else:
        y = 0

    rxstr = rect.get('rx')
    rystr = rect.get('ry')

    if rxstr is None and rystr is None:  # Sharp corners
        pts = [
            (x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)
        ]

    else:  # Rounded corners
        rx = 0.0 if rxstr is None else svgparselength(rxstr)[0] * factor
        ry = 0.0 if rystr is None else svgparselength(rystr)[0] * factor

        n_points = int(n_points / 4 + 0.5)
        t = np.arange(n_points, dtype=float) / n_points / 4

        x_ = (x + w - rx) + rx * np.cos(2 * np.pi * (t + 0.75))
        y_ = (y + ry) + ry * np.sin(2 * np.pi * (t + 0.75))

        lower_right = [(x_[i], y_[i]) for i in range(n_points)]

        x_ = (x + w - rx) + rx * np.cos(2 * np.pi * t)
        y_ = (y + h - ry) + ry * np.sin(2 * np.pi * t)

        upper_right = [(x_[i], y_[i]) for i in range(n_points)]

        x_ = (x + rx) + rx * np.cos(2 * np.pi * (t + 0.25))
        y_ = (y + h - ry) + ry * np.sin(2 * np.pi * (t + 0.25))

        upper_left = [(x_[i], y_[i]) for i in range(n_points)]

        x_ = (x + rx) + rx * np.cos(2 * np.pi * (t + 0.5))
        y_ = (y + ry) + ry * np.sin(2 * np.pi * (t + 0.5))

        lower_left = [(x_[i], y_[i]) for i in range(n_points)]

        pts = [(x + rx, y), (x - rx + w, y)] + \
            lower_right + \
            [(x + w, y + ry), (x + w, y + h - ry)] + \
            upper_right + \
            [(x + w - rx, y + h), (x + rx, y + h)] + \
            upper_left + \
            [(x, y + h - ry), (x, y + ry)] + \
            lower_left

    # TODO: honor fill="none", visible stroke geometry and stroke join/cap semantics for SVG rectangles.
    return Polygon(pts).buffer(0)
    # return LinearRing(pts)


def svgcircle2shapely(circle, n_points=64, factor=1.0):
    """
    Converts an SVG circle into Shapely geometry.

    :param circle:      Circle Element
    :type circle:       xml.etree.ElementTree.Element
    :param n_points:    circle resolution; nr of points to b e used to approximate a circle
    :type n_points:     int
    :param factor:
    :type factor:       float
    :return:            Shapely representation of the circle.
    :rtype:             shapely.geometry.polygon.LinearRing
    """
    # cx = float(circle.get('cx'))
    # cy = float(circle.get('cy'))
    # r = float(circle.get('r'))
    cx = svgparselength(circle.get('cx'))[0]  # TODO: No units support yet
    cx = cx * factor if cx else cx
    cy = svgparselength(circle.get('cy'))[0]  # TODO: No units support yet
    cy = cy * factor if cy else cy
    r = svgparselength(circle.get('r'))[0]  # TODO: No units support yet
    r = r * factor if r else r

    return Point(cx, cy).buffer(r, resolution=n_points)


def svgellipse2shapely(ellipse, n_points=64, factor=1.0):
    """
    Converts an SVG ellipse into Shapely geometry

    :param ellipse:     Ellipse Element
    :type ellipse:      xml.etree.ElementTree.Element
    :param n_points:    Number of discrete points in output.
    :type n_points:     int
    :param factor:
    :type factor:       float
    :return:            Shapely representation of the ellipse.
    :rtype:             shapely.geometry.polygon.LinearRing
    """

    cx = svgparselength(ellipse.get('cx'))[0]   # TODO: No units support yet
    cx = cx * factor if cx else cx
    cy = svgparselength(ellipse.get('cy'))[0]   # TODO: No units support yet
    cy = cy * factor if cy else cy

    rx = svgparselength(ellipse.get('rx'))[0]   # TODO: No units support yet
    rx = rx * factor if rx else rx
    ry = svgparselength(ellipse.get('ry'))[0]   # TODO: No units support yet
    ry = ry * factor if ry else ry

    t = np.arange(n_points, dtype=float) / n_points
    x = cx + rx * np.cos(2 * np.pi * t)
    y = cy + ry * np.sin(2 * np.pi * t)
    pts = [(x[i], y[i]) for i in range(n_points)]

    return Polygon(pts).buffer(0)
    # return LinearRing(pts)


def svgline2shapely(line, factor=1.0):
    """

    :param line:        Line element
    :type line:         xml.etree.ElementTree.Element
    :param factor:      correction factor due of virtual units
    :type factor:       float
    :return:            Shapely representation on the line.
    :rtype:             shapely.geometry.polygon.LineString
    """

    x1 = svgparselength(line.get('x1'))[0] * factor
    y1 = svgparselength(line.get('y1'))[0] * factor
    x2 = svgparselength(line.get('x2'))[0] * factor
    y2 = svgparselength(line.get('y2'))[0] * factor

    return LineString([(x1, y1), (x2, y2)])


def svggetstyle(node):
    """
    Parse the SVG style attribute into a dictionary.

    :param node:        SVG element
    :return:            Style dictionary
    :rtype:             dict
    """

    style = node.get('style')
    style_dict = {}

    if not style:
        return style_dict

    for css in style.split(';'):
        key, separator, value = css.partition(':')
        if separator:
            style_dict[key.strip()] = value.strip()

    return style_dict


def svgget_effective_style(node, inherited_style=None):
    """
    Build the effective SVG style for a node using a minimal inherited subset.

    :param node:                SVG element
    :param inherited_style:     Parent style dictionary
    :type inherited_style:      dict|None
    :return:                    Effective style dictionary
    :rtype:                     dict
    """

    effective_style = dict(inherited_style) if inherited_style is not None else {}

    stroke = node.get('stroke')
    if stroke is not None:
        effective_style['stroke'] = stroke

    stroke_width = node.get('stroke-width')
    if stroke_width is not None:
        effective_style['stroke-width'] = stroke_width

    stroke_opacity = node.get('stroke-opacity')
    if stroke_opacity is not None:
        effective_style['stroke-opacity'] = stroke_opacity

    fill = node.get('fill')
    if fill is not None:
        effective_style['fill'] = fill

    fill_opacity = node.get('fill-opacity')
    if fill_opacity is not None:
        effective_style['fill-opacity'] = fill_opacity

    fill_rule = node.get('fill-rule')
    if fill_rule is not None:
        effective_style['fill-rule'] = fill_rule

    opacity = node.get('opacity')
    if opacity is not None:
        effective_style['opacity'] = opacity

    display = node.get('display')
    if display is not None:
        effective_style['display'] = display

    visibility = node.get('visibility')
    if visibility is not None:
        effective_style['visibility'] = visibility

    effective_style.update(svggetstyle(node))

    return effective_style


def svg_node_is_visible(effective_style, ancestor_hidden=False):
    """Return False when a node or any CAM ancestor explicitly hides its subtree."""

    if ancestor_hidden:
        return False

    style = effective_style or {}
    display = str(style.get('display', '')).strip().lower()
    visibility = str(style.get('visibility', '')).strip().lower()

    if display == 'none' or visibility in ['hidden', 'collapse']:
        return False

    opacity = style.get('opacity')
    if opacity is not None:
        try:
            opacity_text = str(opacity).strip()
            opacity_value = float(opacity_text.rstrip('%'))
            if opacity_text.endswith('%'):
                opacity_value /= 100.0
            if opacity_value <= 0:
                return False
        except (TypeError, ValueError):
            pass

    return True


def svggetstroke_width(node, factor=1.0, effective_style=None):
    """
    Extract stroke-width from an SVG element.

    :param node:        SVG element
    :param factor:      correction factor due of virtual units
    :type factor:       float
    :return:            Stroke width or None
    :rtype:             float|None
    """

    style_dict = effective_style if effective_style is not None else svgget_effective_style(node)
    stroke = style_dict.get('stroke')

    if stroke is None or stroke.strip().lower() in ['none', 'transparent']:
        return None

    for opacity_name in ['opacity', 'stroke-opacity']:
        opacity = style_dict.get(opacity_name)
        if opacity is None:
            continue
        try:
            opacity_value = float(opacity.strip().rstrip('%'))
            if opacity.strip().endswith('%'):
                opacity_value /= 100.0
            if opacity_value <= 0:
                return None
        except (AttributeError, TypeError, ValueError):
            pass

    stroke_width = style_dict.get('stroke-width')

    if stroke_width is None:
        # SVG Tiny 1.2 initial value for a visible stroke is 1 user unit.
        return 1.0 * factor

    try:
        width = svgparselength(stroke_width)[0]
    except Exception as e:
        log.debug("ParseSVG.svggetstroke_width() --> %s" % str(e))
        return None

    if width is None or width <= 0:
        return None

    return width * factor


def svgstroke2solid(geo, node, factor=1.0, effective_style=None):
    """
    Convert an SVG stroked line geometry into solid geometry.

    :param geo:         Shapely line geometry
    :param node:        SVG element
    :param factor:      correction factor due of virtual units
    :type factor:       float
    :return:            Shapely geometry
    """

    stroke_width = svggetstroke_width(node, factor=factor, effective_style=effective_style)
    if stroke_width is None:
        return geo

    try:
        solid_geo = geo.buffer(stroke_width / 2.0)
    except Exception as e:
        log.debug("ParseSVG.svgstroke2solid() --> %s" % str(e))
        return geo

    if solid_geo.is_empty:
        return geo

    return solid_geo


def svgis_white_color(color):
    """
    Check if an SVG color string is white.

    :param color:       SVG color value
    :return:            True if the color is white
    :rtype:             bool
    """

    if color is None:
        return False

    color = color.strip().lower()
    return color in ['white', '#fff', '#ffffff', 'rgb(255,255,255)', 'rgb(255, 255, 255)']


def svgpath_is_closed_by_coords(path, tolerance=1e-6):
    """
    Check whether an SVG path is closed by matching first and last coordinates.

    :param path:        svg.path.Path instance
    :param tolerance:   Coordinate tolerance
    :type tolerance:    float
    :return:            True if first start and last end match
    :rtype:             bool
    """

    if not path:
        return False

    start = path[0].start
    end = path[-1].end

    return abs(start.real - end.real) <= tolerance and abs(start.imag - end.imag) <= tolerance


def svgextract_circular_paths(node, root=None, factor=1.0, inherited_style=None, circle_tolerance=0.02,
                              ancestor_hidden=False):
    """
    Extract approximately circular closed paths from an SVG node.

    :param node:                SVG element
    :param root:                SVG root element
    :param factor:              correction factor due of virtual units
    :param inherited_style:     Parent style dictionary
    :param circle_tolerance:    Max allowed difference between rx and ry
    :type circle_tolerance:     float
    :return:                    List of circular path descriptors
    :rtype:                     list
    """

    if root is None:
        root = node

    # Illustrator XMP metadata may contain comments and processing instructions
    # whose lxml tag is not a string. They cannot contain CAM geometry.
    if not isinstance(node.tag, str):
        return []

    kind = re.search('(?:\{.*\})?(.*)$', node.tag).group(1)
    if kind in ['metadata', 'style', 'title', 'desc', 'defs', 'symbol']:
        return []

    effective_style = svgget_effective_style(node, inherited_style=inherited_style)
    if svg_node_is_visible(effective_style, ancestor_hidden=ancestor_hidden) is False:
        return []

    circles = []

    if len(node) > 0:
        for child in node:
            circles += svgextract_circular_paths(
                child, root=root, factor=factor, inherited_style=effective_style,
                circle_tolerance=circle_tolerance, ancestor_hidden=False
            )
        return circles

    if kind != 'path':
        return circles

    path_data = node.get('d')
    if not path_data:
        return circles

    try:
        parsed_path = parse_path(path_data)
    except Exception as e:
        log.debug("ParseSVG.svgextract_circular_paths() parse_path --> %s" % str(e))
        return circles

    if not svgpath_is_closed_by_coords(parsed_path):
        return circles

    geos = path2shapely(parsed_path, object_type='geometry', factor=factor) or []
    for geo in geos:
        if geo is None or geo.is_empty:
            continue

        minx, miny, maxx, maxy = geo.bounds
        rx = (maxx - minx) / 2.0
        ry = (maxy - miny) / 2.0

        if rx <= 0 or ry <= 0 or abs(rx - ry) > circle_tolerance:
            continue

        cx = (minx + maxx) / 2.0
        cy = (miny + maxy) / 2.0

        circles.append({
            'center': (cx, cy),
            'radius': (rx + ry) / 2.0,
            'rx': rx,
            'ry': ry,
            'bounds': geo.bounds,
            'fill': effective_style.get('fill'),
            'stroke': effective_style.get('stroke'),
            'style': effective_style,
            'geometry': geo
        })

    return circles


def extract_proteus_svg_drills(svg_filename, center_tolerance=0.02, diameter_tolerance=0.01,
                               circle_tolerance=0.02):
    """
    Experimental Proteus SVG Drill Extraction MVP.

    Detect white circular paths inside larger concentric circular pads and
    return Excellon-compatible tools/drills data.

    :param svg_filename:        SVG filename
    :type svg_filename:         str
    :param center_tolerance:    Max distance between concentric centers
    :type center_tolerance:     float
    :param diameter_tolerance:  Grouping tolerance for drill diameter
    :type diameter_tolerance:   float
    :param circle_tolerance:    Max allowed difference between rx and ry
    :type circle_tolerance:     float
    :return:                    Detection result
    :rtype:                     dict
    """

    svg_tree = ET.parse(svg_filename)
    svg_root = svg_tree.getroot()
    factor = svgparse_viewbox(svg_root)

    circles = svgextract_circular_paths(svg_root, root=svg_root, factor=factor, circle_tolerance=circle_tolerance)
    hole_candidates = []
    pad_candidates = []

    for circle in circles:
        fill = circle.get('fill')
        stroke = circle.get('stroke')
        is_white_fill = svgis_white_color(fill)
        # Illustrator omits stroke when a filled path has no visible outline.
        is_stroke_none = stroke is None or not stroke.strip() or stroke.strip().lower() == 'none'

        if is_white_fill and is_stroke_none:
            hole_candidates.append(circle)
        else:
            pad_candidates.append(circle)

    drill_candidates = []
    used_centers = set()

    for hole in hole_candidates:
        hx, hy = hole['center']
        matching_pads = []

        for pad in pad_candidates:
            px, py = pad['center']
            if abs(px - hx) > center_tolerance or abs(py - hy) > center_tolerance:
                continue
            if pad['radius'] <= hole['radius']:
                continue
            matching_pads.append(pad)

        if not matching_pads:
            continue

        matching_pads.sort(key=lambda c: c['radius'])
        drill_dia = 2.0 * hole['radius']
        center_key = (round(hx, 4), round(hy, 4), round(drill_dia, 4))
        if center_key in used_centers:
            continue
        used_centers.add(center_key)

        drill_candidates.append({
            'center': (hx, hy),
            'diameter': drill_dia,
            'hole_radius': hole['radius'],
            'pad_radius': matching_pads[0]['radius'],
            'hole': hole,
            'pad': matching_pads[0]
        })

    tools = {}
    for drill in drill_candidates:
        drill_point = Point(drill['center'])
        drill_dia = drill['diameter']
        tool_id = None

        for tid, tool in tools.items():
            if abs(tool['tooldia'] - drill_dia) <= diameter_tolerance:
                tool_id = tid
                break

        if tool_id is None:
            tool_id = max(tools.keys()) + 1 if tools else 1
            tools[tool_id] = {
                'tooldia': drill_dia,
                'drills': [],
                'slots': [],
                'solid_geometry': []
            }

        tools[tool_id]['drills'].append(drill_point)
        tools[tool_id]['solid_geometry'].append(drill_point.buffer(tools[tool_id]['tooldia'] / 2.0))

    return {
        'tools': tools,
        'drills': drill_candidates,
        'circles': circles,
        'hole_candidates': hole_candidates,
        'pad_candidates': pad_candidates,
        'factor': factor
    }


def extract_illustrator_svg_drills(svg_filename, min_diameter=0.2, max_diameter=6.0,
                                   diameter_tolerance=0.01, circle_tolerance=0.002,
                                   relative_circle_tolerance=0.005):
    """Extract strict white-circle drill markers from an Adobe Illustrator SVG."""

    svg_tree = ET.parse(svg_filename)
    svg_root = svg_tree.getroot()
    advisor = svg_source_advisor(svg_filename)
    scale_info = svg_physical_scale(svg_root)
    factor = scale_info['factor']
    drill_candidates = []
    rejected = []

    result = {
        'tools': {},
        'drills': drill_candidates,
        'rejected': rejected,
        'factor': factor,
        'source': advisor.get('source'),
        'scale_status': scale_info.get('scale_status')
    }

    # Keep this experimental detector isolated from Proteus and generic SVG files.
    if advisor.get('source') != 'illustrator':
        result['disabled_reason'] = 'source_not_illustrator'
        return result
    if scale_info.get('scale_status') in ['missing', 'non_uniform']:
        result['disabled_reason'] = 'unreliable_physical_scale'
        return result

    def opacity_is_visible(style, names):
        for opacity_name in names:
            opacity = style.get(opacity_name)
            if opacity is None:
                continue
            try:
                opacity_value = float(opacity.strip().rstrip('%'))
                if opacity.strip().endswith('%'):
                    opacity_value /= 100.0
                if opacity_value <= 0:
                    return False
            except (AttributeError, TypeError, ValueError):
                pass
        return True

    def dark_stroke_is_visible(node, style):
        stroke = style.get('stroke')
        if stroke is None or svggetstroke_width(node, factor=factor, effective_style=style) is None:
            return False

        stroke_as_fill = {
            'fill': stroke,
            'opacity': style.get('opacity'),
            'fill-opacity': style.get('stroke-opacity')
        }
        return svgfill_is_dark_visible(stroke_as_fill)

    def apply_transform_chain(geometry, transform_chain):
        transformed = [geometry]
        # A child transform is applied before each enclosing parent transform.
        for transform_list in transform_chain[::-1]:
            transformed = svg_apply_transform(transformed, transform_list, factor=factor)
        return transformed[0]

    def visit(node, inherited_style=None, transform_chain=None, ancestor_hidden=False):
        if not isinstance(node.tag, str):
            return

        kind = re.search('(?:\{.*\})?(.*)$', node.tag).group(1)
        if kind in ['metadata', 'defs', 'style', 'symbol']:
            return

        effective_style = svgget_effective_style(node, inherited_style=inherited_style)
        if svg_node_is_visible(effective_style, ancestor_hidden=ancestor_hidden) is False:
            return

        current_transforms = list(transform_chain) if transform_chain is not None else []
        if node.get('transform'):
            current_transforms.append(parse_svg_transform(node.get('transform')))

        if kind == 'circle':
            candidate_info = {'element': node}
            fill = effective_style.get('fill', '#000000')
            if not svgis_white_color(fill) or not opacity_is_visible(
                    effective_style, ['opacity', 'fill-opacity']):
                candidate_info['reason'] = 'fill_not_visible_white'
                rejected.append(candidate_info)
            elif not dark_stroke_is_visible(node, effective_style):
                candidate_info['reason'] = 'stroke_not_visible_dark'
                rejected.append(candidate_info)
            else:
                circle_geometry = svgcircle2shapely(node, n_points=64, factor=factor)
                center_geometry = Point(circle_geometry.centroid.x, circle_geometry.centroid.y)
                circle_geometry = apply_transform_chain(circle_geometry, current_transforms)
                center_geometry = apply_transform_chain(center_geometry, current_transforms)

                distances = [
                    center_geometry.distance(Point(x, y))
                    for x, y in list(circle_geometry.exterior.coords)[:-1]
                ]
                radius = sum(distances) / len(distances) if distances else 0.0
                radial_tolerance = max(circle_tolerance, radius * relative_circle_tolerance)
                diameter = radius * 2.0

                candidate_info.update({
                    'center': (center_geometry.x, center_geometry.y),
                    'diameter': diameter,
                    'radius_spread': max(distances) - min(distances) if distances else 0.0
                })

                if not distances or candidate_info['radius_spread'] > radial_tolerance:
                    candidate_info['reason'] = 'not_circular_after_transform'
                    rejected.append(candidate_info)
                elif diameter < min_diameter - 1e-6 or diameter > max_diameter + 1e-6:
                    candidate_info['reason'] = 'diameter_out_of_range'
                    rejected.append(candidate_info)
                else:
                    candidate_info['stroke_width'] = svggetstroke_width(
                        node, factor=factor, effective_style=effective_style
                    )
                    drill_candidates.append(candidate_info)

        elif kind == 'ellipse':
            rejected.append({
                'element': node,
                'reason': 'element_not_circle'
            })

        for child in node:
            visit(
                child, inherited_style=effective_style, transform_chain=current_transforms,
                ancestor_hidden=False
            )

    visit(svg_root)

    tools = {}
    for drill in drill_candidates:
        drill_point = Point(drill['center'])
        drill_diameter = drill['diameter']
        tool_id = None

        for tid, tool in tools.items():
            if abs(tool['tooldia'] - drill_diameter) <= diameter_tolerance:
                tool_id = tid
                break

        if tool_id is None:
            tool_id = max(tools.keys()) + 1 if tools else 1
            tools[tool_id] = {
                'tooldia': drill_diameter,
                'drills': [],
                'slots': [],
                'solid_geometry': []
            }

        tools[tool_id]['drills'].append(drill_point)
        tools[tool_id]['solid_geometry'].append(drill_point.buffer(tools[tool_id]['tooldia'] / 2.0))

    result['tools'] = tools
    return result


def svg_detect_overlapping_compound_paths(svg_filename):
    """Return named SVG layers whose filled compound paths need advanced winding support."""

    svg_tree = ET.parse(svg_filename)
    svg_root = svg_tree.getroot()
    factor = svg_physical_scale(svg_root)['factor']
    detected_layers = []
    detected_keys = set()

    def layer_name(node, inherited_name):
        if re.search('(?:\{.*\})?(.*)$', node.tag).group(1) != 'g':
            return inherited_name

        for attribute in [
            'id', 'name', 'data-name', 'label',
            '{http://www.inkscape.org/namespaces/inkscape}label'
        ]:
            value = node.get(attribute)
            if value and value.strip():
                return value.strip()
        return inherited_name

    def closed_subpath_polygon(subpath):
        explicitly_closed = any(isinstance(component, svg.path.Close) for component in subpath)
        if explicitly_closed is False and svgpath_is_closed_by_coords(subpath) is False:
            return None

        subpath_geometries = path2shapely(
            subpath, object_type='geometry', factor=factor
        ) or []
        if not subpath_geometries:
            return None

        geometry = subpath_geometries[0]
        if isinstance(geometry, Polygon):
            return geometry
        if isinstance(geometry, LineString):
            coordinates = list(geometry.coords)
            if len(coordinates) < 3:
                return None
            if coordinates[0] != coordinates[-1]:
                coordinates.append(coordinates[0])
            polygon = Polygon(coordinates)
            return polygon if polygon.is_empty is False else None
        return None

    def visit(node, inherited_style=None, inherited_layer=None, ancestor_hidden=False):
        if not isinstance(node.tag, str):
            return

        kind = re.search('(?:\{.*\})?(.*)$', node.tag).group(1)
        if kind in ['metadata', 'defs', 'style', 'symbol']:
            return

        effective_style = svgget_effective_style(node, inherited_style=inherited_style)
        if svg_node_is_visible(effective_style, ancestor_hidden=ancestor_hidden) is False:
            return

        current_layer = layer_name(node, inherited_layer)
        if kind == 'path' and svgfill_is_dark_visible(effective_style):
            try:
                parsed_path = parse_path(node.get('d') or '')
                subpaths = svgpath_split_subpaths(parsed_path)
                closed_polygons = [closed_subpath_polygon(subpath) for subpath in subpaths]
                closed_polygons = [polygon for polygon in closed_polygons if polygon is not None]

                if len(closed_polygons) > 1:
                    combined_geometries = path2shapely(
                        parsed_path, object_type='geometry', factor=factor
                    ) or []
                    invalid_combination = any(
                        geometry is not None and geometry.is_empty is False and geometry.is_valid is False
                        for geometry in combined_geometries
                    )

                    exterior = closed_polygons[0]
                    partial_overlap = any(
                        exterior.intersects(candidate) and
                        exterior.intersection(candidate).area > 1e-9 and
                        candidate.difference(exterior).area > 1e-9
                        for candidate in closed_polygons[1:]
                    )

                    if invalid_combination or partial_overlap:
                        warning_layer = current_layer or 'Unknown Layer'
                        warning_key = warning_layer
                        if warning_key not in detected_keys:
                            detected_keys.add(warning_key)
                            detected_layers.append(warning_layer)
            except Exception as e:
                log.debug("SVG overlapping Compound Path inspection skipped --> %s" % str(e))

        for child in node:
            visit(
                child, inherited_style=effective_style, inherited_layer=current_layer,
                ancestor_hidden=False
            )

    visit(svg_root)
    return detected_layers


def svgpolyline2shapely(polyline, factor=1.0):
    """

    :param polyline:    Polyline element
    :type polyline:     xml.etree.ElementTree.Element
    :param factor:      correction factor due of virtual units
    :type factor:       float
    :return:            Shapely representation of the PolyLine
    :rtype:             shapely.geometry.polygon.LineString
    """

    ptliststr = polyline.get('points')
    points = parse_svg_point_list(ptliststr, factor)

    return LineString(points)


def svgfill_is_dark_visible(effective_style):
    """Return True for a visible dark fill that is useful as CAM geometry."""

    style = effective_style or {}
    fill = style.get('fill', '#000000').strip().lower()

    if fill in ['none', 'transparent'] or svgis_white_color(fill):
        return False

    for opacity_name in ['opacity', 'fill-opacity']:
        opacity = style.get(opacity_name)
        if opacity is None:
            continue
        try:
            opacity_value = float(opacity.strip().rstrip('%'))
            if opacity.strip().endswith('%'):
                opacity_value /= 100.0
            if opacity_value <= 0:
                return False
        except (AttributeError, TypeError, ValueError):
            pass

    # Keep this MVP conservative: only verified dark RGB/hex fills become CAM solids.
    is_dark_fill = fill == 'black'
    if fill.startswith('#'):
        hex_color = fill[1:]
        if len(hex_color) == 3:
            hex_color = ''.join(channel * 2 for channel in hex_color)
        if len(hex_color) == 6:
            try:
                red, green, blue = [int(hex_color[idx:idx + 2], 16) for idx in (0, 2, 4)]
                is_dark_fill = (0.2126 * red + 0.7152 * green + 0.0722 * blue) < 128
            except ValueError:
                is_dark_fill = False
    elif fill.startswith('rgb(') and fill.endswith(')'):
        try:
            channels = [int(channel.strip()) for channel in fill[4:-1].split(',')]
            if len(channels) == 3:
                is_dark_fill = (0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]) < 128
        except ValueError:
            is_dark_fill = False

    return is_dark_fill


def svgbasicshape_fill_stroke(shape, node, factor=1.0, effective_style=None):
    """Materialize the visible CAM fill and stroke of a basic closed SVG shape."""

    style = effective_style if effective_style is not None else svgget_effective_style(node)
    fill_geometry = shape if svgfill_is_dark_visible(style) else None
    stroke_width = svggetstroke_width(node, factor=factor, effective_style=style)

    stroke_geometry = None
    if stroke_width is not None:
        try:
            stroke_geometry = shape.boundary.buffer(stroke_width / 2.0)
            if stroke_geometry.is_empty or stroke_geometry.is_valid is False:
                stroke_geometry = None
        except Exception as e:
            log.debug("SVG basic shape stroke skipped --> %s" % str(e))

    if fill_geometry is not None and stroke_geometry is not None:
        try:
            fill_and_stroke = unary_union([fill_geometry, stroke_geometry])
            if fill_and_stroke.is_empty is False and fill_and_stroke.is_valid:
                return [fill_and_stroke]
        except Exception as e:
            log.debug("SVG basic shape fill/stroke union skipped --> %s" % str(e))
        return [fill_geometry]

    if fill_geometry is not None:
        return [fill_geometry]
    if stroke_geometry is not None:
        return [stroke_geometry]
    return []


def svgpolyline_fill2shapely(polyline, factor=1.0, effective_style=None):
    """Create a safe virtual fill for an open SVG polyline."""

    style = effective_style if effective_style is not None else svgget_effective_style(polyline)
    if svgfill_is_dark_visible(style) is False:
        return None

    points = parse_svg_point_list(polyline.get('points'), factor)
    if len(points) < 3 or points[0] == points[-1]:
        return None

    virtual_ring = LineString(points + [points[0]])
    if virtual_ring.is_ring is False or virtual_ring.is_simple is False:
        log.warning("Open SVG polyline fill skipped: virtual closure is self-intersecting or ambiguous.")
        return None

    fill_geometry = Polygon(points)
    if fill_geometry.is_empty or fill_geometry.is_valid is False or fill_geometry.area <= 0:
        log.warning("Open SVG polyline fill skipped: virtual Polygon is invalid or empty.")
        return None

    return fill_geometry


def svgpath_split_subpaths(path):
    """Split an svg.path.Path into independent subpaths."""

    subpaths = []
    components = []

    for component in path:
        if isinstance(component, svg.path.Move) and components:
            subpaths.append(svg.path.Path(*components))
            components = []
        components.append(component)

    if components:
        subpaths.append(svg.path.Path(*components))

    return subpaths


def svgcompound_fillrule2shapely(path, node, object_type, units='MM', factor=1.0, effective_style=None):
    """Resolve partially overlapping closed subpaths using SVG fill-rule semantics for Geometry imports."""

    if object_type != 'geometry':
        return None

    subpaths = svgpath_split_subpaths(path)
    if len(subpaths) < 2:
        return None

    rings = []
    polygons = []
    windings = []

    for subpath in subpaths:
        explicitly_closed = any(isinstance(component, svg.path.Close) for component in subpath)
        if explicitly_closed is False and svgpath_is_closed_by_coords(subpath) is False:
            return None

        subpath_geo = path2shapely(subpath, object_type, units=units, factor=factor) or []
        if len(subpath_geo) != 1 or isinstance(subpath_geo[0], (Polygon, LineString)) is False:
            return None

        geometry = subpath_geo[0]
        coords = list(geometry.exterior.coords) if isinstance(geometry, Polygon) else list(geometry.coords)
        if len(coords) < 3:
            return None
        if coords[0] != coords[-1]:
            coords.append(coords[0])

        signed_area = sum(
            coords[index][0] * coords[index + 1][1] - coords[index + 1][0] * coords[index][1]
            for index in range(len(coords) - 1)
        ) / 2.0
        if abs(signed_area) <= 1e-12:
            return None

        ring_polygon = Polygon(coords)
        if ring_polygon.is_empty or ring_polygon.is_valid is False or ring_polygon.area <= 0:
            return None

        rings.append(LineString(coords))
        polygons.append(ring_polygon)
        windings.append(1 if signed_area > 0 else -1)

    area_tolerance = max(1e-12, max(polygon.area for polygon in polygons) * 1e-9)
    partial_overlap = False
    for first_index, first_polygon in enumerate(polygons):
        for second_polygon in polygons[first_index + 1:]:
            try:
                intersection_area = first_polygon.intersection(second_polygon).area
            except Exception:
                return None
            if intersection_area > area_tolerance and not first_polygon.covers(second_polygon) and \
                    not second_polygon.covers(first_polygon):
                partial_overlap = True
                break
        if partial_overlap:
            break

    if partial_overlap is False:
        return None

    try:
        atomic_regions = list(polygonize(unary_union(rings)))
        if not atomic_regions:
            log.warning("Advanced SVG Compound Path fill-rule fallback: polygonize returned no regions.")
            return None

        style = effective_style if effective_style is not None else svgget_effective_style(node)
        fill_rule = str(style.get('fill-rule', 'nonzero')).strip().lower()
        if fill_rule not in ['evenodd', 'nonzero']:
            fill_rule = 'nonzero'

        filled_regions = []
        for region in atomic_regions:
            sample = region.representative_point()
            containing = [index for index, polygon in enumerate(polygons) if polygon.covers(sample)]

            if fill_rule == 'evenodd':
                is_filled = len(containing) % 2 == 1
            else:
                winding_number = sum(windings[index] for index in containing)
                is_filled = winding_number != 0

            if is_filled:
                filled_regions.append(region)

        if not filled_regions:
            log.warning("Advanced SVG Compound Path fill-rule fallback: no filled regions.")
            return None

        fill_geometry = unary_union(filled_regions)
        if fill_geometry.is_valid is False:
            repaired_geometry = fill_geometry.buffer(0)
            if repaired_geometry.is_empty or repaired_geometry.is_valid is False:
                log.warning("Advanced SVG Compound Path fill-rule fallback: invalid result.")
                return None
            fill_geometry = repaired_geometry

        if fill_geometry.is_empty or fill_geometry.geom_type not in ['Polygon', 'MultiPolygon']:
            log.warning("Advanced SVG Compound Path fill-rule fallback: unsupported result.")
            return None

        return [fill_geometry]
    except Exception as e:
        log.warning("Advanced SVG Compound Path fill-rule fallback: %s" % str(e))
        return None


def svgclosedpath_stroke2solid(path, node, object_type, units='MM', factor=1.0, effective_style=None):
    """Buffer closed path subpaths while leaving open path behavior untouched."""

    stroke_width = svggetstroke_width(node, factor=factor, effective_style=effective_style)
    solid_strokes = []
    closed_subpaths = 0

    for subpath in svgpath_split_subpaths(path):
        subpath_geo = path2shapely(subpath, object_type, units=units, factor=factor) or []
        explicitly_closed = any(isinstance(component, svg.path.Close) for component in subpath)
        for geometry in subpath_geo:
            if isinstance(geometry, Polygon):
                outline = LineString(geometry.exterior.coords)
            elif explicitly_closed and isinstance(geometry, LineString):
                outline_coords = list(geometry.coords)
                if outline_coords and outline_coords[0] != outline_coords[-1]:
                    outline_coords.append(outline_coords[0])
                outline = LineString(outline_coords)
            else:
                continue

            closed_subpaths += 1
            if stroke_width is None:
                continue

            solid_stroke = outline.buffer(stroke_width / 2.0)
            if solid_stroke.is_empty is False and solid_stroke.is_valid:
                solid_strokes.append(solid_stroke)

    return solid_strokes, closed_subpaths


def svgpolygon2shapely(polygon, n_points=64, factor=1.0):
    """
    Convert a SVG polygon to a Shapely Polygon.

    :param polygon:
    :type polygon:
    :param n_points:    circle resolution; nr of points to b e used to approximate a circle
    :type n_points:     int
    :param factor:      correction factor due of virtual units
    :type factor:       float
    :return:            Shapely Polygon
    """

    ptliststr = polygon.get('points')
    points = parse_svg_point_list(ptliststr, factor)

    return Polygon(points).buffer(0, resolution=n_points)
    # return LinearRing(points)


def svg_apply_transform(geometry, transform_list, factor=1.0):
    """Apply parsed SVG transforms to geometry already scaled by factor."""

    transformed = geometry

    # Preserve SVG transform-list composition by applying items in reverse order.
    for transform_item in transform_list[::-1]:
        transform_kind = transform_item[0]

        if transform_kind == 'translate':
            transformed = [
                translate(item, transform_item[1] * factor, transform_item[2] * factor)
                for item in transformed
            ]
        elif transform_kind == 'scale':
            transformed = [
                scale(item, transform_item[1], transform_item[2], origin=(0, 0))
                for item in transformed
            ]
        elif transform_kind == 'rotate':
            transformed = [
                rotate(
                    item,
                    transform_item[1],
                    origin=(transform_item[2] * factor, transform_item[3] * factor)
                )
                for item in transformed
            ]
        elif transform_kind == 'skew':
            transformed = [
                skew(item, transform_item[1], transform_item[2], origin=(0, 0))
                for item in transformed
            ]
        elif transform_kind == 'matrix':
            svg_a, svg_b, svg_c, svg_d, svg_e, svg_f = transform_item[1:]
            shapely_matrix = [
                svg_a, svg_c, svg_b, svg_d,
                svg_e * factor, svg_f * factor
            ]
            transformed = [affine_transform(item, shapely_matrix) for item in transformed]
        else:
            raise Exception('Unknown transformation: %s' % str(transform_item))

    return transformed


def getsvggeo(node, object_type, root=None, units='MM', res=64, factor=1.0, inherited_style=None,
              ancestor_hidden=False, allow_definitions=False):
    """
    Extracts and flattens all geometry from an SVG node
    into a list of Shapely geometry.

    :param node:        xml.etree.ElementTree.Element
    :param object_type:
    :param root:
    :param units:       FlatCAM units
    :param res:         resolution to be used for circles buffering
    :param factor:      correction factor due of virtual units
    :type factor:       float
    :param inherited_style: inherited SVG style values
    :type inherited_style:  dict|None
    :param ancestor_hidden: True when an enclosing CAM subtree is hidden
    :type ancestor_hidden:  bool
    :param allow_definitions: True only for an explicit reference such as use
    :type allow_definitions: bool
    :return:            List of Shapely geometry
    :rtype:             list
    """
    if root is None:
        root = node

    if not isinstance(node.tag, str):
        return None

    kind = re.search('(?:\{.*\})?(.*)$', node.tag).group(1)
    if kind in ['metadata', 'style', 'title', 'desc']:
        return None
    if kind in ['defs', 'symbol'] and allow_definitions is False:
        return None

    effective_style = svgget_effective_style(node, inherited_style=inherited_style)
    if svg_node_is_visible(effective_style, ancestor_hidden=ancestor_hidden) is False:
        return []

    geo = []

    # Recurse
    if len(node) > 0:
        for child in node:
            subgeo = getsvggeo(
                child, object_type, root=root, units=units, res=res, factor=factor,
                inherited_style=effective_style, ancestor_hidden=False,
                allow_definitions=allow_definitions
            )
            if subgeo is not None:
                geo += subgeo
    # Parse
    elif kind == 'path':
        log.debug("***PATH***")
        P = parse_path(node.get('d'))
        path_geo = path2shapely(P, object_type, units=units, factor=factor) or []

        if svgfill_is_dark_visible(effective_style):
            advanced_path_geo = svgcompound_fillrule2shapely(
                P, node, object_type, units=units, factor=factor, effective_style=effective_style
            )
            if advanced_path_geo is not None:
                path_geo = advanced_path_geo

            # Preserve the established fill and safely add visible closed-path strokes.
            geo = path_geo
            stroke_geo, closed_subpaths = svgclosedpath_stroke2solid(
                P, node, object_type, units=units, factor=factor, effective_style=effective_style
            )
            if stroke_geo:
                try:
                    fill_and_stroke = unary_union(path_geo + stroke_geo)
                    if fill_and_stroke.is_empty is False and fill_and_stroke.is_valid and \
                            fill_and_stroke.geom_type in ['Polygon', 'MultiPolygon']:
                        geo = [fill_and_stroke]
                    else:
                        log.warning("SVG path fill/stroke union skipped: result is invalid or empty.")
                except Exception as e:
                    log.debug("SVG path fill/stroke union skipped --> %s" % str(e))
        else:
            stroke_geo, closed_subpaths = svgclosedpath_stroke2solid(
                P, node, object_type, units=units, factor=factor, effective_style=effective_style
            )
            # Closed paths without a CAM fill produce only their visible stroke.
            # Open paths remain unchanged in this MVP.
            geo = stroke_geo if closed_subpaths else path_geo

    elif kind == 'rect':
        ## log.debug("***RECT***")
        R = svgrect2shapely(node, n_points=res, factor=factor)
        geo = svgbasicshape_fill_stroke(
            R, node, factor=factor, effective_style=effective_style
        )

    elif kind == 'circle':
        log.debug("***CIRCLE***")
        C = svgcircle2shapely(node, n_points=res, factor=factor)
        geo = svgbasicshape_fill_stroke(
            C, node, factor=factor, effective_style=effective_style
        )

    elif kind == 'ellipse':
        log.debug("***ELLIPSE***")
        E = svgellipse2shapely(node, n_points=res, factor=factor)
        geo = svgbasicshape_fill_stroke(
            E, node, factor=factor, effective_style=effective_style
        )

    elif kind == 'polygon':
        log.debug("***POLYGON***")
        poly = svgpolygon2shapely(node, n_points=res, factor=factor)
        geo = [poly]

    elif kind == 'line':
        log.debug("***LINE***")
        line = svgline2shapely(node, factor=factor)
        line = svgstroke2solid(line, node, factor=factor, effective_style=effective_style)
        geo = [line]

    elif kind == 'polyline':
        log.debug("***POLYLINE***")
        pline = svgpolyline2shapely(node, factor=factor)
        pline = svgstroke2solid(pline, node, factor=factor, effective_style=effective_style)
        geo = [pline]
        fill_geo = svgpolyline_fill2shapely(node, factor=factor, effective_style=effective_style)
        if fill_geo is not None:
            geo.append(fill_geo)

    elif kind == 'use':
        log.debug('***USE***')
        # href= is the preferred name for this[1], but inkscape still generates xlink:href=.
        # [1] https://developer.mozilla.org/en-US/docs/Web/SVG/Element/use#Attributes
        href = node.attrib['href'] if 'href' in node.attrib else node.attrib['{http://www.w3.org/1999/xlink}href']
        ref = root.find(".//*[@id='%s']" % href.replace('#', ''))
        if ref is not None:
            geo = getsvggeo(
                ref, object_type, root=root, units=units, res=res, factor=factor,
                inherited_style=effective_style, ancestor_hidden=False, allow_definitions=True
            )

    else:
        log.warning("Unknown kind: " + kind)
        geo = None

    # ignore transformation for unknown kind
    if geo is not None:
        # Transformations
        if 'transform' in node.attrib:
            trstr = node.get('transform')
            trlist = parse_svg_transform(trstr)
            geo = svg_apply_transform(geo, trlist, factor=factor)

    return geo


def getsvgtext(node, object_type, units='MM', inherited_style=None, ancestor_hidden=False):
    """
    Extracts and flattens all geometry from an SVG node
    into a list of Shapely geometry.

    :param node:        xml.etree.ElementTree.Element
    :param object_type:
    :param units:       FlatCAM units
    :param inherited_style: inherited SVG style values
    :type inherited_style: dict|None
    :param ancestor_hidden: True when an enclosing CAM subtree is hidden
    :type ancestor_hidden: bool
    :return:            List of Shapely geometry
    :rtype:             list
    """
    if not isinstance(node.tag, str):
        return None

    kind = re.search('(?:\{.*\})?(.*)$', node.tag).group(1)
    if kind in ['metadata', 'style', 'title', 'desc', 'defs', 'symbol']:
        return None

    effective_style = svgget_effective_style(node, inherited_style=inherited_style)
    if svg_node_is_visible(effective_style, ancestor_hidden=ancestor_hidden) is False:
        return []

    geo = []

    # Recurse
    if len(node) > 0:
        for child in node:
            subgeo = getsvgtext(
                child, object_type, units=units, inherited_style=effective_style,
                ancestor_hidden=False
            )
            if subgeo is not None:
                geo += subgeo

    # Parse
    elif kind == 'tspan':
        current_attrib = node.attrib
        txt = node.text
        style_dict = {}
        parrent_attrib = node.getparent().attrib
        style = parrent_attrib['style']

        try:
            style_list = style.split(';')
            for css in style_list:
                style_dict[css.rpartition(':')[0]] = css.rpartition(':')[-1]

            pos_x = float(current_attrib['x'])
            pos_y = float(current_attrib['y'])

            # should have used the instance from FlatCAMApp.App but how? without reworking everything ...
            pf = ParseFont()
            pf.get_fonts_by_types()
            font_name = style_dict['font-family'].replace("'", '')

            if style_dict['font-style'] == 'italic' and style_dict['font-weight'] == 'bold':
                font_type = 'bi'
            elif style_dict['font-weight'] == 'bold':
                font_type = 'bold'
            elif style_dict['font-style'] == 'italic':
                font_type = 'italic'
            else:
                font_type = 'regular'

            # value of 2.2 should have been 2.83 (conversion value from pixels to points)
            # but the dimensions from Inkscape did not corelate with the ones after importing in FlatCAM
            # so I adjusted this
            font_size = svgparselength(style_dict['font-size'])[0] * 2.2
            geo = [pf.font_to_geometry(txt,
                                       font_name=font_name,
                                       font_size=font_size,
                                       font_type=font_type,
                                       units=units,
                                       coordx=pos_x,
                                       coordy=pos_y)
                   ]

            geo = [(scale(g, 1.0, -1.0)) for g in geo]
        except Exception as e:
            log.debug(str(e))
    else:
        geo = None

    # ignore transformation for unknown kind
    if geo is not None:
        # Transformations
        if 'transform' in node.attrib:
            trstr = node.get('transform')
            trlist = parse_svg_transform(trstr)
            # Text geometry is not globally scaled by this legacy function.
            geo = svg_apply_transform(geo, trlist, factor=1.0)

    return geo


def parse_svg_point_list(ptliststr, factor):
    """
    Returns a list of coordinate pairs extracted from the "points"
    attribute in SVG polygons and polyline's.

    :param ptliststr:       "points" attribute string in polygon or polyline.
    :param factor:          correction factor due of virtual units
    :type factor:           float
    :return:                List of tuples with coordinates.
    """

    integer_re_str = r'[+-]?[0-9]+'
    number_re_str = r'(?:[+-]?[0-9]*\.[0-9]+(?:[Ee]' + integer_re_str + ')?' + r')|' + \
                    r'(?:' + integer_re_str + r'(?:[Ee]' + integer_re_str + r')?)'
    values = [float(match.group(0)) for match in re.finditer(number_re_str, ptliststr)]

    if len(values) % 2 != 0:
        log.warning("Incomplete coordinates.")

    pairs = [
        (factor * values[i], factor * values[i + 1])
        for i in range(0, len(values) - 1, 2)
    ]

    return pairs


def parse_svg_transform(trstr):
    """
    Parses an SVG transform string into a list
    of transform names and their parameters.

    Possible transformations are:

    * Translate: translate(<tx> [<ty>]), which specifies
      a translation by tx and ty. If <ty> is not provided,
      it is assumed to be zero. Result is
      ['translate', tx, ty]

    * Scale: scale(<sx> [<sy>]), which specifies a scale operation
      by sx and sy. If <sy> is not provided, it is assumed to be
      equal to <sx>. Result is: ['scale', sx, sy]

    * Rotate: rotate(<rotate-angle> [<cx> <cy>]), which specifies
      a rotation by <rotate-angle> degrees about a given point.
      If optional parameters <cx> and <cy> are not supplied,
      the rotate is about the origin of the current user coordinate
      system. Result is: ['rotate', rotate-angle, cx, cy]

    * Skew: skewX(<skew-angle>), which specifies a skew
      transformation along the x-axis. skewY(<skew-angle>), which
      specifies a skew transformation along the y-axis.
      Result is ['skew', angle-x, angle-y]

    * Matrix: matrix(<a> <b> <c> <d> <e> <f>), which specifies a
      transformation in the form of a transformation matrix of six
      values. matrix(a,b,c,d,e,f) is equivalent to applying the
      transformation matrix [a b c d e f]. Result is
      ['matrix', a, b, c, d, e, f]

    Note: All parameters to the transformations are "numbers",
    i.e. no units present.

    :param trstr: SVG transform string.
    :type trstr: str
    :return: List of transforms.
    :rtype: list
    """
    trlist = []

    assert isinstance(trstr, str)
    trstr = trstr.strip(' ')

    integer_re_str = r'[+-]?[0-9]+'
    number_re_str = r'(?:[+-]?[0-9]*\.[0-9]+(?:[Ee]' + integer_re_str + ')?' + r')|' + \
                    r'(?:' + integer_re_str + r'(?:[Ee]' + integer_re_str + r')?)'

    # num_re_str = r'[\+\-]?[0-9\.e]+'  # TODO: Negative exponents missing
    comma_or_space_re_str = r'(?:(?:\s+)|(?:\s*,\s*))'
    translate_re_str = r'translate\s*\(\s*(' + \
                       number_re_str + r')(?:' + \
                       comma_or_space_re_str + \
                       r'(' + number_re_str + r'))?\s*\)'
    scale_re_str = r'scale\s*\(\s*(' + \
                   number_re_str + r')' + \
                   r'(?:' + comma_or_space_re_str + \
                   r'(' + number_re_str + r'))?\s*\)'
    skew_re_str = r'skew([XY])\s*\(\s*(' + \
                  number_re_str + r')\s*\)'
    rotate_re_str = r'rotate\s*\(\s*(' + \
                    number_re_str + r')' + \
                    r'(?:' + comma_or_space_re_str + \
                    r'(' + number_re_str + r')' + \
                    comma_or_space_re_str + \
                    r'(' + number_re_str + r'))?\s*\)'
    matrix_re_str = r'matrix\s*\(\s*' + \
                    r'(' + number_re_str + r')' + comma_or_space_re_str + \
                    r'(' + number_re_str + r')' + comma_or_space_re_str + \
                    r'(' + number_re_str + r')' + comma_or_space_re_str + \
                    r'(' + number_re_str + r')' + comma_or_space_re_str + \
                    r'(' + number_re_str + r')' + comma_or_space_re_str + \
                    r'(' + number_re_str + r')\s*\)'

    while len(trstr) > 0:
        match = re.search(r'^' + translate_re_str, trstr)
        if match:
            trlist.append([
                'translate',
                float(match.group(1)),
                float(match.group(2)) if (match.group(2) is not None) else 0.0
            ])
            trstr = trstr[len(match.group(0)):].strip(' ')
            continue

        match = re.search(r'^' + scale_re_str, trstr)
        if match:
            trlist.append([
                'scale',
                float(match.group(1)),
                float(match.group(2)) if (match.group(2) is not None) else float(match.group(1))
            ])
            trstr = trstr[len(match.group(0)):].strip(' ')
            continue

        match = re.search(r'^' + skew_re_str, trstr)
        if match:
            trlist.append([
                'skew',
                float(match.group(2)) if match.group(1) == 'X' else 0.0,
                float(match.group(2)) if match.group(1) == 'Y' else 0.0
            ])
            trstr = trstr[len(match.group(0)):].strip(' ')
            continue

        match = re.search(r'^' + rotate_re_str, trstr)
        if match:
            trlist.append([
                'rotate',
                float(match.group(1)),
                float(match.group(2)) if match.group(2) else 0.0,
                float(match.group(3)) if match.group(3) else 0.0
            ])
            trstr = trstr[len(match.group(0)):].strip(' ')
            continue

        match = re.search(r'^' + matrix_re_str, trstr)
        if match:
            trlist.append(['matrix'] + [float(x) for x in match.groups()])
            trstr = trstr[len(match.group(0)):].strip(' ')
            continue

        # raise Exception("Don't know how to parse: %s" % trstr)
        log.error("[ERROR] Don't know how to parse: %s" % trstr)

    return trlist

# if __name__ == "__main__":
#     tree = ET.parse('tests/svg/drawing.svg')
#     root = tree.getroot()
#     ns = re.search(r'\{(.*)\}', root.tag).group(1)
#     print(ns)
#     for geo in getsvggeo(root):
#         print(geo)
