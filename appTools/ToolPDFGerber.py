# ##########################################################
# FlatCAM: 2D Post-processing for Manufacturing            #
# File Author: Marius Adrian Stanciu (c)                   #
# Date: 4/23/2019                                          #
# MIT Licence                                              #
# ##########################################################

# ########################################################## ##
# FlatCAM 9 Neo S2                                            #
# Shapely 2.x Friendly Edition                                #
# Community modernized fork                                   #
# Maintained by Luis Enrique Yacupoma Aguirre                 #
# Date: 01/06/2026                                            #
# https://github.com/ProgLuis/FlatCAM9NeoS2                   #
# ########################################################## ##

from PyQt5 import QtWidgets, QtCore

from appTool import AppTool

from appParsers.ParsePDF import grace
from appParsers.PDFContentAnalyzer import analyze_pdf_source
from appParsers.PDFGerberBuilder import PDFGerberBuilder
from appParsers.PDFPreparedInput import prepare_pdf_page, remove_prepared_pdf
from appParsers.PDFSourceAdvisor import advise_pdf_source
from appTools.PDFImportAssistant import PDFImportAssistantDialog
from shapely.geometry import Point, MultiPolygon, Polygon
from shapely.ops import unary_union

from copy import deepcopy

import time
import logging
import traceback

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

log = logging.getLogger('base')


