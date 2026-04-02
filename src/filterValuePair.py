import time
import numpy as np
from math import sqrt
import threading
import os
import logging

from PySide6.QtWidgets import (
    QWidget, QLabel, QSlider, QDoubleSpinBox, QSpinBox, QLineEdit,
    QCheckBox, QComboBox, QPushButton, QHBoxLayout, QFileDialog
)
from PySide6.QtCore import Qt


def cubic_interp1d(x0, x, y):

    x = np.asfarray(x)
    y = np.asfarray(y)

    if np.any(np.diff(x) < 0):
        indexes = np.argsort(x)
        x = x[indexes]
        y = y[indexes]

    size = len(x)

    xdiff = np.diff(x)
    ydiff = np.diff(y)

    Li = np.empty(size)
    Li_1 = np.empty(size-1)
    z = np.empty(size)

    Li[0] = sqrt(2*xdiff[0])
    Li_1[0] = 0.0
    B0 = 0.0
    z[0] = B0 / Li[0]

    for i in range(1, size-1, 1):
        Li_1[i] = xdiff[i-1] / Li[i-1]
        Li[i] = sqrt(2*(xdiff[i-1]+xdiff[i]) - Li_1[i-1] * Li_1[i-1])
        Bi = 6*(ydiff[i]/xdiff[i] - ydiff[i-1]/xdiff[i-1])
        z[i] = (Bi - Li_1[i-1]*z[i-1])/Li[i]

    i = size - 1
    Li_1[i-1] = xdiff[-1] / Li[i-1]
    Li[i] = sqrt(2*xdiff[-1] - Li_1[i-1] * Li_1[i-1])
    Bi = 0.0
    z[i] = (Bi - Li_1[i-1]*z[i-1])/Li[i]

    i = size-1
    z[i] = z[i] / Li[i]
    for i in range(size-2, -1, -1):
        z[i] = (z[i] - Li_1[i-1]*z[i+1])/Li[i]

    index = x.searchsorted(x0)
    np.clip(index, 1, size-1, index)

    xi1, xi0 = x[index], x[index-1]
    yi1, yi0 = y[index], y[index-1]
    zi1, zi0 = z[index], z[index-1]
    hi1 = xi1 - xi0

    f0 = zi0/(6*hi1)*(xi1-x0)**3 + \
         zi1/(6*hi1)*(x0-xi0)**3 + \
         (yi1/hi1 - zi1*hi1/6)*(x0-xi0) + \
         (yi0/hi1 - zi0*hi1/6)*(xi1-x0)
    return f0


def debounce(wait):
    def decorator(fn):
        def debounced(*args, **kwargs):
            def call_it():
                debounced._timer = None
                debounced._last_call = time.time()
                return fn(*args, **kwargs)

            time_since_last_call = time.time() - debounced._last_call
            if time_since_last_call >= wait:
                return call_it()

            if debounced._timer is None:
                debounced._timer = threading.Timer(wait - time_since_last_call, call_it)
                debounced._timer.start()
        debounced._timer = None
        debounced._last_call = 0
        return debounced
    return decorator


