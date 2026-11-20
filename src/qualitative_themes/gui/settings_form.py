"""Settings controls shared by the Find themes and Adjust themes dialogs.

Both dialogs ask for the same keyword and naming choices, so they're built
here once. A new keyword setting is added in KeywordOptions and both dialogs
pick it up.
"""
from PySide6.QtWidgets import QComboBox, QFormLayout, QPlainTextEdit

from qualitative_themes.analysis.settings import DEFAULT_NAMING_MODEL, parse_ignored_words

WORDS_AND_PHRASES, WORDS_ONLY = 0, 1


class KeywordOptions:
    """Words to ignore and keyword phrases, added as rows to a form.

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
            self.ignored.setToolTip("Ignored words affect extracted keywords; passage embeddings still use the complete text.")
        form.addRow("Words to ignore", self.ignored)
        self.phrases = QComboBox()
        self.phrases.addItems(["Words and two-word phrases", "Words only"])
        self.phrases.setCurrentIndex(WORDS_ONLY if settings.ngram_range == (1, 1) else WORDS_AND_PHRASES)
        form.addRow("Keyword phrases", self.phrases)

    def changed_signals(self):
        """Signals that fire when any of these controls changes (for re-validating)."""
        return [self.ignored.textChanged, self.phrases.currentIndexChanged]

    def set_enabled(self, enabled: bool):
        self.ignored.setEnabled(enabled)
        self.phrases.setEnabled(enabled)

    def reset(self):
        self.ignored.clear()
        self.phrases.setCurrentIndex(WORDS_AND_PHRASES)

    def values(self) -> dict:
        return {"ignored_words": parse_ignored_words(self.ignored.toPlainText()),
                "ngram_range": (1, 1) if self.phrases.currentIndex() == WORDS_ONLY else (1, 2)}


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
