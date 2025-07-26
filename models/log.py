import pymysql
from datetime import datetime, timedelta
from config import Config
from database.init_db import get_db_connection


class Log:
    def __init__(self, id=None, user_id=None, action=None, role=None, created_at=None, user_name=None):
        self.id = id
        self.user_id = user_id
        self.action = action
        self.role = role
        self.created_at = created_at
        self.user_name = user_name

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
    def _iter_pages(current_page, total_pages):
        """Generate page numbers for pagination"""
        if total_pages <= 10:
            return range(1, total_pages + 1)

        if current_page <= 6:
            return list(range(1, 8)) + [None] + [total_pages]
        elif current_page >= total_pages - 5:
            return [1] + [None] + list(range(total_pages - 6, total_pages + 1))
        else:
            return [1] + [None] + list(range(current_page - 2, current_page + 3)) + [None] + [total_pages]

    @classmethod
    def get_paginated_logs(cls, page=1, per_page=20):
        """Get paginated logs with user information"""
        connection = get_db_connection()
        try:
            with connection.cursor() as cursor:
                # Get total count
                cursor.execute("SELECT COUNT(*) as total FROM logs")
                total = cursor.fetchone()['total']

                # Calculate pagination
                offset = (page - 1) * per_page
                total_pages = (total + per_page - 1) // per_page

                # Get logs for current page with updated query
                sql = """
                    SELECT l.id, l.user_id, l.action, l.role, l.created_at,
                           u.name as user_name
                    FROM logs l
                    LEFT JOIN users u ON l.user_id = u.id
                    ORDER BY l.created_at DESC
                    LIMIT %s OFFSET %s
                """
                cursor.execute(sql, (per_page, offset))
                results = cursor.fetchall()

                logs = []
                for row in results:
                    log = cls(
                        id=row['id'],
                        user_id=row['user_id'],
                        action=row['action'],
                        role=row['role'],
                        created_at=row['created_at'],
                        user_name=row['user_name']
                    )
                    logs.append(log)

                # Simple pagination object
                pagination = type('Pagination', (), {
                    'page': page,
                    'per_page': per_page,
                    'total': total,
                    'pages': total_pages,
                    'has_prev': page > 1,
                    'prev_num': page - 1 if page > 1 else None,
                    'has_next': page < total_pages,
                    'next_num': page + 1 if page < total_pages else None,
                    'iter_pages': lambda self=None: cls._iter_pages(page, total_pages)
                })()

                return {
                    'logs': logs,
                    'pagination': pagination
                }
        finally:
            connection.close()

    @classmethod
    def get_by_id(cls, log_id):
        """Get a specific log by ID"""
        connection = get_db_connection()
        try:
            with connection.cursor() as cursor:
                sql = """
                    SELECT l.id, l.user_id, l.action, l.role, l.created_at,
                           u.name as user_name
                    FROM logs l
                    LEFT JOIN users u ON l.user_id = u.id
                    WHERE l.id = %s
                """
                cursor.execute(sql, (log_id,))
                row = cursor.fetchone()

                if row:
                    return cls(
                        id=row['id'],
                        user_id=row['user_id'],
                        action=row['action'],
                        role=row['role'],
                        created_at=row['created_at'],
                        user_name=row['user_name']
                    )
                return None
        finally:
            connection.close()

    @classmethod
    def get_log_statistics(cls):
        """Get basic log statistics"""
        connection = get_db_connection()
        try:
            with connection.cursor() as cursor:
                # Today's count
                cursor.execute("""
                    SELECT COUNT(*) as count FROM logs 
                    WHERE DATE(created_at) = CURDATE()
                """)
                today_count = cursor.fetchone()['count']

                # Unique users today
                cursor.execute("""
                    SELECT COUNT(DISTINCT user_id) as count FROM logs 
                    WHERE DATE(created_at) = CURDATE()
                """)
                unique_users = cursor.fetchone()['count']

                # Recent count (last hour)
                cursor.execute("""
                    SELECT COUNT(*) as count FROM logs 
                    WHERE created_at >= DATE_SUB(NOW(), INTERVAL 1 HOUR)
                """)
                recent_count = cursor.fetchone()['count']

                return {
                    'today_count': today_count,
                    'unique_users': unique_users,
                    'recent_count': recent_count
                }
        finally:
            connection.close()

    @classmethod
    def clear_old_logs(cls, days=90):
        """Clear logs older than specified days"""
        connection = get_db_connection()
        try:
            with connection.cursor() as cursor:
                cutoff_date = datetime.now() - timedelta(days=days)

                # Count logs to be deleted
                cursor.execute("""
                    SELECT COUNT(*) as count FROM logs 
                    WHERE created_at < %s
                """, (cutoff_date,))
                count = cursor.fetchone()['count']

                # Delete old logs
                cursor.execute("""
                    DELETE FROM logs 
                    WHERE created_at < %s
                """, (cutoff_date,))

                connection.commit()
                return count
        finally:
            connection.close()