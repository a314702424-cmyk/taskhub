from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash


db = SQLAlchemy()


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    full_name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(255), default='')
    role = db.Column(db.String(20), default='employee')
    is_active_user = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    smtp_host = db.Column(db.String(255), default='')
    smtp_port = db.Column(db.Integer, default=587)
    smtp_username = db.Column(db.String(255), default='')
    smtp_password = db.Column(db.String(255), default='')
    sender_email = db.Column(db.String(255), default='')
    employer_target_email = db.Column(db.String(255), default='')
    theme_color = db.Column(db.String(20), default='#1a73e8')

    tasks = db.relationship('Task', backref='assignee', lazy=True, foreign_keys='Task.assignee_id')
    created_tasks = db.relationship('Task', backref='creator', lazy=True, foreign_keys='Task.created_by_id')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def display_sender_email(self):
        return self.sender_email or self.email or self.smtp_username

    def to_dict(self, include_sensitive=True):
        data = {
            'username': self.username,
            'full_name': self.full_name,
            'email': self.email,
            'role': self.role,
            'is_active_user': self.is_active_user,
            'smtp_host': self.smtp_host,
            'smtp_port': self.smtp_port,
            'smtp_username': self.smtp_username,
            'sender_email': self.sender_email,
            'employer_target_email': self.employer_target_email,
            'theme_color': self.theme_color,
        }
        if include_sensitive:
            data['smtp_password'] = self.smtp_password
        return data


