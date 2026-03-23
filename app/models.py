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

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def display_sender_email(self):
        return self.sender_email or self.email or self.smtp_username


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


class TaskUpdate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey('task.id'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    author_name = db.Column(db.String(120), default='')


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
