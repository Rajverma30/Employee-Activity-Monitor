"""
Config loader for Employee Monitoring system.
Loads defaults, then overrides from config.yml if present.
"""
import os
import json
from dataclasses import dataclass, field
from typing import List, Optional, Dict
from pathlib import Path

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - optional at runtime
    yaml = None


@dataclass
class AppConfig:
    employee_id: str = "UNKNOWN"
    db_path: str = "./employee_monitor.db"
    log_file: str = "./monitor.log"
    screenshot_dir: str = "./screenshots"
    idle_photo_dir: str = "./idle_photos"
    reports_dir: str = "./reports"
    idle_threshold_seconds: int = 30
    screenshot_interval_seconds: int = 30
    screenshot_jitter_seconds: int = 60
    activity_poll_seconds: int = 5
    window_poll_seconds: int = 5
    webcam_index: int = 0
    encryption_enabled: bool = False
    encryption_key_path: Optional[str] = None
    non_work_patterns: List[str] = field(default_factory=lambda: [
        # domains
        r"facebook\.com", r"instagram\.com", r"twitter\.com|x\.com",
        r"youtube\.com", r"netflix\.com", r"tiktok\.com", r"primevideo\.com",
        r"hotstar\.com", r"spotify\.com", r"gaana\.com", r"wynk\.in",
        # generic keywords to catch titles without URLs
        r"\byoutube\b", r"\bfacebook\b", r"\binstagram\b", r"\btwitter\b", r"\bx\b",
        r"\bnetflix\b", r"\btiktok\b", r"\bprime video\b", r"\bhotstar\b",
        r"\bspotify\b", r"\bsoundcloud\b",
    ])
    work_whitelist: List[str] = field(default_factory=list)
    process_blacklist: List[str] = field(default_factory=lambda: ["vlc", "steam", "epicgames"])
    api_token: Optional[str] = None
    api_host: str = "127.0.0.1"
    api_port: int = 8081
    admin_enabled: bool = True
    cpu_limit_percent: int = 50
    memory_limit_mb: int = 512
    face_blur: bool = True

    def ensure_dirs(self) -> None:
        os.makedirs(self.screenshot_dir, exist_ok=True)
        os.makedirs(self.idle_photo_dir, exist_ok=True)
        os.makedirs(self.reports_dir, exist_ok=True)


def _load_yaml_config(path: str) -> Dict:
    if not os.path.exists(path) or yaml is None:
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data


def load_config() -> AppConfig:
    cfg = AppConfig()
    # Load from CONFIG_PATH env or default ./config.yml
    config_path = os.environ.get("CONFIG_PATH", os.path.join(os.getcwd(), "config.yml"))
    overrides = _load_yaml_config(config_path)
    for key, value in overrides.items():
        if hasattr(cfg, key):
            setattr(cfg, key, value)
    cfg.ensure_dirs()
    return cfg


# Backwards compatible constants used by some legacy modules in this workspace
_LEGACY = load_config()
SCREENSHOT_DIR = _LEGACY.screenshot_dir
REPORT_DIR = _LEGACY.reports_dir
IDLE_PHOTO_DIR = _LEGACY.idle_photo_dir
DB_PATH = _LEGACY.db_path
LOG_FILE = _LEGACY.log_file

# Intervals for legacy modules
SCREENSHOT_MIN_INTERVAL = max(30, _LEGACY.screenshot_interval_seconds - _LEGACY.screenshot_jitter_seconds)
SCREENSHOT_MAX_INTERVAL = _LEGACY.screenshot_interval_seconds + _LEGACY.screenshot_jitter_seconds
ACTIVITY_CHECK_INTERVAL = max(1, _LEGACY.activity_poll_seconds)
PRESENCE_CHECK_INTERVAL = 60
IDLE_THRESHOLD = _LEGACY.idle_threshold_seconds
IDLE_WEBCAM_THRESHOLD = _LEGACY.idle_threshold_seconds

# Webcam
WEBCAM_ID = _LEGACY.webcam_index

# Movement Monitoring
MOVEMENT_BATCH_INTERVAL = 10
LOG_SENSITIVE_KEYS = False

# ---------------- Runtime settings (modifiable at runtime by dashboard) ----------------

_RUNTIME_SETTINGS_PATH = Path(os.environ.get("RUNTIME_SETTINGS_PATH", os.path.join(os.getcwd(), "runtime_settings.json")))

def load_runtime_settings() -> Dict:
    try:
        if _RUNTIME_SETTINGS_PATH.exists():
            with open(_RUNTIME_SETTINGS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
        else:
            data = {}
    except Exception:
        data = {}
    # Provide defaults from legacy config if not set
    if "screenshot_interval_seconds" not in data:
        data["screenshot_interval_seconds"] = int(_LEGACY.screenshot_interval_seconds)
    if "screenshot_jitter_seconds" not in data:
        data["screenshot_jitter_seconds"] = int(_LEGACY.screenshot_jitter_seconds)
    return data

def save_runtime_settings(settings: Dict) -> None:
    try:
        # Merge with current to avoid dropping unrelated keys
        current = load_runtime_settings()
        current.update(settings or {})
        _RUNTIME_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_RUNTIME_SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(current, f, indent=2)
    except Exception:
        # Swallow errors to avoid breaking caller paths
        pass