"""
Flask web app for Employee Monitoring Dashboard.
"""
from flask import Flask, render_template, session, redirect, url_for, request, jsonify, abort, send_file
import os
from datetime import datetime
from database import (
    get_dashboard_stats, get_recent_logs, get_activity_data, get_today_ratio, get_movement_stats,
    get_all_employee_summaries, get_employee_summary, get_global_summary,
    get_employee_full_activity, update_employee_idle_threshold, get_apps_usage, get_company_apps_usage,
    clear_employee_timeline, upsert_employee, list_employees, delete_employee, clear_today_all
)
from config import REPORT_DIR, load_runtime_settings, save_runtime_settings
from pathlib import Path

app = Flask(__name__)
app.secret_key = 'dev_key_change_in_prod'

def login_required(f):
    def wrap(*args, **kwargs):
        if 'logged_in' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    wrap.__name__ = f.__name__
    return wrap

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if request.form['username'] == 'admin' and request.form['password'] == 'admin':
            session['logged_in'] = True
            session['admin_name'] = 'Admin User'
            return redirect(url_for('dashboard'))
    return '''
    <form method="post">
        Username: <input type="text" name="username"><br>
        Password: <input type="password" name="password"><br>
        <input type="submit" value="Login">
    </form>
    <p>Demo: admin / admin</p>
    '''

@app.route('/')
@app.route('/dashboard')
@login_required
def dashboard():
    stats = get_dashboard_stats()
    recent_logs = get_recent_logs()
    activity = get_activity_data()
    ratio = get_today_ratio()
    movement = get_movement_stats()  # Ensure this matches database.py output
    employee_summaries = get_all_employee_summaries()
    company = get_global_summary()
    
    try:
        import psutil
        cpu_percent = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory()
        mem_percent = memory.percent
    except:
        cpu_percent = mem_percent = 0
    
    reports = [f for f in os.listdir(REPORT_DIR) if f.startswith('daily_summary_')]
    reports.sort(reverse=True)
    
    # Prefetch today's events per employee for timeline fallback
    day = datetime.utcnow().date()
    start = datetime.combine(day, datetime.min.time()).isoformat()
    end = datetime.combine(day, datetime.max.time()).isoformat()
    employee_events = {}
    for emp in employee_summaries:
        try:
            detail = get_employee_full_activity(emp.get('employee_id'), start, end)
            employee_events[emp.get('employee_id')] = detail.get('events', [])
        except Exception:
            employee_events[emp.get('employee_id')] = []

    # Company-wide apps usage (today) with server-side dedupe by normalized key
    raw_company_apps = get_company_apps_usage()
    merged = {}
    for item in (raw_company_apps or []):
        key = (item.get('key') or (item.get('app') or '').lower())
        mins = float(item.get('minutes') or 0)
        if key in merged:
            merged[key]['minutes'] = merged[key].get('minutes', 0.0) + mins
        else:
            merged[key] = {
                'key': key,
                'app': item.get('app') or key.title(),
                'minutes': mins,
            }
    company_apps = sorted(merged.values(), key=lambda x: x['minutes'], reverse=True)

    return render_template('dashboard.html',
                          admin_name=session['admin_name'],
                          stats=stats,
                          recent_logs=recent_logs,
                          activity=activity,
                          ratio=ratio,
                          movement=movement,
                          company=company,
                          employee_summaries=employee_summaries,
                          employee_events=employee_events,
                          company_apps=company_apps,
                          cpu_percent=cpu_percent,
                          mem_percent=mem_percent,
                          reports=reports)

@app.route('/reports/<filename>')
@login_required
def view_report(filename):
    filepath = os.path.join(REPORT_DIR, filename)
    if os.path.exists(filepath):
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        return render_template('report.html', content=content, filename=filename)
    return "Report not found", 404

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('login'))
# ------------------- Employees Management -------------------

