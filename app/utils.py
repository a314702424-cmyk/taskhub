from datetime import date
from convertdate import hebrew
import calendar
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

HEB_MONTHS = {
    1: 'ניסן', 2: 'אייר', 3: 'סיוון', 4: 'תמוז', 5: 'אב', 6: 'אלול',
    7: 'תשרי', 8: 'חשוון', 9: 'כסלו', 10: 'טבת', 11: 'שבט', 12: 'אדר', 13: 'אדר ב׳'
}

PRIORITY_META = {
    'low': {'label': 'נמוכה', 'icon': '🟢'},
    'normal': {'label': 'רגילה', 'icon': '🟡'},
    'high': {'label': 'גבוהה', 'icon': '🔴'},
}

STATUS_META = {
    'open': 'פתוח',
    'in_progress': 'בתהליך',
    'done': 'הושלם',
}


def hebrew_date_string(gdate: date | None):
    if not gdate:
        return ''
    hy, hm, hd = hebrew.from_gregorian(gdate.year, gdate.month, gdate.day)
    month_name = HEB_MONTHS.get(hm, str(hm))
    return f"{hd} {month_name} {hy}"


def build_month_calendar(year: int, month: int, tasks_by_date: dict):
    cal = calendar.Calendar(firstweekday=6)
    weeks = []
    for week in cal.monthdatescalendar(year, month):
        week_data = []
        for day in week:
            week_data.append({
                'date': day,
                'in_month': day.month == month,
                'hebrew': hebrew_date_string(day),
                'tasks': tasks_by_date.get(day.isoformat(), [])
            })
        weeks.append(week_data)
    return weeks


def smtp_config_for_user(user, settings):
    return {
        'smtp_host': user.smtp_host or settings.smtp_host,
        'smtp_port': int(user.smtp_port or settings.smtp_port or 587),
        'smtp_username': user.smtp_username or settings.smtp_username,
        'smtp_password': user.smtp_password or settings.smtp_password,
        'smtp_sender': user.display_sender_email or settings.smtp_sender,
        'target_email': user.employer_target_email or settings.employer_email,
    }


def send_email(config, to_email, subject, html_body):
    if not all([
        config.get('smtp_host'),
        config.get('smtp_port'),
        config.get('smtp_username'),
        config.get('smtp_password'),
        config.get('smtp_sender'),
        to_email,
    ]):
        return False, 'הגדרות המייל אינן מלאות.'

    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = config['smtp_sender']
    msg['To'] = to_email
    msg.attach(MIMEText(html_body, 'html', 'utf-8'))

    try:
        with smtplib.SMTP(config['smtp_host'], int(config['smtp_port'])) as server:
            server.starttls()
            server.login(config['smtp_username'], config['smtp_password'])
            server.sendmail(config['smtp_sender'], [to_email], msg.as_string())
        return True, 'נשלח בהצלחה'
    except Exception as exc:
        return False, str(exc)


def format_task_summary(tasks):
    sections = []
    for task in tasks:
        priority = PRIORITY_META.get(task.priority, PRIORITY_META['normal'])
        lines = [
            f"<h3>{task.title}</h3>",
            f"<p><b>עובד:</b> {task.assignee.full_name}<br><b>סטטוס:</b> {STATUS_META.get(task.status, task.status)}<br><b>עדיפות:</b> {priority['icon']} {priority['label']}<br><b>עדכון אחרון:</b> {task.updated_at.strftime('%d/%m/%Y %H:%M')}</p>",
            f"<p><b>תיאור:</b><br>{(task.description or '').replace(chr(10), '<br>')}</p>",
        ]
        if task.updates:
            lines.append('<ul>')
            for upd in task.updates:
                lines.append(
                    f"<li><b>{upd.author_name}</b> | {upd.created_at.strftime('%d/%m/%Y %H:%M')}<br>{upd.content.replace(chr(10), '<br>')}</li>"
                )
            lines.append('</ul>')
        sections.append('\n'.join(lines))
    return '<hr>'.join(sections) if sections else '<p>לא נמצאו משימות.</p>'
