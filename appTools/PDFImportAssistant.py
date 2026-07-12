# ##########################################################
# FlatCAM 9 Neo S2
# PDF Import Assistant for PDF as Geometry Object
# ##########################################################

from PyQt5 import QtCore, QtGui, QtWidgets

from appParsers.PDFGeometryBuilder import PDFGeometryBuilder
from appParsers.PDFImportLimits import (
    PDF_WARN_VECTOR_OPS,
    PDF_MAX_VECTOR_OPS,
    PDF_HIGH_COMPLEXITY_MESSAGE,
    PDF_TOO_COMPLEX_MESSAGE
)
from appParsers.PDFWhiteDrillDetector import detect_white_pdf_drills


class PDFPreviewLabel(QtWidgets.QLabel):
    cropChanged = QtCore.pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(QtCore.Qt.AlignCenter)
        self.setMinimumSize(480, 320)
        self.setMouseTracking(True)
        self._pixmap = None
        self._scaled_rect = QtCore.QRect()
        self._selection = QtCore.QRect()
        self._drag_start = None

    def set_preview_pixmap(self, pixmap):
        self._pixmap = pixmap
        self._selection = QtCore.QRect()
        self._drag_start = None
        self.update()

    def use_full_page(self):
        self._selection = QtCore.QRect()
        self.update()
        self.cropChanged.emit()

    def crop_rect_pixels(self):
        if not self._pixmap or self._selection.isNull() or self._selection.isEmpty():
            return None

        selection = self._selection.normalized().intersected(self._scaled_rect)
        if selection.isNull() or selection.isEmpty():
            return None

        sx = float(self._pixmap.width()) / float(self._scaled_rect.width())
        sy = float(self._pixmap.height()) / float(self._scaled_rect.height())
        x0 = (selection.left() - self._scaled_rect.left()) * sx
        y0 = (selection.top() - self._scaled_rect.top()) * sy
        x1 = (selection.right() - self._scaled_rect.left()) * sx
        y1 = (selection.bottom() - self._scaled_rect.top()) * sy
        return x0, y0, x1, y1

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._pixmap is None:
            return

        painter = QtGui.QPainter(self)
        scaled = self._pixmap.scaled(self.size(), QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
        x = (self.width() - scaled.width()) // 2
        y = (self.height() - scaled.height()) // 2
        self._scaled_rect = QtCore.QRect(x, y, scaled.width(), scaled.height())
        painter.drawPixmap(self._scaled_rect, scaled)

        if not self._selection.isNull() and not self._selection.isEmpty():
            pen = QtGui.QPen(QtGui.QColor(30, 130, 230), 2, QtCore.Qt.SolidLine)
            painter.setPen(pen)
            painter.setBrush(QtGui.QColor(30, 130, 230, 45))
            painter.drawRect(self._selection.normalized())

    def mousePressEvent(self, event):
        if self._pixmap is None or event.button() != QtCore.Qt.LeftButton:
            return
        if not self._scaled_rect.contains(event.pos()):
            return
        self._drag_start = event.pos()
        self._selection = QtCore.QRect(self._drag_start, self._drag_start)
        self.update()

    def mouseMoveEvent(self, event):
        if self._drag_start is None:
            return
        pos = event.pos()
        pos.setX(max(self._scaled_rect.left(), min(pos.x(), self._scaled_rect.right())))
        pos.setY(max(self._scaled_rect.top(), min(pos.y(), self._scaled_rect.bottom())))
        self._selection = QtCore.QRect(self._drag_start, pos)
        self.update()

    def mouseReleaseEvent(self, event):
        if self._drag_start is None:
            return
        self.mouseMoveEvent(event)
        self._drag_start = None
        self.cropChanged.emit()


class PDFImportAssistantDialog(QtWidgets.QDialog):
    def __init__(self, app, filename, analysis, advisor, parent=None, enable_white_drill_extraction=False):
        super().__init__(parent)
        self.app = app
        self.filename = filename
        self.analysis = analysis or {}
        self.advisor = advisor or {}
        self.page_count = int(self.analysis.get('pages') or 1)
        self.page_number = 1
        self.page_rect = None
        self.render_zoom = 1.5
        self.crop_rect = None
        self.white_drill_result = None
        self.enable_white_drill_extraction = bool(enable_white_drill_extraction)
        self._updating = False

        self.setWindowTitle('PDF Import Assistant')
        self.resize(980, 680)

        self.preview = PDFPreviewLabel()
        self.preview.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self.preview.cropChanged.connect(self.update_complexity)

        self.page_spin = QtWidgets.QSpinBox()
        self.page_spin.setMinimum(1)
        self.page_spin.setMaximum(max(self.page_count, 1))
        self.page_spin.setValue(1)
        self.page_spin.valueChanged.connect(self.on_page_changed)

        self.full_page_btn = QtWidgets.QPushButton('Use Full Page')
        self.full_page_btn.clicked.connect(self.on_full_page)

        self.info_label = QtWidgets.QLabel()
        self.info_label.setWordWrap(True)
        self.info_label.setMinimumWidth(310)
        self.info_label.setMaximumWidth(330)
        self.info_label.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Maximum)

        self.complexity_bar = QtWidgets.QProgressBar()
        self.complexity_bar.setMinimum(0)
        self.complexity_bar.setMaximum(PDF_MAX_VECTOR_OPS)

        self.status_label = QtWidgets.QLabel()
        self.status_label.setWordWrap(True)
        self.status_label.setMaximumWidth(330)
        self.status_label.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Maximum)

        self.flip_horizontal_cb = QtWidgets.QCheckBox('Flip Horizontal before import')
        self.flip_horizontal_cb.setChecked(False)
        self.flip_horizontal_cb.setVisible(self.flip_controls_available())

        self.flip_vertical_cb = QtWidgets.QCheckBox('Flip Vertical before import')
        self.flip_vertical_cb.setChecked(False)
        self.flip_vertical_cb.setVisible(self.flip_controls_available())

        self.flip_info_label = QtWidgets.QLabel('Flip options are applied to the imported result.')
        self.flip_info_label.setWordWrap(True)
        self.flip_info_label.setMaximumWidth(330)
        self.flip_info_label.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Maximum)
        self.flip_info_label.setVisible(self.flip_controls_available())

        self.extract_white_drills_cb = QtWidgets.QCheckBox(
            'Extract white circular PDF geometry as Excellon drills'
        )
        self.extract_white_drills_cb.setChecked(False)
        self.extract_white_drills_cb.setVisible(self.white_drill_controls_available())

        self.white_drill_label = QtWidgets.QLabel()
        self.white_drill_label.setWordWrap(True)
        self.white_drill_label.setMaximumWidth(330)
        self.white_drill_label.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Maximum)
        self.white_drill_label.setVisible(self.white_drill_controls_available())

        button_box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)

        controls = QtWidgets.QHBoxLayout()
        controls.addWidget(QtWidgets.QLabel('Page:'))
        controls.addWidget(self.page_spin)
        controls.addWidget(self.full_page_btn)
        controls.addStretch()

        side_panel_width = 360
        side_content_width = 330
        self.side_panel = QtWidgets.QWidget()
        self.side_panel.setFixedWidth(side_panel_width)
        self.side_panel.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Expanding)

        self.side_scroll = QtWidgets.QScrollArea()
        self.side_scroll.setWidgetResizable(True)
        self.side_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.side_scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.side_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.side_scroll.setFixedWidth(side_panel_width)

        self.side_scroll_content = QtWidgets.QWidget()
        self.side_scroll_content.setMinimumWidth(side_content_width)
        self.side_scroll_content.setMaximumWidth(side_content_width)
        self.side_scroll_content.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.MinimumExpanding)

        side = QtWidgets.QVBoxLayout(self.side_scroll_content)
        side.addWidget(self.info_label)
        side.addWidget(QtWidgets.QLabel('Estimated Complexity:'))
        side.addWidget(self.complexity_bar)
        side.addWidget(self.status_label)
        side.addWidget(self.flip_horizontal_cb)
        side.addWidget(self.flip_vertical_cb)
        side.addWidget(self.flip_info_label)
        side.addWidget(self.extract_white_drills_cb)
        side.addWidget(self.white_drill_label)
        side.addStretch()
        self.side_scroll.setWidget(self.side_scroll_content)

        side_outer = QtWidgets.QVBoxLayout(self.side_panel)
        side_outer.setContentsMargins(0, 0, 0, 0)
        side_outer.addWidget(self.side_scroll, 1)
        side_outer.addWidget(button_box)

        main = QtWidgets.QHBoxLayout()
        left = QtWidgets.QVBoxLayout()
        left.addLayout(controls)
        left.addWidget(self.preview, 1)
        main.addLayout(left, 1)
        main.addWidget(self.side_panel)
        self.setLayout(main)

        self.render_page()

    def flip_controls_available(self):
        content_type = (self.analysis.get('content_type') or 'unknown').lower()
        return content_type in ['vector', 'mixed']

    def white_drill_controls_available(self):
        if not self.enable_white_drill_extraction:
            return False

        content_type = (self.analysis.get('content_type') or 'unknown').lower()
        return content_type in ['vector', 'mixed']

    def drill_object_name(self):
        base_name = self.filename.split('/')[-1].split('\\')[-1].rpartition('.')[0]
        if not base_name:
            base_name = self.filename.split('/')[-1].split('\\')[-1]
        return base_name + '_drills'

    @property
    def extract_white_drills(self):
        if not self.white_drill_controls_available():
            return False
        return self.extract_white_drills_cb.isChecked()

    @property
    def flip_horizontal(self):
        if not self.flip_controls_available():
            return False
        return self.flip_horizontal_cb.isChecked()

    @property
    def flip_vertical(self):
        if not self.flip_controls_available():
            return False
        return self.flip_vertical_cb.isChecked()

    def on_page_changed(self, value):
        if self._updating:
            return
        self.page_number = int(value)
        self.preview.use_full_page()
        self.render_page()

    def on_full_page(self):
        self.preview.use_full_page()
        self.update_complexity()

    def render_page(self):
        try:
            import fitz
            doc = fitz.open(self.filename)
            try:
                page = doc.load_page(self.page_number - 1)
                self.page_rect = page.rect
                pixmap = page.get_pixmap(matrix=fitz.Matrix(self.render_zoom, self.render_zoom), alpha=False)
                image_format = QtGui.QImage.Format_RGB888 if pixmap.n >= 3 else QtGui.QImage.Format_Grayscale8
                qimage = QtGui.QImage(pixmap.samples, pixmap.width, pixmap.height, pixmap.stride, image_format).copy()
                self.preview.set_preview_pixmap(QtGui.QPixmap.fromImage(qimage))
            finally:
                doc.close()
        except Exception as e:
            self.status_label.setText('Preview failed: %s' % str(e))
            return

        self.update_complexity()

    def selected_crop_rect_pdf(self):
        if self.page_rect is None:
            return None

        pixels = self.preview.crop_rect_pixels()
        if pixels is None:
            return None

        x0, y0, x1, y1 = pixels
        x_scale = float(self.page_rect.width) / float(self.preview._pixmap.width())
        y_scale = float(self.page_rect.height) / float(self.preview._pixmap.height())
        left = min(x0, x1) * x_scale
        right = max(x0, x1) * x_scale
        top = min(y0, y1) * y_scale
        bottom = max(y0, y1) * y_scale
        if right - left <= 1.0 or bottom - top <= 1.0:
            return None
        return left, top, right, bottom

    def update_complexity(self):
        self.crop_rect = self.selected_crop_rect_pdf()
        content_type = self.analysis.get('content_type') or 'unknown'
        source = self.advisor.get('source') or 'Unknown'
        content = self.advisor.get('content') or content_type
        recommended = self.advisor.get('recommended_import') or 'PDF as Geometry Object'
        confidence = float(self.analysis.get('confidence') or 0.0) * 100.0

        area_text = 'Full page'
        if self.page_rect is not None:
            rect = self.crop_rect
            if rect is None:
                width_mm = float(self.page_rect.width) * 25.4 / 72.0
                height_mm = float(self.page_rect.height) * 25.4 / 72.0
            else:
                width_mm = abs(rect[2] - rect[0]) * 25.4 / 72.0
                height_mm = abs(rect[3] - rect[1]) * 25.4 / 72.0
            area_text = '%.3f x %.3f mm' % (width_mm, height_mm)

        complexity = self.estimate_complexity(content_type)
        warning_limit = PDF_WARN_VECTOR_OPS
        limit = PDF_MAX_VECTOR_OPS
        self.complexity_bar.setMaximum(limit)
        self.complexity_bar.setValue(min(complexity, limit))

        if complexity <= int(warning_limit * 0.35):
            status = 'Safe to import'
        elif complexity <= int(warning_limit * 0.70):
            status = 'Medium complexity'
        elif complexity <= warning_limit:
            status = 'High complexity'
        elif complexity <= limit:
            status = 'High complexity warning'
        else:
            status = 'Too Complex - reduce crop area or simplify the PDF'

        self.info_label.setText(
            'PDF Source: %s\nConfidence: %.0f%%\nContent: %s\nPages: %s / %s\n'
            'Selected Area: %s\nRecommended Workflow: %s' %
            (source, confidence, content, self.page_number, self.page_count, area_text, recommended)
        )
        recommendations = ''
        if complexity > limit:
            recommendations = (
                '\n%s\n' % PDF_TOO_COMPLEX_MESSAGE +
                '\nRecommendations:\n'
                '- Reduce crop area.\n'
                '- Remove decorative artwork.\n'
                '- Export only the PCB layer.\n'
                '- Simplify the PDF.'
            )
        elif complexity > warning_limit:
            recommendations = '\n%s' % PDF_HIGH_COMPLEXITY_MESSAGE
        self.status_label.setText(
            'Estimated Vector Operations: %s / %s\nStatus: %s%s' %
            (complexity, limit, status, recommendations)
        )
        visible_flip = self.flip_controls_available()
        self.flip_horizontal_cb.setVisible(visible_flip)
        self.flip_vertical_cb.setVisible(visible_flip)
        self.flip_info_label.setVisible(visible_flip)
        self.update_white_drill_estimate(content_type)

    def update_white_drill_estimate(self, content_type):
        if not self.white_drill_controls_available():
            self.white_drill_result = None
            self.extract_white_drills_cb.setVisible(False)
            self.white_drill_label.setVisible(False)
            return

        self.extract_white_drills_cb.setVisible(True)
        self.white_drill_label.setVisible(True)

        content_type = (content_type or 'unknown').lower()
        if content_type not in ['vector', 'mixed']:
            self.white_drill_result = None
            self.white_drill_label.setText('')
            return

        try:
            self.white_drill_result = detect_white_pdf_drills(
                self.filename,
                page_number=self.page_number,
                crop_rect=self.crop_rect
            )
        except Exception as e:
            self.white_drill_result = None
            self.white_drill_label.setText(
                'Estimated Excellon Extraction\n\n'
                'White drill estimation failed: %s' % str(e)
            )
            return

        candidate_count = self.white_drill_result.get('candidate_count', 0) if self.white_drill_result else 0
        if candidate_count <= 0:
            self.white_drill_label.setText(
                'Estimated Excellon Extraction\n\n'
                'Estimated Excellon Object:\n%s\n\n'
                'No white circular drill candidates detected.' % self.drill_object_name()
            )
            return

        tools = self.white_drill_result.get('tools', {})
        lines = [
            'Estimated Excellon Extraction',
            '',
            'Estimated Excellon Object:',
            self.drill_object_name(),
            '',
            'Estimated candidates: %d' % candidate_count,
            'Estimated tools: %d' % len(tools),
            '',
            'Diameters:'
        ]

        for diameter_key in sorted(tools, key=lambda key: float(key)):
            lines.append('* %.4f mm (%d)' % (float(diameter_key), len(tools[diameter_key])))

        source = (self.analysis.get('source') or '').lower()
        if content_type == 'mixed' or source in ['coreldraw', 'corel']:
            lines += ['', 'WARNING: Verify detected drills before manufacturing.']

        self.white_drill_label.setText('\n'.join(lines))

    def estimate_complexity(self, content_type):
        try:
            if content_type in ['vector', 'mixed']:
                return PDFGeometryBuilder(app=self.app).estimate_vector_complexity(
                    self.filename,
                    page_number=self.page_number,
                    page_count=self.page_count,
                    crop_rect=self.crop_rect
                )

            if content_type == 'raster':
                import fitz
                doc = fitz.open(self.filename)
                try:
                    page = doc.load_page(self.page_number - 1)
                    clip = fitz.Rect(*self.crop_rect) if self.crop_rect is not None else None
                    pix = page.get_pixmap(matrix=fitz.Matrix(0.5, 0.5), clip=clip, alpha=False)
                    return max(1, int((pix.width * pix.height) / 2500.0))
                finally:
                    doc.close()
        except Exception:
            return PDF_MAX_VECTOR_OPS + 1

        return 0
