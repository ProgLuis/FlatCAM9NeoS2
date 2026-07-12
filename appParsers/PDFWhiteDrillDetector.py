# ##########################################################
# FlatCAM 9 Neo S2
# PDF white drill candidate detector
# ##########################################################

import os

from appParsers.PDFSubpathUtils import PDF_POINT_TO_MM, reconstruct_pdf_subpaths, subpath_circle_info


def _crop_rect_to_mm(crop_rect, page_height):
    if crop_rect is None:
        return None

    left, top, right, bottom = [float(value) for value in crop_rect]
    xmin = min(left, right) * PDF_POINT_TO_MM
    xmax = max(left, right) * PDF_POINT_TO_MM
    ymin = (float(page_height) - max(top, bottom)) * PDF_POINT_TO_MM
    ymax = (float(page_height) - min(top, bottom)) * PDF_POINT_TO_MM
    return xmin, ymin, xmax, ymax


def _point_inside_crop_mm(x_coord, y_coord, crop_box, tolerance=0.01):
    if crop_box is None:
        return True

    xmin, ymin, xmax, ymax = crop_box
    return (
        float(x_coord) >= xmin - tolerance and
        float(x_coord) <= xmax + tolerance and
        float(y_coord) >= ymin - tolerance and
        float(y_coord) <= ymax + tolerance
    )


def _bbox_intersects_crop_mm(bounds, crop_box, tolerance=0.01):
    if crop_box is None:
        return True
    if bounds is None:
        return False

    xmin, ymin, xmax, ymax = bounds
    cxmin, cymin, cxmax, cymax = crop_box
    return not (
        xmax < cxmin - tolerance or
        xmin > cxmax + tolerance or
        ymax < cymin - tolerance or
        ymin > cymax + tolerance
    )


def _is_white_color(color, tolerance=0.01):
    if color is None:
        return False

    try:
        values = list(color)
    except Exception:
        return False

    if len(values) == 1:
        try:
            return abs(float(values[0]) - 1.0) <= tolerance
        except Exception:
            return False

    if len(values) < 3:
        return False

    try:
        return all(abs(float(value) - 1.0) <= tolerance for value in values[:3])
    except Exception:
        return False


def _pdf_point_to_mm(x_pdf, y_pdf, page_height):
    x_mm = float(x_pdf) * PDF_POINT_TO_MM
    y_mm = (float(page_height) - float(y_pdf)) * PDF_POINT_TO_MM
    return x_mm, y_mm


def _point_inside_crop(x_coord, y_coord, crop_rect, tolerance=0.01):
    if crop_rect is None:
        return True

    left, top, right, bottom = [float(value) for value in crop_rect]
    return (
        float(x_coord) >= min(left, right) - tolerance and
        float(x_coord) <= max(left, right) + tolerance and
        float(y_coord) >= min(top, bottom) - tolerance and
        float(y_coord) <= max(top, bottom) + tolerance
    )


def _add_tool(tools, candidate, diameter_tolerance):
    diameter = candidate['diameter']
    selected_key = None

    for key in tools:
        try:
            if abs(float(key) - diameter) <= diameter_tolerance:
                selected_key = key
                break
        except Exception:
            continue

    if selected_key is None:
        selected_key = '%.4f' % diameter
        tools[selected_key] = []

    tools[selected_key].append(candidate)


