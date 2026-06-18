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

# import xml.etree.ElementTree as ET
from lxml import etree as ET
from svg.path import Line, Arc, CubicBezier, QuadraticBezier, parse_path
# from svg.path.path import Move
# from svg.path.path import Close
import svg.path
from shapely.geometry import LineString, MultiLineString, Point, Polygon
from shapely.affinity import skew, affine_transform, rotate
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
    val = root.get('viewBox')
    if val is None:
        return 1.0

    res = [float(x) for x in val.split()] or [float(x) for x in val.split(',')]
    w = svgparselength(root.get('width'))[0]
    # h = svgparselength(root.get('height'))[0]

    v_w = res[2]
    # v_h = res[3]

    return w / v_w


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
    w = svgparselength(rect.get('width'))[0]
    h = svgparselength(rect.get('height'))[0]

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
    rxstr = rxstr * factor if rxstr else rxstr
    rystr = rect.get('ry')
    rystr = rystr * factor if rystr else rystr

    if rxstr is None and rystr is None:  # Sharp corners
        pts = [
            (x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)
        ]

    else:  # Rounded corners
        rx = 0.0 if rxstr is None else svgparselength(rxstr)[0]
        ry = 0.0 if rystr is None else svgparselength(rystr)[0]

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

    fill = node.get('fill')
    if fill is not None:
        effective_style['fill'] = fill

    effective_style.update(svggetstyle(node))

    return effective_style


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

    if stroke is not None and stroke.strip().lower() == 'none':
        return None

    stroke_width = style_dict.get('stroke-width')

    if stroke_width is None:
        return None

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


def svgextract_circular_paths(node, root=None, factor=1.0, inherited_style=None, circle_tolerance=0.02):
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

    kind = re.search('(?:\{.*\})?(.*)$', node.tag).group(1)
    effective_style = svgget_effective_style(node, inherited_style=inherited_style)
    circles = []

    if len(node) > 0:
        for child in node:
            circles += svgextract_circular_paths(
                child, root=root, factor=factor, inherited_style=effective_style,
                circle_tolerance=circle_tolerance
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
        is_stroke_none = stroke is not None and stroke.strip().lower() == 'none'

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


def getsvggeo(node, object_type, root=None, units='MM', res=64, factor=1.0, inherited_style=None):
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
    :return:            List of Shapely geometry
    :rtype:             list
    """
    if root is None:
        root = node

    kind = re.search('(?:\{.*\})?(.*)$', node.tag).group(1)
    effective_style = svgget_effective_style(node, inherited_style=inherited_style)
    geo = []

    # Recurse
    if len(node) > 0:
        for child in node:
            subgeo = getsvggeo(
                child, object_type, root=root, units=units, res=res, factor=factor,
                inherited_style=effective_style
            )
            if subgeo is not None:
                geo += subgeo
    # Parse
    elif kind == 'path':
        log.debug("***PATH***")
        P = parse_path(node.get('d'))
        P = path2shapely(P, object_type, units=units, factor=factor)
        # for path, the resulting geometry is already a list so no need to create a new one
        geo = P

    elif kind == 'rect':
        ## log.debug("***RECT***")
        R = svgrect2shapely(node, n_points=res, factor=factor)
        geo = [R]

    elif kind == 'circle':
        log.debug("***CIRCLE***")
        C = svgcircle2shapely(node, n_points=res, factor=factor)
        geo = [C]

    elif kind == 'ellipse':
        log.debug("***ELLIPSE***")
        E = svgellipse2shapely(node, n_points=res, factor=factor)
        geo = [E]

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

    elif kind == 'use':
        log.debug('***USE***')
        # href= is the preferred name for this[1], but inkscape still generates xlink:href=.
        # [1] https://developer.mozilla.org/en-US/docs/Web/SVG/Element/use#Attributes
        href = node.attrib['href'] if 'href' in node.attrib else node.attrib['{http://www.w3.org/1999/xlink}href']
        ref = root.find(".//*[@id='%s']" % href.replace('#', ''))
        if ref is not None:
            geo = getsvggeo(
                ref, object_type, root=root, units=units, res=res, factor=factor,
                inherited_style=effective_style
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
            # log.debug(trlist)

            # Transformations are applied in reverse order
            for tr in trlist[::-1]:
                if tr[0] == 'translate':
                    geo = [translate(geoi, tr[1], tr[2]) for geoi in geo]
                elif tr[0] == 'scale':
                    geo = [scale(geoi, tr[1], tr[2], origin=(0, 0))
                           for geoi in geo]
                elif tr[0] == 'rotate':
                    geo = [rotate(geoi, tr[1], origin=(tr[2], tr[3]))
                           for geoi in geo]
                elif tr[0] == 'skew':
                    geo = [skew(geoi, tr[1], tr[2], origin=(0, 0))
                           for geoi in geo]
                elif tr[0] == 'matrix':
                    geo = [affine_transform(geoi, tr[1:]) for geoi in geo]
                else:
                    raise Exception('Unknown transformation: %s', tr)

    return geo


def getsvgtext(node, object_type, units='MM'):
    """
    Extracts and flattens all geometry from an SVG node
    into a list of Shapely geometry.

    :param node:        xml.etree.ElementTree.Element
    :param object_type:
    :param units:       FlatCAM units
    :return:            List of Shapely geometry
    :rtype:             list
    """
    kind = re.search('(?:\{.*\})?(.*)$', node.tag).group(1)
    geo = []

    # Recurse
    if len(node) > 0:
        for child in node:
            subgeo = getsvgtext(child, object_type, units=units)
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
            # log.debug(trlist)

            # Transformations are applied in reverse order
            for tr in trlist[::-1]:
                if tr[0] == 'translate':
                    geo = [translate(geoi, tr[1], tr[2]) for geoi in geo]
                elif tr[0] == 'scale':
                    geo = [scale(geoi, tr[1], tr[2], origin=(0, 0))
                           for geoi in geo]
                elif tr[0] == 'rotate':
                    geo = [rotate(geoi, tr[1], origin=(tr[2], tr[3]))
                           for geoi in geo]
                elif tr[0] == 'skew':
                    geo = [skew(geoi, tr[1], tr[2], origin=(0, 0))
                           for geoi in geo]
                elif tr[0] == 'matrix':
                    geo = [affine_transform(geoi, tr[1:]) for geoi in geo]
                else:
                    raise Exception('Unknown transformation: %s', tr)

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
