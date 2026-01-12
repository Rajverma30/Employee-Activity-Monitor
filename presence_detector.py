"""
Face detection using OpenCV for presence confirmation.
"""
import cv2
import logging
import time
from config import WEBCAM_ID, PRESENCE_CHECK_INTERVAL
import threading
logger = logging.getLogger(__name__)

class PresenceDetector:
    def __init__(self):
        self.cap = None
        self.face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
        self.is_running = False
        self.thread = None

    def _detect_face(self):
        """Detect face in single frame."""
        try:
            if self.cap is None:
                self.cap = cv2.VideoCapture(WEBCAM_ID)
                if not self.cap.isOpened():
                    logger.warning("Webcam not available.")
                    return 0

            ret, frame = self.cap.read()
            if ret:
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                faces = self.face_cascade.detectMultiScale(gray, 1.1, 4)
                confirmed = 1 if len(faces) > 0 else 0
                logger.debug(f"Presence: {'Confirmed' if confirmed else 'Not detected'}")
                return confirmed
            return 0
        except Exception as e:
            logger.error(f"Face detection error: {e}")
            return 0
        finally:
            if self.cap:
                self.cap.release()
                self.cap = None

    def _presence_loop(self):
        """Thread loop for periodic checks."""
        while self.is_running:
            try:
                time.sleep(PRESENCE_CHECK_INTERVAL)
                if self.is_running:
                    self._detect_face()
            except Exception as e:
                logger.error(f"Presence loop error: {e}")
                time.sleep(60)

    def start(self):
        """Start detection."""
        self.is_running = True
        self.thread = threading.Thread(target=self._presence_loop, daemon=True)
        self.thread.start()
        logger.info("Presence detector started.")

    def stop(self):
        """Stop and release camera."""
        self.is_running = False
        if self.cap:
            self.cap.release()
        logger.info("Presence detector stopped.")

    def check_presence(self):
        """Manual check for immediate use."""
        return self._detect_face()