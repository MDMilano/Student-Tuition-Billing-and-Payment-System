import random
import string
from datetime import datetime
from functools import wraps
from flask import flash, redirect, url_for
from flask_login import current_user
import pymysql
from config import Config
from database.init_db import get_working_connection


def generate_otp():
    return ''.join(random.choices(string.digits, k=6))


def log_activity(user_id, action, role=None):
    # connection = pymysql.connect(
    #     host=Config.MYSQL_HOST,
    #     user=Config.MYSQL_USER,
    #     password=Config.MYSQL_PASSWORD,
    #     database=Config.MYSQL_DB,
    #     cursorclass=pymysql.cursors.DictCursor
    # )

    '''use automated port'''
    _, port = get_working_connection()
    connection = pymysql.connect(
        host=Config.MYSQL_HOST,
        port=port,  # ✅ Add this line
        user=Config.MYSQL_USER,
        password=Config.MYSQL_PASSWORD,
        database=Config.MYSQL_DB,
        cursorclass=pymysql.cursors.DictCursor
    )

    """Log user activity with the new table structure"""
    try:
        from models.user import User  # Import here to avoid circular imports
        # connection = User.get_db_connection()

        with connection.cursor() as cursor:
            # Get user role if not provided
            if role is None:
                cursor.execute("SELECT role FROM users WHERE id = %s", (user_id,))
                user = cursor.fetchone()
                role = user['role'] if user else 'unknown'

            # Insert log with new table structure
            cursor.execute("""
                INSERT INTO logs (user_id, action, role, created_at)
                VALUES (%s, %s, %s, NOW())
            """, (user_id, action, role))

            connection.commit()

    except Exception as e:
        print(f"Error logging activity: {e}")
    finally:
        if 'connection' in locals():
            connection.close()


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'admin':
            flash('Access denied. Admin privileges required.', 'error')
            return redirect(url_for('index'))
        return f(*args, **kwargs)

    return decorated_function


def cashier_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role not in ['cashier', 'admin']:
            flash('Access denied. Cashier privileges required.', 'error')
            return redirect(url_for('index'))
        return f(*args, **kwargs)

    return decorated_function