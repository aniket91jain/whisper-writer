import os
import sys
import subprocess
from dotenv import load_dotenv


def _peek_singleton_and_signal_if_running() -> bool:
    """If a Whisper PC instance is already running, pulse its show-event so it
    surfaces its window, and return True. Otherwise return False.

    This is a peek, not a hold: we open the named mutex with OpenMutexW and
    close it immediately. main.py is the canonical owner of the mutex
    (CreateMutexW). This way the legitimate parent→child handoff (run.py
    spawning main.py) doesn't fight over ownership.

    Doing this in run.py too — even though main.py also enforces — saves the
    cost of spinning up Python + PyQt for the duplicate just to have it exit.
    """
    try:
        import ctypes
    except Exception:
        return False
    kernel32 = ctypes.windll.kernel32
    SYNCHRONIZE = 0x00100000
    EVENT_MODIFY_STATE = 0x0002
    MUTEX_NAME = 'WhisperPC.SingleInstance.v3'
    EVENT_NAME = 'WhisperPC.ShowEvent.v3'
    mutex = kernel32.OpenMutexW(SYNCHRONIZE, False, MUTEX_NAME)
    if not mutex:
        return False  # no peer
    kernel32.CloseHandle(mutex)
    # Surface the existing instance's main window.
    event = kernel32.OpenEventW(EVENT_MODIFY_STATE, False, EVENT_NAME)
    if event:
        kernel32.SetEvent(event)
        kernel32.CloseHandle(event)
    return True


if _peek_singleton_and_signal_if_running():
    print('Whisper PC is already running; surfacing existing window.')
    sys.exit(0)

print('Starting Whisper PC...')
load_dotenv()
subprocess.run([sys.executable, os.path.join('src', 'main.py')])
