import json
from datetime import datetime, date, timezone
from functools import wraps
from zoneinfo import ZoneInfo

from flask import render_template, request, redirect, url_for, flash, Response, session
from flask_login import login_user, logout_user, login_required, current_user
from sqlalchemy import or_

from .models import (
    db, User, Task, TaskUpdate, TaskAssignment, SeniorPermission, ensure_v12_integrity,
    get_settings, export_all_data, import_all_data
)
from .utils import (
    hebrew_date_string,
    build_month_calendar,
    send_email_async,
    format_task_summary,
    PRIORITY_META,
    STATUS_META,
    smtp_config_for_user,
)

ISRAEL_TZ = ZoneInfo("Asia/Jerusalem")


def utc_now():
    return datetime.now(timezone.utc)


def normalize_utc(dt):
    if not dt:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def to_israel_time(dt):
    dt = normalize_utc(dt)
    return dt.astimezone(ISRAEL_TZ) if dt else None


def format_israel_datetime(dt):
    local_dt = to_israel_time(dt)
    return local_dt.strftime('%d/%m/%Y %H:%M') if local_dt else ''


def parse_due_date(raw_value):
    return datetime.strptime(raw_value, '%Y-%m-%d').date() if raw_value else None


def get_shift_started_at():
    raw = session.get('shift_started_at')
    if raw:
        try:
            return normalize_utc(datetime.fromisoformat(raw))
        except ValueError:
            pass
    now = utc_now()
    session['shift_started_at'] = now.isoformat()
    return now


def set_new_shift_start():
    now = utc_now()
    session['shift_started_at'] = now.isoformat()
    return now


def build_redirect_filters():
    return {
        'date': (request.form.get('current_date_filter') or request.args.get('date') or session.get('dashboard_date_filter') or '').strip(),
        'assignee': (request.form.get('current_assignee_filter') or request.args.get('assignee') or session.get('dashboard_assignee_filter') or '').strip(),
        'priority': (request.form.get('current_priority_filter') or request.args.get('priority') or session.get('dashboard_priority_filter') or '').strip(),
        'search': (request.form.get('current_search_filter') or request.args.get('search') or session.get('dashboard_search_filter') or '').strip(),
    }


def redirect_dashboard():
    filters = build_redirect_filters()
    clean = {k: v for k, v in filters.items() if v}
    return redirect(url_for('dashboard', **clean))


def is_admin():
    return current_user.is_authenticated and current_user.role == 'admin'


def is_senior():
    return current_user.is_authenticated and current_user.role == 'senior'


def get_senior_allowed_ids(user=None):
    """Return employee IDs that a senior user is allowed to manage.

    Important: this function must work both for current_user and for regular
    User model instances loaded from the database. A database User object is not
    always the same proxy object as current_user, so we must not rely on
    is_authenticated here.
    """
    user = user or current_user
    if not user or getattr(user, 'role', None) != 'senior':
        return []
    return [
        p.allowed_user_id
        for p in SeniorPermission.query.filter_by(senior_id=user.id).all()
    ]


def get_task_assigned_ids(task):
    assigned_ids = {a.user_id for a in task.assignments if a.user_id}
    if task.assignee_id:
        assigned_ids.add(task.assignee_id)
    return assigned_ids


def can_access_task(task):
    if current_user.role == 'admin':
        return True

    assigned_ids = get_task_assigned_ids(task)

    if current_user.role == 'senior':
        allowed_ids = set(get_senior_allowed_ids())
        return bool(assigned_ids & allowed_ids) or task.created_by_id == current_user.id

    return current_user.id in assigned_ids


def managed_employee_query():
    q = User.query.filter(User.role == 'employee', User.is_active_user == True)
    if is_senior():
        allowed_ids = get_senior_allowed_ids()
        q = q.filter(User.id.in_(allowed_ids or [-1]))
    return q.order_by(User.full_name.asc())


