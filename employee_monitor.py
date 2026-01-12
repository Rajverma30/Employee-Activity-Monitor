"""
Main Employee Monitor class and entry point.
Orchestrates all modules; runs in background.
"""
import logging
import signal
import sys
import time
import threading
from datetime import datetime
from activity_detector import ActivityDetector
from screenshot_handler import ScreenshotHandler
from idle_photo_handler import capture_idle_photo
from window_detector import get_active_context, get_active_window
from presence_detector import PresenceDetector
from database import init_db, log_event, insert_screenshot, upsert_employee
from reporter import generate_daily_report
from config import LOG_FILE, ACTIVITY_CHECK_INTERVAL, IDLE_WEBCAM_THRESHOLD, load_config, load_runtime_settings
import random
import psutil
import re

# Setup logging with UTF-8 support
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ],
    force=True
)
logger = logging.getLogger(__name__)

class EmployeeMonitor:
    def __init__(self):
        self.cfg = load_config()
        self.employee_id = self.cfg.employee_id
        self.activity_detector = ActivityDetector()
        self.screenshot_handler = ScreenshotHandler()
        self.presence_detector = PresenceDetector()
        self.is_running = False
        self.thread = None
        self.shutdown_event = threading.Event()
        self.idle_start_time = None  # Track idle duration
        # precompile non-work regex
        self._non_work_regexes = [re.compile(pat, re.IGNORECASE) for pat in (self.cfg.non_work_patterns or [])]
        self._whitelist = set((self.cfg.work_whitelist or []))
        # random screenshot scheduler state
        self._next_random_shot_at = 0.0
        self._last_settings_load = 0.0
        self._settings_cache = load_runtime_settings()

    def _reload_settings_periodically(self):
        now = time.time()
        if now - self._last_settings_load >= 30:  # refresh every 30s
            try:
                self._settings_cache = load_runtime_settings()
            except Exception:
                pass
            self._last_settings_load = now

    def _schedule_next_shot(self, base: int, jitter: int):
        now = time.time()
        # randomize in [base - jitter, base + jitter], clamp to >= 30s
        low = max(30, int(base) - max(0, int(jitter)))
        high = max(low, int(base) + max(0, int(jitter)))
        delay = random.randint(low, high)
        self._next_random_shot_at = now + delay

    def _is_non_work_context(self, window_title: str) -> bool:
        if not window_title:
            return False
        for w in self._whitelist:
            if w.lower() in window_title.lower():
                return False
        return any(r.search(window_title or "") for r in self._non_work_regexes)

    def _monitoring_loop(self):
        """Main loop: Check activity, window, presence; log periodically."""
        last_window = None
        # initialize next random shot
        s = self._settings_cache
        self._schedule_next_shot(int(s.get('screenshot_interval_seconds', 600)), int(s.get('screenshot_jitter_seconds', 60)))
        while self.is_running and not self.shutdown_event.is_set():
            try:
                self._reload_settings_periodically()
                # Get current status
                activity_status = self.activity_detector.get_status()
                ctx = get_active_context()
                current_window = ctx.get('title')
                current_process = ctx.get('process_name')

                # Handle Idle Webcam Photo
                if activity_status == 'idle':
                    if self.idle_start_time is None:
                        self.idle_start_time = time.time()
                    elif (time.time() - self.idle_start_time) >= IDLE_WEBCAM_THRESHOLD:
                        webcam_photo = capture_idle_photo()
                        movement_summary = self.activity_detector.get_summary()
                        if current_window and movement_summary:
                            current_window += f" | {movement_summary}"
                        cpu = psutil.cpu_percent(interval=None)
                        mem = psutil.virtual_memory().percent
                        log_event(
                            employee_id=self.employee_id,
                            event_type='idle_photo',
                            active_window=current_window,
                            process_name=current_process,
                            cpu_percent=cpu,
                            mem_percent=mem,
                            idle_photo_path=webcam_photo,
                            note='Idle threshold reached'
                        )
                        logger.info("Idle webcam photo triggered.")
                        self.idle_start_time = None  # Reset after photo
                else:
                    self.idle_start_time = None

                # Screenshot and Log on Window Change
                if current_window != last_window:
                    last_window = current_window
                    logger.info(f"Window changed: {current_window}")
                    # do not append movement summary to window title for detection/logging
                    cpu = psutil.cpu_percent(interval=None)
                    mem = psutil.virtual_memory().percent
                    # check both title and process name for non-work
                    if self._is_non_work_context((current_window or "")) or self._is_non_work_context((current_process or "")):
                        screenshot_path = self.screenshot_handler.capture()
                        if screenshot_path:
                            insert_screenshot(self.employee_id, screenshot_path, reason='non_work_detected')
                        log_event(
                            employee_id=self.employee_id,
                            event_type='non_work_detected',
                            active_window=current_window,
                            process_name=current_process,
                            cpu_percent=cpu,
                            mem_percent=mem,
                            screenshot_path=screenshot_path if screenshot_path else None,
                            note='Non-work content detected'
                        )
                    else:
                        log_event(
                            employee_id=self.employee_id,
                            event_type='window_change',
                            active_window=current_window,
                            process_name=current_process,
                            cpu_percent=cpu,
                            mem_percent=mem,
                        )

                # Periodic Log if Active (no screenshot)
                elif activity_status == 'active':
                    cpu = psutil.cpu_percent(interval=None)
                    mem = psutil.virtual_memory().percent
                    log_event(
                        employee_id=self.employee_id,
                        event_type='active',
                        active_window=current_window,
                        process_name=current_process,
                        cpu_percent=cpu,
                        mem_percent=mem,
                    )

                # Log explicit idle transitions
                elif activity_status == 'idle':
                    cpu = psutil.cpu_percent(interval=None)
                    mem = psutil.virtual_memory().percent
                    log_event(
                        employee_id=self.employee_id,
                        event_type='idle',
                        active_window=current_window,
                        process_name=current_process,
                        cpu_percent=cpu,
                        mem_percent=mem,
                    )

                # Periodic randomized screenshot across all employees
                if time.time() >= self._next_random_shot_at:
                    try:
                        shot = self.screenshot_handler.capture()
                        if shot:
                            insert_screenshot(self.employee_id, shot, reason='scheduled_random')
                            logger.info("Random scheduled screenshot captured.")
                        # reschedule
                        s = self._settings_cache
                        self._schedule_next_shot(int(s.get('screenshot_interval_seconds', 600)), int(s.get('screenshot_jitter_seconds', 60)))
                    except Exception:
                        # still reschedule to avoid tight loops
                        s = self._settings_cache
                        self._schedule_next_shot(int(s.get('screenshot_interval_seconds', 600)), int(s.get('screenshot_jitter_seconds', 60)))

                time.sleep(ACTIVITY_CHECK_INTERVAL)
            except Exception as e:
                logger.error(f"Monitoring loop error: {e}")
                time.sleep(10)  # Backoff

    def start(self):
        """Start all components."""
        try:
            init_db()
            # CLI prompt to select or add employee
            try:
                from database import list_employees
                emps = list_employees()
                print("\nSelect employee to login as:")
                for idx, e in enumerate(emps, start=1):
                    print(f"  {idx}) {e.get('employee_id')} - {e.get('name') or '-'}")
                print("  N) Add new employee")
                choice = input("Enter number or N: ").strip()
                if choice.lower() == 'n':
                    emp_id = input("New Employee ID: ").strip()
                    name = input("Name (optional): ").strip() or None
                    team = input("Team (optional): ").strip() or None
                    if emp_id:
                        upsert_employee(emp_id, name=name, team=team)
                        self.employee_id = emp_id
                else:
                    try:
                        num = int(choice)
                        if 1 <= num <= len(emps):
                            self.employee_id = emps[num-1].get('employee_id')
                    except Exception:
                        pass
            except Exception:
                pass
            # ensure employee exists in DB (optional fields can be None)
            upsert_employee(self.employee_id)
            self.activity_detector.start(self.employee_id)
            self.screenshot_handler.start()
            self.presence_detector.start()
            self.is_running = True
            self.thread = threading.Thread(target=self._monitoring_loop, daemon=True)
            self.thread.start()
            logger.info("Employee Monitor started.")
        except Exception as e:
            logger.error(f"Start error: {e}")

    def stop(self):
        """Stop all components and generate report."""
        self.is_running = False
        self.shutdown_event.set()
        self.activity_detector.stop()
        self.screenshot_handler.stop()
        self.presence_detector.stop()
        if self.thread:
            self.thread.join(timeout=5)
        date_str = datetime.now().strftime("%Y-%m-%d")
        generate_daily_report(date_str)
        logger.info("Employee Monitor stopped.")

def signal_handler(sig, frame):
    """Handle Ctrl+C for graceful shutdown."""
    logger.info("Shutdown signal received.")
    monitor.stop()
    sys.exit(0)

if __name__ == "__main__":
    monitor = EmployeeMonitor()
    signal.signal(signal.SIGINT, signal_handler)
    monitor.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        signal_handler(None, None)