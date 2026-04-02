"""
CutselectionUi - PySide6 placeholder stub for Phase 1.
Full implementation will replace this in Phase 2.
"""

from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QFileDialog, QMessageBox, QInputDialog
from PySide6.QtCore import QTimer

import os
import logging


class CutselectionUi(QWidget):

  def __init__(self, master=None, globalOptions=None, *args, **kwargs):
    super().__init__()

    self.controller = None
    self.globalOptions = globalOptions or {}
    self.disableFileWidgets = False
    self.targetTrimVar = _StubVar('0')

    layout = QVBoxLayout(self)
    self._placeholder = QLabel('Cut Selection (placeholder - PySide6 migration in progress)')
    layout.addWidget(self._placeholder)

    # Stub attributes that controllers/other code reference
    self.frameTimeLineFrame = _StubTimelineFrame()

  # === Methods called by the main controller ===

  def setController(self, controller):
    self.controller = controller

  def tabSwitched(self, tabName):
    pass

  def showSlicePlanner(self):
    pass

  def forgetPlannerFrame(self):
    pass

  def getPlannerFrame(self):
    return self

  def toggleCompletedFrame(self):
    pass

  def confirmWithMessage(self, title, message, icon='warning', allowCancel=False):
    buttons = QMessageBox.Yes | QMessageBox.No
    if allowCancel:
      buttons |= QMessageBox.Cancel
    result = QMessageBox.question(self, title, message, buttons)
    if result == QMessageBox.Yes:
      return 'yes'
    elif result == QMessageBox.No:
      return 'no'
    return None

  def clearVideoMousePress(self):
    pass

  def loadVideoFiles(self):
    filenames, _ = QFileDialog.getOpenFileNames(
      self, 'Load Video Files', '',
      'Video Files (*.mp4 *.webm *.mkv *.avi *.mov *.flv *.wmv);;All Files (*)'
    )
    if filenames and self.controller:
      self.controller.loadFiles(filenames)

  def loadClipboardUrls(self):
    pass

  def loadVideoYTdl(self):
    pass

  def startScreencap(self, captureType='gdigrab'):
    pass

  def loadImageFile(self):
    pass

  def updateProgressPreview(self, data):
    pass

  # === Methods called by CutselectionController ===

  def setinitialFocus(self):
    pass

  def after(self, ms, callback, *args):
    """Tk .after() compatibility - use QTimer."""
    QTimer.singleShot(ms, lambda: callback(*args) if args else callback())

  def setDragDur(self, dur):
    pass

  def askInteger(self, title, prompt, initialvalue=0):
    value, ok = QInputDialog.getInt(self, title, prompt, initialvalue)
    return value if ok else None

  def askFloat(self, title, prompt, initialvalue=0.0):
    value, ok = QInputDialog.getDouble(self, title, prompt, initialvalue)
    return value if ok else None

  def setUiDirtyFlag(self, specificRID=None, withLock=False):
    pass

  def addSubclipByTextRange(self, controller, totalDuration):
    pass

  def generateSoundWaveBackgrounds(self, style='GENERAL'):
    pass

  def updateSummary(self, *args, **kwargs):
    pass

  def updateFileListing(self, files):
    pass

  def restartForNewFile(self, filename=None):
    pass

  def getPlayerFrameWid(self):
    return int(self.winId())

  def destroy(self):
    pass

  def setPausedStatus(self, value):
    pass

  def update(self, withLock=False):
    pass

  def handleMpvFPSChange(self, value):
    pass

  def centerTimelineOnCurrentPosition(self):
    pass

  def updateViewPreviewFrame(self, requestId, responseImage):
    pass

  def updateProgressStatitics(self, totalExTrim, totalTrim, fileCount, clipsLeft):
    pass

  def getCurrentlySelectedRegion(self):
    return None, None

  def clearCurrentlySelectedRegion(self):
    pass

  def displayLoopSearchModal(self, useRange=False, rangeStart=None, rangeEnd=None):
    pass

  def displayrunVoiceActivityDetectionmodal(self):
    pass

  def removefileIfLoaded(self, path):
    pass


class _StubVar:
  """Minimal stub for Tk StringVar compatibility."""
  def __init__(self, value=''):
    self._value = value

  def get(self):
    return self._value

  def set(self, value):
    self._value = value


class _StubTimelineFrame:
  """Minimal stub for timeline frame references."""
  def resetForNewFile(self):
    pass