@app.route('/employees', methods=['GET', 'POST'])
@login_required
def employees_page():
    msg = None
    if request.method == 'POST':
        emp_id = (request.form.get('employee_id') or '').strip()
        name = (request.form.get('name') or '').strip() or None
        team = (request.form.get('team') or '').strip() or None
        if emp_id:
            upsert_employee(emp_id, name=name, team=team)
            msg = f"Employee {emp_id} saved."
        else:
            msg = "Employee ID is required."
    emps = list_employees()
    # simple inline page to avoid creating a new template file
    rows = ''
    for e in emps:
        eid = e['employee_id']
        name = e.get('name') or '-'
        team = e.get('team') or '-'
        rows += f"<tr><td>{eid}</td><td>{name}</td><td>{team}</td><td class='text-end'><button class='btn btn-sm btn-outline-danger' onclick=\"deleteEmployee('{eid}')\">Delete</button></td></tr>"
    return f'''
    <html><head><title>Employees</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
    </head><body class="p-4">
    <a href="{url_for('dashboard')}" class="btn btn-secondary mb-3">← Back to Dashboard</a>
    <h3>Employees</h3>
    {('<div class="alert alert-success">'+msg+'</div>') if msg else ''}
    <div class="row">
      <div class="col-md-6">
        <table class="table table-striped"><thead><tr><th>ID</th><th>Name</th><th>Team</th><th class="text-end">Actions</th></tr></thead><tbody>{rows or '<tr><td colspan="4" class="text-muted">No employees</td></tr>'}</tbody></table>
      </div>
      <div class="col-md-6">
        <h5>Add / Update Employee</h5>
        <form method="post">
          <div class="mb-2"><label class="form-label">Employee ID</label><input name="employee_id" class="form-control" required></div>
          <div class="mb-2"><label class="form-label">Name</label><input name="name" class="form-control"></div>
          <div class="mb-2"><label class="form-label">Team</label><input name="team" class="form-control"></div>
          <button class="btn btn-primary">Save</button>
        </form>
      </div>
    </div>
    <script>
    async function deleteEmployee(eid) {{
        var confirmed = confirm('Delete employee ' + eid + ' and all related data?');
        if (!confirmed) return;
        try {{
            const res = await fetch('/api/employees/delete', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ employee_id: eid }})
            }});
            if (res.ok) {{ location.reload(); }}
            else {{ alert('Delete failed'); }}
        }} catch (e) {{ alert('Delete failed'); }}
    }}
    </script>
    </body></html>
    '''


@app.route('/api/employees/delete', methods=['POST'])
@login_required
def api_employee_delete():
    try:
        eid = request.json.get('employee_id') if request.is_json else None
        if not eid:
            return jsonify({'ok': False, 'error': 'employee_id required'}), 400
        delete_employee(eid)
        return jsonify({'ok': True, 'employee_id': eid})
    except Exception:
        return jsonify({'ok': False}), 500


# ------------------- JSON API -------------------

@app.route('/api/employees')
def api_employees():
    return jsonify(get_all_employee_summaries())


@app.route('/api/employee/<employee_id>/stats')
def api_employee_stats(employee_id):
    data = get_employee_summary(employee_id)
    if not data:
        abort(404)
    return jsonify(data)


@app.route('/api/summary')
def api_summary():
    return jsonify(get_global_summary())


# Placeholder for secure image serving (implement token/auth as needed)
@app.route('/api/screenshot/<int:screenshot_id>')
def api_screenshot(screenshot_id: int):
    # This is a stub; in a full implementation, look up path by id and enforce auth
    abort(501)


@app.route('/employee/<employee_id>')
@login_required
def employee_detail(employee_id):
    # default to today
    d = datetime.utcnow().date()
    start = datetime.combine(d, datetime.min.time()).isoformat()
    end = datetime.combine(d, datetime.max.time()).isoformat()
    summary = get_employee_summary(employee_id)
    detail = get_employee_full_activity(employee_id, start, end)
    return render_template('employee_detail.html', summary=summary, detail=detail)


