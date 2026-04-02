#!/usr/bin/env python3

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QTabWidget,
    QMenuBar, QMenu, QLabel, QPushButton, QProgressBar, QScrollArea,
    QCheckBox, QFileDialog, QMessageBox, QApplication
)
from PySide6.QtGui import QAction, QPixmap, QImage, QIcon, QKeySequence
from PySide6.QtCore import Qt, QTimer, Signal, QMimeData, QUrl

import webbrowser
import sys
import logging
import urllib.request
import json
import threading
import time
import os
import psutil
import random
from math import sin, cos, floor
from .platformUtils import is_windows
import colorsys
import numpy as np

RELEASE_NUMVER = 'v3.42.0'


def load_stylesheet(dark_mode=False):
    """Load the appropriate QSS stylesheet."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    qss_file = 'style_dark.qss' if dark_mode else 'style.qss'
    qss_path = os.path.join(script_dir, qss_file)
    try:
        with open(qss_path, 'r') as f:
            return f.read()
    except Exception as e:
        logging.error(f"Failed to load stylesheet {qss_path}", exc_info=e)
        return ''


class WebmGeneratorUi:

    def __init__(self, controller, mainWindow):

        self.controller = controller
        self.mainWindow = mainWindow
        self.completed = []
        self.completedimgs = []
        self.completedPixmaps = []
        self.completeFrame = False
        self.panes = []

        darkMode = self.controller.globalOptions.get('darkMode', False)

        # Apply stylesheet
        app = QApplication.instance()
        app.setStyleSheet(load_stylesheet(darkMode))

        self.mainWindow.setWindowTitle('WebmGenerator - ' + RELEASE_NUMVER)
        self.mainWindow.setMinimumSize(1024, 720)

        # Load icons
        self.iconLookup = {}
        try:
            for iconFileName in os.listdir(os.path.join("resources", "icons")):
                key = iconFileName[:-4]
                try:
                    icon_path = os.path.join("resources", "icons", f"{key}.png")
                    self.iconLookup[key] = QIcon(icon_path)
                except Exception as e:
                    print(e)
        except Exception as e:
            print(e)

        # Central widget and layout
        self.centralWidget = QWidget()
        self.mainWindow.setCentralWidget(self.centralWidget)
        self.mainLayout = QVBoxLayout(self.centralWidget)
        self.mainLayout.setContentsMargins(0, 0, 0, 0)
        self.mainLayout.setSpacing(0)

        # Main content area: tabs + completed sidebar
        self.contentWidget = QWidget()
        self.contentLayout = QHBoxLayout(self.contentWidget)
        self.contentLayout.setContentsMargins(0, 0, 0, 0)
        self.contentLayout.setSpacing(0)

        # Tab widget
        self.notebook = QTabWidget()
        self.notebook.currentChanged.connect(self._notebookSwitched)
        self.contentLayout.addWidget(self.notebook, stretch=1)

        # Completed encodes sidebar
        self.frameConverted = QWidget()
        self.frameConverted.setFixedWidth(0)
        self.frameConvertedLayout = QVBoxLayout(self.frameConverted)
        self.frameConvertedLayout.setContentsMargins(0, 0, 0, 0)

        self.scrolledConverted = QScrollArea()
        self.scrolledConverted.setWidgetResizable(True)
        self.scrolledConvertedInner = QWidget()
        self.scrolledConvertedInnerLayout = QVBoxLayout(self.scrolledConvertedInner)
        self.scrolledConvertedInnerLayout.setAlignment(Qt.AlignTop)

        self.completedLabel = QLabel('Completed Encodes')
        self.scrolledConvertedInnerLayout.addWidget(self.completedLabel)

        self.autoConvertCheck = QCheckBox('Auto convert')
        self.autoConvertCheck.stateChanged.connect(self.toggleAutoconvert)
        self.scrolledConvertedInnerLayout.addWidget(self.autoConvertCheck)

        self.scrolledConverted.setWidget(self.scrolledConvertedInner)
        self.frameConvertedLayout.addWidget(self.scrolledConverted)

        self.contentLayout.addWidget(self.frameConverted)

        self.mainLayout.addWidget(self.contentWidget, stretch=1)

        # Status bar frame
        self.statusFrame = QWidget()
        self.statusFrame.setFixedHeight(28)
        self.statusLayout = QHBoxLayout(self.statusFrame)
        self.statusLayout.setContentsMargins(2, 0, 2, 0)
        self.statusLayout.setSpacing(4)

        self.statusCancel = QPushButton('Stop')
        self.statusCancel.setEnabled(False)
        self.statusCancel.clicked.connect(self.cancelAction)
        self.statusCancel.setFixedWidth(40)
        self.statusLayout.addWidget(self.statusCancel)

        self.statusSplit = QPushButton('Split')
        self.statusSplit.setEnabled(False)
        self.statusSplit.clicked.connect(self.splitStream)
        self.statusSplit.setFixedWidth(40)
        self.statusLayout.addWidget(self.statusSplit)

        self.statusPreview = QPushButton('Preview')
        self.statusPreview.setEnabled(False)
        self.statusPreview.clicked.connect(self.togglePreview)
        self.statusLayout.addWidget(self.statusPreview)

        self.statusLabel = QLabel('Idle no background task')
        self.statusLayout.addWidget(self.statusLabel, stretch=1)

        self.statusProgress = QProgressBar()
        self.statusProgress.setValue(0)
        self.statusProgress.setObjectName('greenProgress')
        self.statusLayout.addWidget(self.statusProgress, stretch=1)

        self.mainLayout.addWidget(self.statusFrame)

        # Fullscreen state
        self.fullscreen = self.controller.globalOptions.get('startFullscreen', False)
        if self.fullscreen:
            self.mainWindow.showFullScreen()

        # Build menus
        self._buildMenuBar()

        # Free space monitoring thread
        def checkFreeSpaceWorker():
            try:
                while True:
                    drive, _ = os.path.splitdrive(
                        os.path.abspath(self.controller.globalOptions.get('tempFolder'))
                    )
                    usage = psutil.disk_usage('/')
                    label = "{drive} {percent:.1f}% free ({freespace}) ".format(
                        drive=drive,
                        freespace=self.sizeof_fmt(usage.free),
                        percent=100 - usage.percent
                    )
                    # Update menu from main thread
                    QTimer.singleShot(0, lambda l=label: self._updateFreeSpaceLabel(l))
                    time.sleep(60)
            except Exception as e:
                print(e)

        self.freeSpaceThread = threading.Thread(target=checkFreeSpaceWorker, daemon=True)
        self.freeSpaceThread.start()

        # Boring mode attractor state
        self.boringw = 1000
        self.boringh = 600
        self.boringscale = 1
        self.boringSteps = 1
        self.boringMode = False
        self.boringFloats = np.array([[0] * self.boringw for _ in range(self.boringh)])
        self.boringImageArray = bytearray([0] * (self.boringw * self.boringh * 3))
        self.boringMax = 0.0
        self.boringText = QLabel('')
        self.boringText.setObjectName('boringMode')
        self.boringText.setCursor(Qt.CrossCursor)
        self.boringText.setAlignment(Qt.AlignCenter)
        self.boringText.mousePressEvent = self._boringMousePress
        self.boringText.wheelEvent = self._boringWheelEvent
        self.boringText.hide()
        self.mainLayout.addWidget(self.boringText)

        self.algorithms = {
            'Clifford Attractor': (
                lambda x, y, a, b, c, d: sin(a * y) + c * cos(a * x),
                lambda x, y, a, b, c, d: sin(b * x) + d * cos(b * y), 8, 8),
            'Jason Rampe 1': (
                lambda x, y, a, b, c, d: cos(y * b) + c * sin(x * b),
                lambda x, y, a, b, c, d: cos(x * a) + d * sin(y * a), 8, 8),
            'Jason Rampe 2': (
                lambda x, y, a, b, c, d: cos(y * b) + c * cos(x * b),
                lambda x, y, a, b, c, d: cos(x * a) + d * cos(y * a), 8, 8),
            'Jason Rampe 3': (
                lambda x, y, a, b, c, d: sin(y * b) + c * cos(x * b),
                lambda x, y, a, b, c, d: cos(x * a) + d * sin(y * a), 8, 8),
            'Johnny Svensson Attractor': (
                lambda x, y, a, b, c, d: d * sin(x * a) - sin(y * b),
                lambda x, y, a, b, c, d: c * cos(x * a) + cos(y * b), 8, 8),
            'Peter DeJong Attractor': (
                lambda x, y, a, b, c, d: sin(y * a) - cos(x * b),
                lambda x, y, a, b, c, d: sin(x * c) - cos(y * d), 6, 6),
        }

        self.algo = 'Peter DeJong Attractor'
        self.boringi = 0
        self.boringx = 0.1
        self.boringy = 0.1
        self.lastboringx = 0.1
        self.lastboringy = 0.1
        self.boringa = 0.0
        self.boringb = 0.0
        self.boringc = 0.0
        self.boringd = 0.0
        self.tonemapScale = 1000

        self.versioncheckResultIndex = None
        self.alwaysOnTop = False
        self.dropLabel = None
        self.dropAbort = None
        self.showStreamPreviews = False

    def _buildMenuBar(self):
        """Build the menu bar with all menus."""
        menubar = self.mainWindow.menuBar()

        # === File menu ===
        self.filemenu = menubar.addMenu('File')
        self.filemenu.aboutToShow.connect(self.updateDownloadCounts)

        self._addAction(self.filemenu, "New Project", self.newProject, 'folder-add-file')
        self._addAction(self.filemenu, "Open Project", self.openProject, 'folder-file-project')
        self._addAction(self.filemenu, "Save Project", self.saveProject, 'save')

        self.recentProjectsMenu = self.filemenu.addMenu('Recent Projects')
        if 'clock-time' in self.iconLookup:
            self.recentProjectsMenu.setIcon(self.iconLookup['clock-time'])

        self.filemenu.addSeparator()

        self.loadAutosaveAction = self._addAction(
            self.filemenu, "Load last autosave", self.controller.loadAutoSave, 'redo'
        )
        self.loadAutosaveAction.setEnabled(self.controller.autoSaveExists())

        self.filemenu.addSeparator()
        self._addAction(self.filemenu, "Load Video from File", self.loadVideoFiles, 'media-film-video')

        self.recentlyPlayedMenu = self.filemenu.addMenu('Recently Played')
        if 'clock-time' in self.iconLookup:
            self.recentlyPlayedMenu.setIcon(self.iconLookup['clock-time'])

        self._addAction(self.filemenu, "Load Video from youtube-dlp supported url", self.loadVideoYTdl, 'youtube-video')
        self._addAction(self.filemenu, "Load Image as static video", self.loadImageFile, 'photo-image-picture')

        self.filemenu.addSeparator()

        # Screen capture submenu (Windows only)
        if is_windows():
            captureMenu = self.filemenu.addMenu('Screen capture')
            if 'video-camera-media' in self.iconLookup:
                captureMenu.setIcon(self.iconLookup['video-camera-media'])
            captureMenu.addAction("Start GDI screengrabber + cpu screen capture", self.startScreencap_gdigrab)
            captureMenu.addAction("Start GDI screengrabber + nvenc screen capture", self.startScreencap_gdigrab_nvenc)
            captureMenu.addAction("Start Desktop Duplication API + nvenc screen capture", self.startScreencap_ddagrab)

        self.filemenu.addSeparator()
        self._addAction(self.filemenu, "Extract .srt subtitles from video file", self.extractSubs, 'alphabet-s')

        self.filemenu.addSeparator()
        self._addAction(self.filemenu, "Toggle fullscreen", self.toggleFullscreen, 'maximize-expand')

        self.filemenu.addSeparator()
        self._addAction(self.filemenu, "Watch clipboard and automatically add urls", self.loadClipboardUrls, 'copy-duplicate')

        self.filemenu.addSeparator()
        self.filemenu.addAction("Cancel current youtube-dlp download", self.cancelCurrentYoutubeDl)
        self.filemenu.addAction("Update youtube-dlp", self.updateYoutubeDl)

        self.filemenu.addSeparator()
        self.clearDownloadsAction = self.filemenu.addAction("Delete all downloaded files", self.clearDownloadedfiles)

        self.filemenu.addSeparator()
        self.filemenu.addAction("Preferences", self.updatePreferences)

        self.alwaysOnTopAction = self.filemenu.addAction("Enable Always On Top", self.toggleAlwaysOntop)

        self.filemenu.addSeparator()
        self.filemenu.addAction("Exit", self.exitProject)

        # === Commands menu ===
        self.commandmenu = menubar.addMenu('Commands')

        # Split clip submenu
        splitMenu = self.commandmenu.addMenu('Split clip')
        splitMenu.addAction("Split clip into n equal Subclips", self.splitClipIntoNEqualSections)
        splitMenu.addAction("Split clip into subclips of n seconds", self.splitClipIntoSectionsOfLengthN)
        splitMenu.addSeparator()
        splitMenu.addAction("Fill gaps between subclips", self.fillGapsBetweenSublcips)

        # Content detectors submenu
        detectMenu = self.commandmenu.addMenu('Content detectors')
        detectMenu.addAction("Run scene change detection and add Marks", self.controller.runSceneChangeDetection)
        detectMenu.addAction("Run scene change detection and add SubClips", self.controller.runSceneChangeDetectionCuts)
        detectMenu.addSeparator()
        a = detectMenu.addAction("Run search for any perfect loops.", self.controller.runFullLoopSearch)
        detectMenu.addSeparator()
        sceneCentreAction = detectMenu.addAction("Run representative scene centeres detection and add SubClips", self.controller.runSceneCentreDetectionCuts)
        sceneCentreAction.setEnabled(False)
        detectMenu.addSeparator()
        detectMenu.addAction("Run audio loudness threshold detection", self.scanAndAddLoudSections)
        detectMenu.addAction("Run voice activity detection", self.controller.runVoiceActivityDetection)

        # Audio spectra submenu
        spectraMenu = self.commandmenu.addMenu('Audio spectra')
        spectraMenu.addAction("Generate general audio spectra", self.generateSoundWaveBackgrounds)

        voiceModelEnabled = os.path.exists(os.path.join('resources', 'voiceModel', 'model.rnnn'))
        voiceAction = spectraMenu.addAction("Generate voice spectra", self.generateSoundVoiceBackgrounds)
        voiceAction.setEnabled(voiceModelEnabled)

        speechModelEnabled = os.path.exists(os.path.join('resources', 'speechModel', 'model.rnnn'))
        speechAction = spectraMenu.addAction("Generate speech spectra", self.generateSoundSpeechBackgrounds)
        speechAction.setEnabled(speechModelEnabled)

        self.commandmenu.addSeparator()
        self.commandmenu.addAction("Clear all SubClips on current clip", self.clearAllSubclipsOnCurrentClip)
        self.commandmenu.addAction("Clear all Interest Marks on current clip", self.clearAllInterestMarksOnCurrentClip)
        self.commandmenu.addSeparator()
        self.commandmenu.addAction(
            f"Screenshot to {self.controller.tempFolder}", self.takeScreenshot
        )
        self.commandmenu.addSeparator()
        self.commandmenu.addAction("Add subclip by text range", self.addSubclipByTextRange)
        self.commandmenu.addSeparator()

        seqAction = self._addAction(self.commandmenu, "Show sequence editor", self.showSequencePreview, 'more-horizontal')
        sliceAction = self.commandmenu.addAction("Show audio slice planner", self.showSlicePlanner)
        sliceAction.setEnabled(False)

        self.commandmenu.addSeparator()
        self._addAction(self.commandmenu, "Toggle completed frame", self.toggleCompletedFrame, 'chevrons-right-arrows')

        # === Help menu ===
        self.helpmenu = menubar.addMenu('Help')
        self.helpmenu.addAction(f"{RELEASE_NUMVER} - Check for new version", self.versioncheck)
        self.helpmenu.addAction("Open Documentation", self.openDocs)

        # Free space label in menu bar
        self.freeSpaceAction = menubar.addAction("Checking free space...")
        self.freeSpaceAction.setEnabled(False)

    def _addAction(self, menu, label, callback, icon_key=None):
        """Helper to add a menu action with optional icon."""
        if icon_key and icon_key in self.iconLookup:
            action = menu.addAction(self.iconLookup[icon_key], label, callback)
        else:
            action = menu.addAction(label, callback)
        return action

    def _updateFreeSpaceLabel(self, label):
        """Update the free space label in the menu bar (called from main thread)."""
        self.freeSpaceAction.setText(label)

    # === Completed encodes sidebar ===

    def toggleCompletedFrame(self):
        self.completeFrame = not self.completeFrame
        if self.completeFrame:
            self.frameConverted.setFixedWidth(150)
        else:
            self.frameConverted.setFixedWidth(0)

    def registerComplete(self, label, filename, img=None):
        try:
            completedWidget = QLabel(label)
            completedWidget.setWordWrap(True)
            completedWidget.setFixedWidth(130)
            completedWidget.setStyleSheet("border: 1px solid grey; padding: 4px;")
            completedWidget.setCursor(Qt.OpenHandCursor)

            if img is not None:
                try:
                    self.completedPixmaps.append(img)
                except Exception as imgE:
                    print(imgE)

            # Enable drag from completed items
            def startDrag(event, fn=filename):
                from PySide6.QtGui import QDrag
                drag = QDrag(completedWidget)
                mime = QMimeData()
                mime.setUrls([QUrl.fromLocalFile(fn)])
                drag.setMimeData(mime)
                drag.exec(Qt.CopyAction)

            completedWidget.mouseMoveEvent = startDrag

            # Right-click to remove
            def removeComplete(event, w=completedWidget):
                w.setParent(None)
                w.deleteLater()

            completedWidget.setContextMenuPolicy(Qt.CustomContextMenu)
            completedWidget.customContextMenuRequested.connect(
                lambda pos, w=completedWidget: removeComplete(None, w)
            )

            self.scrolledConvertedInnerLayout.addWidget(completedWidget)
            self.completed.append(completedWidget)
        except Exception as oe:
            print(oe)

    def toggleAutoconvert(self, state):
        self.controller.setAutoConvert(state == Qt.Checked.value if hasattr(Qt.Checked, 'value') else state == 2)

    # === Recent menus ===

    def updateRecentProjects(self):
        self.recentProjectsMenu.clear()
        for fn in self.controller.getRecentProjects():
            self.recentProjectsMenu.addAction(fn, lambda f=fn: self.controller.openProject(f))

    def updateRecentlyPlayed(self):
        self.recentlyPlayedMenu.clear()
        for fn in self.controller.getRecentlyPlayed():
            self.recentlyPlayedMenu.addAction(
                fn, lambda f=fn: self.controller.cutselectionController.loadFiles([f])
            )

    # === Always on top ===

    def toggleAlwaysOntop(self):
        self.alwaysOnTop = not self.alwaysOnTop
        if self.alwaysOnTop:
            self.alwaysOnTopAction.setText("Disable Always On Top")
            self.mainWindow.setWindowFlags(
                self.mainWindow.windowFlags() | Qt.WindowStaysOnTopHint
            )
        else:
            self.alwaysOnTopAction.setText("Enable Always On Top")
            self.mainWindow.setWindowFlags(
                self.mainWindow.windowFlags() & ~Qt.WindowStaysOnTopHint
            )
        self.mainWindow.show()

    # === File load options modal ===

    def getFileLoadOptions(self):
        from .modalWindows import AdvancedDropModal
        data = {}
        modal = AdvancedDropModal(self.mainWindow, dataDestination=data)
        modal.exec()
        print(data)
        return data

    def setIgnoreDrop(self, path):
        self.controller.setIgnoreDrop(path)

    # === Drop overlay ===

    def setLoadLabel(self, text):
        try:
            if self.dropLabel:
                self.dropLabel.setText(text)
                self.dropLabel.setStyleSheet(
                    "background-color: #282828; color: #69bfdb; font-size: 20pt;"
                )
        except Exception as e:
            print(e)

    def showDrop(self, n=None):
        if n is None:
            text = 'Drop to load\nHold CTRL for advanced options.'
        else:
            text = f'Drop to load {n} paths\nHold CTRL for advanced options.'

        self.dropLabel = QLabel(text, self.mainWindow.centralWidget())
        self.dropLabel.setObjectName('dropMessage')
        self.dropLabel.setAlignment(Qt.AlignCenter)
        self.dropLabel.setGeometry(self.mainWindow.centralWidget().rect())
        self.dropLabel.show()
        self.dropLabel.raise_()

        self.dropAbort = QPushButton('Cancel Load', self.mainWindow.centralWidget())
        self.dropAbort.setObjectName('abortLoad')
        self.dropAbort.clicked.connect(self.abortLoad)
        geom = self.mainWindow.centralWidget().rect()
        self.dropAbort.setGeometry(0, int(geom.height() * 0.95), geom.width(), int(geom.height() * 0.05))
        self.dropAbort.show()
        self.dropAbort.raise_()

    def hideDrop(self):
        try:
            if self.dropLabel:
                self.dropLabel.hide()
                self.dropLabel.deleteLater()
                self.dropLabel = None
        except Exception as e:
            print('pre-label-creation', e)

        try:
            if self.dropAbort:
                self.dropAbort.hide()
                self.dropAbort.deleteLater()
                self.dropAbort = None
        except Exception as e:
            print('pre-label-creation', e)

    def abortLoad(self):
        self.controller.abortCurrentLoad()

    # === Misc delegations ===

    def showSlicePlanner(self):
        self.controller.showSlicePlanner()

    def showSequencePreview(self):
        self.controller.showSequencePreview()

    # === Boring mode attractor ===

    def _boringMousePress(self, event):
        self.resetBoringText()

    def _boringWheelEvent(self, event):
        ctrl = event.modifiers() & Qt.ControlModifier
        if ctrl:
            alist = sorted(self.algorithms.keys())
            algind = (alist.index(self.algo) + 1) % len(alist)
            self.algo = alist[algind]
            self.resetBoringText()
        else:
            delta = event.angleDelta().y()
            if delta > 0:
                self.tonemapScale -= 125
            else:
                self.tonemapScale += 125
            self.tonemapScale = max(0, self.tonemapScale)

    def regenerateBoringText(self):
        E_map = np.empty_like(self.boringFloats, dtype='float32')
        E_min = self.boringFloats.min()
        E_max = self.boringFloats.max()
        if E_max != E_min:
            E_map = ((self.boringFloats - E_min) / (E_max - E_min)) * self.tonemapScale
        else:
            E_map = np.zeros_like(self.boringFloats, dtype='float32')

        tonemap = np.clip(E_map, 0.0, 255.0).astype('uint8')
        rgb_data = np.dstack([tonemap, tonemap, tonemap]).flatten()

        # Create QImage from numpy array
        img = QImage(rgb_data.data, self.boringw, self.boringh, self.boringw * 3, QImage.Format_RGB888)
        pixmap = QPixmap.fromImage(img)

        boringTextContent = ''
        boringTextContent += '\nBoring "{}" Strange Attractor Render\n'.format(self.algo)
        boringTextContent += 'CLICK TO RANDOMISE ATTRACTOR, SCROLL TO ADJUST WHITEPOINT, CTRL-SCROLL to switch algorithms\n'
        boringTextContent += 'PRESS CTRL-N TO RESET WMG - CHANGES WILL BE LOST\n'
        boringTextContent += 'PRESS CTRL-B TO RESTORE WMG - WILL RESTORE LAST WORKING SESSION\n'
        boringTextContent += 'x:{:+0.5f} y:{:+0.4f} frozenSteps:{:03d} whitepoint:{}\na:{:0.5f} b:{:0.5f} c:{:0.5f} d:{:0.5f}\n'.format(
            self.boringx, self.boringy, self.boringi, self.tonemapScale,
            self.boringa, self.boringb, self.boringc, self.boringd
        )

        if self.boringi > 10:
            self.resetBoringText()

        self.boringImageArray = bytearray(rgb_data.tobytes())

        self.boringText.setPixmap(pixmap)
        self.boringText.setText(boringTextContent)

        self.boringSteps = min(self.boringSteps + 10, 1530)

        algx, algy, xscale, yscale = self.algorithms[self.algo]

        for i in range(self.boringSteps):
            xnew = algx(self.boringx, self.boringy, self.boringa, self.boringb, self.boringc, self.boringd)
            ynew = algy(self.boringx, self.boringy, self.boringa, self.boringb, self.boringc, self.boringd)

            self.boringx = xnew
            self.boringy = ynew

            xnew_i = int((self.boringw // 2) + (xnew * (self.boringw / xscale)))
            ynew_i = int((self.boringh // 2) + (ynew * (self.boringh / yscale)))

            if 0 < xnew_i < self.boringw and 0 < ynew_i < self.boringh:
                newVal = self.boringFloats[ynew_i][xnew_i] + 1
                self.boringMax = max(self.boringMax, newVal)
                self.boringFloats[ynew_i][xnew_i] = newVal
            else:
                self.boringi += 1

            self.lastboringx = xnew_i
            self.lastboringy = ynew_i

        if self.boringMode:
            QTimer.singleShot(100, self.regenerateBoringText)

    def resetBoringText(self, e=None):
        self.boringi = 0
        self.boringx = 0.1
        self.boringy = 0.1
        self.boringa = random.uniform(-3, 3)
        self.boringb = random.uniform(-3, 3)
        self.boringc = random.uniform(-3, 3)
        self.boringd = random.uniform(-3, 3)
        self.boringFloats = np.array([[0] * self.boringw for _ in range(self.boringh)])
        self.boringImageArray = bytearray([0] * (self.boringw * self.boringh * 3))
        self.boringMax = 0.0
        self.boringSteps = 1

    # === Preferences / modals ===

    def updatePreferences(self):
        from .modalWindows import OptionsDialog
        changedOptions = {}
        optionsScreen = OptionsDialog(
            parent=self.mainWindow,
            optionsDict=self.controller.globalOptions.copy(),
            changedProperties=changedOptions,
            changeCallback=self.controller.updateGlobalOptions
        )
        optionsScreen.exec()

    def extractSubs(self):
        from .modalWindows import SubtitleExtractionModal
        subsScreen = SubtitleExtractionModal(parent=self.mainWindow)
        subsScreen.exec()

    # === Utilities ===

    def sizeof_fmt(self, num, suffix="B"):
        for unit in ["", "Ki", "Mi", "Gi", "Ti", "Pi", "Ei", "Zi"]:
            if abs(num) < 1024.0:
                return f"{num:3.1f}{unit}{suffix}"
            num /= 1024.0
        return f"{num:.1f}Yi{suffix}"

    # === Version check ===

    def versioncheck(self):
        try:
            req = urllib.request.Request('https://api.github.com/repos/dfaker/WebmGenerator/releases')
            req.add_header('Referer', 'http://localhost/dfaker/WebmGenerator/updateCheck')
            with urllib.request.urlopen(req) as f:
                data = json.loads(f.read())
                leadTag = data[0]['tag_name']
                menubar = self.mainWindow.menuBar()
                if self.versioncheckResultIndex is not None:
                    menubar.removeAction(self.versioncheckResultIndex)

                if leadTag != RELEASE_NUMVER:
                    self.versioncheckResultIndex = menubar.addAction(
                        f"New Version {leadTag} available!", self.gotoReleasesPage
                    )
                else:
                    self.versioncheckResultIndex = menubar.addAction(
                        f"You're on the most recent version {leadTag}", self.gotoReleasesPage
                    )
        except Exception as e:
            logging.error('versioncheck', exc_info=e)
            menubar = self.mainWindow.menuBar()
            if self.versioncheckResultIndex is not None:
                menubar.removeAction(self.versioncheckResultIndex)
            self.versioncheckResultIndex = menubar.addAction(
                "Version check failed!", self.gotoReleasesPage
            )

    def updateDownloadCounts(self):
        count, sz = self.controller.getDownloadFilesCountAndsize()
        if count == 0:
            self.clearDownloadsAction.setText("Delete all downloaded files (Downloads empty)")
            self.clearDownloadsAction.setEnabled(False)
        else:
            self.clearDownloadsAction.setText(
                f"Delete all downloaded files ({count} files {self.sizeof_fmt(sz)})"
            )
            self.clearDownloadsAction.setEnabled(True)

    # === Fullscreen ===

    def toggleFullscreen(self):
        self.fullscreen = not self.fullscreen
        if self.fullscreen:
            self.mainWindow.showFullScreen()
        else:
            self.mainWindow.showNormal()

    # === Help / boring mode toggle ===

    def toggleHelpFor(self, tabName):
        self.toggleBoringMode()

    def getTabName(self):
        idx = self.notebook.currentIndex()
        if 0 <= idx < len(self.panes):
            return str(self.panes[idx].__class__.__name__).lower()
        return ''

    # === Actions delegated to controller ===

    def takeScreenshot(self):
        tabName = self.getTabName()
        self.controller.takeScreenshotToFile(tabName)

    def fillGapsBetweenSublcips(self):
        self.controller.fillGapsBetweenSublcips()

    def splitClipIntoNEqualSections(self):
        self.controller.splitClipIntoNEqualSections()

    def scanAndAddLoudSections(self):
        self.controller.scanAndAddLoudSections()

    def splitClipIntoSectionsOfLengthN(self):
        self.controller.splitClipIntoSectionsOfLengthN()

    def generateSoundVoiceBackgrounds(self):
        self.controller.generateSoundWaveBackgrounds(style='VOICE')

    def generateSoundSpeechBackgrounds(self):
        self.controller.generateSoundWaveBackgrounds(style='SPEECH')

    def generateSoundWaveBackgrounds(self):
        self.controller.generateSoundWaveBackgrounds(style='GENERAL')

    def clearAllSubclipsOnCurrentClip(self):
        self.controller.clearAllSubclipsOnCurrentClip()

    def clearAllInterestMarksOnCurrentClip(self):
        self.controller.clearAllInterestMarksOnCurrentClip()

    def addSubclipByTextRange(self):
        self.controller.addSubclipByTextRange()

    def gotoReleasesPage(self):
        webbrowser.open('https://github.com/dfaker/WebmGenerator/releases', new=2)

    def loadVideoFiles(self):
        self.controller.cutselectionUi.loadVideoFiles()

    def loadClipboardUrls(self):
        self.controller.cutselectionUi.loadClipboardUrls()

    def cancelCurrentYoutubeDl(self):
        self.controller.cancelCurrentYoutubeDl()

    def clearDownloadedfiles(self):
        self.controller.clearDownloadedfiles()
        self.updateDownloadCounts()

    def loadVideoYTdl(self):
        self.controller.cutselectionUi.loadVideoYTdl()

    def startScreencap_gdigrab_nvenc(self):
        self.controller.cutselectionUi.startScreencap(captureType='gdigrab_nvenc')

    def startScreencap_gdigrab(self):
        self.controller.cutselectionUi.startScreencap(captureType='gdigrab')

    def startScreencap_ddagrab(self):
        self.controller.cutselectionUi.startScreencap(captureType='ddagrab')

    def loadImageFile(self):
        self.controller.cutselectionUi.loadImageFile()

    # === Tab management ===

    def switchTab(self, ind):
        self.notebook.setCurrentIndex(ind)

    def newProject(self):
        self.controller.newProject()
        self.notebook.setCurrentIndex(0)
        self.statusLabel.setText('Idle no background task')
        self.statusProgress.setValue(0)

    def openProject(self):
        filename, _ = QFileDialog.getOpenFileName(
            self.mainWindow,
            'Open WebmGenerator Project',
            '',
            'WebmGenerator Project (*.webgproj)'
        )
        if filename:
            self.controller.openProject(filename)
            self.controller.logProject(filename)

    def saveProject(self):
        filename, _ = QFileDialog.getSaveFileName(
            self.mainWindow,
            'Save WebmGenerator Project',
            '',
            'WebmGenerator Project (*.webgproj)'
        )
        if filename:
            if not filename.endswith('.webgproj'):
                filename = filename + '.webgproj'
            self.controller.saveProject(filename)
            self.controller.logProject(filename)

    def splitStream(self):
        self.controller.splitStream()

    def togglePreview(self):
        self.showStreamPreviews = not self.showStreamPreviews
        self.controller.toggleYTPreview(self.showStreamPreviews)

        if self.showStreamPreviews:
            self.controller.cutselectionUi.updateProgressPreview("P5\n220 130\n255\n" + ("127" * 220 * 130))
        else:
            self.controller.cutselectionUi.updateProgressPreview(None)

    def updateYoutubeDl(self):
        self.controller.updateYoutubeDl()

    def exitProject(self):
        self.controller.close_ui()
        sys.exit()

    def openDocs(self):
        webbrowser.open('https://github.com/dfaker/WebmGenerator/wiki', new=2)

    def cancelAction(self):
        self.controller.cancelCurrentYoutubeDl()
        self.statusCancel.setEnabled(False)

    # === Status bar updates ===

    def updateGlobalStatus(self, message, percentage, progressPreview=None):
        if progressPreview is not None and self.showStreamPreviews:
            self.controller.cutselectionUi.updateProgressPreview(progressPreview)
        elif message is not None and 'streaming' not in message:
            self.controller.cutselectionUi.updateProgressPreview(None)

        if message is not None:
            if 'streaming' in message:
                self.statusPreview.setEnabled(True)
                self.statusSplit.setEnabled(True)
            else:
                self.statusPreview.setEnabled(False)
                self.statusSplit.setEnabled(False)

            if 'Download progress' in message or 'streaming' in message or 'Running loop scan' in message:
                self.statusCancel.setEnabled(True)
            else:
                self.statusCancel.setEnabled(False)
            self.statusLabel.setText(message)

        if percentage is not None:
            if percentage < 0:
                self.statusProgress.setRange(0, 0)  # indeterminate
            else:
                self.statusProgress.setRange(0, 100)
                self.statusProgress.setValue(max(0, min(100, int(percentage * 100))))

    # === Pane / tab management ===

    def addPane(self, pane, name):
        self.panes.append(pane)
        self.notebook.addTab(pane, name)

    def _notebookSwitched(self, index):
        for pane in self.panes:
            pane.tabSwitched(index)

    # === Lifecycle ===

    def run(self):
        self.mainWindow.show()

    def close_ui(self):
        self.controller.cancelCurrentYoutubeDl()

    # === Boring mode ===

    def toggleBoringMode(self):
        self.boringMode = not self.boringMode

        if self.boringMode:
            self.controller.cutselectionController.setVolume(0)
            self.controller.filterSelectionController.setVolume(0)
            self.notebook.hide()
            self.statusFrame.hide()
            self.boringText.show()
            self.mainWindow.setWindowTitle('Boring Strange Attractor Renderer')
            self.mainWindow.menuBar().hide()
            self.resetBoringText()
            self.regenerateBoringText()
        else:
            self.boringText.hide()
            self.notebook.show()
            self.statusFrame.show()
            self.mainWindow.setWindowTitle('WebmGenerator')
            self.mainWindow.menuBar().show()
