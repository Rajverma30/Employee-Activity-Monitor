"""
SQLite database module with multi-employee schema, thread-safe writes, and analytics helpers.
"""
import json
import sqlite3
import threading
import logging
from datetime import datetime, timedelta, date
from typing import Dict, List, Optional, Tuple

from config import load_config

cfg = load_config()
logger = logging.getLogger(__name__)
_DB_LOCK = threading.RLock()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(cfg.db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def init_db() -> None:
    with _DB_LOCK:
        conn = _connect()
        cur = conn.cursor()
        # employees
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS employees (
                employee_id TEXT PRIMARY KEY,
                name TEXT,
                team TEXT,
                config JSON
            )
            """
        )
        # events
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                event_type TEXT NOT NULL,
                active_window TEXT,
                process_name TEXT,
                cpu_percent REAL,
                mem_percent REAL,
                screenshot_path TEXT,
                idle_photo_path TEXT,
                note TEXT,
                FOREIGN KEY(employee_id) REFERENCES employees(employee_id)
            )
            """
        )
        # screenshots
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS screenshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                path TEXT NOT NULL,
                reason TEXT,
                FOREIGN KEY(employee_id) REFERENCES employees(employee_id)
            )
            """
        )
        # movement logs
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS movement_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                event_type TEXT,
                detail TEXT,
                distance_px REAL DEFAULT 0
            )
            """
        )
        # Migration: ensure employee_id column exists on movement_logs
        cur.execute("PRAGMA table_info(movement_logs)")
        ml_cols = [row[1] for row in cur.fetchall()]
        if 'employee_id' not in ml_cols:
            try:
                cur.execute("ALTER TABLE movement_logs ADD COLUMN employee_id TEXT")
            except sqlite3.OperationalError:
                pass

        # indexes
        cur.execute("CREATE INDEX IF NOT EXISTS idx_events_emp_ts ON events(employee_id, timestamp)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_shots_emp_ts ON screenshots(employee_id, timestamp)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_mov_emp_ts ON movement_logs(employee_id, timestamp)")
        conn.commit()
        conn.close()


