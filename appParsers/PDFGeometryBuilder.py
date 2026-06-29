# ##########################################################
# FlatCAM 9 Neo S2
# PDF as Geometry adapter
# ##########################################################

from copy import deepcopy
import zlib

from shapely.geometry import Polygon, MultiPolygon
from shapely.ops import unary_union

from appParsers.ParsePDF import PdfParser


class PDFGeometryBuilder:
    """
    Convert supported vector PDF content into Shapely geometry.

    This adapter deliberately does not create Gerber apertures or Excellon
    objects. It reuses the existing PDF parser output as an intermediate
    representation and extracts Geometry Object friendly Shapely geometry.
    """

    def __init__(self, app):
        self.app = app
        self.parser = PdfParser(app=self.app)

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

    def extract_pdf_vector_streams(self, pdf_filename):
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

    def parse_vector_pdf(self, pdf_filename):
        pdf_content = self.extract_pdf_vector_streams(pdf_filename)
        if not pdf_content.strip():
            return {
                'success': False,
                'solid_geometry': [],
                'follow_geometry': [],
                'warnings': ['No usable vector PDF content found.']
            }

        vector_ops = [' m', ' l', ' c', ' re', ' S', ' s', ' f', ' F', ' B', ' b']
        if not any(op in pdf_content for op in vector_ops):
            return {
                'success': False,
                'solid_geometry': [],
                'follow_geometry': [],
                'warnings': ['No drawable vector PDF operators found.']
            }

        parsed_pdf = self.parser.parse_pdf(pdf_content=pdf_content)
        if not parsed_pdf:
            return {
                'success': False,
                'solid_geometry': [],
                'follow_geometry': [],
                'warnings': ['PDF vector parser returned no geometry.']
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

        if not solid_geometry and not follow_geometry:
            return {
                'success': False,
                'solid_geometry': [],
                'follow_geometry': [],
                'warnings': ['No Geometry Object compatible PDF geometry was produced.']
            }

        return {
            'success': True,
            'solid_geometry': solid_geometry,
            'follow_geometry': follow_geometry,
            'warnings': []
        }