class ToolPDFGerber(AppTool):
    """
    Parse a PDF file.
    Reference here: https://www.adobe.com/content/dam/acom/en/devnet/pdf/pdfs/pdf_reference_archives/PDFReference.pdf
    Return a list of geometries
    """
    toolName = _("PDF as Gerber Object")

    def __init__(self, app):
        AppTool.__init__(self, app)
        self.app = app
        self.decimals = self.app.decimals
       
        self.pdf_decompressed = {}

        # key = file name and extension
        # value is a dict to store the parsed content of the PDF
        self.pdf_parsed = {}

        # QTimer for periodic check
        self.check_thread = QtCore.QTimer()

        # Every time a parser is started we add a promise; every time a parser finished we remove a promise
        # when empty we start the layer rendering
        self.parsing_promises = []

        self.builder = PDFGerberBuilder(app=self.app)


    @staticmethod
    def iter_geom(geometry):
        if geometry is None:
            return []

        if getattr(geometry, 'is_empty', False):
            return []

        if isinstance(geometry, (list, tuple)):
            geo_list = []
            for geo in geometry:
                geo_list += ToolPDFGerber.iter_geom(geo)
            return geo_list

        if hasattr(geometry, 'geoms'):
            geo_list = []
            for geo in geometry.geoms:
                geo_list += ToolPDFGerber.iter_geom(geo)
            return geo_list

        return [geometry]

    def emit_pdf_content_analysis(self, filename):
        try:
            analysis = analyze_pdf_source(filename)
        except Exception as e:
            self.app.inform.emit(
                '[WARNING_NOTCL] %s: %s' % (_("PDF Content Analyzer failed"), str(e))
            )
            return

        source_names = {
            'illustrator': 'Adobe Illustrator',
            'coreldraw': 'CorelDRAW',
            'proteus': 'Proteus',
            'unknown': 'Unknown'
        }
        content_names = {
            'vector': 'vector',
            'raster': 'raster',
            'mixed': 'mixed',
            'unknown': 'unknown'
        }

        source = source_names.get(analysis.get('source'), analysis.get('source') or 'Unknown')
        content = content_names.get(analysis.get('content_type'), analysis.get('content_type') or 'unknown')
        confidence = analysis.get('confidence')
        pages = analysis.get('pages')

        summary = "PDF Content Analyzer: Source: %s; Content: %s; Confidence: %.2f; Pages: %s" % (
            source, content, confidence if confidence is not None else 0.0, pages if pages is not None else 'unknown'
        )
        self.app.inform.emit(summary)

        if pages and pages > 1:
            self.app.inform.emit(
                '[WARNING_NOTCL] %s' %
                _("Multi-page PDF detected. PDF Import Assistant will process only the selected page.")
            )

        warnings = analysis.get('warnings') or []
        warning = None
        for candidate in warnings:
            if candidate.startswith('Some compressed PDF streams could not be decoded'):
                continue
            if candidate.startswith('Transparency resources detected') and content == 'vector':
                continue
            warning = candidate
            break
        if warning:
            self.app.inform.emit('[WARNING_NOTCL] %s' % warning)

        recommendations = analysis.get('recommendations') or []
        if recommendations:
            self.app.inform.emit("PDF Content Analyzer recommendation: %s" % recommendations[0])

        try:
            advice = advise_pdf_source(analysis)
            self.app.inform.emit(advice.get('message'))
        except Exception:
            pass

        return analysis

    def run(self, toggle=True):
        self.app.defaults.report_usage("ToolPDFGerber()")

        self.set_tool_ui()
        self.on_open_pdf_click()

    def install(self, icon=None, separator=None, **kwargs):
        AppTool.install(self, icon, separator, shortcut='Ctrl+Q', **kwargs)

    def set_tool_ui(self):
        pass

    def on_open_pdf_click(self):
        """
        File menu callback for opening an PDF file.

        :return: None
        """

        self.app.defaults.report_usage("ToolPDFGerber.on_open_pdf_click()")
        self.app.log.debug("ToolPDFGerber.on_open_pdf_click()")

        _filter_ = "Adobe PDF Files (*.pdf);;" \
                   "All Files (*.*)"

        try:
            filenames, _f = QtWidgets.QFileDialog.getOpenFileNames(caption=_("Open PDF"),
                                                                   directory=self.app.get_last_folder(),
                                                                   filter=_filter_)
        except TypeError:
            filenames, _f = QtWidgets.QFileDialog.getOpenFileNames(caption=_("Open PDF"), filter=_filter_)

        if len(filenames) == 0:
            self.app.inform.emit('[WARNING_NOTCL] %s.' % _("Open PDF cancelled"))
        else:
            queued = False
            for filename in filenames:
                if filename != '':
                    analysis = self.emit_pdf_content_analysis(filename)
                    if analysis is None:
                        continue

                    content_type = analysis.get('content_type')
                    if content_type == 'raster':
                        self.app.inform.emit(
                            '[WARNING_NOTCL] %s' %
                            _("Raster PDF detected. PDF as Gerber Object does not run the raster pipeline in this phase. "
                              "Use PDF as Geometry Object / Raster Pipeline or export native Gerber/Excellon when possible.")
                        )
                        continue

                    if content_type == 'mixed':
                        self.app.inform.emit(
                            '[WARNING_NOTCL] %s' %
                            _("Mixed vector/raster PDF detected. Raster images may be ignored by PDF as Gerber Object.")
                        )

                    try:
                        advisor = advise_pdf_source(analysis)
                    except Exception:
                        advisor = {}

                    assistant = PDFImportAssistantDialog(
                        app=self.app,
                        filename=filename,
                        analysis=analysis,
                        advisor=advisor
                    )
                    if assistant.exec_() != QtWidgets.QDialog.Accepted:
                        self.app.inform.emit('[WARNING_NOTCL] %s.' % _("PDF as Gerber Object cancelled"))
                        continue

                    page_number = assistant.page_number
                    crop_rect = assistant.crop_rect
                    if crop_rect is None:
                        try:
                            prepared_filename, temp_filename = prepare_pdf_page(
                                filename,
                                page_number=page_number,
                                page_count=analysis.get('pages') or 1,
                                crop_rect=None
                            )
                        except Exception as e:
                            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("PDF preparation failed"), str(e)))
                            continue
                    else:
                        prepared_filename = filename
                        temp_filename = None

                    queued = True
                    self.app.worker_task.emit({'fcn': self.open_pdf,
                                               'params': [prepared_filename, filename, temp_filename, page_number, crop_rect]})

            if queued:
                # start the parsing timer with a period of 1 second
                self.periodic_check(1000)

    def open_pdf(self, filename, display_filename=None, cleanup_filename=None, page_number=1, crop_rect=None):
        display_filename = display_filename or filename
        short_name = display_filename.split('/')[-1].split('\\')[-1]
        self.parsing_promises.append(short_name)



        log.debug("ToolPDFGerber.open_pdf() --> started for: %s" % short_name)
        log.debug("ToolPDFGerber.open_pdf() --> promises: %s" % str(self.parsing_promises))


    

        self.pdf_parsed[short_name] = {
            'pdf': {},
            'filename': display_filename
        }

        self.pdf_decompressed[short_name] = ''

        def cleanup_prepared_input():
            if cleanup_filename:
                removed = remove_prepared_pdf(cleanup_filename)
                if removed:
                    self.app.inform.emit("Temporary prepared PDF removed.")

        if self.app.abort_flag:
            # graceful abort requested by the user
            cleanup_prepared_input()
            raise grace

        with self.app.proc_container.new(_("Parsing ...")):
            try:
                result = self.builder.parse_file(
                    filename,
                    page_number=page_number,
                    crop_rect=crop_rect
                )
            except Exception as e:
                log.debug("ToolPDFGerber.open_pdf() --> parse failed: %s" % str(e))
                self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("PDF as Gerber parse failed"), str(e)))
                cleanup_prepared_input()
                self.remove_parsing_promise(short_name)
                return

            if not result.get('success'):
                warning = result.get('warning') or 'PDF contains no usable vector geometry.'
                log.debug("ToolPDFGerber.open_pdf() --> %s" % warning)
                self.app.inform.emit('[WARNING_NOTCL] %s' % _(warning))
                cleanup_prepared_input()
                self.remove_parsing_promise(short_name)
                return

            self.pdf_decompressed[short_name] = result.get('pdf_content') or ''
            self.pdf_parsed[short_name]['pdf'] = result.get('parsed_pdf') or {}
            log.debug("ToolPDFGerber.open_pdf() --> parse_pdf() finished")
            self.pdf_decompressed[short_name] = ''

        cleanup_prepared_input()

        # removal from list is done in a multithreaded way therefore not always the removal can be done
        # try to remove until it's done
        try:
            while True:
                self.parsing_promises.remove(short_name)
                time.sleep(0.1)
        except Exception as e:
            log.debug("ToolPDFGerber.open_pdf() --> %s" % str(e))
        self.app.inform.emit('[success] %s: %s' % (_("Opened"),  str(display_filename)))

    def remove_parsing_promise(self, short_name):
        try:
            while True:
                self.parsing_promises.remove(short_name)
                time.sleep(0.1)
        except Exception:
            pass

    def layer_rendering_as_excellon(self, filename, ap_dict, layer_nr):
        outname = filename.split('/')[-1].split('\\')[-1] + "_%s" % str(layer_nr)

        # store the points here until reconstitution:
        # keys are diameters and values are list of (x,y) coords
        points = {}

        def obj_init(exc_obj, app_obj):
            clear_geo = [geo_el['clear'] for geo_el in ap_dict['0']['geometry']]

            for geo in clear_geo:
                xmin, ymin, xmax, ymax = geo.bounds
                center = (((xmax - xmin) / 2) + xmin, ((ymax - ymin) / 2) + ymin)

                # for drill bits, even in INCH, it's enough 3 decimals
                correction_factor = 0.974
                dia = (xmax - xmin) * correction_factor
                dia = round(dia, 3)
                if dia in points:
                    points[dia].append(center)
                else:
                    points[dia] = [center]

            sorted_dia = sorted(points.keys())

            name_tool = 0
            for dia in sorted_dia:
                name_tool += 1
                tool = str(name_tool)

                exc_obj.tools[tool] = {
                    'tooldia': dia,
                    'drills': [],
                    'solid_geometry': []
                }

                # update the drill list
                for dia_points in points:
                    if dia == dia_points:
                        for pt in points[dia_points]:
                            exc_obj.tools[tool]['drills'].append(Point(pt))
                        break

            ret = exc_obj.create_geometry()
            if ret == 'fail':
                log.debug("Could not create geometry for Excellon object.")
                return "fail"

            for tool in exc_obj.tools:
                if exc_obj.tools[tool]['solid_geometry']:
                    return
            app_obj.inform.emit('[ERROR_NOTCL] %s: %s' % (_("No geometry found in file"), outname))
            return "fail"

        with self.app.proc_container.new(_("Rendering PDF layer #%d ...") % int(layer_nr)):

            ret_val = self.app.app_obj.new_object("excellon", outname, obj_init, autoselected=False)
            if ret_val == 'fail':
                self.app.inform.emit('[ERROR_NOTCL] %s' % _('Open PDF file failed.'))
                return
            # Register recent file
            self.app.file_opened.emit("excellon", filename)
            # GUI feedback
            self.app.inform.emit('[success] %s: %s' % (_("Rendered"),  outname))

    def layer_rendering_as_gerber(self, filename, ap_dict, layer_nr):
        outname = filename.split('/')[-1].split('\\')[-1] + "_%s" % str(layer_nr)

        def obj_init(grb_obj, app_obj):

            grb_obj.apertures = ap_dict

            poly_buff = []
            follow_buf = []
            for ap in grb_obj.apertures:
                for k in grb_obj.apertures[ap]:
                    if k == 'geometry':
                        for geo_el in ap_dict[ap][k]:
                            if 'solid' in geo_el:
                                poly_buff.append(geo_el['solid'])
                            if 'follow' in geo_el:
                                follow_buf.append(geo_el['follow'])
            poly_buff = unary_union(poly_buff)

            if '0' in grb_obj.apertures:
                global_clear_geo = []
                if 'geometry' in grb_obj.apertures['0']:
                    for geo_el in ap_dict['0']['geometry']:
                        if 'clear' in geo_el:
                            global_clear_geo.append(geo_el['clear'])

                if global_clear_geo:
                    solid = []
                    for apid in grb_obj.apertures:
                        if 'geometry' in grb_obj.apertures[apid]:
                            for elem in grb_obj.apertures[apid]['geometry']:
                                if 'solid' in elem:
                                    solid_geo = deepcopy(elem['solid'])
                                    for clear_geo in global_clear_geo:
                                        # Make sure that the clear_geo is within the solid_geo otherwise we loose
                                        # the solid_geometry. We want for clear_geometry just to cut into solid_geometry
                                        # not to delete it
                                        if clear_geo.within(solid_geo):
                                            solid_geo = solid_geo.difference(clear_geo)
                                        if solid_geo.is_empty:
                                            solid_geo = elem['solid']
                                    for poly in self.iter_geom(solid_geo):
                                        if poly is None or poly.is_empty:
                                            continue
                                        if isinstance(poly, Polygon):
                                            solid.append(poly)    
                   
                    if solid:
                        poly_buff = deepcopy(MultiPolygon(solid))
                    else:
                        poly_buff = deepcopy(poly_buff)

            follow_buf = unary_union(follow_buf)

            try:
                poly_buff = poly_buff.buffer(0.0000001)
            except ValueError:
                pass
            try:
                poly_buff = poly_buff.buffer(-0.0000001)
            except ValueError:
                pass

            grb_obj.solid_geometry = deepcopy(poly_buff)
            grb_obj.follow_geometry = deepcopy(follow_buf)

        with self.app.proc_container.new(_("Rendering PDF layer #%d ...") % int(layer_nr)):

            ret = self.app.app_obj.new_object('gerber', outname, obj_init, autoselected=False)
            if ret == 'fail':
                self.app.inform.emit('[ERROR_NOTCL] %s' % _('Open PDF file failed.'))
                return
            # Register recent file
            self.app.file_opened.emit('gerber', filename)
            # GUI feedback
            self.app.inform.emit('[success] %s: %s' % (_("Rendered"), outname))

    def periodic_check(self, check_period):
        """
        This function starts an QTimer and it will periodically check if parsing was done

        :param check_period: time at which to check periodically if all plots finished to be plotted
        :return:
        """

        # self.plot_thread = threading.Thread(target=lambda: self.check_plot_finished(check_period))
        # self.plot_thread.start()
        log.debug("ToolPDFGerber --> Periodic Check started.")

        try:
            self.check_thread.stop()
        except TypeError:
            pass

        self.check_thread.setInterval(check_period)
        try:
            self.check_thread.timeout.disconnect(self.periodic_check_handler)
        except (TypeError, AttributeError):
            pass

        self.check_thread.timeout.connect(self.periodic_check_handler)
        self.check_thread.start(QtCore.QThread.HighPriority)

    def periodic_check_handler(self):
        """
        If the parsing worker finished then start multithreaded rendering
        :return:
        """
        # log.debug("checking parsing --> %s" % str(self.parsing_promises))

        try:
            if not self.parsing_promises:
                self.check_thread.stop()
                log.debug("PDF --> start rendering")
                # parsing finished start the layer rendering
                if self.pdf_parsed:
                    obj_to_delete = []
                    for object_name in self.pdf_parsed:
                        if self.app.abort_flag:
                            # graceful abort requested by the user
                            raise grace

                        filename = deepcopy(self.pdf_parsed[object_name]['filename'])
                        pdf_content = deepcopy(self.pdf_parsed[object_name]['pdf'])
                        obj_to_delete.append(object_name)
                        for k in pdf_content:
                            if self.app.abort_flag:
                                # graceful abort requested by the user
                                raise grace

                            ap_dict = pdf_content[k]
                            print(k, ap_dict)
                            if ap_dict:
                                layer_nr = k
                                if k == 0:
                                    self.app.worker_task.emit({'fcn': self.layer_rendering_as_excellon,
                                                               'params': [filename, ap_dict, layer_nr]})
                                else:
                                    self.app.worker_task.emit({'fcn': self.layer_rendering_as_gerber,
                                                               'params': [filename, ap_dict, layer_nr]})
                    # delete the object already processed so it will not be processed again for other objects
                    # that were opened at the same time; like in drag & drop on appGUI
                    for obj_name in obj_to_delete:
                        if obj_name in self.pdf_parsed:
                            self.pdf_parsed.pop(obj_name)

                log.debug("ToolPDFGerber --> Periodic check finished.")
        except Exception:
            traceback.print_exc()
