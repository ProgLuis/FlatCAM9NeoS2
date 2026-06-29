# ##########################################################
# FlatCAM 9 Neo S2
# PDF as Geometry Object Tool
# ##########################################################

from PyQt5 import QtWidgets

from appTool import AppTool
from appParsers.PDFContentAnalyzer import analyze_pdf_source
from appParsers.PDFGeometryBuilder import PDFGeometryBuilder
from appParsers.PDFRasterVectorizer import PDFRasterVectorizer

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


class ToolPDFGeometry(AppTool):
    toolName = _("PDF as Geometry Object")

    def __init__(self, app):
        AppTool.__init__(self, app)
        self.app = app

    def run(self, toggle=True):
        self.app.defaults.report_usage("ToolPDFGeometry()")
        self.on_open_pdf_geometry_click()

    def install(self, icon=None, separator=None, **kwargs):
        AppTool.install(self, icon, separator, shortcut='Ctrl+Shift+Q', **kwargs)

    def on_open_pdf_geometry_click(self):
        pdf_filter = "Adobe PDF Files (*.pdf);;All Files (*.*)"
        try:
            filenames, _f = QtWidgets.QFileDialog.getOpenFileNames(
                caption=_("Open PDF as Geometry"),
                directory=self.app.get_last_folder(),
                filter=pdf_filter
            )
        except TypeError:
            filenames, _f = QtWidgets.QFileDialog.getOpenFileNames(
                caption=_("Open PDF as Geometry"),
                filter=pdf_filter
            )

        if len(filenames) == 0:
            self.app.inform.emit('[WARNING_NOTCL] %s.' % _("Open PDF as Geometry cancelled"))
            return

        for filename in filenames:
            if filename:
                try:
                    analysis = analyze_pdf_source(filename)
                except Exception as e:
                    self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("PDF Content Analyzer failed"), str(e)))
                    continue

                self.emit_analysis_summary(analysis)
                pages = analysis.get('pages') or 1
                page_number = 1

                if pages > 1:
                    page_number, accepted = QtWidgets.QInputDialog.getInt(
                        None,
                        _("PDF Page Selection"),
                        _("Select PDF page to import:"),
                        1,
                        1,
                        int(pages),
                        1
                    )
                    if not accepted:
                        self.app.inform.emit('[WARNING_NOTCL] %s.' % _("PDF as Geometry cancelled"))
                        continue

                self.app.worker_task.emit({
                    'fcn': self.open_pdf_as_geometry,
                    'params': [filename, analysis, page_number]
                })

    def emit_analysis_summary(self, analysis):
        source = analysis.get('source') or 'unknown'
        content = analysis.get('content_type') or 'unknown'
        confidence = analysis.get('confidence') or 0.0
        pages = analysis.get('pages') if analysis.get('pages') is not None else 'unknown'
        self.app.inform.emit(
            "PDF Content Analyzer: Source: %s; Content: %s; Confidence: %.2f; Pages: %s" %
            (source, content, confidence, pages)
        )

    def open_pdf_as_geometry(self, filename, analysis=None, page_number=1):
        if analysis is None:
            try:
                analysis = analyze_pdf_source(filename)
            except Exception as e:
                self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("PDF Content Analyzer failed"), str(e)))
                return
            self.emit_analysis_summary(analysis)

        pages = analysis.get('pages')
        if pages and pages > 1:
            self.app.inform.emit(
                '[WARNING_NOTCL] %s' %
                _("Multi-page PDF detected. Only the selected page will be processed.")
            )

        content_type = analysis.get('content_type')
        outname = filename.split('/')[-1].split('\\')[-1]

        if content_type == 'raster':
            result = PDFRasterVectorizer(app=self.app).vectorize_pdf(filename, page_number=page_number)
            for warning in result.get('warnings', []):
                self.app.inform.emit('[WARNING_NOTCL] %s' % warning)
            if result.get('success'):
                solid_geometry = result.get('solid_geometry')

                def obj_init(geo_obj, app_obj):
                    geo_obj.solid_geometry = solid_geometry
                    geo_obj.multigeo = False

                ret = self.app.app_obj.new_object("geometry", outname, obj_init, autoselected=False)
                if ret == 'fail':
                    self.app.inform.emit('[ERROR_NOTCL] %s' % _("PDF raster as Geometry import failed."))
                    return

                self.app.file_opened.emit("geometry", filename)
                self.app.inform.emit('[success] %s: %s' % (_("Opened"), filename))
            else:
                self.app.inform.emit('[ERROR_NOTCL] %s' % _("PDF raster as Geometry import failed."))
            return

        if content_type == 'unknown':
            self.app.inform.emit(
                '[WARNING_NOTCL] %s' %
                _("PDF content is unknown. PDF as Geometry import was cancelled for safety.")
            )
            return

        if content_type == 'mixed':
            self.app.inform.emit(
                '[WARNING_NOTCL] %s' %
                _("Mixed vector/raster PDF detected. Only vector geometry will be imported in this phase.")
            )

        vector_ops = analysis.get('vector_operator_counts') or {}
        vector_complexity = sum(
            vector_ops.get(op, 0)
            for op in ('m', 'l', 'c', 'v', 'y', 'h', 're', 'S', 's', 'f', 'F', 'B', 'b', 'W')
        )
        if vector_complexity > 15000:
            self.app.inform.emit(
                '[WARNING_NOTCL] %s' %
                _("PDF vector content is too complex for the initial PDF as Geometry MVP. Operation cancelled.")
            )
            return

        builder = PDFGeometryBuilder(app=self.app)
        with self.app.proc_container.new(_("Importing PDF as Geometry ...")):
            result = builder.parse_vector_pdf(filename, page_number=page_number, page_count=pages)

            for warning in result.get('warnings', []):
                self.app.inform.emit('[WARNING_NOTCL] %s' % warning)

            if not result.get('success'):
                self.app.inform.emit('[ERROR_NOTCL] %s' % _("PDF as Geometry import failed."))
                return

            solid_geometry = result.get('solid_geometry') or []
            follow_geometry = result.get('follow_geometry') or []

            def obj_init(geo_obj, app_obj):
                geo_obj.solid_geometry = solid_geometry if solid_geometry else follow_geometry
                geo_obj.follow_geometry = follow_geometry
                geo_obj.multigeo = False

            ret = self.app.app_obj.new_object("geometry", outname, obj_init, autoselected=False)
            if ret == 'fail':
                self.app.inform.emit('[ERROR_NOTCL] %s' % _("PDF as Geometry import failed."))
                return

            self.app.file_opened.emit("geometry", filename)
            self.app.inform.emit('[success] %s: %s' % (_("Opened"), filename))
