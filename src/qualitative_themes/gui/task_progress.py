"""Persistent stage progress; counters refer to the current stage, never the whole run."""
import re
from PySide6.QtCore import QElapsedTimer, QTimer, Signal, Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QProgressBar, QPushButton


class TaskProgress(QWidget):
    cancel_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.active = False
        self.cancelling = False
        self.outcome = None
        self.clock = QElapsedTimer()
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.update_elapsed)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        row = QHBoxLayout()
        self.stage = QLabel()
        self.stage.setTextFormat(Qt.TextFormat.PlainText)
        self.stage.setWordWrap(True)
        self.stage.setAccessibleName('Current task stage')
        row.addWidget(self.stage, 1)
        self.elapsed = QLabel('Elapsed 00:00')
        row.addWidget(self.elapsed)
        self.cancel = QPushButton('Cancel')
        self.cancel.clicked.connect(self.cancel_requested)
        row.addWidget(self.cancel)
        layout.addLayout(row)
        self.bar = QProgressBar()
        self.bar.setAccessibleName('Current stage progress')
        self.bar.setMaximumHeight(16)
        self.bar.setToolTip('Counts measure the current stage only. An animated bar means the stage has no measurable total.')
        layout.addWidget(self.bar)
        self.hide()

    def begin(self, title):
        self.active = True
        self.cancelling = False
        self.outcome = None
        self.clock.start()
        self.update_elapsed()
        self.cancel.setEnabled(True)
        self.bar.show()
        self.bar.setRange(0, 0)
        self.stage.setText(title + ' · Starting…')
        self.timer.start()
        self.show()

    def update_elapsed(self):
        seconds = max(0, self.clock.elapsed() // 1000) if self.clock.isValid() else 0
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        value = f'{hours}:{minutes:02}:{seconds:02}' if hours else f'{minutes:02}:{seconds:02}'
        self.elapsed.setText('Elapsed ' + value)

    def update_stage(self, message):
        if not self.active or self.cancelling or self.outcome:
            return
        self.stage.setText(message)
        # Only known backend counters are interpreted; arbitrary text is never a percentage.
        match = re.match(r'^(?:Embedding passages:|Naming theme)\s*(\d+)/(\d+)', message)
        if match and 0 < int(match[2]) and 0 <= int(match[1]) <= int(match[2]):
            current, total = map(int, match.groups())
            self.bar.setRange(0, total)
            self.bar.setValue(current)
            self.bar.setFormat('Current stage: %v / %m')
        else:
            self.bar.setRange(0, 0)

    def request_cancel(self):
        if self.active:
            self.cancelling = True
            self.cancel.setEnabled(False)
            self.stage.setText('Cancellation requested · Waiting for the current computation or request to finish…')
            self.bar.setRange(0, 0)

    def terminal(self, outcome, message):
        self.outcome = outcome
        self.stage.setText(message)
        self.cancel.setEnabled(False)
        self.bar.hide()

    def finish(self):
        if not self.active:
            return
        self.update_elapsed()
        self.timer.stop()
        self.active = False
        self.cancel.setEnabled(False)
        self.bar.hide()
        if not self.outcome:
            self.outcome = 'complete'
            self.stage.setText('Task finished after cancellation was requested. Check the saved results.' if self.cancelling else 'Completed · ' + self.stage.text())
