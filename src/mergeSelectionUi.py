"""
MergeSelectionUi - PySide6 placeholder stub for Phase 1.
Full implementation will replace this in Phase 4.
"""

from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel


class MergeSelectionUi(QWidget):

  def __init__(self, master=None, defaultProfile='None', globalOptions=None, *args, **kwargs):
    super().__init__()

    self.controller = None
    self.defaultProfile = defaultProfile
    self.globalOptions = globalOptions or {}

    layout = QVBoxLayout(self)
    layout.addWidget(QLabel('Merge Selection (placeholder - PySide6 migration in progress)'))

  def setController(self, controller):
    self.controller = controller

  def tabSwitched(self, tabName):
    pass

  def videoSubclipDurationChangeCallback(self, *args, **kwargs):
    pass

  def updateSelectableVideos(self):
    pass

  def clearSequence(self, includeProgress=True):
    pass

  def addAllClipsInTimelineOrder(self, minrid=-1, clearProgress=True):
    return minrid

  def encodeCurrent(self):
    pass

  def previewSequencetimings(self, uiParent=None):
    pass

  def destroyPlannerModal(self):
    pass

  def toggleBoringMode(self, boringMode):
    pass

  def close_ui(self):
    pass
