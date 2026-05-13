"""Settings tab for editing the structured proper-nouns list that drives the
polish prompt's PROPER NOUNS active-correction rule.

Mirrors the schema at `llm_polish.proper_nouns`:

    proper_nouns:
      locations: [{word, misheard: [...]}, ...]
      people:    [{word, misheard: [...]}, ...]
      products:  [{word, misheard: [...]}, ...]

Tab content: one group box per category (Locations / People / Products), each
with a list of entries and Add/Edit/Delete buttons. Add/Edit opens a small
dialog with two fields — the correct spelling and an optional comma-separated
list of misheard variants. On save, the parent SettingsWindow's normal save
flow writes back via ConfigManager.set_config_value + save_config; the polish
prompt picks up the new list on the next dictation (no app restart needed).
"""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QListWidget, QListWidgetItem,
    QPushButton, QDialog, QDialogButtonBox, QLineEdit, QLabel, QFormLayout,
    QMessageBox,
)


_CATEGORIES = ("locations", "people", "products")
_CATEGORY_LABELS = {
    "locations": "Locations",
    "people": "People",
    "products": "Products",
}


class _EntryDialog(QDialog):
    """Modal dialog for adding/editing a single proper-noun entry."""

    def __init__(self, parent=None, title="Add entry", word="", misheard=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)

        form = QFormLayout()
        self.word_edit = QLineEdit(word)
        self.word_edit.setPlaceholderText("e.g. Adhiraj")
        self.misheard_edit = QLineEdit(", ".join(misheard or []))
        self.misheard_edit.setPlaceholderText("e.g. addaraj, adira (optional)")

        form.addRow(QLabel("Word:"), self.word_edit)
        form.addRow(QLabel("Misheard variants:"), self.misheard_edit)

        layout = QVBoxLayout(self)
        layout.addLayout(form)

        hint = QLabel(
            "Misheard variants are Whisper STT mishearings that polish should "
            "replace with the correct word. Leave blank if you only want STT "
            "vocabulary bias and no active correction."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")
        layout.addWidget(hint)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.resize(400, 160)

    def _on_accept(self):
        if not self.word_edit.text().strip():
            QMessageBox.warning(self, "Missing word",
                                "The 'Word' field is required.")
            return
        self.accept()

    def values(self):
        word = self.word_edit.text().strip()
        misheard = [m.strip() for m in self.misheard_edit.text().split(",")]
        misheard = [m for m in misheard if m]
        return word, misheard


class CustomVocabularyTab(QWidget):
    """Widget that owns the in-memory copy of the structured proper-nouns list
    and lets the user edit it. Round-trips with `llm_polish.proper_nouns` in
    ConfigManager via [load_from_config] and [save_to_config].
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = {cat: [] for cat in _CATEGORIES}
        self._lists = {}
        self._build_ui()
        self.load_from_config()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Words and proper nouns that polish should actively correct in "
            "your dictations. Entries also become part of the Whisper STT "
            "vocabulary hint, biasing transcription toward them. Words "
            "auto-added via the 'X spelled X-X-X' trigger land under People — "
            "use Edit to move them to another category."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: gray;")
        layout.addWidget(intro)

        for cat in _CATEGORIES:
            layout.addWidget(self._build_group(cat))

    def _build_group(self, category):
        group = QGroupBox(_CATEGORY_LABELS[category])
        group_layout = QVBoxLayout(group)

        list_widget = QListWidget()
        list_widget.setSelectionMode(QListWidget.SingleSelection)
        list_widget.itemDoubleClicked.connect(
            lambda _it, c=category: self._edit_selected(c))
        self._lists[category] = list_widget
        group_layout.addWidget(list_widget)

        buttons = QHBoxLayout()
        add_btn = QPushButton("Add")
        add_btn.clicked.connect(lambda _checked, c=category: self._add(c))
        edit_btn = QPushButton("Edit")
        edit_btn.clicked.connect(lambda _checked, c=category: self._edit_selected(c))
        delete_btn = QPushButton("Delete")
        delete_btn.clicked.connect(lambda _checked, c=category: self._delete_selected(c))
        buttons.addWidget(add_btn)
        buttons.addWidget(edit_btn)
        buttons.addWidget(delete_btn)
        buttons.addStretch(1)
        group_layout.addLayout(buttons)
        return group

    # ----- Config round-trip -----

    def load_from_config(self):
        """Load proper_nouns from ConfigManager into the in-memory state and
        the list widgets. Safe to call repeatedly (used on init and reset)."""
        from utils import ConfigManager
        pn = ConfigManager.get_config_value("llm_polish", "proper_nouns") or {}
        for cat in _CATEGORIES:
            entries = pn.get(cat) if isinstance(pn, dict) else None
            self._state[cat] = [self._normalize_entry(e) for e in (entries or [])
                                if self._is_valid_entry(e)]
        self._refresh_all_lists()

    def save_to_config(self):
        """Flush the in-memory state into ConfigManager. The parent
        SettingsWindow then calls ConfigManager.save_config()."""
        from utils import ConfigManager
        snapshot = {cat: [dict(word=e["word"], misheard=list(e["misheard"]))
                          for e in self._state[cat]]
                    for cat in _CATEGORIES}
        ConfigManager.set_config_value(snapshot, "llm_polish", "proper_nouns")

    @staticmethod
    def _is_valid_entry(entry):
        return isinstance(entry, dict) and isinstance(entry.get("word"), str) \
            and entry["word"].strip()

    @staticmethod
    def _normalize_entry(entry):
        word = entry["word"].strip()
        raw = entry.get("misheard") or []
        misheard = [m.strip() for m in raw if isinstance(m, str) and m.strip()]
        return {"word": word, "misheard": misheard}

    # ----- List widget updates -----

    def _refresh_all_lists(self):
        for cat in _CATEGORIES:
            self._refresh_list(cat)

    def _refresh_list(self, category):
        widget = self._lists[category]
        widget.clear()
        for entry in self._state[category]:
            widget.addItem(QListWidgetItem(self._format_entry(entry)))

    @staticmethod
    def _format_entry(entry):
        if entry["misheard"]:
            return f"{entry['word']}  —  misheard: {', '.join(entry['misheard'])}"
        return entry["word"]

    # ----- Button handlers -----

    def _add(self, category):
        dialog = _EntryDialog(self, title=f"Add to {_CATEGORY_LABELS[category]}")
        if dialog.exec_() != QDialog.Accepted:
            return
        word, misheard = dialog.values()
        if self._duplicate_word(category, word):
            QMessageBox.information(self, "Duplicate",
                                    f"'{word}' is already in {_CATEGORY_LABELS[category]}.")
            return
        self._state[category].append({"word": word, "misheard": misheard})
        self._refresh_list(category)

    def _edit_selected(self, category):
        widget = self._lists[category]
        row = widget.currentRow()
        if row < 0 or row >= len(self._state[category]):
            return
        existing = self._state[category][row]
        dialog = _EntryDialog(self,
                              title=f"Edit {_CATEGORY_LABELS[category]} entry",
                              word=existing["word"],
                              misheard=existing["misheard"])
        if dialog.exec_() != QDialog.Accepted:
            return
        word, misheard = dialog.values()
        if word.lower() != existing["word"].lower() and self._duplicate_word(category, word):
            QMessageBox.information(self, "Duplicate",
                                    f"'{word}' is already in {_CATEGORY_LABELS[category]}.")
            return
        self._state[category][row] = {"word": word, "misheard": misheard}
        self._refresh_list(category)
        widget.setCurrentRow(row)

    def _delete_selected(self, category):
        widget = self._lists[category]
        row = widget.currentRow()
        if row < 0 or row >= len(self._state[category]):
            return
        entry = self._state[category][row]
        reply = QMessageBox.question(
            self,
            "Delete entry",
            f"Remove '{entry['word']}' from {_CATEGORY_LABELS[category]}?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        del self._state[category][row]
        self._refresh_list(category)

    def _duplicate_word(self, category, word):
        lower = word.strip().lower()
        return any(e["word"].lower() == lower for e in self._state[category])
