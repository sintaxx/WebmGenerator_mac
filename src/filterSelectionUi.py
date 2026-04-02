"""
FilterSelectionUi — PySide6 rewrite (Phase 3).
Replaces Tkinter ttk.Frame / ScrolledFrame / Canvas with QWidget / QScrollArea / QPainter.
All business-logic methods are preserved verbatim; only the rendering/input backend changed.
"""

import os
import copy
import json
import logging
import threading
import colorsys
from math import atan2, pi
from threading import Lock

import numpy as np

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QScrollArea, QSlider, QDoubleSpinBox,
    QCheckBox, QComboBox, QSizePolicy, QMenu, QMessageBox,
    QApplication, QGroupBox,
)
from PySide6.QtCore import Qt, QTimer, QPoint
from PySide6.QtGui import QPainter, QColor, QPen, QFont, QCursor

try:
    from .filterSpec import selectableFilters
    from .encodingUtils import cleanFilenameForFfmpeg
    from .filterValuePair import FilterValuePair
    from .platformUtils import is_macos
except ImportError:
    from filterSpec import selectableFilters
    from encodingUtils import cleanFilenameForFfmpeg
    from filterValuePair import FilterValuePair
    from platformUtils import is_macos


def escapeForFfmpegFilterArg(s):
    """Escape a string for use as an ffmpeg filter argument value."""
    if s is None:
        return ''
    s = str(s)
    s = s.replace('\\', '\\\\')
    s = s.replace(':', '\\:')
    s = s.replace("'", "\\'")
    return s


specCounter = 0

def getSpecificationNumber():
    global specCounter
    specCounter += 1
    return specCounter


# ---------------------------------------------------------------------------
# Value-timeline canvas widget (replaces tk.Canvas canvasValueTimeline)
# ---------------------------------------------------------------------------

