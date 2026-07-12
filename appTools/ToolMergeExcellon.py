# ##########################################################
# FlatCAM: 2D Post-processing for Manufacturing
# ##########################################################

from PyQt5 import QtWidgets

from appTool import AppTool

from shapely.geometry import Point

from copy import deepcopy
import logging

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

log = logging.getLogger('base')


class ToolMergeExcellon(AppTool):
    toolName = _("Merge Excellon Objects")

    def __init__(self, app):
        AppTool.__init__(self, app)

        self.decimals = self.app.decimals
        self.selected_excellons = []
        self.selection_has_non_excellon = False

        self.ui = MergeExcellonUI(layout=self.layout, app=self.app)
        self.toolName = self.ui.toolName

        self.ui.merge_button.clicked.connect(self.on_merge_click)
        self.ui.close_button.clicked.connect(self.on_close)

        try:
            self.app.collection.view.selectionModel().selectionChanged.connect(
                lambda *args: self.refresh_selection()
            )
        except Exception as e:
            log.debug("ToolMergeExcellon.__init__() selection signal connect failed: %s" % str(e))

    def install(self, icon=None, separator=None, **kwargs):
        AppTool.install(self, icon, separator, **kwargs)

    def run(self, toggle=True):
        self.app.defaults.report_usage("Merge Excellon Objects()")

        if toggle:
            if self.app.ui.splitter.sizes()[0] == 0:
                self.app.ui.splitter.setSizes([1, 1])
            else:
                try:
                    if self.app.ui.tool_scroll_area.widget().objectName() == self.toolName:
                        if not self.app.ui.notebook.currentWidget() is self.app.ui.tool_tab:
                            self.app.ui.notebook.setCurrentWidget(self.app.ui.tool_tab)
                        else:
                            self.on_close()
                            return
                except AttributeError:
                    pass
        else:
            if self.app.ui.splitter.sizes()[0] == 0:
                self.app.ui.splitter.setSizes([1, 1])

        AppTool.run(self)
        self.refresh_selection()
        self.app.ui.notebook.setTabText(2, _("Merge Excellon Objects"))

    def refresh_selection(self):
        self.ui.objects_list.clear()
        self.selected_excellons = []

        selected = [obj for obj in self.app.collection.get_selected() if obj is not None]
        self.selection_has_non_excellon = any(getattr(obj, 'kind', None) != 'excellon' for obj in selected)

        if self.selection_has_non_excellon:
            self.ui.status_label.setText(
                _("This tool requires Excellon Objects selected from the Project panel.")
            )
            self.ui.merge_button.setDisabled(True)
            return

        self.selected_excellons = selected
        for obj in self.selected_excellons:
            drill_count, tool_count, slot_count = self._object_counts(obj)
            self.ui.objects_list.addItem(
                "%s  -  %d drills, %d tools, %d slots" % (
                    obj.options.get('name', ''),
                    drill_count,
                    tool_count,
                    slot_count
                )
            )

        if len(self.selected_excellons) < 2:
            self.ui.status_label.setText(
                _("At least two Excellon Objects are required as drill sources.")
            )
            self.ui.merge_button.setDisabled(True)
            return

        self.ui.status_label.setText(
            _("%d Excellon Objects selected.") % len(self.selected_excellons)
        )
        self.ui.merge_button.setDisabled(False)

    def on_merge_click(self):
        self.refresh_selection()

        selected = self.selected_excellons
        if self.selection_has_non_excellon:
            self.app.inform.emit(
                '[WARNING_NOTCL] %s' %
                _("This tool requires Excellon Objects selected from the Project panel.")
            )
            return

        if len(selected) < 2:
            self.app.inform.emit(
                '[WARNING_NOTCL] %s' %
                _("At least two Excellon Objects are required as drill sources.")
            )
            return

        if self._selection_contains_slots(selected):
            self.app.inform.emit(
                '[WARNING_NOTCL] %s' %
                _("Selected Excellon Objects contain slots, which are not supported by this version of the merge tool.")
            )
            return

        drill_records = self._collect_drill_records(selected)
        if not drill_records:
            self.app.inform.emit(
                '[WARNING_NOTCL] %s' %
                _("At least two Excellon Objects are required as drill sources.")
            )
            return

        if self._has_overlapping_drills(drill_records):
            self.app.inform.emit(
                '[ERROR_NOTCL] %s' %
                _("A new Excellon Object cannot be created because overlapping drills were detected. "
                  "Overlapping drills violate basic PCB design rules.")
            )
            return

        tools = self._build_tools(drill_records)
        all_drills = []
        for tool_data in tools.values():
            all_drills += tool_data['drills']

        outname = self._unique_name("merged_excellon")

        def obj_init(obj_inst, app_inst):
            obj_inst.options['name'] = outname
            obj_inst.tools = deepcopy(tools)
            obj_inst.drills = deepcopy(all_drills)
            obj_inst.create_geometry()
            obj_inst.source_file = app_inst.f_handlers.export_excellon(
                obj_name=obj_inst.options['name'],
                local_use=obj_inst,
                filename=None,
                use_thread=False
            )

        ret = self.app.app_obj.new_object("excellon", outname, obj_init)
        if ret == 'fail':
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Failed."))
            return

        self.app.inform.emit('[success] %s' % _("Excellon Objects merged successfully."))

    def on_close(self):
        try:
            if self.app.ui.tool_scroll_area.widget() is self:
                self.app.ui.tool_scroll_area.takeWidget()
        except Exception as e:
            log.debug("ToolMergeExcellon.on_close() tool widget cleanup failed: %s" % str(e))

        try:
            self.app.ui.notebook.setTabText(2, _("Tool"))
        except Exception:
            pass

        try:
            self.app.ui.notebook.setVisible(True)
            self.app.ui.notebook.setCurrentWidget(self.app.ui.project_tab)
        except Exception as e:
            log.debug("ToolMergeExcellon.on_close() notebook focus failed: %s" % str(e))

    def _object_counts(self, obj):
        drill_count = 0
        slot_count = 0

        for tool_data in getattr(obj, 'tools', {}).values():
            drill_count += len(tool_data.get('drills', []))
            slot_count += len(tool_data.get('slots', []))

        return drill_count, len(getattr(obj, 'tools', {})), slot_count

    def _selection_contains_slots(self, selected):
        for obj in selected:
            for tool_data in getattr(obj, 'tools', {}).values():
                if tool_data.get('slots', []):
                    return True
        return False

    def _collect_drill_records(self, selected):
        drill_records = []

        for obj in selected:
            obj_name = obj.options.get('name', '')
            for tool_id, tool_data in getattr(obj, 'tools', {}).items():
                try:
                    dia = float(tool_data.get('tooldia', tool_data.get('C')))
                except (TypeError, ValueError):
                    continue

                for drill in tool_data.get('drills', []):
                    point = self._as_point(drill)
                    if point is None:
                        continue

                    drill_records.append({
                        'point': point,
                        'x': point.x,
                        'y': point.y,
                        'dia': dia,
                        'radius': dia / 2.0,
                        'source_name': obj_name,
                        'tool_id': tool_id
                    })

        return drill_records

    def _as_point(self, drill):
        if isinstance(drill, Point):
            return Point(drill.x, drill.y)

        try:
            return Point(float(drill[0]), float(drill[1]))
        except Exception:
            return None

    def _has_overlapping_drills(self, drill_records):
        tol = 10 ** (-self.decimals)
        records_count = len(drill_records)

        for first_idx in range(records_count):
            first = drill_records[first_idx]

            for second_idx in range(first_idx + 1, records_count):
                second = drill_records[second_idx]
                center_dist = first['point'].distance(second['point'])

                if center_dist <= tol:
                    return True

                if center_dist < (first['radius'] + second['radius'] - tol):
                    return True

        return False

    def _build_tools(self, drill_records):
        grouped = {}
        for record in drill_records:
            dia_key = float('%.*f' % (self.decimals, record['dia']))
            grouped.setdefault(dia_key, []).append(Point(record['x'], record['y']))

        tools = {}
        for tool_no, dia in enumerate(sorted(grouped.keys()), start=1):
            tools[tool_no] = {
                'tooldia': dia,
                'drills': grouped[dia],
                'slots': []
            }

        return tools

    def _unique_name(self, base_name):
        names = set(self.app.collection.get_names())
        name = base_name
        suffix = 1

        while name in names:
            name = "%s_%d" % (base_name, suffix)
            suffix += 1

        return name


class MergeExcellonUI:
    toolName = _("Merge Excellon Objects")

    def __init__(self, layout, app):
        self.app = app
        self.decimals = self.app.decimals

        self.title_label = QtWidgets.QLabel("<font size=4><b>%s</b></font>" % _("Merge Excellon Objects"))
        layout.addWidget(self.title_label)

        self.help_label = QtWidgets.QLabel(
            _("Hold Ctrl and click at least two Excellon Objects in the Project panel.")
        )
        self.help_label.setWordWrap(True)
        layout.addWidget(self.help_label)

        self.list_label = QtWidgets.QLabel("<b>%s</b>" % _("Selected Excellon Objects"))
        layout.addWidget(self.list_label)

        self.objects_list = QtWidgets.QListWidget()
        self.objects_list.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        self.objects_list.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        layout.addWidget(self.objects_list)

        self.status_label = QtWidgets.QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.merge_button = QtWidgets.QPushButton(_("Merge Selected Excellon Objects"))
        self.merge_button.setDisabled(True)
        layout.addWidget(self.merge_button)

        self.close_button = QtWidgets.QPushButton(_("Close"))
        layout.addWidget(self.close_button)

        layout.addStretch()
