# ##########################################################
# FlatCAM 9 Neo S2
# PDF raster vectorization entry point
# ##########################################################

import os
import tempfile
import time

import numpy as np
from shapely.ops import unary_union


class PDFRasterVectorizer:
    """
    Initial guarded raster PDF vectorization facade.

    The real raster pipeline is intentionally not executed unless all optional
    dependencies are available. This keeps PDF as Geometry independent and
    prevents partial raster imports from affecting existing CAM flows.
    """

    REQUIRED_MODULES = (
        ('fitz', 'PyMuPDF'),
        ('PIL', 'Pillow'),
        ('potrace', 'potracer'),
        ('svgwrite', 'svgwrite'),
    )

    def __init__(self, app=None):
        self.app = app

    @classmethod
    def dependency_status(cls):
        missing = []
        available = []
        for module_name, friendly_name in cls.REQUIRED_MODULES:
            try:
                __import__(module_name)
                available.append(friendly_name)
            except Exception:
                missing.append(friendly_name)
        return available, missing

    @staticmethod
    def _point_xy(point):
        if hasattr(point, 'x') and hasattr(point, 'y'):
            return float(point.x), float(point.y)
        if hasattr(point, '__getitem__'):
            return float(point[0]), float(point[1])
        text = str(point).replace('Point(', '').replace(')', '')
        x_text, y_text = text.split(',')
        return float(x_text), float(y_text)

    @staticmethod
    def _geometry_metrics(geometry):
        if geometry is None:
            return 0, None

        if isinstance(geometry, (list, tuple)):
            valid_geo = [geo for geo in geometry if geo is not None and not getattr(geo, 'is_empty', False)]
            if not valid_geo:
                return 0, None
            try:
                return len(valid_geo), unary_union(valid_geo).bounds
            except Exception:
                return len(valid_geo), None

        if getattr(geometry, 'is_empty', False):
            return 0, None

        try:
            return 1, geometry.bounds
        except Exception:
            return 1, None

    @classmethod
    def _curve_to_svg_path(cls, curve):
        sx, sy = cls._point_xy(curve.start_point)
        commands = ['M %.6f %.6f' % (sx, sy)]
        for segment in curve:
            ex, ey = cls._point_xy(segment.end_point)
            if getattr(segment, 'is_corner', False):
                cx, cy = cls._point_xy(segment.c)
                commands.append('L %.6f %.6f' % (cx, cy))
                commands.append('L %.6f %.6f' % (ex, ey))
            else:
                c1x, c1y = cls._point_xy(segment.c1)
                c2x, c2y = cls._point_xy(segment.c2)
                commands.append('C %.6f %.6f %.6f %.6f %.6f %.6f' % (c1x, c1y, c2x, c2y, ex, ey))
        commands.append('Z')
        return ' '.join(commands)

    def vectorize_pdf(self, pdf_filename, page_number=1, crop_rect=None):
        page_index = max(int(page_number) - 1, 0)
        started = time.time()
        available, missing = self.dependency_status()
        if missing:
            return {
                'success': False,
                'solid_geometry': [],
                'warnings': [
                    'Raster PDF detected, but raster vectorization dependencies are not available. '
                    'Required: PyMuPDF, Pillow, potracer, svgwrite. Missing: %s.' % ', '.join(missing)
                ],
                'available_dependencies': available,
                'missing_dependencies': missing,
                'page': page_index,
                'file': pdf_filename,
                'stages': {
                    'pymupdf': False,
                    'pillow': False,
                    'potrace': False,
                    'svg_bridge': False,
                    'parsesvg': False,
                    'geometry': False
                },
                'elapsed': time.time() - started
            }

        import fitz
        from PIL import Image
        import potrace
        import svgwrite
        from camlib import Geometry

        temp_files = []
        deleted_files = []
        stages = {
            'pymupdf': False,
            'pillow': False,
            'potrace': False,
            'svg_bridge': False,
            'parsesvg': False,
            'geometry': False
        }

        try:
            pdf_doc = fitz.open(pdf_filename)
            if page_index < 0 or page_index >= pdf_doc.page_count:
                return {
                    'success': False,
                    'solid_geometry': [],
                    'warnings': ['Selected PDF page is outside the available page range.'],
                    'available_dependencies': available,
                    'missing_dependencies': [],
                    'page': page_index,
                    'file': pdf_filename,
                    'stages': stages,
                    'elapsed': time.time() - started
                }

            page = pdf_doc.load_page(page_index)
            page_rect = page.rect
            clip = None
            if crop_rect is not None:
                clip = fitz.Rect(*crop_rect)
                page_rect = clip
            matrix = fitz.Matrix(2.0, 2.0)
            pixmap = page.get_pixmap(matrix=matrix, clip=clip, alpha=False)
            stages['pymupdf'] = True

            mode = 'RGB' if pixmap.n >= 3 else 'L'
            image = Image.frombytes(mode, (pixmap.width, pixmap.height), pixmap.samples)
            gray = image.convert('L')

            # Software-generated PCB rasters are usually high contrast.
            # Potrace expects a real black/white bitmap; black pixels are traced.
            arr = np.array(gray)
            mask = arr < 128
            binary = np.where(mask, 0, 255).astype(np.uint8)
            stages['pillow'] = True

            bitmap = potrace.Bitmap(binary)
            traced_path = bitmap.trace()
            path_count = len(traced_path) if hasattr(traced_path, '__len__') else sum(1 for _ in traced_path)
            if path_count <= 0:
                return {
                    'success': False,
                    'solid_geometry': [],
                    'warnings': ['Potrace did not produce vector paths from the raster PDF page.'],
                    'available_dependencies': available,
                    'missing_dependencies': [],
                    'page': page_index,
                    'file': pdf_filename,
                    'stages': stages,
                    'elapsed': time.time() - started
                }
            stages['potrace'] = True

            svg_fd, svg_filename = tempfile.mkstemp(suffix='.svg', prefix='flatcam_pdf_raster_')
            os.close(svg_fd)
            temp_files.append(svg_filename)

            width_mm = float(page_rect.width) * 25.4 / 72.0
            height_mm = float(page_rect.height) * 25.4 / 72.0
            drawing = svgwrite.Drawing(
                svg_filename,
                size=('%0.6fmm' % width_mm, '%0.6fmm' % height_mm),
                profile='tiny'
            )
            drawing.viewbox(0, 0, pixmap.width, pixmap.height)
            for curve in traced_path:
                d_value = self._curve_to_svg_path(curve)
                drawing.add(drawing.path(d=d_value, fill='#000000', stroke='none'))
            drawing.save()
            stages['svg_bridge'] = True

            if self.app is not None:
                Geometry.app = self.app
            geo_importer = Geometry.__new__(Geometry)
            geo_importer.solid_geometry = None
            geo_importer.follow_geometry = None
            geo_importer.decimals = getattr(self.app, 'decimals', 4)
            geo_importer.units = self.app.defaults.get('units', 'MM') if self.app is not None else 'MM'
            geo_importer.options = {'name': os.path.basename(pdf_filename)}
            geo_importer.tools = {}
            geo_importer.import_svg(svg_filename, object_type='geometry', flip=True, units='MM')
            stages['parsesvg'] = True

            solid_geometry = geo_importer.solid_geometry
            geometry_count, bounds = self._geometry_metrics(solid_geometry)
            if geometry_count <= 0:
                return {
                    'success': False,
                    'solid_geometry': [],
                    'warnings': ['ParseSVG did not produce usable geometry from the temporary SVG bridge.'],
                    'available_dependencies': available,
                    'missing_dependencies': [],
                    'page': page_index,
                    'file': pdf_filename,
                    'stages': stages,
                    'temp_files': temp_files,
                    'deleted_files': deleted_files,
                    'elapsed': time.time() - started
                }
            stages['geometry'] = True

            return {
                'success': True,
                'solid_geometry': solid_geometry,
                'warnings': [],
                'available_dependencies': available,
                'missing_dependencies': [],
                'page': page_index,
                'file': pdf_filename,
                'stages': stages,
                'path_count': path_count,
                'geometry_count': geometry_count,
                'image_size': (pixmap.width, pixmap.height),
                'page_size_mm': (width_mm, height_mm),
                'bounds': bounds,
                'temp_files': temp_files,
                'deleted_files': deleted_files,
                'elapsed': time.time() - started
            }
        except Exception as e:
            return {
                'success': False,
                'solid_geometry': [],
                'warnings': ['PDF raster vectorization failed: %s' % str(e)],
                'available_dependencies': available,
                'missing_dependencies': [],
                'page': page_index,
                'file': pdf_filename,
                'stages': stages,
                'temp_files': temp_files,
                'deleted_files': deleted_files,
                'elapsed': time.time() - started
            }
        finally:
            for filename in list(temp_files):
                try:
                    if os.path.exists(filename):
                        os.remove(filename)
                        deleted_files.append(filename)
                except Exception:
                    pass
