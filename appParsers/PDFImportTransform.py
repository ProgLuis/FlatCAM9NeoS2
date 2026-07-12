# ##########################################################
# FlatCAM 9 Neo S2
# Shared PDF import transformations
# ##########################################################

from shapely.affinity import scale


PDF_POINT_TO_MM = 25.4 / 72.0


def _pdf_rect_to_mm_origin(page_height, rect):
    left, top, right, bottom = [float(value) for value in rect]
    x_center = ((left + right) / 2.0) * PDF_POINT_TO_MM
    y_center = (float(page_height) - ((top + bottom) / 2.0)) * PDF_POINT_TO_MM
    return x_center, y_center


def get_pdf_import_origin(pdf_filename, page_number=1, crop_rect=None):
    import fitz

    page_index = max(int(page_number or 1) - 1, 0)
    doc = fitz.open(pdf_filename)
    try:
        if page_index >= doc.page_count:
            page_index = 0
        page = doc.load_page(page_index)
        if crop_rect is not None:
            return _pdf_rect_to_mm_origin(page.rect.height, crop_rect)

        rect = page.rect
        return _pdf_rect_to_mm_origin(page.rect.height, (rect.x0, rect.y0, rect.x1, rect.y1))
    finally:
        doc.close()


def apply_pdf_import_transform_to_geometry(geometry, origin, flip_horizontal=False, flip_vertical=False):
    if geometry is None:
        return geometry
    if not flip_horizontal and not flip_vertical:
        return geometry

    xfact = -1.0 if flip_horizontal else 1.0
    yfact = -1.0 if flip_vertical else 1.0
    return scale(geometry, xfact=xfact, yfact=yfact, zfact=1.0, origin=origin)


def apply_pdf_import_transform_to_geometry_list(geometry_list, origin, flip_horizontal=False, flip_vertical=False):
    if not geometry_list or (not flip_horizontal and not flip_vertical):
        return geometry_list

    transformed = []
    for geometry in geometry_list:
        try:
            transformed.append(
                apply_pdf_import_transform_to_geometry(
                    geometry,
                    origin,
                    flip_horizontal=flip_horizontal,
                    flip_vertical=flip_vertical
                )
            )
        except Exception:
            transformed.append(geometry)
    return transformed


def apply_pdf_import_transform_to_point(x_coord, y_coord, origin, flip_horizontal=False, flip_vertical=False):
    x_value = float(x_coord)
    y_value = float(y_coord)
    origin_x, origin_y = origin

    if flip_horizontal:
        x_value = (2.0 * float(origin_x)) - x_value
    if flip_vertical:
        y_value = (2.0 * float(origin_y)) - y_value

    return x_value, y_value


def apply_pdf_import_transform_to_parsed_pdf(parsed_pdf, origin, flip_horizontal=False, flip_vertical=False):
    if not parsed_pdf or (not flip_horizontal and not flip_vertical):
        return parsed_pdf

    for layer_nr in parsed_pdf:
        layer = parsed_pdf.get(layer_nr) or {}
        for aperture_id in layer:
            aperture = layer.get(aperture_id) or {}
            for geo_el in aperture.get('geometry', []) or []:
                for key in ['solid', 'follow', 'clear']:
                    if key in geo_el:
                        try:
                            geo_el[key] = apply_pdf_import_transform_to_geometry(
                                geo_el[key],
                                origin,
                                flip_horizontal=flip_horizontal,
                                flip_vertical=flip_vertical
                            )
                        except Exception:
                            pass
    return parsed_pdf
