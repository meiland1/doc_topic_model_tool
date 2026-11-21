"""Optional orientation; every action leads to the existing researcher workflow."""
from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QPushButton, QScrollArea


class ProjectGuide(QWidget):
    action_requested = Signal(str)

    def __init__(self):
        super().__init__()
        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        self.heading = QLabel('Your project: a place to start')
        self.heading.setObjectName('panelTitle')
        layout.addWidget(self.heading)
        self.intro = QLabel()
        self.intro.setWordWrap(True)
        self.intro.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.intro)
        self.buttons = {}
        steps = [
            ('add', '1. Add documents', 'PDF, Word or text files. Keep the originals somewhere safe; the project keeps its own copy.'),
            ('check', '2. Check the text', 'Open each document on Documents & analysis and look for missing text, broken paragraphs and speaker labels.'),
            ('topics', '3. Find themes (optional)', 'Select documents and choose Find themes. The themes are suggestions to read, not findings.'),
            ('code', '4. Read & code', 'Select text, then press a number key or click a theme. Add comments as you go. This works without the AI.'),
            ('export', '5. Export your work', 'File → Export: a codebook, your coded passages, or a reading guide.'),
        ]
        for key, title, description in steps:
            button = QPushButton(title)
            button.setAccessibleName(title)
            button.clicked.connect(lambda _checked=False, action=key: self.action_requested.emit(action))
            self.buttons[key] = button
            layout.addWidget(button)
            label = QLabel(description)
            label.setWordWrap(True)
            layout.addWidget(label)
        self.practice_help = QLabel('Practice: open interview A and find the missed bus appointment. Select the passage, '
                                    'make a theme such as “Transport barriers”, and add a comment explaining your choice. '
                                    'Then read interview C for a different account. Export your codebook and reopen this '
                                    'project from Recent projects. These are exercises; there are no right answers.')
        self.practice_help.setWordWrap(True)
        layout.addWidget(self.practice_help)
        self.fresh = QPushButton('Start a fresh practice copy')
        self.fresh.setToolTip('Creates another copy. Your current project and earlier practice work are kept.')
        self.fresh.clicked.connect(lambda: self.action_requested.emit('practice'))
        layout.addWidget(self.fresh)
        layout.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll)

    def set_project(self, project, practice=False):
        self.intro.setText(('Practice project — all interviews are fictional. No AI setup is needed for the coding exercise.\n'
                            if practice else 'Start with close reading, or let the tool suggest themes to choose where to read.\n')
                           + f'Project: {project.name}\nSaved in: {project.directory}')
        self.practice_help.setVisible(practice)
        self.fresh.setVisible(practice)