def upsert_employee(employee_id: str, name: Optional[str] = None, team: Optional[str] = None, config: Optional[Dict] = None) -> None:
    with _DB_LOCK:
        conn = _connect()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO employees(employee_id, name, team, config)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(employee_id) DO UPDATE SET
                name=COALESCE(excluded.name, employees.name),
                team=COALESCE(excluded.team, employees.team),
                config=COALESCE(excluded.config, employees.config)
            """,
            (employee_id, name, team, json.dumps(config or {})),
        )
        conn.commit()
        conn.close()


def log_event(
    employee_id: str,
    event_type: str,
    active_window: Optional[str] = None,
    process_name: Optional[str] = None,
    cpu_percent: Optional[float] = None,
    mem_percent: Optional[float] = None,
    screenshot_path: Optional[str] = None,
    idle_photo_path: Optional[str] = None,
    note: Optional[str] = None,
    timestamp: Optional[str] = None,
) -> int:
    ts = timestamp or datetime.utcnow().isoformat()
    with _DB_LOCK:
        conn = _connect()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO events (
                employee_id, timestamp, event_type, active_window, process_name,
                cpu_percent, mem_percent, screenshot_path, idle_photo_path, note
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                employee_id,
                ts,
                event_type,
                active_window,
                process_name,
                cpu_percent,
                mem_percent,
                screenshot_path,
                idle_photo_path,
                note,
            ),
        )
        event_id = cur.lastrowid
        conn.commit()
        conn.close()
        return event_id


def insert_screenshot(employee_id: str, path: str, reason: str, timestamp: Optional[str] = None) -> int:
    ts = timestamp or datetime.utcnow().isoformat()
    with _DB_LOCK:
        conn = _connect()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO screenshots(employee_id, timestamp, path, reason) VALUES (?, ?, ?, ?)",
            (employee_id, ts, path, reason),
        )
        shot_id = cur.lastrowid
        conn.commit()
        conn.close()
        return shot_id


def log_movement_batch(events: List[Tuple[str, str, str, float]], employee_id: Optional[str] = None) -> None:
    if not events:
        return
    with _DB_LOCK:
        conn = _connect()
        cur = conn.cursor()
        for ts, event_type, detail, distance in events:
            cur.execute(
                "INSERT INTO movement_logs(employee_id, timestamp, event_type, detail, distance_px) VALUES (?, ?, ?, ?, ?)",
                (employee_id, ts, event_type, detail, float(distance)),
            )
        conn.commit()
        conn.close()


# ---------- Analytics helpers ----------

def _day_bounds(day: date) -> Tuple[str, str]:
    start = datetime.combine(day, datetime.min.time()).isoformat()
    end = datetime.combine(day, datetime.max.time()).isoformat()
    return start, end


def get_daily_stats(employee_id: str, day: Optional[date] = None) -> Dict:
    d = day or datetime.utcnow().date()
    start, end = _day_bounds(d)
    with _DB_LOCK:
        conn = _connect()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT timestamp, event_type, active_window
            FROM events
            WHERE employee_id=? AND timestamp BETWEEN ? AND ?
            ORDER BY timestamp
            """,
            (employee_id, start, end),
        )
        rows = cur.fetchall()
        cur.execute(
            "SELECT COUNT(*) FROM screenshots WHERE employee_id=? AND timestamp BETWEEN ? AND ?",
            (employee_id, start, end),
        )
        screenshot_count = cur.fetchone()[0]
        cur.execute(
            """
            SELECT COUNT(*) FROM events
            WHERE employee_id=? AND event_type='idle_photo' AND timestamp BETWEEN ? AND ?
            """,
            (employee_id, start, end),
        )
        idle_photos = cur.fetchone()[0]
        conn.close()

    active_time = 0.0
    idle_time = 0.0
    app_usage: Dict[str, float] = {}
    if not rows:
        return {
            "date": d.isoformat(),
            "active_time_min": 0.0,
            "idle_time_min": 0.0,
            "screenshot_count": screenshot_count,
            "idle_photo_count": idle_photos,
            "top_apps": [],
        }
    prev_ts = datetime.fromisoformat(rows[0][0])
    prev_state = "idle"
    for r in rows:
        ts = datetime.fromisoformat(r[0])
        ev = r[1]
        win = r[2]
        delta_min = (ts - prev_ts).total_seconds() / 60.0
        if prev_state == "active":
            active_time += max(0.0, delta_min)
        else:
            idle_time += max(0.0, delta_min)
        if win:
            app_usage[win] = app_usage.get(win, 0.0) + max(0.0, delta_min)
        prev_ts = ts
        if ev in ("active", "window_change", "non_work_detected"):
            prev_state = "active"
        elif ev in ("idle", "idle_photo"):
            prev_state = "idle"

    top_apps = sorted(app_usage.items(), key=lambda x: x[1], reverse=True)[:5]
    return {
        "date": d.isoformat(),
        "active_time_min": round(active_time, 2),
        "idle_time_min": round(idle_time, 2),
        "screenshot_count": int(screenshot_count),
        "idle_photo_count": int(idle_photos),
        "top_apps": top_apps,
    }


