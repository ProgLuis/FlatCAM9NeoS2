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
from appParsers.PDFSourceAdvisor import advise_pdf_source
from appParsers.PDFImportLimits import (
    PDF_WARN_VECTOR_OPS,
    PDF_MAX_VECTOR_OPS,
    PDF_HIGH_COMPLEXITY_MESSAGE,
    PDF_TOO_COMPLEX_MESSAGE
)
from appParsers.PDFSubpathUtils import reconstruct_pdf_subpaths


class PDFGeometryBuilder:
    """
    Convert supported vector PDF content into Shapely geometry.

    This adapter deliberately does not create Gerber apertures or Excellon
    objects. It reuses the existing PDF parser output as an intermediate
    representation and extracts Geometry Object friendly Shapely geometry.
    """

    MAX_VECTOR_OPS = PDF_MAX_VECTOR_OPS

    def __init__(self, app):
        self.app = app
        self.parser = PdfParser(app=self.app)
        self.max_vector_ops = self.MAX_VECTOR_OPS

    @staticmethod
    def modern_source_from_advisor(analysis):
        analysis = analysis or {}
        content_type = (analysis.get('content_type') or '').strip().lower()
        if content_type not in ['vector', 'mixed']:
            return False, 'unknown'

        source = (analysis.get('source') or '').strip().lower()
        try:
            advisor = advise_pdf_source(analysis)
            advisor_source = (advisor.get('source') or '').strip().lower()
            if advisor_source:
                source = advisor_source
        except Exception:
            pass

        normalized_source = source.replace(' ', '').replace('adobe', '')
        modern_sources = ['illustrator', 'coreldraw', 'corel', 'proteus']
        return normalized_source in modern_sources, source or 'unknown'

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

    def extract_pdf_drawings_for_page(self, pdf_filename, page_number=1, crop_rect=None,
                                      exclude_drawing_indices=None):
        import fitz

        exclude_drawing_indices = set(exclude_drawing_indices or [])
        doc = fitz.open(pdf_filename)
        try:
            page = doc.load_page(int(page_number or 1) - 1)
            clip = fitz.Rect(*crop_rect) if crop_rect is not None else None
            chunks = []
            for index, drawing in enumerate(page.get_drawings()):
                if index in exclude_drawing_indices:
                    continue
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

    @staticmethod
    def _subpath_key_set(descriptors):
        keys = set()
        for descriptor in descriptors or []:
            if isinstance(descriptor, (list, tuple)) and len(descriptor) >= 2:
                keys.add((int(descriptor[0]), int(descriptor[1])))
                continue
            if isinstance(descriptor, dict):
                try:
                    keys.add((int(descriptor.get('drawing_index')), int(descriptor.get('subpath_index'))))
                except Exception:
                    continue
        return keys

    def drawings_to_geometry(self, pdf_filename, page_number=1, crop_rect=None, exclude_drawing_indices=None,
                             preserve_circle_indices=None, excluded_subpaths=None, preserved_circle_subpaths=None):
        import fitz

        exclude_drawing_indices = set(exclude_drawing_indices or [])
        preserve_circle_indices = set(preserve_circle_indices or [])
        excluded_subpaths = self._subpath_key_set(excluded_subpaths)
        preserved_circle_subpaths = self._subpath_key_set(preserved_circle_subpaths)
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

            for index, drawing in enumerate(page.get_drawings()):
                if index in exclude_drawing_indices:
                    continue
                rect = drawing.get('rect')
                if clip is not None and rect is not None and not fitz.Rect(rect).intersects(clip):
                    continue

                subpaths = reconstruct_pdf_subpaths(drawing, page_height, unit_factor)

                draw_type = drawing.get('type') or ''
                if index in preserve_circle_indices and not preserved_circle_subpaths:
                    for subpath in subpaths:
                        points = subpath.get('closed_points') or subpath.get('points') or []
                        if len(points) < 3:
                            continue
                        try:
                            add_cropped(follow_geometry, LineString(points))
                        except Exception:
                            continue
                    continue

                drawing_solids = []
                preserved_added = set()
                if 'f' in draw_type:
                    fill_lines = []
                    for subpath in subpaths:
                        key = (index, subpath.get('index'))
                        if key in excluded_subpaths:
                            continue
                        if key in preserved_circle_subpaths:
                            try:
                                add_cropped(follow_geometry, LineString(subpath.get('closed_points')))
                                preserved_added.add(key)
                            except Exception:
                                pass
                            continue
                        points = subpath.get('points') or []
                        if len(points) < 3:
                            continue
                        closed = list(points)
                        if closed[0] != closed[-1]:
                            closed.append(closed[0])
                        try:
                            polygon = Polygon(closed)
                            if polygon.is_valid and not polygon.is_empty and polygon.area > 0:
                                drawing_solids.append(polygon)
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
                                    drawing_solids.append(polygon)
                        except Exception:
                            pass

                if 's' in draw_type:
                    stroke_width = float(drawing.get('width') or 0.0) * unit_factor
                    if stroke_width > 0.0:
                        for subpath in subpaths:
                            key = (index, subpath.get('index'))
                            if key in excluded_subpaths:
                                continue
                            if key in preserved_circle_subpaths:
                                if key in preserved_added:
                                    continue
                                try:
                                    add_cropped(follow_geometry, LineString(subpath.get('closed_points')))
                                    preserved_added.add(key)
                                except Exception:
                                    pass
                                continue
                            points = subpath.get('points') or []
                            if len(points) < 2:
                                continue
                            try:
                                stroke_path = LineString(points)
                                if not stroke_path.is_empty and stroke_path.length > 0:
                                    drawing_solids.append(stroke_path.buffer(stroke_width / 2.0))
                            except Exception:
                                continue
                    else:
                        for subpath in subpaths:
                            key = (index, subpath.get('index'))
                            if key in excluded_subpaths:
                                continue
                            try:
                                add_cropped(follow_geometry, subpath.get('line'))
                            except Exception:
                                continue

                if drawing_solids:
                    try:
                        drawing_result = unary_union(drawing_solids) if len(drawing_solids) > 1 else drawing_solids[0]
                        add_cropped(solid_geometry, drawing_result)
                    except Exception:
                        for geometry in drawing_solids:
                            add_cropped(solid_geometry, geometry)
        finally:
            doc.close()

        return solid_geometry, follow_geometry

    def drawings_parse_result(self, pdf_filename, page_number=1, page_count=1, crop_rect=None,
                              started=None, temp_files=None, deleted_files=None, warnings=None,
                              exclude_drawing_indices=None, preserve_circle_indices=None,
                              excluded_subpaths=None, preserved_circle_subpaths=None):
        started = started or time.time()
        temp_files = temp_files or []
        deleted_files = deleted_files or []
        warnings = warnings or []

        pdf_content = self.extract_pdf_drawings_for_page(
            pdf_filename=pdf_filename,
            page_number=page_number,
            crop_rect=crop_rect,
            exclude_drawing_indices=exclude_drawing_indices
        )
        vector_complexity = self.count_vector_ops(pdf_content)
        if vector_complexity <= 0:
            return {
                'success': False,
                'solid_geometry': [],
                'follow_geometry': [],
                'warnings': warnings + ['No drawable vector PDF operators found.'],
                'page_number': page_number,
                'page_count': page_count,
                'temp_files': temp_files,
                'deleted_files': deleted_files,
                'elapsed': time.time() - started
            }

        complexity_warnings = list(warnings)
        if vector_complexity > PDF_WARN_VECTOR_OPS:
            complexity_warnings.append(PDF_HIGH_COMPLEXITY_MESSAGE)

        if vector_complexity > self.max_vector_ops:
            return {
                'success': False,
                'solid_geometry': [],
                'follow_geometry': [],
                'warnings': complexity_warnings + [
                    'The selected PDF page contains approximately %s vector operations. '
                    '%s' % (vector_complexity, PDF_TOO_COMPLEX_MESSAGE)
                ],
                'page_number': page_number,
                'page_count': page_count,
                'vector_complexity': vector_complexity,
                'temp_files': temp_files,
                'deleted_files': deleted_files,
                'elapsed': time.time() - started
            }

        solid_geometry, follow_geometry = self.drawings_to_geometry(
            pdf_filename, page_number=page_number, crop_rect=crop_rect,
            exclude_drawing_indices=exclude_drawing_indices,
            preserve_circle_indices=preserve_circle_indices,
            excluded_subpaths=excluded_subpaths,
            preserved_circle_subpaths=preserved_circle_subpaths
        )
        if solid_geometry or follow_geometry:
            return {
                'success': True,
                'solid_geometry': solid_geometry,
                'follow_geometry': follow_geometry,
                'warnings': complexity_warnings,
                'page_number': page_number,
                'page_count': page_count,
                'temp_files': temp_files,
                'deleted_files': deleted_files,
                'elapsed': time.time() - started
            }

        empty_message = 'No Geometry Object compatible PDF geometry was produced.'
        if crop_rect is not None:
            empty_message = 'No Geometry Object compatible PDF geometry was produced inside the selected crop.'
        return {
            'success': False,
            'solid_geometry': [],
            'follow_geometry': [],
            'warnings': complexity_warnings + [empty_message],
            'page_number': page_number,
            'page_count': page_count,
            'temp_files': temp_files,
            'deleted_files': deleted_files,
            'elapsed': time.time() - started
        }

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

    def parse_vector_pdf(self, pdf_filename, page_number=1, page_count=1, crop_rect=None, analysis=None,
                         exclude_drawing_indices=None, preserve_circle_indices=None,
                         excluded_subpaths=None, preserved_circle_subpaths=None):
        started = time.time()
        temp_filename = None
        temp_files = []
        deleted_files = []
        use_modern_parser, modern_source = self.modern_source_from_advisor(analysis)

        if use_modern_parser:
            return self.drawings_parse_result(
                pdf_filename,
                page_number=page_number,
                page_count=page_count,
                crop_rect=crop_rect,
                started=started,
                temp_files=temp_files,
                deleted_files=deleted_files,
                exclude_drawing_indices=exclude_drawing_indices,
                preserve_circle_indices=preserve_circle_indices,
                excluded_subpaths=excluded_subpaths,
                preserved_circle_subpaths=preserved_circle_subpaths
            )

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
                return self.drawings_parse_result(
                    pdf_filename,
                    page_number=page_number,
                    page_count=page_count,
                    crop_rect=crop_rect,
                    started=started,
                    temp_files=temp_files,
                    deleted_files=deleted_files,
                    exclude_drawing_indices=exclude_drawing_indices,
                    preserve_circle_indices=preserve_circle_indices,
                    excluded_subpaths=excluded_subpaths,
                    preserved_circle_subpaths=preserved_circle_subpaths
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

            complexity_warnings = []
            if vector_complexity > PDF_WARN_VECTOR_OPS:
                complexity_warnings.append(PDF_HIGH_COMPLEXITY_MESSAGE)

            if vector_complexity > self.max_vector_ops:
                return {
                    'success': False,
                    'solid_geometry': [],
                    'follow_geometry': [],
                    'warnings': [
                        'The selected PDF page contains approximately %s vector operations. '
                        '%s' % (vector_complexity, PDF_TOO_COMPLEX_MESSAGE)
                    ],
                    'page_number': page_number,
                    'page_count': page_count,
                    'vector_complexity': vector_complexity,
                    'temp_files': temp_files,
                    'deleted_files': deleted_files,
                    'elapsed': time.time() - started
                    }

            try:
                parsed_pdf = self.parser.parse_pdf(pdf_content=pdf_content)
            except Exception:
                return self.drawings_parse_result(
                    pdf_filename,
                    page_number=page_number,
                    page_count=page_count,
                    crop_rect=crop_rect,
                    started=started,
                    temp_files=temp_files,
                    deleted_files=deleted_files,
                    warnings=[
                        'Legacy PDF parser failed.',
                        'Falling back to PyMuPDF drawings parser.'
                    ],
                    exclude_drawing_indices=exclude_drawing_indices,
                    preserve_circle_indices=preserve_circle_indices,
                    excluded_subpaths=excluded_subpaths,
                    preserved_circle_subpaths=preserved_circle_subpaths
                )
            if not parsed_pdf:
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
                        pdf_filename, page_number=page_number, crop_rect=crop_rect,
                        exclude_drawing_indices=exclude_drawing_indices,
                        preserve_circle_indices=preserve_circle_indices,
                        excluded_subpaths=excluded_subpaths,
                        preserved_circle_subpaths=preserved_circle_subpaths
                    )
                    if solid_geometry or follow_geometry:
                        return {
                            'success': True,
                            'solid_geometry': solid_geometry,
                            'follow_geometry': follow_geometry,
                            'warnings': complexity_warnings,
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
                'warnings': complexity_warnings,
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