def admin_or_senior_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role not in ('admin', 'senior'):
            flash('אין הרשאה.', 'danger')
            return redirect(url_for('dashboard'))
        return func(*args, **kwargs)
    return wrapper


def admin_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'admin':
            flash('אין הרשאה.', 'danger')
            return redirect(url_for('dashboard'))
        return func(*args, **kwargs)
    return wrapper


def sync_task_assignments(task, user_ids):
    """Replace all assignees for a task and keep legacy assignee_id in sync.

    The app still keeps assignee_id for backwards compatibility, but the real
    source for multi-assignment is TaskAssignment.
    """
    clean_ids = []
    for uid in user_ids or []:
        if str(uid).isdigit():
            clean_ids.append(int(uid))
    clean_ids = list(dict.fromkeys(clean_ids))

    if not clean_ids and task.assignee_id:
        clean_ids = [int(task.assignee_id)]

    if not clean_ids:
        return []

    task.assignee_id = clean_ids[0]
    TaskAssignment.query.filter_by(task_id=task.id).delete()
    db.session.flush()

    for uid in clean_ids:
        db.session.add(TaskAssignment(task_id=task.id, user_id=uid))

    print('TASK ASSIGNMENTS SYNC:', task.id, clean_ids)
    return clean_ids


def task_recipients(task, settings):
    recipients = []
    for user in task.assigned_users:
        if user.email:
            recipients.append(user.email.strip())
        if user.employer_target_email:
            recipients.append(user.employer_target_email.strip())
    if settings.employer_email:
        recipients.append(settings.employer_email.strip())
    clean = []
    seen = set()
    for email in recipients:
        if email and email not in seen:
            clean.append(email)
            seen.add(email)
    return clean


def mail_config_is_complete(config):
    return all([
        config.get('smtp_host'),
        config.get('smtp_port'),
        config.get('smtp_username'),
        config.get('smtp_password'),
        config.get('smtp_sender'),
    ])


def choose_mail_config(actor, task, settings):
    """Pick a working SMTP config.

    Prefer the actor so worker comments can be sent from the worker when configured.
    If the actor is missing SMTP settings, fall back to assigned users and then
    to the global/admin settings.
    """
    candidates = []
    if actor:
        candidates.append(actor)
    candidates.extend([u for u in task.assigned_users if u])
    admin_user = User.query.filter_by(role='admin').first()
    if admin_user:
        candidates.append(admin_user)

    seen = set()
    for user in candidates:
        if not user or user.id in seen:
            continue
        seen.add(user.id)
        config = smtp_config_for_user(user, settings)
        if mail_config_is_complete(config):
            return config

    return smtp_config_for_user(actor or admin_user or (task.assigned_users[0] if task.assigned_users else None), settings)


def build_task_notification_body(task, action_label, update_entry=None):
    assignee_name = task.assigned_names or 'לא משויך'
    creator_name = task.creator.full_name if getattr(task, 'creator', None) else 'לא ידוע'
    due_text = task.due_date.strftime('%d/%m/%Y') if task.due_date else 'ללא תאריך יעד'
    priority = PRIORITY_META.get(task.priority, PRIORITY_META['normal'])
    description_html = (task.description or '').replace(chr(10), '<br>') or 'ללא תיאור'
    latest_update_html = ''
    if update_entry is not None:
        latest_update_html = f"<p><b>תוכן העדכון האחרון:</b><br>{(update_entry.content or '').replace(chr(10), '<br>')}</p>"
    all_updates_html = ''
    if task.updates:
        all_updates_html = '<p><b>כל העדכונים במשימה:</b></p><ul>'
        for upd in task.updates:
            all_updates_html += (
                f"<li><b>{upd.author_name}</b> | {format_israel_datetime(upd.created_at)}<br>"
                f"{(upd.content or '').replace(chr(10), '<br>')}</li>"
            )
        all_updates_html += '</ul>'
    return f"""<div dir='rtl' style='font-family:Arial,sans-serif;line-height:1.8'>
<h2>שלום רב,</h2>
<p>{action_label}</p>
<p><b>כותרת המשימה:</b> {task.title}</p>
<p><b>עובדים משויכים:</b> {assignee_name}</p>
<p><b>נוצרה על ידי:</b> {creator_name}</p>
<p><b>עדיפות:</b> {priority['icon']} {priority['label']}</p>
<p><b>תאריך יעד:</b> {due_text}</p>
<p><b>תיאור:</b><br>{description_html}</p>
{latest_update_html}
{all_updates_html}
<p>תודה רבה</p>
</div>"""


