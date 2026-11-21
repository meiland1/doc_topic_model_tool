"""Settings controls shared by the Find themes and Adjust themes dialogs.

Both dialogs ask for the same keyword and naming choices, so they're built
here once. A new keyword setting is added in KeywordOptions and both dialogs
pick it up. DiscoveryOptions holds the More options that only apply when
themes are found from scratch (Find themes, or Adjust themes → Find themes again).
"""
import random

from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
                               QSpinBox, QWidget)

from qualitative_themes.analysis.settings import (DEFAULT_NAMING_MODEL, KEYWORD_STYLES, PRESETS, UNSORTED_LEVELS,
                                                  AnalysisSettings, min_samples_for, parse_ignored_words,
                                                  unsorted_level)

class KeywordOptions:
    """Keyword style, words to ignore and two-word keywords, added as rows to a form.

    Pre: `settings` is the AnalysisSettings to start from.
    Post: values() returns the matching AnalysisSettings fields; it raises
    ValueError (with a message for the researcher) on bad ignored words.
    """

    def __init__(self, form: QFormLayout, settings, *, tooltips=True):
        self.style = QComboBox()
        for key, label in KEYWORD_STYLES.items():
            self.style.addItem(label, key)
        self.style.setCurrentIndex(max(0, self.style.findData(settings.keyword_style)))
        self.style.setToolTip("Plain counts: the words most typical of the theme. More varied: leaves out near-repeats "
                              "(\"clinic, clinics\"), which also helps AI names. Based on meaning: words closest in "
                              "meaning to the theme's passages; slower.")
        form.addRow("Keyword style", self.style)
        self.play_down = QCheckBox("Play down words found in every theme")
        self.play_down.setChecked(settings.play_down_common_words)
        self.play_down.setToolTip("Stops filler words, and the main subject of your study, showing up as a keyword "
                                  "in every theme.")
        form.addRow(self.play_down)
        self.count = QSpinBox()
        self.count.setRange(5, 20)
        self.count.setValue(settings.top_n_words)
        self.count.setAccessibleName("Keywords per theme")
        form.addRow("Keywords per theme", self.count)
        self.ignored = QPlainTextEdit()
        self.ignored.setTabChangesFocus(True)
        self.ignored.setAccessibleName('Words to ignore')
        self.ignored.setMaximumHeight(65)
        self.ignored.setPlainText(", ".join(settings.ignored_words))
        if tooltips:
            self.ignored.setPlaceholderText("Words separated by commas, spaces, or newlines; added to English stop words")
            self.ignored.setToolTip("These words won't appear in theme keywords. Themes are still found from the full text.")
        form.addRow("Words to ignore", self.ignored)
        self.phrases = QCheckBox('Allow two-word keywords (e.g. "health insurance")')
        self.phrases.setChecked(settings.ngram_range != (1, 1))
        form.addRow(self.phrases)

    def changed_signals(self):
        """Signals that fire when any of these controls changes (for re-validating)."""
        return [self.ignored.textChanged, self.phrases.toggled, self.style.currentIndexChanged,
                self.play_down.toggled, self.count.valueChanged]

    def set_enabled(self, enabled: bool):
        for widget in (self.style, self.play_down, self.count, self.ignored, self.phrases):
            widget.setEnabled(enabled)

    def reset(self):
        defaults = AnalysisSettings()
        self.style.setCurrentIndex(self.style.findData(defaults.keyword_style))
        self.play_down.setChecked(defaults.play_down_common_words)
        self.count.setValue(defaults.top_n_words)
        self.ignored.clear()
        self.phrases.setChecked(True)

    def values(self) -> dict:
        return {"ignored_words": parse_ignored_words(self.ignored.toPlainText()),
                "ngram_range": (1, 2) if self.phrases.isChecked() else (1, 1),
                "keyword_style": self.style.currentData(), "play_down_common_words": self.play_down.isChecked(),
                "top_n_words": self.count.value()}


