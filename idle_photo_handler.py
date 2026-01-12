"""
Idle photo capture using OpenCV.
"""
import cv2
import os
from datetime import datetime
import logging
from config import WEBCAM_ID, IDLE_PHOTO_DIR

logger = logging.getLogger(__name__)

def capture_idle_photo():
    """Take a single webcam photo on idle."""
    try:
        cap = cv2.VideoCapture(WEBCAM_ID)
        if not cap.isOpened():
            logger.warning("Webcam not available for idle photo.")
            return None

        ret, frame = cap.read()
        if ret:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"idle_photo_{timestamp}.jpg"
            filepath = os.path.join(IDLE_PHOTO_DIR, filename)
            cv2.imwrite(filepath, frame)
            logger.info(f"Idle webcam photo saved: {filepath}")
            return filepath
        else:
            logger.warning("Failed to read webcam frame.")
            return None
    except Exception as e:
        logger.error(f"Idle photo capture error: {e}")
        return None
    finally:
        cap.release()