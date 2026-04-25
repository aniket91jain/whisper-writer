import os
import sys
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                              QScrollArea, QPushButton, QFrame, QApplication,
                              QSizePolicy)
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QFont, QCursor

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from ui.base_window import BaseWindow


def _parse_log(log_path):
    """Read transcript_log.txt; return (timestamp, polished) list, newest first."""
    if not os.path.isfile(log_path):
        return []
    with open(log_path, 'r', encoding='utf-8') as f:
        content = f.read()
    entries = []
    for block in content.strip().split('\n\n'):
        lines = block.strip().split('\n')
        timestamp = polished = ''
        for line in lines:
            s = line.strip()
            if s.startswith('[') and s.endswith(']'):
                timestamp = s[1:-1]
            elif s.startswith('POLISHED:'):
                polished = s[9:].strip()
        if polished:
            entries.append((timestamp, polished))
    return list(reversed(entries))  # newest first


def _simulate_paste():
    """Simulate Ctrl+V in whichever window currently has keyboard focus."""
    try:
        from pynput.keyboard import Key, Controller as _KbController
        kb = _KbController()
        kb.press(Key.ctrl)
        kb.press('v')
        kb.release('v')
        kb.release(Key.ctrl)
    except Exception:
        pass


class TranscriptCard(QFrame):
    def __init__(self, timestamp, polished, parent=None):
        super().__init__(parent)
        self._polished = polished
        self.setObjectName('TranscriptCard')
        self.setCursor(QCursor(Qt.PointingHandCursor))
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self._set_style(hovered=False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(3)

        ts_label = QLabel(timestamp)
        ts_label.setFont(QFont('Segoe UI', 8))
        ts_label.setStyleSheet('color: #999; background: transparent;')
        layout.addWidget(ts_label)

        self._text_label = QLabel(polished)
        self._text_label.setFont(QFont('Segoe UI', 10))
        self._text_label.setWordWrap(True)
        self._text_label.setStyleSheet('color: #2c2c2c; background: transparent;')
        layout.addWidget(self._text_label)

        self._status_label = QLabel()
        self._status_label.setFont(QFont('Segoe UI', 8))
        self._status_label.setStyleSheet('color: #3a863a; background: transparent;')
        self._status_label.hide()
        layout.addWidget(self._status_label)

    def _set_style(self, hovered):
        bg, border = ('#eaf4ea', '#5aac5a') if hovered else ('#f7f7f7', '#e0e0e0')
        self.setStyleSheet(f'''
            QFrame#TranscriptCard {{
                background-color: {bg};
                border: 1px solid {border};
                border-radius: 8px;
            }}
        ''')

    def enterEvent(self, event):
        self._set_style(hovered=True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._set_style(hovered=False)
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            QApplication.clipboard().setText(self._polished)
            # Paste into whichever app still has focus (50ms lets clipboard settle)
            QTimer.singleShot(50, _simulate_paste)
            self._status_label.setText('✓  Pasted at cursor')
            self._status_label.show()
            QTimer.singleShot(2000, self._status_label.hide)
        event.accept()  # don't bubble up to BaseWindow drag handler


class TranscriptHistoryWindow(BaseWindow):
    def __init__(self, log_path):
        super().__init__('Transcript History', 540, 680)
        self._log_path = log_path

        # Float above all other windows; Tool keeps it off the taskbar.
        # WA_ShowWithoutActivating prevents stealing focus when show() is called.
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)

        self._init_content()
        self._load()

    def showEvent(self, event):
        super().showEvent(event)
        # WS_EX_NOACTIVATE: window receives mouse events but never becomes the
        # active (keyboard-focus) window when clicked — so clicks paste into the
        # previously focused app via _simulate_paste().
        try:
            import ctypes
            GWL_EXSTYLE = -20
            WS_EX_NOACTIVATE = 0x08000000
            hwnd = int(self.winId())
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE)
        except Exception:
            pass  # Non-Windows or ctypes unavailable; window still stays on top

    def _init_content(self):
        header_row = QHBoxLayout()
        hint = QLabel('Click any entry to paste at cursor')
        hint.setFont(QFont('Segoe UI', 9))
        hint.setStyleSheet('color: #666;')
        header_row.addWidget(hint)
        header_row.addStretch()

        refresh_btn = QPushButton('↻  Refresh')
        refresh_btn.setFont(QFont('Segoe UI', 9))
        refresh_btn.setFixedHeight(28)
        refresh_btn.setCursor(QCursor(Qt.PointingHandCursor))
        refresh_btn.setStyleSheet('''
            QPushButton {
                background: #f0f0f0;
                border: 1px solid #ccc;
                border-radius: 4px;
                padding: 0 10px;
                color: #404040;
            }
            QPushButton:hover { background: #e0e0e0; }
        ''')
        refresh_btn.clicked.connect(self._load)
        header_row.addWidget(refresh_btn)
        self.main_layout.addLayout(header_row)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet('QScrollArea { border: none; background: transparent; }')

        self._container = QWidget()
        self._container.setStyleSheet('background: transparent;')
        self._cards_layout = QVBoxLayout(self._container)
        self._cards_layout.setContentsMargins(0, 0, 6, 0)
        self._cards_layout.setSpacing(8)
        self._cards_layout.addStretch()

        self._scroll.setWidget(self._container)
        self.main_layout.addWidget(self._scroll)

    def _load(self):
        while self._cards_layout.count() > 1:
            item = self._cards_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        entries = _parse_log(self._log_path)
        if not entries:
            label = QLabel('No transcriptions yet.\nDictate something and come back.')
            label.setFont(QFont('Segoe UI', 10))
            label.setStyleSheet('color: #aaa;')
            label.setAlignment(Qt.AlignCenter)
            self._cards_layout.insertWidget(0, label)
            return

        for i, (ts, polished) in enumerate(entries):
            card = TranscriptCard(ts, polished, self._container)
            self._cards_layout.insertWidget(i, card)

    def closeEvent(self, event):
        self.hide()
        event.ignore()
