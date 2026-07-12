# ##########################################################
# FlatCAM: 2D Post-processing for Manufacturing            #
# File Author: Marius Adrian Stanciu (c)                   #
# Date: 1/10/2020                                          #
# MIT Licence                                              #
# ##########################################################

from PyQt5 import QtWidgets, QtCore, QtGui

from appTool import AppTool
from appGUI.GUIElements import RadioSet, FCDoubleSpinner, FCCheckBox, FCComboBox

from shapely.geometry import Point, LineString, LinearRing, Polygon, MultiPolygon, GeometryCollection
import logging
import gettext
import appTranslation as fcTranslate
import builtins
import math

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

log = logging.getLogger('base')


class ToolExtractDrills(AppTool):

    def __init__(self, app):
        AppTool.__init__(self, app)
        self.decimals = self.app.decimals
        self.canvas = self.app.plotcanvas
        self.pending_circ_drills = {}
        self.pending_circ_selections = []
        self.circ_select_active = False
        self.circ_mr = None
        self.circ_geo_obj = None
        self.circ_highlight_ids = []

        # #############################################################################
        # ######################### Tool GUI ##########################################
        # #############################################################################
        self.ui = ExtractDrillsUI(layout=self.layout, app=self.app)
        self.toolName = self.ui.toolName

        # ## Signals
        self.ui.hole_size_radio.activated_custom.connect(self.on_hole_size_toggle)
        self.ui.e_drills_button.clicked.connect(self.on_extract_drills_click)
        self.ui.reset_button.clicked.connect(self.on_reset_gerber_tool)
        self.ui.circ_method_radio.activated_custom.connect(self.on_circ_method_toggle)
        self.ui.circ_select_button.clicked.connect(self.on_circumference_select_click)
        self.ui.circ_undo_button.clicked.connect(self.on_circumference_undo_last)
        self.ui.circ_clear_button.clicked.connect(self.on_circumference_clear_selection)
        self.ui.circ_generate_button.clicked.connect(self.on_generate_circ_excellon_click)
        self.ui.circ_reset_button.clicked.connect(self.on_reset_geometry_tool)
        self.ui.circ_geometry_combo.currentIndexChanged.connect(
            lambda: self.on_circ_geometry_combo_changed()
        )
        self.app.object_status_changed.connect(self.on_collection_status_changed)

        self.ui.circular_cb.stateChanged.connect(
            lambda state:
            self.ui.circular_ring_entry.setDisabled(False) if state else self.ui.circular_ring_entry.setDisabled(True)
        )

        self.ui.oblong_cb.stateChanged.connect(
            lambda state:
            self.ui.oblong_ring_entry.setDisabled(False) if state else self.ui.oblong_ring_entry.setDisabled(True)
        )

        self.ui.square_cb.stateChanged.connect(
            lambda state:
            self.ui.square_ring_entry.setDisabled(False) if state else self.ui.square_ring_entry.setDisabled(True)
        )

        self.ui.rectangular_cb.stateChanged.connect(
            lambda state:
            self.ui.rectangular_ring_entry.setDisabled(False) if state else
            self.ui.rectangular_ring_entry.setDisabled(True)
        )

        self.ui.other_cb.stateChanged.connect(
            lambda state:
            self.ui.other_ring_entry.setDisabled(False) if state else self.ui.other_ring_entry.setDisabled(True)
        )

    def install(self, icon=None, separator=None, **kwargs):
        AppTool.install(self, icon, separator, shortcut='Alt+I', **kwargs)

    def run(self, toggle=True):
        self.app.defaults.report_usage("Extract Drills()")

        if toggle:
            # if the splitter is hidden, display it, else hide it but only if the current widget is the same
            if self.app.ui.splitter.sizes()[0] == 0:
                self.app.ui.splitter.setSizes([1, 1])
            else:
                try:
                    if self.app.ui.tool_scroll_area.widget().objectName() == self.toolName:
                        # if tab is populated with the tool but it does not have the focus, focus on it
                        if not self.app.ui.notebook.currentWidget() is self.app.ui.tool_tab:
                            # focus on Tool Tab
                            self.app.ui.notebook.setCurrentWidget(self.app.ui.tool_tab)
                        else:
                            self.disconnect_circumference_selection(restore_app=True)
                            self.clear_circumference_selection(clear_highlights=True)
                            self.app.ui.splitter.setSizes([0, 1])
                except AttributeError:
                    pass
        else:
            if self.app.ui.splitter.sizes()[0] == 0:
                self.app.ui.splitter.setSizes([1, 1])

        AppTool.run(self)
        self.set_tool_ui()

        self.app.ui.notebook.setTabText(2, _("Extract Drills Tool"))

    def set_tool_ui(self):
        self.on_reset_gerber_tool()
        self.on_reset_geometry_tool()

    def on_reset_gerber_tool(self):
        self.reset_gerber_fields()
        self.ui.hole_size_radio.set_value(self.app.defaults["tools_edrills_hole_type"])

        self.ui.dia_entry.set_value(float(self.app.defaults["tools_edrills_hole_fixed_dia"]))

        self.ui.circular_ring_entry.set_value(float(self.app.defaults["tools_edrills_circular_ring"]))
        self.ui.oblong_ring_entry.set_value(float(self.app.defaults["tools_edrills_oblong_ring"]))
        self.ui.square_ring_entry.set_value(float(self.app.defaults["tools_edrills_square_ring"]))
        self.ui.rectangular_ring_entry.set_value(float(self.app.defaults["tools_edrills_rectangular_ring"]))
        self.ui.other_ring_entry.set_value(float(self.app.defaults["tools_edrills_others_ring"]))

        self.ui.circular_cb.set_value(self.app.defaults["tools_edrills_circular"])
        self.ui.oblong_cb.set_value(self.app.defaults["tools_edrills_oblong"])
        self.ui.square_cb.set_value(self.app.defaults["tools_edrills_square"])
        self.ui.rectangular_cb.set_value(self.app.defaults["tools_edrills_rectangular"])
        self.ui.other_cb.set_value(self.app.defaults["tools_edrills_others"])

        self.ui.factor_entry.set_value(float(self.app.defaults["tools_edrills_hole_prop_factor"]))

    def on_reset_geometry_tool(self):
        self.disconnect_circumference_selection(restore_app=True)
        self.clear_circumference_selection(clear_highlights=True)
        self.circ_geo_obj = None
        self.ui.circ_method_radio.set_value('automatic')
        self.ui.circ_manual_dia_entry.set_value(self.get_default_manual_diameter())
        self.ui.circ_remove_original_cb.set_value(True)
        self.refresh_circ_geometry_combo()
        self.on_circ_method_toggle('automatic')
        self.update_circumference_geometry_controls()
        self.update_circumference_pending_labels()

    def on_extract_drills_click(self):

        drill_dia = self.ui.dia_entry.get_value()
        circ_r_val = self.ui.circular_ring_entry.get_value()
        oblong_r_val = self.ui.oblong_ring_entry.get_value()
        square_r_val = self.ui.square_ring_entry.get_value()
        rect_r_val = self.ui.rectangular_ring_entry.get_value()
        other_r_val = self.ui.other_ring_entry.get_value()

        prop_factor = self.ui.factor_entry.get_value() / 100.0

        drills = []
        tools = {}

        selection_index = self.ui.gerber_object_combo.currentIndex()
        model_index = self.app.collection.index(selection_index, 0, self.ui.gerber_object_combo.rootModelIndex())

        try:
            fcobj = model_index.internalPointer().obj
        except Exception:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("There is no Gerber object loaded ..."))
            return

        outname = fcobj.options['name'].rpartition('.')[0]

        mode = self.ui.hole_size_radio.get_value()

        if mode == 'fixed':
            tools = {
                1: {
                    "tooldia": drill_dia,
                    "drills": [],
                    "slots": []
                }
            }
            for apid, apid_value in fcobj.apertures.items():
                ap_type = apid_value['type']

                if ap_type == 'C':
                    if self.ui.circular_cb.get_value() is False:
                        continue
                elif ap_type == 'O':
                    if self.ui.oblong_cb.get_value() is False:
                        continue
                elif ap_type == 'R':
                    width = float(apid_value['width'])
                    height = float(apid_value['height'])

                    # if the height == width (float numbers so the reason for the following)
                    if round(width, self.decimals) == round(height, self.decimals):
                        if self.ui.square_cb.get_value() is False:
                            continue
                    else:
                        if self.ui.rectangular_cb.get_value() is False:
                            continue
                else:
                    if self.ui.other_cb.get_value() is False:
                        continue

                for geo_el in apid_value['geometry']:
                    if 'follow' in geo_el and isinstance(geo_el['follow'], Point):
                        tools[1]["drills"].append(geo_el['follow'])
                        if 'solid_geometry' not in tools[1]:
                            tools[1]['solid_geometry'] = []
                        else:
                            tools[1]['solid_geometry'].append(geo_el['follow'])

            if 'solid_geometry' not in tools[1] or not tools[1]['solid_geometry']:
                self.app.inform.emit('[WARNING_NOTCL] %s' % _("No drills extracted. Try different parameters."))
                return
        elif mode == 'ring':
            drills_found = set()
            for apid, apid_value in fcobj.apertures.items():
                ap_type = apid_value['type']

                dia = None
                if ap_type == 'C':
                    if self.ui.circular_cb.get_value():
                        dia = float(apid_value['size']) - (2 * circ_r_val)
                elif ap_type == 'O':
                    width = float(apid_value['width'])
                    height = float(apid_value['height'])
                    if self.ui.oblong_cb.get_value():
                        if width > height:
                            dia = float(apid_value['height']) - (2 * oblong_r_val)
                        else:
                            dia = float(apid_value['width']) - (2 * oblong_r_val)
                elif ap_type == 'R':
                    width = float(apid_value['width'])
                    height = float(apid_value['height'])

                    # if the height == width (float numbers so the reason for the following)
                    if abs(float('%.*f' % (self.decimals, width)) - float('%.*f' % (self.decimals, height))) < \
                            (10 ** -self.decimals):
                        if self.ui.square_cb.get_value():
                            dia = float(apid_value['height']) - (2 * square_r_val)
                    else:
                        if self.ui.rectangular_cb.get_value():
                            if width > height:
                                dia = float(apid_value['height']) - (2 * rect_r_val)
                            else:
                                dia = float(apid_value['width']) - (2 * rect_r_val)
                else:
                    if self.ui.other_cb.get_value():
                        try:
                            dia = float(apid_value['size']) - (2 * other_r_val)
                        except KeyError:
                            if ap_type == 'AM':
                                pol = apid_value['geometry'][0]['solid']
                                x0, y0, x1, y1 = pol.bounds
                                dx = x1 - x0
                                dy = y1 - y0
                                if dx <= dy:
                                    dia = dx - (2 * other_r_val)
                                else:
                                    dia = dy - (2 * other_r_val)

                # if dia is None then none of the above applied so we skip the following
                if dia is None:
                    continue

                tool_in_drills = False
                for tool, tool_val in tools.items():
                    if abs(float('%.*f' % (
                            self.decimals,
                            tool_val["tooldia"])) - float('%.*f' % (self.decimals, dia))) < (10 ** -self.decimals):
                        tool_in_drills = tool

                if tool_in_drills is False:
                    if tools:
                        new_tool = max([int(t) for t in tools]) + 1
                        tool_in_drills = new_tool
                    else:
                        tool_in_drills = 1

                for geo_el in apid_value['geometry']:
                    if 'follow' in geo_el and isinstance(geo_el['follow'], Point):
                        if tool_in_drills not in tools:
                            tools[tool_in_drills] = {
                                "tooldia": dia,
                                "drills": [],
                                "slots": []
                            }

                        tools[tool_in_drills]['drills'].append(geo_el['follow'])

                        if 'solid_geometry' not in tools[tool_in_drills]:
                            tools[tool_in_drills]['solid_geometry'] = []
                        else:
                            tools[tool_in_drills]['solid_geometry'].append(geo_el['follow'])

                if tool_in_drills in tools:
                    if 'solid_geometry' not in tools[tool_in_drills] or not tools[tool_in_drills]['solid_geometry']:
                        drills_found.add(False)
                    else:
                        drills_found.add(True)

            if True not in drills_found:
                self.app.inform.emit('[WARNING_NOTCL] %s' % _("No drills extracted. Try different parameters."))
                return
        else:
            drills_found = set()
            for apid, apid_value in fcobj.apertures.items():
                ap_type = apid_value['type']

                dia = None
                if ap_type == 'C':
                    if self.ui.circular_cb.get_value():
                        dia = float(apid_value['size']) * prop_factor
                elif ap_type == 'O':
                    width = float(apid_value['width'])
                    height = float(apid_value['height'])
                    if self.ui.oblong_cb.get_value():
                        if width > height:
                            dia = float(apid_value['height']) * prop_factor
                        else:
                            dia = float(apid_value['width']) * prop_factor
                elif ap_type == 'R':
                    width = float(apid_value['width'])
                    height = float(apid_value['height'])

                    # if the height == width (float numbers so the reason for the following)
                    if abs(float('%.*f' % (self.decimals, width)) - float('%.*f' % (self.decimals, height))) < \
                            (10 ** -self.decimals):
                        if self.ui.square_cb.get_value():
                            dia = float(apid_value['height']) * prop_factor
                    else:
                        if self.ui.rectangular_cb.get_value():
                            if width > height:
                                dia = float(apid_value['height']) * prop_factor
                            else:
                                dia = float(apid_value['width']) * prop_factor
                else:
                    if self.ui.other_cb.get_value():
                        try:
                            dia = float(apid_value['size']) * prop_factor
                        except KeyError:
                            if ap_type == 'AM':
                                pol = apid_value['geometry'][0]['solid']
                                x0, y0, x1, y1 = pol.bounds
                                dx = x1 - x0
                                dy = y1 - y0
                                if dx <= dy:
                                    dia = dx * prop_factor
                                else:
                                    dia = dy * prop_factor

                # if dia is None then none of the above applied so we skip the following
                if dia is None:
                    continue

                tool_in_drills = False
                for tool, tool_val in tools.items():
                    if abs(float('%.*f' % (
                            self.decimals,
                            tool_val["tooldia"])) - float('%.*f' % (self.decimals, dia))) < (10 ** -self.decimals):
                        tool_in_drills = tool

                if tool_in_drills is False:
                    if tools:
                        new_tool = max([int(t) for t in tools]) + 1
                        tool_in_drills = new_tool
                    else:
                        tool_in_drills = 1

                for geo_el in apid_value['geometry']:
                    if 'follow' in geo_el and isinstance(geo_el['follow'], Point):
                        if tool_in_drills not in tools:
                            tools[tool_in_drills] = {
                                "tooldia": dia,
                                "drills": [],
                                "slots": []
                            }

                        tools[tool_in_drills]['drills'].append(geo_el['follow'])

                        if 'solid_geometry' not in tools[tool_in_drills]:
                            tools[tool_in_drills]['solid_geometry'] = []
                        else:
                            tools[tool_in_drills]['solid_geometry'].append(geo_el['follow'])

                if tool_in_drills in tools:
                    if 'solid_geometry' not in tools[tool_in_drills] or not tools[tool_in_drills]['solid_geometry']:
                        drills_found.add(False)
                    else:
                        drills_found.add(True)

            if True not in drills_found:
                self.app.inform.emit('[WARNING_NOTCL] %s' % _("No drills extracted. Try different parameters."))
                return

        def obj_init(obj_inst, app_inst):
            obj_inst.tools = tools
            obj_inst.drills = drills
            obj_inst.create_geometry()
            obj_inst.source_file = app_inst.f_handlers.export_excellon(obj_name=outname, local_use=obj_inst,
                                                                       filename=None,
                                                                       use_thread=False)

        self.app.app_obj.new_object("excellon", outname, obj_init)

    def on_hole_size_toggle(self, val):
        if val == "fixed":
            self.ui.fixed_label.setVisible(True)
            self.ui.dia_entry.setVisible(True)
            self.ui.dia_label.setVisible(True)

            self.ui.ring_frame.setVisible(False)

            self.ui.prop_label.setVisible(False)
            self.ui.factor_label.setVisible(False)
            self.ui.factor_entry.setVisible(False)
        elif val == "ring":
            self.ui.fixed_label.setVisible(False)
            self.ui.dia_entry.setVisible(False)
            self.ui.dia_label.setVisible(False)

            self.ui.ring_frame.setVisible(True)

            self.ui.prop_label.setVisible(False)
            self.ui.factor_label.setVisible(False)
            self.ui.factor_entry.setVisible(False)
        elif val == "prop":
            self.ui.fixed_label.setVisible(False)
            self.ui.dia_entry.setVisible(False)
            self.ui.dia_label.setVisible(False)

            self.ui.ring_frame.setVisible(False)

            self.ui.prop_label.setVisible(True)
            self.ui.factor_label.setVisible(True)
            self.ui.factor_entry.setVisible(True)

    def reset_gerber_fields(self):
        self.ui.gerber_object_combo.setRootModelIndex(self.app.collection.index(0, 0, QtCore.QModelIndex()))
        self.ui.gerber_object_combo.setCurrentIndex(0)

    def get_geometry_root_index(self):
        try:
            geo_group = self.app.collection.group_items.get('geometry')
            return self.app.collection.index(geo_group.row(), 0, QtCore.QModelIndex())
        except Exception:
            return self.app.collection.index(2, 0, QtCore.QModelIndex())

    def refresh_circ_geometry_combo(self):
        geo_root = self.get_geometry_root_index()
        current_name = self.ui.circ_geometry_combo.currentText()
        self.ui.circ_geometry_combo.setRootModelIndex(geo_root)

        selected_name = None
        try:
            active_obj = self.app.collection.get_active()
            if active_obj and getattr(active_obj, 'kind', '').lower() == 'geometry':
                selected_name = active_obj.options['name']
        except Exception:
            selected_name = None

        if selected_name is None and current_name:
            selected_name = current_name

        idx = self.ui.circ_geometry_combo.findText(str(selected_name)) if selected_name else -1
        if idx >= 0:
            self.ui.circ_geometry_combo.setCurrentIndex(idx)
        elif self.app.collection.rowCount(geo_root) > 0:
            self.ui.circ_geometry_combo.setCurrentIndex(0)

        self.ui.circ_geometry_combo.obj_type = "Geometry"
        self.update_circumference_geometry_controls()

    def on_circ_geometry_combo_changed(self):
        if self.pending_circ_selections:
            self.clear_circumference_selection(clear_highlights=True)
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Temporary circumference selection cleared."))
        self.circ_geo_obj = self.get_selected_circ_geometry(quiet=True)
        self.update_circumference_geometry_controls()

    def on_collection_status_changed(self, obj, status, name):
        try:
            obj_kind = getattr(obj, 'kind', '').lower()
        except Exception:
            obj_kind = ''

        if status == 'delete_all' or obj_kind == 'geometry' or str(name) == self.ui.circ_geometry_combo.currentText():
            if status == 'delete_all' or str(name) == self.ui.circ_geometry_combo.currentText():
                self.clear_circumference_selection(clear_highlights=True)
            self.refresh_circ_geometry_combo()

    def get_default_manual_diameter(self):
        units = str(self.app.options.get("units", "MM")).upper()
        if units == 'IN':
            return 0.8 / 25.4
        return 0.8

    def get_circ_diameter_range(self):
        units = str(self.app.options.get("units", "MM")).upper()
        if units == 'IN':
            return 0.2 / 25.4, 15.0 / 25.4
        return 0.2, 15.0

    def get_circ_highlight_color(self):
        color = self.app.defaults.get('global_sel_draw_color', '#00FFFF')
        if isinstance(color, str) and len(color) <= 7:
            return color + 'AF'
        return color

    def redraw_circ_tool_shapes(self):
        try:
            self.app.tool_shapes.redraw()
        except Exception:
            log.debug("Could not redraw circumference selection highlights.", exc_info=True)

    def add_circ_highlight(self, geometry):
        if geometry is None or getattr(self.app, 'tool_shapes', None) is None:
            return None

        color = self.get_circ_highlight_color()
        try:
            shape_id = self.app.tool_shapes.add(
                shape=geometry,
                color=color,
                face_color=None,
                visible=True,
                update=False,
                layer=0,
                tolerance=None,
                linewidth=2
            )
        except TypeError:
            try:
                shape_id = self.app.tool_shapes.add(
                    shape=geometry,
                    color=color,
                    face_color=None,
                    visible=True,
                    update=False,
                    layer=0,
                    tolerance=None
                )
            except Exception:
                log.debug("Could not add circumference selection highlight.", exc_info=True)
                return None
        except Exception:
            log.debug("Could not add circumference selection highlight.", exc_info=True)
            return None

        self.circ_highlight_ids.append(shape_id)
        self.redraw_circ_tool_shapes()
        return shape_id

    def remove_circ_highlight(self, shape_id, redraw=True):
        if shape_id is None or getattr(self.app, 'tool_shapes', None) is None:
            return
        try:
            self.app.tool_shapes.remove(shape_id, update=False)
        except Exception:
            log.debug("Could not remove circumference selection highlight.", exc_info=True)
        if shape_id in self.circ_highlight_ids:
            self.circ_highlight_ids.remove(shape_id)
        if redraw:
            self.redraw_circ_tool_shapes()

    def clear_circ_highlights(self):
        if getattr(self.app, 'tool_shapes', None) is None:
            self.circ_highlight_ids = []
            return

        for shape_id in list(self.circ_highlight_ids):
            self.remove_circ_highlight(shape_id, redraw=False)
        self.circ_highlight_ids = []
        self.redraw_circ_tool_shapes()

    def clear_circumference_selection(self, clear_highlights=True):
        self.pending_circ_selections = []
        self.pending_circ_drills = {}
        if clear_highlights:
            self.clear_circ_highlights()
        self.update_circumference_pending_labels()

    def update_circumference_geometry_controls(self):
        geo_obj = self.get_selected_circ_geometry(quiet=True)
        has_geo = geo_obj is not None
        self.ui.circ_select_button.setEnabled(has_geo)
        self.ui.circ_generate_button.setEnabled(bool(self.pending_circ_drills))
        if has_geo:
            self.ui.circ_status_label.setText("")
        else:
            self.ui.circ_status_label.setText(_("No Geometry Object available."))

    def on_circ_method_toggle(self, val):
        self.ui.circ_manual_dia_label.setEnabled(val == 'manual')
        self.ui.circ_manual_dia_entry.setEnabled(val == 'manual')

    def get_selected_circ_geometry(self, quiet=False):
        selection_index = self.ui.circ_geometry_combo.currentIndex()
        model_index = self.app.collection.index(selection_index, 0, self.ui.circ_geometry_combo.rootModelIndex())

        try:
            obj = model_index.internalPointer().obj
        except Exception:
            if quiet is False:
                self.app.inform.emit('[WARNING_NOTCL] %s' % _("There is no Geometry object loaded ..."))
            return None

        if getattr(obj, 'kind', '').lower() != 'geometry':
            if quiet is False:
                self.app.inform.emit('[WARNING_NOTCL] %s' % _("The selected object is not a Geometry object."))
            return None
        return obj

    @staticmethod
    def iter_geometry(geometry):
        if geometry is None:
            return
        if isinstance(geometry, (list, tuple)):
            for geo in geometry:
                for geo_el in ToolExtractDrills.iter_geometry(geo):
                    yield geo_el
        elif isinstance(geometry, GeometryCollection):
            for geo in geometry.geoms:
                for geo_el in ToolExtractDrills.iter_geometry(geo):
                    yield geo_el
        elif isinstance(geometry, MultiPolygon):
            for geo in geometry.geoms:
                yield geo
        else:
            yield geometry

    @staticmethod
    def closed_coords(geometry):
        if isinstance(geometry, LinearRing):
            coords = list(geometry.coords)
            return coords if len(coords) >= 4 else None
        if isinstance(geometry, LineString):
            coords = list(geometry.coords)
            if len(coords) >= 4 and coords[0] == coords[-1]:
                return coords
            return None
        if isinstance(geometry, Polygon):
            coords = list(geometry.exterior.coords)
            return coords if len(coords) >= 4 else None
        return None

    @staticmethod
    def circumference_info(geometry):
        coords = ToolExtractDrills.closed_coords(geometry)
        if not coords:
            return None

        try:
            xmin, ymin, xmax, ymax = geometry.bounds
        except Exception:
            return None

        width = xmax - xmin
        height = ymax - ymin
        if width <= 0 or height <= 0:
            return None

        diameter = (width + height) / 2.0
        center_x = (xmin + xmax) / 2.0
        center_y = (ymin + ymax) / 2.0
        wh_rel = abs(width - height) / diameter if diameter else 999.0

        try:
            length = geometry.length
        except Exception:
            return None

        expected_length = math.pi * diameter
        length_rel = abs(length - expected_length) / expected_length if expected_length else 999.0

        radii = [math.hypot(x - center_x, y - center_y) for x, y, *rest in coords[:-1]]
        if not radii:
            return None
        radius_mean = sum(radii) / len(radii)
        radial_rel = (max(radii) - min(radii)) / radius_mean if radius_mean else 999.0

        try:
            poly = Polygon(coords)
            circularity = 4.0 * math.pi * poly.area / (poly.length * poly.length) \
                if poly.is_valid and poly.length else 0.0
        except Exception:
            circularity = 0.0

        if wh_rel > 0.05 or length_rel > 0.08 or radial_rel > 0.10 or circularity < 0.88:
            return None

        return {
            'center': Point(center_x, center_y),
            'diameter': diameter,
            'bounds': (xmin, ymin, xmax, ymax),
            'circularity': circularity,
            'geometry': geometry
        }

    @staticmethod
    def same_circumference(geometry, reference_info):
        info = ToolExtractDrills.circumference_info(geometry)
        if info is None:
            return False

        dia_tol = max(0.005, reference_info['diameter'] * 0.01)
        center_tol = max(0.005, reference_info['diameter'] * 0.05)

        if abs(info['diameter'] - reference_info['diameter']) > dia_tol:
            return False
        if info['center'].distance(reference_info['center']) > center_tol:
            return False
        return True

    @staticmethod
    def remove_matching_circumference(geometry, reference_info):
        removed = 0
        if geometry is None:
            return geometry, removed

        if isinstance(geometry, list):
            new_geo = []
            for geo in geometry:
                geo_new, geo_removed = ToolExtractDrills.remove_matching_circumference(geo, reference_info)
                removed += geo_removed
                if geo_removed and (geo_new is None or getattr(geo_new, 'is_empty', False)):
                    continue
                if geo_new is not None:
                    new_geo.append(geo_new)
            return new_geo, removed

        if isinstance(geometry, tuple):
            new_geo = []
            for geo in geometry:
                geo_new, geo_removed = ToolExtractDrills.remove_matching_circumference(geo, reference_info)
                removed += geo_removed
                if geo_removed and (geo_new is None or getattr(geo_new, 'is_empty', False)):
                    continue
                if geo_new is not None:
                    new_geo.append(geo_new)
            return new_geo, removed

        if isinstance(geometry, GeometryCollection):
            new_geo = []
            for geo in geometry.geoms:
                geo_new, geo_removed = ToolExtractDrills.remove_matching_circumference(geo, reference_info)
                removed += geo_removed
                if geo_removed and (geo_new is None or getattr(geo_new, 'is_empty', False)):
                    continue
                if geo_new is not None:
                    new_geo.append(geo_new)
            return new_geo, removed

        if isinstance(geometry, MultiPolygon):
            new_geo = []
            for geo in geometry.geoms:
                if ToolExtractDrills.same_circumference(geo, reference_info):
                    removed += 1
                else:
                    new_geo.append(geo)
            return new_geo, removed

        if ToolExtractDrills.same_circumference(geometry, reference_info):
            return None, 1
        return geometry, 0

    def find_circumference_at_point(self, geo_obj, click_pt):
        candidates = []
        for geo in self.iter_geometry(getattr(geo_obj, 'follow_geometry', None)):
            info = self.circumference_info(geo)
            if info is None:
                continue
            tolerance = max(0.05, info['diameter'] * 0.08)
            distance = geo.distance(click_pt)
            if distance <= tolerance:
                candidates.append((distance, info))

        for geo in self.iter_geometry(getattr(geo_obj, 'solid_geometry', None)):
            info = self.circumference_info(geo)
            if info is None:
                continue
            tolerance = max(0.05, info['diameter'] * 0.08)
            distance = geo.distance(click_pt)
            if distance <= tolerance:
                candidates.append((distance, info))

        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0])
        return candidates[0][1]

    def on_circumference_select_click(self):
        geo_obj = self.get_selected_circ_geometry()
        if geo_obj is None:
            self.update_circumference_geometry_controls()
            return

        self.circ_geo_obj = geo_obj
        if self.ui.circ_remove_original_cb.get_value():
            self.app.inform.emit(
                '[WARNING_NOTCL] %s' %
                _("Selected circumferences will be removed from the Geometry Object after successful conversion.")
            )
        else:
            self.app.inform.emit('%s' % _("Selected circumferences will be kept in the Geometry Object."))
        self.app.inform.emit('%s' % _("Click a circumference on the canvas. Right click to finish."))

        if self.circ_select_active:
            return

        self.circ_select_active = True
        self.circ_mr = self.canvas.graph_event_connect('mouse_release', self.on_circumference_mouse_release)
        try:
            if self.app.is_legacy is False:
                self.canvas.graph_event_disconnect('mouse_release', self.app.on_mouse_click_release_over_plot)
            else:
                self.canvas.graph_event_disconnect(self.app.mr)
        except Exception:
            pass

    def on_circumference_mouse_release(self, event):
        if self.app.is_legacy is False:
            event_pos = event.pos
            right_button = 2
            event_is_dragging = self.app.event_is_dragging
        else:
            event_pos = (event.xdata, event.ydata)
            right_button = 3
            event_is_dragging = self.app.ui.popMenu.mouse_is_panning

        if event.button == right_button and event_is_dragging is False:
            self.disconnect_circumference_selection(restore_app=True)
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Circumference selection finished."))
            return

        if event.button != 1:
            return

        try:
            pos_canvas = self.canvas.translate_coords(event_pos)
            click_pt = Point([pos_canvas[0], pos_canvas[1]])
        except Exception:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Could not read canvas position."))
            return

        geo_obj = self.circ_geo_obj or self.get_selected_circ_geometry()
        if geo_obj is None:
            return

        info = self.find_circumference_at_point(geo_obj, click_pt)
        if info is None:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Selected geometry is not a valid circumference."))
            return

        method = self.ui.circ_method_radio.get_value()
        if method == 'manual':
            tool_dia = self.ui.circ_manual_dia_entry.get_value()
        else:
            tool_dia = info['diameter']

        min_dia, max_dia = self.get_circ_diameter_range()
        if tool_dia < min_dia or tool_dia > max_dia:
            self.app.inform.emit(
                '[WARNING_NOTCL] %s: [%.*f, %.*f]' %
                (_("Tool diameter is out of range"), self.decimals, min_dia, self.decimals, max_dia)
            )
            return

        drill_point = info['center']
        diameter_key = float('%.*f' % (self.decimals, tool_dia))

        for selection in self.pending_circ_selections:
            if selection.get('geo_obj') is geo_obj and self.same_circumference(
                    selection.get('circumference_info', {}).get('geometry'), info):
                self.app.inform.emit('[WARNING_NOTCL] %s' % _("Circumference already selected."))
                return

        selection = {
            'geo_obj': geo_obj,
            'circumference_info': info,
            'drill': drill_point,
            'tool_dia': tool_dia,
            'diameter_key': diameter_key,
            'overlay_id': None
        }
        selection['overlay_id'] = self.add_circ_highlight(info.get('geometry'))
        self.pending_circ_selections.append(selection)
        self.rebuild_pending_circ_drills()

        self.update_circumference_pending_labels()
        self.app.inform.emit(
            '[success] %s: %.*f' % (_("Circumference added to temporary selection"), self.decimals, tool_dia)
        )

    def rebuild_pending_circ_drills(self):
        self.pending_circ_drills = {}
        for selection in self.pending_circ_selections:
            diameter_key = selection['diameter_key']
            if diameter_key not in self.pending_circ_drills:
                self.pending_circ_drills[diameter_key] = []
            self.pending_circ_drills[diameter_key].append(selection['drill'])

    def on_circumference_undo_last(self):
        if not self.pending_circ_selections:
            return
        selection = self.pending_circ_selections.pop()
        self.remove_circ_highlight(selection.get('overlay_id'), redraw=True)
        self.rebuild_pending_circ_drills()
        self.update_circumference_pending_labels()
        self.app.inform.emit('[success] %s' % _("Last circumference selection removed."))

    def on_circumference_clear_selection(self):
        if not self.pending_circ_selections:
            return
        self.clear_circumference_selection(clear_highlights=True)
        self.app.inform.emit('[success] %s' % _("Circumference selection cleared."))

    def disconnect_circumference_selection(self, restore_app=True):
        if self.circ_select_active is False:
            return

        try:
            if self.app.is_legacy is False:
                self.canvas.graph_event_disconnect('mouse_release', self.on_circumference_mouse_release)
            else:
                self.canvas.graph_event_disconnect(self.circ_mr)
        except Exception:
            pass

        if restore_app:
            try:
                self.app.mr = self.canvas.graph_event_connect('mouse_release', self.app.on_mouse_click_release_over_plot)
            except Exception:
                pass

        self.circ_select_active = False
        self.circ_mr = None

    def update_circumference_pending_labels(self):
        drill_count = sum(len(drills) for drills in self.pending_circ_drills.values())
        tool_count = len([dia for dia, drills in self.pending_circ_drills.items() if drills])
        self.ui.circ_selected_label.setText("%s: %d" % (_("Selected circumferences"), drill_count))
        self.ui.circ_tools_label.setText("%s: %d" % (_("Tools"), tool_count))
        has_selection = drill_count > 0
        self.ui.circ_undo_button.setEnabled(has_selection)
        self.ui.circ_clear_button.setEnabled(has_selection)
        self.ui.circ_generate_button.setEnabled(has_selection)

    def remove_selected_circumferences_after_success(self):
        touched_objects = {}
        total_removed = 0

        for selection in self.pending_circ_selections:
            geo_obj = selection.get('geo_obj')
            info = selection.get('circumference_info')
            if geo_obj is None or info is None:
                continue

            new_solid, solid_removed = self.remove_matching_circumference(
                getattr(geo_obj, 'solid_geometry', None), info
            )
            new_follow, follow_removed = self.remove_matching_circumference(
                getattr(geo_obj, 'follow_geometry', None), info
            )

            if solid_removed:
                geo_obj.solid_geometry = new_solid
            if follow_removed:
                geo_obj.follow_geometry = new_follow

            removed = solid_removed + follow_removed
            if removed:
                touched_objects[id(geo_obj)] = geo_obj
                total_removed += removed

        for geo_obj in touched_objects.values():
            try:
                geo_obj.plot()
            except Exception:
                log.debug("Could not refresh Geometry Object after circumference removal.", exc_info=True)

        if touched_objects:
            try:
                self.app.collection.update_view()
            except Exception:
                pass
            try:
                self.app.plotcanvas.redraw()
            except Exception:
                pass

        return total_removed

    def on_generate_circ_excellon_click(self):
        if not self.pending_circ_drills:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("No circumference drills have been selected."))
            return

        geo_obj = self.circ_geo_obj or self.get_selected_circ_geometry()
        if geo_obj is None:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("There is no Geometry object loaded ..."))
            return

        tools = {}
        all_drills = []
        for tool_id, diameter in enumerate(sorted(self.pending_circ_drills), start=1):
            drills = self.pending_circ_drills[diameter]
            tools[tool_id] = {
                "tooldia": diameter,
                "drills": drills[:],
                "slots": [],
                "solid_geometry": [drill.buffer(diameter / 2.0) for drill in drills]
            }
            all_drills.extend(drills)

        if not all_drills:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("No circumference drills have been selected."))
            return

        base_name = geo_obj.options['name'].rpartition('.')[0] or geo_obj.options['name']
        outname = base_name + "_circumference_drills"

        def obj_init(obj_inst, app_inst):
            obj_inst.tools = tools
            obj_inst.drills = all_drills
            obj_inst.create_geometry()
            obj_inst.source_file = app_inst.f_handlers.export_excellon(
                obj_name=obj_inst.options['name'], local_use=obj_inst, filename=None, use_thread=False
            )

        ret = self.app.app_obj.new_object("excellon", outname, obj_init)
        if ret == 'fail':
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Could not create Excellon object."))
            return

        removed = 0
        if self.ui.circ_remove_original_cb.get_value():
            removed = self.remove_selected_circumferences_after_success()

        self.clear_circumference_selection(clear_highlights=True)
        self.app.inform.emit(
            '[success] %s: %d %s, %d %s' %
            (_("Created"), len(all_drills), _("drills"), len(tools), _("tools"))
        )
        if self.ui.circ_remove_original_cb.get_value():
            self.app.inform.emit('[success] %s: %d' % (_("Removed original circumference geometries"), removed))


