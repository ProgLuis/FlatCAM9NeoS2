# ##########################################################
# FlatCAM 9 Neo S2
# PDF as Gerber Object builder
# ##########################################################

import re
import zlib

from appParsers.ParsePDF import PdfParser
from appParsers.PDFGeometryBuilder import PDFGeometryBuilder
from appParsers.PDFImportLimits import (
    PDF_WARN_VECTOR_OPS,
    PDF_MAX_VECTOR_OPS,
    PDF_HIGH_COMPLEXITY_MESSAGE,
    PDF_TOO_COMPLEX_MESSAGE
)


class PDFGerberBuilder:
    """
    Build the parsed Gerber/Excellon PDF structures used by ToolPDFGerber.

    This class intentionally keeps the legacy ParsePDF parser untouched. It
    only prepares the vector stream text that ParsePDF already expects.
    """

    stream_re = re.compile(
        b'(<<.*?>>)?\\s*stream\\r?\\n(.*?)\\r?\\nendstream',
        re.S
    )
    pdf_ops = [' m', ' l', ' c', ' re', ' S', ' s', ' f', ' F', ' B', ' b', ' q', ' Q', ' cm']
    vector_ops = [' m', ' l', ' c', ' re', ' S', ' s', ' f', ' F', ' B', ' b']
    max_vector_ops = PDF_MAX_VECTOR_OPS

    def __init__(self, app):
        self.app = app
        self.parser = PdfParser(app=self.app)

    @staticmethod
    def has_cmyk_operators(pdf_bytes):
        return re.search(rb'(\s|^)([0-9.]+\s+){4}[kK](\s|$)', pdf_bytes) is not None

    def extract_vector_streams(self, pdf_bytes):
        decompressed = ''
        stream_nr = 0

        for stream_header, stream in re.findall(self.stream_re, pdf_bytes):
            if self.app.abort_flag:
                break

            stream_nr += 1
            stream = stream.strip(b'\r\n')
            try:
                if b'FlateDecode' in stream_header:
                    try:
                        raw_stream = zlib.decompress(stream)
                    except Exception:
                        continue
                else:
                    raw_stream = stream

                try:
                    decoded_stream = raw_stream.decode('utf-8')
                except UnicodeDecodeError:
                    decoded_stream = raw_stream.decode('latin-1', errors='ignore')

                if not any(op in decoded_stream for op in self.pdf_ops):
                    continue

                decompressed += decoded_stream + '\r\n'
            except Exception:
                continue

        return decompressed

    def count_vector_ops(self, pdf_content):
        return sum((pdf_content or '').count(op) for op in self.vector_ops)

    def count_crop_vector_ops(self, pdf_filename, page_number=1, crop_rect=None):
        geometry_builder = PDFGeometryBuilder(app=self.app)
        return geometry_builder.estimate_vector_complexity(
            pdf_filename,
            page_number=page_number,
            page_count=1,
            crop_rect=crop_rect
        )

    @staticmethod
    def _geometry_entry(geometry, key):
        if geometry is None or getattr(geometry, 'is_empty', False):
            return None
        return {key: geometry}

    def parse_crop(self, pdf_filename, page_number=1, crop_rect=None):
        vector_ops = self.count_crop_vector_ops(
            pdf_filename,
            page_number=page_number,
            crop_rect=crop_rect
        )
        if vector_ops > self.max_vector_ops:
            return {
                'success': False,
                'parsed_pdf': {},
                'pdf_content': '',
                'vector_ops': vector_ops,
                'warning': (
                    'The selected PDF crop contains approximately %s vector operations. '
                    '%s'
                ) % (vector_ops, PDF_TOO_COMPLEX_MESSAGE)
            }

        geometry_builder = PDFGeometryBuilder(app=self.app)
        solid_geometry, follow_geometry = geometry_builder.drawings_to_geometry(
            pdf_filename,
            page_number=page_number,
            crop_rect=crop_rect
        )

        geometry = []
        for solid in solid_geometry or []:
            entry = self._geometry_entry(solid, 'solid')
            if entry:
                geometry.append(entry)
        for follow in follow_geometry or []:
            entry = self._geometry_entry(follow, 'follow')
            if entry:
                geometry.append(entry)

        if not geometry:
            return {
                'success': False,
                'parsed_pdf': {},
                'pdf_content': '',
                'vector_ops': vector_ops,
                'warning': 'No Gerber-compatible PDF geometry was produced inside the selected crop.'
            }

        return {
            'success': True,
            'parsed_pdf': {
                1: {
                    '0': {
                        'size': 0.0,
                        'type': 'C',
                        'geometry': geometry
                    }
                }
            },
            'pdf_content': '',
            'vector_ops': vector_ops,
            'warning': PDF_HIGH_COMPLEXITY_MESSAGE if vector_ops > PDF_WARN_VECTOR_OPS else None
        }

    def parse_file(self, pdf_filename, page_number=1, crop_rect=None):
        if crop_rect is not None:
            return self.parse_crop(
                pdf_filename,
                page_number=page_number,
                crop_rect=crop_rect
            )

        with open(pdf_filename, "rb") as pdf_file:
            pdf_bytes = pdf_file.read()

        if self.has_cmyk_operators(pdf_bytes):
            return {
                'success': False,
                'parsed_pdf': {},
                'pdf_content': '',
                'warning': 'CMYK PDF detected. FlatCAM currently supports RGB vector PDFs only.'
            }

        pdf_content = self.extract_vector_streams(pdf_bytes)
        if not pdf_content.strip():
            return {
                'success': False,
                'parsed_pdf': {},
                'pdf_content': pdf_content,
                'warning': 'No vector PDF content found. This PDF may be raster/image based.'
            }

        if not any(op in pdf_content for op in self.vector_ops):
            return {
                'success': False,
                'parsed_pdf': {},
                'pdf_content': pdf_content,
                'warning': 'No usable vector geometry found. This PDF may be raster/image based.'
            }

        vector_ops = self.count_vector_ops(pdf_content)
        if vector_ops > self.max_vector_ops:
            return {
                'success': False,
                'parsed_pdf': {},
                'pdf_content': pdf_content,
                'vector_ops': vector_ops,
                'warning': (
                    'The selected PDF page contains approximately %s vector operations. '
                    '%s'
                ) % (vector_ops, PDF_TOO_COMPLEX_MESSAGE)
            }

        parsed_pdf = self.parser.parse_pdf(pdf_content=pdf_content)
        if not parsed_pdf:
            return {
                'success': False,
                'parsed_pdf': {},
                'pdf_content': pdf_content,
                'warning': 'PDF contains no usable vector geometry.'
            }

        has_geometry = False
        for layer in parsed_pdf:
            if parsed_pdf[layer]:
                has_geometry = True
                break

        if has_geometry is False:
            return {
                'success': False,
                'parsed_pdf': {},
                'pdf_content': pdf_content,
                'warning': 'PDF has no drawable vector data.'
            }

        return {
            'success': True,
            'parsed_pdf': parsed_pdf,
            'pdf_content': pdf_content,
            'vector_ops': vector_ops,
            'warning': PDF_HIGH_COMPLEXITY_MESSAGE if vector_ops > PDF_WARN_VECTOR_OPS else None
        }