@app.route('/api/employee/<employee_id>/export.csv')
def api_employee_export(employee_id):
    try:
        d = datetime.utcnow().date()
        start = datetime.combine(d, datetime.min.time()).isoformat()
        end = datetime.combine(d, datetime.max.time()).isoformat()
        detail = get_employee_full_activity(employee_id, start, end)
        # build CSV in-memory
        from io import StringIO, BytesIO
        import csv, zipfile
        si = StringIO()
        writer = csv.writer(si)
        writer.writerow(['id','timestamp','event_type','active_window','process_name','screenshot_path','idle_photo_path','note'])
        for ev in detail.get('events', []):
            writer.writerow([
                ev.get('id'), ev.get('timestamp'), ev.get('event_type'),
                ev.get('active_window'), ev.get('process_name'),
                ev.get('screenshot_path'), ev.get('idle_photo_path'), ev.get('note')
            ])

        # package CSV + images into a zip
        zip_buf = BytesIO()
        with zipfile.ZipFile(zip_buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(f'{employee_id}_today.csv', si.getvalue())
            # add screenshots
            for s in detail.get('screenshots', []):
                path = s.get('path')
                if path and os.path.exists(path):
                    zf.write(path, arcname=f'screenshots/{os.path.basename(path)}')
            # add idle photos
            for p in detail.get('idle_photos', []):
                path = p.get('path')
                if path and os.path.exists(path):
                    zf.write(path, arcname=f'idle_photos/{os.path.basename(path)}')
        zip_buf.seek(0)
        from flask import send_file
        return send_file(zip_buf, as_attachment=True, download_name=f'{employee_id}_today.zip', mimetype='application/zip')
    except Exception as e:
        # Return a small CSV as fallback so user gets a file
        from io import BytesIO
        buf = BytesIO(b'id,timestamp,event_type,active_window,process_name,screenshot_path,idle_photo_path,note\n')
        from flask import send_file
        return send_file(buf, as_attachment=True, download_name=f'{employee_id}_today.csv', mimetype='text/csv')


@app.route('/api/employee/<employee_id>/timeline.csv')
def api_employee_timeline_csv(employee_id):
    try:
        # optional date param
        d = request.args.get('date')
        if d:
            try:
                day = datetime.fromisoformat(d).date()
            except Exception:
                day = datetime.utcnow().date()
        else:
            day = datetime.utcnow().date()
        start = datetime.combine(day, datetime.min.time()).isoformat()
        end = datetime.combine(day, datetime.max.time()).isoformat()
        detail = get_employee_full_activity(employee_id, start, end)
        from io import StringIO
        import csv
        si = StringIO()
        w = csv.writer(si)
        w.writerow(['id','timestamp','event_type','active_window','process_name','note'])
        for ev in detail.get('events', []):
            w.writerow([ev.get('id'), ev.get('timestamp'), ev.get('event_type'), ev.get('active_window'), ev.get('process_name'), ev.get('note')])
        from flask import Response
        return Response(si.getvalue(), mimetype='text/csv', headers={'Content-Disposition': f'attachment; filename="{employee_id}_{day.isoformat()}_timeline.csv"'})
    except Exception:
        from flask import Response
        return Response('id,timestamp,event_type,active_window,process_name,note\n', mimetype='text/csv', headers={'Content-Disposition': f'attachment; filename="{employee_id}_timeline.csv"'})


@app.route('/api/employee/<employee_id>/timeline/clear', methods=['POST'])
def api_employee_timeline_clear(employee_id):
    try:
        d = request.json.get('date') if request.is_json else None
        if d:
            try:
                day = datetime.fromisoformat(d).date()
            except Exception:
                day = datetime.utcnow().date()
        else:
            day = datetime.utcnow().date()
        clear_employee_timeline(employee_id, day)
        return jsonify({'ok': True, 'employee_id': employee_id, 'date': day.isoformat()})
    except Exception:
        return jsonify({'ok': False}), 500


@app.route('/api/admin/clear-today', methods=['POST'])
@login_required
def api_admin_clear_today():
    try:
        include_media = bool((request.json or {}).get('include_media')) if request.is_json else False
        clear_today_all(include_media=include_media)
        return jsonify({'ok': True})
    except Exception:
        return jsonify({'ok': False}), 500


@app.route('/api/employee/<employee_id>/idle-threshold', methods=['POST'])
def api_set_idle_threshold(employee_id):
    try:
        seconds = int(request.json.get('seconds'))
    except Exception:
        abort(400)
    update_employee_idle_threshold(employee_id, seconds)
    return jsonify({'ok': True, 'employee_id': employee_id, 'idle_threshold_seconds': seconds})


# ------------------- Admin: Runtime Settings -------------------

@app.route('/api/admin/runtime-settings', methods=['GET'])
@login_required
def api_get_runtime_settings():
    try:
        data = load_runtime_settings()
        return jsonify({'ok': True, 'settings': data})
    except Exception:
        return jsonify({'ok': False, 'settings': {}}), 500


@app.route('/api/admin/runtime-settings', methods=['POST'])
@login_required
def api_set_runtime_settings():
    try:
        body = request.get_json(force=True, silent=True) or {}
        # Accept only known keys
        out = {}
        if 'screenshot_interval_seconds' in body:
            try:
                val = int(body['screenshot_interval_seconds'])
                # clamp to reasonable range (30s .. 6h)
                val = max(30, min(val, 6 * 3600))
                out['screenshot_interval_seconds'] = val
            except Exception:
                pass
        if 'screenshot_jitter_seconds' in body:
            try:
                val = int(body['screenshot_jitter_seconds'])
                val = max(0, min(val, 3600))
                out['screenshot_jitter_seconds'] = val
            except Exception:
                pass
        if not out:
            return jsonify({'ok': False, 'error': 'no valid keys'}), 400
        save_runtime_settings(out)
        return jsonify({'ok': True, 'settings': load_runtime_settings()})
    except Exception:
        return jsonify({'ok': False}), 500


# ------------------- Media APIs (thumbnails/preview) -------------------

@app.route('/api/employee/<employee_id>/media.json')
def api_employee_media(employee_id):
    try:
        # date param optional; defaults to today
        d = request.args.get('date')
        if d:
            try:
                day = datetime.fromisoformat(d).date()
            except Exception:
                day = datetime.utcnow().date()
        else:
            day = datetime.utcnow().date()
        start = datetime.combine(day, datetime.min.time()).isoformat()
        end = datetime.combine(day, datetime.max.time()).isoformat()
        detail = get_employee_full_activity(employee_id, start, end)
        items = []
        for s in detail.get('screenshots', []):
            p = s.get('path') or ''
            name = Path(p).name
            file_path = Path('screenshots') / name
            if name and file_path.exists():
                items.append({
                    'type': 'screenshot',
                    'timestamp': s.get('timestamp'),
                    'name': name,
                    'url': url_for('serve_media', kind='s', name=name)
                })
        for p in detail.get('idle_photos', []):
            ip = p.get('path') or ''
            name = Path(ip).name
            file_path = Path('idle_photos') / name
            if name and file_path.exists():
                items.append({
                    'type': 'idle_photo',
                    'timestamp': p.get('timestamp'),
                    'name': name,
                    'url': url_for('serve_media', kind='i', name=name)
                })
        # sort by time
        items.sort(key=lambda x: x.get('timestamp') or '', reverse=True)
        return jsonify(items)
    except Exception:
        return jsonify([])


@app.route('/media/<kind>/<name>')
def serve_media(kind, name):
    # kind: 's' for screenshots, 'i' for idle photos
    base = None
    if kind == 's':
        base = Path('screenshots')
    elif kind == 'i':
        base = Path('idle_photos')
    else:
        abort(404)
    path = (base / name).resolve()
    # prevent path traversal; ensure under base dir
    if base.resolve() not in path.parents and base.resolve() != path.parent:
        abort(403)
    if not path.exists():
        abort(404)
    return send_file(str(path))


# ------------------- Events API (full activity timeline) -------------------

@app.route('/api/employee/<employee_id>/events.json')
def api_employee_events(employee_id):
    try:
        # date param optional; defaults to today
        d = request.args.get('date')
        if d:
            try:
                day = datetime.fromisoformat(d).date()
            except Exception:
                day = datetime.utcnow().date()
        else:
            day = datetime.utcnow().date()
        start = datetime.combine(day, datetime.min.time()).isoformat()
        end = datetime.combine(day, datetime.max.time()).isoformat()
        detail = get_employee_full_activity(employee_id, start, end)
        return jsonify(detail.get('events', []))
    except Exception:
        return jsonify([])


@app.route('/api/employee/<employee_id>/apps.json')
def api_employee_apps(employee_id):
    try:
        # optional date param
        d = request.args.get('date')
        if d:
            try:
                day = datetime.fromisoformat(d).date()
            except Exception:
                day = datetime.utcnow().date()
        else:
            day = datetime.utcnow().date()
        apps = get_apps_usage(employee_id, day)
        return jsonify(apps)
    except Exception:
        return jsonify([])


@app.route('/api/employee/events.json')
@app.route('/api/employee/events')
@app.route('/api/employee/events/')
def api_employee_events_qs():
    employee_id = request.args.get('employee_id')
    if not employee_id:
        return jsonify([])
    try:
        d = request.args.get('date')
        if d:
            try:
                day = datetime.fromisoformat(d).date()
            except Exception:
                day = datetime.utcnow().date()
        else:
            day = datetime.utcnow().date()
        start = datetime.combine(day, datetime.min.time()).isoformat()
        end = datetime.combine(day, datetime.max.time()).isoformat()
        detail = get_employee_full_activity(employee_id, start, end)
        return jsonify(detail.get('events', []))
    except Exception:
        return jsonify([])

if __name__ == '__main__': app.run(debug=True, host='0.0.0.0', port=5000)