class Task(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, default='')
    status = db.Column(db.String(30), default='open')
    priority = db.Column(db.String(20), default='normal')
    position = db.Column(db.Integer, default=0)
    due_date = db.Column(db.Date, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    assignee_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

    updates = db.relationship(
        'TaskUpdate',
        backref='task',
        lazy=True,
        cascade='all, delete-orphan',
        order_by='TaskUpdate.created_at.desc()'
    )

    def to_dict(self):
        return {
            'title': self.title,
            'description': self.description,
            'status': self.status,
            'priority': self.priority,
            'position': self.position,
            'due_date': self.due_date.isoformat() if self.due_date else None,
            'assignee_username': self.assignee.username if self.assignee else None,
            'created_by_username': self.creator.username if self.creator else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'updates': [u.to_dict() for u in self.updates],
        }


class TaskUpdate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey('task.id'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    author_name = db.Column(db.String(120), default='')

    def to_dict(self):
        return {
            'content': self.content,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'author_name': self.author_name,
        }


class AppSetting(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    company_name = db.Column(db.String(255), default='TaskHub')
    employer_email = db.Column(db.String(255), default='')
    smtp_host = db.Column(db.String(255), default='')
    smtp_port = db.Column(db.Integer, default=587)
    smtp_username = db.Column(db.String(255), default='')
    smtp_password = db.Column(db.String(255), default='')
    smtp_sender = db.Column(db.String(255), default='')
    primary_color = db.Column(db.String(20), default='#1a73e8')
    secondary_color = db.Column(db.String(20), default='#e8f0fe')
    accent_color = db.Column(db.String(20), default='#34a853')
    card_color = db.Column(db.String(20), default='#ffffff')
    text_color = db.Column(db.String(20), default='#202124')
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'company_name': self.company_name,
            'employer_email': self.employer_email,
            'smtp_host': self.smtp_host,
            'smtp_port': self.smtp_port,
            'smtp_username': self.smtp_username,
            'smtp_password': self.smtp_password,
            'smtp_sender': self.smtp_sender,
            'primary_color': self.primary_color,
            'secondary_color': self.secondary_color,
            'accent_color': self.accent_color,
            'card_color': self.card_color,
            'text_color': self.text_color,
        }

    def apply_dict(self, data):
        for key, value in data.items():
            if hasattr(self, key):
                setattr(self, key, value)


def get_settings():
    settings = AppSetting.query.first()
    if not settings:
        settings = AppSetting()
        db.session.add(settings)
        db.session.commit()
    return settings


def ensure_sqlite_columns():
    engine = db.engine
    if not str(engine.url).startswith('sqlite'):
        return
    with engine.connect() as conn:
        existing = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(user)").fetchall()}
        wanted = {
            'smtp_host': "ALTER TABLE user ADD COLUMN smtp_host VARCHAR(255) DEFAULT ''",
            'smtp_port': "ALTER TABLE user ADD COLUMN smtp_port INTEGER DEFAULT 587",
            'smtp_username': "ALTER TABLE user ADD COLUMN smtp_username VARCHAR(255) DEFAULT ''",
            'smtp_password': "ALTER TABLE user ADD COLUMN smtp_password VARCHAR(255) DEFAULT ''",
            'sender_email': "ALTER TABLE user ADD COLUMN sender_email VARCHAR(255) DEFAULT ''",
            'employer_target_email': "ALTER TABLE user ADD COLUMN employer_target_email VARCHAR(255) DEFAULT ''",
            'theme_color': "ALTER TABLE user ADD COLUMN theme_color VARCHAR(20) DEFAULT '#1a73e8'",
            'is_active_user': "ALTER TABLE user ADD COLUMN is_active_user BOOLEAN DEFAULT 1",
        }
        for col, stmt in wanted.items():
            if col not in existing:
                conn.exec_driver_sql(stmt)
        conn.commit()


def export_all_data():
    settings = get_settings().to_dict()
    users = [u.to_dict(include_sensitive=True) for u in User.query.order_by(User.id.asc()).all() if u.username != 'admin']
    tasks = [t.to_dict() for t in Task.query.order_by(Task.position.asc(), Task.id.asc()).all()]
    return {
        'version': 11,
        'exported_at': datetime.utcnow().isoformat(),
        'settings': settings,
        'users': users,
        'tasks': tasks,
    }


def import_all_data(payload: dict):
    settings = get_settings()
    settings.apply_dict(payload.get('settings') or {})

    for task in Task.query.all():
        db.session.delete(task)
    for user in User.query.filter(User.username != 'admin').all():
        db.session.delete(user)
    db.session.flush()

    username_map = {}
    admin = User.query.filter_by(username='admin').first()
    if admin:
        admin.email = settings.employer_email or admin.email
        admin.sender_email = settings.smtp_sender or settings.employer_email or admin.sender_email
        username_map['admin'] = admin

    for row in payload.get('users') or []:
        username = (row.get('username') or '').strip()
        if not username:
            continue
        user = User(
            username=username,
            full_name=(row.get('full_name') or username).strip(),
            email=(row.get('email') or '').strip(),
            role=row.get('role', 'employee'),
            is_active_user=bool(row.get('is_active_user', True)),
            smtp_host=(row.get('smtp_host') or '').strip(),
            smtp_port=int(row.get('smtp_port') or 587),
            smtp_username=(row.get('smtp_username') or '').strip(),
            smtp_password=(row.get('smtp_password') or '').strip(),
            sender_email=(row.get('sender_email') or '').strip(),
            employer_target_email=(row.get('employer_target_email') or '').strip(),
            theme_color=(row.get('theme_color') or '#1a73e8').strip() or '#1a73e8',
        )
        user.set_password('123456')
        db.session.add(user)
        db.session.flush()
        username_map[user.username] = user

    for row in payload.get('tasks') or []:
        assignee = username_map.get(row.get('assignee_username')) or admin
        creator = username_map.get(row.get('created_by_username')) or admin or assignee
        if not assignee:
            continue
        task = Task(
            title=(row.get('title') or 'ללא כותרת').strip(),
            description=row.get('description', ''),
            status=row.get('status', 'open'),
            priority=row.get('priority', 'normal'),
            position=int(row.get('position') or 0),
            due_date=datetime.fromisoformat(row['due_date']).date() if row.get('due_date') else None,
            assignee_id=assignee.id,
            created_by_id=(creator.id if creator else assignee.id),
            created_at=datetime.fromisoformat(row['created_at']) if row.get('created_at') else datetime.utcnow(),
            updated_at=datetime.fromisoformat(row['updated_at']) if row.get('updated_at') else datetime.utcnow(),
        )
        db.session.add(task)
        db.session.flush()
        for upd in row.get('updates') or []:
            note = TaskUpdate(
                task_id=task.id,
                content=upd.get('content', ''),
                author_name=upd.get('author_name', ''),
                created_at=datetime.fromisoformat(upd['created_at']) if upd.get('created_at') else datetime.utcnow(),
            )
            db.session.add(note)
    db.session.commit()


def ensure_default_data():
    settings = get_settings()
    admin = User.query.filter_by(username='admin').first()
    if not admin:
        admin = User(
            username='admin',
            full_name='מנהל מערכת',
            email=settings.employer_email or '',
            sender_email=settings.smtp_sender or settings.employer_email or '',
            role='admin',
        )
        admin.set_password('admin123')
        db.session.add(admin)
        db.session.commit()
