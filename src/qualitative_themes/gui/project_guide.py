"""The Quick guide: five steps from documents to a codebook, ending with sample interviews.

One widget, used in two places: on the start screen (no project needed) and as
the Help tab inside a project. Every button only emits action_requested; the
main window does the work (see ProjectSessionMixin.guide_action).
"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

STEPS = [
    ('add', '1. Add documents',
     'PDF, Word or text files. Keep the originals somewhere safe; the project keeps its own copy.'),
    ('check', '2. Check the text',
     'Open each document on Documents & analysis and look for missing text, broken paragraphs and speaker labels.'),
    ('topics', '3. Find themes (optional)',
     'Select documents and choose Find themes. The themes are suggestions to read, not findings.'),
    ('code', '4. Read & code',
     'Select text, then press a number key or click a theme. Add comments as you go. This works without the AI.'),
    ('export', '5. Export your work',
     'File → Export: a codebook, your coded passages, or a reading guide.'),
]


class ProjectGuide(QWidget):
    """Emits 'add', 'check', 'topics', 'code', 'export', 'practice' or 'start_screen'."""
    action_requested = Signal(str)

    def __init__(self, on_start_screen=False):
        super().__init__()
        self.on_start_screen = on_start_screen
        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(40, 24, 40, 24)
        if on_start_screen:
            back = QPushButton('← Start screen')
            back.setFlat(True)
            back.clicked.connect(lambda: self.action_requested.emit('start_screen'))
            layout.addWidget(back, 0, Qt.AlignmentFlag.AlignLeft)
        self.heading = QLabel('Quick guide')
        self.heading.setObjectName('panelTitle')
        layout.addWidget(self.heading)
        self.intro = QLabel()
        self.intro.setWordWrap(True)
        self.intro.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.intro)
        self.buttons = {}
        for key, title, description in STEPS:
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
        # Last step everywhere: try the tool on fictional interviews.
        self.try_it = QFrame()
        self.try_it.setObjectName('backCard')
        try_row = QHBoxLayout(self.try_it)
        try_text = QLabel('<b>Try it on sample interviews</b><br>Opens a fresh practice project of short fictional '
                          'interviews. Nothing you do there touches your own projects.')
        try_text.setWordWrap(True)
        try_row.addWidget(try_text, 1)
        self.fresh = QPushButton('Open sample interviews')
        self.fresh.setProperty('primary', True)
        self.fresh.clicked.connect(lambda: self.action_requested.emit('practice'))
        try_row.addWidget(self.fresh)
        layout.addWidget(self.try_it)
        layout.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll)
        self.set_project(None)

    def set_project(self, project, practice=False):
        """Point the guide at the open project (None on the start screen with nothing open).

        Without a project, step 1 starts a new project and steps 2 to 5 wait for one.
        """
        if project is None:
            self.intro.setText('Five steps from documents to a codebook. The same guide is under Help inside a project.')
        elif practice:
            self.intro.setText('Practice project: all the interviews are fictional. No AI is needed for the coding '
                               f'exercise.\nProject: {project.name}\nSaved in: {project.directory}')
        else:
            self.intro.setText('Start with close reading, or let the tool suggest themes to choose where to read.\n'
                               f'Project: {project.name}\nSaved in: {project.directory}')
        self.buttons['add'].setText('1. Add documents' if project else '1. Add documents (starts a new project)')
        for key in ('check', 'topics', 'code', 'export'):
            self.buttons[key].setEnabled(project is not None)
            self.buttons[key].setToolTip('' if project else 'Open or create a project first.')
        self.practice_help.setVisible(practice)
        self.fresh.setText('Start a fresh practice copy' if practice else 'Open sample interviews')
