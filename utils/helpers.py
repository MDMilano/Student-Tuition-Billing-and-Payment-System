import random
import string
from functools import wraps
from flask import flash, redirect, url_for
from flask_login import current_user
import pymysql
from config import Config


def generate_otp():
    return ''.join(random.choices(string.digits, k=6))


def log_activity(user_id, action, table_name=None, record_id=None, ip_address=None, user_agent=None):
    connection = pymysql.connect(
        host=Config.MYSQL_HOST,
        user=Config.MYSQL_USER,
        password=Config.MYSQL_PASSWORD,
        database=Config.MYSQL_DB,
        cursorclass=pymysql.cursors.DictCursor
    )

    try:
        with connection.cursor() as cursor:
            cursor.execute('''
                INSERT INTO logs (user_id, action, table_name, record_id, ip_address, user_agent)
                VALUES (%s, %s, %s, %s, %s, %s)
            ''', (user_id, action, table_name, record_id, ip_address, user_agent))
            connection.commit()
    except Exception as e:
        print(f"Error logging activity: {e}")
    finally:
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