class DiscoveryOptions:
    """More options that change how themes are found, added as rows to a form.

    Call add_technical_rows() once the form's other rows are in.

    values(style) gives the matching AnalysisSettings fields; "How many
    passages stay unsorted" is stored as HDBSCAN min_samples for that style.
    """

    def __init__(self, form: QFormLayout, settings):
        self.minimum = QSpinBox()
        self.minimum.setRange(1, 10000)
        self.minimum.setValue(settings.min_topic_size)
        self.minimum.setSuffix(" passages")
        self.minimum.setToolTip("Smaller groups go to \"Not in any theme\". At 1, every passage gets a theme "
                                "(small-collection grouping), which suits a few whole documents. "
                                "Changing the theme size resets this.")
        form.addRow("A theme needs at least", self.minimum)
        self.unsorted = QComboBox()
        self.unsorted.addItems(UNSORTED_LEVELS)
        self.unsorted.setCurrentText(unsorted_level(settings))
        self.unsorted.setToolTip("Fewer: more passages are given a theme, some loosely. "
                                 "More: only clear matches; the rest go to \"Not in any theme\".")
        form.addRow("How many passages stay unsorted", self.unsorted)
        self.fill = QCheckBox("Place leftover passages in their closest theme")
        self.fill.setChecked(settings.fill_leftovers)
        self.fill.setToolTip("Passages that fit no theme well go into the closest one if they're close enough. "
                             "They're suggestions like the rest: keep or reject them in Read & code.")
        form.addRow(self.fill)
        self.merge = QCheckBox("Merge very similar themes automatically")
        self.merge.setChecked(settings.auto_merge)
        self.merge.setToolTip("A middle way between letting the tool decide and asking for about N themes. "
                              "Ignored when you ask for about N.")
        form.addRow(self.merge)
        seed_row = QWidget()
        seed_layout = QHBoxLayout(seed_row)
        seed_layout.setContentsMargins(0, 0, 0, 0)
        self.seed = QSpinBox()
        self.seed.setRange(0, 2**31 - 1)
        self.seed.setValue(settings.seed)
        self.seed.setAccessibleName("Random seed")
        seed_layout.addWidget(self.seed)
        other = QPushButton("Try a different seed")
        other.setToolTip("Run again with another seed to see which themes hold up. Same seed, same results.")
        other.clicked.connect(lambda: self.seed.setValue(random.randrange(1, 100000)))
        seed_layout.addWidget(other)
        seed_layout.addStretch()
        self._seed_row = seed_row

    def add_technical_rows(self, form: QFormLayout):
        """The seed goes last, under Technical details, after the dialog's other More options."""
        heading = QLabel("Technical details")
        heading.setObjectName("quiet")
        form.addRow(heading)
        form.addRow("Random seed", self._seed_row)

    def changed_signals(self):
        return [self.minimum.valueChanged, self.unsorted.currentIndexChanged, self.fill.toggled,
                self.merge.toggled, self.seed.valueChanged]

    def style_changed(self, style):
        """A new theme size resets the minimum to that size's default."""
        self.minimum.setValue(PRESETS[style]["min_topic_size"])

    def reset(self, style="Balanced"):
        defaults = AnalysisSettings()
        self.minimum.setValue(PRESETS[style]["min_topic_size"])
        self.unsorted.setCurrentText("Balanced")
        self.fill.setChecked(defaults.fill_leftovers)
        self.merge.setChecked(defaults.auto_merge)
        self.seed.setValue(defaults.seed)

    def values(self, style) -> dict:
        return {"min_topic_size": self.minimum.value(), "min_samples": min_samples_for(style, self.unsorted.currentText()),
                "fill_leftovers": self.fill.isChecked(), "auto_merge": self.merge.isChecked(), "seed": self.seed.value()}


def naming_model_choice(models, initial, *, fallback_label, unverified_suffix=""):
    """A combo box of installed naming models, with a 'no model' entry first.

    `models` are model names; `initial` is preselected and is added (with
    `unverified_suffix`) if it isn't in the list yet.
    """
    combo = QComboBox()
    combo.addItem(fallback_label, None)
    for name in dict.fromkeys(name for name in models if name):
        combo.addItem(name, name)
    if initial and combo.findData(initial) < 0:
        combo.addItem(initial + unverified_suffix, initial)
    combo.setCurrentIndex(max(0, combo.findData(initial)))
    return combo


def refill_naming_models(combo, names, *, fallback_label):
    """Replace the list after a refresh, keeping the current choice when it's still installed.

    Otherwise the default naming model is chosen if present, else the first model.
    """
    old = combo.currentData()
    combo.clear()
    combo.addItem(fallback_label, None)
    for name in names:
        combo.addItem(name, name)
    index = combo.findData(old)
    if index < 0:
        index = combo.findData(DEFAULT_NAMING_MODEL)
    combo.setCurrentIndex(index if index >= 0 else (1 if names else 0))
