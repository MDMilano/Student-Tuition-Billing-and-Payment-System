from werkzeug.security import check_password_hash, generate_password_hash
from flask_login import UserMixin
import pymysql
from config import Config


class User(UserMixin):
    def __init__(self, id, name, email, password_hash, role, is_active, created_at, updated_at):
        self.id = id
        self.name = name
        self.email = email
        self.password_hash = password_hash
        self.role = role
        self._is_active = is_active
        self.created_at = created_at
        self.updated_at = updated_at

    @property
    def is_active(self):
        return self._is_active

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @staticmethod
    def get_db_connection():
        return pymysql.connect(
            host=Config.MYSQL_HOST,
            user=Config.MYSQL_USER,
            password=Config.MYSQL_PASSWORD,
            database=Config.MYSQL_DB,
            cursorclass=pymysql.cursors.DictCursor
        )

    @staticmethod
    def get_by_id(user_id):
        connection = User.get_db_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT * FROM users WHERE id = %s AND is_active = TRUE", (user_id,))
                user_data = cursor.fetchone()
                if user_data:
                    return User(**user_data)
        finally:
            connection.close()
        return None

    @staticmethod
    def get_by_email(email):
        connection = User.get_db_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT * FROM users WHERE email = %s AND is_active = TRUE", (email,))
                user_data = cursor.fetchone()
                if user_data:
                    return User(**user_data)
        finally:
            connection.close()
        return None

    @staticmethod
    def create(name, email, password, role):
        connection = User.get_db_connection()
        try:
            with connection.cursor() as cursor:
                password_hash = generate_password_hash(password)
                cursor.execute('''
                    INSERT INTO users (name, email, password_hash, role)
                    VALUES (%s, %s, %s, %s)
                ''', (name, email, password_hash, role))
                connection.commit()
                return cursor.lastrowid
        finally:
            connection.close()

    @staticmethod
    def update_password(email, new_password):
        connection = User.get_db_connection()
        try:
            with connection.cursor() as cursor:
                password_hash = generate_password_hash(new_password)
                cursor.execute('''
                    UPDATE users SET password_hash = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE email = %s
                ''', (password_hash, email))
                connection.commit()
                return cursor.rowcount > 0
        finally:
            connection.close()

    @staticmethod
    def get_all_cashiers():
        connection = User.get_db_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT * FROM users WHERE role = 'cashier' ORDER BY name")
                return cursor.fetchall()
        finally:
            connection.close()

    @staticmethod
    def toggle_active(user_id):
        connection = User.get_db_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute('''
                    UPDATE users SET is_active = NOT is_active, updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                ''', (user_id,))
                connection.commit()
                return cursor.rowcount > 0
        finally:
            connection.close()