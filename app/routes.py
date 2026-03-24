import json
from datetime import datetime, date
from functools import wraps
from flask import render_template, request, redirect, url_for, flash, Response
from flask_login import login_user, logout_user, login_required, current_user
from .models import db, User, Task, TaskUpdate, get_settings, export_all_data, import_all_data
from .utils import (
    hebrew_date_string,
    build_month_calendar,
    send_email_async,
    format_task_summary,
    PRIORITY_META,
    STATUS_META,
    smtp_config_for_user,
)


def admin_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'admin':
            flash('אין הרשאה לעמוד זה.', 'danger')
            return redirect(url_for('dashboard'))
        return func(*args, **kwargs)
    return wrapper


def parse_due_date(raw_value):
    return datetime.strptime(raw_value, '%Y-%m-%d').date() if raw_value else None


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
            'ui_version': 'V5',
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
        query = Task.query
        if current_user.role != 'admin':
            query = query.filter_by(assignee_id=current_user.id)
        elif assignee_filter.isdigit():
            query = query.filter_by(assignee_id=int(assignee_filter))
        if date_filter:
            try:
                query = query.filter_by(due_date=parse_due_date(date_filter))
            except ValueError:
                flash('תאריך הסינון לא תקין.', 'danger')
        tasks = query.order_by(Task.position.asc(), Task.updated_at.desc()).all()
        users = User.query.filter_by(is_active_user=True).order_by(User.full_name.asc()).all()
        employee_users = [u for u in users if u.role == 'employee' and u.is_active_user]
        selected_employee = None
        if assignee_filter.isdigit():
            selected_employee = next((u for u in employee_users if u.id == int(assignee_filter)), None)
        return render_template(
            'dashboard.html',
            tasks=tasks,
            users=users,
            employee_users=employee_users,
            selected_employee=selected_employee,
            date_filter=date_filter,
            assignee_filter=assignee_filter,
        )

    @app.route('/task/create', methods=['POST'])
    @login_required
    def create_task():
        assignee_id = int(request.form.get('assignee_id') or current_user.id)
        if current_user.role != 'admin':
            assignee_id = current_user.id
        max_pos = db.session.query(db.func.max(Task.position)).scalar() or 0
        task = Task(
            title=request.form.get('title', '').strip(),
            description=request.form.get('description', '').strip(),
            priority=request.form.get('priority', 'normal'),
            assignee_id=assignee_id,
            created_by_id=current_user.id,
            position=max_pos + 1,
            due_date=parse_due_date(request.form.get('due_date') or None),
        )
        if not task.title:
            flash('צריך להזין כותרת למשימה.', 'danger')
            return redirect(url_for('dashboard'))
        db.session.add(task)
        db.session.commit()
        flash('המשימה נוספה.', 'success')
        return redirect(url_for('dashboard'))

    @app.route('/task/<int:task_id>/update', methods=['POST'])
    @login_required
    def update_task(task_id):
        task = Task.query.get_or_404(task_id)
        if current_user.role != 'admin' and task.assignee_id != current_user.id:
            flash('אין הרשאה.', 'danger')
            return redirect(url_for('dashboard'))
        task.title = request.form.get('title', task.title).strip()
        task.description = request.form.get('description', task.description).strip()
        task.status = request.form.get('status', task.status)
        task.priority = request.form.get('priority', task.priority)
        task.due_date = parse_due_date(request.form.get('due_date') or None)
        if current_user.role == 'admin':
            task.assignee_id = int(request.form.get('assignee_id') or task.assignee_id)
        db.session.commit()
        flash('המשימה עודכנה.', 'success')
        return redirect(url_for('dashboard'))

    @app.route('/task/<int:task_id>/delete', methods=['POST'])
    @login_required
    def delete_task(task_id):
        task = Task.query.get_or_404(task_id)
        if current_user.role != 'admin' and task.assignee_id != current_user.id:
            flash('אין הרשאה.', 'danger')
            return redirect(url_for('dashboard'))
        db.session.delete(task)
        db.session.commit()
        flash('המשימה נמחקה.', 'success')
        return redirect(url_for('dashboard'))

    @app.route('/task/<int:task_id>/note', methods=['POST'])
    @login_required
    def add_note(task_id):
        task = Task.query.get_or_404(task_id)
        if current_user.role != 'admin' and task.assignee_id != current_user.id:
            flash('אין הרשאה.', 'danger')
            return redirect(url_for('dashboard'))
        content = request.form.get('content', '').strip()
        if not content:
            flash('אין תוכן לעדכון.', 'danger')
            return redirect(url_for('dashboard'))
        stamp = datetime.now().strftime('%d/%m/%Y %H:%M')
        content_with_stamp = f"[{stamp}]\n{content}"
        note = TaskUpdate(task_id=task.id, content=content_with_stamp, author_name=current_user.full_name)
        db.session.add(note)
        task.updated_at = datetime.utcnow()
        db.session.commit()
        flash('העדכון נשמר.', 'success')
        return redirect(url_for('dashboard'))

    @app.route('/task/<int:task_id>/move', methods=['POST'])
    @login_required
    def move_task(task_id):
        task = Task.query.get_or_404(task_id)
        direction = request.form.get('direction')
        if current_user.role != 'admin' and task.assignee_id != current_user.id:
            flash('אין הרשאה.', 'danger')
            return redirect(url_for('dashboard'))
        query = Task.query
        if current_user.role != 'admin':
            query = query.filter_by(assignee_id=current_user.id)
        if direction == 'up':
            other = query.filter(Task.position < task.position).order_by(Task.position.desc()).first()
        else:
            other = query.filter(Task.position > task.position).order_by(Task.position.asc()).first()
        if other:
            task.position, other.position = other.position, task.position
            db.session.commit()
        return redirect(url_for('dashboard'))

    @app.route('/calendar')
    @login_required
    def calendar_view():
        month = int(request.args.get('month', date.today().month))
        year = int(request.args.get('year', date.today().year))
        q = Task.query.filter(Task.due_date.isnot(None))
        if current_user.role != 'admin':
            q = q.filter_by(assignee_id=current_user.id)
        tasks = q.all()
        tasks_by_date = {}
        for task in tasks:
            key = task.due_date.isoformat()
            tasks_by_date.setdefault(key, []).append(task)
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
        settings = get_settings().to_dict()
        data = json.dumps(settings, ensure_ascii=False, indent=2)
        return Response(
            data,
            mimetype='application/json',
            headers={'Content-Disposition': 'attachment; filename=settings_backup.json'}
        )

    @app.route('/settings/import', methods=['POST'])
    @login_required
    @admin_required
    def import_settings():
        file = request.files.get('settings_file')
        if not file:
            flash('לא נבחר קובץ.', 'danger')
            return redirect(url_for('settings_page'))
        try:
            payload = json.load(file)
            settings = get_settings()
            settings.apply_dict(payload)
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
        payload = export_all_data()
        data = json.dumps(payload, ensure_ascii=False, indent=2)
        filename = f"taskhub_full_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        return Response(
            data,
            mimetype='application/json',
            headers={'Content-Disposition': f'attachment; filename={filename}'}
        )

    @app.route('/backup/import-all', methods=['POST'])
    @login_required
    @admin_required
    def import_all_backup():
        file = request.files.get('backup_file')
        if not file:
            flash('לא נבחר קובץ גיבוי מלא.', 'danger')
            return redirect(url_for('settings_page'))
        try:
            payload = json.load(file)
            import_all_data(payload)
            flash('כל הנתונים יובאו בהצלחה: הגדרות, עובדים, משימות והיסטוריית עדכונים.', 'success')
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
            user = User(
                username=username,
                full_name=request.form.get('full_name', '').strip(),
                email=request.form.get('email', '').strip(),
                role=request.form.get('role', 'employee'),
                is_active_user=True,
                smtp_host=request.form.get('smtp_host', '').strip(),
                smtp_port=int(request.form.get('smtp_port') or 587),
                smtp_username=request.form.get('smtp_username', '').strip(),
                smtp_password=request.form.get('smtp_password', '').strip(),
                sender_email=request.form.get('sender_email', '').strip(),
                employer_target_email=request.form.get('employer_target_email', '').strip(),
                theme_color=request.form.get('theme_color', '#1a73e8').strip() or '#1a73e8',
            )
            password = request.form.get('password', '').strip() or '123456'
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            flash('המשתמש נוסף.', 'success')
            return redirect(url_for('users_page'))
        users = User.query.order_by(User.role.desc(), User.full_name.asc()).all()
        return render_template('users.html', users=users)

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
        tasks = Task.query.filter_by(assignee_id=current_user.id).order_by(Task.position.asc()).all()
        body = f"<h2>סיכום סוף משמרת - {current_user.full_name}</h2>" + format_task_summary(tasks)
        config = smtp_config_for_user(current_user, settings)
        ok, msg = send_email_async(config, config['target_email'], f'סיכום משמרת - {current_user.full_name}', body)
        flash(
            'בקשת השליחה יצאה. אם ההגדרות תקינות, הסיכום יישלח תוך זמן קצר.' if ok else f'לא נשלח: {msg}',
            'success' if ok else 'danger'
        )
        return redirect(url_for('dashboard'))

    @app.route('/health')
    def health():
        return {'ok': True, 'version': 'v5'}
