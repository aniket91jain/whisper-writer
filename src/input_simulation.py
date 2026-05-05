import subprocess
import os
import signal
import time
import ctypes
import ctypes.wintypes
import pyperclip
from pynput.keyboard import Controller as PynputController, Key

from utils import ConfigManager


class _GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ('cbSize', ctypes.wintypes.DWORD),
        ('flags', ctypes.wintypes.DWORD),
        ('hwndActive', ctypes.wintypes.HWND),
        ('hwndFocus', ctypes.wintypes.HWND),
        ('hwndCapture', ctypes.wintypes.HWND),
        ('hwndMenuOwner', ctypes.wintypes.HWND),
        ('hwndMoveSize', ctypes.wintypes.HWND),
        ('hwndCaret', ctypes.wintypes.HWND),
        ('rcCaret', ctypes.wintypes.RECT),
    ]

def run_command_or_exit_on_failure(command):
    """
    Run a shell command and exit if it fails.

    Args:
        command (list): The command to run as a list of strings.
    """
    try:
        subprocess.run(command, check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error running command: {e}")
        exit(1)

class InputSimulator:
    """
    A class to simulate keyboard input using various methods.
    """

    # Window class names that accept keyboard text input.
    # Includes both child-level focused controls (Edit, RichEdit, Scintilla,
    # Chrome_RenderWidgetHostHWND) AND top-level window classes as a fallback —
    # Chrome's render widget lives on a separate thread so GetGUIThreadInfo on
    # the browser thread returns hwndFocus=0, leaving only the top-level handle.
    _TEXT_INPUT_CLASSES = {
        'Edit',                          # Standard Win32 text boxes
        'RichEdit20W', 'RichEdit20A',    # Rich text editors (WordPad, etc.)
        'RICHEDIT50W',                   # Word and newer rich text controls
        'Scintilla',                     # Code editors (Notepad++, etc.)
        'Chrome_RenderWidgetHostHWND',   # Chrome / Edge / Electron (child control)
        'Chrome_WidgetWin_1',            # Chrome / Edge / Electron (top-level) ← main fix
        'MozillaWindowClass',            # Firefox
        'Notepad',                       # Windows Notepad
        'ConsoleWindowClass',            # Windows Terminal / cmd
    }

    # Native classes whose cursor position can be queried via Win32 messages
    # (EM_GETSEL / WM_GETTEXT). Anything outside this set falls back to the
    # clipboard-probe path for the smart leading-space logic.
    _NATIVE_TEXT_CLASSES = {
        'Edit',
        'RichEdit20W', 'RichEdit20A', 'RICHEDIT50W',
        'Scintilla',
        'Notepad',
    }

    def __init__(self):
        """
        Initialize the InputSimulator with the specified configuration.
        """
        self.input_method = ConfigManager.get_config_value('post_processing', 'input_method')
        self.dotool_process = None

        if self.input_method in ('pynput', 'clipboard'):
            self.keyboard = PynputController()
        elif self.input_method == 'dotool':
            self._initialize_dotool()

    def _initialize_dotool(self):
        """
        Initialize the dotool process for input simulation.
        """
        self.dotool_process = subprocess.Popen("dotool", stdin=subprocess.PIPE, text=True)
        assert self.dotool_process.stdin is not None

    def _terminate_dotool(self):
        """
        Terminate the dotool process if it's running.
        """
        if self.dotool_process:
            os.kill(self.dotool_process.pid, signal.SIGINT)
            self.dotool_process = None

    def typewrite(self, text):
        """
        Simulate typing the given text with the specified interval between keystrokes.

        Args:
            text (str): The text to type.
        """
        interval = ConfigManager.get_config_value('post_processing', 'writing_key_press_delay')
        if self.input_method == 'clipboard':
            self._typewrite_clipboard(text)
        elif self.input_method == 'pynput':
            self._typewrite_pynput(text, interval)
        elif self.input_method == 'ydotool':
            self._typewrite_ydotool(text, interval)
        elif self.input_method == 'dotool':
            self._typewrite_dotool(text, interval)

    def _focused_text_control(self):
        """Locate the focused text-input control in the foreground window.

        Returns (focus_hwnd, class_name) when a known text-input class is
        focused, otherwise None. Single Win32 round-trip shared by both the
        focus check and the smart leading-space lookup.
        """
        try:
            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                return None
            thread_id = user32.GetWindowThreadProcessId(hwnd, None)
            info = _GUITHREADINFO()
            info.cbSize = ctypes.sizeof(_GUITHREADINFO)
            if not user32.GetGUIThreadInfo(thread_id, ctypes.byref(info)):
                return None
            focus_hwnd = info.hwndFocus or hwnd
            buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(focus_hwnd, buf, 256)
            class_name = buf.value
            if class_name not in self._TEXT_INPUT_CLASSES:
                return None
            return (focus_hwnd, class_name)
        except Exception:
            return None

    def _is_text_input_focused(self):
        """Return True if the foreground window's focused control is a known text input."""
        return self._focused_text_control() is not None

    def _char_before_cursor(self, focus_hwnd, class_name):
        """Return the character immediately before the cursor in the focused field.

        Returns:
            ''   - cursor is at position 0; no preceding char.
            str  - the preceding character (single code unit).
            None - couldn't determine (active selection, ambiguous probe, error).
        """
        if class_name in self._NATIVE_TEXT_CLASSES:
            return self._char_before_cursor_native(focus_hwnd)
        return self._char_before_cursor_probe()

    @staticmethod
    def _char_before_cursor_native(focus_hwnd):
        """Win32 fast path: EM_GETSEL + WM_GETTEXT against the focused control."""
        try:
            user32 = ctypes.windll.user32
            EM_GETSEL = 0x00B0
            WM_GETTEXTLENGTH = 0x000E
            WM_GETTEXT = 0x000D

            start = ctypes.wintypes.DWORD(0)
            end = ctypes.wintypes.DWORD(0)
            user32.SendMessageW(focus_hwnd, EM_GETSEL,
                                ctypes.byref(start), ctypes.byref(end))
            if start.value != end.value:
                # Live selection — paste will overwrite; don't add a space.
                return None
            if start.value == 0:
                return ''

            length = user32.SendMessageW(focus_hwnd, WM_GETTEXTLENGTH, 0, 0)
            if length <= 0 or start.value > length:
                return ''
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.SendMessageW(focus_hwnd, WM_GETTEXT, length + 1, buf)
            text = buf.value
            if start.value - 1 >= len(text):
                return ''
            return text[start.value - 1]
        except Exception:
            return None

    def _char_before_cursor_probe(self):
        """Clipboard probe fallback: select one char left, copy, restore."""
        try:
            saved = pyperclip.paste()
        except Exception:
            saved = ''
        try:
            # Multi-char so it can never be confused with a single preceding char.
            sentinel = '__WW_PROBE_SENTINEL__'
            try:
                pyperclip.copy(sentinel)
            except Exception:
                return None
            time.sleep(0.02)
            with self.keyboard.pressed(Key.shift):
                self.keyboard.press(Key.left)
                self.keyboard.release(Key.left)
            time.sleep(0.02)
            with self.keyboard.pressed(Key.ctrl):
                self.keyboard.press('c')
                self.keyboard.release('c')
            time.sleep(0.04)
            try:
                probe = pyperclip.paste()
            except Exception:
                probe = sentinel
            # Collapse the selection back to the original cursor position.
            self.keyboard.press(Key.right)
            self.keyboard.release(Key.right)

            if probe == sentinel:
                # Clipboard untouched - nothing was selected (cursor at start).
                return ''
            if len(probe) == 1:
                return probe
            # Apps that "smart-copy" the current line on empty selection
            # produce ambiguous results - bail out conservatively.
            return None
        finally:
            try:
                pyperclip.copy(saved)
            except Exception:
                pass

    def _typewrite_sendinput(self, text):
        """Send entire text in one batched Windows SendInput call (instantaneous)."""
        INPUT_KEYBOARD    = 1
        KEYEVENTF_UNICODE = 0x0004
        KEYEVENTF_KEYUP   = 0x0002
        ULONG_PTR         = ctypes.c_void_p  # 8 bytes on 64-bit; None → null pointer

        class MOUSEINPUT(ctypes.Structure):  # must be in union to give union correct size (32 B)
            _fields_ = [('dx', ctypes.c_long), ('dy', ctypes.c_long),
                        ('mouseData', ctypes.c_ulong), ('dwFlags', ctypes.c_ulong),
                        ('time', ctypes.c_ulong), ('dwExtraInfo', ULONG_PTR)]

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [('wVk', ctypes.wintypes.WORD), ('wScan', ctypes.wintypes.WORD),
                        ('dwFlags', ctypes.wintypes.DWORD), ('time', ctypes.wintypes.DWORD),
                        ('dwExtraInfo', ULONG_PTR)]

        class HARDWAREINPUT(ctypes.Structure):
            _fields_ = [('uMsg', ctypes.wintypes.DWORD),
                        ('wParamL', ctypes.wintypes.WORD), ('wParamH', ctypes.wintypes.WORD)]

        class _INPUT_UNION(ctypes.Union):
            _fields_ = [('mi', MOUSEINPUT), ('ki', KEYBDINPUT), ('hi', HARDWAREINPUT)]

        class INPUT(ctypes.Structure):
            _fields_ = [('type', ctypes.wintypes.DWORD), ('union', _INPUT_UNION)]

        # Encode as UTF-16LE so chars outside the BMP become proper surrogate pairs
        raw = text.encode('utf-16-le')
        inputs = []
        for i in range(0, len(raw), 2):
            scan = raw[i] | (raw[i + 1] << 8)
            for flags in (KEYEVENTF_UNICODE, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP):
                ki = KEYBDINPUT(wVk=0, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=None)
                inputs.append(INPUT(INPUT_KEYBOARD, _INPUT_UNION(ki=ki)))

        n = len(inputs)
        if n:
            ctypes.windll.user32.SendInput(n, (INPUT * n)(*inputs), ctypes.sizeof(INPUT))

    def _typewrite_clipboard(self, text):
        focus = self._focused_text_control()
        if focus is not None:
            if ConfigManager.get_config_value('post_processing', 'add_leading_space_if_needed'):
                ch = self._char_before_cursor(*focus)
                if ch and not ch.isspace():
                    text = ' ' + text
            # Save whatever the user had copied, paste transcription, then restore.
            # Ctrl+V is the only truly instantaneous path (browsers process WM_CHAR one at a time).
            try:
                saved = pyperclip.paste()
            except Exception:
                saved = ''
            pyperclip.copy(text)
            time.sleep(0.05)
            with self.keyboard.pressed(Key.ctrl):
                self.keyboard.press('v')
                self.keyboard.release('v')
            time.sleep(0.1)
            try:
                pyperclip.copy(saved)
            except Exception:
                pass
        else:
            # No text field focused; leave transcription in clipboard for manual pasting
            pyperclip.copy(text)

    def _typewrite_pynput(self, text, interval):
        """
        Simulate typing using pynput.

        Args:
            text (str): The text to type.
            interval (float): The interval between keystrokes in seconds.
        """
        for char in text:
            self.keyboard.press(char)
            self.keyboard.release(char)
            time.sleep(interval)

    def _typewrite_ydotool(self, text, interval):
        """
        Simulate typing using ydotool.

        Args:
            text (str): The text to type.
            interval (float): The interval between keystrokes in seconds.
        """
        cmd = "ydotool"
        run_command_or_exit_on_failure([
            cmd,
            "type",
            "--key-delay",
            str(interval * 1000),
            "--",
            text,
        ])

    def _typewrite_dotool(self, text, interval):
        """
        Simulate typing using dotool.

        Args:
            text (str): The text to type.
            interval (float): The interval between keystrokes in seconds.
        """
        assert self.dotool_process and self.dotool_process.stdin
        self.dotool_process.stdin.write(f"typedelay {interval * 1000}\n")
        self.dotool_process.stdin.write(f"type {text}\n")
        self.dotool_process.stdin.flush()

    def cleanup(self):
        """
        Perform cleanup operations, such as terminating the dotool process.
        """
        if self.input_method == 'dotool':
            self._terminate_dotool()
