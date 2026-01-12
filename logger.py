"""
Logging utility with rotating file handler and structured formatting.
"""
import logging
from logging.handlers import RotatingFileHandler
from typing import Optional


def setup_logger(name: str, log_file: str, level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(level)

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=5)
    file_handler.setFormatter(formatter)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    logger.propagate = False
    return logger


def get_child_logger(parent: logging.Logger, child_name: str, level: Optional[int] = None) -> logging.Logger:
    child = parent.getChild(child_name)
    if level is not None:
        child.setLevel(level)
    return child