def notify_task_change(task, actor, settings, action='new', update_entry=None):
    try:
        if action == 'new':
            subject = 'נוספה לך משימה חדשה'
            action_label = 'נוספה לך משימה חדשה.'
        elif action == 'note':
            subject = 'נוסף עדכון למשימה'
            action_label = 'נוסף עדכון חדש למשימה.'
        else:
            subject = 'משימה עודכנה'
            action_label = 'עודכנה משימה קיימת.'

        # Make sure relationship data is fresh after create/update sync.
        try:
            db.session.flush()
            db.session.expire(task, ['assignments'])
        except Exception:
            pass

        recipients = task_recipients(task, settings)
        assigned_ids = sorted(list(get_task_assigned_ids(task)))
        print('TASK NOTIFY ASSIGNED IDS:', task.id, assigned_ids)
        print('TASK NOTIFY RECIPIENTS:', recipients)

        if not recipients:
            print('TASK EMAIL SKIPPED: no recipients')
            return

        config = choose_mail_config(actor, task, settings)
        body = build_task_notification_body(task, action_label, update_entry=update_entry)

        for target_email in recipients:
            ok, msg = send_email_async(config, target_email, subject, body)
            print('TASK EMAIL RESULT:', target_email, ok, msg)

    except Exception as exc:
        print('TASK EMAIL ERROR:', str(exc))


def build_shift_updates_html(tasks, shift_started_at):
    sections = []
    shift_started_at = normalize_utc(shift_started_at)
    for task in tasks:
        pieces = []
        task_created_at = normalize_utc(task.created_at)
        if task_created_at and shift_started_at and task_created_at >= shift_started_at:
            pieces.append('<li>המשימה נוצרה במשמרת זו</li>')
        if task.updates:
            note_items = []
            for upd in task.updates:
                upd_dt = normalize_utc(upd.created_at)
                if upd_dt and shift_started_at and upd_dt >= shift_started_at:
                    note_items.append(
                        f"<li><b>{upd.author_name}</b> | {format_israel_datetime(upd.created_at)}<br>{(upd.content or '').replace(chr(10), '<br>')}</li>"
                    )
            if note_items:
                pieces.append('<li><b>עדכונים במשמרת זו:</b><ul>' + ''.join(note_items) + '</ul></li>')
        if pieces:
            sections.append(f"<h3>{task.title}</h3><ul>{''.join(pieces)}</ul>")
    return '<hr>'.join(sections) if sections else '<p>לא נמצאו שינויים במשמרת זו.</p>'


