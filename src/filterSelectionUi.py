"""
FilterSelectionUi - PySide6 placeholder stub for Phase 1.
Full implementation will replace this in Phase 3.
"""

from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel


class FilterSelectionUi(QWidget):

  def __init__(self, master=None, globalOptions=None, enableFaceDetection=False, *args, **kwargs):
    super().__init__()

    self.controller = None
    self.globalOptions = globalOptions or {}

    # Attributes accessed directly by filterSelectionController
    self.subClipOrder = []
    self.currentSubclipIndex = None
    self.subclips = {}

    layout = QVBoxLayout(self)
    layout.addWidget(QLabel('Filter Selection (placeholder - PySide6 migration in progress)'))

  def setController(self, controller):
    self.controller = controller

  def tabSwitched(self, tabName):
    pass

  def getPlayerFrameWid(self):
    return int(self.winId())

  def jumpToFilterByRid(self, rid):
    pass

  def filterFailure(self):
    pass

  def updateSeekPositionThousands(self, position, elapsed):
    pass

  def updateSeekLabel(self, elapsed):
    pass

  def setActiveTimeLineValue(self, value):
    pass

  def setVolume(self, volume):
    pass

  def close_ui(self):
    pass

  def takeScreenshotToFile(self, folder, includes='video'):
    pass
