from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import check_password_hash
import pymysql
from config import Config
from models.user import User
from blueprints.auth import auth_bp
from blueprints.admin import admin_bp
from blueprints.cashier import cashier_bp
from utils.helpers import log_activity

app = Flask(__name__)
app.config.from_object(Config)

# Initialize Flask-Login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'index'
login_manager.login_message = 'Please log in to access this page.'
login_manager.login_message_category = 'info'

@login_manager.user_loader
def load_user(user_id):
    return User.get_by_id(int(user_id))

# Register Blueprints
app.register_blueprint(auth_bp)
app.register_blueprint(admin_bp, url_prefix='/admin')
app.register_blueprint(cashier_bp, url_prefix='/cashier')

@app.route('/')
def index():
    if current_user.is_authenticated:
        if current_user.role == 'admin':
            return redirect(url_for('admin.dashboard'))
        elif current_user.role == 'cashier':
            return redirect(url_for('cashier.dashboard'))
    return render_template('index.html')

@app.route('/login', methods=['POST'])
def login():
    email = request.form.get('email')
    password = request.form.get('password')

    if not email or not password:
        flash('Please enter both email and password.', 'error')
        return redirect(url_for('index'))

    user = User.get_by_email(email)

    if user and user.check_password(password):
        if not user.is_active:
            flash('Your account has been disabled. Please contact administrator.', 'error')
            return redirect(url_for('index'))

        login_user(user)
        # Simple login log with role
        session['user_name'] = user.name
        log_activity(user.id, f"User login: {user.email}", role=user.role)

        if user.role == 'admin':
            return redirect(url_for('admin.dashboard'))
        elif user.role == 'cashier':
            return redirect(url_for('cashier.dashboard'))
    else:
        flash('Invalid email or password.', 'error')

    return redirect(url_for('index'))

@app.route('/logout')
@login_required
def logout():
    # Simple logout log with role
    log_activity(current_user.id, f"User logout: {current_user.email}", role=current_user.role)
    logout_user()
    flash('You have been logged out successfully.', 'success')
    return redirect(url_for('index'))

# @app.errorhandler(404)
# def not_found(error):
#     return render_template('404.html'), 404
#
# @app.errorhandler(500)
# def internal_error(error):
#     return render_template('500.html'), 500

if __name__ == '__main__':
    app.run(debug=True, port=8000)