def get_continuous_sessions(employee_id: str, day: Optional[date] = None) -> List[Dict]:
    d = day or datetime.utcnow().date()
    start, end = _day_bounds(d)
    with _DB_LOCK:
        conn = _connect()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT timestamp, event_type
            FROM events
            WHERE employee_id=? AND timestamp BETWEEN ? AND ?
            ORDER BY timestamp
            """,
            (employee_id, start, end),
        )
        rows = cur.fetchall()
        conn.close()

    sessions: List[Dict] = []
    current_start: Optional[datetime] = None
    for r in rows:
        ts = datetime.fromisoformat(r[0])
        ev = r[1]
        if ev in ("active", "window_change") and current_start is None:
            current_start = ts
        if ev in ("idle", "idle_photo", "non_work_detected") and current_start is not None:
            duration = (ts - current_start).total_seconds()
            sessions.append({"start": current_start.isoformat(), "end": ts.isoformat(), "duration_seconds": int(duration)})
            current_start = None
    return sessions


def get_top_apps(employee_id: str, day: Optional[date] = None, limit: int = 5) -> List[Tuple[str, float]]:
    stats = get_daily_stats(employee_id, day)
    return stats.get("top_apps", [])[:limit]


def rank_employees(start_date: date, end_date: date) -> List[Tuple[str, float]]:
    with _DB_LOCK:
        conn = _connect()
        cur = conn.cursor()
        cur.execute("SELECT employee_id FROM employees")
        employees = [row[0] for row in cur.fetchall()]
        conn.close()
    totals: Dict[str, float] = {emp: 0.0 for emp in employees}
    day = start_date
    while day <= end_date:
        for emp in employees:
            d = get_daily_stats(emp, day)
            totals[emp] += float(d.get("active_time_min", 0.0))
        day += timedelta(days=1)
    return sorted(totals.items(), key=lambda x: x[1], reverse=True)


def get_all_employees_status() -> List[Dict]:
    with _DB_LOCK:
        conn = _connect()
        cur = conn.cursor()
        cur.execute("SELECT employee_id FROM employees")
        employees = [row[0] for row in cur.fetchall()]
        out: List[Dict] = []
        for emp in employees:
            cur.execute(
                """
                SELECT event_type, timestamp, active_window, screenshot_path
                FROM events WHERE employee_id=? ORDER BY timestamp DESC LIMIT 1
                """,
                (emp,),
            )
            last = cur.fetchone()
            status = {
                "employee_id": emp,
                "status": "unknown",
                "last_activity_timestamp": None,
                "current_app": None,
                "last_screenshot_time": None,
            }
            if last:
                status["status"] = "active" if last[0] in ("active", "window_change", "non_work_detected") else "idle"
                status["last_activity_timestamp"] = last[1]
                status["current_app"] = last[2]
                if last[3]:
                    status["last_screenshot_time"] = last[1]
            out.append(status)
        conn.close()
        return out


# ---- Backward-compatible helpers for existing Flask dashboard ----

def get_movement_stats(date_str: Optional[str] = None) -> Dict:
    d = date.fromisoformat(date_str) if date_str else datetime.utcnow().date()
    start = datetime.combine(d, datetime.min.time()).isoformat()
    end = datetime.combine(d, datetime.max.time()).isoformat()
    with _DB_LOCK:
        conn = _connect()
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM movement_logs WHERE timestamp BETWEEN ? AND ? AND event_type='key_press'",
            (start, end),
        )
        keys_pressed = int(cur.fetchone()[0] or 0)
        cur.execute(
            "SELECT SUM(distance_px) FROM movement_logs WHERE timestamp BETWEEN ? AND ? AND event_type='mouse_move'",
            (start, end),
        )
        total_distance = float(cur.fetchone()[0] or 0.0)
        cur.execute(
            "SELECT COUNT(*) FROM movement_logs WHERE timestamp BETWEEN ? AND ? AND event_type='mouse_click'",
            (start, end),
        )
        clicks = int(cur.fetchone()[0] or 0)
        cur.execute(
            """
            SELECT event_type, COUNT(*) FROM movement_logs
            WHERE timestamp BETWEEN ? AND ?
            GROUP BY event_type
            """,
            (start, end),
        )
        activity_split = {row[0]: row[1] for row in cur.fetchall()}
        conn.close()
        return {
            'keys_pressed': keys_pressed,
            'total_distance_px': round(total_distance, 2),
            'clicks': clicks,
        'activity_split': activity_split,
    }


def get_activity_data(days: int = 7) -> Dict:
    end_day = datetime.utcnow().date()
    labels: List[str] = [(end_day - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]
    data: List[int] = []
    with _DB_LOCK:
        conn = _connect()
        cur = conn.cursor()
        for ds in labels:
            start = datetime.combine(date.fromisoformat(ds), datetime.min.time()).isoformat()
            end = datetime.combine(date.fromisoformat(ds), datetime.max.time()).isoformat()
            cur.execute(
                "SELECT COUNT(*) FROM events WHERE event_type='active' AND timestamp BETWEEN ? AND ?",
                (start, end),
            )
            data.append(int(cur.fetchone()[0] or 0))
        conn.close()
    return {'labels': labels, 'data': data}


def get_today_ratio() -> List[int]:
    d = datetime.utcnow().date()
    start = datetime.combine(d, datetime.min.time()).isoformat()
    end = datetime.combine(d, datetime.max.time()).isoformat()
    with _DB_LOCK:
        conn = _connect()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM events WHERE event_type='active' AND timestamp BETWEEN ? AND ?", (start, end))
        active = int(cur.fetchone()[0] or 0)
        cur.execute("SELECT COUNT(*) FROM events WHERE timestamp BETWEEN ? AND ?", (start, end))
        total = int(cur.fetchone()[0] or 0)
        conn.close()
    idle = max(0, total - active)
    return [active, idle]


def get_recent_logs(limit: int = 5) -> List[Dict]:
    with _DB_LOCK:
        conn = _connect()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT timestamp, active_window, event_type, process_name
            FROM events ORDER BY timestamp DESC LIMIT ?
            """,
            (limit,),
        )
        rows = cur.fetchall()
        conn.close()
    out: List[Dict] = []
    for ts, window, ev_type, process in rows:
        out.append({
            'timestamp': datetime.fromisoformat(ts).strftime('%Y-%m-%d %H:%M:%S'),
            'window': window or process or 'Unknown',
            'status': ev_type,
            'presence': 'N/A',
        })
    return out


