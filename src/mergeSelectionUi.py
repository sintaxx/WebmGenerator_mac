"""
MergeSelectionUi — PySide6 rewrite (Phase 4).
Replaces Tkinter ttk.Frame / ScrolledFrame / tkinterdnd2 with QWidget / QScrollArea / QDrag.
All business-logic methods preserved verbatim; only rendering/input backend changed.
"""

import os
import string
import random
import time
import platform
import logging
import threading
import subprocess as sp
from math import floor
from collections import deque

import mpv

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QScrollArea, QDoubleSpinBox,
    QCheckBox, QComboBox, QSizePolicy, QProgressBar,
    QApplication, QFrame, QGroupBox, QFileDialog, QMessageBox,
    QSpinBox,
)
from PySide6.QtCore import Qt, QTimer, QMimeData, QUrl
from PySide6.QtGui import QPixmap, QDrag, QColor

try:
    from .modalWindows import VideoAudioSync, AdvancedEncodeFlagsModal
    from .encoders.specVideoEncoder import SpecVideoEncoder
except ImportError:
    from modalWindows import VideoAudioSync, AdvancedEncodeFlagsModal
    from encoders.specVideoEncoder import SpecVideoEncoder

from pathvalidate import sanitize_filepath


def format_timedelta(value, time_format="{days} days, {hours2}:{minutes2}:{seconds2}"):
    if hasattr(value, 'seconds'):
        seconds = value.seconds + value.days * 24 * 3600
    else:
        seconds = value
    seconds_total = seconds
    minutes = int(floor(seconds / 60))
    minutes_total = minutes
    seconds -= minutes * 60
    seconds = int(seconds)
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
        'seconds': seconds, 'seconds2': str(seconds).zfill(2),
        'minutes': minutes, 'minutes2': str(minutes).zfill(2),
        'hours': hours,     'hours2': str(hours).zfill(2),
        'days': days, 'years': years,
        'seconds_total': seconds_total, 'minutes_total': minutes_total,
        'hours_total': hours_total,     'days_total': days_total,
        'years_total': years_total,
    })


# ---------------------------------------------------------------------------
# EncodeProgress
# ---------------------------------------------------------------------------

