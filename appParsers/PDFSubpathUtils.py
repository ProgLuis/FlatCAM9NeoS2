# ##########################################################
# FlatCAM 9 Neo S2
# PDF drawing subpath helpers
# ##########################################################

from shapely.geometry import LineString


PDF_POINT_TO_MM = 25.4 / 72.0


def bezier_points(p0, p1, p2, p3, steps=16):
    points = []
    for idx in range(steps + 1):
        t = float(idx) / float(steps)
        mt = 1.0 - t
        x_coord = (mt ** 3) * p0.x + 3 * (mt ** 2) * t * p1.x + 3 * mt * (t ** 2) * p2.x + (t ** 3) * p3.x
        y_coord = (mt ** 3) * p0.y + 3 * (mt ** 2) * t * p1.y + 3 * mt * (t ** 2) * p2.y + (t ** 3) * p3.y
        points.append((x_coord, y_coord))
    return points


def pdf_point_to_geometry(point, page_height, unit_factor=PDF_POINT_TO_MM):
    return float(point.x) * unit_factor, (float(page_height) - float(point.y)) * unit_factor


def pdf_xy_to_geometry(x_coord, y_coord, page_height, unit_factor=PDF_POINT_TO_MM):
    return float(x_coord) * unit_factor, (float(page_height) - float(y_coord)) * unit_factor


def subpath_bounds(points):
    if not points:
        return None
    x_values = [point[0] for point in points]
    y_values = [point[1] for point in points]
    return min(x_values), min(y_values), max(x_values), max(y_values)


def reconstruct_pdf_subpaths(drawing, page_height, unit_factor=PDF_POINT_TO_MM, curve_steps=16):
    subpaths = []
    current = []
    current_ops = []

    def finish_subpath():
        if len(current) >= 2:
            points = list(current)
            bounds = subpath_bounds(points)
            line_points = points
            if points[0] != points[-1]:
                line_points = points + [points[0]]
            subpaths.append({
                'index': len(subpaths),
                'points': points,
                'closed_points': line_points,
                'ops': list(current_ops),
                'bounds': bounds,
                'closed': points[0] == points[-1],
                'line': LineString(points),
                'closed_line': LineString(line_points)
            })
        del current[:]
        del current_ops[:]

    def append_points(points, op_name):
        if not points:
            return
        if current and current[-1] != points[0]:
            finish_subpath()
        if not current:
            current.extend(points)
        else:
            current.extend(points[1:])
        current_ops.append(op_name)

    for item in drawing.get('items') or []:
        op_name = item[0]
        if op_name == 'l':
            segment = [
                pdf_point_to_geometry(item[1], page_height, unit_factor),
                pdf_point_to_geometry(item[2], page_height, unit_factor)
            ]
            append_points(segment, op_name)
        elif op_name == 'c':
            curve_points = [
                pdf_xy_to_geometry(x_coord, y_coord, page_height, unit_factor)
                for x_coord, y_coord in bezier_points(item[1], item[2], item[3], item[4], steps=curve_steps)
            ]
            append_points(curve_points, op_name)
        elif op_name == 're':
            rect = item[1]
            rect_coords = [
                pdf_xy_to_geometry(rect.x0, rect.y0, page_height, unit_factor),
                pdf_xy_to_geometry(rect.x1, rect.y0, page_height, unit_factor),
                pdf_xy_to_geometry(rect.x1, rect.y1, page_height, unit_factor),
                pdf_xy_to_geometry(rect.x0, rect.y1, page_height, unit_factor),
                pdf_xy_to_geometry(rect.x0, rect.y0, page_height, unit_factor)
            ]
            finish_subpath()
            current.extend(rect_coords)
            current_ops.append(op_name)
            finish_subpath()
        elif op_name == 'qu':
            quad = item[1]
            quad_coords = [
                pdf_point_to_geometry(quad.ul, page_height, unit_factor),
                pdf_point_to_geometry(quad.ur, page_height, unit_factor),
                pdf_point_to_geometry(quad.lr, page_height, unit_factor),
                pdf_point_to_geometry(quad.ll, page_height, unit_factor),
                pdf_point_to_geometry(quad.ul, page_height, unit_factor)
            ]
            finish_subpath()
            current.extend(quad_coords)
            current_ops.append(op_name)
            finish_subpath()

    finish_subpath()
    return subpaths


def subpath_circle_info(subpath, tolerance=0.02):
    bounds = subpath.get('bounds')
    if bounds is None:
        return None

    xmin, ymin, xmax, ymax = bounds
    width = abs(xmax - xmin)
    height = abs(ymax - ymin)
    if width <= 0.0 or height <= 0.0:
        return None
    if abs(width - height) > tolerance:
        return None

    ops = subpath.get('ops') or []
    has_circle_ops = ops.count('c') >= 2 or 're' in ops or 'qu' in ops
    if not has_circle_ops:
        return None

    closed_line = subpath.get('closed_line')
    if closed_line is None or closed_line.is_empty:
        return None

    return {
        'bbox_mm': bounds,
        'center_mm': ((xmin + xmax) / 2.0, (ymin + ymax) / 2.0),
        'diameter_mm': (width + height) / 2.0,
        'width_mm': width,
        'height_mm': height,
        'closed': bool(closed_line.is_ring),
        'items_count': len(ops)
    }
