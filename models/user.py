from werkzeug.security import check_password_hash, generate_password_hash
from flask_login import UserMixin
import pymysql
from config import Config
from database.init_db import get_db_connection


class User(UserMixin):
    def __init__(self, id=None, name=None, email=None, password_hash=None, role=None, is_active=True, created_at=None,
                 updated_at=None):
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

    '''commented to use the automatic connecting to database with different ports.'''
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
        # connection = User.get_db_connection()
        #for the automated connetion:
        connection = get_db_connection()
        """Get user by ID - Fixed version"""
        connection = get_db_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT * FROM users WHERE id = %s", (user_id,))
                user_data = cursor.fetchone()
                if user_data:
                    return User(**user_data)
                return None
        except Exception as e:
            print(f"Error getting user by ID: {e}")
            return None
        finally:
            connection.close()

    @staticmethod
    def get_by_email(email):
#         connection = User.get_db_connection()
#for the automated connetion:
        connection = get_db_connection()

        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT * FROM users WHERE email = %s;", (email,))
                user_data = cursor.fetchone()
                if user_data:
                    return User(**user_data)
        finally:
            connection.close()
        return None

    @staticmethod
    def create(name, email, password, role='cashier'):
        """Create new user with enhanced validation"""
        connection = get_db_connection()
        try:
            with connection.cursor() as cursor:
                # Check if email already exists
                cursor.execute("SELECT id FROM users WHERE email = %s", (email,))
                if cursor.fetchone():
                    raise Exception("Email already exists")

                password_hash = generate_password_hash(password)
                cursor.execute('''
                    INSERT INTO users (name, email, password_hash, role, is_active)
                    VALUES (%s, %s, %s, %s, TRUE)
                ''', (name, email, password_hash, role))
                connection.commit()
                return cursor.lastrowid
        except Exception as e:
            print(f"Error creating user: {e}")
            connection.rollback()
            raise e
        finally:
            connection.close()

    @staticmethod
    def update_password(email, new_password):
        #connection = User.get_db_connection()
        #for the automated connetion:
        connection = get_db_connection()

        try:
            with connection.cursor() as cursor:
                password_hash = generate_password_hash(new_password)
                cursor.execute('''
                    UPDATE users SET password_hash = %s
                    WHERE email = %s
                ''', (password_hash, email))
                connection.commit()
                return cursor.rowcount > 0
        finally:
            connection.close()

    @staticmethod
    def get_all_cashiers():
#         connection = User.get_db_connection()
#for the automated connetion:

        """Get all users with cashier role"""
        connection = get_db_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT * FROM users WHERE role = 'cashier' ORDER BY created_at DESC")
                rows = cursor.fetchall()

                cashiers = []
                for row in rows:
                    cashiers.append(User(**row))

                return cashiers
        except Exception as e:
            print(f"Error getting cashiers: {e}")
            return []
        finally:
            connection.close()

    @staticmethod
    def update_cashier(cashier_id, name, email):
        """Update cashier information"""
        # connection = User.get_db_connection()
        connection = get_db_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute("""
                    UPDATE users 
                    SET name = %s, email = %s, updated_at = CURRENT_TIMESTAMP 
                    WHERE id = %s AND role = 'cashier'
                """, (name, email, cashier_id))

                connection.commit()
                return cursor.rowcount > 0
        except Exception as e:
            print(f"Error updating cashier: {e}")
            connection.rollback()
            raise e
        finally:
            connection.close()

    @staticmethod
    def toggle_active(user_id):
#         connection = User.get_db_connection()
#for the automated connetion:

        """Toggle user active status"""
        connection = get_db_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute('''
                    UPDATE users SET is_active = NOT is_active, updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                ''', (user_id,))
                connection.commit()
                return cursor.rowcount > 0
        except Exception as e:
            print(f"Error toggling user status: {e}")
            connection.rollback()
            return False
        finally:
            connection.close()

    @staticmethod
    def has_payment_records(user_id):
        """Check if user has any payment records"""
        # connection = User.get_db_connection()
        connection = get_db_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) as count FROM payments WHERE collected_by = %s", (user_id,))
                result = cursor.fetchone()
                return result['count'] > 0
        except Exception as e:
            print(f"Error checking payment records: {e}")
            return True  # Safe default - assume has records
        finally:
            connection.close()

    @staticmethod
    def delete(user_id):
        """Delete user (use with caution)"""
        connection = get_db_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM users WHERE id = %s", (user_id,))
                connection.commit()
                return cursor.rowcount > 0
        except Exception as e:
            print(f"Error deleting user: {e}")
            connection.rollback()
            return False
        finally:
            connection.close()