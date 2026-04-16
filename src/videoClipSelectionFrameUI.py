
from PySide6.QtWidgets import QWidget, QLabel, QVBoxLayout
from PySide6.QtGui import QPixmap
from PySide6.QtCore import Qt

import datetime
import threading
from math import floor
import time
import logging
from threading import Lock


class VideoClipSelectionFrameUI(QWidget):

  def __init__(self, master, controller, globalOptions={}, *args, **kwargs):
    super().__init__(master)
    self.controller = controller
    self.globalOptions = globalOptions

    layout = QVBoxLayout(self)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)

    self.clip_canvas = QLabel(self)
    self.clip_canvas.setFixedSize(200, 200)
    self.clip_canvas.setStyleSheet('background-color: #1E1E1E;')
    self.clip_canvas.setAlignment(Qt.AlignCenter)
    layout.addWidget(self.clip_canvas)

    self.uiDirty = True

  def setPixmap(self, pixmap: QPixmap):
    """Display a preview image on the clip canvas."""
    self.clip_canvas.setPixmap(
      pixmap.scaled(self.clip_canvas.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
    )

if __name__ == '__main__':
  import webmGenerator
