"""
Active window and process detection helpers (Windows-focused).
Returns both window title and process name using ctypes + psutil, with
pygetwindow as a best-effort fallback for the title only.
"""
import logging
import psutil

try:
    import pygetwindow as gw  # optional
except Exception:  # pragma: no cover
    gw = None

logger = logging.getLogger(__name__)


def _get_foreground_process_info():
    """Return (pid, process_name) for the foreground window using ctypes.

    This avoids requiring pywin32 by calling Win32 APIs via ctypes.
    Returns (None, None) on failure.
    """
    try:
        import ctypes
        import ctypes.wintypes as wt

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        GetForegroundWindow = user32.GetForegroundWindow
        GetWindowThreadProcessId = user32.GetWindowThreadProcessId

        hwnd = GetForegroundWindow()
        if not hwnd:
            return None, None

        pid = wt.DWORD()
        GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        pid_val = pid.value if pid.value else None
        if not pid_val:
            return None, None

        try:
            p = psutil.Process(pid_val)
            return pid_val, p.name()
        except Exception:
            return pid_val, None
    except Exception as e:  # pragma: no cover
        logger.debug(f"ctypes foreground process resolution failed: {e}")
        return None, None


def get_active_context():
    """Return a dict with keys: title, process_name.

    - title: Active window title (best-effort)
    - process_name: Foreground process name (best-effort)
    """
    title = None
    if gw is not None:
        try:
            win = gw.getActiveWindow()
            if win:
                title = win.title or None
        except Exception as e:
            logger.debug(f"pygetwindow getActiveWindow failed: {e}")

    pid, proc_name = _get_foreground_process_info()

    # Fallbacks
    if not title:
        title = proc_name or "Unknown Window"
    if not proc_name:
        try:
            # last resort: first running process (not ideal)
            for proc in psutil.process_iter(['name']):
                proc_name = proc.info.get('name')
                if proc_name:
                    break
        except Exception:
            proc_name = None

    return {"title": title, "process_name": proc_name}


def get_active_window():
    """Legacy helper returning only the window title (backward-compat)."""
    ctx = get_active_context()
    return ctx.get("title") or "Unknown Window"