def get_dashboard_stats() -> Dict:
    # Aggregate quick stats for legacy dashboard
    today = datetime.utcnow().date()
    start = datetime.combine(today, datetime.min.time()).isoformat()
    end = datetime.combine(today, datetime.max.time()).isoformat()
    with _DB_LOCK:
        conn = _connect()
        cur = conn.cursor()
        # totals
        cur.execute("SELECT COUNT(*) FROM employees")
        total_employees = int(cur.fetchone()[0] or 0)
        # current status per employee
        statuses = get_all_employees_status()
        active_now = sum(1 for s in statuses if s.get('status') == 'active')
        idle_now = sum(1 for s in statuses if s.get('status') == 'idle')
        # today screenshots
        cur.execute("SELECT COUNT(*) FROM screenshots WHERE timestamp BETWEEN ? AND ?", (start, end))
        shots_today = int(cur.fetchone()[0] or 0)
        conn.close()
    movement = get_movement_stats(today.isoformat())
    return {
        'total_logs': shots_today,  # legacy field repurposed
        'recent_active': active_now,
        'active_time_min': 0,
        'idle_time_min': 0,
        'notifications': [],
        'top_apps': [],
        'screenshot_count': shots_today,
        'idle_photo_count': 0,
        'keys_pressed': movement['keys_pressed'],
        'mouse_distance': movement['total_distance_px'],
        'clicks': movement['clicks'],
        'movement_split': movement['activity_split'],
        'active_employees': active_now,
        'idle_employees': idle_now,
        'total_employees': total_employees,
    }


# ---------- High-level summaries for API/dashboard ----------

def _today_bounds() -> Tuple[str, str]:
    d = datetime.utcnow().date()
    return (
        datetime.combine(d, datetime.min.time()).isoformat(),
        datetime.combine(d, datetime.max.time()).isoformat(),
    )


def _week_bounds() -> Tuple[str, str]:
    today = datetime.utcnow().date()
    start_day = today - timedelta(days=today.weekday())  # Monday start
    start = datetime.combine(start_day, datetime.min.time()).isoformat()
    end = datetime.combine(today, datetime.max.time()).isoformat()
    return start, end


