# ##########################################################
# FlatCAM 9 Neo S2
# PDF as Geometry Object Tool
# ##########################################################

from PyQt5 import QtWidgets
from shapely.geometry import Point

from appTool import AppTool
from appParsers.PDFContentAnalyzer import analyze_pdf_source
from appParsers.PDFGeometryBuilder import PDFGeometryBuilder
from appParsers.PDFImportTransform import (
    get_pdf_import_origin,
    apply_pdf_import_transform_to_geometry_list,
    apply_pdf_import_transform_to_point
)
from appParsers.PDFRasterVectorizer import PDFRasterVectorizer
from appParsers.PDFSourceAdvisor import advise_pdf_source
from appParsers.PDFWhiteDrillDetector import detect_white_pdf_drills
from appTools.PDFImportAssistant import PDFImportAssistantDialog

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
                try:
                    advisor = advise_pdf_source(analysis)
                except Exception:
                    advisor = {}

                assistant = PDFImportAssistantDialog(
                    app=self.app,
                    filename=filename,
                    analysis=analysis,
                    advisor=advisor,
                    enable_white_drill_extraction=True
                )
                if assistant.exec_() != QtWidgets.QDialog.Accepted:
                    self.app.inform.emit('[WARNING_NOTCL] %s.' % _("PDF as Geometry cancelled"))
                    continue

                page_number = assistant.page_number
                crop_rect = assistant.crop_rect
                extract_white_drills = assistant.extract_white_drills
                flip_horizontal = assistant.flip_horizontal
                flip_vertical = assistant.flip_vertical

                self.app.worker_task.emit({
                    'fcn': self.open_pdf_as_geometry,
                    'params': [
                        filename,
                        analysis,
                        page_number,
                        crop_rect,
                        extract_white_drills,
                        flip_horizontal,
                        flip_vertical
                    ]
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
        try:
            advice = advise_pdf_source(analysis)
            self.app.inform.emit(advice.get('message'))
        except Exception:
            pass

    def emit_white_drill_report(self, filename, analysis, page_number=1, crop_rect=None):
        content_type = (analysis.get('content_type') or 'unknown').lower()
        if content_type not in ['vector', 'mixed']:
            return None

        try:
            drill_result = detect_white_pdf_drills(
                filename,
                page_number=page_number,
                crop_rect=crop_rect
            )
        except Exception as e:
            self.app.inform.emit('[WARNING_NOTCL] PDF White Drill Detector failed: %s' % str(e))
            return None

        for warning in drill_result.get('warnings', []):
            self.app.inform.emit('[WARNING_NOTCL] %s' % warning)

        candidate_count = drill_result.get('candidate_count', 0)
        if candidate_count <= 0:
            self.app.inform.emit("PDF White Drill Detector: no white circular drill candidates found.")
            return drill_result

        self.app.inform.emit(
            "PDF White Drill Detector: %d candidate drill(s) found." % candidate_count
        )

        tool_messages = []
        tools = drill_result.get('tools', {})
        for diameter_key in sorted(tools, key=lambda key: float(key)):
            tool_messages.append(
                "%.4f mm: %d" % (float(diameter_key), len(tools[diameter_key]))
            )

        if tool_messages:
            self.app.inform.emit("PDF White Drill Detector tools: %s" % '; '.join(tool_messages))

        source = (analysis.get('source') or '').lower()
        if content_type == 'mixed' or source in ['coreldraw', 'corel']:
            self.app.inform.emit(
                '[WARNING_NOTCL] %s' %
                _("PDF White Drill Detector: candidates found in mixed/CorelDRAW PDF. "
                  "Verify manually before future Excellon extraction.")
            )
        return drill_result

    def import_pdf_white_drills(self, filename, drill_result, outname=None, plot=True,
                                transform_origin=None, flip_horizontal=False, flip_vertical=False):
        tools = drill_result.get('tools', {}) if drill_result else {}
        drill_count = sum(len(tool_drills) for tool_drills in tools.values())

        self.app.inform.emit("PDF White Drill Detector:")
        self.app.inform.emit("%d candidates" % drill_count)

        if not tools or drill_count == 0:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("No PDF white circular drill candidates were detected."))
            return 'fail'

        self.app.inform.emit("Creating Excellon Object...")
        self.app.inform.emit("Grouping tools...")

        name = outname or (filename.split('/')[-1].split('\\')[-1].rpartition('.')[0] + "_drills")

        excellon_tools = {}
        for index, diameter_key in enumerate(sorted(tools, key=lambda key: float(key)), start=1):
            tool_id = index
            diameter = float(diameter_key)
            excellon_tools[tool_id] = {
                'tooldia': diameter,
                'drills': [],
                'slots': [],
                'solid_geometry': []
            }
            self.app.inform.emit("Tool %d" % tool_id)
            self.app.inform.emit("%.4f mm" % diameter)
            self.app.inform.emit("%d drills" % len(tools[diameter_key]))

            for candidate in tools[diameter_key]:
                x_coord = float(candidate['x'])
                y_coord = float(candidate['y'])
                if transform_origin is not None and (flip_horizontal or flip_vertical):
                    x_coord, y_coord = apply_pdf_import_transform_to_point(
                        x_coord,
                        y_coord,
                        transform_origin,
                        flip_horizontal=flip_horizontal,
                        flip_vertical=flip_vertical
                    )
                drill_point = Point(x_coord, y_coord)
                excellon_tools[tool_id]['drills'].append(drill_point)
                excellon_tools[tool_id]['solid_geometry'].append(drill_point.buffer(diameter / 2.0))

        def obj_init(exc_obj, app_obj):
            exc_obj.tools = excellon_tools
            exc_obj.drills = [
                drill
                for tool in excellon_tools.values()
                for drill in tool.get('drills', [])
            ]
            exc_obj.create_geometry()
            exc_obj.source_file = app_obj.f_handlers.export_excellon(
                obj_name=name, local_use=exc_obj, filename=None, use_thread=False
            )
            app_obj.inform.emit("Excellon Object created successfully.")

        ret = self.app.app_obj.new_object("excellon", name, obj_init, autoselected=False, plot=plot)
        if ret == 'fail':
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("PDF white drill Excellon import failed."))
            return 'fail'

        return ret

    def open_pdf_as_geometry(self, filename, analysis=None, page_number=1, crop_rect=None,
                             extract_white_pdf_drills=False, flip_horizontal=False, flip_vertical=False):
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
            if flip_horizontal or flip_vertical:
                self.app.inform.emit(
                    '[WARNING_NOTCL] %s' %
                    _("PDF raster flip is not implemented in this phase. Raster Geometry will be imported without flip.")
                )
            result = PDFRasterVectorizer(app=self.app).vectorize_pdf(
                filename, page_number=page_number, crop_rect=crop_rect
            )
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

        white_drill_result = self.emit_white_drill_report(
            filename,
            analysis,
            page_number=page_number,
            crop_rect=crop_rect
        )

        builder = PDFGeometryBuilder(app=self.app)
        with self.app.proc_container.new(_("Importing PDF as Geometry ...")):
            exclude_drawing_indices = None
            preserve_circle_indices = None
            excluded_subpaths = None
            preserved_circle_subpaths = None
            if white_drill_result and white_drill_result.get('candidate_count', 0) > 0:
                excluded_subpaths = white_drill_result.get('accepted_drill_subpaths') or []
                if not excluded_subpaths:
                    exclude_drawing_indices = white_drill_result.get('accepted_drawing_indices') or []
            if white_drill_result:
                preserved_circle_subpaths = white_drill_result.get('preserved_circle_subpaths') or []
                if not preserved_circle_subpaths:
                    preserve_circle_indices = white_drill_result.get('preserved_circle_indices') or []

            result = builder.parse_vector_pdf(
                filename, page_number=page_number, page_count=pages, crop_rect=crop_rect, analysis=analysis,
                exclude_drawing_indices=exclude_drawing_indices,
                preserve_circle_indices=preserve_circle_indices,
                excluded_subpaths=excluded_subpaths,
                preserved_circle_subpaths=preserved_circle_subpaths
            )

            for warning in result.get('warnings', []):
                self.app.inform.emit('[WARNING_NOTCL] %s' % warning)

            if not result.get('success'):
                self.app.inform.emit('[ERROR_NOTCL] %s' % _("PDF as Geometry import failed."))
                return

            solid_geometry = result.get('solid_geometry') or []
            follow_geometry = result.get('follow_geometry') or []
            transform_origin = None
            if flip_horizontal or flip_vertical:
                try:
                    transform_origin = get_pdf_import_origin(filename, page_number=page_number, crop_rect=crop_rect)
                    solid_geometry = apply_pdf_import_transform_to_geometry_list(
                        solid_geometry,
                        transform_origin,
                        flip_horizontal=flip_horizontal,
                        flip_vertical=flip_vertical
                    )
                    follow_geometry = apply_pdf_import_transform_to_geometry_list(
                        follow_geometry,
                        transform_origin,
                        flip_horizontal=flip_horizontal,
                        flip_vertical=flip_vertical
                    )
                    self.app.inform.emit(
                        "PDF import flip applied: horizontal=%s; vertical=%s; origin=(%.4f, %.4f) mm." %
                        (bool(flip_horizontal), bool(flip_vertical), transform_origin[0], transform_origin[1])
                    )
                except Exception as e:
                    self.app.inform.emit('[WARNING_NOTCL] %s: %s' % (_("PDF import flip failed"), str(e)))
                    transform_origin = None

            def obj_init(geo_obj, app_obj):
                visible_geometry = []
                if solid_geometry:
                    visible_geometry.extend(solid_geometry)
                if follow_geometry:
                    visible_geometry.extend(follow_geometry)

                geo_obj.solid_geometry = visible_geometry
                geo_obj.follow_geometry = follow_geometry
                geo_obj.multigeo = False

            ret = self.app.app_obj.new_object("geometry", outname, obj_init, autoselected=False)
            if ret == 'fail':
                self.app.inform.emit('[ERROR_NOTCL] %s' % _("PDF as Geometry import failed."))
                return

            self.app.file_opened.emit("geometry", filename)
            self.app.inform.emit('[success] %s: %s' % (_("Opened"), filename))

            if extract_white_pdf_drills and white_drill_result and white_drill_result.get('candidate_count', 0) > 0:
                drill_name = outname.rpartition('.')[0] + "_drills"
                self.import_pdf_white_drills(
                    filename,
                    white_drill_result,
                    outname=drill_name,
                    plot=True,
                    transform_origin=transform_origin,
                    flip_horizontal=flip_horizontal,
                    flip_vertical=flip_vertical
                )
