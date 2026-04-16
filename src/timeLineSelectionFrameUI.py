
import os
import datetime
import threading
from math import floor
import time
import logging
from threading import Lock

import subprocess as sp
import numpy as np
import math

from contextlib import contextmanager

from PySide6.QtWidgets import QWidget, QMenu, QSizePolicy
from PySide6.QtCore import Qt, QRect, QPoint, QTimer, QSize
from PySide6.QtGui import (QPainter, QColor, QPen, QBrush, QPixmap,
                            QImage, QFont, QCursor, QKeySequence,
                            QPolygon, QPainterPath)

try:
    from .platformUtils import resource_path, tool_command
except Exception:
    from platformUtils import resource_path, tool_command


class AbstractContextManager:

    def __init__(self):
        pass

    def __enter__(self):
        pass

    def __exit__(self, type, value, traceback):
        pass


@contextmanager
def acquire_timeout(lock, timeout):
    result = lock.acquire(timeout=timeout)
    yield result
    if result:
        lock.release()


jumpRemovelock = Lock()


def format_timedelta(value, time_format="{days} days, {hours2}:{minutes2}:{seconds:02.2F}"):

    if hasattr(value, 'seconds'):
        seconds = value.seconds + value.days * 24 * 3600
    else:
        seconds = value

    seconds_total = seconds

    minutes = int(floor(seconds / 60))
    minutes_total = minutes
    seconds -= minutes * 60

    if hasattr(value, 'microseconds'):
        seconds += (value.microseconds / 1000000)
        seconds = round(seconds, 2)

    hours = int(floor(minutes / 60))
    hours_total = hours
    minutes -= hours * 60

    days = int(floor(hours / 24))
    days_total = days
    hours -= days * 24

    years = int(floor(days / 365))
    years_total = years
    days -= years * 365

    return time_format.format(**{
        'seconds': seconds,
        'seconds2': str(seconds).zfill(2),
        'minutes': minutes,
        'minutes2': str(minutes).zfill(2),
        'hours': hours,
        'hours2': str(hours).zfill(2),
        'days': days,
        'years': years,
        'seconds_total': seconds_total,
        'minutes_total': minutes_total,
        'hours_total': hours_total,
        'days_total': days_total,
        'years_total': years_total,
    })


def debounce(wait_time, max_gap):
    def decorator(function):
        def debounced(*args, **kwargs):
            def call_function():
                debounced._timer = None
                debounced._last_call = time.time()
                return function(*args, **kwargs)

            if debounced._timer is not None:
                debounced._timer.cancel()

            if debounced._last_call is not None and abs(time.time() - debounced._last_call) >= max_gap:
                function(*args, **kwargs)
                debounced._last_call = time.time()
            else:
                debounced._timer = threading.Timer(wait_time, call_function)
                debounced._timer.start()
        debounced._last_call = time.time()
        debounced._timer = None
        return debounced

    return decorator


audioProcessingSampleRate = 4000
realtimeAudoProcessing = True


def detectBeats(audio_samples, sample_rate, window_length_secs=10, n_peaks=3):
    return []


def numpy_to_pixmap(arr):
    """Convert a numpy uint8 array (H x W x 3 or H x W x 4) to QPixmap."""
    if arr is None:
        return None
    if arr.dtype != np.uint8:
        arr = arr.astype(np.uint8)
    if arr.ndim == 2:
        arr = np.stack([arr, arr, arr], axis=2)
    h, w = arr.shape[:2]
    c = arr.shape[2] if arr.ndim == 3 else 1
    fmt = QImage.Format_RGB888 if c == 3 else QImage.Format_RGBA8888
    arr_contig = np.ascontiguousarray(arr)
    img = QImage(arr_contig.data, w, h, w * c, fmt)
    return QPixmap.fromImage(img)


def pgm_bytes_to_pixmap(data):
    """Convert raw PPM/PGM bytes (as returned by ffmpeg) to QPixmap."""
    if data is None:
        return None
    try:
        px = QPixmap()
        px.loadFromData(data)
        if not px.isNull():
            return px
    except Exception:
        pass
    return None