def _sum_active_idle(employee_id: str, start_iso: str, end_iso: str) -> Tuple[float, float]:
    with _DB_LOCK:
        conn = _connect()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT timestamp, event_type, active_window
            FROM events
            WHERE employee_id=? AND timestamp BETWEEN ? AND ?
            ORDER BY timestamp
            """,
            (employee_id, start_iso, end_iso),
        )
        rows = cur.fetchall()
        conn.close()
    if not rows:
        return 0.0, 0.0
    prev_ts = datetime.fromisoformat(rows[0][0])
    prev_state = 'idle'
    active_min = 0.0
    idle_min = 0.0
    for r in rows:
        ts = datetime.fromisoformat(r[0])
        ev = r[1]
        delta = (ts - prev_ts).total_seconds() / 60.0
        if prev_state == 'active':
            active_min += max(0.0, delta)
        else:
            idle_min += max(0.0, delta)
        prev_ts = ts
        if ev in ('active', 'window_change', 'non_work_detected'):
            prev_state = 'active'
        elif ev in ('idle', 'idle_photo'):
            prev_state = 'idle'
    # Close tail to end of range
    try:
        end_dt = datetime.fromisoformat(end_iso)
    except Exception:
        end_dt = datetime.utcnow()
    if end_dt > prev_ts:
        delta = (end_dt - prev_ts).total_seconds() / 60.0
        if prev_state == 'active':
            active_min += max(0.0, delta)
        else:
            idle_min += max(0.0, delta)
    return round(active_min, 2), round(idle_min, 2)


def get_employee_summary(employee_id: str) -> Dict:
    today_start, today_end = _today_bounds()
    week_start, week_end = _week_bounds()
    # status
    with _DB_LOCK:
        conn = _connect()
        cur = conn.cursor()
        cur.execute(
            "SELECT event_type, timestamp, active_window, screenshot_path FROM events WHERE employee_id=? ORDER BY timestamp DESC LIMIT 1",
            (employee_id,),
        )
        last = cur.fetchone()
        cur.execute("SELECT name, team FROM employees WHERE employee_id=?", (employee_id,))
        emp = cur.fetchone()
        cur.execute(
            "SELECT timestamp FROM screenshots WHERE employee_id=? AND timestamp BETWEEN ? AND ? ORDER BY timestamp DESC LIMIT 1",
            (employee_id, today_start, today_end),
        )
        last_shot = cur.fetchone()
        conn.close()
    active_today, idle_today = _sum_active_idle(employee_id, today_start, today_end)
    active_week, idle_week = _sum_active_idle(employee_id, week_start, week_end)
    return {
        'employee_id': employee_id,
        'name': emp[0] if emp else None,
        'team': emp[1] if emp else None,
        'status': ('active' if last and last[0] in ('active', 'window_change', 'non_work_detected') else 'idle'),
        'last_activity': last[1] if last else None,
        'current_app': last[2] if last else None,
        'last_screenshot_time': last_shot[0] if last_shot else None,
        'active_time_today_min': active_today,
        'idle_time_today_min': idle_today,
        'active_time_week_min': active_week,
        'idle_time_week_min': idle_week,
    }


def get_employee_full_activity(employee_id: str, start_iso: str, end_iso: str) -> Dict:
    with _DB_LOCK:
        conn = _connect()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, timestamp, event_type, active_window, process_name, screenshot_path, idle_photo_path, note
            FROM events
            WHERE employee_id=? AND timestamp BETWEEN ? AND ?
            ORDER BY timestamp
            """,
            (employee_id, start_iso, end_iso),
        )
        events = [dict(row) for row in cur.fetchall()]
        cur.execute(
            "SELECT id, timestamp, path, reason FROM screenshots WHERE employee_id=? AND timestamp BETWEEN ? AND ? ORDER BY timestamp",
            (employee_id, start_iso, end_iso),
        )
        shots = [dict(row) for row in cur.fetchall()]
        cur.execute(
            "SELECT id, timestamp, idle_photo_path FROM events WHERE employee_id=? AND idle_photo_path IS NOT NULL AND timestamp BETWEEN ? AND ? ORDER BY timestamp",
            (employee_id, start_iso, end_iso),
        )
        idle_photos = [
            {"id": row[0], "timestamp": row[1], "path": row[2]}
            for row in cur.fetchall()
            if row[2]
        ]
        conn.close()
    active_min, idle_min = _sum_active_idle(employee_id, start_iso, end_iso)
    return {
        'events': events,
        'screenshots': shots,
        'idle_photos': idle_photos,
        'active_minutes': active_min,
        'idle_minutes': idle_min,
    }


def clear_employee_timeline(employee_id: str, day: Optional[date] = None, include_media: bool = False) -> None:
    """Delete events and movement logs for the specified employee and day (defaults to today).
    If include_media=True, also delete screenshot rows for that day.
    """
    d = day or datetime.utcnow().date()
    start, end = _day_bounds(d)
    with _DB_LOCK:
        conn = _connect()
        cur = conn.cursor()
        cur.execute("DELETE FROM events WHERE employee_id=? AND timestamp BETWEEN ? AND ?", (employee_id, start, end))
        cur.execute("DELETE FROM movement_logs WHERE employee_id=? AND timestamp BETWEEN ? AND ?", (employee_id, start, end))
        if include_media:
            cur.execute("DELETE FROM screenshots WHERE employee_id=? AND timestamp BETWEEN ? AND ?", (employee_id, start, end))
        conn.commit()
        conn.close()


def clear_today_all(include_media: bool = False) -> None:
    """Clear today's activity for all employees. Optionally includes screenshots."""
    with _DB_LOCK:
        conn = _connect()
        cur = conn.cursor()
        cur.execute("SELECT employee_id FROM employees")
        ids = [row[0] for row in cur.fetchall()]
        conn.close()
    d = datetime.utcnow().date()
    for eid in ids:
        clear_employee_timeline(eid, d, include_media=include_media)


