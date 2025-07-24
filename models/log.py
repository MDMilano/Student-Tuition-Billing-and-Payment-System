import pymysql
from datetime import datetime, timedelta
from config import Config
import math
import json
from flask import request


class Log:
    def __init__(self, id=None, user_id=None, action=None, table_name=None,
                 record_id=None, user_agent=None, created_at=None, user_name=None,
                 severity=None, ip_address=None, additional_data=None):
        self.id = id
        self.user_id = user_id
        self.action = action
        self.table_name = table_name
        self.record_id = record_id
        self.user_agent = user_agent
        self.created_at = created_at
        self.user_name = user_name
        self.severity = severity
        self.ip_address = ip_address
        self.additional_data = additional_data

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
    def create(user_id, action, table_name=None, record_id=None, user_agent=None,
               severity='INFO', ip_address=None, additional_data=None):
        """Create a new log entry with enhanced data"""
        connection = Log.get_db_connection()
        try:
            with connection.cursor() as cursor:
                # Get user agent and IP if not provided
                if user_agent is None and request:
                    user_agent = request.headers.get('User-Agent', 'Unknown')

                if ip_address is None and request:
                    ip_address = Log._get_client_ip()

                # Convert additional_data to JSON string if it's a dict
                if additional_data and isinstance(additional_data, dict):
                    additional_data = json.dumps(additional_data, default=str)

                cursor.execute('''
                    INSERT INTO logs (user_id, action, table_name, record_id, user_agent, 
                                    severity, ip_address, additional_data)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ''', (user_id, action, table_name, record_id, user_agent,
                      severity, ip_address, additional_data))
                connection.commit()
                return cursor.lastrowid
        except Exception as e:
            print(f"Error creating log: {e}")
            return None
        finally:
            connection.close()

    @staticmethod
    def _get_client_ip():
        """Get the real client IP address"""
        if request:
            # Check for forwarded headers (useful behind proxies/load balancers)
            forwarded_ips = request.headers.get('X-Forwarded-For')
            if forwarded_ips:
                return forwarded_ips.split(',')[0].strip()

            real_ip = request.headers.get('X-Real-IP')
            if real_ip:
                return real_ip

            return request.remote_addr
        return None

    @staticmethod
    def get_paginated_logs(page=1, per_page=20):
        """Get logs with pagination and user names"""
        connection = Log.get_db_connection()
        try:
            with connection.cursor() as cursor:
                # Calculate offset
                offset = (page - 1) * per_page

                # Get total count
                cursor.execute("SELECT COUNT(*) as count FROM logs")
                total = cursor.fetchone()['count']

                # Get logs with user names
                cursor.execute('''
                    SELECT l.*, u.name as user_name
                    FROM logs l
                    LEFT JOIN users u ON l.user_id = u.id
                    ORDER BY l.created_at DESC
                    LIMIT %s OFFSET %s
                ''', (per_page, offset))

                logs_data = cursor.fetchall()
                logs = [Log(**log_data) for log_data in logs_data]

                # Create simple pagination object
                pagination = {
                    'page': page,
                    'per_page': per_page,
                    'total': total,
                    'pages': math.ceil(total / per_page),
                    'has_prev': page > 1,
                    'has_next': page < math.ceil(total / per_page),
                    'prev_num': page - 1 if page > 1 else None,
                    'next_num': page + 1 if page < math.ceil(total / per_page) else None
                }

                # Add iter_pages method
                def iter_pages():
                    start = max(1, page - 2)
                    end = min(pagination['pages'] + 1, page + 3)
                    for num in range(start, end):
                        yield num

                pagination['iter_pages'] = iter_pages

                return {
                    'logs': logs,
                    'pagination': pagination
                }

        except Exception as e:
            print(f"Error getting paginated logs: {e}")
            return {'logs': [], 'pagination': None}
        finally:
            connection.close()

    @staticmethod
    def get_by_id(log_id):
        """Get specific log by ID with user name"""
        connection = Log.get_db_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute('''
                    SELECT l.*, u.name as user_name
                    FROM logs l
                    LEFT JOIN users u ON l.user_id = u.id
                    WHERE l.id = %s
                ''', (log_id,))

                log_data = cursor.fetchone()
                if log_data:
                    return Log(**log_data)
                return None

        except Exception as e:
            print(f"Error getting log by ID: {e}")
            return None
        finally:
            connection.close()

    @staticmethod
    def get_log_statistics():
        """Get various log statistics"""
        connection = Log.get_db_connection()
        try:
            with connection.cursor() as cursor:
                # Today's count
                cursor.execute('''
                    SELECT COUNT(*) as count FROM logs 
                    WHERE DATE(created_at) = CURDATE()
                ''')
                today_count = cursor.fetchone()['count']

                # Unique users count (users who performed actions)
                cursor.execute('''
                    SELECT COUNT(DISTINCT user_id) as count FROM logs 
                    WHERE created_at >= DATE_SUB(NOW(), INTERVAL 7 DAY)
                ''')
                unique_users = cursor.fetchone()['count']

                # Recent count (last hour)
                cursor.execute('''
                    SELECT COUNT(*) as count FROM logs 
                    WHERE created_at >= DATE_SUB(NOW(), INTERVAL 1 HOUR)
                ''')
                recent_count = cursor.fetchone()['count']

                # Critical events count (last 24 hours)
                cursor.execute('''
                    SELECT COUNT(*) as count FROM logs 
                    WHERE severity IN ('ERROR', 'CRITICAL') 
                    AND created_at >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
                ''')
                critical_count = cursor.fetchone()['count']

                return {
                    'today_count': today_count,
                    'unique_users': unique_users,
                    'recent_count': recent_count,
                    'critical_count': critical_count
                }

        except Exception as e:
            print(f"Error getting log statistics: {e}")
            return {
                'today_count': 0,
                'unique_users': 0,
                'recent_count': 0,
                'critical_count': 0
            }
        finally:
            connection.close()

    @staticmethod
    def clear_old_logs(days=90):
        """Clear logs older than specified days"""
        connection = Log.get_db_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute('''
                    DELETE FROM logs 
                    WHERE created_at < DATE_SUB(NOW(), INTERVAL %s DAY)
                ''', (days,))
                connection.commit()
                return cursor.rowcount

        except Exception as e:
            print(f"Error clearing old logs: {e}")
            connection.rollback()
            raise e
        finally:
            connection.close()

    @staticmethod
    def setup_enhanced_logging():
        """Setup enhanced logging table structure"""
        connection = Log.get_db_connection()
        try:
            with connection.cursor() as cursor:
                # Add new columns to existing logs table if they don't exist
                cursor.execute("""
                    ALTER TABLE logs 
                    ADD COLUMN IF NOT EXISTS severity ENUM('INFO', 'WARNING', 'ERROR', 'CRITICAL') DEFAULT 'INFO' AFTER action,
                    ADD COLUMN IF NOT EXISTS ip_address VARCHAR(45) AFTER user_id,
                    ADD COLUMN IF NOT EXISTS additional_data JSON AFTER record_id,
                    ADD INDEX IF NOT EXISTS idx_severity (severity),
                    ADD INDEX IF NOT EXISTS idx_ip_address (ip_address)
                """)
                connection.commit()
                print("Enhanced logging setup completed!")
        except Exception as e:
            print(f"Error setting up enhanced logging: {e}")
        finally:
            connection.close()


