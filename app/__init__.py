import os
from flask import Flask
from flask_login import LoginManager
from .models import db, User, ensure_default_data, ensure_sqlite_columns

login_manager = LoginManager()
login_manager.login_view = 'login'

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


def create_app():
    app = Flask(__name__, instance_relative_config=True)
    os.makedirs(app.instance_path, exist_ok=True)
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'change-this-secret-key')

    database_url = os.environ.get('DATABASE_URL', f"sqlite:///{os.path.join(app.instance_path, 'app.db')}")
    if database_url.startswith('postgres://'):
        database_url = database_url.replace('postgres://', 'postgresql://', 1)

    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'pool_pre_ping': True,
        'pool_recycle': 280,
    }

    db.init_app(app)
    login_manager.init_app(app)

    from .routes import register_routes
    register_routes(app)

    with app.app_context():
        db.create_all()
        ensure_sqlite_columns()
        ensure_default_data()

    return app
