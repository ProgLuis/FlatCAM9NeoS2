# ##########################################################
# FlatCAM 9 Neo S2
# PDF raster vectorization entry point
# ##########################################################


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

    def vectorize_pdf(self, pdf_filename, page=0):
        available, missing = self.dependency_status()
        if missing:
            return {
                'success': False,
                'geometry': [],
                'warnings': [
                    'Raster PDF detected, but raster vectorization dependencies are not available. '
                    'Required: PyMuPDF, Pillow, potracer, svgwrite. Missing: %s.' % ', '.join(missing)
                ],
                'available_dependencies': available,
                'missing_dependencies': missing,
                'page': page,
                'file': pdf_filename
            }

        return {
            'success': False,
            'geometry': [],
            'warnings': [
                'Raster PDF vectorization pipeline is reserved for a future phase. '
                'Use external image cleanup software before importing if the raster requires cleanup.'
            ],
            'available_dependencies': available,
            'missing_dependencies': [],
            'page': page,
            'file': pdf_filename
        }