def detect_white_pdf_drills(pdf_filename, page_number=1, crop_rect=None, min_dia=0.2, max_dia=6.0,
                            units='MM', circle_tolerance=0.02, color_tolerance=0.01,
                            diameter_tolerance=0.01):
    """
    Detect white circular vector drawings that may represent drill holes in a PDF page.

    This function is intentionally pure with respect to FlatCAM objects: it does not
    create Excellon objects and does not modify Geometry. Coordinates and diameters
    are returned in millimeters using the PDF point scale.
    """

    result = {
        'success': False,
        'source_file': pdf_filename,
        'page_number': int(page_number or 1),
        'crop_rect': crop_rect,
        'candidate_count': 0,
        'tools': {},
        'diameters': [],
        'warnings': [],
        'rejected': {
            'not_white': 0,
            'ellipse': 0,
            'too_small': 0,
            'too_large': 0,
            'outside_crop': 0,
            'not_filled': 0,
            'invalid': 0
        },
        'units': units,
        'drawings': 0,
        'accepted_drawing_indices': [],
        'preserved_circle_indices': [],
        'accepted_drill_subpaths': [],
        'preserved_circle_subpaths': []
    }

    if not pdf_filename or not os.path.isfile(pdf_filename):
        result['warnings'].append('PDF file was not found for white drill detection.')
        return result

    try:
        import fitz
    except Exception as e:
        result['warnings'].append('PyMuPDF is required for PDF white drill detection: %s' % str(e))
        return result

    try:
        doc = fitz.open(pdf_filename)
    except Exception as e:
        result['warnings'].append('PDF file could not be opened for white drill detection: %s' % str(e))
        return result

    try:
        page_index = max(int(page_number or 1) - 1, 0)
        if page_index >= len(doc):
            result['warnings'].append('Selected PDF page is outside the document page range.')
            return result

        page = doc.load_page(page_index)
        page_height = float(page.rect.height)
        crop_box_mm = _crop_rect_to_mm(crop_rect, page_height)
        drawings = page.get_drawings()
        result['drawings'] = len(drawings)

        if not drawings:
            try:
                if page.get_images(full=True):
                    result['warnings'].append('Raster PDF is not supported for white drill detection.')
            except Exception:
                pass
            result['success'] = True
            return result

        for index, drawing in enumerate(drawings):
            draw_type = drawing.get('type') or ''
            fill = drawing.get('fill')
            is_filled = 'f' in draw_type
            subpaths = reconstruct_pdf_subpaths(drawing, page_height)
            drawing_accepted = False
            drawing_preserved = False
            drawing_had_circle = False

            if not subpaths:
                result['rejected']['invalid'] += 1
                continue

            for subpath in subpaths:
                circle = subpath_circle_info(subpath, tolerance=circle_tolerance)
                if circle is None:
                    continue

                drawing_had_circle = True
                intersects_crop = _bbox_intersects_crop_mm(circle.get('bbox_mm'), crop_box_mm)
                if not intersects_crop:
                    continue

                descriptor = {
                    'x': circle.get('center_mm')[0],
                    'y': circle.get('center_mm')[1],
                    'drawing_index': index,
                    'subpath_index': subpath.get('index'),
                    'center': circle.get('center_mm'),
                    'center_mm': circle.get('center_mm'),
                    'diameter': circle.get('diameter_mm'),
                    'diameter_mm': circle.get('diameter_mm'),
                    'width': circle.get('width_mm'),
                    'height': circle.get('height_mm'),
                    'bbox_mm': circle.get('bbox_mm'),
                    'closed': circle.get('closed'),
                    'items_count': circle.get('items_count'),
                    'page_number': page_index + 1,
                    'type': draw_type,
                    'fill': fill,
                    'stroke': drawing.get('color'),
                    'stroke_width': drawing.get('width'),
                    'source': 'PyMuPDF get_drawings subpath'
                }

                accepted = True
                if not is_filled:
                    result['rejected']['not_filled'] += 1
                    accepted = False
                elif not _is_white_color(fill, tolerance=color_tolerance):
                    result['rejected']['not_white'] += 1
                    accepted = False
                elif circle.get('diameter_mm') < float(min_dia):
                    result['rejected']['too_small'] += 1
                    accepted = False
                elif circle.get('diameter_mm') > float(max_dia):
                    result['rejected']['too_large'] += 1
                    accepted = False
                elif not _point_inside_crop_mm(circle.get('center_mm')[0], circle.get('center_mm')[1], crop_box_mm):
                    result['rejected']['outside_crop'] += 1
                    accepted = False

                if accepted:
                    _add_tool(result['tools'], descriptor, diameter_tolerance=diameter_tolerance)
                    result['accepted_drill_subpaths'].append(descriptor)
                    drawing_accepted = True
                else:
                    result['preserved_circle_subpaths'].append(descriptor)
                    drawing_preserved = True

            if drawing_accepted and index not in result['accepted_drawing_indices'] and len(subpaths) == 1:
                result['accepted_drawing_indices'].append(index)
            if drawing_preserved and index not in result['preserved_circle_indices'] and not drawing_accepted:
                result['preserved_circle_indices'].append(index)
            if not drawing_had_circle:
                result['rejected']['invalid'] += 1

        result['diameters'] = sorted(float(key) for key in result['tools'])
        result['candidate_count'] = sum(len(items) for items in result['tools'].values())
        result['success'] = True
        return result
    except Exception as e:
        result['warnings'].append('PDF white drill detection failed: %s' % str(e))
        return result
    finally:
        try:
            doc.close()
        except Exception:
            pass