class TimeLineSelectionFrameUI(QWidget):

    def __init__(self, master=None, controller=None, globalOptions=None, *args, **kwargs):
        super().__init__(master)
        self.controller = controller
        self.globalOptions = globalOptions or {}

        self.seekSpeedNormal = self.globalOptions.get("seekSpeedNormal", 1)
        self.seekSpeedFast   = self.globalOptions.get("seekSpeedFast", 2)
        self.seekSpeedSlow   = self.globalOptions.get("seekSpeedSlow", 0.1)

        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(200, 150)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)

        # ------------------------------------------------------------------ #
        # State that the old code kept as canvas item IDs — now plain attrs   #
        # ------------------------------------------------------------------ #
        self.timelineZoomFactor = 1.0
        self.dragPreviewPos = 0.1
        self.dragPreviewMode = 'abs'
        self.currentZoomRangeMidpoint = 0.5

        self.targetTrim = 0.25
        self.defaultSliceDuration = 10.0

        self.handleWidth = 10
        self.handleHeight = 30
        self.midrangeHeight = 20
        self.miniMidrangeHeight = 7

        self.canvasRegionCache = {}
        self.timeline_mousedownstate = {}
        self.tickmarks = []
        self.uiDirty = 1
        self.clickTarget = None
        self.lastClickedRange = None

        self.rangeHeaderClickStart = None

        self.lastSeek = None
        self.resumeplaybackTimer = None

        self.lastClickedEndpoint = None
        self.framesRequested = False
        self.previewFrames = {}   # timestamp -> (frameWidth, QPixmap)
        self.dirtySelectionRanges = set()

        self.generateWaveImages = False
        self.generateMotionImages = False

        self.lastFilenameForAudioToBytesRequest = None
        self.audioByteValuesReadLock = threading.Lock()
        self.audioByteValues = []
        self.beats = []
        self.latestAudioByteDecoded = 0
        self.completedAudioByteDecoded = False
        self.audioToBytesThread = None
        self.audioToBytesThreadKill = False
        self.generateWaveStyle = 'GENERAL'

        self.lastWavePicSectionsRequested = None
        self.waveAsPicSections = []
        self.waveAsPicPixmap = None       # QPixmap for the spectrogram
        self.waveAsPicPixmapX = 0
        self.wavePicSectionsThread = None

        self.lastSpectraStart = None
        self.lastSpectraZoomFactor = None
        self.SpectraPixmap = None
        self.SpectraPixmapX = 0

        self.tempRangeStart = None

        self.uiUpdateLock = threading.RLock()
        self.clipped = None

        self.frameRate = None
        self.initialShiftStart = 'Start'

        self.timeline_canvas_last_right_click_x = None
        self.timeline_canvas_last_right_click_range = None

        self.hoverRID = None
        self.previewRID = None
        self.previewRIDRequested = False

        # Hover preview images (QPixmap)
        self.startImg = None
        self.endImg = None

        # Random block preview state
        self.blockx0 = 0
        self.blockx1 = 0
        self.blocks0 = 0
        self.blocks1 = 0

        self.lastRandomSubclipPos = -1

        # Temporary range preview coordinates (canvas coords-style)
        self._tempRangePreview = (0, 0, 0, 0)       # x1,y1,x2,y2
        self._randRangePreview = (0, 0, 0, 0)

        # Seek pointer X
        self._seekPointerX = -100
        self._seekPointerY0 = 0
        self._seekPointerY1 = 0
        self._seekTimestampText = ''
        self._seekTimestampX = -100
        self._seekTimestampY = 0

        # Range header
        self._rangeHeaderActiveX1 = 0
        self._rangeHeaderActiveX2 = 0
        self._rangeHeaderSeekX = 0
        self._rangeHeaderMidX = 0

        # Range-region drawing data: keyed by (rid, partname) -> dict with draw params
        # We keep this for compatibility but repaint everything in paintEvent
        self._rangeDrawData = {}   # rid -> dict of geometry

        # Mouse press offset
        self.timelineMousePressOffset = 0

        # Load handle images
        self.image_handle_left_base  = self._load_pixmap(resource_path("resources", "slider_left_base.gif"))
        self.image_handle_right_base = self._load_pixmap(resource_path("resources", "slider_right_base.gif"))
        self.image_handle_left_light  = self._load_pixmap(resource_path("resources", "slider_left_light.gif"))
        self.image_handle_right_light = self._load_pixmap(resource_path("resources", "slider_right_light.gif"))

        # prefade images (optional)
        self.prefade0 = self._load_pixmap(resource_path("resources", "prefade0.png"))
        self.prefade1 = self._load_pixmap(resource_path("resources", "prefade1.png"))

        # Temp range duration label
        self._tempRangeDurationText = ''
        self._tempRangeDurationX = 0
        self._tempRangeDurationY = 0

        # Build right-click context menu
        self._build_context_menu()

        # Repaint timer — coalesces rapid update() calls
        self._repaintPending = False

        # Index for rangePropertiesEntry / similarSoundsEntry (stored as action refs)
        # (already set in _build_context_menu)

    # ---------------------------------------------------------------------- #
    # Helpers                                                                  #
    # ---------------------------------------------------------------------- #

    def _load_pixmap(self, path):
        """Load a QPixmap from path, returning None on failure."""
        try:
            px = QPixmap(path)
            if not px.isNull():
                return px
        except Exception:
            pass
        return None

    def _build_context_menu(self):
        self._ctx_menu = QMenu(self)

        self._ctx_menu.addAction("Add new subclip", self.canvasPopupAddNewSubClipCallback)
        self._ctx_menu.addAction("Add new subclip to interest marks", self.canvasPopupAddNewSubClipToInterestMarksCallback)
        self._ctx_menu.addSeparator()
        self._ctx_menu.addAction("Delete subclip", self.canvasPopupRemoveSubClipCallback)
        self._ctx_menu.addSeparator()
        self._ctx_menu.addAction("Clone subclip", self.canvasPopupCloneSubClipCallback)
        self._ctx_menu.addSeparator()
        self._ctx_menu.addAction("Copy subclip timestamps", self.canvasPopupCopySubClipCallback)
        self._ctx_menu.addAction("Paste subclip timestamps", self.canvasPopupPasteSubClipCallback)
        self._ctx_menu.addAction("Expand subclip to interest marks", self.canvasPopupExpandSublcipToInterestMarksCallback)
        self._ctx_menu.addSeparator()
        self._ctx_menu.addAction("Add new interest mark", self.canvasPopupAddNewInterestMarkCallback)
        self._ctx_menu.addSeparator()

        # Perfect loop sub-menu
        loopMenu = QMenu("Loop tools", self)
        loopMenu.addAction("Move Center on highest inter-frame difference to Start",
                           lambda: self.canvasPopupReCenterOnInterFrameDistance('start'))
        loopMenu.addAction("Move Center on highest inter-frame difference to Middle",
                           lambda: self.canvasPopupReCenterOnInterFrameDistance('mid'))
        loopMenu.addAction("Move Center on highest inter-frame difference to End",
                           lambda: self.canvasPopupReCenterOnInterFrameDistance('end'))
        loopMenu.addSeparator()
        loopMenu.addAction("Improve this loop moving the ends at most {}%".format(
            self.globalOptions.get('loopNudgeLimit1', 1)), self.canvasPopupFindLowestError1s)
        loopMenu.addAction("Improve this loop moving the ends at most {}%".format(
            self.globalOptions.get('loopNudgeLimit2', 2)), self.canvasPopupFindLowestError2s)
        loopMenu.addSeparator()
        loopMenu.addAction("Find best loop between {} and {}s centered here".format(
            self.globalOptions.get('loopSearchLower1', 2), self.globalOptions.get('loopSearchUpper1', 3)),
            self.canvasPopupFindContainingLoop3s)
        loopMenu.addAction("Find best loop between {} and {}s  centered here".format(
            self.globalOptions.get('loopSearchLower2', 3), self.globalOptions.get('loopSearchUpper2', 6)),
            self.canvasPopupFindContainingLoop6s)
        self._ctx_menu.addMenu(loopMenu)
        self._ctx_menu.addSeparator()

        self._rangePropertiesAction = self._ctx_menu.addAction("Edit subclip", self.canvasPopupRangeProperties)
        self._similarSoundsAction   = self._ctx_menu.addAction("Find Similar Sounds", self.canvasPopupSimilarSounds)

    def scheduleRepaint(self):
        if not self._repaintPending:
            self._repaintPending = True
            QTimer.singleShot(0, self._doRepaint)

    def _doRepaint(self):
        self._repaintPending = False
        self.update()

    # ---------------------------------------------------------------------- #
    # Qt overrides                                                             #
    # ---------------------------------------------------------------------- #

    def resizeEvent(self, event):
        self.setUiDirtyFlag()
        if self.controller is not None and self.controller.getTotalDuration() is not None:
            self.updateCanvas(withLock=False)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)

        W = self.width()
        H = self.height()

        # Background
        painter.fillRect(0, 0, W, H, QColor('#1E1E1E'))

        # ---- Spectrogram / wave image (drawn lowest) ----
        if self.SpectraPixmap is not None:
            painter.drawPixmap(int(self.SpectraPixmapX), 45, self.SpectraPixmap)
        elif self.waveAsPicPixmap is not None:
            painter.drawPixmap(int(self.waveAsPicPixmapX), 45 + 20 + 20, self.waveAsPicPixmap)

        # ---- Preview frames ----
        for ts, (fw, pixmap) in list(self.previewFrames.items()):
            if pixmap is not None:
                ts_x = int(self.secondsToXcoord(ts))
                painter.drawPixmap(ts_x, 20, pixmap)

        # ---- Hover end frames ----
        ticky = 0
        if self.globalOptions.get('generateTimelineThumbnails', True):
            ticky = 45

        if self.startImg is not None:
            # Drawn at the start handle position, anchor sw
            if self.hoverRID == 'V':
                ridstart = self.blocks0
            else:
                try:
                    _, ridend, ridstart = self.controller.controller.videoManager.getDetailsForRangeId(self.hoverRID)
                except Exception:
                    ridstart = None
            if ridstart is not None:
                st = int(self.secondsToXcoord(ridstart))
                offset = self.handleWidth
                if self.hoverRID == 'V':
                    offset = 0
                px = self.startImg
                painter.drawPixmap(st + offset, H - px.height(), px)

        if self.endImg is not None:
            if self.hoverRID == 'V':
                ridend = self.blocks1
            else:
                try:
                    _, ridend, ridstart = self.controller.controller.videoManager.getDetailsForRangeId(self.hoverRID)
                except Exception:
                    ridend = None
            if ridend is not None:
                en = int(self.secondsToXcoord(ridend))
                offset = self.handleWidth
                if self.hoverRID == 'V':
                    offset = 0
                px = self.endImg
                painter.drawPixmap(en - offset - px.width(), H - px.height(), px)

        # ---- Range header background ----
        painter.fillRect(0, 0, W, 20, QColor('#3F3F7F'))

        # ---- Range header active range ----
        painter.fillRect(int(self._rangeHeaderActiveX1), 0,
                         int(self._rangeHeaderActiveX2 - self._rangeHeaderActiveX1), 20,
                         QColor('#9E9E9E'))

        # ---- Range header seek mid line ----
        painter.setPen(QPen(QColor('#4E4E4E'), 1))
        x_mid = int(self._rangeHeaderMidX)
        painter.drawLine(x_mid, 0, x_mid, 20)

        # ---- Range header seek pointer ----
        painter.setPen(QPen(QColor('white'), 1))
        x_seek = int(self._rangeHeaderSeekX)
        painter.drawLine(x_seek, 0, x_seek, 20)

        # ---- Interest marks (tick polygons) ----
        if self.controller is not None:
            try:
                for ts, interesttype in self.controller.getInterestMarks():
                    tx = int(self.secondsToXcoord(ts))
                    poly = QPolygon([
                        QPoint(tx - 5, ticky + 85),
                        QPoint(tx + 5, ticky + 85),
                        QPoint(tx, ticky + 90),
                    ])
                    if interesttype == 'manual':
                        painter.setBrush(QBrush(QColor('#ead9a7')))
                    elif interesttype == 'sceneChange':
                        painter.setBrush(QBrush(QColor('green')))
                    else:
                        painter.setBrush(QBrush(QColor('#ead9a7')))
                    painter.setPen(Qt.NoPen)
                    painter.drawPolygon(poly)
            except Exception:
                pass

        # ---- Tick marks ----
        if self.controller is not None and self.controller.getTotalDuration() is not None:
            painter.setPen(QPen(QColor('white'), 1))
            font = QFont()
            font.setPointSize(7)
            painter.setFont(font)

            try:
                timelineWidth = W
                initialzoom = self.timelineZoomFactor
                ticksdone = False
                tickStart = self.xCoordToSeconds(0)
                tickIncrement = (self.xCoordToSeconds(timelineWidth) - self.xCoordToSeconds(0)) / 20
                if tickIncrement > 0:
                    tickStart = int((tickIncrement * round(tickStart / tickIncrement)) - tickIncrement)
                    while True:
                        tickStart += tickIncrement
                        tx = int(self.secondsToXcoord(tickStart))
                        if tx < 0:
                            pass
                        elif tx >= W:
                            ticksdone = True
                            break
                        else:
                            painter.setPen(QPen(QColor('white'), 1))
                            painter.drawLine(tx, ticky + 20, tx, ticky + 25)
                            tm_txt = format_timedelta(
                                datetime.timedelta(seconds=round(self.xCoordToSeconds(tx))),
                                '{hours_total}:{minutes2}:{seconds:02.2F}')
                            # Shadow
                            painter.setPen(QPen(QColor('black'), 1))
                            painter.drawText(tx - 1, ticky + 30 - 1, tm_txt)
                            painter.drawText(tx + 1, ticky + 30 + 1, tm_txt)
                            painter.setPen(QPen(QColor('white'), 1))
                            painter.drawText(tx, ticky + 30, tm_txt)
            except Exception:
                pass

        # ---- Range regions ----
        if self.controller is not None and self.controller.getTotalDuration() is not None:
            try:
                ranges = self.controller.getRangesForClip(self.controller.getcurrentFilename())
                for rid, (s, e) in list(ranges):
                    sx = int(self.secondsToXcoord(s))
                    ex = int(self.secondsToXcoord(e))
                    trimpreend    = int(self.secondsToXcoord(s + self.targetTrim))
                    trimpostStart = int(self.secondsToXcoord(e - self.targetTrim))
                    mx = (sx + ex) // 2

                    # Main fill
                    if self.clipped == rid:
                        main_color = QColor('#ffffff')
                    else:
                        main_color = QColor('#69dbbe')

                    painter.fillRect(sx + 1, H - self.midrangeHeight, ex - sx - 2, self.midrangeHeight, main_color)

                    # Pre/post trim
                    painter.fillRect(sx, H - self.midrangeHeight,
                                     min(trimpreend, ex) - sx, self.midrangeHeight,
                                     QColor('#218a6f'))
                    painter.fillRect(max(trimpostStart, sx), H - self.midrangeHeight,
                                     ex - max(trimpostStart, sx), self.midrangeHeight,
                                     QColor('#218a6f'))

                    # Mini drag bar
                    if self.lastClickedRange == rid:
                        mini_color = QColor('#e5f9f4')
                    else:
                        mini_color = QColor('#2bb390')
                    painter.fillRect(sx, H - self.miniMidrangeHeight,
                                     ex - sx, self.miniMidrangeHeight, mini_color)

                    # Mid line
                    painter.setPen(QPen(QColor('#ffffff'), 1))
                    painter.drawLine(mx, H - self.midrangeHeight - 10, mx, H - self.miniMidrangeHeight)

                    # Label
                    if self.clipped == rid:
                        label_text = "At Video Edge"
                    else:
                        label_text = "{}s".format(format_timedelta(
                            datetime.timedelta(seconds=round(e - s, 2)),
                            '{hours_total}:{minutes2}:{seconds:02.2F}'))
                    painter.setPen(QPen(QColor('white'), 1))
                    painter.drawText(mx, H - self.midrangeHeight - 20, label_text)

                    # Start / End handles (pixmaps)
                    if self.lastClickedEndpoint is not None and self.lastClickedEndpoint[0] == rid and self.lastClickedEndpoint[1] == 's':
                        lh = self.image_handle_left_light
                    else:
                        lh = self.image_handle_left_base
                    if self.lastClickedEndpoint is not None and self.lastClickedEndpoint[0] == rid and self.lastClickedEndpoint[1] == 'e':
                        rh = self.image_handle_right_light
                    else:
                        rh = self.image_handle_right_base

                    if lh is not None:
                        # anchor 'ne' -> draw so top-right corner is at (sx, H-handleHeight)
                        painter.drawPixmap(sx - lh.width(), H - self.handleHeight, lh)
                    if rh is not None:
                        # anchor 'nw' -> draw so top-left corner is at (ex, H-handleHeight)
                        painter.drawPixmap(ex, H - self.handleHeight, rh)

                    # Header range bar
                    if self.controller.getTotalDuration() and self.controller.getTotalDuration() > 0:
                        hstx = int((s / self.controller.getTotalDuration()) * W)
                        henx = int((e / self.controller.getTotalDuration()) * W)
                        painter.fillRect(hstx, 10, henx - hstx, 10, QColor('#299b9b'))

            except Exception as exc:
                logging.error('paintEvent ranges error', exc_info=exc)

        # ---- Temp range preview (selection being created) ----
        x1, y1, x2, y2 = self._tempRangePreview
        if x1 != x2 or y1 != y2:
            painter.setBrush(QBrush(QColor('#113a47')))
            pen = QPen(QColor('#69bfdb'), 1, Qt.DashLine)
            painter.setPen(pen)
            painter.drawRect(int(min(x1, x2)), int(min(y1, y2)),
                             int(abs(x2 - x1)), int(abs(y2 - y1)))
            # Duration label
            if self._tempRangeDurationText:
                painter.setPen(QPen(QColor('#69bfdb'), 1))
                painter.drawText(int(self._tempRangeDurationX), int(self._tempRangeDurationY),
                                 self._tempRangeDurationText)

        # ---- Rand range preview ----
        rx1, ry1, rx2, ry2 = self._randRangePreview
        if rx1 != rx2 or ry1 != ry2:
            painter.setBrush(QBrush(QColor('#461147')))
            painter.setPen(QPen(QColor('#ca56cc'), 1))
            painter.drawRect(int(min(rx1, rx2)), int(min(ry1, ry2)),
                             int(abs(rx2 - rx1)), int(abs(ry2 - ry1)))

        # ---- Seek pointer ----
        sx_ptr = int(self._seekPointerX)
        if sx_ptr >= 0:
            painter.setPen(QPen(QColor('white'), 1))
            painter.drawLine(sx_ptr, int(self._seekPointerY1), sx_ptr, int(self._seekPointerY0))
            if self._seekTimestampText:
                painter.drawText(int(self._seekTimestampX), int(self._seekTimestampY),
                                 self._seekTimestampText)

        painter.end()

    def keyPressEvent(self, event):
        key = event.key()
        modifiers = event.modifiers()

        # Build a fake event-like object with .state for existing handler methods
        class FakeEvent:
            def __init__(self, state_val):
                self.state = state_val
        state = 0
        if modifiers & Qt.ShiftModifier:
            state |= 0x1
        if modifiers & Qt.ControlModifier:
            state |= 0x4
        if modifiers & Qt.AltModifier:
            state |= 0x20000
        fe = FakeEvent(state)

        if key == Qt.Key_Left:
            self.keyboardLeft(fe)
        elif key == Qt.Key_Right:
            self.keyboardRight(fe)
        elif key == Qt.Key_Space:
            self.keyboardSpace(fe)
        elif key in (Qt.Key_V,):
            self.keyboardTempSection(fe)
        elif key in (Qt.Key_C,):
            if modifiers & Qt.ControlModifier:
                self.controller.addFullClip()
            else:
                self.keyboardCutatTime(fe)
        elif key in (Qt.Key_M,):
            self.keyboardMergeatTime(fe)
        elif key in (Qt.Key_B,):
            self.keyboardBlockAtTime(fe)
        elif key in (Qt.Key_D,):
            self.keyboardRemoveBlockAtTime(fe)
        elif key in (Qt.Key_Q,):
            self.controller.jumpClips(-1)
        elif key in (Qt.Key_E,):
            self.controller.jumpClips(1)
        elif key in (Qt.Key_R,):
            if modifiers & Qt.ControlModifier:
                self.controller.searchrandom(0)
            else:
                self.controller.randomClip()
        elif key in (Qt.Key_F,):
            if modifiers & Qt.ControlModifier:
                self.controller.search(0)
            else:
                self.controller.fastSeek()
        elif key == Qt.Key_A and (modifiers & Qt.ControlModifier):
            self.controller.addFullClip()
        elif key == Qt.Key_Comma:
            self.stepBackwards(fe)
        elif key == Qt.Key_Period:
            self.stepForwards(fe)
        elif key == Qt.Key_Y:
            self.acceptAndRandomJump(fe)
        elif key == Qt.Key_U:
            self.rejectAndRandomJump(fe)
        else:
            super().keyPressEvent(event)

    def mousePressEvent(self, event):
        self._dispatchMouseEvent(event)

    def mouseReleaseEvent(self, event):
        self._dispatchMouseEvent(event)

    def mouseMoveEvent(self, event):
        self._dispatchMouseEvent(event)

    def enterEvent(self, event):
        pass

    def leaveEvent(self, event):
        pass

    def wheelEvent(self, event):
        self._dispatchWheelEvent(event)

    def _dispatchWheelEvent(self, qevent):
        """Translate Qt wheel event → call timelineMousewheel with a fake Tk-like event."""
        modifiers = qevent.modifiers()
        state = 0
        if modifiers & Qt.ShiftModifier:
            state |= 0x1
        if modifiers & Qt.ControlModifier:
            state |= 0x4
        if modifiers & Qt.AltModifier:
            state |= 0x20000

        delta = qevent.angleDelta().y()
        pos = qevent.position() if hasattr(qevent, 'position') else qevent.pos()

        class FakeWheelEvent:
            pass
        fe = FakeWheelEvent()
        fe.state = state
        fe.x = int(pos.x())
        fe.y = int(pos.y())
        fe.delta = delta

        self.timelineMousewheel(fe)

    def _dispatchMouseEvent(self, qevent):
        """Translate Qt mouse event → call timelineMousePress with a fake Tk-like event."""
        import PySide6.QtCore as _qc

        pos = qevent.position() if hasattr(qevent, 'position') else qevent.pos()
        gpos = qevent.globalPosition() if hasattr(qevent, 'globalPosition') else qevent.globalPos()

        modifiers = qevent.modifiers()
        state = 0
        if modifiers & Qt.ShiftModifier:
            state |= 0x1
        if modifiers & Qt.ControlModifier:
            state |= 0x4
        if modifiers & Qt.AltModifier:
            state |= 0x20000

        buttons = qevent.buttons()

        # Map Qt event type to Tk-style type
        qtype = qevent.type()
        if qtype == _qc.QEvent.MouseButtonPress:
            tk_type = _FakeTkEventType.ButtonPress
            num = self._qt_button_num(qevent.button())
        elif qtype == _qc.QEvent.MouseButtonRelease:
            tk_type = _FakeTkEventType.ButtonRelease
            num = self._qt_button_num(qevent.button())
        elif qtype == _qc.QEvent.MouseMove:
            tk_type = _FakeTkEventType.Motion
            num = 0
        else:
            return

        # Determine which buttons are held (for timeline_mousedownstate)
        if buttons & Qt.LeftButton:
            pass
        if buttons & Qt.RightButton:
            pass

        class FakeMouseEvent:
            pass
        fe = FakeMouseEvent()
        fe.x = int(pos.x())
        fe.y = int(pos.y())
        fe.x_root = int(gpos.x())
        fe.y_root = int(gpos.y())
        fe.state = state
        fe.type = tk_type
        fe.num = num

        self.timelineMousePress(fe)

    @staticmethod
    def _qt_button_num(button):
        if button == Qt.LeftButton:
            return 1
        elif button == Qt.MiddleButton:
            return 2
        elif button == Qt.RightButton:
            return 3
        return 0

    # ---------------------------------------------------------------------- #
    # Original methods — lightly adapted (replace canvas calls with state)    #
    # ---------------------------------------------------------------------- #

    def stepBackwards(self, e):
        self.controller.stepBackwards()

    def stepForwards(self, e):
        self.controller.stepForwards()

    def acceptAndRandomJump(self, e):
        ctrl = (e.state & 0x4) != 0
        if ctrl:
            self.controller.randomClip()
            return

        target = self.controller.fastSeek(centerAfter=True)

        if self.lastRandomSubclipPos is not None:
            self.keyboardBlockAtTime(e, pos=self.lastRandomSubclipPos, abonly=False)

        self.keyboardBlockAtTime(e, pos=target, abonly=True)

        self.lastRandomSubclipPos = target

        self.setUiDirtyFlag()
        self.controller.requestAutoconvert()
        print('Exit acceptAndRandomJump')

    def rejectAndRandomJump(self, e):
        target = self.controller.fastSeek(centerAfter=True)
        self.keyboardBlockAtTime(e, pos=target, abonly=True)
        self.lastRandomSubclipPos = target

    def correctMouseXPosition(self, x):
        # No-op in Qt: cannot warp cursor the same way; just note new X
        pass

    def getCurrentlySelectedRegion(self):
        start = self.tempRangeStart
        pos = self.controller.getCurrentPlaybackPosition()
        if start is not None and pos is not None and pos != start:
            a, b = sorted([start, pos])
            return a, b
        return None, None

    def processFileAudioToBytes(self, filename, totalDuration, style='SPEECH'):
        print('processFileAudioToBytes ENTRY')
        sampleRate = audioProcessingSampleRate
        if self.generateWaveStyle == 'SPEECH':
            proc = sp.Popen([tool_command('ffmpeg'), '-i', filename, '-ac', '1', '-filter:a',
                             'arnndn={model},loudnorm=I=-16:TP=-1.5:LRA=11,aresample={sampleRate}:async=1'.format(
                                 model=resource_path("resources", "speechModel", "model.rnnn").replace('\\', '/'),
                                 sampleRate=sampleRate),
                             '-map', '0:a', '-c:a', 'pcm_u8', '-f', 'data', '-'],
                            stdout=sp.PIPE, stderr=sp.DEVNULL)
        elif self.generateWaveStyle == 'VOICE':
            proc = sp.Popen([tool_command('ffmpeg'), '-i', filename, '-ac', '1', '-filter:a',
                             'arnndn={model},loudnorm=I=-16:TP=-1.5:LRA=11,aresample={sampleRate}:async=1'.format(
                                 model=resource_path("resources", "voiceModel", "model.rnnn").replace('\\', '/'),
                                 sampleRate=sampleRate),
                             '-map', '0:a', '-c:a', 'pcm_u8', '-f', 'data', '-'],
                            stdout=sp.PIPE, stderr=sp.DEVNULL)
        else:
            proc = sp.Popen([tool_command('ffmpeg'), '-i', filename, '-ac', '1', '-filter:a',
                             'compand,highpass=f=200,lowpass=f=3000,aresample={}:async=1'.format(sampleRate),
                             '-map', '0:a', '-c:a', 'pcm_u8', '-f', 'data', '-'],
                            stdout=sp.PIPE, stderr=sp.DEVNULL)
        self.audioByteValues = np.ones((int(totalDuration * sampleRate)), np.uint8) * 127
        n = 0
        self.completedAudioByteDecoded = False
        while 1:
            if self.audioToBytesThreadKill:
                break
            n += 1
            l = proc.stdout.read(1)
            if len(l) == 0:
                break
            self.audioByteValuesReadLock.acquire()
            if n == 1:
                self.audioByteValues = np.ones((int(totalDuration * sampleRate)), np.uint8) * 127
            try:
                self.audioByteValues[n - 1] = int.from_bytes(l, "little")
                self.latestAudioByteDecoded = n / sampleRate
            except Exception as exc:
                print(exc)

            if realtimeAudoProcessing and n % (sampleRate * 50) == 0 and n > 1:
                self.beats = detectBeats(self.audioByteValues, sampleRate)

            self.audioByteValuesReadLock.release()
        proc.communicate()

        self.beats = detectBeats(self.audioByteValues, sampleRate)

        if self.audioToBytesThreadKill:
            self.audioToBytesThreadKill = False
            return

        self.completedAudioByteDecoded = True
        self.audioToBytesThreadKill = False
        self.audioToBytesThread = None

    def generateImageSections(self, filename, startpc, endpc, totalDuration, outputWidth):
        args = (filename, startpc, endpc, totalDuration, outputWidth)

        completeOnLastPass = False
        while 1:
            startTS = totalDuration * startpc
            endTS = totalDuration * endpc

            if self.lastSpectraStart is not None and self.SpectraPixmap is not None:
                if self.lastSpectraStart != startTS and self.lastSpectraZoomFactor == self.timelineZoomFactor:
                    oldx = self.secondsToXcoord(self.lastSpectraStart)
                    self.SpectraPixmapX = oldx
                    self.scheduleRepaint()

            if self.completedAudioByteDecoded:
                completeOnLastPass = True

            indSt = int(math.floor(len(self.audioByteValues) * (startTS / totalDuration)))
            indEn = int(math.floor(len(self.audioByteValues) * (endTS / totalDuration)))

            tempsamples = np.array(self.audioByteValues[indSt:indEn], np.uint8)

            if args != self.lastWavePicSectionsRequested:
                return

            proc = sp.Popen([tool_command('ffmpeg'), '-y', '-f', 'u8', '-i', 'pipe:0',
                             '-filter_complex',
                             "showspectrumpic=s={width}x120:legend=0:fscale=lin:scale=cbrt".format(
                                 width=outputWidth),
                             '-c:v', 'ppm', '-f', 'rawvideo', '-'],
                            stdout=sp.PIPE, stdin=sp.PIPE)

            outs, errs = proc.communicate(input=tempsamples.tobytes())

            if args != self.lastWavePicSectionsRequested:
                return

            new_pixmap = pgm_bytes_to_pixmap(outs)
            if new_pixmap is not None:
                self.SpectraPixmap = new_pixmap
                self.SpectraPixmapX = 0
                self.lastSpectraStart = startTS
                self.lastSpectraZoomFactor = self.timelineZoomFactor
                self.scheduleRepaint()

            time.sleep(0.1)

            if self.completedAudioByteDecoded and not completeOnLastPass:
                completeOnLastPass = True
                continue

            if completeOnLastPass:
                self.wavePicSectionsThread = None
                return

    def clearCurrentlySelectedRegion(self):
        self.tempRangeStart = None
        self.updateCanvas()

    def keyboardMergeatTime(self, e):
        if self.tempRangeStart is not None:
            a, b = sorted([self.tempRangeStart, self.controller.getCurrentPlaybackPosition()])
            overlappingRanges = []
            ranges = self.controller.getRangesForClip(self.controller.getcurrentFilename())
            for rid, (s, en) in list(ranges):
                if a <= s <= b or a <= en <= b:
                    overlappingRanges.append((s, en, rid))
            if len(ranges) > 1:
                overlappingRanges = sorted(overlappingRanges)
                finalend = overlappingRanges[-1][1]
                for s, en, rid in overlappingRanges[1:]:
                    self.controller.removeSubclip((s + en) / 2)
                self.tempRangeStart = None
                self.controller.updatePointForClip(self.controller.getcurrentFilename(), overlappingRanges[0][2], 'e', finalend)
                self.setUiDirtyFlag(specificRID=overlappingRanges[0][2])

    def keyboardCutatTime(self, e):
        mid = self.controller.getCurrentPlaybackPosition()
        selectedRange = None
        ranges = self.controller.getRangesForClip(self.controller.getcurrentFilename())
        for rid, (s, en) in list(ranges):
            if s < mid < en:
                selectedRange = rid
                break
        if selectedRange is not None:
            mid = self.roundToNearestFrame(mid)
            self.controller.updatePointForClip(self.controller.getcurrentFilename(), selectedRange, 'e', mid)
            self.controller.addNewSubclip(mid, en, seekAfter=False)
            self.setUiDirtyFlag(specificRID=selectedRange)
            self.updateCanvas(withLock=False)

    def keyboardRemoveBlockAtTime(self, e, pos=None):
        self._randRangePreview = (0, 0, 0, 0)
        self.scheduleRepaint()

        if self.tempRangeStart is not None:
            if pos is None:
                pos = self.controller.getCurrentPlaybackPosition()

            a, b = sorted([self.tempRangeStart, pos])

            ranges = self.controller.getRangesForClip(self.controller.getcurrentFilename())
            for rid, (s, en) in list(ranges):
                if a <= s <= b and a <= en <= b:
                    self.controller.removeSubclip((s + en) / 2)
                    self.setUiDirtyFlag(specificRID=rid)
            self.tempRangeStart = None
        else:
            mid = self.controller.getCurrentPlaybackPosition()
            self.controller.removeSubclip(mid)

    def keyboardBlockAtTime(self, e, pos=None, abonly=False):
        ctrl = (e.state & 0x4) != 0
        if ctrl:
            return

        if pos is None:
            pos = self.controller.getCurrentPlaybackPosition()

        if pos is not None:
            pre = self.defaultSliceDuration * 0.5
            post = self.defaultSliceDuration * 0.5
            a, b = self.roundToNearestFrame(pos - pre), self.roundToNearestFrame(pos + post)
            if abonly:
                ax, bx = self.secondsToXcoord(a), self.secondsToXcoord(b)
                self._randRangePreview = (ax, 140, bx, 150)
                self.scheduleRepaint()

                self.hoverRID = 'V'
                self.previewRID = 'V'

                self.blockx0 = ax
                self.blockx1 = bx
                self.blocks0 = a
                self.blocks1 = b

                self.requestRIDHoverPreviews(self.previewRID)
                self.previewRIDRequested = True

                self.controller.setAB(a, b, seekAfter=False)
            else:
                self.controller.addNewSubclip(a, b, seekAfter=False)

    def startTempSelection(self, startOverride=None):
        if startOverride is not None:
            self.tempRangeStart = startOverride
        else:
            self.tempRangeStart = self.controller.getCurrentPlaybackPosition()

    def endTempSelection(self):
        a, b = self.tempRangeStart, self.controller.getCurrentPlaybackPosition()
        a, b = self.roundToNearestFrame(a), self.roundToNearestFrame(b)
        if a != b:
            self.controller.addNewSubclip(a, b, seekAfter=False)
        self.tempRangeStart = None
        self.updateCanvas()
        self.setUiDirtyFlag()

    def keyboardTempSection(self, e):
        if self.tempRangeStart is None:
            self.startTempSelection()
        else:
            self.endTempSelection()

    def keyboardSpace(self, e):
        self.controller.playPauseToggle()

    def recenterZoomView(self):
        pospc = self.controller.getCurrentPlaybackPosition() / self.controller.getTotalDuration()
        timelineWidth = self.width()
        startpc = self.xCoordToSeconds(0) / self.controller.getTotalDuration()
        endpc   = self.xCoordToSeconds(timelineWidth) / self.controller.getTotalDuration()

        if pospc <= startpc or pospc >= endpc:
            self.currentZoomRangeMidpoint = pospc
            self.centerTimelineOnCurrentPosition()

    def keyboardRight(self, e):
        pos = self.controller.getCurrentPlaybackPosition()
        shift = (e.state & 0x1) != 0
        ctrl  = (e.state & 0x4) != 0

        self._randRangePreview = (0, 0, 0, 0)
        self.scheduleRepaint()

        if self.lastClickedEndpoint is not None:
            self.incrementEndpointPosition(
                self.seekSpeedSlow if ctrl and shift else self.seekSpeedFast if shift else self.seekSpeedNormal,
                *self.lastClickedEndpoint)
        else:
            if ctrl and shift:
                self.controller.seekRelative(self.seekSpeedSlow)
            elif shift:
                self.controller.seekRelative(self.seekSpeedFast)
            elif ctrl:
                ranges = self.controller.getRangesForClip(self.controller.getcurrentFilename())
                nextTarget = None
                for rid, (sts, ens) in sorted(ranges, key=lambda x: x[1][0])[::-1]:
                    if sts > pos:
                        nextTarget = (sts + ens) / 2
                if nextTarget is not None:
                    self.seekto(nextTarget)
                else:
                    self.controller.seekRelative(self.seekSpeedNormal)
            else:
                self.controller.seekRelative(self.seekSpeedNormal)
        self.recenterZoomView()

    def keyboardLeft(self, e):
        pos = self.controller.getCurrentPlaybackPosition()
        shift = (e.state & 0x1) != 0
        ctrl  = (e.state & 0x4) != 0

        self._randRangePreview = (0, 0, 0, 0)
        self.scheduleRepaint()

        if self.lastClickedEndpoint is not None:
            self.incrementEndpointPosition(
                -self.seekSpeedSlow if ctrl and shift else -self.seekSpeedFast if shift else -self.seekSpeedNormal,
                *self.lastClickedEndpoint)
        else:
            if ctrl and shift:
                self.controller.seekRelative(-self.seekSpeedSlow)
            elif shift:
                self.controller.seekRelative(-self.seekSpeedFast)
            elif ctrl:
                ranges = self.controller.getRangesForClip(self.controller.getcurrentFilename())
                nextTarget = None
                for rid, (sts, ens) in sorted(ranges, key=lambda x: x[1][1]):
                    if ens < pos:
                        nextTarget = (sts + ens) / 2
                if nextTarget is not None:
                    self.seekto(nextTarget)
                else:
                    self.controller.seekRelative(-self.seekSpeedNormal)
            else:
                self.controller.seekRelative(-self.seekSpeedNormal)
        self.recenterZoomView()

    def incrementEndpointPosition(self, increment, markerrid, pos):
        ranges = self.controller.getRangesForClip(self.controller.getcurrentFilename())
        for rid, (sts, ens) in ranges:
            if rid == markerrid:
                self.controller.pause()
                try:
                    self.resumeplaybackTimer.cancel()
                except AttributeError:
                    pass
                if self.globalOptions.get('autoResumeAfterSeek', True):
                    self.resumeplaybackTimer = threading.Timer(0.8, self.controller.play)
                    self.resumeplaybackTimer.start()

                if pos == 's':
                    startTarget = self.roundToNearestFrame(sts + (increment * 0.05))
                    self.clipped = self.controller.updatePointForClip(self.controller.getcurrentFilename(), rid, pos, startTarget)
                    self.setUiDirtyFlag(specificRID=rid)
                    self.seekto(startTarget)
                elif pos == 'e':
                    endTarget = self.roundToNearestFrame(ens + (increment * 0.05))
                    self.clipped = self.controller.updatePointForClip(self.controller.getcurrentFilename(), rid, pos, endTarget)
                    self.setUiDirtyFlag(specificRID=rid)
                    self.seekto(endTarget - 0.001)
                break

    def setDragPreviewPos(self, value, mode):
        self.dragPreviewPos = value
        self.dragPreviewMode = mode

    def resetForNewFile(self):
        self.timelineZoomFactor = 1.0
        self.currentZoomRangeMidpoint = 0.5
        self.tempRangeStart = None
        self.canvasRegionCache = {}
        self.controller.requestTimelinePreviewFrames(None, None, None, None, None, self.frameResponseCallback)
        self.framesRequested = False
        self._seekPointerX = -100
        self._tempRangePreview = (0, 0, 0, 0)
        self._randRangePreview = (0, 0, 0, 0)
        self._tempRangeDurationText = ''
        self.previewFrames = {}
        self.SpectraPixmap = None
        self.waveAsPicPixmap = None
        self.generateWaveImages = False
        self.generateMotionImages = False
        self.audioToBytesThreadKill = True
        self.audioToBytesThread = None
        self.completedAudioByteDecoded = False
        self.frameRate = None
        self.lastRandomSubclipPos = None
        self.startImg = None
        self.endImg = None
        self.hoverRID = None
        self.previewRID = None
        self.previewRIDRequested = False
        self.setUiDirtyFlag()
        self.scheduleRepaint()

    def updateFrameRate(self, fps):
        print('########FPS!!!', fps)
        self.frameRate = fps

    @staticmethod
    def pureGetClampedCenterPosAndRange(totalDuration, zoomFactor, currentMidpoint):
        outputDuration = totalDuration * (1 / zoomFactor)
        minPercent     = (1 / zoomFactor) / 2
        center         = min(max(minPercent, currentMidpoint), 1 - minPercent)
        lowerRange     = (totalDuration * center) - (outputDuration / 2)
        return outputDuration, center, lowerRange

    def getClampedCenterPosAndRange(self, update=True, negative=False):
        result = self.pureGetClampedCenterPosAndRange(self.controller.getTotalDuration(),
                                                      self.timelineZoomFactor,
                                                      self.currentZoomRangeMidpoint)
        outputDuration, center, lowerRange = result
        if update:
            self.currentZoomRangeMidpoint = center
        return center, lowerRange, outputDuration

    def roundToNearestFrame(self, seconds):
        if self.frameRate is not None:
            rem = seconds % (1 / self.frameRate)
            return seconds - rem
        return seconds

    def secondsToXcoord(self, seconds, update=True, negative=False):
        try:
            center, rangeStart, duration = self.getClampedCenterPosAndRange(update=update, negative=negative)
            return (((seconds - rangeStart)) / duration) * self.width()
        except Exception as exc:
            logging.error('Seconds to x coord Exception', exc_info=exc)
            return 0

    def xCoordToSeconds(self, xpos, update=True, negative=False):
        try:
            center, rangeStart, duration = self.getClampedCenterPosAndRange(update=update, negative=negative)
            seconds = rangeStart + ((xpos / self.width()) * duration)
            return self.roundToNearestFrame(seconds)
        except Exception as exc:
            logging.error('x coord to seconds Exception', exc_info=exc)
            return 0

    def timelineMousewheel(self, e):
        ctrl  = (e.state & 0x4) != 0
        shift = (e.state & 0x1) != 0
        alt   = (e.state & 0x20000) != 0

        self._randRangePreview = (0, 0, 0, 0)
        self.scheduleRepaint()

        if e.y < 20:
            factor = 0.10
            if shift:
                factor = 0.25
            if ctrl:
                factor = 1

            if e.delta > 0:
                self.currentZoomRangeMidpoint += factor / self.timelineZoomFactor
                self.setUiDirtyFlag()
                return
            else:
                self.currentZoomRangeMidpoint -= factor / self.timelineZoomFactor
                self.setUiDirtyFlag()
                return

        ranges = self.controller.getRangesForClip(self.controller.getcurrentFilename())

        rangeHit = False
        for rid, (sts, ens) in ranges:
            st = self.secondsToXcoord(sts)
            en = self.secondsToXcoord(ens)
            if st - self.handleWidth <= e.x <= en + self.handleWidth and e.y > self.height() - (self.midrangeHeight + 10):
                rangeHit = True
                targetSeconds = (sts + ens) / 2

                jumpspeed = 0.01
                print(shift, alt)
                if shift:
                    jumpspeed = 0.1
                if alt:
                    jumpspeed = 1.0
                if shift and alt:
                    jumpspeed = 10.0

                if e.delta > 0:
                    targetSeconds += jumpspeed
                else:
                    targetSeconds -= jumpspeed

                self.controller.pause()
                try:
                    self.resumeplaybackTimer.cancel()
                except AttributeError:
                    pass
                if self.globalOptions.get('autoResumeAfterSeek', True):
                    self.resumeplaybackTimer = threading.Timer(0.8, self.controller.play)
                    self.resumeplaybackTimer.start()
                targetSeconds = self.roundToNearestFrame(targetSeconds)
                self.clipped = self.controller.updatePointForClip(self.controller.getcurrentFilename(), rid, 'm', targetSeconds)
                self.setUiDirtyFlag(specificRID=rid)

                newX = self.secondsToXcoord(targetSeconds)
                qw = int((en - st) / 4)

                if qw < 3:
                    qw = 0

                if ((st + en) / 2) > e.x:
                    # Closer to End
                    newX -= qw
                    if self.dragPreviewMode == 'abs':
                        dragoffset = self.dragPreviewPos
                    else:
                        dragoffset = (ens - sts) * (self.dragPreviewPos / 100)

                    if ctrl:
                        self.controller.seekTo(((targetSeconds + ((ens - sts) / 2))) - dragoffset)
                    else:
                        self.controller.seekTo(((targetSeconds - ((ens - sts) / 2))) + dragoffset)
                else:
                    # Closer to Start
                    newX += qw

                    if self.dragPreviewMode == 'abs':
                        dragoffset = self.dragPreviewPos
                    else:
                        dragoffset = (ens - sts) * (self.dragPreviewPos / 100)

                    if ctrl:
                        self.controller.seekTo(((targetSeconds - ((ens - sts) / 2))) + dragoffset)
                    else:
                        self.controller.seekTo(((targetSeconds + ((ens - sts) / 2))) - dragoffset)

                self.correctMouseXPosition(newX)
                break

        if shift and not rangeHit:
            if e.delta > 0:
                if ctrl and shift:
                    self.controller.seekRelative(+self.seekSpeedSlow)
                else:
                    self.controller.seekRelative(+self.seekSpeedFast)
                self.currentZoomRangeMidpoint = self.controller.getCurrentPlaybackPosition() / self.controller.getTotalDuration()
                self.setUiDirtyFlag()
            else:
                if ctrl and shift:
                    self.controller.seekRelative(-self.seekSpeedSlow)
                else:
                    self.controller.seekRelative(-self.seekSpeedFast)
                self.currentZoomRangeMidpoint = self.controller.getCurrentPlaybackPosition() / self.controller.getTotalDuration()
                self.setUiDirtyFlag()

        elif not rangeHit:
            newZoomFactor = self.timelineZoomFactor
            if e.delta > 0:
                newZoomFactor *= 1.5 if ctrl else 1.01
                self.setUiDirtyFlag()
            else:
                newZoomFactor *= 0.666 if ctrl else 0.99
                self.setUiDirtyFlag()

            maxzoom = 150
            try:
                maxzoom = int(self.controller.getTotalDuration())
            except Exception:
                pass

            newZoomFactor = min(max(1, newZoomFactor), maxzoom)

            if newZoomFactor == self.timelineZoomFactor:
                self.currentZoomRangeMidpoint = self.controller.getCurrentPlaybackPosition() / self.controller.getTotalDuration()
                return

            newZoomRangeMidpoint = self.controller.getCurrentPlaybackPosition() / self.controller.getTotalDuration()
            self.currentZoomRangeMidpoint = (self.currentZoomRangeMidpoint + newZoomRangeMidpoint) / 2

            self.timelineZoomFactor = newZoomFactor

    @debounce(0.1, 0.5)
    def seekto(self, seconds):
        if self.lastSeek is not None and abs(self.lastSeek - seconds) < 0.001:
            return
        else:
            self.lastSeek = seconds
        self.controller.seekTo(seconds)

    def requestRIDPreviewCallback(self, filename, timestamp, frameWidth, frameData):
        offset = self.handleWidth

        if self.hoverRID == 'V':
            ridfilename = self.controller.getcurrentFilename()
            ridend = self.blocks0
            ridstart = self.blocks1
            offset = 0
        else:
            ridfilename, ridend, ridstart = self.controller.controller.videoManager.getDetailsForRangeId(self.hoverRID)

        timelineHeight = self.height()

        pixmap = pgm_bytes_to_pixmap(frameData)
        if pixmap is None:
            return

        if ridstart == timestamp:
            self.startImg = pixmap
        if ridend == timestamp:
            self.endImg = pixmap

        self.scheduleRepaint()
        print(filename, timestamp, frameWidth)

    def requestRIDHoverPreviews(self, rid, pos='m'):
        if self.globalOptions.get('generateRIDHoverPreviews', False):
            if rid == 'V':
                ridfilename = self.controller.getcurrentFilename()
                ridend = self.blocks0
                ridstart = self.blocks1
            else:
                ridfilename, ridend, ridstart = self.controller.controller.videoManager.getDetailsForRangeId(self.hoverRID)

            timelineHeight = self.height()

            self.controller.requestRIDHoverPreviews(rid,
                                                    (timelineHeight * 2, 50),
                                                    self.requestRIDPreviewCallback,
                                                    start=ridstart,
                                                    end=ridend)

    def timelineMousePress(self, e):

        if e.type in (_FakeTkEventType.ButtonPress, _FakeTkEventType.ButtonRelease):
            self._randRangePreview = (0, 0, 0, 0)
            self.scheduleRepaint()

        if not self.controller.getIsPlaybackStarted():
            self.setCursor(Qt.ForbiddenCursor)
            return

        enableDraggableHint = False
        ranges = self.controller.getRangesForClip(self.controller.getcurrentFilename())

        if e.y < 20:
            enableDraggableHint = True
        elif self.clickTarget is not None:
            enableDraggableHint = True
        elif e.y > self.height() - self.handleHeight:
            for rid, (sts, ens) in ranges:
                st = self.secondsToXcoord(sts)
                en = self.secondsToXcoord(ens)

                if (st < e.x < en and e.y > self.height() - self.midrangeHeight) or \
                   (st - self.handleWidth < e.x < en + self.handleWidth and e.y > self.height() - self.miniMidrangeHeight):
                    enableDraggableHint = True
                elif st - self.handleWidth < e.x < st + 2:
                    enableDraggableHint = True
                elif en - 2 < e.x < en + self.handleWidth:
                    enableDraggableHint = True
                if enableDraggableHint:
                    break

        if enableDraggableHint:
            self.setCursor(Qt.SizeHorCursor)
        else:
            self.setCursor(Qt.CrossCursor)

        ctrl = (e.state & 0x4) != 0

        self.setFocus()

        if e.type in (_FakeTkEventType.ButtonPress,):
            self.lastClickedEndpoint = None
            self.timelineMousePressOffset = 0

            if ctrl and self.tempRangeStart is None:
                ctrl_seconds = self.xCoordToSeconds(e.x)
                self.startTempSelection(startOverride=ctrl_seconds)

        if self.clickTarget is None:
            if e.type == _FakeTkEventType.Motion or e.type == _FakeTkEventType.ButtonRelease:
                if e.y > 20:
                    for rid, (sts, ens) in ranges:
                        st = self.secondsToXcoord(sts)
                        en = self.secondsToXcoord(ens)
                        if (st - self.handleWidth) < e.x < (en + self.handleWidth):
                            self.hoverRID = rid
                            break
                    else:
                        self.hoverRID = None
                else:
                    self.hoverRID = None

            if self.hoverRID != self.previewRID:
                self.previewRID = self.hoverRID
                if not self.previewRIDRequested:
                    self.requestRIDHoverPreviews(self.previewRID)
                    self.previewRIDRequested = True
                if self.hoverRID is None:
                    self.startImg = None
                    self.endImg = None
                    self.previewRIDRequested = False
                    self.scheduleRepaint()

        if e.type in (_FakeTkEventType.ButtonPress, _FakeTkEventType.ButtonRelease):
            self.timeline_mousedownstate[e.num] = e.type == _FakeTkEventType.ButtonPress

            if (e.num == 1 and e.y < 20) or e.num == 2:
                self.rangeHeaderClickStart = self.currentZoomRangeMidpoint - (e.x / self.width())

            elif e.num == 1 and e.y > self.height() - self.handleHeight:
                targetFound = None

                for rid, (sts, ens) in ranges:
                    st = self.secondsToXcoord(sts)
                    en = self.secondsToXcoord(ens)

                    if (st < e.x < en and e.y > self.height() - self.midrangeHeight) or \
                       (st - self.handleWidth < e.x < en + self.handleWidth and e.y > self.height() - self.miniMidrangeHeight):
                        self.tempRangeStart = None
                        self.clickTarget = (rid, 'm', sts, ens)

                        if ((st + en) / 2) > e.x:
                            self.initialShiftStart = 'End'
                        else:
                            self.initialShiftStart = 'Start'

                        self.setUiDirtyFlag(specificRID=rid)
                        self.timelineMousePressOffset = ((st + en) / 2) - e.x
                        self.controller.pause()
                        targetFound = rid
                        break
                    elif st - self.handleWidth < e.x < st + 2:
                        self.tempRangeStart = None
                        self.clickTarget = (rid, 's', sts, ens)
                        self.setUiDirtyFlag(specificRID=rid)
                        self.lastClickedEndpoint = (rid, 's')
                        self.timelineMousePressOffset = st - e.x
                        self.controller.pause()
                        targetFound = rid
                        break
                    elif en - 2 < e.x < en + self.handleWidth:
                        self.tempRangeStart = None
                        self.clickTarget = (rid, 'e', sts, ens)
                        self.setUiDirtyFlag(specificRID=rid)
                        self.lastClickedEndpoint = (rid, 'e')
                        self.timelineMousePressOffset = en - e.x
                        self.controller.pause()
                        targetFound = rid
                        break

                if targetFound is None and self.lastClickedRange is not None:
                    self.setUiDirtyFlag(specificRID=self.lastClickedRange)
                    self.lastClickedRange = None
                elif targetFound is not None and self.lastClickedRange != targetFound:
                    self.setUiDirtyFlag(specificRID=self.lastClickedRange)
                    self.lastClickedRange = targetFound
                    self.setUiDirtyFlag(specificRID=targetFound)

        if e.type in (_FakeTkEventType.ButtonRelease,) and e.num in (1, 2):
            if self.tempRangeStart is not None:
                self.endTempSelection()

            if self.clickTarget is not None:
                rid, pos, os_, oe = self.clickTarget
                if pos == 'e':
                    self.controller.seekTo(os_)

                self.hoverRID = rid
                self.previewRID = self.hoverRID
                if self.hoverRID is not None:
                    self.requestRIDHoverPreviews(self.hoverRID, pos=pos)
                    self.previewRIDRequested = True

            self.clickTarget = None
            self.rangeHeaderClickStart = None
            if self.globalOptions.get('autoResumeAfterSeek', True):
                self.controller.play()

        if self.timeline_mousedownstate.get(2, False):
            if self.rangeHeaderClickStart is not None:
                self.currentZoomRangeMidpoint = (e.x / self.width()) + self.rangeHeaderClickStart
                self.setUiDirtyFlag()

        if self.timeline_mousedownstate.get(1, False):
            if self.clickTarget is None:
                if self.rangeHeaderClickStart is not None:
                    self.currentZoomRangeMidpoint = ((e.x) / self.width()) + self.rangeHeaderClickStart
                    self.setUiDirtyFlag()
                else:
                    seconds = self.xCoordToSeconds(e.x)
                    self.controller.pause()
                    self.seekto(seconds)

                    if e.x > self.width() - 2:
                        self.currentZoomRangeMidpoint += 0.001
                    if e.x < 2:
                        self.currentZoomRangeMidpoint -= 0.001

                    if ctrl and self.tempRangeStart is None:
                        self.startTempSelection()

                    if (not ctrl) and self.tempRangeStart is not None:
                        self.endTempSelection()

            if self.clickTarget is not None:
                rid, pos, os_, oe = self.clickTarget

                targetSeconds = self.xCoordToSeconds(e.x + self.timelineMousePressOffset)
                targetSeconds = self.roundToNearestFrame(targetSeconds)
                self.setUiDirtyFlag(specificRID=rid, withLock=False)
                self.clipped = self.controller.updatePointForClip(self.controller.getcurrentFilename(), rid, pos, targetSeconds)
                self.setUiDirtyFlag(specificRID=rid, withLock=False)
                if pos == 's':
                    self.controller.seekTo(targetSeconds)
                elif pos == 'e':
                    self.controller.seekTo(targetSeconds - 0.001)
                elif pos == 'm':
                    if self.dragPreviewMode == 'abs':
                        dragoffset = self.dragPreviewPos
                    else:
                        dragoffset = (oe - os_) * (self.dragPreviewPos / 100)

                    if self.initialShiftStart == 'End':
                        if ctrl:
                            targetSeconds = targetSeconds + ((oe - os_) / 2)
                            self.controller.seekTo(targetSeconds - dragoffset)
                        else:
                            targetSeconds = targetSeconds - ((oe - os_) / 2)
                            self.controller.seekTo(targetSeconds + dragoffset)
                    else:
                        if ctrl:
                            targetSeconds = targetSeconds - ((oe - os_) / 2)
                            self.controller.seekTo(targetSeconds + dragoffset)
                        else:
                            targetSeconds = targetSeconds + ((oe - os_) / 2)
                            self.controller.seekTo(targetSeconds - dragoffset)

        if e.type == _FakeTkEventType.ButtonPress:
            if e.num == 3:
                self.timeline_canvas_last_right_click_x = e.x
                self.timeline_canvas_last_right_click_range = None

                ranges = self.controller.getRangesForClip(self.controller.getcurrentFilename())
                mid   = self.xCoordToSeconds(self.timeline_canvas_last_right_click_x)
                lower = self.xCoordToSeconds(self.timeline_canvas_last_right_click_x - self.handleWidth)
                upper = self.xCoordToSeconds(self.timeline_canvas_last_right_click_x + self.handleWidth)
                for rid, (st, et) in list(ranges):
                    if st < mid < et:
                        self.timeline_canvas_last_right_click_range = rid
                        break
                    if lower < et < upper or lower < st < upper:
                        self.timeline_canvas_last_right_click_range = rid
                        break

                if self.timeline_canvas_last_right_click_range is None:
                    self._rangePropertiesAction.setEnabled(False)
                    self._similarSoundsAction.setEnabled(False)
                else:
                    self._rangePropertiesAction.setEnabled(True)
                    self._similarSoundsAction.setEnabled(True)

                self._ctx_menu.exec(QCursor.pos())

    def frameResponseCallback(self, filename, timestamp, frameWidth, frameData):
        pixmap = pgm_bytes_to_pixmap(frameData)
        if pixmap is None:
            # Try as numpy array
            try:
                pixmap = numpy_to_pixmap(frameData)
            except Exception:
                pass
        if filename == self.controller.getcurrentFilename():
            self.previewFrames[timestamp] = (frameWidth, pixmap)
            self.scheduleRepaint()

    def requestFrames(self, filename, startTime, Endtime, timelineWidth, frameWidth):
        self.framesRequested = self.controller.requestTimelinePreviewFrames(
            filename, startTime, Endtime, frameWidth, timelineWidth, self.frameResponseCallback)

    def centerTimelineOnCurrentPosition(self):
        seekpc = self.controller.getCurrentPlaybackPosition() / self.controller.getTotalDuration()
        self.currentZoomRangeMidpoint = seekpc
        self.setUiDirtyFlag()

    def updateCanvas(self, withLock=False):

        if withLock:
            updateLock = acquire_timeout(self.uiUpdateLock, 0.1)
        else:
            updateLock = AbstractContextManager()

        with updateLock:
            canvasUpdated = False

            if self.controller.getcurrentFilename() is None or self.controller.getTotalDuration() is None:
                return

            ranges = self.controller.getRangesForClip(self.controller.getcurrentFilename())
            timelineWidth = self.width()
            timelineHeight = self.height()

            startpc = self.xCoordToSeconds(0) / self.controller.getTotalDuration()
            endpc   = self.xCoordToSeconds(timelineWidth) / self.controller.getTotalDuration()

            # Temp range preview
            if self.tempRangeStart is not None:
                a = self.tempRangeStart
                b = self.controller.getCurrentPlaybackPosition()
                d = abs(b - a)
                ax = self.secondsToXcoord(a)
                bx = self.secondsToXcoord(b)
                self._tempRangePreview = (ax, 110, bx, 150)
                self._tempRangeDurationX = (ax + bx) // 2
                self._tempRangeDurationY = 102
                self._tempRangeDurationText = format_timedelta(
                    datetime.timedelta(seconds=d),
                    '{hours_total}:{minutes2}:{seconds:02.2F}')
            else:
                self._tempRangePreview = (0, 0, 0, 0)
                self._tempRangeDurationText = ''

            # Wave/spectrogram
            if self.uiDirty > 0 and self.generateWaveImages:
                if self.audioToBytesThread is None and not self.completedAudioByteDecoded:
                    self.audioToBytesThread = threading.Timer(
                        0.0, self.processFileAudioToBytes,
                        args=(self.controller.controller.getcurrentFilename(), self.controller.getTotalDuration()))
                    self.audioToBytesThreadKill = False
                    self.audioToBytesThread.daemon = True
                    self.audioToBytesThread.start()

                newWaveAsPicRequest = (self.controller.controller.getcurrentFilename(), startpc, endpc,
                                       self.controller.getTotalDuration(), timelineWidth)

                if self.lastWavePicSectionsRequested != newWaveAsPicRequest:
                    if self.wavePicSectionsThread is not None:
                        self.wavePicSectionsThread.cancel()
                        self.wavePicSectionsThread = None
                    self.wavePicSectionsThread = threading.Timer(
                        0.0, self.generateImageSections, args=newWaveAsPicRequest)
                    self.wavePicSectionsThread.daemon = True
                    self.lastWavePicSectionsRequested = newWaveAsPicRequest
                    self.wavePicSectionsThread.start()

            # Preview frames
            for ts, (frameWidth, frameData) in list(self.previewFrames.items()):
                canvasUpdated = True   # frames exist, we'll paint them

            if not self.framesRequested and self.controller.getcurrentFilename() is not None and self.controller.getTotalDuration() is not None:
                self.requestFrames(self.controller.getcurrentFilename(), 0, self.controller.getTotalDuration(), timelineWidth, 90)

            # Range header
            self._rangeHeaderActiveX1 = startpc * timelineWidth
            self._rangeHeaderActiveX2 = endpc * timelineWidth

            seekpc = self.controller.getCurrentPlaybackPosition() / self.controller.getTotalDuration()
            self._rangeHeaderSeekX = seekpc * timelineWidth

            seekMidpc = (startpc + endpc) / 2
            self._rangeHeaderMidX = seekMidpc * timelineWidth

            # Seek pointer
            ticky = 0
            if self.globalOptions.get('generateTimelineThumbnails', True):
                ticky = 45

            currentPlaybackX = self.secondsToXcoord(
                self.roundToNearestFrame(self.controller.getCurrentPlaybackPosition()))
            self._seekPointerX = currentPlaybackX
            self._seekPointerY0 = ticky + 55
            self._seekPointerY1 = timelineHeight
            self._seekTimestampX = currentPlaybackX
            self._seekTimestampY = ticky + 45
            self._seekTimestampText = format_timedelta(
                datetime.timedelta(seconds=round(self.xCoordToSeconds(currentPlaybackX), 2)),
                '{hours_total}:{minutes2}:{seconds:02.2F}')

            # Loop pos
            checkRanges = True
            activeRanges = set()
            for rid, (s, e) in list(ranges):
                if checkRanges and s < self.controller.getCurrentPlaybackPosition() < e:
                    self.controller.setLoopPos(s, e)
                    checkRanges = False
                activeRanges.add(rid)

                if rid in self.dirtySelectionRanges or self.uiDirty > 0 or (rid, 'main') not in self.canvasRegionCache:
                    self.dirtySelectionRanges.add(rid)
                    canvasUpdated = True

                    if (rid, 'main') in self.canvasRegionCache:
                        if self.clipped != rid:
                            try:
                                self.dirtySelectionRanges.remove(rid)
                            except Exception as exc:
                                self.setUiDirtyFlag()
                                print(exc)
                        else:
                            self.clipped = None
                    else:
                        # First time seeing this RID — mark cache entry so we know it exists
                        self.canvasRegionCache[(rid, 'main')] = True

            if self.uiDirty > 0:
                self.decrementUiDirtyFlag()

            # Remove stale RIDs from cache
            for (rid, name) in list(self.canvasRegionCache.keys()):
                if rid not in activeRanges and rid != 'previewFrame':
                    del self.canvasRegionCache[(rid, name)]

            self.scheduleRepaint()

    def setUiDirtyFlag(self, specificRID=None, withLock=True):
        pos = 'm'
        if self.clickTarget is not None:
            _, pos, _, _ = self.clickTarget

        if specificRID is None:
            self.uiDirty = min(max(0, self.uiDirty + 1), 2)
            if pos in 'sm':
                self.endImg = None
            if pos in 'em':
                self.startImg = None
            self.hoverRID = None
            self.previewRIDRequested = False
        else:
            self.dirtySelectionRanges.add(specificRID)
            if self.hoverRID == specificRID:
                if pos in 'sm':
                    self.endImg = None
                if pos in 'em':
                    self.startImg = None
                self.hoverRID = None
                self.previewRIDRequested = False

        self.scheduleRepaint()

    def decrementUiDirtyFlag(self):
        self.uiDirty = min(max(0, self.uiDirty - 1), 2)

    # ---------------------------------------------------------------------- #
    # Context menu callbacks — unchanged logic                                 #
    # ---------------------------------------------------------------------- #

    def canvasPopupAddNewSubClipToInterestMarksCallback(self, setDirtyAfter=True):
        a = 0
        b = 0
        if self.timeline_canvas_last_right_click_x is not None:
            cs = self.xCoordToSeconds(self.timeline_canvas_last_right_click_x)
            a, b = self.controller.getSurroundingInterestMarks(cs)

        newRid = None
        if a != b:
            a, b = self.roundToNearestFrame(a), self.roundToNearestFrame(b)
            newRid = self.controller.addNewSubclip(a, b)

        self.timeline_canvas_last_right_click_x = None
        if setDirtyAfter and newRid is not None:
            self.setUiDirtyFlag(specificRID=newRid)

    def canvasPopupExpandSublcipToInterestMarksCallback(self, setDirtyAfter=True):
        if self.timeline_canvas_last_right_click_x is not None:
            ranges = self.controller.getRangesForClip(self.controller.getcurrentFilename())
            mid   = self.xCoordToSeconds(self.timeline_canvas_last_right_click_x)
            lower = self.xCoordToSeconds(self.timeline_canvas_last_right_click_x - self.handleWidth)
            upper = self.xCoordToSeconds(self.timeline_canvas_last_right_click_x + self.handleWidth)
            for rid, (s, e) in list(ranges):
                if s < mid < e:
                    self.controller.expandSublcipToInterestMarks((e + s) / 2)
                    if setDirtyAfter:
                        self.setUiDirtyFlag(specificRID=rid)
                    break
                if lower < e < upper or lower < s < upper:
                    self.controller.expandSublcipToInterestMarks((e + s) / 2)
                    if setDirtyAfter:
                        self.setUiDirtyFlag(specificRID=rid)
                    break
        self.timeline_canvas_last_right_click_x = None

    def canvasPopupCloneSubClipCallback(self):
        if self.timeline_canvas_last_right_click_x is not None:
            ranges = self.controller.getRangesForClip(self.controller.getcurrentFilename())
            mid   = self.xCoordToSeconds(self.timeline_canvas_last_right_click_x)
            lower = self.xCoordToSeconds(self.timeline_canvas_last_right_click_x - self.handleWidth)
            upper = self.xCoordToSeconds(self.timeline_canvas_last_right_click_x + self.handleWidth)
            for rid, (s, e) in list(ranges):
                if s < mid < e:
                    self.controller.cloneSubclip((e + s) / 2)
                    break
                if lower < e < upper or lower < s < upper:
                    self.controller.cloneSubclip((e + s) / 2)
                    break
        self.timeline_canvas_last_right_click_x = None
        self.setUiDirtyFlag()

    def canvasPopupCopySubClipCallback(self):
        if self.timeline_canvas_last_right_click_x is not None:
            ranges = self.controller.getRangesForClip(self.controller.getcurrentFilename())
            mid   = self.xCoordToSeconds(self.timeline_canvas_last_right_click_x)
            lower = self.xCoordToSeconds(self.timeline_canvas_last_right_click_x - self.handleWidth)
            upper = self.xCoordToSeconds(self.timeline_canvas_last_right_click_x + self.handleWidth)
            for rid, (s, e) in list(ranges):
                if s < mid < e:
                    self.controller.copySubclip((e + s) / 2)
                    break
                if lower < e < upper or lower < s < upper:
                    self.controller.copySubclip((e + s) / 2)
                    break
        self.timeline_canvas_last_right_click_x = None

    def canvasPopupPasteSubClipCallback(self):
        newRid = self.controller.pasteSubclip()
        self.timeline_canvas_last_right_click_x = None
        self.setUiDirtyFlag(specificRID=newRid)

    def canvasPopupRemoveSubClipCallback(self):
        if self.timeline_canvas_last_right_click_x is not None:
            ranges = self.controller.getRangesForClip(self.controller.getcurrentFilename())
            mid   = self.xCoordToSeconds(self.timeline_canvas_last_right_click_x)
            lower = self.xCoordToSeconds(self.timeline_canvas_last_right_click_x - self.handleWidth)
            upper = self.xCoordToSeconds(self.timeline_canvas_last_right_click_x + self.handleWidth)
            for rid, (s, e) in list(ranges):
                if s <= mid <= e:
                    self.controller.removeSubclip((e + s) / 2)
                    break
                if lower <= e <= upper or lower <= s <= upper:
                    self.controller.removeSubclip((e + s) / 2)
                    break
        self.timeline_canvas_last_right_click_x = None

    def canvasPopupAddNewInterestMarkCallback(self):
        if self.timeline_canvas_last_right_click_x is not None:
            self.controller.addNewInterestMark(self.xCoordToSeconds(self.timeline_canvas_last_right_click_x))
            self.setUiDirtyFlag()

    def canvasPopupAddNewSubClipCallback(self, setDirtyAfter=True, defaultSliceDurationOverride=None):
        newRid = None
        if self.timeline_canvas_last_right_click_x is not None:
            if defaultSliceDurationOverride is not None:
                pre = defaultSliceDurationOverride * 0.5
                post = defaultSliceDurationOverride * 0.5
            else:
                pre = self.defaultSliceDuration * 0.5
                post = self.defaultSliceDuration * 0.5

            a = self.xCoordToSeconds(self.timeline_canvas_last_right_click_x) - pre
            b = self.xCoordToSeconds(self.timeline_canvas_last_right_click_x) + post
            a, b = self.roundToNearestFrame(a), self.roundToNearestFrame(b)
            newRid = self.controller.addNewSubclip(a, b)

        self.timeline_canvas_last_right_click_x = None
        if setDirtyAfter:
            self.setUiDirtyFlag(specificRID=newRid)

    def canvasPopupFindLowestError1s(self):
        self.findLowestErrorForBetterLoop(self.globalOptions.get('loopNudgeLimit1', 1.0))

    def canvasPopupFindLowestError2s(self):
        self.findLowestErrorForBetterLoop(self.globalOptions.get('loopNudgeLimit2', 2.0))

    def canvasPopupFindContainingLoop3s(self):
        self.findLoopAroundFrame(self.globalOptions.get('loopSearchLower1', 2), self.globalOptions.get('loopSearchUpper1', 3))

    def canvasPopupFindContainingLoop6s(self):
        self.findLoopAroundFrame(self.globalOptions.get('loopSearchLower2', 3), self.globalOptions.get('loopSearchUpper2', 6))

    def canvasPopupReCenterOnInterFrameDistance(self, pos):
        if self.timeline_canvas_last_right_click_x is not None:
            selectedRange = None
            ranges = self.controller.getRangesForClip(self.controller.getcurrentFilename())
            mid   = self.xCoordToSeconds(self.timeline_canvas_last_right_click_x)
            lower = self.xCoordToSeconds(self.timeline_canvas_last_right_click_x - self.handleWidth)
            upper = self.xCoordToSeconds(self.timeline_canvas_last_right_click_x + self.handleWidth)
            for rid, (s, e) in list(ranges):
                if s < mid < e:
                    selectedRange = rid
                    break
                if lower < e < upper or lower < s < upper:
                    selectedRange = rid
                    break
            if selectedRange is not None:
                self.controller.moveToMaximumInterFrameDistance(rid, pos)
        self.timeline_canvas_last_right_click_x = None

    def findLoopAroundFrame(self, minSeconds, maxSeconds):
        if self.timeline_canvas_last_right_click_x is not None:
            mid = self.xCoordToSeconds(self.timeline_canvas_last_right_click_x)
            self.controller.findLoopAroundFrame(mid, minSeconds, maxSeconds)
        self.timeline_canvas_last_right_click_x = None

    def findLowestErrorForBetterLoop(self, secondsChange):
        if self.timeline_canvas_last_right_click_x is not None:
            selectedRange = None
            ranges = self.controller.getRangesForClip(self.controller.getcurrentFilename())
            mid   = self.xCoordToSeconds(self.timeline_canvas_last_right_click_x)
            lower = self.xCoordToSeconds(self.timeline_canvas_last_right_click_x - self.handleWidth)
            upper = self.xCoordToSeconds(self.timeline_canvas_last_right_click_x + self.handleWidth)
            for rid, (s, e) in list(ranges):
                if s < mid < e:
                    selectedRange = rid
                    break
                if lower < e < upper or lower < s < upper:
                    selectedRange = rid
                    break
            if selectedRange is not None:
                self.controller.findLowestErrorForBetterLoop(rid, secondsChange)
        self.timeline_canvas_last_right_click_x = None

    def setDefaultsliceDuration(self, value):
        self.defaultSliceDuration = value

    def setTargetTrim(self, value):
        self.targetTrim = value

    def canvasPopupRangeProperties(self):
        if self.timeline_canvas_last_right_click_x is not None:
            selectedRange = None
            ranges = self.controller.getRangesForClip(self.controller.getcurrentFilename())
            mid   = self.xCoordToSeconds(self.timeline_canvas_last_right_click_x)
            lower = self.xCoordToSeconds(self.timeline_canvas_last_right_click_x - self.handleWidth)
            upper = self.xCoordToSeconds(self.timeline_canvas_last_right_click_x + self.handleWidth)
            for rid, (s, e) in list(ranges):
                if s < mid < e:
                    selectedRange = rid
                    break
                if lower < e < upper or lower < s < upper:
                    selectedRange = rid
                    break
            if selectedRange is not None:
                self.controller.canvasPopupRangeProperties(rid)
        self.timeline_canvas_last_right_click_x = None

    def canvasPopupSimilarSounds(self):
        if self.timeline_canvas_last_right_click_x is not None:
            selectedRange = None
            ranges = self.controller.getRangesForClip(self.controller.getcurrentFilename())
            mid   = self.xCoordToSeconds(self.timeline_canvas_last_right_click_x)
            lower = self.xCoordToSeconds(self.timeline_canvas_last_right_click_x - self.handleWidth)
            upper = self.xCoordToSeconds(self.timeline_canvas_last_right_click_x + self.handleWidth)
            for rid, (s, e) in list(ranges):
                if s < mid < e:
                    selectedRange = rid
                    break
                if lower < e < upper or lower < s < upper:
                    selectedRange = rid
                    break
            if selectedRange is not None:
                self.controller.canvasPopupSimilarSounds(rid)
        self.timeline_canvas_last_right_click_x = None


# ---------------------------------------------------------------------------
# Helper: fake Tk event type constants
# ---------------------------------------------------------------------------

class _FakeTkEventType:
    ButtonPress   = 'ButtonPress'
    ButtonRelease = 'ButtonRelease'
    Motion        = 'Motion'
    Enter         = 'Enter'
    Leave         = 'Leave'


if __name__ == '__main__':
    import webmGenerator
