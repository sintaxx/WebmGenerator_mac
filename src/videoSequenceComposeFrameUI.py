"""
VideoSequenceComposeFrameUI — PySide6 stub (Phase 5).
This class was unused in the original codebase; tkinter references removed.
"""

from PySide6.QtWidgets import QWidget
from PySide6.QtGui import QPainter, QColor


class VideoSequenceComposeFrameUI(QWidget):

    def __init__(self, parent=None, controller=None, globalOptions=None, *args, **kwargs):
        super().__init__(parent)
        self.controller = controller
        self.globalOptions = globalOptions or {}
        self.uiDirty = True
        self.setMinimumSize(200, 200)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor('#1E1E1E'))


if __name__ == '__main__':
    import webmGenerator