class _ValueTimelineWidget(QWidget):
    """QPainter-based replacement for the Tkinter canvas used to display
    key-value animation curves."""

    def __init__(self, ui, parent=None):
        super().__init__(parent)
        self.ui = ui          # FilterSelectionUi instance
        self.setMinimumHeight(150)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self._mouseDown = False
        self._seekX = -1      # current seek-pointer x pixel
        self.setStyleSheet('background: #1E1E1E;')

        self._ctx_menu = QMenu(self)
        self._ctx_menu.addAction('Add key value',    lambda: self.ui.addKeyValue())
        self._ctx_menu.addAction('Remove key value', lambda: self.ui.removeKeyValue())
        self._ctx_menu.addAction('Clear all key values',       lambda: self.ui.clearKeyValues())
        self._ctx_menu.addAction('Clear all Group key values', lambda: self.ui.clearAllGrpupKeyValues())

    # ------ public helpers used by FilterSelectionUi ------
    def winfo_width(self):
        return max(self.width(), 1)

    def winfo_height(self):
        return max(self.height(), 1)

    def setSeekX(self, x):
        self._seekX = x
        self.update()

    def scheduleRepaint(self):
        QTimer.singleShot(0, self.update)

    # ------ Qt overrides ------
    def paintEvent(self, event):
        painter = QPainter(self)
        W = self.width()
        H = self.height()

        painter.fillRect(0, 0, W, H, QColor('#1E1E1E'))

        ui = self.ui
        controller = ui.controller
        if controller is None:
            return

        duration = controller.getClipDuration()
        if not duration:
            return

        # ---- tick marks ----
        tickIncrement = duration / 31
        tickStart = -tickIncrement
        painter.setPen(QPen(QColor('white'), 1))
        font = QFont()
        font.setPointSize(7)
        painter.setFont(font)
        while True:
            tickStart += tickIncrement
            tx = int((tickStart / duration) * W)
            if tx < 0:
                continue
            if tx >= W:
                break
            painter.drawLine(tx, 0, tx, 5)
            painter.drawText(tx + 1, 14, '{:.2f}'.format(tickStart))

        # ---- key value curve ----
        fvp = ui.activeCommandFilterValuePair
        if fvp is not None:
            keyList = fvp.getKeyValues()
            valMax, valMin = float('-inf'), float('inf')
            for ts, value, real in keyList:
                valMax = max(valMax, value)
                valMin = min(valMin, value)

            valRange = abs(valMax - valMin) * 0.25
            if valRange == 0:
                valRange = 0.25
            valMin -= valRange
            valMax += valRange
            valRange = abs(valMax - valMin)
            effH = H - 20
            offY = 10

            # vertical lines for real keyframes
            for ts, value, real in keyList:
                if real:
                    tx = int((ts / duration) * W)
                    painter.setPen(QPen(QColor('#375e6b'), 5))
                    painter.drawLine(tx, 20, tx, 130)
                    painter.setPen(QPen(QColor('#69bfdb'), 1))
                    painter.drawLine(tx, 20, tx, 130)

            # curve
            lastX, lastY = None, None
            painter.setPen(QPen(QColor('#db6986'), 1))
            for ts, value, real in sorted(keyList):
                tx = int((ts / duration) * W)
                ty = int(offY + (effH - (((value - valMin) / valRange) * effH)))
                if lastX is None:
                    lastX, lastY = 0, ty
                painter.drawLine(lastX, lastY, tx, ty)
                if real:
                    painter.setBrush(QColor('#db6986'))
                    painter.drawEllipse(tx - 5, ty - 4, 10, 8)
                else:
                    painter.setBrush(QColor('white'))
                    painter.drawEllipse(tx - 2, ty - 2, 4, 3)
                lastX, lastY = tx, ty
            if lastX is not None:
                painter.drawLine(lastX, lastY, W, lastY)

            # label bar
            painter.fillRect(0, 130, W, 20, QColor('#26414a'))

            # value labels near keyframes
            posSeconds = controller.getCurrentPlaybackPosition()
            for ts, value, real in sorted(keyList, key=lambda x: abs(x[0] - posSeconds), reverse=True):
                if real:
                    tx = int((ts / duration) * W)
                    ty = int(offY + (effH - (((value - valMin) / valRange) * effH)))
                    lbl = '{:.2f}'.format(value)
                    fm = painter.fontMetrics()
                    bw = fm.horizontalAdvance(lbl) + 4
                    painter.fillRect(tx - bw // 2, 132, bw, 14, QColor('#375e6b'))
                    painter.setPen(QColor('white'))
                    painter.drawText(tx - bw // 2 + 2, 144, lbl)

            # mode text
            modeText = 'No Mode'
            if fvp.videoSpaceAxis in ('yaw', 'pitch'):
                modeText = '[VR Look Mode - Ctrl-Click once on video to control head {} with mouse.]'.format(fvp.videoSpaceAxis)
            elif fvp.videoSpaceAxis == 'deg':
                modeText = '[Angle Snap Mode - Ctrl click on video to define a rotation angle, snapped to 90 degrees.]'
            elif ui.sourceTargetVectorSet:
                modeText = '[XY Warp Mode - Ctrl click a point on the video to warp it to the target position.]'
            painter.setPen(QColor('white'))
            painter.drawText(2, H - 4, '{} {} subdiv:{}'.format(
                fvp.commandvarName, modeText, fvp.interpolationFactor))

        # ---- seek pointer ----
        if self._seekX >= 0:
            painter.setPen(QPen(QColor('white'), 1))
            painter.drawLine(self._seekX, 20, self._seekX, H - 5)

        # ---- filter failed overlay ----
        if ui.filterFailed:
            cy = H // 2
            painter.fillRect(0, cy - 10, W, 20, QColor('#ff0000'))
            painter.setPen(QColor('white'))
            painter.setFont(QFont())
            painter.drawText(W // 2 - 40, cy + 5, 'Filter Failed!')

    def mousePressEvent(self, event):
        self.setFocus()
        if event.button() == Qt.LeftButton:
            self._mouseDown = True
            self.ui.controller and self.ui.controller.seekToPercent(event.x() / self.winfo_width())
        elif event.button() == Qt.RightButton:
            if self.ui.activeCommandFilterValuePair is not None:
                self.ui.timeline_canvas_last_right_click_x = event.x()
                self._ctx_menu.exec(QCursor.pos())

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._mouseDown = False

    def mouseMoveEvent(self, event):
        if self._mouseDown:
            self.ui.controller and self.ui.controller.seekToPercent(event.x() / self.winfo_width())

    def wheelEvent(self, event):
        if self.ui.controller is None:
            return
        ctrl  = bool(event.modifiers() & Qt.ControlModifier)
        shift = bool(event.modifiers() & Qt.ShiftModifier)
        delta = 1 if event.angleDelta().y() > 0 else -1
        duration = self.ui.controller.getClipDuration()
        fvp = self.ui.activeCommandFilterValuePair
        if fvp is None:
            return
        ex = event.position().x() if hasattr(event, 'position') else event.x()
        ex = int(ex)
        sep = self.ui.keyValueSeparation
        if shift:
            fvp.incrementAllKeyValues(10 * delta if ctrl else delta)
        else:
            for ts, value, real in fvp.getKeyValues():
                if real:
                    tx = int((ts / duration) * self.winfo_width())
                    if ex - sep < tx < ex + sep:
                        fvp.incrementKeyValue(ts, 10 * delta if ctrl else delta)
                        self.ui.refreshtimeLineForNewClip()
                        self.ui.timeline_canvas_last_right_click_x = ex
                        self.ui.controller.seekToPercent(ex / self.winfo_width())
                        break

    def keyPressEvent(self, event):
        # forward to the parent UI's keyboard handlers
        self.ui.keyPressEvent(event)

    def resizeEvent(self, event):
        self.update()
        if self.ui.controller:
            self.ui.refreshtimeLineForNewClip()


# ---------------------------------------------------------------------------
# FilterSpecification — one filter card in the stack
# ---------------------------------------------------------------------------

class FilterSpecification(QWidget):

    def __init__(self, master, controller, spec, filterId, *args, **kwargs):
        super().__init__(master)
        self.filterId  = filterId
        self.enabled   = spec.get('enabled', True)
        self.isAudioFilter = spec.get('isAudioFilter', False)
        self.spec       = spec
        self.controller = controller
        self.autoNumber = getSpecificationNumber()
        self.timelineReinit    = spec.get('timelineReinit', False)
        self.encodingStageFilter = spec.get('encodingStageFilter', False)
        self.timelineSupport   = spec.get('timelineSupport', False)
        self.presets           = spec.get('presets', [])
        self.filterValuePairs  = []
        self.rectProps         = {}
        self._timelineStart    = ''
        self._timelineEnd      = ''

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)

        # colour strip
        hue = (self.autoNumber % 20) / 20.0
        r, g, b = colorsys.hsv_to_rgb(hue, 1.0, 1.0)
        colour = '#{:02x}{:02x}{:02x}'.format(int(r * 255), int(g * 255), int(b * 255))
        strip = QLabel()
        strip.setFixedHeight(3)
        strip.setStyleSheet('background: {};'.format(colour))
        layout.addWidget(strip)

        # name label
        nameLabel = QLabel(spec['name'])
        nameLabel.setStyleSheet('font-weight: bold; color: #ffffff;')
        nameLabel.setAlignment(Qt.AlignCenter)
        layout.addWidget(nameLabel)

        # description
        desc = spec.get('desc', '')
        if desc:
            descLabel = QLabel(desc)
            descLabel.setWordWrap(True)
            descLabel.setAlignment(Qt.AlignCenter)
            descLabel.setStyleSheet('color: #aaaaaa; font-size: 10px;')
            layout.addWidget(descLabel)

        # action buttons row
        actionRow = QHBoxLayout()
        self.btnRemove = QPushButton('Remove')
        self.btnRemove.setToolTip('Remove this filter from the filter stack.')
        self.btnRemove.clicked.connect(self.remove)
        actionRow.addWidget(self.btnRemove)

        self.btnToggleEnabled = QPushButton('Enabled' if self.enabled else 'Disabled')
        self.btnToggleEnabled.setFixedWidth(70)
        self.btnToggleEnabled.setToolTip('Disable this filter but keep it in the filter stack.')
        self.btnToggleEnabled.clicked.connect(self.toggleEnabled)
        if not self.enabled:
            self.btnToggleEnabled.setStyleSheet('color: #888;')
        actionRow.addWidget(self.btnToggleEnabled)

        self.btnDown = QPushButton('▼')
        self.btnDown.setFixedWidth(24)
        self.btnDown.clicked.connect(self.moveFilterUpStack)
        actionRow.addWidget(self.btnDown)

        self.btnUp = QPushButton('▲')
        self.btnUp.setFixedWidth(24)
        self.btnUp.clicked.connect(self.moveFilterDownStack)
        actionRow.addWidget(self.btnUp)

        layout.addLayout(actionRow)

        # timeline start/end (if timelineSupport)
        if self.timelineSupport:
            tlRow1 = QHBoxLayout()
            tlRow1.addWidget(QLabel('Start at:'))
            self._spinTimelineStart = QDoubleSpinBox()
            self._spinTimelineStart.setRange(0, 100)
            self._spinTimelineStart.setSingleStep(0.1)
            self._spinTimelineStart.setDecimals(3)
            self._spinTimelineStart.valueChanged.connect(self._timelineStartChanged)
            tlRow1.addWidget(self._spinTimelineStart)
            btnStartNow = QPushButton('Current Time')
            btnStartNow.clicked.connect(self.getCurrenttimeForStart)
            tlRow1.addWidget(btnStartNow)
            layout.addLayout(tlRow1)

            tlRow2 = QHBoxLayout()
            tlRow2.addWidget(QLabel('End at:'))
            self._spinTimelineEnd = QDoubleSpinBox()
            self._spinTimelineEnd.setRange(0, 100)
            self._spinTimelineEnd.setSingleStep(0.1)
            self._spinTimelineEnd.setDecimals(3)
            self._spinTimelineEnd.valueChanged.connect(self._timelineEndChanged)
            tlRow2.addWidget(self._spinTimelineEnd)
            btnEndNow = QPushButton('Current Time')
            btnEndNow.clicked.connect(self.getCurrenttimeForEnd)
            tlRow2.addWidget(btnEndNow)
            layout.addLayout(tlRow2)

        # presets
        if self.presets:
            self._presetCombo = QComboBox()
            self._presetCombo.addItem('--No preset--')
            for p in self.presets:
                self._presetCombo.addItem(p['preset_name'])
            self._presetCombo.currentTextChanged.connect(self.presetUpdated)
            layout.addWidget(self._presetCombo)

        # filter value pairs
        for param in spec.get('params', []):
            if param.get('type') == 'timelineStart':
                if self.timelineSupport:
                    try:
                        self._spinTimelineStart.setValue(float(param.get('value', 0)))
                    except Exception:
                        pass
            elif param.get('type') == 'timelineEnd':
                if self.timelineSupport:
                    try:
                        self._spinTimelineEnd.setValue(float(param.get('value', 0)))
                    except Exception:
                        pass
            else:
                fvp = FilterValuePair(self, self, param)
                self.filterValuePairs.append(fvp)
                layout.addWidget(fvp)

        # extensions
        if 'v360ViewHeadLogger' in spec.get('extensions', []):
            btn = QPushButton('Run head motion logger')
            btn.clicked.connect(self.headMotionLogger)
            layout.addWidget(btn)

        if 'growShrinkBox' in spec.get('extensions', []):
            growRow = QHBoxLayout()
            for lbl, inc in [('-10', -10), ('-1', -1), ('+1', 1), ('+10', 10)]:
                b = QPushButton(lbl)
                b.setFixedWidth(36)
                b.clicked.connect(lambda checked=False, i=inc: self.incrementBox(i))
                growRow.addWidget(b)
            layout.addLayout(growRow)

        if self.rectProps:
            btnPop = QPushButton('Populate from selection')
            btnPop.clicked.connect(self.populateRectPropValues)
            layout.addWidget(btnPop)

        self.setStyleSheet('background: #2a2a2a; border: 1px solid #444; margin: 1px;')
        if not self.enabled:
            self.setStyleSheet('background: #1e1e1e; border: 1px solid #333; margin: 1px; opacity: 0.6;')

    # ---- timeline start/end helpers ----

    def _timelineStartChanged(self, value):
        self._timelineStart = str(value)
        self.recaculateFilters('timelineStartChanged')
        try:
            self.controller.seekToTimelinePoint(value)
        except Exception:
            pass

    def _timelineEndChanged(self, value):
        self._timelineEnd = str(value)
        self.recaculateFilters('timelineEndChanged')
        try:
            self.controller.seekToTimelinePoint(value)
        except Exception:
            pass

    # ---- shims for old StringVar get() ----

    class _SpinProxy:
        def __init__(self, spin):
            self._spin = spin
        def get(self):
            return str(self._spin.value())
        def set(self, v):
            try:
                self._spin.setValue(float(v))
            except Exception:
                pass

    @property
    def timelineStart(self):
        if self.timelineSupport:
            return self._SpinProxy(self._spinTimelineStart)
        class _Empty:
            def get(self): return ''
            def set(self, v): pass
        return _Empty()

    @property
    def timelineEnd(self):
        if self.timelineSupport:
            return self._SpinProxy(self._spinTimelineEnd)
        class _Empty:
            def get(self): return ''
            def set(self, v): pass
        return _Empty()

    # ---- public API ----

    def presetUpdated(self, newPreset):
        for p in self.presets:
            if p.get('preset_name', '') == newPreset:
                for k, v in p.items():
                    for fvp in self.filterValuePairs:
                        if fvp.n == k:
                            fvp.valueVar.set(v)

    def getStringValue(self, valueToken):
        return self.controller.getStringValue(valueToken)

    def incrementBox(self, inc):
        videoAR = self.controller.getViideoAR()
        for fvp in self.filterValuePairs:
            if fvp.rectProp is None:
                continue
            _, value = fvp.getValuePair()
            value = int(value)
            if fvp.rectProp == 'x':
                fvp.valueVar.set(str(int(value - (inc * videoAR))))
                fvp.valueUpdated()
            elif fvp.rectProp == 'y':
                fvp.valueVar.set(str(value - inc))
                fvp.valueUpdated()
            elif fvp.rectProp == 'w':
                fvp.valueVar.set(str(int(value + ((inc * videoAR) * 2))))
                fvp.valueUpdated()
            elif fvp.rectProp == 'h':
                fvp.valueVar.set(str(value + (inc * 2)))
                fvp.valueUpdated()

    def headMotionLogger(self):
        clip = self.controller.getCurrentClip()
        filename = clip['filename']
        abA = clip['start']
        abB = clip['end']
        self.controller.pause()
        import mpv
        player = mpv.MPV(loglevel='error', loop='1', mute=True,
                         background='color' if is_macos() else '#282828',
                         log_handler=print, autofit_larger='1280',
                         autoload_files='no', cover_art_auto='no',
                         audio_file_auto='no', sub_auto='no')
        player.command('load-script', os.path.join('src', 'vrscript.lua'))
        self.headmotions = []
        self.initivalues = {}

        @player.message_handler('vrscript')
        def my_handler(cmdType, direction, timepos, value):
            try:
                cmdTypeStr = cmdType.decode('utf8')
                directionStr = direction.decode('utf8')
            except Exception:
                cmdTypeStr = cmdType
                directionStr = direction
            if cmdTypeStr == 'resetRecording':
                self.headmotions = []
            elif cmdTypeStr == 'setValue':
                self.headmotions.append((float(timepos), directionStr, float(value)))
            elif cmdTypeStr == 'setInitValue':
                self.headmotions.append((float(abA), directionStr, float(value)))
                self.initivalues[directionStr] = float(value)

        player.start = abA
        player.loop = 'inf'
        player.ab_loop_a = abA
        player.ab_loop_b = abB
        player.play(filename)
        player.wait_until_playing()
        iData = {}
        for fvp in self.filterValuePairs:
            fiparam, fivalue = fvp.getValuePair()
            iData[fiparam] = fivalue
        player.command('script-message', 'vrscript_initialiseValues',
                        iData.get('in_proj', ''), iData.get('out_proj', ''),
                        iData.get('in_trans', ''), iData.get('out_trans', ''),
                        iData.get('h_flip', ''), iData.get('ih_flip', ''),
                        iData.get('iv_flip', ''), iData.get('in_stereo', ''),
                        iData.get('out_stereo', ''), iData.get('w', ''),
                        iData.get('h', ''), iData.get('yaw', ''),
                        iData.get('pitch', ''), iData.get('roll', ''),
                        iData.get('d_fov', ''), iData.get('id_fov', ''),
                        iData.get('interp', ''))
        player.wait_for_shutdown()
        player.terminate()
        if len(self.headmotions) > 0:
            motionMaps = {}
            for ts, direction, val in sorted(self.headmotions):
                if abA <= ts <= abB:
                    motionMaps.setdefault(direction, []).append((ts - abA, val))
            firstfvp = None
            for k, v in motionMaps.items():
                for fvp in self.filterValuePairs:
                    try:
                        if fvp.videoSpaceAxis == k and fvp.commandVarAvaliable:
                            if not fvp.commandVarEnabled:
                                fvp.toggleTimelineCmdMode()
                            firstfvp = fvp
                            fvp.deactivateTimeLineSection()
                            fvp.toggleTimelineSelection()
                            fvp.keyValues = dict(v)
                            fvp.valueUpdated()
                            fvp.deactivateTimeLineSection()
                    except Exception as e:
                        print(e)
            if firstfvp is not None:
                firstfvp.toggleTimelineSelection()
            self.controller.recaculateFilters('v360 update final')
            for direction, value in self.initivalues.items():
                for fvp in self.filterValuePairs:
                    if fvp.videoSpaceAxis == direction and fvp.commandVarAvaliable:
                        fvp.valueVar.set(str(value))
                        fvp.valueUpdated()
        self.headmotions = []
        self.controller.play()

    def packself(self):
        pass  # Qt layout manages visibility

    def getBoundingBox(self, seconds):
        box = {'x': None, 'y': None, 'w': None, 'h': None}
        for fvp in self.filterValuePairs:
            if fvp.param.get('n') in box:
                box[fvp.param.get('n')] = fvp.getPredictedValue(seconds)
        return box

    def cycleSelectedPropertySameGroup(self, activeValuePair):
        enableablefilters = [x for x in self.filterValuePairs if x.commandVarEnabled]
        if activeValuePair not in enableablefilters:
            return
        activeInd = enableablefilters.index(activeValuePair)
        if activeValuePair.rectPropGroup is None:
            return
        for offset in range(1, len(enableablefilters) * 2):
            nxt = enableablefilters[(activeInd + offset) % len(enableablefilters)]
            if nxt != activeValuePair and activeValuePair.rectPropGroup == nxt.rectPropGroup:
                nxt.toggleTimelineSelection()
                break

    def cycleSelectedProperty(self, activeValuePair):
        enableablefilters = [x for x in self.filterValuePairs if x.commandVarEnabled]
        if activeValuePair not in enableablefilters:
            return
        activeInd = enableablefilters.index(activeValuePair)
        nxt = enableablefilters[(activeInd + 1) % len(enableablefilters)]
        if nxt != activeValuePair:
            nxt.toggleTimelineSelection()

    def setActiveTimeLineValue(self, activeValuePair):
        self.controller.setActiveTimeLineValue(activeValuePair)
        self.controller.canvasValueTimeline.setFocus()

    def moveFilterUpStack(self):
        self.controller.shiftFilterOnStack(self, 1)

    def moveFilterDownStack(self):
        self.controller.shiftFilterOnStack(self, -1)

    def hasKeyValueCommandsSet(self):
        for fvp in self.filterValuePairs:
            if len(fvp.getKeyValues()) > 0:
                return True
        return False

    def deselectNonMatchingFilterValuePairs(self, activeValuePair):
        for fvp in self.filterValuePairs:
            if fvp != activeValuePair:
                fvp.deactivateTimeLineSection()

    def getGlobalOptions(self):
        return self.controller.getGlobalOptions()

    def getClipDuration(self):
        return self.controller.getClipDuration()

    def getCurrenttimeForStart(self):
        if self.timelineSupport:
            v = self.controller.getCurrentPlaybackPosition()
            try:
                self._spinTimelineStart.setValue(float(v))
            except Exception:
                pass

    def getCurrenttimeForEnd(self):
        if self.timelineSupport:
            v = self.controller.getCurrentPlaybackPosition()
            try:
                self._spinTimelineEnd.setValue(float(v))
            except Exception:
                pass

    def getTimelineValuesAsSpecifications(self):
        specs = []
        if self.timelineSupport:
            try:
                specs.append({'type': 'timelineStart', 'value': float(self._spinTimelineStart.value())})
            except Exception:
                pass
            try:
                specs.append({'type': 'timelineEnd', 'value': float(self._spinTimelineEnd.value())})
            except Exception:
                pass
        return specs

    def populateRectPropValues(self):
        x1, y1, x2, y2 = self.controller.getRectProperties()
        iw, ih = self.controller.getVideoDimensions()
        x1, x2 = sorted([x1, x2])
        y1, y2 = sorted([y1, y2])
        rectDerivedProps = dict(
            x=x1, y=y1, x1=x1, y1=y1, x2=x2, y2=y2,
            w=x2 - x1, h=y2 - y1, cx=(x1 + x2) / 2, cy=(y1 + y2) / 2,
            xf=round(x1 / iw, 4), yf=round(y1 / ih, 4),
            wf=round((x2 - x1) / iw, 4), hf=round((y2 - y1) / ih, 4),
            cxf=((x1 + x2) / 2) / iw, cyf=((y1 + y2) / 2) / ih,
            px0=x1, py0=y1, px1=x2, py1=y1, px2=x1, py2=y2, px3=x2, py3=y2,
        )
        for k, v in rectDerivedProps.items():
            if k in self.rectProps:
                valVar, t = self.rectProps.get(k)
                if t == 'int':
                    valVar.set(int(v))
                else:
                    valVar.set(v)

    def registerRectProp(self, prop, var, var_type):
        self.rectProps[prop] = (var, var_type)

    def toggleEnabled(self):
        self.enabled = not self.enabled
        if self.enabled:
            self.btnToggleEnabled.setText('Enabled')
            self.btnToggleEnabled.setStyleSheet('')
            self.setStyleSheet('background: #2a2a2a; border: 1px solid #444; margin: 1px;')
        else:
            self.btnToggleEnabled.setText('Disabled')
            self.btnToggleEnabled.setStyleSheet('color: #888;')
            self.setStyleSheet('background: #1e1e1e; border: 1px solid #333; margin: 1px;')
        self.controller.recaculateFilters('toggleEnabled')

    def getFilterExpression(self, preview=False, encodingStage=False):
        nullfilter = 'anull' if self.isAudioFilter else 'null'
        if not self.enabled:
            return nullfilter
        if preview:
            filterExp = self.spec.get('filterPreview', self.spec.get('filter', nullfilter))
        elif self.encodingStageFilter and not encodingStage:
            return nullfilter
        else:
            filterExp = self.spec.get('filter', nullfilter)

        filerExprams = []
        i = self.autoNumber
        values = dict(x.getValuePair() for x in self.filterValuePairs)
        formatDict = {}

        for param in self.spec.get('params', []):
            if param.get('n') is not None:
                if '{' + param['n'] + '}' in filterExp:
                    paramValue = values[param['n']]
                    if param['type'] == 'file':
                        paramValue = escapeForFfmpegFilterArg(paramValue)
                    formatDict.update({'fn': i, param['n']: paramValue})
                elif self.spec.get('appendUnusedParams', True):
                    try:
                        if param['type'] == 'file':
                            filerExprams.append(':{}=\'{}\''.format(param['n'], escapeForFfmpegFilterArg(values[param['n']])))
                        elif param['type'] == 'float':
                            try:
                                floatVal = float(values[param['n']])
                                if param.get('offsetClipStartSeconds', False) and preview:
                                    floatVal = self.controller.normaliseTimestamp(floatVal)
                                filerExprams.append(':{n}={v:01.6f}'.format(n=param['n'], v=floatVal))
                            except Exception:
                                filerExprams.append(':{n}={v:01.6f}'.format(n=param['n'], v=values[param['n']]))
                        elif param['type'] == 'int':
                            try:
                                filerExprams.append(':{}={}'.format(param['n'], int(values[param['n']])))
                            except Exception:
                                filerExprams.append(':{n}={v:01.2f}'.format(n=param['n'], v=values[param['n']]))
                        else:
                            filerExprams.append(':{}={}'.format(param['n'], values[param['n']]))
                    except Exception:
                        filerExprams.append(':{}=\'{}\''.format(param['n'], values[param['n']]))

        if '{fn}' in filterExp:
            formatDict.update({'fn': i})

        if formatDict:
            filterExp = filterExp.format(**formatDict)

        for idx, e in enumerate(filerExprams):
            if idx == 0:
                filterExp += '=' + e[1:]
            else:
                filterExp += e

        if self.timelineSupport and filterExp != nullfilter and not self.encodingStageFilter:
            tsStart = tsEnd = None
            ts_s = self.timelineStart.get()
            ts_e = self.timelineEnd.get()
            if ts_s:
                try:
                    tsStart = float(ts_s)
                    if preview:
                        tsStart = self.controller.normaliseTimestamp(tsStart)
                except Exception:
                    pass
            if ts_e:
                try:
                    tsEnd = float(ts_e)
                    if preview:
                        tsEnd = self.controller.normaliseTimestamp(tsEnd)
                except Exception:
                    pass
            timelineExpression = ''
            if tsStart is not None and tsEnd is not None:
                timelineExpression = ":enable='between(t,{},{})'".format(tsStart, tsEnd)
            elif tsStart is not None:
                timelineExpression = ":enable='gte(t,{})'".format(tsStart)
            elif tsEnd is not None:
                timelineExpression = ":enable='lte(t,{})'".format(tsEnd)
            if '{timelineExpression}' in filterExp:
                filterExp = filterExp.format(timelineExpression=timelineExpression)
            else:
                filterExp += timelineExpression

        return filterExp

    def getTimeLimeCommandValues(self):
        commands = {}
        for fvp in self.filterValuePairs:
            if fvp.commandVarEnabled:
                for varTarget in fvp.commandVarTarget:
                    varTarget = varTarget.format(fn=self.autoNumber)
                    for varProperty in fvp.commandVarProperty:
                        for timeStamp, commandValue, _ in fvp.getKeyValues():
                            commands.setdefault(timeStamp, []).append(
                                (varTarget, varProperty, commandValue, fvp.commandInterpolationMode))
        return commands

    def recaculateFilters(self, caller):
        self.controller.recaculateFilters('recaculateFilters-FilterSpecification' + caller)

    def remove(self):
        for fvp in self.filterValuePairs:
            if fvp.commandVarSelected:
                self.controller.setActiveTimeLineValue(None)
        self.controller.removeFilter(self.filterId)


# ---------------------------------------------------------------------------
# FilterSelectionUi — main Filters tab
# ---------------------------------------------------------------------------

class FilterSelectionUi(QWidget):

    def __init__(self, master=None, enableFaceDetection=False, globalOptions=None, *args, **kwargs):
        super().__init__(master)
        self.controller = None
        self.globalOptions = globalOptions or {}

        # state
        self.subclips = {}
        self.subClipOrder = []
        self.currentSubclipIndex = None
        self.filterClipboard = []
        self.filterSpecifications = []
        self.filterSpecificationCount = 0
        self.activeCommandFilterValuePair = None
        self.timelineModificationLock = Lock()
        self.filterFailed = False
        self.filterFailedResetTimer = None
        self.timelineFileIndex = 0
        self.keyValueSeparation = 3
        self.mouseRectDragging = False
        self.mouseRectMoving = False
        self.mouseRectDragStart = (0, 0)
        self.mouseRectMoveStart = (0, 0)
        self.videoMouseRect = [None, None, None, None]
        self.screenMouseRect = [None, None, None, None]
        self.sketch = []
        self.sketching = False
        self.sketchstart = False
        self.sourceTargetVectorSet = False
        self.sourceRegistrationMark = [0, 0]
        self.vrPanStartSet = False
        self.vrPanLastStart = [0, 0]
        self.AngleDragStartSet = False
        self.sourceAngleDragStart = [0, 0]
        self.tracker = None
        self.trackerOffsetX = 0
        self.trackerOffsetY = 0
        self.referenceFrame = None
        self.timeline_canvas_last_right_click_x = 0
        self.lastVideoRCX = 0
        self.lastVideoRCY = 0

        self._buildUi(enableFaceDetection)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _buildUi(self, enableFaceDetection):
        mainLayout = QVBoxLayout(self)
        mainLayout.setContentsMargins(2, 2, 2, 2)
        mainLayout.setSpacing(2)

        # Top row: filter browser (left) + player (right)
        topRow = QHBoxLayout()
        mainLayout.addLayout(topRow, stretch=1)

        self._buildFilterBrowserPanel(topRow)
        self._buildPlayerPanel(topRow, enableFaceDetection)

        # Bottom: value timeline
        self.canvasValueTimeline = _ValueTimelineWidget(self)
        self.canvasValueTimeline.setFixedHeight(175)
        mainLayout.addWidget(self.canvasValueTimeline)

    def _buildFilterBrowserPanel(self, parentLayout):
        panel = QWidget()
        panel.setMinimumWidth(220)
        panel.setMaximumWidth(320)
        panelLayout = QVBoxLayout(panel)
        panelLayout.setContentsMargins(0, 0, 0, 0)
        panelLayout.setSpacing(2)

        # Subclip nav
        navRow = QHBoxLayout()
        self.buttonVideoPickerPrevious = QPushButton('<')
        self.buttonVideoPickerPrevious.setFixedWidth(24)
        self.buttonVideoPickerPrevious.clicked.connect(self.goToPreviousSubclip)
        navRow.addWidget(self.buttonVideoPickerPrevious)
        self.labelVideoPickerLabel = QLabel('No Subclips Selected 0/0')
        self.labelVideoPickerLabel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        navRow.addWidget(self.labelVideoPickerLabel)
        self.VideoPickerNext = QPushButton('>')
        self.VideoPickerNext.setFixedWidth(24)
        self.VideoPickerNext.clicked.connect(self.goToNextSubclip)
        navRow.addWidget(self.VideoPickerNext)
        panelLayout.addLayout(navRow)

        # Action buttons
        actRow = QHBoxLayout()
        self.buttonFilterActionClear = QPushButton('Clear')
        self.buttonFilterActionClear.setToolTip('Clear all filters applied to the current clip.')
        self.buttonFilterActionClear.clicked.connect(self.clearFilters)
        actRow.addWidget(self.buttonFilterActionClear)
        self.buttonCopyFilters = QPushButton('Copy')
        self.buttonCopyFilters.setToolTip('Copy current filters to the internal clipboard.')
        self.buttonCopyFilters.clicked.connect(self.copyfilters)
        actRow.addWidget(self.buttonCopyFilters)
        self.buttonAppendFilters = QPushButton('Append')
        self.buttonAppendFilters.setToolTip('Append filters from the clipboard after the current filters.')
        self.buttonAppendFilters.clicked.connect(self.appendFilters)
        actRow.addWidget(self.buttonAppendFilters)
        self.buttonPasteFilters = QPushButton('Paste')
        self.buttonPasteFilters.setToolTip('Paste filters from the clipboard, replacing the current filters.')
        self.buttonPasteFilters.clicked.connect(self.pasteFilters)
        actRow.addWidget(self.buttonPasteFilters)
        self.buttonOverrideFilters = QPushButton('Apply to all')
        self.buttonOverrideFilters.setToolTip('Apply current filters to all other clips.')
        self.buttonOverrideFilters.clicked.connect(self.overrideFilters)
        self.buttonOverrideFilters.setContextMenuPolicy(Qt.CustomContextMenu)
        self.buttonOverrideFilters.customContextMenuRequested.connect(
            lambda pos: self._appendMenu.exec(self.buttonOverrideFilters.mapToGlobal(pos)))
        actRow.addWidget(self.buttonOverrideFilters)
        panelLayout.addLayout(actRow)

        self._appendMenu = QMenu(self)
        self._appendMenu.addAction('Append to all', self.appendFiltersToAll)

        # Filter add row
        addRow = QHBoxLayout()
        self.comboboxFilterSelection = QPushButton('Crop ▾')
        self.comboboxFilterSelection.setToolTip('Click to select a filter to add.')
        self.comboboxFilterSelection.clicked.connect(self.showFilterMenu)
        self.comboboxFilterSelection.setContextMenuPolicy(Qt.CustomContextMenu)
        self.comboboxFilterSelection.customContextMenuRequested.connect(
            lambda pos: self.showFilterMenu())
        addRow.addWidget(self.comboboxFilterSelection, stretch=1)
        self._selectedFilterName = 'Crop'
        self.buttonAddFilter = QPushButton('Add Filter')
        self.buttonAddFilter.setToolTip('Add the selected filter to the filter stack.')
        self.buttonAddFilter.clicked.connect(self.addSelectedfilter)
        addRow.addWidget(self.buttonAddFilter)
        panelLayout.addLayout(addRow)

        # Build filter menu
        self.filterMenu = QMenu(self)
        self.submenuMap = {}
        basicFilters = []
        quickFilters = [x.strip().upper() for x in self.globalOptions.get('quickFilters', '').split(',') if x.strip()]
        for fltx in sorted(selectableFilters, key=lambda x: x.get('name', '').upper()):
            categories = fltx.get('category', ['General'])
            if isinstance(categories, str):
                categories = [categories]
            if quickFilters:
                if fltx.get('name', '').upper() in quickFilters:
                    basicFilters.append(fltx)
            elif 'Basic' in categories:
                basicFilters.append(fltx)
            for cat in categories:
                submenu = self.submenuMap.setdefault(cat, QMenu(cat, self.filterMenu))
                n = fltx.get('name', 'UNNAMED')
                d = fltx.get('desc', n + ' filter')
                submenu.addAction('{} - {}'.format(n, d),
                                  lambda checked=False, name=n: self._setSelectedFilter(name))
        if quickFilters:
            basicFilters = sorted(basicFilters, key=lambda x: quickFilters.index(x.get('name', '').upper())
                                  if x.get('name', '').upper() in quickFilters else 999)
        for fltx in basicFilters:
            n = fltx.get('name', 'UNNAMED')
            d = fltx.get('desc', n + ' filter')
            self.filterMenu.addAction('{} - {}'.format(n, d),
                                      lambda checked=False, name=n: self._setSelectedFilter(name))
        self.filterMenu.addSeparator()
        for k, v in sorted(self.submenuMap.items()):
            if k != 'Basic':
                self.filterMenu.addMenu(v)

        # Scrollable filter stack
        self.filterStackScroll = QScrollArea()
        self.filterStackScroll.setWidgetResizable(True)
        self.filterStackScroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.filterContainer = QWidget()
        self.filterContainerLayout = QVBoxLayout(self.filterContainer)
        self.filterContainerLayout.setContentsMargins(0, 0, 0, 0)
        self.filterContainerLayout.setSpacing(2)
        self.filterContainerLayout.addStretch(1)
        self.filterStackScroll.setWidget(self.filterContainer)
        panelLayout.addWidget(self.filterStackScroll, stretch=1)

        parentLayout.addWidget(panel)

    def _buildPlayerPanel(self, parentLayout, enableFaceDetection):
        panel = QWidget()
        panelLayout = QVBoxLayout(panel)
        panelLayout.setContentsMargins(0, 0, 0, 0)
        panelLayout.setSpacing(2)

        # Options bar
        optRow = QHBoxLayout()
        self.autocropButton = QPushButton('Autocrop')
        self.autocropButton.setToolTip('Auto-detect and crop black borders.')
        self.autocropButton.clicked.connect(self.autoCrop)
        optRow.addWidget(self.autocropButton)

        optRow.addWidget(QLabel('Vol'))
        self.scaleVolume = QSlider(Qt.Horizontal)
        self.scaleVolume.setRange(0, 100)
        self.scaleVolume.setFixedWidth(60)
        self.scaleVolume.valueChanged.connect(lambda v: self.setVolume(v))
        optRow.addWidget(self.scaleVolume)

        self.fixSeectionArEnabledVar = False
        self.arFixCheckbox = QCheckBox('Force Aspect')
        self.arFixCheckbox.setToolTip('Clamp selection aspect ratio to match the current video.')
        self.arFixCheckbox.stateChanged.connect(lambda s: setattr(self, 'fixSeectionArEnabledVar', bool(s)))
        optRow.addWidget(self.arFixCheckbox)

        self._arSpin = QDoubleSpinBox()
        self._arSpin.setRange(0.01, 100.0)
        self._arSpin.setValue(1.7)
        self._arSpin.setSingleStep(0.01)
        self._arSpin.setFixedWidth(60)
        self._arSpin.setContextMenuPolicy(Qt.CustomContextMenu)
        self._arSpin.customContextMenuRequested.connect(
            lambda pos: self.showAspectPopup(self._arSpin.mapToGlobal(pos)))
        optRow.addWidget(self._arSpin)

        self.flipARButton = QPushButton('Flip AR')
        self.flipARButton.setToolTip('Flip aspect ratio, effectively rotating selection 90°.')
        self.flipARButton.clicked.connect(self.flipAR)
        optRow.addWidget(self.flipARButton)

        self._fitToScreen = True
        self.fitToScreenCheckbox = QCheckBox('Scale')
        self.fitToScreenCheckbox.setChecked(True)
        self.fitToScreenCheckbox.setToolTip('Scale the video to fill the frame.')
        self.fitToScreenCheckbox.stateChanged.connect(self._onFitToScreenChanged)
        optRow.addWidget(self.fitToScreenCheckbox)

        self.volumeLabel = QLabel('0.0s')
        optRow.addWidget(self.volumeLabel)

        optRow.addStretch(1)

        self.templateButton = QPushButton('Templates')
        self.templateButton.setToolTip('Load a JSON filter template from the filterTemplates folder.')
        self.templateButton.clicked.connect(self.showTemplateMenuPopup)
        optRow.addWidget(self.templateButton)

        self.importButton = QPushButton('Import JSON')
        self.importButton.setToolTip('Load and apply a JSON filter from the system clipboard.')
        self.importButton.clicked.connect(self.importJson)
        optRow.addWidget(self.importButton)

        self.exportButton = QPushButton('Export JSON')
        self.exportButton.setToolTip('Copy current filters to the clipboard in JSON format.')
        self.exportButton.clicked.connect(self.exportJson)
        optRow.addWidget(self.exportButton)

        optRow.addWidget(QLabel('speed'))
        self._speedSpin = QDoubleSpinBox()
        self._speedSpin.setRange(0.01, 100.0)
        self._speedSpin.setValue(2.0)
        self._speedSpin.setSingleStep(0.1)
        self._speedSpin.setFixedWidth(55)
        self._speedSpin.setToolTip('Playback speed (1=normal, 2=double).')
        self._speedSpin.valueChanged.connect(self._onSpeedChanged)
        optRow.addWidget(self._speedSpin)

        panelLayout.addLayout(optRow)

        # Player frame
        self.playerContainerFrame = QWidget()
        self.playerContainerFrame.setCursor(Qt.CrossCursor)
        playerLayout = QVBoxLayout(self.playerContainerFrame)
        playerLayout.setContentsMargins(0, 0, 0, 0)

        self.framePlayerFrame = QWidget()
        self.framePlayerFrame.setMinimumSize(320, 180)
        self.framePlayerFrame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.framePlayerFrame.setStyleSheet('background: #282828;')
        self.framePlayerFrame.setAttribute(Qt.WA_NativeWindow, True)
        self.framePlayerFrame.setMouseTracking(True)
        self.framePlayerFrame.installEventFilter(self)
        playerLayout.addWidget(self.framePlayerFrame, stretch=1)

        panelLayout.addWidget(self.playerContainerFrame, stretch=1)
        parentLayout.addWidget(panel, stretch=1)

        # Context menus
        self._videoCtxMenu = QMenu(self)
        self._videoCtxMenu.addAction('Add Crosshair Registration Mark',
                                     lambda: self.addRegistrationMark('cross'))
        self._videoCtxMenu.addAction('Add Vertical Line Registration Mark',
                                     lambda: self.addRegistrationMark('vline'))
        self._videoCtxMenu.addAction('Add Horizontal Line Registration Mark',
                                     lambda: self.addRegistrationMark('hline'))
        self._videoCtxMenu.addSeparator()
        self._videoCtxMenu.addAction('Set target position for X and Y warping.',
                                     lambda: self.addRegistrationMark('tvec'))
        self._videoCtxMenu.addSeparator()
        self._videoCtxMenu.addAction('Clear Registration Marks',
                                     lambda: self.addRegistrationMark('clear'))
        self._videoCtxMenu.addSeparator()
        self._videoCtxMenu.addAction('Start Sketch', self.startSketch)
        self._videoCtxMenu.addAction('Stop Sketch', self.stopSketch)
        self._videoCtxMenu.addSeparator()
        if enableFaceDetection:
            self._videoCtxMenu.addAction('Add rect from face detector', self.addDetectedFaceRect)
            self._videoCtxMenu.addAction('Centre selected rect from face detector', self.centreDetectedFaceRect)
            self._videoCtxMenu.addAction('Align eyes horizontal from face detector', self.alignDetectedEyes)
        else:
            for lbl in ('Add rect from face detector', 'Align eyes horizontal from face detector'):
                a = self._videoCtxMenu.addAction(lbl, lambda: None)
                a.setEnabled(False)

        self._aspectMenu = QMenu(self)
        for lbl, ar in [('9:16 - Vertical video', 9/16), ('1:1 - Square', 1),
                        ('4:3 - Standard tv', 4/3), ('16:10 - Display or tablet', 16/10),
                        ('16:9 - Standard HDTV', 16/9), ('2:1 - Superscope', 2)]:
            self._aspectMenu.addAction(lbl, lambda checked=False, a=ar: self.setCropAspect(a))
        self._aspectMenu.addSeparator()
        self._aspectMenu.addAction('Match video aspect', lambda: self.setCropAspect(None))

        self.templatePopupMenu = QMenu(self)
        self.templatePopupMenu.addAction('No filter templates found')

    # ------ helpers ------

    def _setSelectedFilter(self, name):
        self._selectedFilterName = name
        self.comboboxFilterSelection.setText(name + ' ▾')

    def _onFitToScreenChanged(self, state):
        self._fitToScreen = bool(state)
        if self.controller:
            self.controller.fitoScreen(self._fitToScreen)

    def _onSpeedChanged(self, value):
        if self.controller:
            try:
                self.controller.setSpeed(float(value))
            except Exception:
                pass

    # ------ eventFilter for playerFrame mouse events ------

    def eventFilter(self, obj, event):
        if obj is self.framePlayerFrame:
            from PySide6.QtCore import QEvent
            if event.type() in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease, QEvent.MouseMove):
                self._handleVideoMouseEvent(event)
                return False
            elif event.type() == QEvent.Wheel:
                self.videoMouseScroll(event)
                return True
        return super().eventFilter(obj, event)

    def _handleVideoMouseEvent(self, event):
        from PySide6.QtCore import QEvent
        shift = bool(event.modifiers() & Qt.ShiftModifier)
        ctrl  = bool(event.modifiers() & Qt.ControlModifier)
        ex, ey = int(event.position().x()), int(event.position().y())

        class _FakeEvent:
            pass
        e = _FakeEvent()
        e.x = ex
        e.y = ey
        e.state = (0x4 if ctrl else 0) | (0x1 if shift else 0)
        if event.type() == QEvent.MouseButtonPress:
            e.type = 'ButtonPress'
            e.num = 1 if event.button() == Qt.LeftButton else 3
        elif event.type() == QEvent.MouseButtonRelease:
            e.type = 'ButtonRelease'
            e.num = 1 if event.button() == Qt.LeftButton else 3
        else:
            e.type = 'Motion'
            e.num = 0

        if e.type == 'ButtonPress' and e.num == 3:
            self.lastVideoRCX = ex
            self.lastVideoRCY = ey
            self.showRegMarkMenu(QCursor.pos())
            return

        self.videomousePress(e)

    # ------ controller wiring ------

    def setController(self, controller):
        self.controller = controller
        # expose canvasValueTimeline reference that FilterSpecification uses
        self.controller.canvasValueTimeline = self.canvasValueTimeline

        if len(self.controller.getTemplateListing()) == 0:
            self.templateButton.setEnabled(False)
        else:
            self.templatePopupMenu.clear()

        for name, value in self.controller.getTemplateListing():
            self.templatePopupMenu.addAction(name, lambda checked=False, val=value: self.importJson(jsonOverride=val))

    def tabSwitched(self, tabName):
        if self.controller is None:
            return
        # tabName is the tab label string or widget ref — check if this is us
        self.recauclateSubclips()
        hasClips = len(self.subclips) > 0
        for w in (self.buttonVideoPickerPrevious, self.VideoPickerNext,
                  self.buttonFilterActionClear, self.buttonOverrideFilters,
                  self.buttonPasteFilters, self.buttonAppendFilters,
                  self.buttonCopyFilters, self.buttonAddFilter,
                  self.comboboxFilterSelection):
            w.setEnabled(hasClips)
        self.refreshtimeLineForNewClip()
        self.controller.play()

    # ------ public API ------

    def getPlayerFrameWid(self):
        self.framePlayerFrame.setAttribute(Qt.WA_NativeWindow, True)
        self.framePlayerFrame.winId()
        QApplication.processEvents()
        return int(self.framePlayerFrame.winId())

    def jumpToFilterByRid(self, rid):
        self.recauclateSubclips()
        if rid in self.subClipOrder:
            ridInd = self.subClipOrder.index(rid)
            self.setSubclipIndex(ridInd)
            self.updateFilterDisplay()
            self.refreshtimeLineForNewClip()
            self.controller.jumpToOwnTab()

    def filterFailure(self):
        self.filterFailed = True
        if self.filterFailedResetTimer is not None:
            self.filterFailedResetTimer.cancel()
        self.filterFailedResetTimer = threading.Timer(2, self.clearFilterFailure)
        self.filterFailedResetTimer.start()
        self.refreshtimeLineForNewClip()

    def clearFilterFailure(self):
        self.filterFailed = False
        self.refreshtimeLineForNewClip()
        self.filterFailedResetTimer = None

    def updateSeekPositionThousands(self, value, seconds):
        if self.controller is None:
            return
        duration = self.controller.getClipDuration()
        if duration:
            tx = int((seconds / duration) * self.canvasValueTimeline.winfo_width())
            self.canvasValueTimeline.setSeekX(tx)

    def updateSeekLabel(self, value):
        self.volumeLabel.setText('{:.2f}s'.format(value))

    def setActiveTimeLineValue(self, activeValuePair):
        for flt in self.filterSpecifications:
            flt.deselectNonMatchingFilterValuePairs(activeValuePair)
        self.activeCommandFilterValuePair = activeValuePair
        self.refreshtimeLineForNewClip()

    def setVolume(self, value):
        if self.controller:
            self.controller.setVolume(value)

    def close_ui(self):
        pass

    def takeScreenshotToFile(self, folder, includes='video'):
        pass

    # ------ filter menu ------

    def showFilterMenu(self):
        self.filterMenu.exec(QCursor.pos())

    def showAspectPopup(self, pos):
        self._aspectMenu.exec(pos if isinstance(pos, QPoint) else QCursor.pos())

    def showAppendMenu(self, pos):
        self._appendMenu.exec(pos if isinstance(pos, QPoint) else QCursor.pos())

    def showRegMarkMenu(self, pos):
        self._videoCtxMenu.exec(pos if isinstance(pos, QPoint) else QCursor.pos())

    def showTemplateMenuPopup(self):
        self.templatePopupMenu.exec(QCursor.pos())

    # ------ keyboard ------

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key_R:
            self.keyboardR(event)
        elif key == Qt.Key_A:
            self.autoShift(event)
        elif key == Qt.Key_S:
            self.keyboardS(event)
        elif key == Qt.Key_C:
            self.keyboardC(event)
        elif key == Qt.Key_I:
            self.keyboardI(event)
        elif key == Qt.Key_D:
            self.keyboardD(event)
        elif key == Qt.Key_N or key == Qt.Key_X:
            self.keyboardN(event)
        elif key == Qt.Key_Space:
            self.keyboardSpace(event)
        elif key == Qt.Key_Up:
            self.keyboardUp(event)
        elif key == Qt.Key_Down:
            self.keyboardDown(event)
        elif key == Qt.Key_Left:
            self.keyboardLeft(event)
        elif key == Qt.Key_Right:
            self.keyboardRight(event)

    def _modState(self, event):
        ctrl  = bool(event.modifiers() & Qt.ControlModifier)
        shift = bool(event.modifiers() & Qt.ShiftModifier)
        state = (0x4 if ctrl else 0) | (0x1 if shift else 0)
        class _E:
            pass
        e = _E()
        e.state = state
        return e

    def keyboardR(self, event):
        e = self._modState(event)
        try:
            pass  # cv2 tracker not enabled
        except Exception:
            return
        self.referenceFrame = self.controller.getCurrentPlaybackPosition()

    def autoShift(self, event):
        e = self._modState(event)
        ctrl  = bool(e.state & 0x4)
        shift = bool(e.state & 0x1)
        if self.tracker is None:
            self.keyboardR(event)
        if self.activeCommandFilterValuePair is None or self.tracker is None:
            return
        self.recaculateFilters('autoshift')
        posSeconds = self.controller.getCurrentPlaybackPosition()
        if self.controller.normaliseTimestamp(posSeconds) > self.controller.playerEnd:
            return
        ss1, divisor = self.controller.getScreenshot(self.controller.normaliseTimestamp(posSeconds), 'null')
        retval, bbox = self.tracker.update(ss1)
        if retval:
            x1, y1, w, h = bbox
            x1 += self.trackerOffsetX
            y1 += self.trackerOffsetY
            if self.activeCommandFilterValuePair.videoSpaceAxis == 'x':
                self.incrementAtCurrentPlaybackPosition(x1 * self.activeCommandFilterValuePair.videoSpaceSign,
                                                       None, useIncrementMultiplier=False, isAsoluteValue=True, applyImmediate=True)
            elif self.activeCommandFilterValuePair.videoSpaceAxis == 'y':
                self.incrementAtCurrentPlaybackPosition(y1 * self.activeCommandFilterValuePair.videoSpaceSign,
                                                       None, useIncrementMultiplier=False, isAsoluteValue=True, applyImmediate=True)
            self.activeCommandFilterValuePair.cycleSelectedPropertySameGroup()
            if shift:
                self.handleSeek(None, 1)
                QTimer.singleShot(1500, lambda: self.autoShift(event))
            if ctrl:
                self.keyboardN(event)

    def getStringValue(self, valueToken):
        return self.controller.getStringValue(valueToken)

    def getViideoAR(self):
        return self.controller.getViideoAR()

    def keyboardS(self, event):
        if self.activeCommandFilterValuePair is not None:
            self.activeCommandFilterValuePair.cycleSelectedPropertySameGroup()

    def keyboardC(self, event):
        if self.activeCommandFilterValuePair is not None:
            self.activeCommandFilterValuePair.cycleSelectedProperty()

    def addRegistrationMark(self, markType):
        if markType == 'tvec':
            self.sourceRegistrationMark = [self.lastVideoRCX, self.lastVideoRCY]
            self.controller.addVideoRegMark(0, 0, 'clear')
            self.sourceTargetVectorSet = True
        if markType == 'clear':
            self.sourceTargetVectorSet = False
        self.controller.addVideoRegMark(self.lastVideoRCX, self.lastVideoRCY, markType)

    def keyboardI(self, event):
        if self.activeCommandFilterValuePair is not None:
            self.activeCommandFilterValuePair.interpolationFactor = (
                self.activeCommandFilterValuePair.interpolationFactor + 1) % 24
            self.recaculateFilters('keyboardI')
            self.refreshtimeLineForNewClip()

    def keyboardD(self, event):
        if self.activeCommandFilterValuePair is None or self.controller is None:
            return
        duration = self.controller.getClipDuration()
        posSeconds = self.controller.getCurrentPlaybackPosition()
        W = self.canvasValueTimeline.winfo_width()
        posX = (posSeconds / duration) * W
        for ts, value, real in self.activeCommandFilterValuePair.getKeyValues():
            tx = int((ts / duration) * W)
            if posX - self.keyValueSeparation < tx < posX + self.keyValueSeparation:
                self.activeCommandFilterValuePair.removeKeyValue(ts)
                self.controller.seekToPercent(tx / W)
                self.refreshtimeLineForNewClip()
                break

    def videoMouseScroll(self, event):
        delta = event.angleDelta().y()
        if delta > 0:
            self.controller.stepRelative(1)
        else:
            self.controller.stepRelative(-1)

    def keyboardUp(self, event):
        ctrl  = bool(event.modifiers() & Qt.ControlModifier)
        shift = bool(event.modifiers() & Qt.ShiftModifier)
        e = self._modState(event)
        self.incrementAtCurrentPlaybackPosition(1, e)
        if ctrl and shift:
            orig = self.activeCommandFilterValuePair
            if orig is not None:
                orig.cycleSelectedPropertySameGroup()
                if orig != self.activeCommandFilterValuePair:
                    self.incrementAtCurrentPlaybackPosition(0, e)
                    self.activeCommandFilterValuePair.cycleSelectedPropertySameGroup()

    def keyboardDown(self, event):
        ctrl  = bool(event.modifiers() & Qt.ControlModifier)
        shift = bool(event.modifiers() & Qt.ShiftModifier)
        e = self._modState(event)
        self.incrementAtCurrentPlaybackPosition(-1, e)
        if ctrl and shift:
            orig = self.activeCommandFilterValuePair
            if orig is not None:
                orig.cycleSelectedPropertySameGroup()
                if orig != self.activeCommandFilterValuePair:
                    self.incrementAtCurrentPlaybackPosition(0, e)
                    self.activeCommandFilterValuePair.cycleSelectedPropertySameGroup()

    def keyboardLeft(self, event):
        ctrl  = bool(event.modifiers() & Qt.ControlModifier)
        shift = bool(event.modifiers() & Qt.ShiftModifier)
        e = self._modState(event)
        if ctrl and shift:
            orig = self.activeCommandFilterValuePair
            self.incrementAtCurrentPlaybackPosition(0, e)
            if orig is not None:
                orig.cycleSelectedPropertySameGroup()
                if orig != self.activeCommandFilterValuePair:
                    self.incrementAtCurrentPlaybackPosition(-1, e)
                    self.activeCommandFilterValuePair.cycleSelectedPropertySameGroup()
        else:
            self.handleSeek(e, -0.5)

    def keyboardRight(self, event):
        ctrl  = bool(event.modifiers() & Qt.ControlModifier)
        shift = bool(event.modifiers() & Qt.ShiftModifier)
        e = self._modState(event)
        if ctrl and shift:
            orig = self.activeCommandFilterValuePair
            self.incrementAtCurrentPlaybackPosition(0, e)
            if orig is not None:
                orig.cycleSelectedPropertySameGroup()
                if orig != self.activeCommandFilterValuePair:
                    self.incrementAtCurrentPlaybackPosition(1, e)
                    self.activeCommandFilterValuePair.cycleSelectedPropertySameGroup()
        else:
            self.handleSeek(e, 0.5)

    def incrementAtCurrentPlaybackPosition(self, increment, e, useIncrementMultiplier=True,
                                            isAsoluteValue=False, applyImmediate=False):
        ctrl = e and bool(e.state & 0x4)
        if self.activeCommandFilterValuePair is None:
            return
        duration = self.controller.getClipDuration()
        posSeconds = self.controller.getCurrentPlaybackPosition()
        W = self.canvasValueTimeline.winfo_width()
        posX = (posSeconds / duration) * W
        existingTS = None
        value = increment
        for ts, v, real in self.activeCommandFilterValuePair.getKeyValues(interpolation=False):
            tx = int((ts / duration) * W)
            if posX - self.keyValueSeparation < tx < posX + self.keyValueSeparation:
                existingTS = ts
                value = v
        if existingTS is None:
            self.activeCommandFilterValuePair.addKeyValue(posSeconds, value=increment,
                                                          useIncrementMultiplier=useIncrementMultiplier,
                                                          isAsoluteValue=isAsoluteValue)
        else:
            self.activeCommandFilterValuePair.incrementKeyValue(
                posSeconds, increment * 10 if ctrl else increment,
                useIncrementMultiplier=useIncrementMultiplier, isAsoluteValue=isAsoluteValue)
        self.refreshtimeLineForNewClip()

    def pause(self):
        self.controller.pause()

    def play(self):
        self.controller.play()

    def keyboardN(self, event):
        if self.controller is None:
            return
        self.controller.pause()
        points = [0, self.controller.getClipDuration()]
        if self.activeCommandFilterValuePair is None:
            self.seekToTimelinePoint(0.1)
            self.refreshtimeLineForNewClip()
            return
        existingPoints = sorted([x[0] for x in self.activeCommandFilterValuePair.getKeyValues() if x[2]])
        if len(existingPoints) < 1 or existingPoints[0] > 0.2:
            self.seekToTimelinePoint(0.1)
            self.refreshtimeLineForNewClip()
        elif existingPoints[-1] < self.controller.getClipDuration() - 0.3:
            self.seekToTimelinePoint(self.controller.getClipDuration() - 0.1)
            self.refreshtimeLineForNewClip()
        else:
            points.extend(existingPoints)
            points = sorted(points, reverse=True)
            mids = sorted([(abs(x - y), (x + y) / 2) for x, y in zip(points[1:], points)], reverse=True)[0][1]
            self.seekToTimelinePoint(mids)
            self.refreshtimeLineForNewClip()

    def handleSeek(self, e, increment):
        ctrl  = e and bool(e.state & 0x4)
        shift = e and bool(e.state & 0x1)
        if shift:
            self.controller.seekRelative(increment)
        elif ctrl and self.activeCommandFilterValuePair is not None:
            posSeconds = self.controller.getCurrentPlaybackPosition()
            points = [0, self.controller.getClipDuration()]
            points.extend([x[0] for x in self.activeCommandFilterValuePair.getKeyValues() if x[2]])
            points = sorted(points)
            mids = [(x + y) / 2 for x, y in zip(points[1:], points)]
            try:
                if increment < 0:
                    self.seekToTimelinePoint([x for x in mids if x < posSeconds - 0.05][-1])
                else:
                    self.seekToTimelinePoint([x for x in mids if x > posSeconds + 0.05][0])
            except Exception:
                pass
        else:
            self.controller.stepRelative(increment)

    def keyboardSpace(self, event):
        self.controller.togglePause()

    def clearKeyValues(self):
        if self.activeCommandFilterValuePair is not None:
            self.activeCommandFilterValuePair.clearKeyValues()
            self.refreshtimeLineForNewClip()

    def clearAllGrpupKeyValues(self):
        if self.activeCommandFilterValuePair is not None:
            self.activeCommandFilterValuePair.clearKeyValues()
            self.activeCommandFilterValuePair.cycleSelectedPropertySameGroup()
            self.activeCommandFilterValuePair.clearKeyValues()
            self.activeCommandFilterValuePair.cycleSelectedPropertySameGroup()
            self.refreshtimeLineForNewClip()

    def addKeyValue(self):
        W = self.canvasValueTimeline.winfo_width()
        secondsClicked = (self.timeline_canvas_last_right_click_x / W) * self.controller.getClipDuration()
        if self.activeCommandFilterValuePair is not None:
            self.activeCommandFilterValuePair.addKeyValue(secondsClicked)
            self.refreshtimeLineForNewClip()
            self.controller.seekToPercent(self.timeline_canvas_last_right_click_x / W)

    def removeKeyValue(self):
        W = self.canvasValueTimeline.winfo_width()
        duration = self.controller.getClipDuration()
        if self.activeCommandFilterValuePair is not None:
            for ts, value, real in self.activeCommandFilterValuePair.getKeyValues():
                if real:
                    tx = int((ts / duration) * W)
                    if self.timeline_canvas_last_right_click_x - self.keyValueSeparation < tx < self.timeline_canvas_last_right_click_x + self.keyValueSeparation:
                        self.activeCommandFilterValuePair.removeKeyValue(ts)
                        self.controller.seekToPercent(tx / W)
                        self.refreshtimeLineForNewClip()
                        break

    def refreshtimeLineForNewClip(self):
        self.canvasValueTimeline.scheduleRepaint()

    def reconfigure(self, e=None):
        self.refreshtimeLineForNewClip()

    def getGlobalOptions(self):
        return self.controller.getGlobalOptions()

    def getCurrentPlaybackPosition(self):
        return self.controller.getCurrentPlaybackPosition()

    def getClipDuration(self):
        return self.controller.getClipDuration()

    def autoCropCallback(self, x, y, w, h):
        self.filterSpecificationCount += 1
        newFilter = None
        for spec in selectableFilters:
            if spec['name'] == 'Crop':
                newFilter = FilterSpecification(self.filterContainer, self, spec, self.filterSpecificationCount)
                self.filterSpecifications.append(newFilter)
                self.filterContainerLayout.insertWidget(
                    self.filterContainerLayout.count() - 1, newFilter)
                break
        if newFilter is not None:
            newFilter.rectProps.get('x', [None])[0] and newFilter.rectProps['x'][0].set(int(x))
            newFilter.rectProps.get('y', [None])[0] and newFilter.rectProps['y'][0].set(int(y))
            newFilter.rectProps.get('w', [None])[0] and newFilter.rectProps['w'][0].set(int(w))
            newFilter.rectProps.get('h', [None])[0] and newFilter.rectProps['h'][0].set(int(h))
        self.recaculateFilters('autoCropCallback')

    def importJson(self, jsonOverride=None):
        if jsonOverride is None:
            clipboard = QApplication.clipboard()
            try:
                s = json.loads(clipboard.text())
            except Exception as e:
                logging.error('importJson clipboard parse error', exc_info=e)
                return
        else:
            s = jsonOverride
        if self.currentSubclipIndex is not None:
            rid = self.subClipOrder[self.currentSubclipIndex]
            self.subclips[rid]['filters'] = copy.deepcopy(s)
            for f in self.filterSpecifications:
                f.setParent(None)
                f.deleteLater()
            self.filterSpecifications = []
            for spec in self.subclips[rid].setdefault('filters', []):
                self.filterSpecificationCount += 1
                fs = FilterSpecification(self.filterContainer, self, spec, self.filterSpecificationCount)
                self.filterSpecifications.append(fs)
                self.filterContainerLayout.insertWidget(self.filterContainerLayout.count() - 1, fs)
            self.recaculateFilters('importJson')

    def exportJson(self):
        clipboard = QApplication.clipboard()
        clipboard.setText(json.dumps(self.convertFilterstoSpecDefaults()))

    def flipAR(self):
        ar = self._arSpin.value()
        self._arSpin.setValue(1.0 / ar if ar else 1.0)

    def autoCrop(self):
        if self.currentSubclipIndex is None:
            return
        rid = self.subClipOrder[self.currentSubclipIndex]
        subclip = self.subclips[rid]
        mid = (subclip['start'] + subclip['end']) / 2
        self.controller.requestAutocrop(rid, mid, subclip['filename'], self.autoCropCallback)

    def speedChange(self, *args):
        if self.controller:
            try:
                self.controller.setSpeed(self._speedSpin.value())
            except Exception:
                pass

    def changeFitToScreen(self, *args):
        if self.controller:
            self.controller.fitoScreen(self._fitToScreen)

    def getVideoDimensions(self):
        return self.controller.getVideoDimensions()

    def getRectProperties(self):
        return self.videoMouseRect

    def setCropAspect(self, ar):
        if ar is None:
            ar = self.controller.getViideoAR()
        self.arFixCheckbox.setChecked(True)
        self.fixSeectionArEnabledVar = True
        self._arSpin.setValue(ar)

    def applyScreenSpaceAR(self, shift=False):
        forceAR = None
        if self.fixSeectionArEnabledVar:
            try:
                forceAR = self._arSpin.value()
            except Exception:
                pass
        if forceAR is not None:
            if shift:
                neww = abs(self.screenMouseRect[0] - self.screenMouseRect[2])
                newh = neww / forceAR
                midx = (self.screenMouseRect[0] + self.screenMouseRect[2]) / 2
                midy = (self.screenMouseRect[1] + self.screenMouseRect[3]) / 2
                self.screenMouseRect[0] = midx - (neww / 2)
                self.screenMouseRect[1] = midy - (newh / 2)
                self.screenMouseRect[2] = midx + (neww / 2)
                self.screenMouseRect[3] = midy + (newh / 2)
            else:
                if self.screenMouseRect[3] > self.screenMouseRect[1]:
                    self.screenMouseRect[3] = self.screenMouseRect[1] + abs(self.screenMouseRect[0] - self.screenMouseRect[2]) / forceAR
                else:
                    self.screenMouseRect[3] = self.screenMouseRect[1] - abs(self.screenMouseRect[0] - self.screenMouseRect[2]) / forceAR
        else:
            try:
                ratio = abs(self.screenMouseRect[0] - self.screenMouseRect[2]) / abs(self.screenMouseRect[1] - self.screenMouseRect[3])
                self._arSpin.setValue(round(ratio, 4))
            except Exception:
                pass

    def startSketch(self):
        self.sketch = []
        self.sketching = True
        self.sketchstart = False
        self.controller.updateSketch(self.sketch)

    def stopSketch(self):
        self.sketching = False
        self.sketchstart = False
        self.controller.updateSketch(self.sketch)

    def videomousePress(self, e):
        shift = bool(e.state & 0x1)
        ctrl  = bool(e.state & 0x4)

        if self.sketching:
            sx, sy = self.controller.screenSpaceToVideoSpace(e.x, e.y)
            if e.type == 'ButtonPress':
                self.sketch.append([sx, sy, sx, sy, 1])
                self.sketchstart = True
                self.controller.updateSketch(self.sketch)
            elif e.type == 'Motion' and self.sketchstart:
                self.sketch[-1][2] = sx
                self.sketch[-1][3] = sy
                self.controller.updateSketch(self.sketch)
            elif e.type == 'ButtonRelease':
                self.sketch[-1][2] = sx
                self.sketch[-1][3] = sy
                self.sketchstart = False
                self.controller.updateSketch(self.sketch)
            return

        if self.sourceTargetVectorSet and e.type == 'ButtonPress':
            x1, y1 = self.controller.screenSpaceToVideoSpace(
                self.sourceRegistrationMark[0], self.sourceRegistrationMark[1])
            x2, y2 = self.controller.screenSpaceToVideoSpace(e.x, e.y)
            self.applyVectorOffset(x1, y1, x2, y2, isAsoluteValue=False)
            if ctrl and shift:
                self.controller.stepRelative(1)
            elif ctrl:
                self.keyboardN(e)
        elif ctrl:
            if (self.activeCommandFilterValuePair is not None and
                    self.activeCommandFilterValuePair.videoSpaceAxis in ('yaw', 'pitch') and
                    e.type == 'ButtonPress'):
                self.vrPanStartSet = True
            elif (self.activeCommandFilterValuePair is not None and
                  self.activeCommandFilterValuePair.videoSpaceAxis == 'deg' and
                  e.type == 'ButtonPress'):
                self.AngleDragStartSet = True
                self.sourceAngleDragStart = [e.x, e.y]
            elif e.type in ('ButtonPress', 'Motion'):
                x2, y2 = self.controller.screenSpaceToVideoSpace(e.x, e.y)
                self.applyVectorOffset(x2, y2, 0, 0, isAsoluteValue=True)
        else:
            videoOriginX, videoOriginY, videoMaxX, videoMaxY = self.controller.getvideoOSDExtents()
            if e.type == 'ButtonPress':
                if (self.screenMouseRect[0] is not None and
                        abs(((self.screenMouseRect[0] + self.screenMouseRect[2]) / 2) - e.x) < 30 and
                        abs(((self.screenMouseRect[1] + self.screenMouseRect[3]) / 2) - e.y) < 30):
                    self.mouseRectMoving = True
                    self.mouseRectMoveStart = (e.x, e.y)
                else:
                    self.mouseRectDragging = True
                    if self.mouseRectDragStart == (0, 0):
                        self.mouseRectDragStart = (e.x, e.y)
                    self.screenMouseRect[0] = e.x
                    self.screenMouseRect[1] = e.y
            elif e.type in ('Motion', 'ButtonRelease') and (self.mouseRectDragging or self.mouseRectMoving):
                if self.mouseRectMoving:
                    haw = abs(self.screenMouseRect[0] - self.screenMouseRect[2]) // 2
                    hah = abs(self.screenMouseRect[1] - self.screenMouseRect[3]) // 2
                    haw = min(haw, (videoMaxX - videoOriginX) // 2)
                    hah = min(hah, (videoMaxY - videoOriginY) // 2)
                    mcx, mcy = e.x, e.y
                    mcx = max(videoOriginX + haw, min(videoMaxX - haw, mcx))
                    mcy = max(videoOriginY + hah, min(videoMaxY - hah, mcy))
                    self.screenMouseRect[0] = mcx - haw
                    self.screenMouseRect[1] = mcy - hah
                    self.screenMouseRect[2] = mcx + haw
                    self.screenMouseRect[3] = mcy + hah
                else:
                    if shift:
                        dx = abs(self.mouseRectDragStart[0] - e.x)
                        dy = abs(self.mouseRectDragStart[1] - e.y)
                        self.screenMouseRect[0] = self.mouseRectDragStart[0] + dx
                        self.screenMouseRect[1] = self.mouseRectDragStart[1] + dy
                        self.screenMouseRect[2] = self.mouseRectDragStart[0] - dx
                        self.screenMouseRect[3] = self.mouseRectDragStart[1] - dy
                    else:
                        if self.mouseRectDragStart[0] > 0 and self.mouseRectDragStart[1] > 0:
                            self.screenMouseRect[0] = self.mouseRectDragStart[0]
                            self.screenMouseRect[1] = self.mouseRectDragStart[1]
                        self.screenMouseRect[2] = e.x
                        self.screenMouseRect[3] = e.y
                self.applyScreenSpaceAR(shift)
                vx1, vy1 = self.controller.screenSpaceToVideoSpace(self.screenMouseRect[0], self.screenMouseRect[1])
                vx2, vy2 = self.controller.screenSpaceToVideoSpace(self.screenMouseRect[2], self.screenMouseRect[3])
                self.controller.setVideoRect(self.screenMouseRect[0], self.screenMouseRect[1],
                                             self.screenMouseRect[2], self.screenMouseRect[3],
                                             desc='{}x{}'.format(int(abs(vx1-vx2)), int(abs(vy1-vy2))))
            if e.type == 'ButtonRelease':
                self.mouseRectDragging = False
                self.mouseRectMoving = False
                self.mouseRectDragStart = (0, 0)
                vx1, vy1 = self.controller.screenSpaceToVideoSpace(self.screenMouseRect[0], self.screenMouseRect[1])
                vx2, vy2 = self.controller.screenSpaceToVideoSpace(self.screenMouseRect[2], self.screenMouseRect[3])
                self.videoMouseRect = [vx1, vy1, vx2, vy2]
                self.controller.setVideoRect(self.screenMouseRect[0], self.screenMouseRect[1],
                                             self.screenMouseRect[2], self.screenMouseRect[3],
                                             desc='{}x{}'.format(int(abs(vx1-vx2)), int(abs(vy1-vy2))))
                if (self.screenMouseRect[0] is not None and not self.mouseRectDragging and
                        self.screenMouseRect[0] == self.screenMouseRect[2] and
                        self.screenMouseRect[1] == self.screenMouseRect[3]):
                    self.screenMouseRect = [None, None, None, None]
                    self.mouseRectDragging = False
                    self.controller.clearVideoRect()

    def applyVectorOffset(self, x1, y1, x2, y2, isAsoluteValue=False, isAngle=False):
        if self.activeCommandFilterValuePair is None:
            return
        horizD = x2 - x1
        vertD  = y2 - y1
        if isAngle:
            if self.activeCommandFilterValuePair.videoSpaceAxis == 'deg':
                angle = -atan2(y2 - y1, x2 - x1)
                snapPositions = 4
                minSnap = float('inf')
                snapOffset = 0
                for i in range(snapPositions):
                    snapAngle = i * ((2 * pi) / snapPositions)
                    if abs(angle - snapAngle) < minSnap:
                        minSnap = abs(angle - snapAngle)
                        snapOffset = snapAngle
                angle -= snapOffset
                self.incrementAtCurrentPlaybackPosition(
                    angle * self.activeCommandFilterValuePair.videoSpaceSign,
                    None, useIncrementMultiplier=False, isAsoluteValue=isAsoluteValue, applyImmediate=True)
        else:
            for usealternate in [False, True]:
                if usealternate:
                    original = self.activeCommandFilterValuePair
                    self.activeCommandFilterValuePair.cycleSelectedPropertySameGroup()
                    if original == self.activeCommandFilterValuePair:
                        return
                if isAsoluteValue:
                    if self.activeCommandFilterValuePair.videoSpaceAxis == 'x':
                        self.incrementAtCurrentPlaybackPosition(
                            x1 * self.activeCommandFilterValuePair.videoSpaceSign,
                            None, useIncrementMultiplier=False, isAsoluteValue=True, applyImmediate=True)
                    elif self.activeCommandFilterValuePair.videoSpaceAxis == 'y':
                        self.incrementAtCurrentPlaybackPosition(
                            y1 * self.activeCommandFilterValuePair.videoSpaceSign,
                            None, useIncrementMultiplier=False, isAsoluteValue=True, applyImmediate=True)
                else:
                    if self.activeCommandFilterValuePair.videoSpaceAxis == 'x':
                        self.incrementAtCurrentPlaybackPosition(
                            horizD * self.activeCommandFilterValuePair.videoSpaceSign,
                            None, useIncrementMultiplier=False, isAsoluteValue=False, applyImmediate=True)
                    elif self.activeCommandFilterValuePair.videoSpaceAxis == 'y':
                        self.incrementAtCurrentPlaybackPosition(
                            vertD * self.activeCommandFilterValuePair.videoSpaceSign,
                            None, useIncrementMultiplier=False, isAsoluteValue=False, applyImmediate=True)
                    elif self.activeCommandFilterValuePair.videoSpaceAxis == 'pitch' and abs(vertD) > 0.1:
                        self.incrementAtCurrentPlaybackPosition(
                            vertD * self.activeCommandFilterValuePair.videoSpaceSign,
                            None, useIncrementMultiplier=False, isAsoluteValue=False, applyImmediate=True)
                    elif self.activeCommandFilterValuePair.videoSpaceAxis == 'yaw' and abs(horizD) > 0.1:
                        self.incrementAtCurrentPlaybackPosition(
                            horizD * self.activeCommandFilterValuePair.videoSpaceSign,
                            None, useIncrementMultiplier=False, isAsoluteValue=False, applyImmediate=True)
                if usealternate:
                    self.activeCommandFilterValuePair.cycleSelectedPropertySameGroup()

    # ---- face detection callbacks ----

    def addDetectedFaceRectCallback(self, sourceFile, timestamp, faces):
        self.controller.addVideoRegMark(0, 0, 'clear')
        for face in faces:
            fx, fy, fs = face['face']['x'], face['face']['y'], face['face']['size']
            fvx, fvy   = self.controller.videoSpaceToScreenSpace(fx, fy)
            fvx2, fvy2 = self.controller.videoSpaceToScreenSpace(fx + fs, fy + fs)
            self.videoMouseRect  = [fx, fy, fx + fs, fy + fs]
            self.screenMouseRect = [fvx, fvy, fvx2, fvy2]
            self.controller.setVideoRect(fvx, fvy, fvx2, fvy2)
            break

    def setCenteredFaceRectCallback(self, sourceFile, timestamp, faces):
        self.controller.addVideoRegMark(0, 0, 'clear')
        for face in faces:
            fx, fy, fs = face['face']['x'], face['face']['y'], face['face']['size']
            fvx, fvy   = self.controller.videoSpaceToScreenSpace(fx, fy)
            fvx2, fvy2 = self.controller.videoSpaceToScreenSpace(fx + fs, fy + fs)
            self.videoMouseRect  = [fx, fy, fx + fs, fy + fs]
            self.screenMouseRect = [fvx, fvy, fvx2, fvy2]
            self.controller.setVideoRect(fvx, fvy, fvx2, fvy2)
            for eye in face.get('eyes', []):
                x, y = self.controller.videoSpaceToScreenSpace(eye['x'], eye['y'])
                self.controller.addVideoRegMark(x, y)
            break

    def alignDetectedEyesFaceRectCallback(self, sourceFile, timestamp, faces):
        eyepoints = []
        for face in faces:
            for eye in face.get('eyes', []):
                x, y = self.controller.videoSpaceToScreenSpace(eye['x'], eye['y'])
                eyepoints.extend([x, y])
            break
        if len(eyepoints) == 4:
            self.applyVectorOffset(eyepoints[0], eyepoints[1], eyepoints[2], eyepoints[3],
                                   isAsoluteValue=True, isAngle=True)

    def addDetectedFaceRect(self):
        self.controller.getFaceBoundingRect(self.addDetectedFaceRectCallback)

    def centreDetectedFaceRect(self):
        self.controller.getFaceBoundingRect(self.setCenteredFaceRectCallback)

    def alignDetectedEyes(self):
        self.controller.getFaceBoundingRect(self.alignDetectedEyesFaceRectCallback)

    # ---- filter stack management ----

    def addSelectedfilter(self):
        self.filterSpecificationCount += 1
        newFilter = None
        for spec in selectableFilters:
            if spec['name'] == self._selectedFilterName:
                newFilter = FilterSpecification(self.filterContainer, self, spec, self.filterSpecificationCount)
                self.filterSpecifications.append(newFilter)
                self.filterContainerLayout.insertWidget(self.filterContainerLayout.count() - 1, newFilter)
                break
        if newFilter is not None and self.videoMouseRect[2] is not None:
            newFilter.populateRectPropValues()
        self.recaculateFilters('addSelectedfilter')

    def removeFilter(self, filterId):
        for flt in self.filterSpecifications:
            if flt.filterId == filterId:
                flt.setParent(None)
                flt.deleteLater()
        self.filterSpecifications = [x for x in self.filterSpecifications if x.filterId != filterId]
        self.recaculateFilters('removeFilter')

    def clearFilters(self):
        for flt in self.filterSpecifications:
            flt.setParent(None)
            flt.deleteLater()
        self.filterSpecifications = []
        self.recaculateFilters('clearFilters')

    def updateSeekLabel(self, value):
        self.volumeLabel.setText('{:.2f}s'.format(value))

    def seekToTimelinePoint(self, ts):
        return self.controller.seekToTimelinePoint(ts)

    def normaliseTimestamp(self, ts):
        return self.controller.normaliseTimestamp(ts)

    def recaculateFilters(self, caller):
        filteraudioexpPreview = []
        filteraudioexpReal = []
        filterexpPreview = []
        filterExpReal = []
        filterExpEncodingStage = []
        commandSet = {}

        for flt in self.filterSpecifications:
            commandSet.update(flt.getTimeLimeCommandValues())
            if flt.isAudioFilter:
                filteraudioexpPreview.append(flt.getFilterExpression(preview=True))
                filteraudioexpReal.append(flt.getFilterExpression(preview=False))
            else:
                filterexpPreview.append(flt.getFilterExpression(preview=True))
                filterExpReal.append(flt.getFilterExpression(preview=False))
                if flt.encodingStageFilter:
                    filterExpEncodingStage.append(flt.getFilterExpression(preview=False, encodingStage=True))

        sep = '\n'
        commandStr_preview = ''
        commandStr_real = ''
        lastCommandValues = {}
        useFile = True

        for k, v in sorted(commandSet.items()):
            for cmdTarget, cmdProperty, cmdValue, interpolationMode in v:
                lastTime, lastValue, _ = lastCommandValues.get((cmdTarget, cmdProperty), (0, cmdValue, interpolationMode))
                norm_k = self.controller.normaliseTimestamp(k)
                norm_lt = self.controller.normaliseTimestamp(lastTime)
                if interpolationMode == 'lerp':
                    commandStr_preview += "{l:.4f}-{k:.4f} [expr] {t} {p} 'lerp({lv:.4f},{cv:.4f},TI)';{s}".format(l=norm_lt, k=norm_k, t=cmdTarget, p=cmdProperty, lv=lastValue, cv=cmdValue, s=sep)
                    commandStr_real    += "{l:.4f}-{k:.4f} [expr] {t} {p} 'lerp({lv:.4f},{cv:.4f},TI)';{s}".format(l=lastTime, k=k, t=cmdTarget, p=cmdProperty, lv=lastValue, cv=cmdValue, s=sep)
                elif interpolationMode == 'lerp-sigmoid':
                    commandStr_preview += "{l:.4f}-{k:.4f} [expr] {t} {p} 'lerp({lv:.4f},{cv:.4f},sin(TI*(PI/2)))';{s}".format(l=norm_lt, k=norm_k, t=cmdTarget, p=cmdProperty, lv=lastValue, cv=cmdValue, s=sep)
                    commandStr_real    += "{l:.4f}-{k:.4f} [expr] {t} {p} 'lerp({lv:.4f},{cv:.4f},sin(TI*(PI/2)))';{s}".format(l=lastTime, k=k, t=cmdTarget, p=cmdProperty, lv=lastValue, cv=cmdValue, s=sep)
                elif interpolationMode == 'lerp-smooth':
                    commandStr_preview += "{l:.4f}-{k:.4f} [expr] {t} {p} 'lerp({lv:.4f},{cv:.4f},(TI*TI*(3-2*TI)))';{s}".format(l=norm_lt, k=norm_k, t=cmdTarget, p=cmdProperty, lv=lastValue, cv=cmdValue, s=sep)
                    commandStr_real    += "{l:.4f}-{k:.4f} [expr] {t} {p} 'lerp({lv:.4f},{cv:.4f},(TI*TI*(3-2*TI)))';{s}".format(l=lastTime, k=k, t=cmdTarget, p=cmdProperty, lv=lastValue, cv=cmdValue, s=sep)
                elif interpolationMode == 'neighbour':
                    commandStr_preview += "{k:.4f} [enter] {t} {p} {cv:.4f};{s}".format(k=norm_k, t=cmdTarget, p=cmdProperty, cv=cmdValue, s=sep)
                    commandStr_real    += "{k:.4f} [enter] {t} {p} {cv:.4f};{s}".format(k=k, t=cmdTarget, p=cmdProperty, cv=cmdValue, s=sep)
                else:  # lerp-smooth-inv, lerp-smooth-2nd etc.
                    commandStr_preview += "{l:.4f}-{k:.4f} [expr] {t} {p} '{cv:.4f}';{s}".format(l=norm_lt, k=norm_k, t=cmdTarget, p=cmdProperty, cv=cmdValue, s=sep)
                    commandStr_real    += "{l:.4f}-{k:.4f} [expr] {t} {p} '{cv:.4f}';{s}".format(l=lastTime, k=k, t=cmdTarget, p=cmdProperty, cv=cmdValue, s=sep)
                lastCommandValues[(cmdTarget, cmdProperty)] = (k, cmdValue, interpolationMode)

        filterAudioExpStrPreview = ','.join(filteraudioexpPreview)
        filterAudioExpStrReal    = ','.join(filteraudioexpReal)
        filterExpStrPreview      = ','.join(filterexpPreview)
        filterExpStrReal         = ','.join(filterExpReal)
        filterExpEncodingStageStr= ','.join(filterExpEncodingStage)

        currentClip = 0
        try:
            if self.currentSubclipIndex is not None:
                currentClip = self.subClipOrder[self.currentSubclipIndex]
        except Exception as e:
            print(e)
            return

        preLockClip = self.getCurrentClip()
        with self.timelineModificationLock:
            if commandSet:
                if useFile:
                    self.timelineFileIndex = (self.timelineFileIndex + 1) % 5
                    tmpPath = self.controller.gettempVideoFilePath()
                    fnPrev = os.path.join(tmpPath, 'commands_{}_{}_{}_preview.txt'.format(currentClip, self.timelineFileIndex, id(self)))
                    with open(fnPrev, 'w') as f:
                        f.write(commandStr_preview)
                    fnPrevClean = escapeForFfmpegFilterArg(cleanFilenameForFfmpeg(os.path.abspath(fnPrev)).replace('\\', '/'))
                    fnReal = os.path.join(tmpPath, 'commands_{}_{}_real.txt'.format(currentClip, id(self)))
                    with open(fnReal, 'w') as f:
                        f.write(commandStr_real)
                    fnRealClean = escapeForFfmpegFilterArg(cleanFilenameForFfmpeg(os.path.abspath(fnReal)).replace('\\', '/'))
                    sndCmdFilter_preview = "sendcmd=f='{}',".format(fnPrevClean)
                    sndCmdFilter_real    = "sendcmd=f='{}',".format(fnRealClean)
                else:
                    sndCmdFilter_preview = "sendcmd=c='{}',".format(escapeForFfmpegFilterArg(commandStr_preview))
                    sndCmdFilter_real    = "sendcmd=c='{}',".format(escapeForFfmpegFilterArg(commandStr_real))
                filterExpStrPreview = sndCmdFilter_preview + filterExpStrPreview
                filterExpStrReal    = sndCmdFilter_real    + filterExpStrReal

            postLockClip = self.getCurrentClip()
            if preLockClip != postLockClip or (not filterexpPreview and not filterAudioExpStrPreview):
                self.controller.clearFilter()
            else:
                self.controller.setFilter(filterExpStrPreview, filterAudioExpStrPreview)
            self.filterFailed = False

        if self.currentSubclipIndex is not None and preLockClip == postLockClip:
            clip = self.getCurrentClip()
            if clip is not None:
                clip['filters']         = self.convertFilterstoSpecDefaults()
                clip['filterexp']        = filterExpStrReal
                clip['filterexpaudio']   = filterAudioExpStrReal
                clip['filterexpEncStage']= filterExpEncodingStageStr

    def convertFilterstoSpecDefaults(self):
        filterstack = []
        for ifilter in self.filterSpecifications:
            baseSpec = None
            for spec in selectableFilters:
                if spec['name'] == ifilter.spec['name']:
                    baseSpec = copy.deepcopy(spec)
                    baseSpec['enabled'] = ifilter.enabled
                    break
            if baseSpec is None:
                continue
            for n, v in [x.getValuePair(forFilter=False) for x in ifilter.filterValuePairs]:
                for param in baseSpec['params']:
                    if param['n'] == n:
                        param['d'] = v
                        break
            for valPair in ifilter.filterValuePairs:
                for param in baseSpec['params']:
                    if param['n'] == valPair.n and valPair.commandVarAvaliable:
                        param['commandVarEnabled']   = valPair.commandVarEnabled
                        param['commandVarSelected']  = valPair.commandVarSelected
                        param['keyValues']           = valPair.keyValues
                        param['interpolationFactor'] = valPair.interpolationFactor
                        param['interpMode']          = valPair.commandInterpolationMode
                        param['restrictedInterpModes'] = valPair.interpolationModes
            baseSpec.setdefault('params', []).extend(ifilter.getTimelineValuesAsSpecifications())
            filterstack.append(baseSpec)
        return filterstack

    def recauclateSubclips(self):
        unusedRids = set(self.subclips.keys())
        for filename, rid, s, e in self.controller.getAllSubclips():
            if rid in self.subclips:
                unusedRids.discard(rid)
                if self.subclips[rid]['start'] != s or self.subclips[rid]['end'] != e:
                    self.subclips[rid]['start'] = s
                    self.subclips[rid]['end'] = e
            else:
                self.subclips[rid] = dict(start=s, end=e, filename=filename, filters=[])

        tempSelectedRid = None
        if self.currentSubclipIndex is not None:
            try:
                tempSelectedRid = self.subClipOrder[self.currentSubclipIndex]
            except Exception:
                pass

        for k in unusedRids:
            del self.subclips[k]

        self.subClipOrder = [k for k, v in sorted(self.subclips.items(),
                              key=lambda x: (x[1]['filename'], x[1]['start']))]

        if tempSelectedRid in self.subClipOrder:
            self.setSubclipIndex(self.subClipOrder.index(tempSelectedRid))
            self.updateFilterDisplay()
        elif self.subClipOrder:
            self.setSubclipIndex(0)
            self.updateFilterDisplay()
        else:
            self.setSubclipIndex(None)
            self.controller.stop()
            self.labelVideoPickerLabel.setText('No Subclips Selected 0/0')
        self.updateFilterDisplay()

    def getCurrentClip(self):
        try:
            if self.subclips and self.currentSubclipIndex is not None:
                return self.subclips[self.subClipOrder[self.currentSubclipIndex]]
        except Exception as e:
            logging.error('getCurrentClip Exception', exc_info=e)
        return None

    def jumpToFilterByRid(self, rid):
        self.recauclateSubclips()
        if rid in self.subClipOrder:
            self.setSubclipIndex(self.subClipOrder.index(rid))
            self.updateFilterDisplay()
            self.refreshtimeLineForNewClip()
            self.controller.jumpToOwnTab()

    def goToNextSubclip(self):
        if self.currentSubclipIndex is not None:
            self.activeCommandFilterValuePair = None
            self.setSubclipIndex((self.currentSubclipIndex + 1) % len(self.subClipOrder))
            self.updateFilterDisplay()
            self.refreshtimeLineForNewClip()

    def goToPreviousSubclip(self):
        if self.currentSubclipIndex is not None:
            self.activeCommandFilterValuePair = None
            self.setSubclipIndex((self.currentSubclipIndex - 1) % len(self.subClipOrder))
            self.updateFilterDisplay()
            self.refreshtimeLineForNewClip()

    def copyfilters(self):
        if self.currentSubclipIndex is not None:
            self.filterClipboard = self.convertFilterstoSpecDefaults()

    def appendFiltersToAll(self):
        if self.currentSubclipIndex is None:
            return
        reply = QMessageBox.question(self, 'Append filters to all clips?',
                                     'This will add the filters on this clip to the end of all other clips, are you sure?')
        if reply == QMessageBox.Yes:
            tempClipboard = self.convertFilterstoSpecDefaults()
            for rid in self.subClipOrder:
                self.subclips[rid].setdefault('filters', []).extend(copy.deepcopy(tempClipboard))
            self.recaculateFilters('appendFiltersToAll')

    def overrideFilters(self):
        if self.currentSubclipIndex is None:
            return
        reply = QMessageBox.question(self, 'Apply filters to all clips?',
                                     'This will clear the filters on all other clips and override them with these filters, are you sure?')
        if reply == QMessageBox.Yes:
            tempClipboard = self.convertFilterstoSpecDefaults()
            for rid in self.subClipOrder:
                self.subclips[rid]['filters'] = copy.deepcopy(tempClipboard)
            self.recaculateFilters('overrideFilters')
            currentClip = self.getCurrentClip()
            if currentClip is not None:
                filters           = copy.deepcopy(currentClip.get('filters', []))
                filterexp         = copy.deepcopy(currentClip.get('filterexp', ''))
                filterexpEncStage = copy.deepcopy(currentClip.get('filterexpEncStage', ''))
                for clip in self.subclips.values():
                    clip['filters']           = filters
                    clip['filterexp']         = filterexp
                    clip['filterexpEncStage'] = filterexpEncStage

    def shiftFilterOnStack(self, flt, direction):
        if flt not in self.filterSpecifications:
            return
        idx = self.filterSpecifications.index(flt)
        newIdx = idx + direction
        if 0 <= newIdx < len(self.filterSpecifications):
            self.filterSpecifications.insert(newIdx, self.filterSpecifications.pop(idx))
            # Reorder in layout
            for w in self.filterSpecifications:
                self.filterContainerLayout.removeWidget(w)
            for w in self.filterSpecifications:
                self.filterContainerLayout.insertWidget(self.filterContainerLayout.count() - 1, w)
            self.recaculateFilters('shiftFilterOnStack')

    def appendFilters(self):
        if self.currentSubclipIndex is None:
            return
        rid = self.subClipOrder[self.currentSubclipIndex]
        self.subclips[rid]['filters'] += copy.deepcopy(self.filterClipboard)
        self._reloadFiltersFromSpec(rid)

    def pasteFilters(self):
        if self.currentSubclipIndex is None:
            return
        rid = self.subClipOrder[self.currentSubclipIndex]
        self.subclips[rid]['filters'] = copy.deepcopy(self.filterClipboard)
        self._reloadFiltersFromSpec(rid)

    def _reloadFiltersFromSpec(self, rid):
        for f in self.filterSpecifications:
            f.setParent(None)
            f.deleteLater()
        self.filterSpecifications = []
        for spec in self.subclips[rid].setdefault('filters', []):
            self.filterSpecificationCount += 1
            fs = FilterSpecification(self.filterContainer, self, spec, self.filterSpecificationCount)
            self.filterSpecifications.append(fs)
            self.filterContainerLayout.insertWidget(self.filterContainerLayout.count() - 1, fs)
        self.recaculateFilters('reloadFilters')

    def setSubclipIndex(self, newIndex):
        self.recaculateFilters('setSubclipIndex')
        if self.currentSubclipIndex is not None and self.subClipOrder:
            try:
                rid = self.subClipOrder[self.currentSubclipIndex]
                self.subclips[rid]['filters'] = self.convertFilterstoSpecDefaults()
            except Exception as e:
                print(e)

        if self.currentSubclipIndex != newIndex:
            self.canvasValueTimeline.setSeekX(-1)
            self.activeCommandFilterValuePair = None

        self.currentSubclipIndex = newIndex
        for f in self.filterSpecifications:
            f.setParent(None)
            f.deleteLater()
        self.filterSpecifications = []

        if newIndex is not None and self.subClipOrder:
            rid = self.subClipOrder[self.currentSubclipIndex]
            for spec in self.subclips[rid].setdefault('filters', []):
                self.filterSpecificationCount += 1
                fs = FilterSpecification(self.filterContainer, self, spec, self.filterSpecificationCount)
                self.filterSpecifications.append(fs)
                self.filterContainerLayout.insertWidget(self.filterContainerLayout.count() - 1, fs)
            self.recaculateFilters('setSubclipIndex')

    def updateFilterDisplay(self):
        currentClip = self.getCurrentClip()
        if currentClip is None:
            return
        basename = os.path.basename(currentClip['filename'])[:16]
        s = currentClip['start']
        e = currentClip['end']
        rid = self.subClipOrder[self.currentSubclipIndex]
        newLabel = '#{r} {n} {s:.2f}-{e:.2f} {i}/{l}'.format(
            r=rid, n=basename, s=s, e=e,
            i=self.currentSubclipIndex + 1, l=len(self.subClipOrder))
        self.labelVideoPickerLabel.setText(newLabel)
        self.controller.playVideoFile(currentClip['filename'], s, e)
