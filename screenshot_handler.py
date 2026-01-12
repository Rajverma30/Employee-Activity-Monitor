"""
Screenshot capture using mss - now called only on window changes.
"""
import mss
from datetime import datetime
from PIL import Image
import logging
import threading
import os
from config import SCREENSHOT_DIR

logger = logging.getLogger(__name__)

class ScreenshotHandler:
    def __init__(self):
        temp_sct = mss.mss()
        self.monitor = temp_sct.monitors[1]
        self._lock = threading.Lock()

    def capture(self):
        """Capture and save screenshot."""
        try:
            with self._lock:
                sct = mss.mss()
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"screenshot_{timestamp}.png"
                filepath = os.path.join(SCREENSHOT_DIR, filename)

                screenshot = sct.grab(self.monitor)
                img = Image.frombytes("RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")
                img.save(filepath)
                logger.info(f"Screenshot saved on window change: {filepath}")
                return filepath
        except Exception as e:
            logger.error(f"Screenshot capture error: {e}")
            return None

    def start(self):
        logger.info("Screenshot handler ready (on-change mode).")

    def stop(self):
        logger.info("Screenshot handler stopped.")