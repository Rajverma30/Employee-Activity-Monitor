"""
Daily summary report generation.
"""
import logging
from datetime import datetime, date
from database import get_global_summary, get_company_apps_usage
from database import get_movement_stats
from config import REPORT_DIR
import os

logger = logging.getLogger(__name__)


def generate_daily_report(date_str=None):
    """Generate company-wide TXT report for a day (default: today)."""
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")
    try:
        # Use existing helpers; they default to today. For a provided date, we reuse
        # the same date for movement aggregation and app usage.
        # Global active/idle are aggregated from per-employee summaries (today).
        summary = get_global_summary()
        move = get_movement_stats(date_str)
        # Company-wide top apps for the given day
        # Convert date_str to date if possible, otherwise fallback to today in helper
        try:
            d = datetime.fromisoformat(date_str).date()
        except Exception:
            d = date.today()
        top_apps = get_company_apps_usage(d)[:3]

        filename = f"daily_summary_{date_str}.txt"
        filepath = os.path.join(REPORT_DIR, filename)
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(f"Daily Summary Report - {date_str}\n")
            f.write(f"========================\n\n")
            # active/idle totals across company (in minutes) already rounded
            f.write(f"Active Time: {summary['total_active_today_min']} minutes\n")
            f.write(f"Idle Time: {summary['total_idle_today_min']} minutes\n")
            f.write(f"Total Screenshots: {summary['screenshots_today']}\n")
            f.write(f"Idle Webcam Photos: 0\n")
            f.write(f"Keys Pressed: {move.get('keys_pressed', 0)}\n")
            f.write(f"Mouse Distance: {move.get('total_distance_px', 0)} px\n")
            f.write(f"Clicks: {move.get('clicks', 0)}\n\n")
            f.write("Top 3 Applications:\n")
            for item in top_apps:
                f.write(f"- {item.get('app')}: {round(item.get('minutes', 0), 2)} minutes\n")
        logger.info(f"Report generated: {filepath}")
    except Exception as e:
        logger.error(f"Report generation error: {e}")