class FilterValuePair(QWidget):
    def __init__(self, master, controller, param, *args, **kwargs):
        QWidget.__init__(self, master)
        self.param = param
        self.controller = controller
        self.fileCategory = param.get('fileCategory', None)
        self.keyValues = self.param.get('keyValues', {})
        self.applyStartTimeOffset = self.param.get('offsetClipStartSeconds', False)

        # Replace tk.StringVar with a plain Python string attribute
        self._valueVar = str(param.get('d', ''))

        self.videoSpaceAxis = self.param.get('videoSpaceAxis', None)
        self.videoSpaceSign = self.param.get('videoSpaceSign', 0)

        self.n = self.param['n']
        self.vmin, self.vmax = float('-inf'), float('inf')
        self.rectProp = param.get('rectProp')
        self.rectPropGroup = param.get('rectPropGroup')
        if param.get('rectProp') is not None:
            self.controller.registerRectProp(param.get('rectProp'), self, param.get('type', 'int'))

        self.commandVarSelected  = False
        self.commandVarAvaliable = False
        self.commandVarEnabled   = False
        self.commandvarName      = None
        self.interpolationFactor = param.get('interpolationFactor', 0)
        self.commandInterpolationMode = param.get('interpMode', 'lerp')
        self.interpolationModes = param.get('restrictedInterpModes', [
            'lerp', 'lerp-smooth', 'lerp-smooth-2nd', 'lerp-sigmoid',
            'lerp-smooth-inv', 'neighbour'
        ])
        self._interpVar = self.commandInterpolationMode

        self.originalIncrement = param.get('inc', 1)

        self.commandVarTarget   = []
        self.commandVarProperty = []

        # Build layout
        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 1, 2, 1)
        layout.setSpacing(4)
        self.setLayout(layout)

        # Timeline / command-var buttons (conditional)
        if len(param.get('commandVar', [])) == 2:
            self.commandVarAvaliable = True
            self.commandVarEnabled   = False
            self.commandVarSelected  = False
            self.commandvarName, targetPairs = param['commandVar']

            for cmdTarget, cmdProp in targetPairs:
                self.commandVarTarget.append(cmdTarget)
                self.commandVarProperty.append(cmdProp)

            self.commandButton = QPushButton('T', self)
            self.commandButton.setFixedWidth(22)
            self.commandButton.clicked.connect(self.toggleTimelineCmdMode)
            layout.addWidget(self.commandButton)

            self.commandSelectButton = QPushButton('S', self)
            self.commandSelectButton.setFixedWidth(22)
            self.commandSelectButton.setEnabled(False)
            self.commandSelectButton.clicked.connect(self.toggleTimelineSelection)
            layout.addWidget(self.commandSelectButton)

        # Interpolation mode combobox (hidden by default, shown when timeline selected)
        self.entryInterpValue = QComboBox(self)
        self.entryInterpValue.addItems(self.interpolationModes)
        idx = self.interpolationModes.index(self.commandInterpolationMode) \
              if self.commandInterpolationMode in self.interpolationModes else 0
        self.entryInterpValue.setCurrentIndex(idx)
        self.entryInterpValue.currentTextChanged.connect(self._on_interp_changed)
        self.entryInterpValue.hide()

        # Label
        if param.get('desc', '') != '':
            labelText = param['n'] + ' (' + param['desc'] + ')'
        else:
            labelText = param['n']
        self.labelfilterValueLabel = QLabel(labelText, self)
        layout.addWidget(self.labelfilterValueLabel, stretch=1)

        # Value widget — varies by type
        ptype = param['type']

        if ptype == 'cycle':
            self.selectableValues = param['cycle']
            self._valueVar = str(param['d'])
            self.entryFilterValueValue = QComboBox(self)
            self.entryFilterValueValue.addItems([str(v) for v in self.selectableValues])
            cur = str(param['d'])
            if cur in [str(v) for v in self.selectableValues]:
                self.entryFilterValueValue.setCurrentText(cur)
            self.entryFilterValueValue.currentTextChanged.connect(self._on_value_changed)

        elif ptype == 'float':
            self._valueVar = str(param['d'])
            if param.get('range') is None:
                vmin, vmax = float('-inf'), float('inf')
            else:
                vmin, vmax = param['range']
                if vmin is None:
                    vmin = float('-inf')
                if vmax is None:
                    vmax = float('inf')
            self.vmin, self.vmax = vmin, vmax

            self.entryFilterValueValue = QDoubleSpinBox(self)
            # QDoubleSpinBox needs finite bounds
            safe_min = vmin if vmin != float('-inf') else -1e18
            safe_max = vmax if vmax != float('inf') else 1e18
            self.entryFilterValueValue.setRange(safe_min, safe_max)
            self.entryFilterValueValue.setSingleStep(float(param.get('inc', 1)))
            self.entryFilterValueValue.setDecimals(6)
            try:
                self.entryFilterValueValue.setValue(float(param['d']))
            except (ValueError, TypeError):
                pass
            self.entryFilterValueValue.valueChanged.connect(self._on_spinbox_changed)
            # Ctrl/Shift modifier for step size
            self.entryFilterValueValue.installEventFilter(self)

        elif ptype in ('string', 'bareString'):
            self._valueVar = str(param['d'])
            self.entryFilterValueValue = QLineEdit(self)
            self.entryFilterValueValue.setText(self._valueVar)
            self.entryFilterValueValue.textChanged.connect(self._on_value_changed)

        elif ptype == 'int':
            self._valueVar = str(param['d'])
            if param.get('range') is None:
                vmin, vmax = float('-inf'), float('inf')
            else:
                vmin, vmax = param['range']
                if vmin is None:
                    vmin = float('-inf')
                if vmax is None:
                    vmax = float('inf')
            self.vmin, self.vmax = vmin, vmax

            self.entryFilterValueValue = QDoubleSpinBox(self)
            safe_min = vmin if vmin != float('-inf') else -1e18
            safe_max = vmax if vmax != float('inf') else 1e18
            self.entryFilterValueValue.setRange(safe_min, safe_max)
            self.entryFilterValueValue.setSingleStep(float(param.get('inc', 1)))
            self.entryFilterValueValue.setDecimals(0)
            try:
                self.entryFilterValueValue.setValue(float(param['d']))
            except (ValueError, TypeError):
                pass
            self.entryFilterValueValue.valueChanged.connect(self._on_spinbox_changed)
            self.entryFilterValueValue.installEventFilter(self)

        elif ptype == 'file':
            self._valueVar = str(param['d'])
            self.entryFilterValueValue = QPushButton(
                'File: {}'.format(self._valueVar[-20:]), self
            )
            self.entryFilterValueValue.clicked.connect(self.selectFile)

        else:
            logging.error("Unhandled param {}".format(str(param)))
            self.entryFilterValueValue = QLineEdit(self)
            self.entryFilterValueValue.setText(str(param.get('d', '')))
            self.entryFilterValueValue.textChanged.connect(self._on_value_changed)

        layout.addWidget(self.entryFilterValueValue)
        layout.addWidget(self.entryInterpValue)

        # Apply initial command-var state
        self.commandVarSelected = self.param.get('commandVarSelected', False)
        self.commandVarEnabled  = self.param.get('commandVarEnabled', False)
        self.updateCommandButtonStyles()

    # ------------------------------------------------------------------
    # Backward-compat shim: parent containers may call .pack(...)
    # ------------------------------------------------------------------
    def pack(self, *args, **kwargs):
        pass

    # ------------------------------------------------------------------
    # valueVar property — emulates tk.StringVar get()/set() interface
    # ------------------------------------------------------------------
    class _ValueVarProxy:
        """Thin proxy so that code calling self.valueVar.get() / .set() still works."""
        def __init__(self, owner):
            self._owner = owner

        def get(self):
            return self._owner._valueVar

        def set(self, value):
            self._owner._valueVar = str(value)
            self._owner._sync_widget_to_var()

    @property
    def valueVar(self):
        if not hasattr(self, '_valueVarProxy'):
            self._valueVarProxy = FilterValuePair._ValueVarProxy(self)
        return self._valueVarProxy

    # ------------------------------------------------------------------
    # interpVar property — emulates tk.StringVar for interpolation mode
    # ------------------------------------------------------------------
    class _InterpVarProxy:
        def __init__(self, owner):
            self._owner = owner

        def get(self):
            return self._owner._interpVar

        def set(self, value):
            self._owner._interpVar = str(value)
            if hasattr(self._owner, 'entryInterpValue'):
                self._owner.entryInterpValue.blockSignals(True)
                self._owner.entryInterpValue.setCurrentText(str(value))
                self._owner.entryInterpValue.blockSignals(False)

    @property
    def interpVar(self):
        if not hasattr(self, '_interpVarProxy'):
            self._interpVarProxy = FilterValuePair._InterpVarProxy(self)
        return self._interpVarProxy

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _on_value_changed(self, text):
        """Called when a QLineEdit / QComboBox text changes."""
        self._valueVar = str(text)
        self.valueUpdated()

    def _on_spinbox_changed(self, value):
        """Called when a QDoubleSpinBox value changes."""
        self._valueVar = str(value)
        self.valueUpdated()

    def _on_interp_changed(self, text):
        self._interpVar = text
        self.interpolationChanged()

    def _sync_widget_to_var(self):
        """Push _valueVar back into the visible widget (used by valueVar.set())."""
        w = getattr(self, 'entryFilterValueValue', None)
        if w is None:
            return
        ptype = self.param.get('type', '')
        if isinstance(w, (QDoubleSpinBox, QSpinBox)):
            w.blockSignals(True)
            try:
                w.setValue(float(self._valueVar))
            except (ValueError, TypeError):
                pass
            w.blockSignals(False)
        elif isinstance(w, QLineEdit):
            w.blockSignals(True)
            w.setText(self._valueVar)
            w.blockSignals(False)
        elif isinstance(w, QComboBox):
            w.blockSignals(True)
            w.setCurrentText(self._valueVar)
            w.blockSignals(False)
        elif isinstance(w, QPushButton) and ptype == 'file':
            w.setText('File: {}'.format(self._valueVar[-20:]))

    # ------------------------------------------------------------------
    # Qt event filter — replicate Ctrl/Shift step-size modifiers
    # ------------------------------------------------------------------
    def eventFilter(self, obj, event):
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QKeyEvent
        if obj is getattr(self, 'entryFilterValueValue', None):
            if event.type() in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease,
                                 QEvent.Type.Wheel):
                modifiers = event.modifiers() if hasattr(event, 'modifiers') else Qt.NoModifier
                ctrl  = bool(modifiers & Qt.ControlModifier)
                shift = bool(modifiers & Qt.ShiftModifier)
                self._apply_increment_modifier(ctrl, shift)
        return super().eventFilter(obj, event)

    def _apply_increment_modifier(self, ctrl, shift):
        w = getattr(self, 'entryFilterValueValue', None)
        if not isinstance(w, (QDoubleSpinBox, QSpinBox)):
            return
        if ctrl and not shift:
            w.setSingleStep(self.originalIncrement * 10)
        elif shift:
            w.setSingleStep(self.originalIncrement * 100)
        else:
            w.setSingleStep(self.originalIncrement)

    def checkCtrl(self, e):
        """Retained for any code that calls this directly (was a Tk binding)."""
        pass

    # ------------------------------------------------------------------
    # Command-button / timeline UI
    # ------------------------------------------------------------------
    def updateCommandButtonStyles(self):
        if not self.commandVarAvaliable:
            return

        if self.commandVarSelected:
            self.commandSelectButton.setStyleSheet('font-weight: bold; color: #00aaff;')
            self.setStyleSheet('background-color: #1a2a3a;')
            self.labelfilterValueLabel.setStyleSheet('color: #00aaff;')
            self.entryFilterValueValue.hide()
            self.entryInterpValue.show()
        else:
            self.commandSelectButton.setStyleSheet('')
            self.setStyleSheet('')
            self.labelfilterValueLabel.setStyleSheet('')
            self.entryInterpValue.hide()
            self.entryFilterValueValue.show()

        if self.commandVarEnabled:
            self.commandButton.setStyleSheet('font-weight: bold; color: #00aaff;')
            self.commandSelectButton.setEnabled(True)
        else:
            self.commandButton.setStyleSheet('')
            self.commandSelectButton.setEnabled(False)

    def interpolationChanged(self, *args):
        newmode = self._interpVar
        if newmode in self.interpolationModes:
            self.commandInterpolationMode = newmode
            self.controller.recaculateFilters('interpolationChanged')
        else:
            self._interpVar = self.commandInterpolationMode
            if hasattr(self, 'entryInterpValue'):
                self.entryInterpValue.blockSignals(True)
                self.entryInterpValue.setCurrentText(self.commandInterpolationMode)
                self.entryInterpValue.blockSignals(False)

    def cycleSelectedPropertySameGroup(self):
        self.controller.cycleSelectedPropertySameGroup(self)

    def cycleSelectedProperty(self):
        self.controller.cycleSelectedProperty(self)

    def clearKeyValues(self):
        self.keyValues = {}

    def getBoundingBox(self, seconds):
        box = self.controller.getBoundingBox(seconds)
        print(box)
        return box

    def getPredictedValue(self, seconds):
        kvs = self.getKeyValues()

        lower = [(k, v) for k, v, _ in kvs if k < seconds][-1:]
        upper = [(k, v) for k, v, _ in sorted(kvs, reverse=True) if k > seconds][-1:]

        if len(lower) == 1 and len(upper) == 1:
            neighbourRange    = upper[0][1] - lower[0][1]
            neighbourDuration = upper[0][0] - lower[0][0]
            percent = (seconds - lower[0][0]) / neighbourDuration
            return self.convertKeyValueToType(lower[0][1] + (neighbourRange * percent))
        elif len(lower) == 1:
            return self.convertKeyValueToType(lower[0][1])
        elif len(upper) == 1:
            return self.convertKeyValueToType(upper[0][1])
        else:
            return self.convertKeyValueToType(self.convertKeyValueToType(self._valueVar))

    def addKeyValue(self, seconds, value=None, useIncrementMultiplier=False, isAsoluteValue=False):
        try:
            if isAsoluteValue and value is not None:
                self.keyValues[seconds] = self.convertKeyValueToType(
                    (float(self.param.get('inc', 1)) if useIncrementMultiplier else 1) * value
                )
            else:
                incrementValue = 0
                if value is not None:
                    incrementValue = value
                if useIncrementMultiplier:
                    incrementValue = value * self.param.get('inc', 1)

                kvs = self.getKeyValues()

                lower = [(k, v) for k, v, _ in kvs if k < seconds][-1:]
                upper = [(k, v) for k, v, _ in sorted(kvs, reverse=True) if k > seconds][-1:]

                if len(lower) == 1 and len(upper) == 1:
                    neighbourRange    = upper[0][1] - lower[0][1]
                    neighbourDuration = upper[0][0] - lower[0][0]
                    percent = (seconds - lower[0][0]) / neighbourDuration
                    self.keyValues[seconds] = self.convertKeyValueToType(
                        lower[0][1] + (neighbourRange * percent) + incrementValue
                    )
                elif len(lower) == 1:
                    self.keyValues[seconds] = self.convertKeyValueToType(
                        lower[0][1] + incrementValue
                    )
                elif len(upper) == 1:
                    self.keyValues[seconds] = self.convertKeyValueToType(
                        upper[0][1] + incrementValue
                    )
                else:
                    valueVarInc = 0
                    try:
                        valueVarInc += self.convertKeyValueToType(self._valueVar)
                    except Exception as e:
                        print("valueVarInc Exception", e)
                    self.keyValues[seconds] = self.convertKeyValueToType(
                        valueVarInc + incrementValue
                    )

        except Exception as e:
            self.keyValues[seconds] = self.convertKeyValueToType(self.param['d'])
            print('addKeyValue Exception', e)

        self.valueUpdated()

    def removeKeyValue(self, seconds):
        del self.keyValues[seconds]
        self.valueUpdated()

    def isInitialTS(self, seconds):
        if len(self.keyValues) > 0:
            return sorted(self.keyValues.keys())[0] == seconds
        else:
            return False

    def incrementAllKeyValues(self, valueOffset, useIncrementMultiplier=True, isAsoluteValue=False):
        for seconds in self.keyValues:
            if isAsoluteValue:
                newval = (valueOffset * (self.param['inc'] if useIncrementMultiplier else 1))
            else:
                newval = self.keyValues[seconds] + (valueOffset * (self.param['inc'] if useIncrementMultiplier else 1))
            newval = max(min(newval, self.vmax), self.vmin)
            self.keyValues[seconds] = self.convertKeyValueToType(newval)
        self.valueUpdated()

    def incrementKeyValue(self, seconds, valueOffset, useIncrementMultiplier=True, isAsoluteValue=False):
        if seconds in self.keyValues:
            if isAsoluteValue:
                newval = (valueOffset * (self.param['inc'] if useIncrementMultiplier else 1))
            else:
                newval = self.keyValues[seconds] + (valueOffset * (self.param['inc'] if useIncrementMultiplier else 1))
            newval = max(min(newval, self.vmax), self.vmin)
            self.keyValues[seconds] = self.convertKeyValueToType(newval)
            self.valueUpdated()

    def convertKeyValueToType(self, value):
        if self.param['type'] == 'int':
            return int(value)
        elif self.param['type'] == 'float':
            return float(value)
        return value

    def getKeyValues(self, interpolation=True):
        sortedKVs = sorted(list(self.keyValues.items()).copy())
        try:
            if self.interpolationFactor > 0 and interpolation and len(sortedKVs) > 1:
                x = np.array([x[0] for x in sortedKVs])
                y = np.array([x[1] for x in sortedKVs])

                x_new = np.linspace(x[0], x[-1], int((x[-1] - x[0]) * int(self.interpolationFactor)))
                x_new = np.append(x_new, list(self.keyValues.keys()))

                y_new = cubic_interp1d(x_new, x, y)

                oldKVS = [(k, v, True) for k, v in sortedKVs]
                newKVS = [(k, v, False) for k, v, in zip(x_new, y_new) if k not in self.keyValues]

                return sorted(newKVS + oldKVS)
            else:
                return [(a, b, True) for a, b, in sortedKVs]
        except Exception as e:
            print('getKeyValues Exception', e)
        return [(a, b, True) for a, b, in sortedKVs]

    def deactivateTimeLineSection(self):
        if self.commandVarAvaliable:
            self.commandVarSelected = False
            self.updateCommandButtonStyles()

    def toggleTimelineSelection(self):
        if self.commandVarAvaliable:
            if self.commandVarSelected:
                self.commandVarSelected = False
                self.controller.setActiveTimeLineValue(None)
            else:
                self.commandVarSelected = True
                self.controller.setActiveTimeLineValue(self)
            self.updateCommandButtonStyles()

    def toggleTimelineCmdMode(self):
        if self.commandVarAvaliable:
            if self.commandVarEnabled:
                self.commandVarEnabled  = False
                self.commandVarSelected = False
                self.controller.setActiveTimeLineValue(None)
                self.updateCommandButtonStyles()
            else:
                self.commandVarEnabled = True
                self.toggleTimelineSelection()
                self.updateCommandButtonStyles()
            self.controller.recaculateFilters('toggleTimelineCmdMode')

    def selectFile(self):
        initialdir = '.'
        name_filter = 'All files (*.*)'

        if self.fileCategory == 'font':
            initialdir = self.controller.getGlobalOptions().get('defaultFontFolder', '.')
        elif self.fileCategory == 'subtitle':
            initialdir = self.controller.getGlobalOptions().get('defaultSubtitleFolder', '.')
            name_filter = 'Subtitle (*.srt *.ass)'
        elif self.fileCategory == 'image':
            initialdir = self.controller.getGlobalOptions().get('defaultImageFolder', '.')
        elif self.fileCategory == 'video':
            initialdir = self.controller.getGlobalOptions().get('defaultVideoFolder', '.')
        elif self.fileCategory == 'audio':
            initialdir = self.controller.getGlobalOptions().get('defaultAudioFolder', '.')

        print(initialdir, name_filter)
        fn, _ = QFileDialog.getOpenFileName(self, 'Select file', initialdir, name_filter)
        if not fn:
            self.entryFilterValueValue.setText('Select file')
        else:
            cleanPath = os.path.abspath(fn).replace('\\', '/').replace(':', '\\:')
            writeBackPath = os.path.abspath(os.path.dirname(fn))

            if self.fileCategory == 'font':
                self.controller.getGlobalOptions()['defaultFontFolder'] = writeBackPath
            elif self.fileCategory == 'subtitle':
                self.controller.getGlobalOptions()['defaultSubtitleFolder'] = writeBackPath
            elif self.fileCategory == 'image':
                self.controller.getGlobalOptions()['defaultImageFolder'] = writeBackPath
            elif self.fileCategory == 'video':
                self.controller.getGlobalOptions()['defaultVideoFolder'] = writeBackPath
            elif self.fileCategory == 'audio':
                self.controller.getGlobalOptions()['defaultAudioFolder'] = writeBackPath

            self._valueVar = cleanPath
            print(self._valueVar)
            self.entryFilterValueValue.setText('File: {}'.format(self._valueVar[-20:]))
            self.valueUpdated()

    def stringValueVarSubstitutions(self):
        valVar = self._valueVar

        if "{!filename}" in valVar:
            valVar = valVar.replace('{!filename}', self.controller.getStringValue('filename'))
        elif "{!title}" in valVar:
            valVar = valVar.replace('{!title}', self.controller.getStringValue('title'))
        elif "{!path}" in valVar:
            valVar = valVar.replace('{!path}', self.controller.getStringValue('path'))
        elif "{!startts}" in valVar:
            valVar = valVar.replace('{!startts}', self.controller.getStringValue('startts'))
        elif "{!endts}" in valVar:
            valVar = valVar.replace('{!endts}', self.controller.getStringValue('endts'))

        if self._valueVar != valVar:
            self._valueVar = valVar
            self._sync_widget_to_var()

    def getValuePair(self, forFilter=True):
        val = self._valueVar
        if val in ('inf', '-inf', str(float('inf')), str(float('-inf'))):
            self._valueVar = '0'
            val = '0'
            self._sync_widget_to_var()

        if self.param['type'] == 'string':
            self.stringValueVarSubstitutions()
            val = self._valueVar

            val = val.replace('\\n', '\n')

            if forFilter:
                outval = []
                for c in val:
                    if c == '\\':
                        outval.append('\\\\')
                    elif c == '"':
                        outval.append('\'"\'')
                    elif c == ':':
                        outval.append('\\:')
                    elif c == "'":
                        outval.append("'\\\\\\''")
                    else:
                        outval.append(c)
                val = ''.join(["'"] + outval + ["'"])
            return (self.param['n'], "{}".format(val))
        else:
            return (self.param['n'], self._valueVar)

    @debounce(0.1)
    def valueUpdated(self, *args):
        self.controller.recaculateFilters('debounced valueUpdated')
