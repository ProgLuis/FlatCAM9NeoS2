# ##########################################################
# FlatCAM 9 Neo S2
# PDF as Geometry adapter
# ##########################################################

from copy import deepcopy
import os
import re
import tempfile
import time
import zlib

from shapely.geometry import LineString, Polygon, MultiPolygon, box
from shapely.ops import polygonize, unary_union

from appParsers.ParsePDF import PdfParser


class PDFGeometryBuilder:
    """
    Convert supported vector PDF content into Shapely geometry.

    This adapter deliberately does not create Gerber apertures or Excellon
    objects. It reuses the existing PDF parser output as an intermediate
    representation and extracts Geometry Object friendly Shapely geometry.
    """

    MAX_VECTOR_OPS = 4000

    def __init__(self, app):
        self.app = app
        self.parser = PdfParser(app=self.app)
        self.max_vector_ops = self.MAX_VECTOR_OPS

    @staticmethod
    def iter_geom(geometry):
        if geometry is None:
            return []
        if getattr(geometry, 'is_empty', False):
            return []
        if isinstance(geometry, (list, tuple)):
            geo_list = []
            for geo in geometry:
                geo_list += PDFGeometryBuilder.iter_geom(geo)
            return geo_list
        if hasattr(geometry, 'geoms'):
            geo_list = []
            for geo in geometry.geoms:
                geo_list += PDFGeometryBuilder.iter_geom(geo)
            return geo_list
        return [geometry]

    @staticmethod
    def iter_pdf_streams(pdf_data):
        pos = 0
        while True:
            stream_pos = pdf_data.find(b'stream', pos)
            if stream_pos < 0:
                break

            if stream_pos >= 3 and pdf_data[stream_pos - 3:stream_pos] == b'end':
                pos = stream_pos + len(b'stream')
                continue

            end_pos = pdf_data.find(b'endstream', stream_pos)
            if end_pos < 0:
                break

            raw_start = stream_pos + len(b'stream')
            if pdf_data[raw_start:raw_start + 2] == b'\r\n':
                raw_start += 2
            elif pdf_data[raw_start:raw_start + 1] in (b'\r', b'\n'):
                raw_start += 1

            raw_stream = pdf_data[raw_start:end_pos]
            if raw_stream.endswith(b'\r\n'):
                raw_stream = raw_stream[:-2]
            elif raw_stream.endswith(b'\r') or raw_stream.endswith(b'\n'):
                raw_stream = raw_stream[:-1]

            header_start = pdf_data.rfind(b'<<', 0, stream_pos)
            header = pdf_data[header_start:stream_pos] if header_start >= 0 else b''

            yield header, raw_stream
            pos = end_pos + len(b'endstream')

    def extract_pdf_vector_streams(self, pdf_filename, page_number=1, page_count=1):
        if page_count and page_count > 1:
            return ''

        with open(pdf_filename, 'rb') as pdf_file:
            pdf_data = pdf_file.read()

        decompressed = ''
        for stream_header, stream_data in self.iter_pdf_streams(pdf_data):
            try:
                if b'FlateDecode' in stream_header:
                    try:
                        raw_stream = zlib.decompress(stream_data)
                    except Exception:
                        raw_stream = zlib.decompress(stream_data, -15)
                else:
                    raw_stream = stream_data

                try:
                    decoded_stream = raw_stream.decode('utf-8')
                except UnicodeDecodeError:
                    decoded_stream = raw_stream.decode('latin-1', errors='ignore')

                pdf_ops = [' m', ' l', ' c', ' re', ' S', ' s', ' f', ' F', ' B', ' b', ' q', ' Q', ' cm']
                if not any(op in decoded_stream for op in pdf_ops):
                    continue

                decompressed += decoded_stream + '\r\n'
            except Exception:
                continue

        return decompressed

    @staticmethod
    def _point_text(point):
        return '%.6f %.6f' % (float(point.x), float(point.y))

    @classmethod
    def drawing_to_pdf_ops(cls, drawing):
        commands = []
        for item in drawing.get('items') or []:
            op = item[0]
            if op == 'l':
                commands.append('%s m' % cls._point_text(item[1]))
                commands.append('%s l' % cls._point_text(item[2]))
            elif op == 'c':
                commands.append('%s m' % cls._point_text(item[1]))
                commands.append(
                    '%s %s %s c' % (
                        cls._point_text(item[2]),
                        cls._point_text(item[3]),
                        cls._point_text(item[4])
                    )
                )
            elif op == 're':
                rect = item[1]
                commands.append(
                    '%.6f %.6f %.6f %.6f re' %
                    (float(rect.x0), float(rect.y0), float(rect.width), float(rect.height))
                )
            elif op == 'qu':
                quad = item[1]
                commands.append('%s m' % cls._point_text(quad.ul))
                commands.append('%s l' % cls._point_text(quad.ur))
                commands.append('%s l' % cls._point_text(quad.lr))
                commands.append('%s l' % cls._point_text(quad.ll))
                commands.append('h')

        if not commands:
            return ''

        if drawing.get('closePath'):
            commands.append('h')

        draw_type = drawing.get('type') or ''
        if 'f' in draw_type and 's' in draw_type:
            commands.append('B')
        elif 'f' in draw_type:
            commands.append('f')
        elif 's' in draw_type:
            commands.append('S')
        else:
            commands.append('S')

        return '\r\n'.join(commands) + '\r\n'

    def extract_pdf_drawings_for_page(self, pdf_filename, page_number=1, crop_rect=None):
        import fitz

        doc = fitz.open(pdf_filename)
        try:
            page = doc.load_page(int(page_number or 1) - 1)
            clip = fitz.Rect(*crop_rect) if crop_rect is not None else None
            chunks = []
            for drawing in page.get_drawings():
                rect = drawing.get('rect')
                if clip is not None and rect is not None and not fitz.Rect(rect).intersects(clip):
                    continue
                chunk = self.drawing_to_pdf_ops(drawing)
                if chunk:
                    chunks.append(chunk)
            return ''.join(chunks)
        finally:
            doc.close()

    @staticmethod
    def _bezier_points(p0, p1, p2, p3, steps=16):
        points = []
        for idx in range(steps + 1):
            t = float(idx) / float(steps)
            mt = 1.0 - t
            x = (mt ** 3) * p0.x + 3 * (mt ** 2) * t * p1.x + 3 * mt * (t ** 2) * p2.x + (t ** 3) * p3.x
            y = (mt ** 3) * p0.y + 3 * (mt ** 2) * t * p1.y + 3 * mt * (t ** 2) * p2.y + (t ** 3) * p3.y
            points.append((x, y))
        return points

    @staticmethod
    def pdf_point_to_geometry(point, page_height, unit_factor):
        return float(point.x) * unit_factor, (float(page_height) - float(point.y)) * unit_factor

    @staticmethod
    def pdf_xy_to_geometry(x_coord, y_coord, page_height, unit_factor):
        return float(x_coord) * unit_factor, (float(page_height) - float(y_coord)) * unit_factor

    @staticmethod
    def pdf_rect_to_geometry_box(rect, page_height, unit_factor):
        xmin = float(rect.x0) * unit_factor
        xmax = float(rect.x1) * unit_factor
        ymin = (float(page_height) - float(rect.y1)) * unit_factor
        ymax = (float(page_height) - float(rect.y0)) * unit_factor
        return box(xmin, ymin, xmax, ymax)

    def drawings_to_geometry(self, pdf_filename, page_number=1, crop_rect=None):
        import fitz

        solid_geometry = []
        follow_geometry = []
        clip = fitz.Rect(*crop_rect) if crop_rect is not None else None

        def add_cropped(collector, geometry):
            try:
                if crop_box is not None:
                    geometry = geometry.intersection(crop_box)
                if geometry is None or geometry.is_empty:
                    return
                if geometry.geom_type in ['Polygon', 'MultiPolygon'] and geometry.is_valid is False:
                    repaired = geometry.buffer(0)
                    if repaired is not None and not repaired.is_empty and repaired.is_valid:
                        geometry = repaired
                collector += self.iter_geom(geometry)
            except Exception:
                pass

        doc = fitz.open(pdf_filename)
        try:
            page = doc.load_page(int(page_number or 1) - 1)
            page_height = float(page.rect.height)
            unit_factor = 25.4 / 72.0
            crop_box = None
            if crop_rect is not None:
                crop_box = self.pdf_rect_to_geometry_box(fitz.Rect(*crop_rect), page_height, unit_factor)

            for drawing in page.get_drawings():
                rect = drawing.get('rect')
                if clip is not None and rect is not None and not fitz.Rect(rect).intersects(clip):
                    continue

                subpaths = []
                current = []
                local_follow = []

                def finish_subpath():
                    if len(current) >= 2:
                        subpaths.append(list(current))
                    del current[:]

                def append_points(points):
                    if not points:
                        return
                    if current and current[-1] != points[0]:
                        finish_subpath()
                    if not current:
                        current.extend(points)
                    else:
                        current.extend(points[1:])

                for item in drawing.get('items') or []:
                    op = item[0]
                    if op == 'l':
                        segment = [
                            self.pdf_point_to_geometry(item[1], page_height, unit_factor),
                            self.pdf_point_to_geometry(item[2], page_height, unit_factor)
                        ]
                        append_points(segment)
                        local_follow.append(LineString(segment))
                    elif op == 'c':
                        curve_points = [
                            (x * unit_factor, (page_height - y) * unit_factor)
                            for x, y in self._bezier_points(item[1], item[2], item[3], item[4])
                        ]
                        append_points(curve_points)
                        local_follow.append(LineString(curve_points))
                    elif op == 're':
                        r = item[1]
                        rect_coords = [
                            self.pdf_xy_to_geometry(r.x0, r.y0, page_height, unit_factor),
                            self.pdf_xy_to_geometry(r.x1, r.y0, page_height, unit_factor),
                            self.pdf_xy_to_geometry(r.x1, r.y1, page_height, unit_factor),
                            self.pdf_xy_to_geometry(r.x0, r.y1, page_height, unit_factor),
                            self.pdf_xy_to_geometry(r.x0, r.y0, page_height, unit_factor)
                        ]
                        finish_subpath()
                        subpaths.append(rect_coords)
                        local_follow.append(LineString(rect_coords))
                    elif op == 'qu':
                        q = item[1]
                        quad_coords = [
                            self.pdf_point_to_geometry(q.ul, page_height, unit_factor),
                            self.pdf_point_to_geometry(q.ur, page_height, unit_factor),
                            self.pdf_point_to_geometry(q.lr, page_height, unit_factor),
                            self.pdf_point_to_geometry(q.ll, page_height, unit_factor),
                            self.pdf_point_to_geometry(q.ul, page_height, unit_factor)
                        ]
                        finish_subpath()
                        subpaths.append(quad_coords)
                        local_follow.append(LineString(quad_coords))
                finish_subpath()

                draw_type = drawing.get('type') or ''
                if 'f' in draw_type:
                    fill_lines = []
                    for subpath in subpaths:
                        if len(subpath) < 3:
                            continue
                        closed = list(subpath)
                        if closed[0] != closed[-1]:
                            closed.append(closed[0])
                        try:
                            polygon = Polygon(closed)
                            if polygon.is_valid and not polygon.is_empty and polygon.area > 0:
                                add_cropped(solid_geometry, polygon)
                            else:
                                fill_lines.append(LineString(closed))
                        except Exception:
                            try:
                                fill_lines.append(LineString(closed))
                            except Exception:
                                continue

                    if fill_lines:
                        try:
                            for polygon in polygonize(unary_union(fill_lines)):
                                if polygon is not None and not polygon.is_empty:
                                    add_cropped(solid_geometry, polygon)
                        except Exception:
                            pass

                if 's' in draw_type:
                    stroke_width = float(drawing.get('width') or 0.0) * unit_factor
                    for follow in local_follow:
                        try:
                            add_cropped(follow_geometry, follow)
                            if stroke_width > 0.0:
                                add_cropped(solid_geometry, follow.buffer(stroke_width / 2.0))
                        except Exception:
                            continue
        finally:
            doc.close()

        return solid_geometry, follow_geometry

    @staticmethod
    def count_vector_ops(pdf_content):
        vector_ops = set(['m', 'l', 'c', 're', 'S', 's', 'f', 'F', 'B', 'b'])
        tokens = re.findall(r'[A-Za-z]+|\*|\'|\"', pdf_content or '')
        return sum(1 for token in tokens if token in vector_ops)

    @staticmethod
    def count_vector_ops_legacy(pdf_content):
        vector_ops = [' m', ' l', ' c', ' re', ' S', ' s', ' f', ' F', ' B', ' b']
        return sum((pdf_content or '').count(op) for op in vector_ops)

    @staticmethod
    def selected_page_pdf(pdf_filename, page_number, page_count, crop_rect=None):
        page_number = int(page_number or 1)
        page_count = int(page_count or 1)
        if page_number < 1 or page_number > page_count:
            raise ValueError(
                'Selected PDF page %s is outside the available page range 1-%s.' %
                (page_number, page_count)
            )

        import fitz

        source_doc = fitz.open(pdf_filename)
        try:
            page_index = page_number - 1
            target_doc = fitz.open()
            try:
                if crop_rect is None:
                    target_doc.insert_pdf(source_doc, from_page=page_index, to_page=page_index)
                else:
                    clip = fitz.Rect(*crop_rect)
                    target_page = target_doc.new_page(width=clip.width, height=clip.height)
                    target_page.show_pdf_page(target_page.rect, source_doc, page_index, clip=clip)
                fd, temp_filename = tempfile.mkstemp(suffix='.pdf', prefix='flatcam_pdf_geometry_page_')
                os.close(fd)
                target_doc.save(temp_filename)
            finally:
                target_doc.close()
        finally:
            source_doc.close()

        return temp_filename

    @staticmethod
    def crop_area_ratio(pdf_filename, page_number, crop_rect=None):
        if crop_rect is None:
            return 1.0
        try:
            import fitz
            doc = fitz.open(pdf_filename)
            try:
                page = doc.load_page(int(page_number or 1) - 1)
                page_area = float(page.rect.width) * float(page.rect.height)
                clip = fitz.Rect(*crop_rect)
                clip_area = max(0.0, float(clip.width) * float(clip.height))
                if page_area <= 0.0:
                    return 1.0
                return max(0.0, min(1.0, clip_area / page_area))
            finally:
                doc.close()
        except Exception:
            return 1.0

    @staticmethod
    def apply_crop_to_geometry(geometry_list, crop_rect=None):
        if crop_rect is None:
            return geometry_list

        crop_box = box(*crop_rect)
        cropped = []
        for geometry in geometry_list:
            try:
                result = geometry.intersection(crop_box)
                if result is not None and not result.is_empty:
                    cropped += PDFGeometryBuilder.iter_geom(result)
            except Exception:
                continue
        return cropped

    def estimate_vector_complexity(self, pdf_filename, page_number=1, page_count=1, crop_rect=None):
        temp_filename = None
        try:
            source_filename = pdf_filename
            source_page_count = page_count
            if page_count and page_count > 1 and crop_rect is None:
                temp_filename = self.selected_page_pdf(pdf_filename, page_number, page_count, crop_rect=crop_rect)
                source_filename = temp_filename
                source_page_count = 1

            if crop_rect is not None:
                pdf_content = self.extract_pdf_drawings_for_page(
                    pdf_filename=pdf_filename,
                    page_number=page_number,
                    crop_rect=crop_rect
                )
            else:
                pdf_content = self.extract_pdf_vector_streams(
                    pdf_filename=source_filename,
                    page_number=page_number,
                    page_count=source_page_count
                )
            raw_complexity = self.count_vector_ops(pdf_content) if crop_rect is not None else \
                self.count_vector_ops_legacy(pdf_content)
            if crop_rect is not None:
                estimated = raw_complexity
            else:
                estimated = int(raw_complexity * self.crop_area_ratio(pdf_filename, page_number, crop_rect=crop_rect))
            return max(1, estimated) if raw_complexity > 0 else 0
        except Exception:
            return self.MAX_VECTOR_OPS + 1
        finally:
            if temp_filename:
                try:
                    if os.path.exists(temp_filename):
                        os.remove(temp_filename)
                except Exception:
                    pass

    def parse_vector_pdf(self, pdf_filename, page_number=1, page_count=1, crop_rect=None):
        started = time.time()
        temp_filename = None
        temp_files = []
        deleted_files = []

        if page_count and page_count > 1 and crop_rect is None:
            try:
                temp_filename = self.selected_page_pdf(pdf_filename, page_number, page_count, crop_rect=crop_rect)
                temp_files.append(temp_filename)
                source_filename = temp_filename
                source_page_count = 1
            except Exception as e:
                return {
                    'success': False,
                    'solid_geometry': [],
                    'follow_geometry': [],
                    'warnings': [
                        'Selected PDF page could not be isolated safely. Operation cancelled: %s' % str(e)
                    ],
                    'page_number': page_number,
                    'page_count': page_count,
                    'temp_files': temp_files,
                    'deleted_files': deleted_files,
                    'elapsed': time.time() - started
                }
        else:
            source_filename = pdf_filename
            source_page_count = page_count

        try:
            if crop_rect is not None:
                pdf_content = self.extract_pdf_drawings_for_page(
                    pdf_filename=pdf_filename,
                    page_number=page_number,
                    crop_rect=crop_rect
                )
            else:
                pdf_content = self.extract_pdf_vector_streams(
                    pdf_filename=source_filename,
                    page_number=page_number,
                    page_count=source_page_count
                )

            if not pdf_content.strip():
                return {
                    'success': False,
                    'solid_geometry': [],
                    'follow_geometry': [],
                    'warnings': ['No usable vector PDF content found.'],
                    'page_number': page_number,
                    'page_count': page_count,
                    'temp_files': temp_files,
                    'deleted_files': deleted_files,
                    'elapsed': time.time() - started
                }

            raw_vector_complexity = self.count_vector_ops(pdf_content) if crop_rect is not None else \
                self.count_vector_ops_legacy(pdf_content)
            if crop_rect is not None:
                vector_complexity = raw_vector_complexity
            else:
                vector_complexity = int(
                    raw_vector_complexity * self.crop_area_ratio(pdf_filename, page_number, crop_rect=crop_rect)
                )
            if raw_vector_complexity > 0:
                vector_complexity = max(1, vector_complexity)
            if vector_complexity <= 0:
                return {
                    'success': False,
                    'solid_geometry': [],
                    'follow_geometry': [],
                    'warnings': ['No drawable vector PDF operators found.'],
                    'page_number': page_number,
                    'page_count': page_count,
                    'temp_files': temp_files,
                    'deleted_files': deleted_files,
                    'elapsed': time.time() - started
                }

            if vector_complexity > self.max_vector_ops:
                return {
                    'success': False,
                    'solid_geometry': [],
                    'follow_geometry': [],
                    'warnings': [
                        'The selected PDF page contains approximately %s vector operations. '
                        'This exceeds the current safe processing limit (%s operations). '
                        'Recommendations: export only the PCB layer to a new PDF; remove decorative artwork, '
                        'title blocks and unused pages; simplify the PDF before importing; if possible, export only '
                        'the circuit geometry. The import was cancelled to prevent excessive memory usage or long '
                        'processing times.' %
                        (vector_complexity, self.max_vector_ops)
                    ],
                    'page_number': page_number,
                    'page_count': page_count,
                    'vector_complexity': vector_complexity,
                    'temp_files': temp_files,
                    'deleted_files': deleted_files,
                        'elapsed': time.time() - started
                    }

            if crop_rect is not None:
                solid_geometry, follow_geometry = self.drawings_to_geometry(
                    pdf_filename, page_number=page_number, crop_rect=crop_rect
                )
                if solid_geometry or follow_geometry:
                    return {
                        'success': True,
                        'solid_geometry': solid_geometry,
                        'follow_geometry': follow_geometry,
                        'warnings': [],
                        'page_number': page_number,
                        'page_count': page_count,
                        'temp_files': temp_files,
                        'deleted_files': deleted_files,
                        'elapsed': time.time() - started
                    }
                return {
                    'success': False,
                    'solid_geometry': [],
                    'follow_geometry': [],
                    'warnings': ['No Geometry Object compatible PDF geometry was produced inside the selected crop.'],
                    'page_number': page_number,
                    'page_count': page_count,
                    'temp_files': temp_files,
                    'deleted_files': deleted_files,
                    'elapsed': time.time() - started
                }

            parsed_pdf = self.parser.parse_pdf(pdf_content=pdf_content)
            if not parsed_pdf:
                if crop_rect is not None:
                    solid_geometry, follow_geometry = self.drawings_to_geometry(
                        pdf_filename, page_number=page_number, crop_rect=crop_rect
                    )
                    if solid_geometry or follow_geometry:
                        return {
                            'success': True,
                            'solid_geometry': solid_geometry,
                            'follow_geometry': follow_geometry,
                            'warnings': [],
                            'page_number': page_number,
                            'page_count': page_count,
                            'temp_files': temp_files,
                            'deleted_files': deleted_files,
                            'elapsed': time.time() - started
                        }
                return {
                    'success': False,
                    'solid_geometry': [],
                    'follow_geometry': [],
                    'warnings': ['PDF vector parser returned no geometry.'],
                    'page_number': page_number,
                    'page_count': page_count,
                    'temp_files': temp_files,
                    'deleted_files': deleted_files,
                    'elapsed': time.time() - started
                }

            solid_geometry = []
            follow_geometry = []
            clear_geometry = []

            for layer_nr in parsed_pdf:
                ap_dict = parsed_pdf[layer_nr]
                if not ap_dict:
                    continue
                for aperture_id in ap_dict:
                    aperture = ap_dict[aperture_id]
                    if 'geometry' not in aperture:
                        continue
                    for geo_el in aperture['geometry']:
                        if 'solid' in geo_el:
                            for geo in self.iter_geom(geo_el['solid']):
                                if geo is not None and not geo.is_empty:
                                    solid_geometry.append(geo)
                        if 'follow' in geo_el:
                            for geo in self.iter_geom(geo_el['follow']):
                                if geo is not None and not geo.is_empty:
                                    follow_geometry.append(geo)
                        if 'clear' in geo_el:
                            for geo in self.iter_geom(geo_el['clear']):
                                if geo is not None and not geo.is_empty:
                                    clear_geometry.append(geo)

            if solid_geometry and clear_geometry:
                cleaned = []
                for solid in solid_geometry:
                    solid_geo = deepcopy(solid)
                    for clear in clear_geometry:
                        try:
                            if clear.within(solid_geo):
                                solid_geo = solid_geo.difference(clear)
                        except Exception:
                            continue
                    if solid_geo is not None and not solid_geo.is_empty:
                        cleaned += self.iter_geom(solid_geo)
                solid_geometry = cleaned

            if solid_geometry:
                try:
                    union_geo = unary_union(solid_geometry)
                    solid_geometry = [geo for geo in self.iter_geom(union_geo) if isinstance(geo, (Polygon, MultiPolygon))]
                except Exception:
                    pass

            solid_geometry = self.apply_crop_to_geometry(solid_geometry, crop_rect=crop_rect)
            follow_geometry = self.apply_crop_to_geometry(follow_geometry, crop_rect=crop_rect)

            if not solid_geometry and not follow_geometry:
                if crop_rect is not None:
                    solid_geometry, follow_geometry = self.drawings_to_geometry(
                        pdf_filename, page_number=page_number, crop_rect=crop_rect
                    )
                    if solid_geometry or follow_geometry:
                        return {
                            'success': True,
                            'solid_geometry': solid_geometry,
                            'follow_geometry': follow_geometry,
                            'warnings': [],
                            'page_number': page_number,
                            'page_count': page_count,
                            'temp_files': temp_files,
                            'deleted_files': deleted_files,
                            'elapsed': time.time() - started
                        }
                return {
                    'success': False,
                    'solid_geometry': [],
                    'follow_geometry': [],
                    'warnings': ['No Geometry Object compatible PDF geometry was produced.'],
                    'page_number': page_number,
                    'page_count': page_count,
                    'temp_files': temp_files,
                    'deleted_files': deleted_files,
                    'elapsed': time.time() - started
                }

            return {
                'success': True,
                'solid_geometry': solid_geometry,
                'follow_geometry': follow_geometry,
                'warnings': [],
                'page_number': page_number,
                'page_count': page_count,
                'temp_files': temp_files,
                'deleted_files': deleted_files,
                'elapsed': time.time() - started
            }
        finally:
            if temp_filename:
                try:
                    if os.path.exists(temp_filename):
                        os.remove(temp_filename)
                        deleted_files.append(temp_filename)
                except Exception:
                    pass