class EncodeProgress(QFrame):

    def __init__(self, parent=None, encodeRequestId=None, controller=None,
                 targetSize=0.0, clip=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.StyledPanel)
        self.setFrameShadow(QFrame.Raised)

        self.encodeRequestId = encodeRequestId
        self.cancelled = False
        self.iscomplete = False
        self.controller = controller
        self.clip = clip
        self.rid = clip.rid if clip else None

        self.progresspercent = 0
        self.encodeStartTime = None
        self.progressQueue = deque([], 10)
        self.timestampQueue = deque([], 10)
        self.finalFilename = None
        self.player = None
        self.lastProgress = 0
        self.lastEncodedSize = None
        self.pix_fmt = 8

        layout = QGridLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)

        # Row 0
        self.labelRequestId = QLabel('Request #{}'.format(encodeRequestId))
        layout.addWidget(self.labelRequestId, 0, 0)

        self.labelRequestStatus = QLabel('Idle')
        layout.addWidget(self.labelRequestStatus, 0, 1, 1, 9)

        # Row 1
        self.labelEncodeStage = QLabel('Stage: Submitted Idle')
        layout.addWidget(self.labelEncodeStage, 1, 0)

        self.labelEncodePass = QLabel('Pass: Preparation Cutting Clips')
        layout.addWidget(self.labelEncodePass, 1, 1)

        if targetSize <= 0.0:
            self.labelTargetSize = QLabel('Target Size: -')
        else:
            self.labelTargetSize = QLabel('Target Size: {}M'.format(targetSize))
        layout.addWidget(self.labelTargetSize, 1, 2)

        self.labelLastEncodedSize = QLabel('Size: -')
        layout.addWidget(self.labelLastEncodedSize, 1, 3)

        self.labelLastEncodedBR = QLabel('Bitrate: -')
        layout.addWidget(self.labelLastEncodedBR, 1, 4)

        self.labelLastBuff = QLabel('Buffer: -')
        layout.addWidget(self.labelLastBuff, 1, 5)

        self.labelLastWR = QLabel('Width Change: -')
        layout.addWidget(self.labelLastWR, 1, 6)

        self.labelTimeLeft = QLabel('Idle')
        self.labelTimeLeft.setMinimumWidth(140)
        layout.addWidget(self.labelTimeLeft, 1, 7)

        self.labelLastEncodedPSNR = QLabel('Quality: -')
        layout.addWidget(self.labelLastEncodedPSNR, 1, 8)

        # Row 2
        self.progressBar = QProgressBar()
        self.progressBar.setRange(0, 100)
        self.progressBar.setValue(0)
        layout.addWidget(self.progressBar, 2, 0, 1, 9)

        self.btnCancel = QPushButton('Cancel')
        self.btnCancel.clicked.connect(self.cancelEncodeRequest)
        layout.addWidget(self.btnCancel, 2, 9)

        self.btnPlay = QPushButton('Play')
        self.btnPlay.clicked.connect(self.playFinal)
        self.btnPlay.hide()
        layout.addWidget(self.btnPlay, 2, 9)

        self.btnOpenFolder = QPushButton('Open folder')
        self.btnOpenFolder.clicked.connect(self.openFolder)
        self.btnOpenFolder.hide()
        layout.addWidget(self.btnOpenFolder, 2, 10)

        # Preview thumbnail (col 11, rows 0-2)
        self.previewLabel = QLabel()
        self.previewLabel.setFixedSize(90, 60)
        self.previewLabel.setAlignment(Qt.AlignCenter)
        if clip is not None and hasattr(clip, 'previewImage') and clip.previewImage is not None:
            self.previewLabel.setPixmap(
                clip.previewImage.scaled(90, 60, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
        layout.addWidget(self.previewLabel, 0, 11, 3, 1)

        # Context menu
        self.previewLabel.setContextMenuPolicy(Qt.CustomContextMenu)
        self.previewLabel.customContextMenuRequested.connect(self._showContextMenu)
        self.btnPlay.setContextMenuPolicy(Qt.CustomContextMenu)
        self.btnPlay.customContextMenuRequested.connect(self._showContextMenu)

        # Drag support: drag out the finished file
        self.previewLabel.mousePressEvent = self._dragStart
        self.btnPlay.mousePressEvent = self._dragStart

        # Add to parent layout if parent is a container with a layout
        if parent is not None and parent.layout() is not None:
            parent.layout().addWidget(self)

    # ---- drag-out support ----
    def _dragStart(self, event):
        if event.button() == Qt.LeftButton and self.finalFilename is not None:
            drag = QDrag(self)
            mime = QMimeData()
            mime.setUrls([QUrl.fromLocalFile(os.path.abspath(self.finalFilename))])
            drag.setMimeData(mime)
            drag.exec(Qt.CopyAction)

    def _showContextMenu(self, pos):
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        menu.addAction('Remove', self.remove)
        menu.addAction('Remove and Delete File', self.deleteCompleteAndRemove)
        menu.exec(self.sender().mapToGlobal(pos))

    def deleteCompleteAndRemove(self):
        if self.finalFilename is not None:
            try:
                os.remove(self.finalFilename)
            except Exception as e:
                print(e)
        self.remove()

    def openFolder(self):
        if self.finalFilename is not None:
            path, _ = os.path.split(self.finalFilename)
            if platform.system() == 'Windows':
                try:
                    sp.call('explorer.exe /select,"{}"'.format(self.finalFilename))
                except Exception as e:
                    logging.error('explorer select Exception', exc_info=e)
                    os.startfile(path)
            elif platform.system() == 'Darwin':
                sp.Popen(['open', path])
            else:
                sp.Popen(['xdg-open', path])

    def playFinal(self):
        if self.finalFilename is not None:
            if self.player is not None:
                self.player.terminate()
            self.player = mpv.MPV(loop='inf', mute=True, volume=100, autofit_larger='1280')
            self.player.play(self.finalFilename)

            def mutetoggle(key_state, key_name, key_char):
                if 'd-' in key_state:
                    self.player.mute = not self.player.mute
            self.mutetoggle = mutetoggle
            self.player.register_key_binding('m', mutetoggle)

            def quitFunc(key_state, key_name, key_char):
                def playerReaper():
                    player = self.player
                    self.player = None
                    player.terminate()
                    player.wait_for_shutdown()
                if 'd-' in key_state or 'p-' in key_state:
                    self.playerReaper = threading.Thread(target=playerReaper, daemon=True)
                    self.playerReaper.start()
            self.quitFunc = quitFunc
            self.player.register_key_binding('q', quitFunc)
            self.player.register_key_binding('Q', quitFunc)
            self.player.register_key_binding('CLOSE_WIN', quitFunc)

            def seekPlayer(player, offset):
                player.command('seek', str(5 * offset), 'relative')
            self.player.register_key_binding('WHEEL_UP',   lambda s, n, c, p=self.player, o=1:  seekPlayer(p, o))
            self.player.register_key_binding('WHEEL_DOWN', lambda s, n, c, p=self.player, o=-1: seekPlayer(p, o))

    def cancelEncodeRequest(self):
        self.cancelled = True
        self.progressBar.setStyleSheet('QProgressBar::chunk { background: red; }')
        self.progressBar.setValue(100)
        self.labelTimeLeft.setText('Cancelled')
        self.progresspercent = 100
        self.btnCancel.setEnabled(False)
        self.controller.cancelEncodeRequest(self.encodeRequestId)

    def sizeof_fmt(self, inum, suffix='B'):
        num = float(inum)
        for unit in ['', 'Ki', 'Mi', 'Gi', 'Ti', 'Pi', 'Ei', 'Zi']:
            if abs(num) < 1024.0:
                return '%3.1f%s%s' % (num, unit, suffix)
            num /= 1024.0
        return '%.1f%s%s' % (num, 'Yi', suffix)

    def setPreviewImage(self, pixmap):
        if isinstance(pixmap, QPixmap):
            self.previewLabel.setPixmap(
                pixmap.scaled(90, 60, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )

    def updateStatus(self, status, percent, finalFilename=None, requestStatus=None,
                     encodeStage=None, pix_fmt=None, encodePass=None, lastEncodedBR=None,
                     lastEncodedCRF=None, lastEncodedSize=None, lastEncodedPSNR=None,
                     lastBuff=None, lastWR=None, currentSize=None):

        if self.cancelled:
            return

        if pix_fmt is not None:
            self.pix_fmt = pix_fmt

        if lastEncodedSize is not None:
            self.lastEncodedSize = lastEncodedSize
        if lastEncodedSize is None and self.lastEncodedSize is not None:
            lastEncodedSize = self.lastEncodedSize

        if requestStatus is not None:
            self.labelRequestStatus.setText(str(requestStatus))

        if encodeStage == 'Encode Failed':
            self.progressBar.setStyleSheet('QProgressBar::chunk { background: red; }')
            self.progressBar.setValue(100)
            self.labelTimeLeft.setText('Failed')
            self.progresspercent = 100
            self.btnCancel.setEnabled(False)
            self.cancelled = True

        if encodeStage is not None:
            self.labelEncodeStage.setText('Stage: {}'.format(encodeStage))

        if encodePass is not None:
            self.labelEncodePass.setText('Pass: {}'.format(encodePass))

        if lastEncodedSize is not None and currentSize is not None:
            self.labelLastEncodedSize.setText(
                'Size: {} ~{}'.format(self.sizeof_fmt(lastEncodedSize, 'B'),
                                      self.sizeof_fmt(currentSize, 'B')))
        elif currentSize is not None:
            self.labelLastEncodedSize.setText('Size: ~{}'.format(self.sizeof_fmt(currentSize, 'B')))
        elif lastEncodedSize is not None:
            self.labelLastEncodedSize.setText('Size: {}'.format(self.sizeof_fmt(lastEncodedSize, 'B')))

        if lastEncodedBR is not None:
            self.labelLastEncodedBR.setText('Bitrate: {}'.format(self.sizeof_fmt(lastEncodedBR, 'B')))
        if lastEncodedCRF is not None:
            self.labelLastEncodedBR.setText('CRF: {}'.format(lastEncodedCRF))

        if lastEncodedPSNR is not None:
            psnr = int(lastEncodedPSNR)
            if psnr >= 48:
                grade, color = 'Excellent', '#006400'
            elif psnr >= 40:
                grade, color = 'Good', '#228B22'
            elif psnr >= 38:
                grade, color = 'Fair', '#DAA520'
            elif psnr >= 30:
                grade, color = 'Poor', '#FF8C00'
            else:
                grade, color = 'Terrible', '#8B0000'
            self.labelLastEncodedPSNR.setText('Quality: {} ({})'.format(lastEncodedPSNR, grade))
            self.labelLastEncodedPSNR.setStyleSheet('color: {}'.format(color))

        if lastBuff is not None:
            self.labelLastBuff.setText('Buffer: {}'.format(self.sizeof_fmt(lastBuff, 'B')))
        if lastWR is not None:
            self.labelLastWR.setText('Width Change: {:0.2f}%'.format(lastWR * 100))

        if self.cancelled:
            return

        if finalFilename is not None:
            self.finalFilename = finalFilename
            self.iscomplete = True
            self.controller.registerComplete(self.finalFilename, clip=self.clip)

        if percent is not None:
            if percent < self.lastProgress:
                self.progressQueue = deque([], 10)
                self.timestampQueue = deque([], 10)
            self.lastProgress = percent
            self.progressQueue.append(percent)
            self.timestampQueue.append(time.time())

            if self.encodeStartTime is None:
                self.encodeStartTime = self.timestampQueue[-1]

            if len(self.progressQueue) >= 2:
                currentValue = self.progressQueue[-1]
                oldestValue  = self.progressQueue[0]
                currentKey   = self.timestampQueue[-1]
                oldestKey    = self.timestampQueue[0]
                try:
                    remaining = (1.0 - currentValue) * (currentKey - oldestKey) / (currentValue - oldestValue)
                    self.labelTimeLeft.setText(
                        format_timedelta(remaining, '{hours_total}:{minutes2}:{seconds2}') +
                        (' left ({:.0%})'.format(percent))
                    )
                except Exception as e:
                    logging.error('format_timedelta Exception', exc_info=e)

            if status is not None:
                self.labelRequestStatus.setText(status)
            self.progressBar.setValue(int(percent * 100))
            self.progresspercent = percent * 100

            if percent >= 1:
                elapsed = format_timedelta(time.time() - self.encodeStartTime, '{hours_total}:{minutes2}:{seconds2}')
                self.labelTimeLeft.setText('Complete in {}'.format(elapsed))
                self.btnCancel.hide()
                if self.finalFilename is not None:
                    self.progressBar.setStyleSheet('QProgressBar::chunk { background: #006400; }')
                    self.btnPlay.show()
                    self.btnOpenFolder.show()
            else:
                self.progressBar.setStyleSheet('QProgressBar::chunk { background: #1E90FF; }')
                self.btnCancel.show()

            # Update window title via QApplication
            try:
                top = self.window()
                if top:
                    top.setWindowTitle('webmGenerator: encoding: {:0.2f}%'.format(percent * 100))
            except Exception:
                pass

    def remove(self):
        self.cancelEncodeRequest()
        self.finalFilename = None
        if self.progresspercent == 100:
            self.setParent(None)
            self.deleteLater()


# ---------------------------------------------------------------------------
# SequencedVideoEntry
# ---------------------------------------------------------------------------

class SequencedVideoEntry(QFrame):

    def __init__(self, parent, controller, sourceClip, direction='LEFT_RIGHT'):
        super().__init__(parent)
        self.setFrameShape(QFrame.Box)

        self.sourceClip = sourceClip
        self.rid = sourceClip.rid
        self.s = sourceClip.s
        self.e = sourceClip.e
        self.controller = controller
        self.player = None
        self.muted = False

        self.filename = sourceClip.filename
        self.filterexp = sourceClip.filterexp
        self.filterexpEnc = sourceClip.filterexpEnc
        self.filteraudioexp = sourceClip.filteraudioexp
        self.basename = sourceClip.basename
        self.previewImage = sourceClip.previewImage

        self.queuedPreview = None

        if direction == 'LEFT_RIGHT':
            outer = QVBoxLayout(self)
        else:
            outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 4, 4, 4)
        outer.setSpacing(2)

        self.labelSequenceVideoName = None
        if direction == 'LEFT_RIGHT':
            self.labelSequenceVideoName = QLabel('{:0.2f}-{:0.2f} {:0.2f}s'.format(self.s, self.e, self.e - self.s))
            self.labelSequenceVideoName.setAlignment(Qt.AlignCenter)
            outer.addWidget(self.labelSequenceVideoName)

        # Middle row: ◄ preview ►
        midRow = QHBoxLayout()
        if direction == 'LEFT_RIGHT':
            self.btnBack = QPushButton('◄')
            self.btnBack.setFixedWidth(24)
            self.btnBack.clicked.connect(self.moveBack)
            midRow.addWidget(self.btnBack)

        self.previewLabel = QLabel()
        self.previewLabel.setFixedSize(120, 80)
        self.previewLabel.setAlignment(Qt.AlignCenter)
        if self.previewImage is not None:
            self.previewLabel.setPixmap(
                self.previewImage.scaled(120, 80, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
        midRow.addWidget(self.previewLabel)

        if direction == 'LEFT_RIGHT':
            self.btnFwd = QPushButton('►')
            self.btnFwd.setFixedWidth(24)
            self.btnFwd.clicked.connect(self.moveForwards)
            midRow.addWidget(self.btnFwd)

        outer.addLayout(midRow)

        # Speed
        speedRow = QHBoxLayout()
        speedRow.addWidget(QLabel('Speed'))
        self.spinSpeed = QDoubleSpinBox()
        self.spinSpeed.setRange(0.001, 100.0)
        self.spinSpeed.setSingleStep(0.1)
        self.spinSpeed.setValue(1.0)
        speedRow.addWidget(self.spinSpeed)
        if not controller.globalOptions.get('perClipSpeedAdjustment', False):
            # hide the speed row widget but keep it in layout for later
            speedWidget = QWidget()
            speedWidget.setLayout(speedRow)
            speedWidget.hide()
            outer.addWidget(speedWidget)
        else:
            speedWidget = QWidget()
            speedWidget.setLayout(speedRow)
            outer.addWidget(speedWidget)

        btnRow = QHBoxLayout()
        self.btnPreview = QPushButton('Preview ►')
        self.btnPreview.clicked.connect(self.preview)
        btnRow.addWidget(self.btnPreview)

        self.btnRemove = QPushButton('Remove ✖')
        self.btnRemove.clicked.connect(self.remove)
        btnRow.addWidget(self.btnRemove)
        outer.addLayout(btnRow)

        self.btnFilter = QPushButton('View filter')
        self.btnFilter.clicked.connect(self.viewFilter)
        outer.addWidget(self.btnFilter)

        self.setFixedWidth(170)

    # ---- entrySpeed compat shim ----
    class _SpeedProxy:
        def __init__(self, spin): self._spin = spin
        def get(self):
            try: return float(self._spin.value())
            except: return 1.0

    def getSpeed(self):
        return self.spinSpeed.value()

    def viewFilter(self):
        self.controller.viewFilterForClip(self)

    def preview(self):
        if self.player is not None:
            self.player.terminate()
        self.player = mpv.MPV(loop='inf', mute=True, volume=100, autofit_larger='1280')
        self.player.play(self.filename)
        self.player.ab_loop_a = self.s
        self.player.ab_loop_b = self.e
        self.player.start = self.s
        self.player.time_pos = self.s

        def mutetoggle(key_state, key_name, key_char):
            if 'd-' in key_state:
                self.player.mute = not self.player.mute
        self.mutetoggle = mutetoggle
        self.player.register_key_binding('m', mutetoggle)

        def quitFunc(key_state, key_name, key_char):
            def playerReaper():
                player = self.player
                self.player = None
                player.terminate()
                player.wait_for_shutdown()
            if 'd-' in key_state or 'p-' in key_state:
                self.playerReaper = threading.Thread(target=playerReaper, daemon=True)
                self.playerReaper.start()
        self.quitFunc = quitFunc
        self.player.register_key_binding('q', quitFunc)
        self.player.register_key_binding('Q', quitFunc)
        self.player.register_key_binding('CLOSE_WIN', quitFunc)

        def seekPlayer(player, offset):
            player.command('seek', str(5 * offset), 'relative')
        self.player.register_key_binding('WHEEL_UP',   lambda s, n, c, p=self.player, o=1:  seekPlayer(p, o))
        self.player.register_key_binding('WHEEL_DOWN', lambda s, n, c, p=self.player, o=-1: seekPlayer(p, o))

    def moveForwards(self):
        self.controller.moveSequencedClip(self, 1)

    def moveBack(self):
        self.controller.moveSequencedClip(self, -1)

    def remove(self):
        self.controller.removeSequencedClip(self)

    def setPreviewImage(self, pixmap):
        if isinstance(pixmap, QPixmap):
            self.previewLabel.setPixmap(
                pixmap.scaled(120, 80, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
        self.previewImage = pixmap

    def requestQueuedPreviews(self):
        if self.queuedPreview is not None:
            self.controller.requestPreviewFrame(*self.queuedPreview)
        self.queuedPreview = None

    def getpreviewImg(self):
        return self.sourceClip.previewImage

    def update(self, s, e, filterexp, filteraudioexp, filterexpEnc, requestPreviewFrame=True):
        self.s = s
        self.e = e
        self.filterexp = filterexp
        self.filteraudioexp = filteraudioexp
        self.filterexpEnc = filterexpEnc
        if self.labelSequenceVideoName is not None:
            self.labelSequenceVideoName.setText('{:0.2f}-{:0.2f} {:0.2f}s'.format(self.s, self.e, self.e - self.s))
        if requestPreviewFrame:
            self.controller.requestPreviewFrame(self.rid, self.filename, (self.e + self.s) / 2, self.filterexp)
            self.queuedPreview = None
        else:
            self.queuedPreview = (self.rid, self.filename, (self.e + self.s) / 2, self.filterexp)


# ---------------------------------------------------------------------------
# GridColumn
# ---------------------------------------------------------------------------

class GridColumn(QGroupBox):

    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller
        self.clips = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        self.clipsWidget = QWidget()
        self.clipsLayout = QVBoxLayout(self.clipsWidget)
        self.clipsLayout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.clipsWidget)

        btnRow = QHBoxLayout()
        self.btnSelect = QPushButton('Select ✔')
        self.btnSelect.clicked.connect(self.selectColumn)
        btnRow.addWidget(self.btnSelect)

        self.btnRemove = QPushButton('Remove ✖')
        self.btnRemove.clicked.connect(self.removeColumn)
        btnRow.addWidget(self.btnRemove)

        layout.addLayout(btnRow)

    def setSelected(self, isSelected):
        if isSelected:
            self.setTitle('Selected')
            self.btnSelect.setText('Selected ✔')
        else:
            self.setTitle('')
            self.btnSelect.setText('Select ✔')

    def selectColumn(self):
        self.controller.selectColumn(self)

    def removeColumn(self):
        self.controller.removeColumn(self)


# ---------------------------------------------------------------------------
# SelectableVideoEntry
# ---------------------------------------------------------------------------

class SelectableVideoEntry(QFrame):

    def __init__(self, parent, controller, filename, rid, s, e,
                 filterexp, filteraudioexp, filterexpEnc):
        super().__init__(parent)
        self.setFrameShape(QFrame.Box)

        self.rid = rid
        self.s = s
        self.e = e
        self.controller = controller
        self.filename = filename
        self.filterexp = filterexp
        self.filteraudioexp = filteraudioexp
        self.filterexpEnc = filterexpEnc
        self.basename = os.path.basename(filename)[:14]
        self.player = None
        self.queuedPreview = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)

        self.labelName = QLabel('#{} {:0.2f}-{:0.2f} {:0.2f}s'.format(rid, s, e, e - s))
        self.labelName.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.labelName)

        self.previewLabel = QLabel()
        self.previewLabel.setFixedSize(120, 80)
        self.previewLabel.setAlignment(Qt.AlignCenter)
        self.previewImage = None
        self._loadDefaultPreview()
        layout.addWidget(self.previewLabel)

        self.btnPreview = QPushButton('preview ►')
        self.btnPreview.clicked.connect(self.preview)
        layout.addWidget(self.btnPreview)

        self.btnAdd = QPushButton('Add to Sequence ▼')
        self.btnAdd.clicked.connect(self.addClipToSequence)
        layout.addWidget(self.btnAdd)

        self.setFixedWidth(150)

        # Request preview frame
        if controller.syncModal is not None and controller.syncModal.isActive:
            self.queuedPreview = (rid, filename, (e + s) / 2, filterexp)
        else:
            controller.requestPreviewFrame(rid, filename, (e + s) / 2, filterexp)

        # Drag support: drag out clip name
        self.previewLabel.mousePressEvent = self._dragStart

        # Add to parent layout
        if parent is not None and parent.layout() is not None:
            parent.layout().addWidget(self)

    def _loadDefaultPreview(self):
        try:
            pix = QPixmap(os.path.join('resources', 'cutPreview.png'))
            if not pix.isNull():
                self.previewLabel.setPixmap(pix.scaled(120, 80, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                return
        except Exception:
            pass
        self.previewLabel.setText('No preview')

    def _dragStart(self, event):
        if event.button() == Qt.LeftButton:
            drag = QDrag(self)
            mime = QMimeData()
            name = os.path.basename(self.filename).rpartition('.')[0]
            mime.setText(name)
            drag.setMimeData(mime)
            drag.exec(Qt.CopyAction)

    def setPreviewImage(self, pixmap):
        if isinstance(pixmap, QPixmap):
            self.previewLabel.setPixmap(
                pixmap.scaled(120, 80, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
        self.previewImage = pixmap

    def requestQueuedPreviews(self):
        if self.queuedPreview is not None:
            self.controller.requestPreviewFrame(*self.queuedPreview)
        self.queuedPreview = None

    def update(self, s, e, filterexp, filteraudioexp, filterexpEnc, requestPreviewFrame=True):
        self.s = s
        self.e = e
        self.filterexp = filterexp
        self.filteraudioexp = filteraudioexp
        self.filterexpEnc = filterexpEnc
        self.labelName.setText('{:0.2f}-{:0.2f} {:0.2f}s'.format(self.s, self.e, self.e - self.s))
        if requestPreviewFrame:
            self.controller.requestPreviewFrame(self.rid, self.filename, (self.e + self.s) / 2, self.filterexp)
            self.queuedPreview = None
        else:
            self.queuedPreview = (self.rid, self.filename, (self.e + self.s) / 2, self.filterexp)

    def addClipToSequence(self):
        self.controller.addClipToSequence(self)

    def preview(self):
        if self.player is not None:
            self.player.terminate()
        self.player = mpv.MPV(loop='inf', mute=True, volume=100, autofit_larger='1280')
        self.player.play(self.filename)
        self.player.ab_loop_a = self.s
        self.player.ab_loop_b = self.e
        self.player.start = self.s
        self.player.time_pos = self.s

        def mutetoggle(key_state, key_name, key_char):
            if 'd-' in key_state:
                self.player.mute = not self.player.mute
        self.mutetoggle = mutetoggle
        self.player.register_key_binding('m', mutetoggle)

        def quitFunc(key_state, key_name, key_char):
            def playerReaper():
                player = self.player
                self.player = None
                player.terminate()
                player.wait_for_shutdown()
            if 'd-' in key_state or 'p-' in key_state:
                self.playerReaper = threading.Thread(target=playerReaper, daemon=True)
                self.playerReaper.start()
        self.quitFunc = quitFunc
        self.player.register_key_binding('q', quitFunc)
        self.player.register_key_binding('Q', quitFunc)
        self.player.register_key_binding('CLOSE_WIN', quitFunc)

        def seekPlayer(player, offset):
            player.command('seek', str(5 * offset), 'relative')
        self.player.register_key_binding('WHEEL_UP',   lambda s, n, c, p=self.player, o=1:  seekPlayer(p, o))
        self.player.register_key_binding('WHEEL_DOWN', lambda s, n, c, p=self.player, o=-1: seekPlayer(p, o))


# ---------------------------------------------------------------------------
# MergeSelectionUi
# ---------------------------------------------------------------------------

class MergeSelectionUi(QWidget):

    def __init__(self, master=None, defaultProfile='None', globalOptions=None, *args, **kwargs):
        super().__init__()

        self.controller = None
        self.defaultProfile = defaultProfile
        self.globalOptions = globalOptions or {}
        self.advancedFlags = {'forceGifFPS': True}

        self.sequencedClips = []
        self.gridColumns = []
        self.selectableVideos = {}
        self.selectedColumn = None
        self.player = None
        self.syncModal = None
        self.encodeRequestId = 0
        self.encoderProgress = []

        # Encode settings value holders (replaces StringVar/BooleanVar)
        self.automaticFileNamingValue = True
        self.interpolateSpeedChangeValue = False
        self.loopStartAndendValue = True
        self.filenamePrefixValue = ''
        self.outputFormatValue = ''
        self.frameSizeStrategyValue = ''
        self.maximumSizeValue = 0.0
        self.initialbitrateValue = 2000.0 * 1024
        self.maxbitrateValue = 6000.0 * 1024
        self.maximumWidthValue = 1280
        self.transDurationValue = 0.0
        self.transStyleValue = 'fade'
        self.speedAdjustmentValue = 1.0
        self.audioRate = '64'
        self.audioChannels = 'Mono'
        self.audioMerge = 'Merge Normalize All'
        self.postProcessingFilter = 'None'
        self.gridLoopMergeOption = 'End on shortest Clip'
        self.gridPadColour = 'Black'
        self.gridPadWidth = 0
        self.minimumPSNR = '0.0'
        self.optimizer = 'Linear Search'
        self.audiOverrideBiasValue = 1.0
        self.audiOverrideDelayValue = '0'
        self.audioOverrideValue = None

        self._buildUi()

    # -----------------------------------------------------------------------
    # UI construction
    # -----------------------------------------------------------------------

    def _buildUi(self):
        mainLayout = QVBoxLayout(self)
        mainLayout.setContentsMargins(0, 0, 0, 0)

        # Outer scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        mainLayout.addWidget(scroll)

        inner = QWidget()
        scroll.setWidget(inner)
        self.innerLayout = QVBoxLayout(inner)
        self.innerLayout.setContentsMargins(4, 4, 4, 4)
        self.innerLayout.setSpacing(4)

        self._buildMergeStyleRow()
        self._buildProfileRow()
        self._buildAvailableCutsSection()
        self._buildAddAllClipsRow()
        self._buildOutputPlanSection()

    def _buildMergeStyleRow(self):
        row = QHBoxLayout()
        row.addWidget(QLabel('Merge Style'))
        self.mergeStyles = [
            'Individual Files - Output each individual subclip as a separate file.',
            'Sequence - Join the subclips into a sequence.',
            'Grid - Pack videos into variably sized grid layouts.',
            'Stream Copy - Ignore all filters and percorm no conversions, just stream cut and join the clips.',
            'Full Source Reencode - Ignore all filters, timestamps, make no temporary files, just re-encode the full source.',
            'Clip Reencode - Ignore all filters, make no temporary files, just re-encode the full source segements.',
        ]
        self.comboMergeStyle = QComboBox()
        self.comboMergeStyle.addItems(self.mergeStyles)
        self.comboMergeStyle.currentIndexChanged.connect(lambda _: self.mergeStyleChanged())
        row.addWidget(self.comboMergeStyle, 1)
        w = QWidget(); w.setLayout(row)
        self.innerLayout.addWidget(w)

    def _buildProfileRow(self):
        row = QHBoxLayout()
        row.addWidget(QLabel('Profile'))
        self.profileSpecs = [
            {'name': 'None', 'editable': False},
            {'name': 'Default max quality mp4', 'editable': False, 'outputFormat': 'mp4:x264', 'maximumSize': '0.0'},
            {'name': 'Sub 4M max quality vp8 webm', 'editable': False, 'outputFormat': 'webm:VP8', 'maximumSize': '4.0'},
            {'name': 'Sub 100M max quality mp4', 'editable': False, 'outputFormat': 'mp4:x264', 'maximumSize': '100.0'},
        ]
        self.profiles = [x['name'] for x in self.profileSpecs]
        self.comboProfile = QComboBox()
        self.comboProfile.addItems(self.profiles)
        if self.defaultProfile in self.profiles:
            self.comboProfile.setCurrentText(self.defaultProfile)
        self.comboProfile.currentTextChanged.connect(self.profileChanged)
        row.addWidget(self.comboProfile, 1)

        self.btnSaveProfile = QPushButton('Save New Profile')
        self.btnSaveProfile.clicked.connect(self.saveProfile)
        row.addWidget(self.btnSaveProfile)

        self.btnDeleteProfile = QPushButton('Delete Profile')
        self.btnDeleteProfile.clicked.connect(self.deleteProfile)
        self.btnDeleteProfile.setEnabled(False)
        row.addWidget(self.btnDeleteProfile)

        w = QWidget(); w.setLayout(row)
        self.innerLayout.addWidget(w)

    def _buildAvailableCutsSection(self):
        box = QGroupBox('Available Cuts')
        boxLayout = QVBoxLayout(box)

        self.selectableScrollArea = QScrollArea()
        self.selectableScrollArea.setWidgetResizable(True)
        self.selectableScrollArea.setFixedHeight(200)
        self.selectableScrollArea.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.selectableScrollArea.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.selectableContainer = QWidget()
        self.selectableContainerLayout = QHBoxLayout(self.selectableContainer)
        self.selectableContainerLayout.setContentsMargins(4, 4, 4, 4)
        self.selectableContainerLayout.setSpacing(4)
        self.selectableContainerLayout.addStretch(1)

        self.selectableScrollArea.setWidget(self.selectableContainer)
        boxLayout.addWidget(self.selectableScrollArea)
        self.innerLayout.addWidget(box)

    def _buildAddAllClipsRow(self):
        row = QHBoxLayout()
        btnAll = QPushButton('▼ Add all clips in timeline order ▼')
        btnAll.clicked.connect(self.addAllClipsInTimelineOrder)
        row.addWidget(btnAll, 1)

        btnRid = QPushButton('Add all clips in Creation order')
        btnRid.clicked.connect(self.addAllClipsInRIDOrder)
        row.addWidget(btnRid)

        btnRand = QPushButton('Add all clips in random order')
        btnRand.clicked.connect(self.addAllClipsInRandomOrder)
        row.addWidget(btnRand)

        btnSmart = QPushButton('Add all clips in non-sequential order')
        btnSmart.clicked.connect(self.addAllClipsInSmartRandomOrder)
        row.addWidget(btnSmart)

        btnInter = QPushButton('Add all clips interspersed')
        btnInter.clicked.connect(self.addAllClipsInInterspersedOrder)
        row.addWidget(btnInter)

        w = QWidget(); w.setLayout(row)
        self.innerLayout.addWidget(w)

    def _buildOutputPlanSection(self):
        self.outputPlanBox = QGroupBox('Output Plan')
        planLayout = QVBoxLayout(self.outputPlanBox)

        # Sequence container (horizontal scroll)
        self.sequenceScrollArea = QScrollArea()
        self.sequenceScrollArea.setWidgetResizable(True)
        self.sequenceScrollArea.setFixedHeight(280)
        self.sequenceScrollArea.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.sequenceScrollArea.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.sequenceContainer = QWidget()
        self.sequenceContainerLayout = QHBoxLayout(self.sequenceContainer)
        self.sequenceContainerLayout.setContentsMargins(4, 4, 4, 4)
        self.sequenceContainerLayout.setSpacing(4)
        self.sequenceContainerLayout.addStretch(1)
        self.sequenceScrollArea.setWidget(self.sequenceContainer)
        planLayout.addWidget(self.sequenceScrollArea)

        # Grid container
        self.gridWidget = QWidget()
        self.gridWidgetLayout = QVBoxLayout(self.gridWidget)
        self.gridWidgetLayout.setContentsMargins(0, 0, 0, 0)

        self.gridColumnContainer = QWidget()
        self.gridColumnLayout = QHBoxLayout(self.gridColumnContainer)
        self.gridColumnLayout.setContentsMargins(0, 0, 0, 0)
        self.gridColumnLayout.addStretch(1)
        self.gridWidgetLayout.addWidget(self.gridColumnContainer)

        self.btnAddColumn = QPushButton('Add Column ⇅')
        self.btnAddColumn.clicked.connect(self.addColumn)
        self.gridWidgetLayout.addWidget(self.btnAddColumn)

        self.gridWidget.hide()
        planLayout.addWidget(self.gridWidget)

        # Summary label
        self.labelSequenceSummary = QLabel('Number of Subclips: 0 Total subclip duration 0s Output Duration 0s')
        self.labelSequenceSummary.setAlignment(Qt.AlignCenter)
        planLayout.addWidget(self.labelSequenceSummary)

        # Encode settings area
        self._buildEncodeSettings(planLayout)

        # Encode progress area
        self.encodeProgressContainer = QWidget()
        self.encodeProgressLayout = QVBoxLayout(self.encodeProgressContainer)
        self.encodeProgressLayout.setContentsMargins(0, 0, 0, 0)
        planLayout.addWidget(self.encodeProgressContainer)

        self.innerLayout.addWidget(self.outputPlanBox)

    def _buildEncodeSettings(self, parentLayout):
        settingsBox = QFrame()
        settingsBox.setFrameShape(QFrame.StyledPanel)
        outerH = QHBoxLayout(settingsBox)

        # Left: values grid
        valGrid = QGridLayout()
        valGrid.setHorizontalSpacing(8)
        valGrid.setVerticalSpacing(4)

        # --- Output format ---
        valGrid.addWidget(QLabel('Output format'), 0, 0, Qt.AlignRight)
        self.outputFormats = [
            'mp4:x264', 'mp4:x264_Nvenc', 'mp4:H265_Nvenc',
            'mp4:H264_VideoToolbox', 'mp4:H265_VideoToolbox', 'mp4:AV1',
            'webm:VP8', 'webm:VP9',
        ]
        self.customEncoderspecs = {}
        customEncoderDir = 'customEnoderSpecs'
        if os.path.exists(customEncoderDir):
            for fn in os.listdir(customEncoderDir):
                try:
                    p = os.path.join(customEncoderDir, fn)
                    spec = SpecVideoEncoder(p)
                    if spec.validate():
                        self.outputFormats.append(spec.getDisplayName())
                        self.customEncoderspecs[spec.getDisplayName()] = spec
                except Exception as e:
                    logging.error('customEncoderspecs Exception', exc_info=e)
        self.outputFormats += ['gif', 'gifski', 'apng']
        self.availableOutputFormats = list(self.outputFormats)

        self.comboOutputFormat = QComboBox()
        self.comboOutputFormat.addItems(self.outputFormats)
        self.comboOutputFormat.setToolTip('The output format of the rendered video.')
        self.comboOutputFormat.currentTextChanged.connect(self.valueChange)
        valGrid.addWidget(self.comboOutputFormat, 0, 1)

        # --- Initial bitrate ---
        valGrid.addWidget(QLabel('Initial Bitrate estimate (KB/s)'), 1, 0, Qt.AlignRight)
        self.spinInitialBitrate = QDoubleSpinBox()
        self.spinInitialBitrate.setRange(0, 1e9)
        self.spinInitialBitrate.setSingleStep(100)
        self.spinInitialBitrate.setValue(2000.0)
        self.spinInitialBitrate.setToolTip('Initial bitrate guess.')
        self.spinInitialBitrate.valueChanged.connect(self.valueChange)
        valGrid.addWidget(self.spinInitialBitrate, 1, 1)

        # --- Max bitrate ---
        valGrid.addWidget(QLabel('Bitrate Cap (KB/s)'), 2, 0, Qt.AlignRight)
        self.spinMaxBitrate = QDoubleSpinBox()
        self.spinMaxBitrate.setRange(0, 1e9)
        self.spinMaxBitrate.setSingleStep(100)
        self.spinMaxBitrate.setValue(6000.0)
        self.spinMaxBitrate.setToolTip('Maximum bitrate.')
        self.spinMaxBitrate.valueChanged.connect(self.valueChange)
        valGrid.addWidget(self.spinMaxBitrate, 2, 1)

        # --- Max size ---
        valGrid.addWidget(QLabel('Maximum File Size (MB)'), 3, 0, Qt.AlignRight)
        self.spinMaxSize = QDoubleSpinBox()
        self.spinMaxSize.setRange(0, 1e9)
        self.spinMaxSize.setSingleStep(0.1)
        self.spinMaxSize.setValue(0.0)
        self.spinMaxSize.setToolTip('Max file size in MB, 0 = no limit.')
        self.spinMaxSize.valueChanged.connect(self.valueChange)
        valGrid.addWidget(self.spinMaxSize, 3, 1)

        # --- Audio channels ---
        valGrid.addWidget(QLabel('Audio Channels'), 4, 0, Qt.AlignRight)
        audioRow = QHBoxLayout()
        self.audioChannelsOptions = ['Stereo', 'Mono', 'No audio']
        self.comboAudioChannels = QComboBox()
        self.comboAudioChannels.addItems(self.audioChannelsOptions)
        self.comboAudioChannels.setCurrentText('Mono')
        self.comboAudioChannels.currentTextChanged.connect(self.valueChange)
        audioRow.addWidget(self.comboAudioChannels)
        audioRow.addWidget(QLabel('@ Bitrate (KB/s)'))
        self.spinAudioRate = QDoubleSpinBox()
        self.spinAudioRate.setRange(5, 510)
        self.spinAudioRate.setSingleStep(1)
        self.spinAudioRate.setValue(64)
        self.spinAudioRate.valueChanged.connect(self.valueChange)
        audioRow.addWidget(self.spinAudioRate)
        aw = QWidget(); aw.setLayout(audioRow)
        valGrid.addWidget(aw, 4, 1)

        # --- Min PSNR ---
        valGrid.addWidget(QLabel('Minimum PSNR'), 5, 0, Qt.AlignRight)
        self.spinMinPSNR = QDoubleSpinBox()
        self.spinMinPSNR.setRange(0, 48)
        self.spinMinPSNR.setSingleStep(1)
        self.spinMinPSNR.setValue(0)
        self.spinMinPSNR.setToolTip('Minimum acceptable quality, 0 = ignore.')
        self.spinMinPSNR.valueChanged.connect(self.valueChange)
        valGrid.addWidget(self.spinMinPSNR, 5, 1)

        # --- Audio dub ---
        valGrid.addWidget(QLabel('Audio Dub'), 6, 0, Qt.AlignRight)
        self.btnAudioOverride = QPushButton('None')
        self.btnAudioOverride.clicked.connect(self.selectAudioOverride)
        self.btnAudioOverride.setToolTip('MP3/WAV file to replace original audio.')
        valGrid.addWidget(self.btnAudioOverride, 6, 1)

        # --- Dub mix bias ---
        valGrid.addWidget(QLabel('Dub Mix Bias'), 7, 0, Qt.AlignRight)
        self.spinAudioBias = QDoubleSpinBox()
        self.spinAudioBias.setRange(0, 1)
        self.spinAudioBias.setSingleStep(0.05)
        self.spinAudioBias.setValue(1.0)
        self.spinAudioBias.setToolTip('1 = all dub, 0 = all original.')
        self.spinAudioBias.valueChanged.connect(self.valueChange)
        valGrid.addWidget(self.spinAudioBias, 7, 1)

        # Right column
        # --- Filename ---
        valGrid.addWidget(QLabel('Output filename prefix'), 0, 2, Qt.AlignRight)
        filenameRow = QHBoxLayout()
        from PySide6.QtWidgets import QLineEdit
        self.entryFilenamePrefix = QLineEdit()
        self.entryFilenamePrefix.textChanged.connect(self.valueChange)
        filenameRow.addWidget(self.entryFilenamePrefix, 1)
        self.checkAutoName = QCheckBox('Auto-name')
        self.checkAutoName.setChecked(True)
        self.checkAutoName.stateChanged.connect(self.valueChange)
        filenameRow.addWidget(self.checkAutoName)
        fnw = QWidget(); fnw.setLayout(filenameRow)
        valGrid.addWidget(fnw, 0, 3)

        # --- Frame size strategy ---
        valGrid.addWidget(QLabel('Size Match Strategy'), 1, 2, Qt.AlignRight)
        self.frameSizeStrategies = [
            'Rescale to largest with black bars',
            'Rescale to largest and center crop smaller',
        ]
        self.comboSizeStrategy = QComboBox()
        self.comboSizeStrategy.addItems(self.frameSizeStrategies)
        self.comboSizeStrategy.currentTextChanged.connect(self.valueChange)
        valGrid.addWidget(self.comboSizeStrategy, 1, 3)

        # --- Max width ---
        valGrid.addWidget(QLabel('Limit largest dimension'), 2, 2, Qt.AlignRight)
        self.defaultMaxWidthOptions = [
            '3840 - 4K', '2560 - QHD', '2048 - 2K', '1920 - Full HD',
            '1600 - HD+', '1440 - Quad HD', '1280 - 720p', '1024 - XGA',
            '960 - qHD', '854 - 480p', '720 - NTSC', '640 - nHD', '480 - SD',
        ]
        self.comboMaxWidth = QComboBox()
        self.comboMaxWidth.setEditable(True)
        self.comboMaxWidth.addItems(self.defaultMaxWidthOptions)
        self.comboMaxWidth.setCurrentText('1280 - 720p')
        self.comboMaxWidth.currentTextChanged.connect(self.valueChange)
        valGrid.addWidget(self.comboMaxWidth, 2, 3)

        # --- Speed adjustment ---
        valGrid.addWidget(QLabel('Speed adjustment'), 3, 2, Qt.AlignRight)
        speedRow = QHBoxLayout()
        self.spinSpeedAdjust = QDoubleSpinBox()
        self.spinSpeedAdjust.setRange(0.001, 1000)
        self.spinSpeedAdjust.setSingleStep(0.01)
        self.spinSpeedAdjust.setValue(1.0)
        self.spinSpeedAdjust.valueChanged.connect(self.valueChange)
        speedRow.addWidget(self.spinSpeedAdjust, 1)
        self.checkInterpolate = QCheckBox('Interpolate')
        self.checkInterpolate.stateChanged.connect(self.valueChange)
        speedRow.addWidget(self.checkInterpolate)
        sw = QWidget(); sw.setLayout(speedRow)
        valGrid.addWidget(sw, 3, 3)

        # --- Optimizer ---
        valGrid.addWidget(QLabel('Optimiser'), 4, 2, Qt.AlignRight)
        self.optimziers = ['Linear Search', 'Nelder-Mead - Early Exit', 'Nelder-Mead - Exhaustive']
        self.comboOptimizer = QComboBox()
        self.comboOptimizer.addItems(self.optimziers)
        self.comboOptimizer.currentTextChanged.connect(self.valueChange)
        valGrid.addWidget(self.comboOptimizer, 4, 3)

        # --- Dub delay ---
        valGrid.addWidget(QLabel('Dub Delay (seconds)'), 5, 2, Qt.AlignRight)
        self.spinAudioDelay = QDoubleSpinBox()
        self.spinAudioDelay.setRange(-9999, 9999)
        self.spinAudioDelay.setSingleStep(0.5)
        self.spinAudioDelay.setValue(0)
        self.spinAudioDelay.valueChanged.connect(self.valueChange)
        valGrid.addWidget(self.spinAudioDelay, 5, 3)

        # --- Post filter ---
        valGrid.addWidget(QLabel('Post filter'), 6, 2, Qt.AlignRight)
        self.postProcessingFilterOptions = ['None', 'Disable all filters']
        if os.path.exists('postFilters'):
            for f in os.listdir('postFilters'):
                if f.upper().endswith('TXT') and f.upper().startswith('POSTFILTER-'):
                    self.postProcessingFilterOptions.append(f)
        self.comboPostFilter = QComboBox()
        self.comboPostFilter.addItems(self.postProcessingFilterOptions)
        defaultPostFilter = 'None'
        for fe in self.postProcessingFilterOptions:
            if 'DEFAULT' in fe.upper():
                defaultPostFilter = fe
                break
        self.comboPostFilter.setCurrentText(defaultPostFilter)
        self.comboPostFilter.currentTextChanged.connect(self.valueChange)
        valGrid.addWidget(self.comboPostFilter, 6, 3)

        # --- Advanced options ---
        self.btnAdvancedOptions = QPushButton('Advanced Encode Options')
        self.btnAdvancedOptions.clicked.connect(self.selectAdvancedOptions)
        valGrid.addWidget(self.btnAdvancedOptions, 7, 3)

        outerH.addLayout(valGrid, 1)

        # --- Transition settings (shown for Sequence mode) ---
        self.frameTransitionSettings = QGroupBox('Transitions')
        transLayout = QVBoxLayout(self.frameTransitionSettings)

        transRow = QHBoxLayout()
        transRow.addWidget(QLabel('Transition Duration'))
        self.spinTransDuration = QDoubleSpinBox()
        self.spinTransDuration.setRange(0, 9999)
        self.spinTransDuration.setSingleStep(0.1)
        self.spinTransDuration.setValue(0.0)
        self.spinTransDuration.valueChanged.connect(self.valueChange)
        transRow.addWidget(self.spinTransDuration, 1)
        transLayout.addLayout(transRow)

        transStyleRow = QHBoxLayout()
        transStyleRow.addWidget(QLabel('Transition Style'))
        self.transStyles = [
            'circleclose', 'circlecrop', 'circleopen', 'diagbl', 'diagbr', 'diagtl', 'diagtr',
            'dissolve', 'distance', 'fade', 'fadeblack', 'fadegrays', 'fadewhite', 'hblur',
            'hlslice', 'horzclose', 'horzopen', 'hrslice', 'pixelize', 'radial', 'rectcrop',
            'slidedown', 'slideleft', 'slideright', 'slideup', 'smoothdown', 'smoothleft',
            'smoothright', 'smoothup', 'squeezeh', 'squeezev', 'vdslice', 'vertclose',
            'vertopen', 'vuslice', 'wipebl', 'wipebr', 'wipedown', 'wipeleft', 'wiperight',
            'wipetl', 'wipetr', 'wipeup', 'zoomin',
            'circleopen, circleclose', 'fadewhite, fadeblack', 'slideleft, slideright',
            'smoothdown, smoothup', 'smoothleft, smoothright',
            'wipetl, wipetr, wipebl, wipebr',
        ]
        self.comboTransStyle = QComboBox()
        self.comboTransStyle.setEditable(True)
        self.comboTransStyle.addItems(self.transStyles)
        self.comboTransStyle.setCurrentText('fade')
        self.comboTransStyle.currentTextChanged.connect(self.valueChange)
        transStyleRow.addWidget(self.comboTransStyle, 1)
        transLayout.addLayout(transStyleRow)

        self.checkLoopStartEnd = QCheckBox('Loop start to end')
        self.checkLoopStartEnd.setChecked(True)
        self.checkLoopStartEnd.stateChanged.connect(self.valueChange)
        transLayout.addWidget(self.checkLoopStartEnd)

        self.btnPreviewSequence = QPushButton('Preview sequence timings')
        self.btnPreviewSequence.clicked.connect(self.previewSequencetimings)
        transLayout.addWidget(self.btnPreviewSequence)

        self.frameTransitionSettings.hide()
        outerH.addWidget(self.frameTransitionSettings)

        # --- Grid settings (shown for Grid mode) ---
        self.frameGridSettings = QGroupBox('Grid Options')
        gridOptLayout = QVBoxLayout(self.frameGridSettings)

        audioMergeRow = QHBoxLayout()
        audioMergeRow.addWidget(QLabel('Grid Audio Merge'))
        self.audioMergeOptions = ['Merge Normalize All', 'Merge Original Volume', 'Selected Column Only', 'Largest Cell by Area', 'Adaptive Loudest Cell']
        self.comboAudioMerge = QComboBox()
        self.comboAudioMerge.addItems(self.audioMergeOptions)
        self.comboAudioMerge.currentTextChanged.connect(self.valueChange)
        audioMergeRow.addWidget(self.comboAudioMerge, 1)
        gridOptLayout.addLayout(audioMergeRow)

        loopRow = QHBoxLayout()
        loopRow.addWidget(QLabel('Grid Loop Option'))
        self.gridLoopMergeOptions = ['End on shortest Clip', 'Loop shorter clips to match longest']
        self.comboGridLoop = QComboBox()
        self.comboGridLoop.addItems(self.gridLoopMergeOptions)
        self.comboGridLoop.currentTextChanged.connect(self.valueChange)
        loopRow.addWidget(self.comboGridLoop, 1)
        gridOptLayout.addLayout(loopRow)

        padColRow = QHBoxLayout()
        padColRow.addWidget(QLabel('Grid Pad Colour'))
        self.gridPadColourOptions = ['Black', 'White', 'DeepPink', 'MintCream', 'DarkGray']
        self.comboPadColour = QComboBox()
        self.comboPadColour.addItems(self.gridPadColourOptions)
        self.comboPadColour.currentTextChanged.connect(self.valueChange)
        padColRow.addWidget(self.comboPadColour, 1)
        gridOptLayout.addLayout(padColRow)

        padWRow = QHBoxLayout()
        padWRow.addWidget(QLabel('Grid Pad Width'))
        self.spinGridPadWidth = QSpinBox()
        self.spinGridPadWidth.setRange(0, 9999)
        self.spinGridPadWidth.valueChanged.connect(self.valueChange)
        padWRow.addWidget(self.spinGridPadWidth, 1)
        gridOptLayout.addLayout(padWRow)

        self.frameGridSettings.hide()
        outerH.addWidget(self.frameGridSettings)

        # Actions column (right)
        actionsLayout = QVBoxLayout()
        self.btnClearSeq = QPushButton('Clear Sequence')
        self.btnClearSeq.clicked.connect(self.clearSequence)
        actionsLayout.addWidget(self.btnClearSeq)

        self.btnEncode = QPushButton('Encode')
        self.btnEncode.clicked.connect(self.encodeCurrent)
        actionsLayout.addWidget(self.btnEncode, 1)

        self.btnCancelAll = QPushButton('Cancel all')
        self.btnCancelAll.clicked.connect(self.cancelAllEncodes)
        actionsLayout.addWidget(self.btnCancelAll)

        outerH.addLayout(actionsLayout)

        parentLayout.addWidget(settingsBox)

        # Initialize value cache from widgets
        self.valueChange()

    # -----------------------------------------------------------------------
    # Value change handler (replaces StringVar/BooleanVar traces)
    # -----------------------------------------------------------------------

    def valueChange(self, *args):
        try:
            self.automaticFileNamingValue = self.checkAutoName.isChecked()
            self.entryFilenamePrefix.setEnabled(not self.automaticFileNamingValue)
        except Exception:
            pass

        try:
            v = self.btnAudioOverride.text()
            self.audioOverrideValue = None if v.upper() == 'NONE' else v
            self.audiOverrideDelayValue = str(self.spinAudioDelay.value())
        except Exception:
            pass

        try:
            self.loopStartAndendValue = self.checkLoopStartEnd.isChecked()
        except Exception:
            pass

        try:
            self.interpolateSpeedChangeValue = self.checkInterpolate.isChecked()
        except Exception:
            pass

        try:
            prefix = self.entryFilenamePrefix.text()
            self.filenamePrefixValue = prefix
            testpath = prefix + '.bin'
            sanitisedPath = sanitize_filepath(testpath)
            pre, _ = os.path.split(testpath)
            if testpath != sanitisedPath or pre != '':
                self.entryFilenamePrefix.setStyleSheet('background: #ffcccc')
            else:
                self.entryFilenamePrefix.setStyleSheet('')
        except Exception:
            try:
                self.entryFilenamePrefix.setStyleSheet('background: #ffcccc')
            except Exception:
                pass

        try:
            tempFmt = self.comboOutputFormat.currentText()
            if tempFmt != self.outputFormatValue:
                for k in list(self.advancedFlags.keys()):
                    if k.startswith('encoder-option-'):
                        del self.advancedFlags[k]
            self.outputFormatValue = tempFmt
        except Exception:
            pass

        try:
            self.frameSizeStrategyValue = self.comboSizeStrategy.currentText()
        except Exception:
            pass

        try:
            self.initialbitrateValue = float(self.spinInitialBitrate.value()) * 1024
        except Exception:
            pass

        try:
            self.maxbitrateValue = float(self.spinMaxBitrate.value()) * 1024
        except Exception:
            pass

        try:
            self.maximumSizeValue = float(self.spinMaxSize.value())
        except Exception:
            pass

        try:
            widthStr = self.comboMaxWidth.currentText().split('-')[0].strip()
            self.maximumWidthValue = int(float(widthStr))
        except Exception:
            pass

        try:
            self.transDurationValue = float(self.spinTransDuration.value())
            minlen = float('inf')
            for clip in self.sequencedClips:
                minlen = min((clip.e - clip.s), minlen)
                minlen = floor(minlen * 1000) / 1000.0
            self.transDurationValue = min(self.transDurationValue, minlen / 2)
        except Exception:
            pass

        try:
            self.transStyleValue = self.comboTransStyle.currentText()
        except Exception:
            pass

        try:
            self.speedAdjustmentValue = float(self.spinSpeedAdjust.value())
        except Exception:
            pass

        try:
            self.audioRate = str(int(self.spinAudioRate.value()))
        except Exception:
            pass

        try:
            self.audioChannels = self.comboAudioChannels.currentText()
        except Exception:
            pass

        try:
            self.audioMerge = self.comboAudioMerge.currentText()
        except Exception:
            pass

        try:
            self.postProcessingFilter = self.comboPostFilter.currentText()
        except Exception:
            pass

        try:
            self.gridLoopMergeOption = self.comboGridLoop.currentText()
        except Exception:
            pass

        try:
            self.gridPadColour = self.comboPadColour.currentText()
        except Exception:
            self.gridPadColour = 'Black'

        try:
            self.gridPadWidth = int(self.spinGridPadWidth.value())
        except Exception:
            self.gridPadWidth = 0

        try:
            self.minimumPSNR = str(self.spinMinPSNR.value())
        except Exception:
            pass

        try:
            self.optimizer = self.comboOptimizer.currentText()
        except Exception:
            pass

        try:
            self.audiOverrideBiasValue = max(0.0, min(1.0, float(self.spinAudioBias.value())))
        except Exception:
            pass

        self.updatedPredictedDuration()

    # -----------------------------------------------------------------------
    # Profile / output format management
    # -----------------------------------------------------------------------

    def profileChanged(self, profileName=None):
        if profileName is None:
            profileName = self.comboProfile.currentText()
        self.editableProfileVars = [
            'outputFormat', 'frameSizeStrategy', 'maximumSize', 'maximumWidth',
            'transDuration', 'transStyle', 'speedAdjustment', 'audioChannels',
            'audioMergeOptions', 'gridLoopMergeOptions', 'audioRate',
        ]
        for p in self.profileSpecs:
            if p['name'] == profileName:
                self.btnDeleteProfile.setEnabled(p.get('editable', False))
                for k, v in p.items():
                    if k == 'outputFormat' and self.controller is not None:
                        v = self.controller.resolveOutputFormat(v)
                    if k == 'outputFormat':
                        self.comboOutputFormat.setCurrentText(str(v))
                    elif k == 'maximumSize':
                        self.spinMaxSize.setValue(float(v))
                break

    def deleteProfile(self):
        pass

    def saveProfile(self):
        pass

    def updateProfileSpecs(self):
        self.profileSpecs = self.controller.getProfiles()
        self.profiles = [x['name'] for x in self.profileSpecs if x.get('name')]
        self.comboProfile.blockSignals(True)
        self.comboProfile.clear()
        self.comboProfile.addItems(self.profiles)
        if self.defaultProfile in self.profiles:
            self.comboProfile.setCurrentText(self.defaultProfile)
        else:
            self.comboProfile.setCurrentIndex(0)
        self.comboProfile.blockSignals(False)
        self.profileChanged(self.comboProfile.currentText())

    def updateOutputFormats(self):
        if self.controller is None:
            return
        availableFormats = self.controller.getAvailableOutputFormats()
        if not availableFormats:
            availableFormats = self.outputFormats
        self.outputFormats = availableFormats
        current = self.comboOutputFormat.currentText()
        self.comboOutputFormat.blockSignals(True)
        self.comboOutputFormat.clear()
        self.comboOutputFormat.addItems(self.outputFormats)
        if current in self.outputFormats:
            self.comboOutputFormat.setCurrentText(current)
        else:
            self.comboOutputFormat.setCurrentIndex(0)
        self.comboOutputFormat.blockSignals(False)

    # -----------------------------------------------------------------------
    # Merge style
    # -----------------------------------------------------------------------

    def mergeStyleChanged(self, *args):
        style = self.comboMergeStyle.currentText().split('-')[0].strip()
        if style == 'Grid':
            self.sequenceScrollArea.hide()
            self.gridWidget.show()
            self.frameGridSettings.show()
            self.frameTransitionSettings.hide()
            self.comboProfile.setEnabled(True)
        elif style in ('Individual Files', 'Clip Reencode'):
            self.gridWidget.hide()
            self.frameGridSettings.hide()
            self.frameTransitionSettings.hide()
            self.comboProfile.setEnabled(True)
            self.sequenceScrollArea.show()
        elif style == 'Sequence':
            self.gridWidget.hide()
            self.frameGridSettings.hide()
            self.frameTransitionSettings.show()
            self.comboProfile.setEnabled(True)
            self.sequenceScrollArea.show()
        elif style == 'Stream Copy':
            self.gridWidget.hide()
            self.frameGridSettings.hide()
            self.frameTransitionSettings.hide()
            self.sequenceScrollArea.show()
            self.comboProfile.setCurrentText('None')
            self.comboProfile.setEnabled(False)
        elif style == 'Full Source Reencode':
            self.gridWidget.hide()
            self.frameGridSettings.hide()
            self.frameTransitionSettings.hide()
            self.comboProfile.setEnabled(True)
            self.sequenceScrollArea.show()

        self.updateSelectableVideos()
        for v in list(self.selectableVideos.values()) + self.sequencedClips:
            v.requestQueuedPreviews()

    # -----------------------------------------------------------------------
    # Sequence summary
    # -----------------------------------------------------------------------

    def updatedPredictedDuration(self):
        totalTime = 0
        timeTrimmedByFade = 0
        for sv in self.sequencedClips:
            totalTime += (sv.e - sv.s) * (1 / sv.getSpeed())
            timeTrimmedByFade += self.transDurationValue
        try:
            totalTime = totalTime * (1 / self.speedAdjustmentValue)
            timeTrimmedByFade = timeTrimmedByFade * (1 / self.speedAdjustmentValue)
        except Exception:
            pass
        self.labelSequenceSummary.setText(
            'Number of Subclips: {n} Total duration {td:0.2f}s Output Duration {tdext:0.2f}s ({factor:0.2%} speed)'.format(
                n=len(self.sequencedClips),
                td=totalTime,
                tdext=totalTime - timeTrimmedByFade,
                factor=self.speedAdjustmentValue,
            )
        )
        if self.checkAutoName.isChecked():
            for sv in self.sequencedClips[:1]:
                outputPrefix = self.convertFilenameToBaseName(sv.filename)
                try:
                    if self.controller and len(self.controller.getLabelForRid(sv.rid)):
                        outputPrefix += '_' + self.convertFilenameToBaseName(
                            self.controller.getLabelForRid(sv.rid), getBasename=False)
                except Exception:
                    pass
                self.entryFilenamePrefix.blockSignals(True)
                self.entryFilenamePrefix.setText(outputPrefix)
                self.entryFilenamePrefix.blockSignals(False)
            else:
                namefound = False
                for col in self.gridColumns:
                    for sv in col['clips']:
                        try:
                            outputPrefix = self.convertFilenameToBaseName(sv.filename)
                            if self.controller and len(self.controller.getLabelForRid(sv.rid)):
                                outputPrefix += '_' + self.convertFilenameToBaseName(
                                    self.controller.getLabelForRid(sv.rid), getBasename=False)
                            self.entryFilenamePrefix.blockSignals(True)
                            self.entryFilenamePrefix.setText(outputPrefix)
                            self.entryFilenamePrefix.blockSignals(False)
                            namefound = True
                            break
                        except Exception:
                            pass
                    if namefound:
                        break

    # -----------------------------------------------------------------------
    # Utility
    # -----------------------------------------------------------------------

    def convertFilenameToBaseName(self, filename, getBasename=True):
        whitespaceChars = '-_. '
        usableChars = string.ascii_letters + string.digits + whitespaceChars
        if getBasename:
            basenameList = ''.join(x for x in os.path.basename(filename).rpartition('.')[0] if x in usableChars)
        else:
            basenameList = ''.join(x for x in filename if x in usableChars)
        for c in whitespaceChars:
            basenameList = basenameList.replace(c, '-')
        basename = ''
        for c in basenameList:
            if len(basename) == 0 or (basename[-1] != c and c == '-') or c != '-':
                basename = basename + c
        if len(basename) == 0:
            basename = 'output'
        return basename

    # -----------------------------------------------------------------------
    # Controller
    # -----------------------------------------------------------------------

    def setController(self, controller):
        self.controller = controller
        self.updateProfileSpecs()
        self.updateOutputFormats()
        for fe in self.postProcessingFilterOptions:
            if fe.upper() == self.controller.getDefaultPostFilter().upper():
                self.comboPostFilter.setCurrentText(fe)
                break

    def tabSwitched(self, tabName):
        self.updateSelectableVideos()
        for v in list(self.selectableVideos.values()) + self.sequencedClips:
            v.requestQueuedPreviews()

    # -----------------------------------------------------------------------
    # Selectable videos
    # -----------------------------------------------------------------------

    def updateSelectableVideos(self):
        if self.controller is None:
            return
        unusedRids = set(self.selectableVideos.keys())

        refreshMode = 'CLIPS'
        if self.comboMergeStyle.currentText().split('-')[0].strip() == 'Full Source Reencode':
            refreshMode = 'VIDEOS'

        for filename, rid, s, e, filterexp, filteraudioexp, filterexpEnc in sorted(
                self.controller.getFilteredClips(refreshMode), key=lambda x: (x[0], x[2])):
            if rid in self.selectableVideos:
                unusedRids.discard(rid)
            if rid not in self.selectableVideos:
                entry = SelectableVideoEntry(
                    self.selectableContainer, self, filename, rid, s, e,
                    filterexp, filteraudioexp, filterexpEnc,
                )
                self.selectableVideos[rid] = entry
                # insert before the stretch
                count = self.selectableContainerLayout.count()
                self.selectableContainerLayout.insertWidget(count - 1, entry)
            elif (self.selectableVideos[rid].s != s or self.selectableVideos[rid].e != e or
                  self.selectableVideos[rid].filterexp != filterexp or
                  self.selectableVideos[rid].filteraudioexp != filteraudioexp):
                self.selectableVideos[rid].update(
                    s, e, filterexp, filteraudioexp, filterexpEnc,
                    requestPreviewFrame=not (self.syncModal is not None and self.syncModal.isActive),
                )

            for sv in self.sequencedClips:
                if sv.rid == rid:
                    sv.update(s, e, filterexp, filteraudioexp, filterexpEnc,
                               requestPreviewFrame=not (self.syncModal is not None and self.syncModal.isActive))
            for col in self.gridColumns:
                for sv in col['clips']:
                    if sv.rid == rid:
                        sv.update(s, e, filterexp, filteraudioexp, filterexpEnc,
                                   requestPreviewFrame=not (self.syncModal is not None and self.syncModal.isActive))

        for rid in unusedRids:
            widget = self.selectableVideos.pop(rid)
            widget.setParent(None)
            widget.deleteLater()

        self.updatedPredictedDuration()

    # -----------------------------------------------------------------------
    # Preview frame callback
    # -----------------------------------------------------------------------

    def previewFrameCallback(self, requestId, timestamp, size, imageData):
        # Convert imageData (bytes) to QPixmap
        pixmap = QPixmap()
        pixmap.loadFromData(imageData)
        for sv in self.selectableVideos.values():
            if sv.rid == requestId:
                sv.setPreviewImage(pixmap)
        for sv in self.sequencedClips:
            if sv.rid == requestId:
                sv.setPreviewImage(pixmap)
        for prog in self.encoderProgress:
            if prog.rid == requestId:
                prog.setPreviewImage(pixmap)
        for col in self.gridColumns:
            for sv in col['clips']:
                if sv.rid == requestId:
                    sv.setPreviewImage(pixmap)

    def requestPreviewFrame(self, rid, filename, timestamp, filterexp):
        self.controller.requestPreviewFrame(rid, filename, timestamp, filterexp, (-1, 80), self.previewFrameCallback)

    # -----------------------------------------------------------------------
    # Sequence management
    # -----------------------------------------------------------------------

    def addClipToSequence(self, clip):
        style = self.comboMergeStyle.currentText().split('-')[0].strip()
        if style == 'Grid':
            if self.selectedColumn is not None:
                entry = SequencedVideoEntry(
                    self.selectedColumn['column'].clipsWidget, self, clip, direction='UP_DOWN'
                )
                self.selectedColumn['column'].clipsLayout.addWidget(entry)
                self.selectedColumn['clips'].append(entry)
        else:
            entry = SequencedVideoEntry(self.sequenceContainer, self, clip)
            count = self.sequenceContainerLayout.count()
            self.sequenceContainerLayout.insertWidget(count - 1, entry)
            self.sequencedClips.append(entry)
        self.updatedPredictedDuration()
        if self.syncModal is not None:
            self.syncModal.valuesChanged = True
            self.syncModal.recalculateEDLTimings()

    def moveSequencedClipByIndex(self, clipIndex, move):
        clip = self.sequencedClips[clipIndex]
        self.moveSequencedClip(clip, move)
        if self.syncModal is not None:
            self.syncModal.valuesChanged = True
            self.syncModal.recalculateEDLTimings()

    def moveSequencedClip(self, clip, move):
        currentIndex = self.sequencedClips.index(clip)
        if 0 <= currentIndex + move < len(self.sequencedClips):
            self.sequencedClips[currentIndex], self.sequencedClips[currentIndex + move] = \
                self.sequencedClips[currentIndex + move], self.sequencedClips[currentIndex]
            # Re-insert in layout order
            for c in self.sequencedClips:
                self.sequenceContainerLayout.removeWidget(c)
            for i, c in enumerate(self.sequencedClips):
                self.sequenceContainerLayout.insertWidget(i, c)
        if self.syncModal is not None:
            self.syncModal.valuesChanged = True
            self.syncModal.recalculateEDLTimings(rid=clip.rid)

    def removeSequencedClip(self, clip):
        style = self.comboMergeStyle.currentText().split('-')[0].strip()
        if style == 'Grid':
            for column in self.gridColumns:
                try:
                    currentIndex = column['clips'].index(clip)
                    removed = column['clips'].pop(currentIndex)
                    removed.setParent(None)
                    removed.deleteLater()
                except Exception:
                    pass
        else:
            currentIndex = self.sequencedClips.index(clip)
            removed = self.sequencedClips.pop(currentIndex)
            removed.setParent(None)
            removed.deleteLater()
            self.updatedPredictedDuration()
        if self.syncModal is not None:
            self.syncModal.valuesChanged = True
            self.syncModal.recalculateEDLTimings()

    def clearSequence(self, includeProgress=True):
        for sv in list(self.sequencedClips):
            sv.setParent(None)
            sv.deleteLater()
        self.sequencedClips.clear()
        for col in list(self.gridColumns):
            self.gridColumns.remove(col)
            col['column'].setParent(None)
            col['column'].deleteLater()
        self.gridColumns.clear()
        if self.syncModal is not None and self.syncModal.isActive:
            self.syncModal.valuesChanged = True
            self.syncModal.recalculateEDLTimings()
        if includeProgress:
            for e in list(self.encoderProgress):
                if e.iscomplete or e.cancelled:
                    e.remove()

    def clearAllColumns(self):
        for column in self.gridColumns:
            while len(column['clips']) > 0:
                removed = column['clips'].pop()
                removed.setParent(None)
                removed.deleteLater()

    # -----------------------------------------------------------------------
    # Grid column management
    # -----------------------------------------------------------------------

    def addRow(self):
        self.addColumn()

    def addColumn(self):
        col = GridColumn(self.gridColumnContainer, self)
        self.gridColumnLayout.insertWidget(self.gridColumnLayout.count() - 1, col)
        self.gridColumns.append({'column': col, 'clips': []})

    def selectColumn(self, col):
        selectedCol = next((x for x in self.gridColumns if x['column'] == col), None)
        if selectedCol is None:
            return
        if self.selectedColumn is not None:
            self.selectedColumn['column'].setSelected(False)
            self.selectedColumn = None
        self.selectedColumn = selectedCol
        self.selectedColumn['column'].setSelected(True)

    def removeColumn(self, col):
        colToRemove = next((x for x in self.gridColumns if x['column'] == col), None)
        if colToRemove is None:
            return
        self.gridColumns.remove(colToRemove)
        col.setParent(None)
        col.deleteLater()
        if self.selectedColumn == colToRemove:
            self.selectedColumn = None

    # -----------------------------------------------------------------------
    # Add all clips helpers
    # -----------------------------------------------------------------------

    def addAllClipsInTimelineOrder(self, minrid=-1, clearProgress=True):
        finalrid = int(minrid)
        style = self.comboMergeStyle.currentText().split('-')[0].strip()
        if style == 'Grid':
            self.clearAllColumns()
            for ind, clip in enumerate(sorted(self.selectableVideos.values(), key=lambda x: (x.filename, x.s))):
                finalrid = max(int(finalrid), int(clip.rid))
                if int(clip.rid) > int(minrid):
                    if ind % max(len(self.gridColumns), 1) < len(self.gridColumns):
                        col = self.gridColumns[ind % len(self.gridColumns)]
                        entry = SequencedVideoEntry(col['column'].clipsWidget, self, clip, direction='UP_DOWN')
                        col['column'].clipsLayout.addWidget(entry)
                        col['clips'].append(entry)
        else:
            self.clearSequence(includeProgress=clearProgress)
            for clip in sorted(self.selectableVideos.values(), key=lambda x: (x.filename, x.s)):
                finalrid = max(int(finalrid), int(clip.rid))
                if int(clip.rid) > int(minrid):
                    self.addClipToSequence(clip)
        return finalrid

    def addAllClipsInRIDOrder(self):
        style = self.comboMergeStyle.currentText().split('-')[0].strip()
        if style == 'Grid':
            self.clearAllColumns()
            for ind, clip in enumerate(sorted(self.selectableVideos.values(), key=lambda x: x.rid)):
                if self.gridColumns:
                    col = self.gridColumns[ind % len(self.gridColumns)]
                    entry = SequencedVideoEntry(col['column'].clipsWidget, self, clip, direction='UP_DOWN')
                    col['column'].clipsLayout.addWidget(entry)
                    col['clips'].append(entry)
        else:
            self.clearSequence()
            for clip in sorted(self.selectableVideos.values(), key=lambda x: x.rid):
                self.addClipToSequence(clip)

    def addAllClipsInRandomOrder(self):
        style = self.comboMergeStyle.currentText().split('-')[0].strip()
        if style == 'Grid':
            self.clearAllColumns()
            for ind, clip in enumerate(sorted(self.selectableVideos.values(), key=lambda x: random.random())):
                if self.gridColumns:
                    col = self.gridColumns[ind % len(self.gridColumns)]
                    entry = SequencedVideoEntry(col['column'].clipsWidget, self, clip, direction='UP_DOWN')
                    col['column'].clipsLayout.addWidget(entry)
                    col['clips'].append(entry)
        else:
            self.clearSequence()
            for clip in sorted(self.selectableVideos.values(), key=lambda x: random.random()):
                self.addClipToSequence(clip)

    def addAllClipsInSmartRandomOrder(self):
        smartOrder = []
        smartCats = {}
        for clip in sorted(self.selectableVideos.values(), key=lambda x: random.random()):
            smartCats.setdefault(clip.filename, []).append(clip)
        smartCats = list(smartCats.values())
        random.shuffle(smartCats)
        lastList = None
        while sum([len(x) for x in smartCats]) > 0:
            smartCats = [x for x in smartCats if len(x) > 0]
            if len(smartCats) == 1:
                lastList = smartCats[0]
                smartOrder.append(lastList.pop())
            else:
                othercats = [x for x in smartCats if x != lastList]
                lastList = random.choice(othercats)
                smartOrder.append(lastList.pop())
        style = self.comboMergeStyle.currentText().split('-')[0].strip()
        if style == 'Grid':
            self.clearAllColumns()
            for ind, clip in enumerate(smartOrder):
                if self.gridColumns:
                    col = self.gridColumns[ind % len(self.gridColumns)]
                    entry = SequencedVideoEntry(col['column'].clipsWidget, self, clip, direction='UP_DOWN')
                    col['column'].clipsLayout.addWidget(entry)
                    col['clips'].append(entry)
        else:
            self.clearSequence()
            for clip in smartOrder:
                self.addClipToSequence(clip)

    def addAllClipsInInterspersedOrder(self):
        finalOrder = []
        clipsByFile = {}
        for clip in sorted(self.selectableVideos.values(), key=lambda x: x.s, reverse=True):
            clipsByFile.setdefault(clip.filename, []).append(clip)
        clipsByFile = list(clipsByFile.values())
        random.shuffle(clipsByFile)
        while sum([len(x) for x in clipsByFile]) > 0:
            for fileClips in clipsByFile:
                if len(fileClips) > 0:
                    finalOrder.append(fileClips.pop())
        style = self.comboMergeStyle.currentText().split('-')[0].strip()
        if style == 'Grid':
            self.clearAllColumns()
            for ind, clip in enumerate(finalOrder):
                if self.gridColumns:
                    col = self.gridColumns[ind % len(self.gridColumns)]
                    entry = SequencedVideoEntry(col['column'].clipsWidget, self, clip, direction='UP_DOWN')
                    col['column'].clipsLayout.addWidget(entry)
                    col['clips'].append(entry)
        else:
            self.clearSequence()
            for clip in finalOrder:
                self.addClipToSequence(clip)

    # -----------------------------------------------------------------------
    # Encode
    # -----------------------------------------------------------------------

    def registerComplete(self, filename, clip=None):
        self.controller.registerComplete(filename, clip=clip)

    def cancelEncodeRequest(self, requestId):
        self.controller.cancelEncodeRequest(requestId)

    def cancelAllEncodes(self):
        for epw in self.encoderProgress:
            epw.cancelEncodeRequest()

    def encodeCurrent(self):
        clip = None
        nullfilter = ''
        disableFilters = self.comboPostFilter.currentText() == 'Disable all filters'

        if (not self.automaticFileNamingValue) and (
                self.filenamePrefixValue is None or self.filenamePrefixValue.strip() == ''):
            self.filenamePrefixValue = 'output'

        style = self.comboMergeStyle.currentText().split('-')[0].strip()

        def makeOptions(**extra):
            opts = {
                'frameSizeStrategy': self.frameSizeStrategyValue,
                'maximumSize': self.maximumSizeValue,
                'initialBitrate': self.initialbitrateValue,
                'maximumBitrate': self.maxbitrateValue,
                'maximumWidth': self.maximumWidthValue,
                'transDuration': self.transDurationValue,
                'transStyle': self.transStyleValue,
                'speedAdjustment': self.speedAdjustmentValue,
                'speedAdjustmentInterploate': self.interpolateSpeedChangeValue,
                'outputFormat': self.outputFormatValue,
                'audioChannels': self.audioChannels,
                'audioRate': self.audioRate,
                'audioMerge': self.audioMerge,
                'postProcessingFilter': self.postProcessingFilter,
                'audioOverride': self.audioOverrideValue,
                'audiOverrideDelay': self.audiOverrideDelayValue,
                'gridLoopMergeOption': self.gridLoopMergeOption,
                'minimumPSNR': self.minimumPSNR,
                'optimizer': self.optimizer,
                'audioOverrideBias': self.audiOverrideBiasValue,
            }
            opts.update(extra)
            opts.update(self.advancedFlags)
            return opts

        def makeProgressWidget(clip, targetSize=None):
            w = EncodeProgress(
                self.encodeProgressContainer,
                encodeRequestId=self.encodeRequestId,
                controller=self,
                targetSize=targetSize if targetSize is not None else self.maximumSizeValue,
                clip=clip,
            )
            self.encodeProgressLayout.addWidget(w)
            self.encoderProgress.append(w)
            return w

        def autoPrefix(clip):
            outputPrefix = self.filenamePrefixValue
            if self.automaticFileNamingValue:
                try:
                    lbl = self.controller.getLabelForRid(clip.rid)
                    if len(lbl):
                        outputPrefix = self.convertFilenameToBaseName(lbl, getBasename=False)
                    else:
                        outputPrefix = self.convertFilenameToBaseName(clip.filename)
                except Exception:
                    outputPrefix = self.convertFilenameToBaseName(clip.filename)
            return outputPrefix

        # ----- Stream Copy -----
        if style == 'Stream Copy':
            encodeSequence = []
            self.encodeRequestId += 1
            for clip in self.sequencedClips:
                encodeSequence.append((clip.rid, clip.filename, clip.s, clip.e, nullfilter, nullfilter, nullfilter, clip.getSpeed()))
            if encodeSequence:
                epw = makeProgressWidget(clip)
                self.controller.encode(self.encodeRequestId, 'STREAMCOPY', encodeSequence, {}, autoPrefix(clip), epw.updateStatus)

        # ----- Grid -----
        if style == 'Grid':
            encodeSequence = []
            selectedColumnInd = 0
            for i, column in enumerate(self.gridColumns):
                outcol = []
                for clip in column['clips']:
                    definition = (clip.rid, clip.filename, clip.s, clip.e,
                                  nullfilter if disableFilters else clip.filterexp,
                                  nullfilter if disableFilters else clip.filteraudioexp,
                                  nullfilter if disableFilters else clip.filterexpEnc,
                                  clip.getSpeed())
                    outcol.append(definition)
                    if column == self.selectedColumn:
                        selectedColumnInd = i
                if outcol:
                    encodeSequence.append(outcol)
            if not encodeSequence:
                return
            self.encodeRequestId += 1
            options = makeOptions(selectedColumn=selectedColumnInd,
                                  gridPaddingWidth=self.gridPadWidth,
                                  gridPadColour=self.gridPadColour)
            try:
                lbl = self.controller.getLabelForRid(encodeSequence[0][0][0])
                outputPrefix = self.convertFilenameToBaseName(lbl, getBasename=False) if len(lbl) else self.convertFilenameToBaseName(encodeSequence[0][0][1])
            except Exception:
                outputPrefix = self.filenamePrefixValue
            epw = makeProgressWidget(clip)
            self.controller.encode(self.encodeRequestId, 'GRID', encodeSequence, options, outputPrefix, epw.updateStatus)

        # ----- Sequence -----
        if style == 'Sequence':
            uniqueSequences = set()
            for clip in self.sequencedClips:
                uniqueSequences.add(self.controller.getSeqGroupForRid(clip.rid))
            sequenceRepreClip = None
            for seqid in sorted(uniqueSequences):
                encodeSequence = []
                self.encodeRequestId += 1
                for clip in self.sequencedClips:
                    if self.controller.getSeqGroupForRid(clip.rid) == seqid:
                        definition = (clip.rid, clip.filename, clip.s, clip.e,
                                      nullfilter if disableFilters else clip.filterexp,
                                      nullfilter if disableFilters else clip.filteraudioexp,
                                      nullfilter if disableFilters else clip.filterexpEnc,
                                      clip.getSpeed())
                        encodeSequence.append(definition)
                        sequenceRepreClip = clip
                if sequenceRepreClip is None and self.sequencedClips:
                    sequenceRepreClip = self.sequencedClips[-1]
                if encodeSequence:
                    options = makeOptions(loopStartAndEnd=self.loopStartAndendValue)
                    try:
                        lbl = self.controller.getLabelForRid(self.sequencedClips[0].rid)
                        outputPrefix = self.convertFilenameToBaseName(lbl, getBasename=False) if len(lbl) else self.convertFilenameToBaseName(self.sequencedClips[0].filename)
                    except Exception:
                        outputPrefix = self.filenamePrefixValue
                    epw = makeProgressWidget(sequenceRepreClip)
                    self.controller.encode(self.encodeRequestId, 'CONCAT', encodeSequence, options.copy(), outputPrefix, epw.updateStatus)

        # ----- Individual Files -----
        if style == 'Individual Files':
            for clip in self.sequencedClips:
                encodeSequence = [(clip.rid, clip.filename, clip.s, clip.e,
                                   nullfilter if disableFilters else clip.filterexp,
                                   nullfilter if disableFilters else clip.filteraudioexp,
                                   nullfilter if disableFilters else clip.filterexpEnc,
                                   clip.getSpeed())]
                self.encodeRequestId += 1
                options = makeOptions(transDuration=0.0)
                epw = makeProgressWidget(clip)
                self.controller.encode(self.encodeRequestId, 'CONCAT', encodeSequence, options.copy(), autoPrefix(clip), epw.updateStatus)

        # ----- Clip Reencode -----
        if style == 'Clip Reencode':
            for clip in self.sequencedClips:
                encodeSequence = [(clip.rid, clip.filename, clip.s, clip.e, nullfilter, nullfilter, nullfilter, 1)]
                self.encodeRequestId += 1
                options = makeOptions(transDuration=0.0)
                epw = makeProgressWidget(clip)
                self.controller.encode(self.encodeRequestId, 'CONCAT', encodeSequence, options.copy(), autoPrefix(clip), epw.updateStatus)

        # ----- Full Source Reencode -----
        if style == 'Full Source Reencode':
            uniquefilenames = set()
            uniqueseq = []
            for clip in self.sequencedClips:
                if clip.filename not in uniquefilenames:
                    uniquefilenames.add(clip.filename)
                    uniqueseq.append(clip)
            for clip in uniqueseq:
                encodeSequence = [(clip.rid, clip.filename, None, None, nullfilter, nullfilter, nullfilter, 1)]
                self.encodeRequestId += 1
                options = makeOptions(transDuration=0.0)
                epw = makeProgressWidget(clip)
                self.controller.encode(self.encodeRequestId, 'CONCAT', encodeSequence, options.copy(), autoPrefix(clip), epw.updateStatus)

    # -----------------------------------------------------------------------
    # Misc controller-facing methods
    # -----------------------------------------------------------------------

    def setIgnoreDrop(self, path):
        self.controller.setIgnoreDrop(path)

    def selectAdvancedOptions(self):
        modal = AdvancedEncodeFlagsModal(master=self, controller=self)
        modal.exec()

    def getAdvancedFlags(self):
        return self.advancedFlags

    def setAdvancedFlags(self, flags):
        self.advancedFlags.update(flags)

    def viewFilterForClip(self, clip):
        self.controller.jumpToFilterByRid(clip.rid)

    def destroyPlannerModal(self):
        if self.syncModal is not None:
            self.syncModal.isActive = False
            self.syncModal.close()

    def previewSequencetimings(self, uiParent=None):
        if self.comboMergeStyle.currentText() != self.mergeStyles[1]:
            self.comboMergeStyle.setCurrentText(self.mergeStyles[1])
        self.destroyPlannerModal()

        from PySide6.QtWidgets import QDialog
        if uiParent is None:
            dlg = QDialog(self)
            dlg.setWindowTitle('Sequence Timings')
            dlgLayout = QVBoxLayout(dlg)
        else:
            dlg = uiParent
            dlgLayout = uiParent.layout() or QVBoxLayout(uiParent)

        self.syncModal = VideoAudioSync(
            uiParent=dlg,
            master=self,
            controller=self.controller,
            sequencedClips=self.sequencedClips,
            dubFile=self.btnAudioOverride,
            dubOffsetVar=self.spinAudioDelay,
            fadeVar=self.spinTransDuration,
            globalOptions=self.globalOptions,
            mixVar=self.spinAudioBias,
        )
        dlgLayout.addWidget(self.syncModal)
        if uiParent is None:
            dlg.exec()

    def selectAudioOverride(self):
        files, _ = QFileDialog.getOpenFileName(
            self, 'Select audio dub', '',
            'Audio files (*.mp3 *.wav);;All files (*.*)',
        )
        if files:
            self.btnAudioOverride.setText(files)
        else:
            self.btnAudioOverride.setText('None')
        self.valueChange()

    def close_ui(self):
        if self.syncModal is not None:
            try:
                self.syncModal.cleanup()
            except Exception:
                pass

    def toggleBoringMode(self, boringMode):
        if self.syncModal is not None and self.syncModal.isActive:
            self.syncModal.toggleBoringMode(boringMode)

    # -----------------------------------------------------------------------
    # VideoSubclip callbacks
    # -----------------------------------------------------------------------

    def videoSubclipDurationChangeCallback(self, rid=None, pos=None, action='UPDATE'):
        if self.syncModal is not None and self.syncModal.isActive:
            refreshMode = 'CLIPS'
            if self.comboMergeStyle.currentText().split('-')[0].strip() == 'Full Source Reencode':
                refreshMode = 'VIDEOS'

            if action == 'NEW':
                self.updateSelectableVideos()
                self.addClipToSequence(self.selectableVideos[rid])

            if action == 'REMOVE':
                clipsForRemoval = [sv for sv in self.sequencedClips if sv.rid == rid]
                for clip in clipsForRemoval:
                    self.removeSequencedClip(clip)

            changedrid = rid
            for filename, rid, s, e, filterexp, filteraudioexp, filterexpEnc in sorted(
                    self.controller.getFilteredClips(refreshMode), key=lambda x: (x[0], x[2])):
                for sv in self.sequencedClips:
                    if sv.rid == rid and (changedrid is None or changedrid == rid):
                        sv.update(s, e, filterexp, filteraudioexp, filterexpEnc,
                                   requestPreviewFrame=not (self.syncModal is not None and self.syncModal.isActive))
            try:
                self.syncModal.keepWidth = True
                self.syncModal.valuesChanged = True
                self.syncModal.recalculateEDLTimings(rid=changedrid, pos=pos)
                self.syncModal.keepWidth = False
            except Exception:
                pass

    def synchroniseCutController(self, rid, startoffset, forceTabJump=False):
        self.controller.synchroniseCutController(rid, startoffset, forceTabJump=forceTabJump)