# Important Activity Logger - Only logs significant events
class ImportantActivityLogger:
    """Focused logger that only captures important administrative activities"""

    # Define what constitutes important activities
    IMPORTANT_ACTIVITIES = {
        'STUDENT_MANAGEMENT': [
            'student_added', 'student_updated', 'student_deactivated',
            'student_activated', 'bulk_student_export'
        ],
        'COURSE_MANAGEMENT': [
            'course_added', 'course_updated', 'course_deleted'
        ],
        'USER_MANAGEMENT': [
            'cashier_added', 'cashier_updated', 'cashier_deactivated',
            'cashier_activated', 'admin_login', 'login_failed'
        ],
        'PAYMENT_ACTIVITIES': [
            'payment_recorded', 'payment_updated', 'large_payment_alert'
        ],
        'SYSTEM_MAINTENANCE': [
            'logs_cleared', 'data_exported', 'system_backup'
        ],
        'SECURITY_EVENTS': [
            'unauthorized_access', 'suspicious_activity', 'duplicate_entry_attempt'
        ]
    }

    @staticmethod
    def log_student_activity(user_id, action, student_data, student_id=None):
        """Log important student-related activities"""
        try:
            student_name = f"{student_data.get('first_name', '')} {student_data.get('last_name', '')}"
            student_sid = student_data.get('student_id', 'N/A')

            action_messages = {
                'add': f"Added new student: {student_name} (ID: {student_sid})",
                'update': f"Updated student: {student_name} (ID: {student_sid})",
                'deactivate': f"Deactivated student: {student_name} (ID: {student_sid})",
                'activate': f"Activated student: {student_name} (ID: {student_sid})"
            }

            message = action_messages.get(action, f"Student {action}: {student_name}")
            severity = 'WARNING' if action in ['deactivate'] else 'INFO'

            additional_data = {
                'student_id': student_sid,
                'student_name': student_name,
                'course_id': student_data.get('course_id'),
                'action_type': f'student_{action}'
            }

            Log.create(
                user_id=user_id,
                action=message,
                table_name='students',
                record_id=student_id,
                severity=severity,
                additional_data=additional_data
            )

        except Exception as e:
            print(f"Error logging student activity: {e}")

    @staticmethod
    def log_course_activity(user_id, action, course_data, course_id=None):
        """Log important course-related activities"""
        try:
            course_name = course_data.get('name', 'Unknown')
            course_code = course_data.get('code', 'N/A')

            action_messages = {
                'add': f"Added new course: {course_name} ({course_code})",
                'update': f"Updated course: {course_name} ({course_code})",
                'delete': f"Deleted course: {course_name} ({course_code})"
            }

            message = action_messages.get(action, f"Course {action}: {course_name}")
            severity = 'WARNING' if action == 'delete' else 'INFO'

            additional_data = {
                'course_name': course_name,
                'course_code': course_code,
                'course_price': course_data.get('price'),
                'action_type': f'course_{action}'
            }

            Log.create(
                user_id=user_id,
                action=message,
                table_name='courses',
                record_id=course_id,
                severity=severity,
                additional_data=additional_data
            )

        except Exception as e:
            print(f"Error logging course activity: {e}")

    @staticmethod
    def log_cashier_activity(user_id, action, cashier_data, cashier_id=None):
        """Log important cashier management activities"""
        try:
            cashier_name = cashier_data.get('name', 'Unknown')
            cashier_email = cashier_data.get('email', 'N/A')

            action_messages = {
                'add': f"Added new cashier: {cashier_name} ({cashier_email})",
                'update': f"Updated cashier: {cashier_name} ({cashier_email})",
                'deactivate': f"Deactivated cashier: {cashier_name}",
                'activate': f"Activated cashier: {cashier_name}"
            }

            message = action_messages.get(action, f"Cashier {action}: {cashier_name}")
            severity = 'WARNING' if action in ['deactivate'] else 'INFO'

            additional_data = {
                'cashier_name': cashier_name,
                'cashier_email': cashier_email,
                'action_type': f'cashier_{action}'
            }

            Log.create(
                user_id=user_id,
                action=message,
                table_name='users',
                record_id=cashier_id,
                severity=severity,
                additional_data=additional_data
            )

        except Exception as e:
            print(f"Error logging cashier activity: {e}")

    @staticmethod
    def log_export_activity(user_id, export_type, record_count, filename=None):
        """Log data export activities"""
        try:
            message = f"Exported {export_type}: {record_count} records"
            if filename:
                message += f" to {filename}"

            additional_data = {
                'export_type': export_type,
                'record_count': record_count,
                'filename': filename,
                'action_type': 'data_export'
            }

            Log.create(
                user_id=user_id,
                action=message,
                table_name='system',
                severity='INFO',
                additional_data=additional_data
            )

        except Exception as e:
            print(f"Error logging export activity: {e}")

    @staticmethod
    def log_system_maintenance(user_id, action, details=None):
        """Log system maintenance activities"""
        try:
            action_messages = {
                'logs_cleared': f"Cleared old log entries: {details.get('deleted_count', 0)} records",
                'backup_created': f"System backup created: {details.get('backup_size', 'Unknown size')}",
                'database_maintenance': f"Database maintenance completed: {details.get('operation', 'Unknown')}"
            }

            message = action_messages.get(action, f"System maintenance: {action}")

            additional_data = {
                'maintenance_type': action,
                'details': details,
                'action_type': 'system_maintenance'
            }

            Log.create(
                user_id=user_id,
                action=message,
                table_name='system',
                severity='WARNING',
                additional_data=additional_data
            )

        except Exception as e:
            print(f"Error logging system maintenance: {e}")

    @staticmethod
    def log_security_event(user_id, event_type, description, severity='WARNING'):
        """Log security-related events"""
        try:
            message = f"SECURITY: {event_type} - {description}"

            additional_data = {
                'event_type': event_type,
                'description': description,
                'action_type': 'security_event'
            }

            Log.create(
                user_id=user_id or 0,
                action=message,
                table_name='security',
                severity=severity,
                additional_data=additional_data
            )

        except Exception as e:
            print(f"Error logging security event: {e}")

    @staticmethod
    def log_authentication(user_id, email, success=True, reason=None):
        """Log authentication attempts"""
        try:
            if success:
                message = f"Successful admin login: {email}"
                severity = 'INFO'
                additional_data = {
                    'email': email,
                    'login_success': True,
                    'action_type': 'authentication'
                }
            else:
                message = f"Failed login attempt: {email}"
                if reason:
                    message += f" - {reason}"
                severity = 'WARNING'
                additional_data = {
                    'email': email,
                    'login_success': False,
                    'failure_reason': reason,
                    'action_type': 'authentication'
                }

            Log.create(
                user_id=user_id or 0,
                action=message,
                table_name='authentication',
                severity=severity,
                additional_data=additional_data
            )

        except Exception as e:
            print(f"Error logging authentication: {e}")


# Enhanced helper function - only logs important activities
def log_activity(user_id, action, table_name=None, record_id=None, user_agent=None,
                 severity='INFO', additional_data=None):
    """
    Enhanced helper function to create log entries for important activities only

    Args:
        user_id: ID of the user performing the action
        action: Description of the action performed
        table_name: Database table affected (optional)
        record_id: ID of the affected record (optional)
        user_agent: User agent string (optional, will be auto-detected)
        severity: Log severity level (INFO, WARNING, ERROR, CRITICAL)
        additional_data: Additional contextual data (dict)
    """
    try:
        # Only log if it's an important activity
        important_keywords = [
            'added', 'updated', 'deleted', 'deactivated', 'activated',
            'exported', 'cleared', 'failed', 'error', 'security',
            'backup', 'maintenance', 'login', 'unauthorized'
        ]

        # Check if the action contains important keywords
        if any(keyword in action.lower() for keyword in important_keywords):
            Log.create(
                user_id=user_id,
                action=action,
                table_name=table_name,
                record_id=record_id,
                user_agent=user_agent,
                severity=severity,
                additional_data=additional_data
            )
    except Exception as e:
        print(f"Error logging activity: {e}")
        # Don't raise exception to avoid breaking the main functionality