def get_apps_usage(employee_id: str, day: Optional[date] = None) -> List[Dict]:
    """Aggregate total minutes spent per application (by process_name/title) for a day.

    Returns a list of dicts: { 'app': str, 'minutes': float }
    """
    d = day or datetime.utcnow().date()
    start, end = _day_bounds(d)
    with _DB_LOCK:
        conn = _connect()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT timestamp, event_type, COALESCE(process_name, active_window) AS app
            FROM events
            WHERE employee_id=? AND timestamp BETWEEN ? AND ?
            ORDER BY timestamp
            """,
            (employee_id, start, end),
        )
        rows = cur.fetchall()
        conn.close()

    if not rows:
        return []

    def _normalize(name: Optional[str]) -> str:
        if not name:
            return 'unknown'
        n = name.strip()
        # cut common window title separators
        for sep in [' - ', ' | ', ' — ', ' :: ']:
            if sep in n:
                n = n.split(sep)[-1] if len(n.split(sep)[-1]) < 8 else n.split(sep)[0]
        n = n.replace('\u200b', '').strip()
        n = n.lower()
        # strip .exe and paths
        if n.endswith('.exe'):
            n = n[:-4]
        n = n.split('\\')[-1].split('/')[-1]
        # map known aliases
        aliases = {
            'chrome': 'chrome', 'msedge': 'edge', 'microsoft edge': 'edge',
            'brave': 'brave', 'firefox': 'firefox', 'opera': 'opera',
            'code': 'vscode', 'visual studio code': 'vscode',
            'winword': 'word', 'word': 'word',
            'excel': 'excel', 'powerpnt': 'powerpoint', 'powerpoint': 'powerpoint',
            'outlook': 'outlook', 'teams': 'teams', 'slack': 'slack', 'discord': 'discord',
            'explorer': 'explorer', 'notepad': 'notepad', 'notepad++': 'notepad++',
            'pycharm': 'pycharm', 'idea64': 'intellij', 'studio64': 'android-studio',
            'zoom': 'zoom', 'whatsapp': 'whatsapp', 'telegram': 'telegram',
        }
        for k, v in aliases.items():
            if k in n:
                return v
        # fallback to first token
        base = n.split()[0]
        return base

    def _display_from(raw: Optional[str]) -> str:
        if not raw:
            return 'Unknown'
        txt = raw.strip()
        # Prefer app-like chunk before separators
        for sep in [' - ', ' | ', ' — ', ' :: ']:
            if sep in txt:
                parts = txt.split(sep)
                # take the part that looks most like an app name (shorter, no slashes)
                parts = [p for p in parts if ('/' not in p and '\\' not in p)] or [txt]
                txt = min(parts, key=len)
                break
        return txt[:80]

    minutes_by_key: Dict[str, float] = {}
    name_by_key: Dict[str, str] = {}
    prev_ts = datetime.fromisoformat(rows[0][0])
    prev_raw = rows[0][2] or 'Unknown'
    prev_key = _normalize(prev_raw)
    prev_state = 'idle'
    for ts_str, ev_type, raw in rows:
        ts = datetime.fromisoformat(ts_str)
        delta_min = (ts - prev_ts).total_seconds() / 60.0
        if prev_state == 'active':
            minutes_by_key[prev_key] = minutes_by_key.get(prev_key, 0.0) + max(0.0, delta_min)
            if prev_key not in name_by_key:
                name_by_key[prev_key] = _display_from(prev_raw)
        prev_ts = ts
        if ev_type in ('active', 'window_change', 'non_work_detected'):
            prev_state = 'active'
            prev_raw = raw or prev_raw
            prev_key = _normalize(prev_raw)
        elif ev_type in ('idle', 'idle_photo'):
            prev_state = 'idle'

    # Close the final active segment up to now or end of day
    try:
        day_end = datetime.fromisoformat(end)
    except Exception:
        day_end = datetime.utcnow()
    tail_end = min(datetime.utcnow(), day_end)
    if prev_state == 'active' and tail_end > prev_ts:
        delta_min = (tail_end - prev_ts).total_seconds() / 60.0
        minutes_by_key[prev_key] = minutes_by_key.get(prev_key, 0.0) + max(0.0, delta_min)
        if prev_key not in name_by_key:
            name_by_key[prev_key] = _display_from(prev_raw)

    out = [
        { 'app': name_by_key.get(k, k.title()), 'key': k, 'minutes': round(v, 2) }
        for k, v in sorted(minutes_by_key.items(), key=lambda x: x[1], reverse=True)
        if v > 0
    ]
    return out


def update_employee_idle_threshold(employee_id: str, seconds: int) -> None:
    with _DB_LOCK:
        conn = _connect()
        cur = conn.cursor()
        cur.execute("SELECT config FROM employees WHERE employee_id=?", (employee_id,))
        row = cur.fetchone()
        cfg_json = json.loads(row[0]) if row and row[0] else {}
        cfg_json['idle_threshold_seconds'] = int(seconds)
        cur.execute("UPDATE employees SET config=? WHERE employee_id=?", (json.dumps(cfg_json), employee_id))
        conn.commit()
        conn.close()


def get_all_employee_summaries() -> List[Dict]:
    with _DB_LOCK:
        conn = _connect()
        cur = conn.cursor()
        cur.execute("SELECT employee_id FROM employees")
        ids = [row[0] for row in cur.fetchall()]
        conn.close()
    return [get_employee_summary(eid) for eid in ids]


def list_employees() -> List[Dict]:
    """Return basic list of employees with id, name, team."""
    with _DB_LOCK:
        conn = _connect()
        cur = conn.cursor()
        cur.execute("SELECT employee_id, name, team FROM employees ORDER BY employee_id")
        rows = cur.fetchall()
        conn.close()
    return [{'employee_id': r[0], 'name': r[1], 'team': r[2]} for r in rows]


def delete_employee(employee_id: str) -> None:
    """Delete employee and all related records (events, screenshots, movement)."""
    if not employee_id:
        return
    with _DB_LOCK:
        conn = _connect()
        cur = conn.cursor()
        # delete related rows first due to FK
        cur.execute("DELETE FROM events WHERE employee_id=?", (employee_id,))
        cur.execute("DELETE FROM screenshots WHERE employee_id=?", (employee_id,))
        cur.execute("DELETE FROM movement_logs WHERE employee_id=?", (employee_id,))
        cur.execute("DELETE FROM employees WHERE employee_id=?", (employee_id,))
        conn.commit()
        conn.close()


def get_global_summary() -> Dict:
    today_start, today_end = _today_bounds()
    with _DB_LOCK:
        conn = _connect()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM employees")
        total = int(cur.fetchone()[0] or 0)
        statuses = get_all_employees_status()
        active_now = sum(1 for s in statuses if s.get('status') == 'active')
        idle_now = sum(1 for s in statuses if s.get('status') == 'idle')
        cur.execute("SELECT COUNT(*) FROM screenshots WHERE timestamp BETWEEN ? AND ?", (today_start, today_end))
        shots_today = int(cur.fetchone()[0] or 0)
        conn.close()
    # Aggregate total active/idle across employees today
    totals_active = 0.0
    totals_idle = 0.0
    for s in get_all_employee_summaries():
        totals_active += s['active_time_today_min']
        totals_idle += s['idle_time_today_min']
    return {
        'active_now': active_now,
        'idle_now': idle_now,
        'total_employees': total,
        'screenshots_today': shots_today,
        'total_active_today_min': round(totals_active, 2),
        'total_idle_today_min': round(totals_idle, 2),
    }


def get_company_apps_usage(day: Optional[date] = None) -> List[Dict]:
    """Aggregate per-app usage across all employees for the given day."""
    d = day or datetime.utcnow().date()
    with _DB_LOCK:
        conn = _connect()
        cur = conn.cursor()
        cur.execute("SELECT employee_id FROM employees")
        employee_ids = [row[0] for row in cur.fetchall()]
        conn.close()

    totals: Dict[str, float] = {}
    names: Dict[str, str] = {}
    for eid in employee_ids:
        try:
            apps = get_apps_usage(eid, d)
            for item in apps:
                key = item.get('key') or (item.get('app') or '').lower()
                minutes = float(item.get('minutes') or 0)
                totals[key] = totals.get(key, 0.0) + minutes
                if key not in names:
                    names[key] = item.get('app') or key.title()
        except Exception:
            continue
    out = [
        { 'key': k, 'app': names.get(k, k.title()), 'minutes': round(v, 2) }
        for k, v in sorted(totals.items(), key=lambda x: x[1], reverse=True)
        if v > 0
    ]
    return out
