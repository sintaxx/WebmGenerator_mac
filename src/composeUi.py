from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel


class ComposeUi(QWidget):

  def __init__(self, master=None, defaultProfile='None', *args, **kwargs):
    super().__init__()

    self.controller = None
    self.defaultProfile = defaultProfile

    layout = QVBoxLayout(self)
    layout.addWidget(QLabel('Compose (placeholder)'))

  def setController(self, controller):
    self.controller = controller

  def tabSwitched(self, tabName):
    pass