class ExtractDrillsUI:

    toolName = _("Extract Drills")

    def __init__(self, layout, app):
        self.app = app
        self.decimals = self.app.decimals
        self.layout = layout

        # ## Title
        title_label = QtWidgets.QLabel("%s" % self.toolName)
        title_label.setStyleSheet("""
                                QLabel
                                {
                                    font-size: 16px;
                                    font-weight: bold;
                                }
                                """)
        self.layout.addWidget(title_label)

        self.layout.addWidget(QtWidgets.QLabel(""))

        self.gerber_group = QtWidgets.QGroupBox()
        self.gerber_group_layout = QtWidgets.QVBoxLayout()
        self.gerber_group_layout.setContentsMargins(8, 12, 8, 8)
        self.gerber_group_layout.setSpacing(6)
        self.gerber_group.setLayout(self.gerber_group_layout)
        self.layout.addWidget(self.gerber_group)

        self.gerber_title_label = QtWidgets.QLabel("<b>%s</b>" % _("A) Convert Gerber Objects to Drills"))
        self.gerber_title_label.setWordWrap(True)
        self.gerber_group_layout.addWidget(self.gerber_title_label)

        # ## Grid Layout
        grid_lay = QtWidgets.QGridLayout()
        self.gerber_group_layout.addLayout(grid_lay)
        grid_lay.setColumnStretch(0, 1)
        grid_lay.setColumnStretch(1, 0)

        # ## Gerber Object
        self.gerber_object_combo = FCComboBox()
        self.gerber_object_combo.setModel(self.app.collection)
        self.gerber_object_combo.setRootModelIndex(self.app.collection.index(0, 0, QtCore.QModelIndex()))
        self.gerber_object_combo.is_last = True
        self.gerber_object_combo.obj_type = "Gerber"

        self.grb_label = QtWidgets.QLabel("<b>%s:</b>" % _("GERBER"))
        self.grb_label.setToolTip('%s.' % _("Gerber from which to extract drill holes"))

        # grid_lay.addRow("Bottom Layer:", self.object_combo)
        grid_lay.addWidget(self.grb_label, 0, 0, 1, 2)
        grid_lay.addWidget(self.gerber_object_combo, 1, 0, 1, 2)

        self.padt_label = QtWidgets.QLabel("<b>%s</b>" % _("Processed Pads Type"))
        self.padt_label.setToolTip(
            _("The type of pads shape to be processed.\n"
              "If the PCB has many SMD pads with rectangular pads,\n"
              "disable the Rectangular aperture.")
        )

        grid_lay.addWidget(self.padt_label, 2, 0, 1, 2)

        # Circular Aperture Selection
        self.circular_cb = FCCheckBox('%s' % _("Circular"))
        self.circular_cb.setToolTip(
            _("Process Circular Pads.")
        )

        grid_lay.addWidget(self.circular_cb, 3, 0, 1, 2)

        # Oblong Aperture Selection
        self.oblong_cb = FCCheckBox('%s' % _("Oblong"))
        self.oblong_cb.setToolTip(
            _("Process Oblong Pads.")
        )

        grid_lay.addWidget(self.oblong_cb, 4, 0, 1, 2)

        # Square Aperture Selection
        self.square_cb = FCCheckBox('%s' % _("Square"))
        self.square_cb.setToolTip(
            _("Process Square Pads.")
        )

        grid_lay.addWidget(self.square_cb, 5, 0, 1, 2)

        # Rectangular Aperture Selection
        self.rectangular_cb = FCCheckBox('%s' % _("Rectangular"))
        self.rectangular_cb.setToolTip(
            _("Process Rectangular Pads.")
        )

        grid_lay.addWidget(self.rectangular_cb, 6, 0, 1, 2)

        # Others type of Apertures Selection
        self.other_cb = FCCheckBox('%s' % _("Others"))
        self.other_cb.setToolTip(
            _("Process pads not in the categories above.")
        )

        grid_lay.addWidget(self.other_cb, 7, 0, 1, 2)

        separator_line = QtWidgets.QFrame()
        separator_line.setFrameShape(QtWidgets.QFrame.HLine)
        separator_line.setFrameShadow(QtWidgets.QFrame.Sunken)
        grid_lay.addWidget(separator_line, 8, 0, 1, 2)

        # ## Grid Layout
        grid1 = QtWidgets.QGridLayout()
        self.gerber_group_layout.addLayout(grid1)
        grid1.setColumnStretch(0, 0)
        grid1.setColumnStretch(1, 1)

        self.method_label = QtWidgets.QLabel('<b>%s</b>' % _("Method"))
        self.method_label.setToolTip(
            _("The method for processing pads. Can be:\n"
              "- Fixed Diameter -> all holes will have a set size\n"
              "- Fixed Annular Ring -> all holes will have a set annular ring\n"
              "- Proportional -> each hole size will be a fraction of the pad size"))
        grid1.addWidget(self.method_label, 2, 0, 1, 2)

        # ## Holes Size
        self.hole_size_radio = RadioSet(
            [
                {'label': _("Fixed Diameter"), 'value': 'fixed'},
                {'label': _("Proportional"), 'value': 'prop'},
                {'label': _("Fixed Annular Ring"), 'value': 'ring'}
            ],
            orientation='vertical',
            stretch=False)

        grid1.addWidget(self.hole_size_radio, 3, 0, 1, 2)

        # grid_lay1.addWidget(QtWidgets.QLabel(''))

        separator_line = QtWidgets.QFrame()
        separator_line.setFrameShape(QtWidgets.QFrame.HLine)
        separator_line.setFrameShadow(QtWidgets.QFrame.Sunken)
        grid1.addWidget(separator_line, 5, 0, 1, 2)

        # Annular Ring
        self.fixed_label = QtWidgets.QLabel('<b>%s</b>' % _("Fixed Diameter"))
        grid1.addWidget(self.fixed_label, 6, 0, 1, 2)

        # Diameter value
        self.dia_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.dia_entry.set_precision(self.decimals)
        self.dia_entry.set_range(0.0000, 10000.0000)

        self.dia_label = QtWidgets.QLabel('%s:' % _("Value"))
        self.dia_label.setToolTip(
            _("Fixed hole diameter.")
        )

        grid1.addWidget(self.dia_label, 8, 0)
        grid1.addWidget(self.dia_entry, 8, 1)

        self.ring_frame = QtWidgets.QFrame()
        self.ring_frame.setContentsMargins(0, 0, 0, 0)
        self.gerber_group_layout.addWidget(self.ring_frame)

        self.ring_box = QtWidgets.QVBoxLayout()
        self.ring_box.setContentsMargins(0, 0, 0, 0)
        self.ring_frame.setLayout(self.ring_box)

        # ## Grid Layout
        grid2 = QtWidgets.QGridLayout()
        grid2.setColumnStretch(0, 0)
        grid2.setColumnStretch(1, 1)
        self.ring_box.addLayout(grid2)

        # Annular Ring value
        self.ring_label = QtWidgets.QLabel('<b>%s</b>' % _("Fixed Annular Ring"))
        self.ring_label.setToolTip(
            _("The size of annular ring.\n"
              "The copper sliver between the hole exterior\n"
              "and the margin of the copper pad.")
        )
        grid2.addWidget(self.ring_label, 0, 0, 1, 2)

        # Circular Annular Ring Value
        self.circular_ring_label = QtWidgets.QLabel('%s:' % _("Circular"))
        self.circular_ring_label.setToolTip(
            _("The size of annular ring for circular pads.")
        )

        self.circular_ring_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.circular_ring_entry.set_precision(self.decimals)
        self.circular_ring_entry.set_range(0.0000, 10000.0000)

        grid2.addWidget(self.circular_ring_label, 1, 0)
        grid2.addWidget(self.circular_ring_entry, 1, 1)

        # Oblong Annular Ring Value
        self.oblong_ring_label = QtWidgets.QLabel('%s:' % _("Oblong"))
        self.oblong_ring_label.setToolTip(
            _("The size of annular ring for oblong pads.")
        )

        self.oblong_ring_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.oblong_ring_entry.set_precision(self.decimals)
        self.oblong_ring_entry.set_range(0.0000, 10000.0000)

        grid2.addWidget(self.oblong_ring_label, 2, 0)
        grid2.addWidget(self.oblong_ring_entry, 2, 1)

        # Square Annular Ring Value
        self.square_ring_label = QtWidgets.QLabel('%s:' % _("Square"))
        self.square_ring_label.setToolTip(
            _("The size of annular ring for square pads.")
        )

        self.square_ring_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.square_ring_entry.set_precision(self.decimals)
        self.square_ring_entry.set_range(0.0000, 10000.0000)

        grid2.addWidget(self.square_ring_label, 3, 0)
        grid2.addWidget(self.square_ring_entry, 3, 1)

        # Rectangular Annular Ring Value
        self.rectangular_ring_label = QtWidgets.QLabel('%s:' % _("Rectangular"))
        self.rectangular_ring_label.setToolTip(
            _("The size of annular ring for rectangular pads.")
        )

        self.rectangular_ring_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.rectangular_ring_entry.set_precision(self.decimals)
        self.rectangular_ring_entry.set_range(0.0000, 10000.0000)

        grid2.addWidget(self.rectangular_ring_label, 4, 0)
        grid2.addWidget(self.rectangular_ring_entry, 4, 1)

        # Others Annular Ring Value
        self.other_ring_label = QtWidgets.QLabel('%s:' % _("Others"))
        self.other_ring_label.setToolTip(
            _("The size of annular ring for other pads.")
        )

        self.other_ring_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.other_ring_entry.set_precision(self.decimals)
        self.other_ring_entry.set_range(0.0000, 10000.0000)

        grid2.addWidget(self.other_ring_label, 5, 0)
        grid2.addWidget(self.other_ring_entry, 5, 1)

        grid3 = QtWidgets.QGridLayout()
        self.gerber_group_layout.addLayout(grid3)
        grid3.setColumnStretch(0, 0)
        grid3.setColumnStretch(1, 1)

        # Annular Ring value
        self.prop_label = QtWidgets.QLabel('<b>%s</b>' % _("Proportional Diameter"))
        grid3.addWidget(self.prop_label, 2, 0, 1, 2)

        # Diameter value
        self.factor_entry = FCDoubleSpinner(callback=self.confirmation_message, suffix='%')
        self.factor_entry.set_precision(self.decimals)
        self.factor_entry.set_range(0.0000, 100.0000)
        self.factor_entry.setSingleStep(0.1)

        self.factor_label = QtWidgets.QLabel('%s:' % _("Value"))
        self.factor_label.setToolTip(
            _("Proportional Diameter.\n"
              "The hole diameter will be a fraction of the pad size.")
        )

        grid3.addWidget(self.factor_label, 3, 0)
        grid3.addWidget(self.factor_entry, 3, 1)

        separator_line = QtWidgets.QFrame()
        separator_line.setFrameShape(QtWidgets.QFrame.HLine)
        separator_line.setFrameShadow(QtWidgets.QFrame.Sunken)
        grid3.addWidget(separator_line, 5, 0, 1, 2)

        # Extract drills from Gerber apertures flashes (pads)
        self.e_drills_button = QtWidgets.QPushButton(_("Extract Drills"))
        self.e_drills_button.setIcon(QtGui.QIcon(self.app.resource_location + '/drill16.png'))
        self.e_drills_button.setToolTip(
            _("Extract drills from a given Gerber file.")
        )
        self.e_drills_button.setStyleSheet("""
                                        QPushButton
                                        {
                                            font-weight: bold;
                                        }
                                        """)
        self.gerber_group_layout.addWidget(self.e_drills_button)

        # ## Reset Gerber Tool
        self.reset_button = QtWidgets.QPushButton(_("Reset Gerber Tool"))
        self.reset_button.setIcon(QtGui.QIcon(self.app.resource_location + '/reset32.png'))
        self.reset_button.setToolTip(
            _("Reset only the Gerber drill extraction controls.")
        )
        self.reset_button.setStyleSheet("""
                                QPushButton
                                {
                                    font-weight: bold;
                                }
                                """)
        self.gerber_group_layout.addWidget(self.reset_button)

        self.geometry_group = QtWidgets.QGroupBox()
        self.geometry_group_layout = QtWidgets.QVBoxLayout()
        self.geometry_group_layout.setContentsMargins(8, 12, 8, 8)
        self.geometry_group_layout.setSpacing(6)
        self.geometry_group.setLayout(self.geometry_group_layout)
        self.layout.addWidget(self.geometry_group)

        self.geometry_title_label = QtWidgets.QLabel("<b>%s</b>" % _("B) Convert Geometry Circumferences to Drills"))
        self.geometry_title_label.setWordWrap(True)
        self.geometry_group_layout.addWidget(self.geometry_title_label)

        self.circ_grid = QtWidgets.QGridLayout()
        self.geometry_group_layout.addLayout(self.circ_grid)
        self.circ_grid.setColumnStretch(0, 0)
        self.circ_grid.setColumnStretch(1, 1)

        self.circ_geometry_label = QtWidgets.QLabel('%s:' % _("Geometry"))
        self.circ_geometry_label.setToolTip(
            _("Geometry Object that contains closed circumferences to convert into drills.")
        )
        self.circ_geometry_combo = FCComboBox()
        self.circ_geometry_combo.setModel(self.app.collection)
        try:
            geo_group = self.app.collection.group_items.get('geometry')
            geo_root = self.app.collection.index(geo_group.row(), 0, QtCore.QModelIndex())
        except Exception:
            geo_root = self.app.collection.index(2, 0, QtCore.QModelIndex())
        self.circ_geometry_combo.setRootModelIndex(geo_root)
        self.circ_geometry_combo.is_last = True
        self.circ_geometry_combo.obj_type = "Geometry"

        self.circ_grid.addWidget(self.circ_geometry_label, 0, 0)
        self.circ_grid.addWidget(self.circ_geometry_combo, 0, 1)

        self.circ_method_label = QtWidgets.QLabel('<b>%s</b>' % _("Tool Diameter Method"))
        self.circ_grid.addWidget(self.circ_method_label, 1, 0, 1, 2)

        self.circ_method_radio = RadioSet(
            [
                {'label': _("Automatic"), 'value': 'automatic'},
                {'label': _("Manual"), 'value': 'manual'}
            ],
            orientation='vertical',
            stretch=False
        )
        self.circ_grid.addWidget(self.circ_method_radio, 2, 0, 1, 2)

        self.circ_manual_dia_label = QtWidgets.QLabel('%s:' % _("Manual Diameter"))
        self.circ_manual_dia_entry = FCDoubleSpinner(callback=self.confirmation_message)
        self.circ_manual_dia_entry.set_precision(self.decimals)
        if str(self.app.options.get("units", "MM")).upper() == 'IN':
            self.circ_manual_dia_entry.set_range(0.2 / 25.4, 15.0 / 25.4)
            self.circ_manual_dia_entry.setSingleStep(0.001)
        else:
            self.circ_manual_dia_entry.set_range(0.2, 15.0)
            self.circ_manual_dia_entry.setSingleStep(0.1)

        self.circ_grid.addWidget(self.circ_manual_dia_label, 3, 0)
        self.circ_grid.addWidget(self.circ_manual_dia_entry, 3, 1)

        self.circ_select_button = QtWidgets.QPushButton(_("Circumference Geometry Select"))
        self.circ_select_button.setToolTip(
            _("Click a closed circular geometry on the canvas and convert it into a pending drill.")
        )
        self.geometry_group_layout.addWidget(self.circ_select_button)

        self.circ_selected_label = QtWidgets.QLabel("%s: 0" % _("Selected circumferences"))
        self.circ_tools_label = QtWidgets.QLabel("%s: 0" % _("Tools"))
        self.circ_status_label = QtWidgets.QLabel("")
        self.geometry_group_layout.addWidget(self.circ_selected_label)
        self.geometry_group_layout.addWidget(self.circ_tools_label)

        self.circ_undo_button = QtWidgets.QPushButton(_("Undo Last Selection"))
        self.circ_undo_button.setToolTip(
            _("Remove only the last temporary circumference selection.")
        )
        self.circ_undo_button.setEnabled(False)
        self.geometry_group_layout.addWidget(self.circ_undo_button)

        self.circ_clear_button = QtWidgets.QPushButton(_("Clear Selection"))
        self.circ_clear_button.setToolTip(
            _("Clear all temporary circumference selections without modifying the Geometry Object.")
        )
        self.circ_clear_button.setEnabled(False)
        self.geometry_group_layout.addWidget(self.circ_clear_button)

        self.circ_remove_original_cb = FCCheckBox(
            _("Remove original geometry circumferences after conversion")
        )
        self.circ_remove_original_cb.setToolTip(
            _("Remove the selected source circumferences only after the Excellon Object is created successfully.")
        )
        self.circ_remove_original_cb.set_value(True)
        self.geometry_group_layout.addWidget(self.circ_remove_original_cb)

        self.geometry_group_layout.addWidget(self.circ_status_label)

        self.circ_generate_button = QtWidgets.QPushButton(_("Generate Excellon"))
        self.circ_generate_button.setIcon(QtGui.QIcon(self.app.resource_location + '/drill16.png'))
        self.circ_generate_button.setToolTip(
            _("Generate one Excellon Object from the selected circumference drills.")
        )
        self.circ_generate_button.setStyleSheet("""
                                QPushButton
                                {
                                    font-weight: bold;
                                }
                                """)
        self.geometry_group_layout.addWidget(self.circ_generate_button)

        self.circ_reset_button = QtWidgets.QPushButton(_("Reset Geometry Tool"))
        self.circ_reset_button.setIcon(QtGui.QIcon(self.app.resource_location + '/reset32.png'))
        self.circ_reset_button.setToolTip(
            _("Reset only the Geometry circumference conversion controls.")
        )
        self.circ_reset_button.setStyleSheet("""
                                QPushButton
                                {
                                    font-weight: bold;
                                }
                                """)
        self.geometry_group_layout.addWidget(self.circ_reset_button)

        self.layout.addStretch()

        self.circular_ring_entry.setEnabled(False)
        self.oblong_ring_entry.setEnabled(False)
        self.square_ring_entry.setEnabled(False)
        self.rectangular_ring_entry.setEnabled(False)
        self.other_ring_entry.setEnabled(False)

        self.dia_entry.setVisible(False)
        self.dia_label.setVisible(False)
        self.factor_label.setVisible(False)
        self.factor_entry.setVisible(False)

        self.ring_frame.setVisible(False)
        # #################################### FINSIHED GUI ###########################
        # #############################################################################

    def confirmation_message(self, accepted, minval, maxval):
        if accepted is False:
            self.app.inform[str, bool].emit('[WARNING_NOTCL] %s: [%.*f, %.*f]' % (_("Edited value is out of range"),
                                                                                  self.decimals,
                                                                                  minval,
                                                                                  self.decimals,
                                                                                  maxval), False)
        else:
            self.app.inform[str, bool].emit('[success] %s' % _("Edited value is within limits."), False)

    def confirmation_message_int(self, accepted, minval, maxval):
        if accepted is False:
            self.app.inform[str, bool].emit('[WARNING_NOTCL] %s: [%d, %d]' %
                                            (_("Edited value is out of range"), minval, maxval), False)
        else:
            self.app.inform[str, bool].emit('[success] %s' % _("Edited value is within limits."), False)
