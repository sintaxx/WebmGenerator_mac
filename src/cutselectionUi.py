"""
CutselectionUi - PySide6 implementation (Phase 2 migration).
"""

import os
import logging
import numpy as np

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QScrollArea, QSizePolicy,
    QFileDialog, QMessageBox, QInputDialog, QApplication,
    QDoubleSpinBox, QSpinBox, QComboBox, QProgressBar,
    QFrame, QSplitter, QCheckBox,
)
from PySide6.QtCore import Qt, QTimer, QMimeData, QUrl, QByteArray, QThread
from PySide6.QtGui import QPixmap, QImage, QDrag


# ---------------------------------------------------------------------------
# Tiny Tk-compat var so controller code using targetTrimVar.get() still works
# ---------------------------------------------------------------------------

class _FloatVar:
    """Minimal compatibility shim replacing Tk DoubleVar / StringVar."""

    def __init__(self, value=0.0):
        self._value = float(value)

    def get(self):
        return str(self._value)

    def set(self, value):
        self._value = float(value)


# ---------------------------------------------------------------------------
# Small clickable label used to represent a file in the file listing
# ---------------------------------------------------------------------------

class _FileListItem(QWidget):
    """A row in the file list: thumbnail + filename label."""

    def __init__(self, filepath, controller, parent=None):
        super().__init__(parent)
        self.filepath = filepath
        self.controller = controller

        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(4)

        self.thumbnailLabel = QLabel()
        self.thumbnailLabel.setFixedSize(64, 36)
        self.thumbnailLabel.setAlignment(Qt.AlignCenter)
        self.thumbnailLabel.setStyleSheet('background: #1E1E1E; border: 1px solid #3a3a3a;')
        layout.addWidget(self.thumbnailLabel)

        nameLabel = QLabel(os.path.basename(filepath))
        nameLabel.setWordWrap(False)
        nameLabel.setStyleSheet('color: #cccccc; font-size: 11px;')
        nameLabel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout.addWidget(nameLabel)

        self.setStyleSheet('background: #2a2a2a;')
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.controller:
            self.controller.playVideoFile(self.filepath, 0)
        super().mousePressEvent(event)

    def setThumbnail(self, pixmap):
        self.thumbnailLabel.setPixmap(
            pixmap.scaled(64, 36, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )


# ---------------------------------------------------------------------------
# Main widget
# ---------------------------------------------------------------------------

class CutselectionUi(QWidget):

    def __init__(self, master=None, globalOptions=None, *args, **kwargs):
        super().__init__()

        self.controller = None
        self.globalOptions = globalOptions or {}
        self.disableFileWidgets = False

        # Compatibility shim: controller reads targetTrimVar.get() as float string
        self.targetTrimVar = _FloatVar(0.0)

        # Internal state
        self._fileItems = {}          # filepath -> _FileListItem
        self._thumbnailCache = {}     # filepath -> QPixmap
        self._currentRegionStart = None
        self._currentRegionEnd   = None
        self._paused = True

        self._buildUi()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _buildUi(self):
        rootLayout = QVBoxLayout(self)
        rootLayout.setContentsMargins(2, 2, 2, 2)
        rootLayout.setSpacing(2)

        # ---- top splitter: file list | player | timeline ----
        self.mainSplitter = QSplitter(Qt.Horizontal)
        rootLayout.addWidget(self.mainSplitter, stretch=1)

        # --- Left panel: file listing ---
        self._buildFileListPanel()

        # --- Centre panel: player ---
        self._buildPlayerPanel()

        # --- Right panel: timeline + subclip params ---
        self._buildTimelinePanel()

        self.mainSplitter.setSizes([200, 600, 300])

        # ---- Bottom bar: params + progress ----
        self._buildBottomBar()
        rootLayout.addWidget(self._bottomBar)

    def _buildFileListPanel(self):
        leftPanel = QWidget()
        leftPanel.setMinimumWidth(160)
        leftPanel.setMaximumWidth(320)
        leftLayout = QVBoxLayout(leftPanel)
        leftLayout.setContentsMargins(0, 0, 0, 0)
        leftLayout.setSpacing(2)

        # Load buttons
        btnRow = QHBoxLayout()
        self.btnLoadFiles = QPushButton('Load Files')
        self.btnLoadFiles.setToolTip('Load video files from disk')
        self.btnLoadFiles.clicked.connect(self.loadVideoFiles)
        btnRow.addWidget(self.btnLoadFiles)
        leftLayout.addLayout(btnRow)

        # Scrollable file list
        self.fileListScroll = QScrollArea()
        self.fileListScroll.setWidgetResizable(True)
        self.fileListScroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.fileListContainer = QWidget()
        self.fileListLayout = QVBoxLayout(self.fileListContainer)
        self.fileListLayout.setContentsMargins(2, 2, 2, 2)
        self.fileListLayout.setSpacing(2)
        self.fileListLayout.addStretch(1)

        self.fileListScroll.setWidget(self.fileListContainer)
        leftLayout.addWidget(self.fileListScroll, stretch=1)

        self.mainSplitter.addWidget(leftPanel)

    def _buildPlayerPanel(self):
        centerPanel = QWidget()
        centerLayout = QVBoxLayout(centerPanel)
        centerLayout.setContentsMargins(0, 0, 0, 0)
        centerLayout.setSpacing(2)

        # mpv embedding frame — must be a plain QWidget with a stable winId
        self.playerFrame = QWidget()
        self.playerFrame.setMinimumSize(320, 180)
        self.playerFrame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.playerFrame.setStyleSheet('background: #282828;')
        # Ensure it is a native window so mpv can embed into it
        self.playerFrame.setAttribute(Qt.WA_NativeWindow, True)
        centerLayout.addWidget(self.playerFrame, stretch=1)

        # Playback controls
        ctrlRow = QHBoxLayout()
        ctrlRow.setSpacing(4)

        self.btnPrevFile = QPushButton('|<')
        self.btnPrevFile.setToolTip('Previous file')
        self.btnPrevFile.setFixedWidth(32)
        self.btnPrevFile.clicked.connect(lambda: self.controller and self.controller.jumpClips(-1))
        ctrlRow.addWidget(self.btnPrevFile)

        self.btnPlayPause = QPushButton('Play')
        self.btnPlayPause.setToolTip('Play / Pause')
        self.btnPlayPause.clicked.connect(lambda: self.controller and self.controller.playPauseToggle())
        ctrlRow.addWidget(self.btnPlayPause)

        self.btnNextFile = QPushButton('>|')
        self.btnNextFile.setToolTip('Next file')
        self.btnNextFile.setFixedWidth(32)
        self.btnNextFile.clicked.connect(lambda: self.controller and self.controller.jumpClips(1))
        ctrlRow.addWidget(self.btnNextFile)

        ctrlRow.addStretch(1)

        self.summaryLabel = QLabel('')
        self.summaryLabel.setStyleSheet('color: #aaaaaa; font-size: 11px;')
        ctrlRow.addWidget(self.summaryLabel)

        centerLayout.addLayout(ctrlRow)

        self.mainSplitter.addWidget(centerPanel)

    def _buildTimelinePanel(self):
        rightPanel = QWidget()
        rightPanel.setMinimumWidth(200)
        rightLayout = QVBoxLayout(rightPanel)
        rightLayout.setContentsMargins(0, 0, 0, 0)
        rightLayout.setSpacing(2)

        try:
            from .timeLineSelectionFrameUI import TimeLineSelectionFrameUI
        except ImportError:
            from timeLineSelectionFrameUI import TimeLineSelectionFrameUI

        # controller is None at construction time; set in setController()
        self.frameTimeLineFrame = TimeLineSelectionFrameUI(
            self, controller=None, globalOptions=self.globalOptions
        )
        rightLayout.addWidget(self.frameTimeLineFrame, stretch=1)

        # Subclip parameter controls
        paramGrid = QGridLayout()
        paramGrid.setHorizontalSpacing(4)
        paramGrid.setVerticalSpacing(2)

        row = 0
        paramGrid.addWidget(QLabel('Slice dur (s):'), row, 0)
        self.sliceDurSpin = QDoubleSpinBox()
        self.sliceDurSpin.setRange(0.1, 3600.0)
        self.sliceDurSpin.setValue(10.0)
        self.sliceDurSpin.setSingleStep(0.5)
        self.sliceDurSpin.setToolTip('Default slice duration in seconds')
        paramGrid.addWidget(self.sliceDurSpin, row, 1)

        row += 1
        paramGrid.addWidget(QLabel('Target (s):'), row, 0)
        self.targetLenSpin = QDoubleSpinBox()
        self.targetLenSpin.setRange(0.0, 7200.0)
        self.targetLenSpin.setValue(0.0)
        self.targetLenSpin.setSingleStep(1.0)
        self.targetLenSpin.setToolTip('Target total encode length (0 = no limit)')
        # Keep targetTrimVar in sync
        self.targetLenSpin.valueChanged.connect(
            lambda v: self.targetTrimVar.set(v)
        )
        paramGrid.addWidget(self.targetLenSpin, row, 1)

        row += 1
        paramGrid.addWidget(QLabel('Trim (s):'), row, 0)
        self.trimSpin = QDoubleSpinBox()
        self.trimSpin.setRange(0.0, 60.0)
        self.trimSpin.setValue(0.25)
        self.trimSpin.setSingleStep(0.05)
        self.trimSpin.setDecimals(3)
        self.trimSpin.setToolTip('Trim amount removed from each end of subclip')
        paramGrid.addWidget(self.trimSpin, row, 1)

        row += 1
        paramGrid.addWidget(QLabel('Drag offset:'), row, 0)
        self.dragOffsetSpin = QDoubleSpinBox()
        self.dragOffsetSpin.setRange(0.0, 60.0)
        self.dragOffsetSpin.setValue(0.0)
        self.dragOffsetSpin.setSingleStep(0.1)
        self.dragOffsetSpin.setDecimals(3)
        self.dragOffsetSpin.setToolTip('Seek offset when clicking a completed clip drag source')
        paramGrid.addWidget(self.dragOffsetSpin, row, 1)

        rightLayout.addLayout(paramGrid)
        self.mainSplitter.addWidget(rightPanel)

    def _buildBottomBar(self):
        self._bottomBar = QWidget()
        bottomLayout = QHBoxLayout(self._bottomBar)
        bottomLayout.setContentsMargins(4, 2, 4, 2)
        bottomLayout.setSpacing(8)

        self.progressBar = QProgressBar()
        self.progressBar.setRange(0, 100)
        self.progressBar.setValue(0)
        self.progressBar.setFixedHeight(14)
        bottomLayout.addWidget(self.progressBar, stretch=1)

        self.statsLabel = QLabel('')
        self.statsLabel.setStyleSheet('color: #aaaaaa; font-size: 10px;')
        bottomLayout.addWidget(self.statsLabel)

    # ------------------------------------------------------------------
    # Controller wiring
    # ------------------------------------------------------------------

    def setController(self, controller):
        self.controller = controller
        # Wire the controller into the timeline widget now that it's available
        if hasattr(self.frameTimeLineFrame, 'controller'):
            self.frameTimeLineFrame.controller = controller

    # ------------------------------------------------------------------
    # Tk .after() compatibility
    # ------------------------------------------------------------------

    def after(self, ms, callback, *args):
        """Tkinter .after() compatibility shim — delegates to QTimer."""
        QTimer.singleShot(ms, lambda: callback(*args) if args else callback())

    # ------------------------------------------------------------------
    # Tab lifecycle
    # ------------------------------------------------------------------

    def tabSwitched(self, tabName):
        """Called by the main window when the tab selection changes."""
        if self.controller is None:
            return
        try:
            if tabName == 'cuts':
                self.controller.isActiveTab = True
            else:
                self.controller.isActiveTab = False
                self.controller.pause()
        except Exception as exc:
            logging.debug('tabSwitched: %s', exc)

    # ------------------------------------------------------------------
    # Focus / misc helpers
    # ------------------------------------------------------------------

    def setinitialFocus(self):
        self.setFocus()

    def clearVideoMousePress(self):
        pass

    # ------------------------------------------------------------------
    # File loading UI actions
    # ------------------------------------------------------------------

    def loadVideoFiles(self):
        filenames, _ = QFileDialog.getOpenFileNames(
            self,
            'Load Video Files',
            '',
            'Video Files (*.mp4 *.webm *.mkv *.avi *.mov *.flv *.wmv *.m4v *.ts *.gif);;All Files (*)',
        )
        if filenames and self.controller:
            self.controller.loadFiles(filenames)

    def loadClipboardUrls(self):
        """Load files/URLs from the clipboard."""
        clipboard = QApplication.clipboard()
        text = clipboard.text().strip()
        if text and self.controller:
            urls = [u.strip() for u in text.splitlines() if u.strip()]
            if urls:
                self.controller.loadFiles(urls)

    def loadVideoYTdl(self):
        pass  # Modal handled elsewhere

    def startScreencap(self, captureType='gdigrab'):
        pass

    def loadImageFile(self):
        filename, _ = QFileDialog.getOpenFileName(
            self,
            'Load Image File',
            '',
            'Image Files (*.png *.jpg *.jpeg *.bmp *.gif *.tiff);;All Files (*)',
        )
        if filename and self.controller:
            duration, ok = QInputDialog.getDouble(
                self, 'Image duration', 'Duration (seconds):', 5.0, 0.1, 3600.0, 1
            )
            if ok:
                self.controller.loadImageFile(filename, duration)

    def updateProgressPreview(self, data):
        pass

    # ------------------------------------------------------------------
    # File listing
    # ------------------------------------------------------------------

    def updateFileListing(self, files):
        """Rebuild the scrollable file listing from a list of file paths."""
        # Remove items that are no longer in the list
        existing = set(self._fileItems.keys())
        new_set = set(files)

        for fp in existing - new_set:
            item = self._fileItems.pop(fp, None)
            if item:
                self.fileListLayout.removeWidget(item)
                item.deleteLater()

        # Add items that are new
        # We insert before the trailing stretch (last item)
        stretch_index = self.fileListLayout.count() - 1
        for i, fp in enumerate(files):
            if fp not in self._fileItems:
                if self.disableFileWidgets:
                    item = _SimpleFileListItem(fp, self.controller)
                else:
                    item = _FileListItem(fp, self.controller)
                    # Request a thumbnail
                    if self.controller and not self.disableFileWidgets:
                        QTimer.singleShot(
                            50,
                            lambda f=fp: self.controller.requestPreviewFrame(f, '10%', (64, 36)),
                        )
                self._fileItems[fp] = item
                self.fileListLayout.insertWidget(stretch_index, item)
                stretch_index += 1

            # restore a cached thumbnail if available
            if fp in self._thumbnailCache and not self.disableFileWidgets:
                item = self._fileItems[fp]
                if hasattr(item, 'setThumbnail'):
                    item.setThumbnail(self._thumbnailCache[fp])

    def removefileIfLoaded(self, path):
        """Remove a specific file entry from the listing (controller callback)."""
        if self.controller:
            self.controller.removefileIfLoaded(path)

    # ------------------------------------------------------------------
    # Thumbnail updates
    # ------------------------------------------------------------------

    def updateViewPreviewFrame(self, requestId, responseImage):
        """Set a thumbnail for the file identified by requestId.

        responseImage may be a numpy array (H×W×3 uint8) or raw PGM bytes.
        requestId is the filepath of the file being previewed.
        """
        # Called from background preview worker threads — must be on main thread
        if QThread.currentThread() is not QApplication.instance().thread():
            QTimer.singleShot(0, lambda: self.updateViewPreviewFrame(requestId, responseImage))
            return

        if responseImage is None:
            return

        pixmap = None
        try:
            if isinstance(responseImage, np.ndarray):
                arr = responseImage
                if arr.ndim == 3 and arr.shape[2] == 3:
                    h, w, ch = arr.shape
                    img = QImage(arr.data, w, h, ch * w, QImage.Format_RGB888)
                    pixmap = QPixmap.fromImage(img.copy())
                elif arr.ndim == 3 and arr.shape[2] == 4:
                    h, w, ch = arr.shape
                    img = QImage(arr.data, w, h, ch * w, QImage.Format_RGBA8888)
                    pixmap = QPixmap.fromImage(img.copy())
            elif isinstance(responseImage, (bytes, bytearray)):
                # Try PGM / PPM raw bytes
                qba = QByteArray(bytes(responseImage))
                img = QImage()
                img.loadFromData(qba)
                if not img.isNull():
                    pixmap = QPixmap.fromImage(img)
        except Exception as exc:
            logging.debug('updateViewPreviewFrame conversion error: %s', exc)

        if pixmap is None:
            return

        filepath = requestId
        self._thumbnailCache[filepath] = pixmap

        item = self._fileItems.get(filepath)
        if item and hasattr(item, 'setThumbnail'):
            item.setThumbnail(pixmap)

    # ------------------------------------------------------------------
    # Player frame access (mpv embedding)
    # ------------------------------------------------------------------

    def getPlayerFrameWid(self):
        """Return the native window ID of the mpv embedding frame."""
        # Ensure the widget has a native handle before returning its winId
        self.playerFrame.setAttribute(Qt.WA_NativeWindow, True)
        self.playerFrame.winId()          # force creation of the native handle
        QApplication.processEvents()
        return int(self.playerFrame.winId())

    # ------------------------------------------------------------------
    # Playback status / timeline updates
    # ------------------------------------------------------------------

    def setPausedStatus(self, value):
        """Called from controller when mpv pause state changes (mpv event thread)."""
        if QThread.currentThread() is not QApplication.instance().thread():
            QTimer.singleShot(0, lambda v=value: self.setPausedStatus(v))
            return
        self._paused = bool(value)
        self.btnPlayPause.setText('Play' if self._paused else 'Pause')

    def update(self, withLock=False):
        """Called frequently by the controller to refresh the timeline display."""
        # The timeline widget handles its own repainting; we just ensure the
        # Qt event loop processes pending events without blocking.
        pass

    def setUiDirtyFlag(self, specificRID=None, withLock=False):
        """Signal the timeline that its data has changed and it should repaint."""
        # May be called from background ffmpegService callback threads
        if QThread.currentThread() is not QApplication.instance().thread():
            QTimer.singleShot(0, lambda r=specificRID: self.setUiDirtyFlag(r))
            return
        if hasattr(self.frameTimeLineFrame, 'setDirty'):
            self.frameTimeLineFrame.setDirty(specificRID)

    def handleMpvFPSChange(self, value):
        """Called when mpv reports a new FPS value (mpv event thread)."""
        if QThread.currentThread() is not QApplication.instance().thread():
            QTimer.singleShot(0, lambda v=value: self.handleMpvFPSChange(v))
            return
        if hasattr(self.frameTimeLineFrame, 'setFrameRate'):
            self.frameTimeLineFrame.setFrameRate(value)

    def centerTimelineOnCurrentPosition(self):
        """Center the timeline view on the current playback position."""
        if hasattr(self.frameTimeLineFrame, 'centerOnCurrentPosition'):
            self.frameTimeLineFrame.centerOnCurrentPosition()

    # ------------------------------------------------------------------
    # Summary / progress labels
    # ------------------------------------------------------------------

    def updateSummary(self, filename, duration=None, videoParams=None, fps=None, estimatedFps=None):
        """Called from controller mpv duration/property callbacks (mpv event thread)."""
        if QThread.currentThread() is not QApplication.instance().thread():
            QTimer.singleShot(0, lambda fn=filename, d=duration, vp=videoParams, f=fps, ef=estimatedFps:
                              self.updateSummary(fn, d, vp, f, ef))
            return
        if filename is None:
            self.summaryLabel.setText('')
            return
        parts = [os.path.basename(str(filename))]
        if duration is not None:
            parts.append('{:.1f}s'.format(duration))
        if fps is not None:
            try:
                parts.append('{:.2f}fps'.format(float(fps)))
            except (TypeError, ValueError):
                pass
        self.summaryLabel.setText('  '.join(parts))

    def updateProgressStatitics(self, totalExTrim, totalTrim, fileCount, clipsLeft):
        try:
            total = totalExTrim + totalTrim
            pct = int(100 * totalExTrim / total) if total > 0 else 0
        except (TypeError, ZeroDivisionError):
            pct = 0
        self.progressBar.setValue(pct)
        self.statsLabel.setText(
            'Files: {}  Clips left: {}  Dur: {:.1f}s (+trim {:.1f}s)'.format(
                fileCount, clipsLeft, totalExTrim, totalTrim
            )
        )

    # ------------------------------------------------------------------
    # File / playback restart
    # ------------------------------------------------------------------

    def restartForNewFile(self, filename=None):
        """Called when a new file begins playing — reset timeline for the file."""
        self.frameTimeLineFrame.resetForNewFile()

    # ------------------------------------------------------------------
    # Drag + planner frames (stubs — planner is handled by controller/compose tab)
    # ------------------------------------------------------------------

    def showSlicePlanner(self):
        pass

    def forgetPlannerFrame(self):
        pass

    def getPlannerFrame(self):
        return self

    def toggleCompletedFrame(self):
        pass

    # ------------------------------------------------------------------
    # Dialog helpers
    # ------------------------------------------------------------------

    def confirmWithMessage(self, title, message, icon='warning', allowCancel=False):
        buttons = QMessageBox.Yes | QMessageBox.No
        if allowCancel:
            buttons |= QMessageBox.Cancel

        if icon == 'warning':
            icon_enum = QMessageBox.Warning
        elif icon == 'question':
            icon_enum = QMessageBox.Question
        else:
            icon_enum = QMessageBox.Information

        box = QMessageBox(icon_enum, title, message, buttons, self)
        result = box.exec()

        if result == QMessageBox.Yes:
            return 'yes'
        elif result == QMessageBox.No:
            return 'no'
        return None

    def askInteger(self, title, prompt, initialvalue=0):
        value, ok = QInputDialog.getInt(self, title, prompt, initialvalue)
        return value if ok else None

    def askFloat(self, title, prompt, initialvalue=0.0):
        value, ok = QInputDialog.getDouble(self, title, prompt, initialvalue, decimals=4)
        return value if ok else None

    # ------------------------------------------------------------------
    # Sub-clip / timeline selection helpers
    # ------------------------------------------------------------------

    def getCurrentlySelectedRegion(self):
        return self._currentRegionStart, self._currentRegionEnd

    def clearCurrentlySelectedRegion(self):
        self._currentRegionStart = None
        self._currentRegionEnd   = None

    def setCurrentlySelectedRegion(self, start, end):
        self._currentRegionStart = start
        self._currentRegionEnd   = end

    # ------------------------------------------------------------------
    # Sound wave / waveform generation (delegated to timeline widget)
    # ------------------------------------------------------------------

    def generateSoundWaveBackgrounds(self, style='GENERAL'):
        if hasattr(self.frameTimeLineFrame, 'generateSoundWaveBackgrounds'):
            self.frameTimeLineFrame.generateSoundWaveBackgrounds(style=style)

    # ------------------------------------------------------------------
    # Add sub-clip by text range modal
    # ------------------------------------------------------------------

    def addSubclipByTextRange(self, controller, totalDuration):
        start, ok1 = QInputDialog.getDouble(
            self, 'Add subclip', 'Start time (seconds):', 0.0, 0.0, totalDuration, 4
        )
        if not ok1:
            return
        end, ok2 = QInputDialog.getDouble(
            self, 'Add subclip', 'End time (seconds):', min(start + 10.0, totalDuration),
            start, totalDuration, 4
        )
        if ok2 and end > start:
            controller.addNewSubclip(start, end)

    # ------------------------------------------------------------------
    # Loop-search and VAD modals (delegated — full modal not yet ported)
    # ------------------------------------------------------------------

    def displayLoopSearchModal(self, useRange=False, rangeStart=None, rangeEnd=None):
        pass

    def displayrunVoiceActivityDetectionmodal(self):
        pass

    # ------------------------------------------------------------------
    # Drag offset
    # ------------------------------------------------------------------

    def setDragDur(self, dur):
        try:
            self.dragOffsetSpin.setValue(float(dur))
        except (TypeError, ValueError):
            pass

    # ------------------------------------------------------------------
    # Destruction
    # ------------------------------------------------------------------

    def destroy(self):
        try:
            super().deleteLater()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Fallback: simple text-only file list item when disableFileWidgets=True
# ---------------------------------------------------------------------------

class _SimpleFileListItem(QLabel):
    def __init__(self, filepath, controller, parent=None):
        super().__init__(os.path.basename(filepath), parent)
        self.filepath = filepath
        self.controller = controller
        self.setStyleSheet('color: #cccccc; font-size: 11px; padding: 2px; background: #2a2a2a;')
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.controller:
            self.controller.playVideoFile(self.filepath, 0)
        super().mousePressEvent(event)

