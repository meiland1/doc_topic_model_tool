"""Settings controls shared by the Find themes and Adjust themes dialogs.

Both dialogs ask for the same keyword and naming choices, so they're built
here once. A new keyword setting is added in KeywordOptions and both dialogs
pick it up.
"""
from PySide6.QtWidgets import QCheckBox, QComboBox, QFormLayout, QPlainTextEdit

from qualitative_themes.analysis.settings import DEFAULT_NAMING_MODEL, parse_ignored_words

class KeywordOptions:
    """Words to ignore and two-word keywords, added as rows to a form.

    Pre: `settings` is the AnalysisSettings to start from.
    Post: values() returns the matching AnalysisSettings fields; it raises
    ValueError (with a message for the researcher) on bad ignored words.
    """

    def __init__(self, form: QFormLayout, settings, *, tooltips=True):
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
        return [self.ignored.textChanged, self.phrases.toggled]

    def set_enabled(self, enabled: bool):
        self.ignored.setEnabled(enabled)
        self.phrases.setEnabled(enabled)

    def reset(self):
        self.ignored.clear()
        self.phrases.setChecked(True)

    def values(self) -> dict:
        return {"ignored_words": parse_ignored_words(self.ignored.toPlainText()),
                "ngram_range": (1, 2) if self.phrases.isChecked() else (1, 1)}


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