def register_routes(app):
    @app.context_processor
    def inject_globals():
        settings = get_settings()
        return {
            'app_settings': settings,
            'hebrew_date_string': hebrew_date_string,
            'today_date': date.today(),
            'priority_meta': PRIORITY_META,
            'status_meta': STATUS_META,
            'ui_version': 'V14',
            'format_israel_datetime': format_israel_datetime,
        }

    @app.route('/')
    def index():
        if current_user.is_authenticated:
            return redirect(url_for('dashboard'))
        return redirect(url_for('login'))

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if request.method == 'POST':
            username = request.form.get('username', '').strip()
            password = request.form.get('password', '')
            user = User.query.filter_by(username=username).first()
            if user and user.check_password(password) and user.is_active_user:
                login_user(user)
                set_new_shift_start()
                return redirect(url_for('dashboard'))
            flash('שם משתמש או סיסמה שגויים.', 'danger')
        return render_template('login.html')

    @app.route('/logout')
    @login_required
    def logout():
        logout_user()
        return redirect(url_for('login'))

    @app.route('/dashboard')
    @login_required
    def dashboard():
        date_filter = request.args.get('date', '').strip()
        assignee_filter = request.args.get('assignee', '').strip()
        priority_filter = request.args.get('priority', '').strip()
        search_filter = request.args.get('search', '').strip()
        session['dashboard_date_filter'] = date_filter
        session['dashboard_assignee_filter'] = assignee_filter
        session['dashboard_priority_filter'] = priority_filter
        session['dashboard_search_filter'] = search_filter

        query = Task.query
        if current_user.role == 'employee':
            query = query.outerjoin(TaskAssignment).filter(
                or_(Task.assignee_id == current_user.id, TaskAssignment.user_id == current_user.id)
            )
        elif current_user.role == 'senior':
            allowed_ids = get_senior_allowed_ids()
            query = query.outerjoin(TaskAssignment)
            if assignee_filter.isdigit() and int(assignee_filter) in set(allowed_ids):
                uid = int(assignee_filter)
                query = query.filter(or_(Task.assignee_id == uid, TaskAssignment.user_id == uid))
            else:
                query = query.filter(or_(
                    Task.assignee_id.in_(allowed_ids or [-1]),
                    TaskAssignment.user_id.in_(allowed_ids or [-1]),
                    Task.created_by_id == current_user.id
                ))
        elif assignee_filter.isdigit():
            uid = int(assignee_filter)
            query = query.outerjoin(TaskAssignment).filter(or_(Task.assignee_id == uid, TaskAssignment.user_id == uid))

        if date_filter:
            try:
                query = query.filter(Task.due_date == parse_due_date(date_filter))
            except ValueError:
                flash('תאריך הסינון לא תקין.', 'danger')
        if priority_filter:
            query = query.filter(Task.priority == priority_filter)
        if search_filter:
            like_value = f"%{search_filter}%"
            query = query.filter(or_(Task.title.ilike(like_value), Task.description.ilike(like_value)))

        tasks = query.distinct().order_by(Task.position.asc(), Task.updated_at.desc()).all()
        users = User.query.filter_by(is_active_user=True).order_by(User.full_name.asc()).all()
        employee_users = managed_employee_query().all() if current_user.role in ('admin', 'senior') else [current_user]
        selected_employee = None
        if assignee_filter.isdigit():
            selected_employee = User.query.get(int(assignee_filter))
        return render_template('dashboard.html', tasks=tasks, users=users, employee_users=employee_users,
                               selected_employee=selected_employee, date_filter=date_filter,
                               assignee_filter=assignee_filter, priority_filter=priority_filter,
                               search_filter=search_filter)

    @app.route('/task/create', methods=['POST'])
    @login_required
    def create_task():
        selected_ids = request.form.getlist('assignee_ids')
        print('TASK CREATE POSTED ASSIGNEE IDS:', selected_ids, 'BY', current_user.id, current_user.role)
        if current_user.role == 'employee':
            selected_ids = [str(current_user.id)]
        elif current_user.role == 'senior':
            allowed = {str(x) for x in get_senior_allowed_ids()}
            selected_ids = [str(uid) for uid in selected_ids if str(uid) in allowed]
            print('TASK CREATE SENIOR ALLOWED:', sorted(list(allowed)), 'FILTERED:', selected_ids)
        if not selected_ids:
            flash('צריך לבחור לפחות עובד אחד למשימה.', 'danger')
            return redirect_dashboard()
        max_pos = db.session.query(db.func.max(Task.position)).scalar() or 0
        task = Task(title=request.form.get('title', '').strip(),
                    description=request.form.get('description', '').strip(),
                    priority=request.form.get('priority', 'normal'),
                    assignee_id=int(selected_ids[0]), created_by_id=current_user.id,
                    position=max_pos + 1, due_date=parse_due_date(request.form.get('due_date') or None))
        if not task.title:
            flash('צריך להזין כותרת למשימה.', 'danger')
            return redirect_dashboard()
        db.session.add(task)
        db.session.flush()
        sync_task_assignments(task, selected_ids)
        db.session.commit()
        notify_task_change(task, current_user, get_settings(), action='new')
        flash('המשימה נוספה.', 'success')
        return redirect_dashboard()

    @app.route('/task/<int:task_id>/update', methods=['POST'])
    @login_required
    def update_task(task_id):
        task = Task.query.get_or_404(task_id)
        if not can_access_task(task):
            flash('אין הרשאה.', 'danger')
            return redirect_dashboard()
        task.title = request.form.get('title', task.title).strip()
        task.description = request.form.get('description', task.description).strip()
        task.status = request.form.get('status', task.status)
        task.priority = request.form.get('priority', task.priority)
        task.due_date = parse_due_date(request.form.get('due_date') or None)
        if current_user.role in ('admin', 'senior'):
            selected_ids = request.form.getlist('assignee_ids')
            print('TASK UPDATE POSTED ASSIGNEE IDS:', task.id, selected_ids, 'BY', current_user.id, current_user.role)
            if current_user.role == 'senior':
                allowed = {str(x) for x in get_senior_allowed_ids()}
                selected_ids = [str(uid) for uid in selected_ids if str(uid) in allowed]
                print('TASK UPDATE SENIOR ALLOWED:', sorted(list(allowed)), 'FILTERED:', selected_ids)
            if selected_ids:
                sync_task_assignments(task, selected_ids)
        db.session.commit()
        notify_task_change(task, current_user, get_settings(), action='updated')
        flash('המשימה עודכנה.', 'success')
        return redirect_dashboard()

    @app.route('/task/<int:task_id>/delete', methods=['POST'])
    @login_required
    def delete_task(task_id):
        task = Task.query.get_or_404(task_id)
        if not can_access_task(task):
            flash('אין הרשאה.', 'danger')
            return redirect_dashboard()
        db.session.delete(task)
        db.session.commit()
        flash('המשימה נמחקה.', 'success')
        return redirect_dashboard()

    @app.route('/task/<int:task_id>/note', methods=['POST'])
    @login_required
    def add_note(task_id):
        task = Task.query.get_or_404(task_id)
        if not can_access_task(task):
            flash('אין הרשאה.', 'danger')
            return redirect_dashboard()
        content = request.form.get('content', '').strip()
        if not content:
            flash('אין תוכן לעדכון.', 'danger')
            return redirect_dashboard()
        stamp = format_israel_datetime(utc_now())
        note = TaskUpdate(task_id=task.id, content=f"[{stamp}]\n{content}", author_name=current_user.full_name)
        db.session.add(note)
        task.updated_at = utc_now()
        db.session.commit()
        notify_task_change(task, current_user, get_settings(), action='note', update_entry=note)
        flash('העדכון נשמר ונשלח.', 'success')
        return redirect_dashboard()

    @app.route('/task/<int:task_id>/move', methods=['POST'])
    @login_required
    def move_task(task_id):
        task = Task.query.get_or_404(task_id)
        direction = request.form.get('direction')
        if not can_access_task(task):
            flash('אין הרשאה.', 'danger')
            return redirect_dashboard()
        query = Task.query
        if current_user.role == 'employee':
            query = query.outerjoin(TaskAssignment).filter(
                or_(Task.assignee_id == current_user.id, TaskAssignment.user_id == current_user.id)
            )
        elif current_user.role == 'senior':
            allowed = get_senior_allowed_ids()
            query = query.outerjoin(TaskAssignment).filter(
                or_(Task.assignee_id.in_(allowed or [-1]), TaskAssignment.user_id.in_(allowed or [-1]))
            )
        other = query.filter(Task.position < task.position).order_by(Task.position.desc()).first() if direction == 'up' else query.filter(Task.position > task.position).order_by(Task.position.asc()).first()
        if other:
            task.position, other.position = other.position, task.position
            db.session.commit()
        return redirect_dashboard()

    @app.route('/calendar')
    @login_required
    def calendar_view():
        month = int(request.args.get('month', date.today().month))
        year = int(request.args.get('year', date.today().year))
        q = Task.query.filter(Task.due_date.isnot(None))
        if current_user.role == 'employee':
            q = q.outerjoin(TaskAssignment).filter(or_(Task.assignee_id == current_user.id, TaskAssignment.user_id == current_user.id))
        elif current_user.role == 'senior':
            allowed = get_senior_allowed_ids()
            q = q.outerjoin(TaskAssignment).filter(or_(Task.assignee_id.in_(allowed or [-1]), TaskAssignment.user_id.in_(allowed or [-1])))
        tasks = q.distinct().all()
        tasks_by_date = {}
        for task in tasks:
            tasks_by_date.setdefault(task.due_date.isoformat(), []).append(task)
        weeks = build_month_calendar(year, month, tasks_by_date)
        return render_template('calendar.html', weeks=weeks, month=month, year=year)

    @app.route('/settings', methods=['GET', 'POST'])
    @login_required
    @admin_required
    def settings_page():
        settings = get_settings()
        if request.method == 'POST':
            settings.company_name = request.form.get('company_name', settings.company_name)
            settings.employer_email = request.form.get('employer_email', settings.employer_email)
            settings.smtp_host = request.form.get('smtp_host', settings.smtp_host)
            settings.smtp_port = int(request.form.get('smtp_port', settings.smtp_port) or 587)
            settings.smtp_username = request.form.get('smtp_username', settings.smtp_username)
            settings.smtp_password = request.form.get('smtp_password', settings.smtp_password)
            settings.smtp_sender = request.form.get('smtp_sender', settings.smtp_sender)
            settings.primary_color = request.form.get('primary_color', settings.primary_color)
            settings.secondary_color = request.form.get('secondary_color', settings.secondary_color)
            settings.accent_color = request.form.get('accent_color', settings.accent_color)
            settings.card_color = request.form.get('card_color', settings.card_color)
            settings.text_color = request.form.get('text_color', settings.text_color)
            db.session.commit()
            flash('ההגדרות נשמרו.', 'success')
            return redirect(url_for('settings_page'))
        return render_template('settings.html', settings=settings)

    @app.route('/settings/export')
    @login_required
    @admin_required
    def export_settings():
        data = json.dumps(get_settings().to_dict(), ensure_ascii=False, indent=2)
        return Response(data, mimetype='application/json', headers={'Content-Disposition': 'attachment; filename=settings_backup.json'})

    @app.route('/settings/import', methods=['POST'])
    @login_required
    @admin_required
    def import_settings():
        file = request.files.get('settings_file')
        if not file:
            flash('לא נבחר קובץ.', 'danger')
            return redirect(url_for('settings_page'))
        try:
            get_settings().apply_dict(json.load(file))
            db.session.commit()
            flash('ההגדרות יובאו בהצלחה.', 'success')
        except Exception as exc:
            db.session.rollback()
            flash(f'שגיאה בייבוא: {exc}', 'danger')
        return redirect(url_for('settings_page'))

    @app.route('/backup/export-all')
    @login_required
    @admin_required
    def export_all_backup():
        data = json.dumps(export_all_data(), ensure_ascii=False, indent=2)
        filename = f"taskhub_full_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        return Response(data, mimetype='application/json', headers={'Content-Disposition': f'attachment; filename={filename}'})

    @app.route('/backup/import-all', methods=['POST'])
    @login_required
    @admin_required
    def import_all_backup():
        file = request.files.get('backup_file')
        if not file:
            flash('לא נבחר קובץ גיבוי מלא.', 'danger')
            return redirect(url_for('settings_page'))
        try:
            import_all_data(json.load(file))
            flash('כל הנתונים יובאו בהצלחה.', 'success')
        except Exception as exc:
            db.session.rollback()
            flash(f'שגיאה בייבוא הגיבוי המלא: {exc}', 'danger')
        return redirect(url_for('settings_page'))

    @app.route('/users', methods=['GET', 'POST'])
    @login_required
    @admin_required
    def users_page():
        if request.method == 'POST':
            username = request.form.get('username', '').strip()
            if not username:
                flash('צריך להזין שם משתמש.', 'danger')
                return redirect(url_for('users_page'))
            if User.query.filter_by(username=username).first():
                flash('שם המשתמש כבר קיים.', 'danger')
                return redirect(url_for('users_page'))
            user = User(username=username, full_name=request.form.get('full_name', '').strip(),
                        email=request.form.get('email', '').strip(), role=request.form.get('role', 'employee'),
                        is_active_user=True, smtp_host=request.form.get('smtp_host', '').strip(),
                        smtp_port=int(request.form.get('smtp_port') or 587),
                        smtp_username=request.form.get('smtp_username', '').strip(),
                        smtp_password=request.form.get('smtp_password', '').strip(),
                        sender_email=request.form.get('sender_email', '').strip(),
                        employer_target_email=request.form.get('employer_target_email', '').strip(),
                        theme_color=request.form.get('theme_color', '#1a73e8').strip() or '#1a73e8')
            user.set_password(request.form.get('password', '').strip() or '123456')
            db.session.add(user)
            db.session.flush()
            if user.role == 'senior':
                allowed_ids = request.form.getlist('allowed_user_ids')
                print('SENIOR CREATE ALLOWED IDS:', user.id, allowed_ids)
                for allowed_id in allowed_ids:
                    if str(allowed_id).isdigit():
                        db.session.add(SeniorPermission(senior_id=user.id, allowed_user_id=int(allowed_id)))
            db.session.commit()
            flash('המשתמש נוסף.', 'success')
            return redirect(url_for('users_page'))
        users = User.query.order_by(User.role.desc(), User.full_name.asc()).all()
        employee_users = User.query.filter(User.role == 'employee', User.is_active_user == True).order_by(User.full_name.asc()).all()
        return render_template('users.html', users=users, employee_users=employee_users)

    @app.route('/users/<int:user_id>/edit', methods=['POST'])
    @login_required
    @admin_required
    def edit_user(user_id):
        user = User.query.get_or_404(user_id)
        username = request.form.get('username', '').strip()
        existing = User.query.filter_by(username=username).first()
        if existing and existing.id != user.id:
            flash('שם המשתמש כבר קיים.', 'danger')
            return redirect(url_for('users_page'))
        user.username = username
        user.full_name = request.form.get('full_name', '').strip()
        user.email = request.form.get('email', '').strip()
        user.role = request.form.get('role', user.role)
        user.smtp_host = request.form.get('smtp_host', '').strip()
        user.smtp_port = int(request.form.get('smtp_port') or 587)
        user.smtp_username = request.form.get('smtp_username', '').strip()
        user.smtp_password = request.form.get('smtp_password', '').strip()
        user.sender_email = request.form.get('sender_email', '').strip()
        user.employer_target_email = request.form.get('employer_target_email', '').strip()
        user.theme_color = request.form.get('theme_color', '#1a73e8').strip() or '#1a73e8'
        SeniorPermission.query.filter_by(senior_id=user.id).delete()
        if user.role == 'senior':
            allowed_ids = request.form.getlist('allowed_user_ids')
            print('SENIOR EDIT ALLOWED IDS:', user.id, allowed_ids)
            for allowed_id in allowed_ids:
                if str(allowed_id).isdigit():
                    db.session.add(SeniorPermission(senior_id=user.id, allowed_user_id=int(allowed_id)))
        db.session.commit()
        flash('פרטי המשתמש עודכנו.', 'success')
        return redirect(url_for('users_page'))

    @app.route('/users/<int:user_id>/toggle', methods=['POST'])
    @login_required
    @admin_required
    def toggle_user(user_id):
        user = User.query.get_or_404(user_id)
        if user.username == 'admin':
            flash('לא ניתן להשבית את admin.', 'danger')
            return redirect(url_for('users_page'))
        user.is_active_user = not user.is_active_user
        db.session.commit()
        flash('המשתמש עודכן.', 'success')
        return redirect(url_for('users_page'))

    @app.route('/users/<int:user_id>/password', methods=['POST'])
    @login_required
    @admin_required
    def reset_password(user_id):
        user = User.query.get_or_404(user_id)
        new_password = request.form.get('new_password', '').strip()
        if not new_password:
            flash('צריך להזין סיסמה חדשה.', 'danger')
            return redirect(url_for('users_page'))
        user.set_password(new_password)
        db.session.commit()
        flash('הסיסמה עודכנה.', 'success')
        return redirect(url_for('users_page'))

    @app.route('/end-shift', methods=['POST'])
    @login_required
    def end_shift():
        settings = get_settings()
        shift_started_at = get_shift_started_at()
        if current_user.role == 'employee':
            tasks = Task.query.outerjoin(TaskAssignment).filter(or_(Task.assignee_id == current_user.id, TaskAssignment.user_id == current_user.id)).distinct().order_by(Task.position.asc()).all()
        elif current_user.role == 'senior':
            allowed = get_senior_allowed_ids()
            tasks = Task.query.outerjoin(TaskAssignment).filter(or_(Task.assignee_id.in_(allowed or [-1]), TaskAssignment.user_id.in_(allowed or [-1]), Task.created_by_id == current_user.id)).distinct().order_by(Task.position.asc()).all()
        else:
            tasks = Task.query.order_by(Task.position.asc()).all()
        config = smtp_config_for_user(current_user, settings)
        target_email = config.get('target_email') or settings.employer_email
        if not target_email:
            flash('לא מוגדר מייל יעד לקבלת סיכום משמרת.', 'danger')
            return redirect_dashboard()
        body_updates = f"<h2>סיכום שינויים במשמרת - {current_user.full_name}</h2>" + build_shift_updates_html(tasks, shift_started_at)
        body_all = f"<h2>סיכום כללי סוף משמרת - {current_user.full_name}</h2>" + format_task_summary(tasks)
        ok1, msg1 = send_email_async(config, target_email, f'שינויים במשמרת - {current_user.full_name}', body_updates)
        ok2, msg2 = send_email_async(config, target_email, f'סיכום כללי סוף משמרת - {current_user.full_name}', body_all)
        set_new_shift_start()
        flash('נשלחו שני דוחות: שינויים במשמרת ודוח כללי.' if ok1 and ok2 else f'חלק מהשליחה נכשלה: {msg1} | {msg2}', 'success' if ok1 and ok2 else 'danger')
        return redirect_dashboard()

    @app.route('/admin/repair-v12')
    @login_required
    @admin_required
    def repair_v12():
        try:
            stats = ensure_v12_integrity()
            flash(f"תיקון V12/V13 בוצע: שיוכי משימות {stats.get('assignments_total', 0)}, הרשאות בכיר {stats.get('senior_permissions_total', 0)}", 'success')
        except Exception as exc:
            db.session.rollback()
            flash(f'שגיאה בתיקון V12/V13: {exc}', 'danger')
        return redirect(url_for('users_page'))

    @app.route('/admin/v12-status')
    @login_required
    @admin_required
    def v12_status():
        stats = ensure_v12_integrity()
        stats['ok'] = True
        stats['version'] = 'v14'
        return stats

    @app.route('/health')
    def health():
        return {'ok': True, 'version': 'v14', 'assignments': TaskAssignment.query.count(), 'senior_permissions': SeniorPermission.query.count()}
