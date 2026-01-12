"""
Activity detector using pynput with thread-safe API and callbacks.
"""
import time
import threading
from datetime import datetime
from pynput import mouse, keyboard
import logging
import math
from typing import Callable, Optional
from config import MOVEMENT_BATCH_INTERVAL, LOG_SENSITIVE_KEYS, IDLE_THRESHOLD
from database import log_movement_batch

logger = logging.getLogger(__name__)


class ActivityDetector:
    def __init__(self, on_status_change: Optional[Callable[[str], None]] = None):
        self.last_activity_time = time.time()
        self.lock = threading.Lock()
        self._mouse_listener = None
        self._keyboard_listener = None
        self.is_running = False
        self.employee_id: Optional[str] = None
        self.on_status_change = on_status_change
        self._current_status = 'active'
        # Movement tracking
        self.movement_events = []
        self.last_mouse_pos = None
        self.batch_thread = None
        self.status_thread = None

    def _on_key_press(self, key):
        try:
            if not LOG_SENSITIVE_KEYS and hasattr(key, 'char') and key.char and key.char.isalpha():
                return
            detail = key.char if hasattr(key, 'char') and key.char else str(key)
            event = (datetime.now().isoformat(), 'key_press', detail, 0.0)
            self._add_event(event)
            self.last_activity_time = time.time()
        except AttributeError:
            pass

    def _on_mouse_move(self, x, y):
        if self.last_mouse_pos:
            dx = x - self.last_mouse_pos[0]
            dy = y - self.last_mouse_pos[1]
            distance = math.sqrt(dx**2 + dy**2)
            event = (datetime.now().isoformat(), 'mouse_move', f"x:{x},y:{y}", distance)
            self._add_event(event)
        self.last_mouse_pos = (x, y)
        self.last_activity_time = time.time()

    def _on_mouse_click(self, x, y, button, pressed):
        if pressed:
            detail = str(button).split('.')[-1]
            event = (datetime.now().isoformat(), 'mouse_click', detail, 0.0)
            self._add_event(event)
            self.last_activity_time = time.time()

    def _add_event(self, event):
        with self.lock:
            self.movement_events.append(event)
            if len(self.movement_events) >= 50:
                self._flush_batch()

    def _batch_loop(self):
        while self.is_running:
            time.sleep(MOVEMENT_BATCH_INTERVAL)
            self._flush_batch()

    def _status_loop(self):
        while self.is_running:
            time.sleep(1)
            status = self.get_status()
            if status != self._current_status:
                self._current_status = status
                if self.on_status_change:
                    try:
                        self.on_status_change(status)
                    except Exception:
                        logger.exception("on_status_change callback failed")

    def _flush_batch(self):
        if self.movement_events:
            log_movement_batch(self.movement_events, employee_id=self.employee_id)
            self.movement_events.clear()

    def _start_listeners(self):
        self._keyboard_listener = keyboard.Listener(on_press=self._on_key_press)
        self._mouse_listener = mouse.Listener(
            on_move=self._on_mouse_move,
            on_click=self._on_mouse_click
        )
        self._mouse_listener.daemon = True
        self._keyboard_listener.daemon = True
        self._mouse_listener.start()
        self._keyboard_listener.start()

    def start(self, employee_id: str):
        self.employee_id = employee_id
        self.last_activity_time = time.time()
        self.is_running = True
        self._start_listeners()
        self.batch_thread = threading.Thread(target=self._batch_loop, daemon=True)
        self.batch_thread.start()
        self.status_thread = threading.Thread(target=self._status_loop, daemon=True)
        self.status_thread.start()

    def stop(self):
        self.is_running = False
        self._flush_batch()
        if self._mouse_listener:
            self._mouse_listener.stop()
        if self._keyboard_listener:
            self._keyboard_listener.stop()
        if self.batch_thread:
            self.batch_thread.join(timeout=5)
        if self.status_thread:
            self.status_thread.join(timeout=5)

    def get_status(self) -> str:
        with self.lock:
            idle_time = time.time() - self.last_activity_time
            return 'active' if idle_time <= IDLE_THRESHOLD else 'idle'

    def get_summary(self) -> str:
        with self.lock:
            keys = sum(1 for _, t, _, _ in self.movement_events if t == 'key_press')
            clicks = sum(1 for _, t, _, _ in self.movement_events if t == 'mouse_click')
            distance = sum(d for _, _, _, d in self.movement_events if d > 0)
            return f"{keys} keys, {clicks} clicks, {distance:.0f}px moved"