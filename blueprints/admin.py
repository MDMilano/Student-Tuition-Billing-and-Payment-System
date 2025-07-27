from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from functools import wraps
from models.user import User
from models.log import Log
from utils.helpers import admin_required
import pymysql
from config import Config
from database.init_db import get_db_connection

from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash, make_response
import pymysql
from config import Config
import csv
from io import StringIO
import re
from datetime import datetime, timedelta

admin_bp = Blueprint('admin', __name__)


@admin_bp.route('/dashboard')
@login_required
@admin_required
def dashboard():
    # connection = User.get_db_connection()
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            # Get dashboard statistics

            # 1. Total active students
            cursor.execute("SELECT COUNT(*) as count FROM students WHERE is_active = TRUE")
            total_students = cursor.fetchone()['count']

            # 2. Total active courses
            cursor.execute("SELECT COUNT(*) as count FROM courses WHERE is_active = TRUE")
            total_courses = cursor.fetchone()['count']

            # 3. Total payments amount
            cursor.execute("SELECT COALESCE(SUM(amount_paid), 0) as total FROM payments")
            total_payments = cursor.fetchone()['total']

            # 4. Total active cashiers
            cursor.execute("SELECT COUNT(*) as count FROM users WHERE role = 'cashier' AND is_active = TRUE")
            total_active_cashiers = cursor.fetchone()['count']

            # 5. Recent activities from logs
            cursor.execute("""
                SELECT l.action, l.created_at, u.name as user_name, u.role as role
                FROM logs l
                JOIN users u ON l.user_id = u.id
                ORDER BY l.created_at DESC
                LIMIT 10
            """)
            recent_activities = cursor.fetchall()

            # 6. Payment status breakdown
            cursor.execute("""
                SELECT 
                    s.id,
                    s.first_name,
                    s.last_name,
                    c.price as course_price,
                    COALESCE(SUM(p.amount_paid), 0) as total_paid,
                    CASE 
                        WHEN COALESCE(SUM(p.amount_paid), 0) >= c.price THEN 'fully_paid'
                        WHEN COALESCE(SUM(p.amount_paid), 0) > 0 THEN 'partially_paid'
                        ELSE 'unpaid'
                    END as payment_status
                FROM students s
                JOIN courses c ON s.course_id = c.id
                LEFT JOIN payments p ON s.id = p.student_id
                WHERE s.is_active = TRUE AND c.is_active = TRUE
                GROUP BY s.id, c.price
            """)
            payment_breakdown = cursor.fetchall()

            # Count payment statuses
            fully_paid = sum(1 for p in payment_breakdown if p['payment_status'] == 'fully_paid')
            partially_paid = sum(1 for p in payment_breakdown if p['payment_status'] == 'partially_paid')
            unpaid = sum(1 for p in payment_breakdown if p['payment_status'] == 'unpaid')

            # Calculate percentages
            total_for_percentage = max(len(payment_breakdown), 1)  # Avoid division by zero
            fully_paid_percent = round((fully_paid / total_for_percentage) * 100, 1)
            partially_paid_percent = round((partially_paid / total_for_percentage) * 100, 1)
            unpaid_percent = round((unpaid / total_for_percentage) * 100, 1)

            # 7. Monthly revenue data for chart
            cursor.execute("""
                SELECT 
                    YEAR(payment_date) as year,
                    MONTH(payment_date) as month,
                    SUM(amount_paid) as monthly_total
                FROM payments
                WHERE payment_date >= DATE_SUB(CURDATE(), INTERVAL 12 MONTH)
                GROUP BY YEAR(payment_date), MONTH(payment_date)
                ORDER BY year, month
            """)
            monthly_revenue_data = cursor.fetchall()

            # 8. Recent payments for activities
            cursor.execute("""
                SELECT 
                    p.amount_paid,
                    p.payment_date,
                    p.created_at,
                    CONCAT(s.first_name, ' ', s.last_name) as student_name,
                    u.name as cashier_name,
                    p.payment_method
                FROM payments p
                JOIN students s ON p.student_id = s.id
                JOIN users u ON p.collected_by = u.id
                ORDER BY p.created_at DESC
                LIMIT 5
            """)
            recent_payments = cursor.fetchall()

            # 9. Course enrollment stats
            cursor.execute("""
                SELECT 
                    c.name as course_name,
                    COUNT(s.id) as enrolled_count
                FROM courses c
                LEFT JOIN students s ON c.id = s.course_id AND s.is_active = TRUE
                WHERE c.is_active = TRUE
                GROUP BY c.id
                ORDER BY enrolled_count DESC
                LIMIT 5
            """)
            top_courses = cursor.fetchall()

    finally:
        connection.close()

    return render_template('admin/dashboard.html',
                           total_students=total_students,
                           total_courses=total_courses,
                           total_payments=float(total_payments),
                           total_active_cashiers=total_active_cashiers,
                           recent_activities=recent_activities,
                           recent_payments=recent_payments,
                           fully_paid=fully_paid,
                           partially_paid=partially_paid,
                           unpaid=unpaid,
                           fully_paid_percent=fully_paid_percent,
                           partially_paid_percent=partially_paid_percent,
                           unpaid_percent=unpaid_percent,
                           monthly_revenue_data=monthly_revenue_data,
                           top_courses=top_courses)


@admin_bp.route('/students')
@login_required
@admin_required
def students():
    connection = get_db_connection()
    """Main students management page with search, filter, and pagination"""
    try:
        with connection.cursor() as cursor:
            # Get all active courses for filter dropdown
            cursor.execute("SELECT id, name, price FROM courses WHERE is_active = TRUE ORDER BY name")
            courses = cursor.fetchall()

            # Build query with filters
            where_conditions = []
            params = []

            # Status filter (active/inactive students)
            status_filter = request.args.get('student_status_filter', 'active').strip()
            if status_filter == 'active':
                where_conditions.append("s.is_active = TRUE")
            elif status_filter == 'inactive':
                where_conditions.append("s.is_active = FALSE")
            # 'all' shows both active and inactive

            # Search functionality
            search = request.args.get('search', '').strip()
            if search:
                where_conditions.append("""
                    (s.student_id LIKE %s OR 
                     CONCAT(s.first_name, ' ', s.last_name) LIKE %s OR 
                     s.email LIKE %s)
                """)
                search_param = f"%{search}%"
                params.extend([search_param, search_param, search_param])

            # Course filter
            course_filter = request.args.get('course_filter', '').strip()
            if course_filter:
                where_conditions.append("s.course_id = %s")
                params.append(course_filter)

            # Payment status filter
            payment_status_filter = request.args.get('payment_status_filter', '').strip()

            # Base query with JOINs
            where_clause = "WHERE " + " AND ".join(where_conditions) if where_conditions else ""

            # Create the main query
            main_query = f"""
                SELECT 
                    s.id,
                    s.student_id,
                    s.first_name,
                    s.last_name,
                    s.email,
                    s.phone,
                    s.address,
                    s.course_id,
                    s.enrollment_date,
                    s.is_active,
                    c.name as course_name,
                    c.price as course_price,
                    COALESCE(SUM(p.amount_paid), 0) as total_paid
                FROM students s
                LEFT JOIN courses c ON s.course_id = c.id
                LEFT JOIN payments p ON s.id = p.student_id
                {where_clause}
                GROUP BY s.id, s.student_id, s.first_name, s.last_name, s.email, 
                         s.phone, s.address, s.course_id, s.enrollment_date, s.is_active,
                         c.name, c.price
            """

            # Add payment status filter using HAVING clause
            having_clause = ""
            if payment_status_filter:
                if payment_status_filter == 'paid':
                    having_clause = " HAVING (c.price - COALESCE(SUM(p.amount_paid), 0)) = 0 AND c.price > 0"
                elif payment_status_filter == 'partial':
                    having_clause = " HAVING COALESCE(SUM(p.amount_paid), 0) > 0 AND (c.price - COALESCE(SUM(p.amount_paid), 0)) > 0"
                elif payment_status_filter == 'unpaid':
                    having_clause = " HAVING COALESCE(SUM(p.amount_paid), 0) = 0"

            # Complete query with having clause
            complete_query = main_query + having_clause

            # Get total count for pagination (wrap the complete query in a subquery)
            count_query = f"SELECT COUNT(*) as total FROM ({complete_query}) as filtered_students"
            cursor.execute(count_query, params)
            total_students = cursor.fetchone()['total']

            # Pagination setup
            page = request.args.get('page', 1, type=int)
            per_page = 10
            offset = (page - 1) * per_page

            # Get paginated results
            paginated_query = f"{complete_query} ORDER BY s.created_at DESC LIMIT %s OFFSET %s"
            cursor.execute(paginated_query, params + [per_page, offset])
            students = cursor.fetchall()

            # Get statistics for all students (not filtered)
            stats_query = """
                SELECT 
                    COUNT(CASE WHEN s.is_active = TRUE THEN 1 END) as active_students,
                    COUNT(CASE WHEN s.is_active = FALSE THEN 1 END) as inactive_students,
                    COUNT(*) as total_students,
                    COALESCE(SUM(CASE WHEN s.is_active = TRUE THEN c.price END), 0) as total_fees,
                    COALESCE(SUM(CASE WHEN s.is_active = TRUE THEN p.total_paid END), 0) as total_collected
                FROM students s
                LEFT JOIN courses c ON s.course_id = c.id
                LEFT JOIN (
                    SELECT student_id, SUM(amount_paid) as total_paid
                    FROM payments
                    GROUP BY student_id
                ) p ON s.id = p.student_id
            """
            cursor.execute(stats_query)
            statistics = cursor.fetchone()

            # Calculate additional stats
            outstanding_balance = statistics['total_fees'] - statistics['total_collected']
            collection_rate = (statistics['total_collected'] / statistics['total_fees'] * 100) if statistics[
                                                                                                      'total_fees'] > 0 else 0

            # Create pagination object
            class Pagination:
                def __init__(self, page, per_page, total):
                    self.page = page
                    self.per_page = per_page
                    self.total = total
                    self.pages = max(1, (total + per_page - 1) // per_page)  # Ensure at least 1 page
                    self.has_prev = page > 1 and total > 0
                    self.has_next = page < self.pages and total > 0
                    self.prev_num = page - 1 if self.has_prev else None
                    self.next_num = page + 1 if self.has_next else None

                def iter_pages(self, left_edge=2, left_current=2, right_current=3, right_edge=2):
                    """Generate page numbers for pagination display"""
                    if self.pages <= 1:
                        return

                    last = self.pages
                    for num in range(1, last + 1):
                        if num <= left_edge or \
                                (self.page - left_current - 1 < num < self.page + right_current) or \
                                num > last - right_edge:
                            yield num
                        elif num == left_edge + 1 or num == last - right_edge:
                            yield None  # This creates the "..." gaps

            # Create pagination instance
            pagination = Pagination(page, per_page, total_students) if total_students > 0 else None

            return render_template('admin/manage_students.html',
                                   students=students,
                                   courses=courses,
                                   pagination=pagination,
                                   statistics={
                                       'active_students': statistics['active_students'],
                                       'inactive_students': statistics['inactive_students'],
                                       'total_students': statistics['total_students'],
                                       'total_fees': statistics['total_fees'],
                                       'total_collected': statistics['total_collected'],
                                       'outstanding_balance': outstanding_balance,
                                       'collection_rate': collection_rate
                                   })

    except Exception as e:
        flash(f'Error loading students: {str(e)}', 'error')
        return render_template('admin/manage_students.html',
                               students=[],
                               courses=[],
                               pagination=None,
                               statistics={
                                   'active_students': 0,
                                   'inactive_students': 0,
                                   'total_students': 0,
                                   'total_fees': 0,
                                   'total_collected': 0,
                                   'outstanding_balance': 0,
                                   'collection_rate': 0
                               })
    finally:
        connection.close()


@admin_bp.route('/student/add', methods=['POST'])
@login_required
@admin_required
def add_student():
    student_id = request.form.get('student_id')
    first_name = request.form.get('first_name')
    last_name = request.form.get('last_name')
    email = request.form.get('email')
    phone = request.form.get('phone')
    address = request.form.get('address')
    course_id = request.form.get('course_id')
    enrollment_date = request.form.get('enrollment_date')

    if not all([student_id, first_name, last_name, email, phone, address, enrollment_date, course_id]):
        flash('Please fill in all required fields.', 'error')
        return redirect(url_for('admin.students'))

#     connection = User.get_db_connection()
    connection = get_db_connection()
    """Add new student"""
    try:
        # connection = User.get_db_connection()
        with connection.cursor() as cursor:
            # Validate required fields
            required_fields = ['student_id', 'first_name', 'last_name', 'email', 'course_id', 'enrollment_date']
            for field in required_fields:
                if not request.form.get(field):
                    flash(f'{field.replace("_", " ").title()} is required', 'error')
                    return redirect(url_for('admin.students'))

            # Check if student ID already exists
            cursor.execute("SELECT id FROM students WHERE student_id = %s", (request.form['student_id'],))
            if cursor.fetchone():
                flash('Student ID already exists', 'error')
                return redirect(url_for('admin.students'))

            # Check if email already exists
            cursor.execute("SELECT id FROM students WHERE email = %s", (request.form['email'],))
            if cursor.fetchone():
                flash('Email already exists', 'error')
                return redirect(url_for('admin.students'))

            # Validate email format
            email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
            if not re.match(email_pattern, request.form['email']):
                flash('Invalid email format', 'error')
                return redirect(url_for('admin.students'))

            # Verify course exists
            cursor.execute("SELECT id FROM courses WHERE id = %s AND is_active = TRUE", (request.form['course_id'],))
            if not cursor.fetchone():
                flash('Invalid course selected', 'error')
                return redirect(url_for('admin.students'))

            # Insert new student
            insert_query = """
                INSERT INTO students (student_id, first_name, last_name, email, phone, 
                                    address, course_id, enrollment_date)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """
            cursor.execute(insert_query, (
                request.form['student_id'],
                request.form['first_name'],
                request.form['last_name'],
                request.form['email'],
                request.form.get('phone', ''),
                request.form.get('address', ''),
                request.form['course_id'],
                request.form['enrollment_date']
            ))

            connection.commit()
            flash('Student added successfully!', 'success')

    except Exception as e:
        flash(f'Error adding student: {str(e)}', 'error')
    finally:
        connection.close()

    return redirect(url_for('admin.students'))


@admin_bp.route('/student/<int:student_id>/edit')
@login_required
@admin_required
def get_student_edit_data(student_id):
    """Get student data for editing (AJAX endpoint)"""
    try:
        connection = get_db_connection()
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT id, student_id, first_name, last_name, email, phone, 
                       address, course_id, enrollment_date
                FROM students 
                WHERE id = %s AND is_active = TRUE
            """, (student_id,))

            student = cursor.fetchone()
            if not student:
                return jsonify({'success': False, 'message': 'Student not found'})

            # Convert date to string format for HTML input
            if student['enrollment_date']:
                student['enrollment_date'] = student['enrollment_date'].strftime('%Y-%m-%d')

            return jsonify({'success': True, 'student': student})

    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})
    finally:
        connection.close()


@admin_bp.route('/student/<int:student_id>/update', methods=['POST'])
@login_required
@admin_required
def update_student(student_id):
    """Update student information"""
    try:
        connection = get_db_connection()
        with connection.cursor() as cursor:
            # Check if student exists
            cursor.execute("SELECT id FROM students WHERE id = %s AND is_active = TRUE", (student_id,))
            if not cursor.fetchone():
                flash('Student not found', 'error')
                return redirect(url_for('admin.students'))

            # Validate required fields
            required_fields = ['first_name', 'last_name', 'email', 'course_id', 'enrollment_date']
            for field in required_fields:
                if not request.form.get(field):
                    flash(f'{field.replace("_", " ").title()} is required', 'error')
                    return redirect(url_for('admin.students'))

            # Check if email exists for other students
            cursor.execute("SELECT id FROM students WHERE email = %s AND id != %s",
                           (request.form['email'], student_id))
            if cursor.fetchone():
                flash('Email already exists for another student', 'error')
                return redirect(url_for('admin.students'))

            # Validate email format
            email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
            if not re.match(email_pattern, request.form['email']):
                flash('Invalid email format', 'error')
                return redirect(url_for('admin.students'))

            # Verify course exists
            cursor.execute("SELECT id FROM courses WHERE id = %s AND is_active = TRUE", (request.form['course_id'],))
            if not cursor.fetchone():
                flash('Invalid course selected', 'error')
                return redirect(url_for('admin.students'))

            # Update student
            update_query = """
                UPDATE students 
                SET first_name = %s, last_name = %s, email = %s, phone = %s,
                    address = %s, course_id = %s, enrollment_date = %s, updated_at = NOW()
                WHERE id = %s
            """
            cursor.execute(update_query, (
                request.form['first_name'],
                request.form['last_name'],
                request.form['email'],
                request.form.get('phone', ''),
                request.form.get('address', ''),
                request.form['course_id'],
                request.form['enrollment_date'],
                student_id
            ))

            connection.commit()
            flash('Student updated successfully!', 'success')

    except Exception as e:
        flash(f'Error updating student: {str(e)}', 'error')
    finally:
        connection.close()

    return redirect(url_for('admin.students'))


@admin_bp.route('/student/<int:student_id>/deactivate', methods=['POST'])
@login_required
@admin_required
def deactivate_student(student_id):
    """Deactivate student (soft delete)"""
    try:
        connection = get_db_connection()
        with connection.cursor() as cursor:
            # Check if student exists and is active
            cursor.execute("SELECT id FROM students WHERE id = %s AND is_active = TRUE", (student_id,))
            if not cursor.fetchone():
                return jsonify({'success': False, 'message': 'Student not found or already inactive'})

            # Deactivate student
            cursor.execute("UPDATE students SET is_active = FALSE, updated_at = NOW() WHERE id = %s", (student_id,))
            connection.commit()

            return jsonify({'success': True, 'message': 'Student deactivated successfully'})

    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})
    finally:
        connection.close()


@admin_bp.route('/student/<int:student_id>/activate', methods=['POST'])
@login_required
@admin_required
def activate_student(student_id):
    """Activate student"""
    try:
        connection = get_db_connection()
        with connection.cursor() as cursor:
            # Check if student exists and is inactive
            cursor.execute("SELECT id FROM students WHERE id = %s AND is_active = FALSE", (student_id,))
            if not cursor.fetchone():
                return jsonify({'success': False, 'message': 'Student not found or already active'})

            # Activate student
            cursor.execute("UPDATE students SET is_active = TRUE, updated_at = NOW() WHERE id = %s", (student_id,))
            connection.commit()

            return jsonify({'success': True, 'message': 'Student activated successfully'})

    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})
    finally:
        connection.close()

# Helper function to generate next student ID
def generate_student_id():
    """Generate next student ID in format STU-YYYY-#####"""
    try:
        connection = get_db_connection()
        with connection.cursor() as cursor:
            current_year = datetime.now().year

            # Get the last student ID for current year
            cursor.execute("""
                SELECT student_id FROM students 
                WHERE student_id LIKE %s 
                ORDER BY student_id DESC LIMIT 1
            """, (f'STU-{current_year}-%',))

            result = cursor.fetchone()
            if result:
                # Extract number and increment
                last_id = result['student_id']
                number_part = int(last_id.split('-')[2])
                next_number = number_part + 1
            else:
                next_number = 1

            return f'STU-{current_year}-{next_number:05d}'

    except Exception:
        # Fallback to basic format
        return f'STU-{datetime.now().year}-00001'
    finally:
        connection.close()


@admin_bp.route('/student/generate-id')
@login_required
@admin_required
def generate_next_student_id():
    """API endpoint to generate next student ID"""
    return jsonify({'student_id': generate_student_id()})


@admin_bp.route('/courses')
@login_required
@admin_required
def courses():
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            # Get all courses (both active and inactive)
            cursor.execute("SELECT * FROM courses ORDER BY is_active DESC, name")
            courses = cursor.fetchall()

            # Get statistics (only active courses and students)
            # Total enrolled students
            cursor.execute("""
                SELECT COUNT(*) as total_students 
                FROM students s 
                JOIN courses c ON s.course_id = c.id 
                WHERE s.is_active = TRUE AND c.is_active = TRUE
            """)
            total_students = cursor.fetchone()['total_students']

            # Total revenue from payments
            cursor.execute("""
                SELECT COALESCE(SUM(p.amount_paid), 0) as total_revenue 
                FROM payments p 
                JOIN students s ON p.student_id = s.id 
                JOIN courses c ON s.course_id = c.id 
                WHERE s.is_active = TRUE AND c.is_active = TRUE
            """)
            total_revenue = cursor.fetchone()['total_revenue']

            # Average students per course (only active courses)
            cursor.execute("""
                SELECT AVG(student_count) as avg_students 
                FROM (
                    SELECT COUNT(s.id) as student_count 
                    FROM courses c 
                    LEFT JOIN students s ON c.id = s.course_id AND s.is_active = TRUE 
                    WHERE c.is_active = TRUE 
                    GROUP BY c.id
                ) as course_counts
            """)
            avg_students_result = cursor.fetchone()
            avg_students = round(avg_students_result['avg_students'] or 0, 1)

            # Get student count per course
            cursor.execute("""
                SELECT c.id, COUNT(s.id) as student_count 
                FROM courses c 
                LEFT JOIN students s ON c.id = s.course_id AND s.is_active = TRUE 
                WHERE c.is_active = TRUE 
                GROUP BY c.id
            """)
            course_students = {row['id']: row['student_count'] for row in cursor.fetchall()}

    finally:
        connection.close()

    return render_template('admin/manage_courses.html',
                           courses=courses,
                           total_students=total_students,
                           total_revenue=total_revenue,
                           avg_students=avg_students,
                           course_students=course_students)


@admin_bp.route('/courses/add', methods=['POST'])
@login_required
@admin_required
def add_course():
    name = request.form.get('name')
    price = request.form.get('price')
    description = request.form.get('description')

    if not all([name, price]):
        flash('Please fill in all required fields.', 'error')
        return redirect(url_for('admin.courses'))

    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute('''
                INSERT INTO courses (name, price, description)
                VALUES (%s, %s, %s)
            ''', (name, price, description))
            connection.commit()

            flash('Course added successfully.', 'success')

    except pymysql.IntegrityError:
        flash('Course name already exists.', 'error')
    except Exception as e:
        flash('An error occurred while adding the course.', 'error')
    finally:
        connection.close()

    return redirect(url_for('admin.courses'))


@admin_bp.route('/courses/edit/<int:course_id>', methods=['POST'])
@login_required
@admin_required
def edit_course(course_id):
    name = request.form.get('name')
    price = request.form.get('price')
    description = request.form.get('description')

    if not all([name, price]):
        flash('Please fill in all required fields.', 'error')
        return redirect(url_for('admin.courses'))

    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            # Check if course exists
            cursor.execute("SELECT * FROM courses WHERE id = %s AND is_active = TRUE", (course_id,))
            course = cursor.fetchone()

            if not course:
                flash('Course not found.', 'error')
                return redirect(url_for('admin.courses'))

            # Update course
            cursor.execute('''
                UPDATE courses 
                SET name = %s, price = %s, description = %s, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
            ''', (name, price, description, course_id))
            connection.commit()

            flash('Course updated successfully.', 'success')

    except pymysql.IntegrityError:
        flash('Course name already exists.', 'error')
    except Exception as e:
        flash('An error occurred while updating the course.', 'error')
    finally:
        connection.close()

    return redirect(url_for('admin.courses'))


@admin_bp.route('/courses/activate/<int:course_id>', methods=['POST'])
@login_required
@admin_required
def activate_course(course_id):
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            # Check if course exists
            cursor.execute("SELECT * FROM courses WHERE id = %s", (course_id,))
            course = cursor.fetchone()

            if not course:
                flash('Course not found.', 'error')
                return redirect(url_for('admin.courses'))

            # Activate the course
            cursor.execute('''
                UPDATE courses 
                SET is_active = TRUE, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
            ''', (course_id,))
            connection.commit()

            flash(f'Course "{course["name"]}" has been activated successfully.', 'success')

    except Exception as e:
        flash('An error occurred while activating the course.', 'error')
    finally:
        connection.close()

    return redirect(url_for('admin.courses'))


@admin_bp.route('/courses/deactivate/<int:course_id>', methods=['POST'])
@login_required
@admin_required
def deactivate_course(course_id):
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            # Check if course exists
            cursor.execute("SELECT * FROM courses WHERE id = %s", (course_id,))
            course = cursor.fetchone()

            if not course:
                flash('Course not found.', 'error')
                return redirect(url_for('admin.courses'))

            # Check if course has active enrolled students
            cursor.execute("SELECT COUNT(*) as student_count FROM students WHERE course_id = %s AND is_active = TRUE",
                           (course_id,))
            student_count = cursor.fetchone()['student_count']

            if student_count > 0:
                flash(f'Cannot deactivate course. It has {student_count} active enrolled student(s). Please handle these students first.', 'warning')
                return redirect(url_for('admin.courses'))

            # Deactivate the course
            cursor.execute('''
                UPDATE courses 
                SET is_active = FALSE, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
            ''', (course_id,))
            connection.commit()

            flash(f'Course "{course["name"]}" has been deactivated successfully.', 'success')

    except Exception as e:
        flash('An error occurred while deactivating the course.', 'error')
    finally:
        connection.close()

    return redirect(url_for('admin.courses'))


@admin_bp.route('/courses/delete/<int:course_id>', methods=['POST'])
@login_required
@admin_required
def delete_course(course_id):
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            # Check if course exists
            cursor.execute("SELECT * FROM courses WHERE id = %s", (course_id,))
            course = cursor.fetchone()

            if not course:
                flash('Course not found.', 'error')
                return redirect(url_for('admin.courses'))

            # Check if course has any students (both active and inactive)
            cursor.execute("SELECT COUNT(*) as student_count FROM students WHERE course_id = %s",
                           (course_id,))
            student_count = cursor.fetchone()['student_count']

            if student_count > 0:
                flash(f'Cannot permanently delete course. It has {student_count} student record(s). Consider deactivating instead.', 'error')
                return redirect(url_for('admin.courses'))

            # Check if course has any payment records
            cursor.execute("""
                SELECT COUNT(*) as payment_count 
                FROM payments p 
                JOIN students s ON p.student_id = s.id 
                WHERE s.course_id = %s
            """, (course_id,))
            payment_count = cursor.fetchone()['payment_count']

            if payment_count > 0:
                flash(f'Cannot permanently delete course. It has {payment_count} payment record(s). Consider deactivating instead.', 'error')
                return redirect(url_for('admin.courses'))

            # Permanently delete the course (only if no students or payments)
            cursor.execute('DELETE FROM courses WHERE id = %s', (course_id,))
            connection.commit()

            flash(f'Course "{course["name"]}" has been permanently deleted.', 'success')

    except Exception as e:
        flash('An error occurred while deleting the course.', 'error')
    finally:
        connection.close()

    return redirect(url_for('admin.courses'))


@admin_bp.route('/courses/<int:course_id>/details', methods=['GET'])
@login_required
@admin_required
def get_course_details(course_id):
    """API endpoint to get course details for editing"""
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM courses WHERE id = %s", (course_id,))
            course = cursor.fetchone()

            if not course:
                return jsonify({'error': 'Course not found'}), 404

            return jsonify({
                'id': course['id'],
                'name': course['name'],
                'price': float(course['price']),
                'description': course['description'] or '',
                'is_active': course['is_active']
            })
    except Exception as e:
        return jsonify({'error': 'An error occurred while fetching course details'}), 500
    finally:
        connection.close()


@admin_bp.route('/courses/<int:course_id>/students', methods=['GET'])
@login_required
@admin_required
def get_course_students(course_id):
    """API endpoint to get students enrolled in a course"""
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT s.*, c.name as course_name,
                       COALESCE(SUM(p.amount_paid), 0) as total_paid,
                       c.price as course_price,
                       CASE 
                           WHEN COALESCE(SUM(p.amount_paid), 0) >= c.price THEN 'Paid'
                           WHEN COALESCE(SUM(p.amount_paid), 0) > 0 THEN 'Partial'
                           ELSE 'Unpaid'
                       END as payment_status
                FROM students s 
                JOIN courses c ON s.course_id = c.id 
                LEFT JOIN payments p ON s.id = p.student_id
                WHERE s.course_id = %s AND s.is_active = TRUE 
                GROUP BY s.id
                ORDER BY s.last_name, s.first_name
            """, (course_id,))
            students = cursor.fetchall()

            cursor.execute("SELECT name FROM courses WHERE id = %s", (course_id,))
            course = cursor.fetchone()

            return jsonify({
                'course_name': course['name'] if course else 'Unknown Course',
                'students': students
            })
    except Exception as e:
        return jsonify({'error': 'An error occurred while fetching student data'}), 500
    finally:
        connection.close()


import secrets
import string
import re
from flask import request, redirect, url_for, flash, render_template
from utils.email_utils import send_login_credentials_email


@admin_bp.route('/cashiers')
@login_required
@admin_required
def cashiers():
    """Display all cashiers with proper data from database"""
    cashiers = User.get_all_cashiers()
    return render_template('admin/manage_cashiers.html', cashiers=cashiers)


def generate_temporary_password(length=12):
    """Generate a secure temporary password"""
    # Use a mix of letters, digits, and special characters
    characters = string.ascii_letters + string.digits + "!@#$%&*"
    # Ensure at least one character from each category
    password = [
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.digits),
        secrets.choice("!@#$%&*")
    ]

    # Fill the rest randomly
    for _ in range(length - 4):
        password.append(secrets.choice(characters))

    # Shuffle the password list
    secrets.SystemRandom().shuffle(password)
    return ''.join(password)


@admin_bp.route('/cashiers/add', methods=['POST'])
@login_required
@admin_required
def add_cashier():
    """Add new cashier with validation and email notification"""
    name = request.form.get('name', '').strip()
    email = request.form.get('email', '').strip().lower()
    send_email = request.form.get('send_email') == 'on'

    # Validation
    if not all([name, email]):
        flash('Please fill in all required fields.', 'error')
        return redirect(url_for('admin.cashiers'))

    # Validate email format
    email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    if not re.match(email_pattern, email):
        flash('Please enter a valid email address.', 'error')
        return redirect(url_for('admin.cashiers'))

    # Generate temporary password
    temporary_password = generate_temporary_password()

    try:
        # Create user account
        user_id = User.create(name, email, temporary_password, 'cashier')

        if user_id:
            # Send email with login credentials if requested
            if send_email:
                email_sent = send_login_credentials_email(name, email, temporary_password)
                if email_sent:
                    flash(f'Cashier "{name}" added successfully! Login credentials have been sent to {email}.',
                          'success')
                else:
                    flash(
                        f'Cashier "{name}" added successfully, but failed to send email. Please manually provide the temporary password: {temporary_password}',
                        'warning')
            else:
                flash(f'Cashier "{name}" added successfully! Temporary password: {temporary_password}', 'success')
        else:
            flash('Error creating cashier account. Please try again.', 'error')

    except Exception as e:
        if 'Duplicate entry' in str(e) or 'email already exists' in str(e).lower():
            flash('Email address already exists. Please use a different email.', 'error')
        else:
            flash('Error adding cashier. Please try again.', 'error')
            print(f"Error creating cashier: {e}")

    return redirect(url_for('admin.cashiers'))


@admin_bp.route('/cashiers/edit/<int:cashier_id>', methods=['POST'])
@login_required
@admin_required
def edit_cashier(cashier_id):
    """Edit existing cashier"""
    name = request.form.get('name', '').strip()
    email = request.form.get('email', '').strip().lower()

    # Validation
    if not all([name, email]):
        flash('Please fill in all required fields.', 'error')
        return redirect(url_for('admin.cashiers'))

    # Validate email format
    email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    if not re.match(email_pattern, email):
        flash('Please enter a valid email address.', 'error')
        return redirect(url_for('admin.cashiers'))

    try:
        if User.update_cashier(cashier_id, name, email):
            flash('Cashier updated successfully.', 'success')
        else:
            flash('Error updating cashier.', 'error')
    except Exception as e:
        if 'Duplicate entry' in str(e):
            flash('Email address already exists. Please use a different email.', 'error')
        else:
            flash('Error updating cashier. Please try again.', 'error')
            print(f"Error updating cashier: {e}")

    return redirect(url_for('admin.cashiers'))


@admin_bp.route('/cashiers/resend-credentials/<int:cashier_id>')
@login_required
@admin_required
def resend_credentials(cashier_id):
    """Generate new temporary password and resend credentials to cashier"""
    try:
        # Get cashier details
        cashier = User.get_by_id(cashier_id)
        if not cashier:
            flash('Cashier not found.', 'error')
            return redirect(url_for('admin.cashiers'))

        if cashier.role != 'cashier':
            flash('Invalid user type.', 'error')
            return redirect(url_for('admin.cashiers'))

        # Generate new temporary password
        new_temporary_password = generate_temporary_password()

        # Update password in database
        if User.update_password_by_id(cashier_id, new_temporary_password):
            # Send email with new credentials
            email_sent = send_login_credentials_email(cashier.name, cashier.email, new_temporary_password)

            if email_sent:
                flash(f'New login credentials have been sent to {cashier.email}.', 'success')
            else:
                flash(f'Failed to send email. New temporary password for {cashier.name}: {new_temporary_password}',
                      'warning')
        else:
            flash('Error generating new credentials. Please try again.', 'error')

    except Exception as e:
        flash('Error resending credentials. Please try again.', 'error')
        print(f"Error resending credentials: {e}")

    return redirect(url_for('admin.cashiers'))


@admin_bp.route('/cashiers/toggle/<int:cashier_id>')
@login_required
@admin_required
def toggle_cashier(cashier_id):
    """Toggle cashier active status"""
    try:
        if User.toggle_active(cashier_id):
            cashier = User.get_by_id(cashier_id)
            status = "activated" if cashier.is_active else "deactivated"
            flash(f'Cashier {status} successfully.', 'success')
        else:
            flash('Error updating cashier status.', 'error')
    except Exception as e:
        flash('Error updating cashier status.', 'error')
        print(f"Error toggling cashier status: {e}")

    return redirect(url_for('admin.cashiers'))


@admin_bp.route('/cashiers/delete/<int:cashier_id>')
@login_required
@admin_required
def delete_cashier(cashier_id):
    """Delete cashier (optional route if you want delete functionality)"""
    try:
        cashier = User.get_by_id(cashier_id)
        if not cashier:
            flash('Cashier not found.', 'error')
            return redirect(url_for('admin.cashiers'))

        # Check if cashier has any payment records
        if User.has_payment_records(cashier_id):
            flash('Cannot delete cashier with payment records. Deactivate instead.', 'error')
            return redirect(url_for('admin.cashiers'))

        if User.delete(cashier_id):
            flash('Cashier deleted successfully.', 'success')
        else:
            flash('Error deleting cashier.', 'error')
    except Exception as e:
        flash('Error deleting cashier.', 'error')
        print(f"Error deleting cashier: {e}")

    return redirect(url_for('admin.cashiers'))


@admin_bp.route('/logs')
@login_required
@admin_required
def logs():
    """Display system logs with pagination"""
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            # Build query with filters
            where_conditions = []
            params = []

            # Search functionality
            search = request.args.get('search', '').strip()
            if search:
                where_conditions.append("""
                    (u.name LIKE %s OR 
                     l.action LIKE %s OR 
                     l.role LIKE %s)
                """)
                search_param = f"%{search}%"
                params.extend([search_param, search_param, search_param])

            # Role filter
            role_filter = request.args.get('role_filter', '').strip()
            if role_filter:
                where_conditions.append("l.role = %s")
                params.append(role_filter)

            # Action filter
            action_filter = request.args.get('action_filter', '').strip()
            if action_filter:
                if action_filter == 'login':
                    where_conditions.append("l.action LIKE %s")
                    params.append("%login%")
                elif action_filter == 'logout':
                    where_conditions.append("l.action LIKE %s")
                    params.append("%logout%")
                else:
                    where_conditions.append("l.action = %s")
                    params.append(action_filter)

            # Date filter
            date_filter = request.args.get('date_filter', '').strip()
            if date_filter:
                if date_filter == 'today':
                    where_conditions.append("DATE(l.created_at) = CURDATE()")
                elif date_filter == 'yesterday':
                    where_conditions.append("DATE(l.created_at) = DATE_SUB(CURDATE(), INTERVAL 1 DAY)")
                elif date_filter == 'week':
                    where_conditions.append("l.created_at >= DATE_SUB(NOW(), INTERVAL 7 DAY)")
                elif date_filter == 'month':
                    where_conditions.append("l.created_at >= DATE_SUB(NOW(), INTERVAL 30 DAY)")

            # Build WHERE clause
            where_clause = ""
            if where_conditions:
                where_clause = "WHERE " + " AND ".join(where_conditions)

            # Pagination setup
            page = request.args.get('page', 1, type=int)
            per_page = 10
            offset = (page - 1) * per_page

            # Get total count for pagination
            count_query = f"SELECT COUNT(*) as total FROM logs l LEFT JOIN users u ON l.user_id = u.id {where_clause}"
            cursor.execute(count_query, params)
            total_logs = cursor.fetchone()['total']

            # Get paginated logs
            logs_query = f"""
                SELECT 
                    l.id,
                    l.user_id,
                    l.action,
                    l.role,
                    l.created_at,
                    u.name as user_name
                FROM logs l
                LEFT JOIN users u ON l.user_id = u.id
                {where_clause}
                ORDER BY l.created_at DESC
                LIMIT %s OFFSET %s
            """
            cursor.execute(logs_query, params + [per_page, offset])
            logs = cursor.fetchall()

            # Get available roles for filter dropdown
            roles_query = "SELECT DISTINCT role FROM logs WHERE role IS NOT NULL AND role != '' ORDER BY role"
            cursor.execute(roles_query)
            available_roles = [row['role'] for row in cursor.fetchall()]

            # Get statistics (for all logs, not filtered)
            stats_query = """
                SELECT 
                    COUNT(*) as total_logs,
                    COUNT(CASE WHEN DATE(l.created_at) = CURDATE() THEN 1 END) as today_count,
                    COUNT(CASE WHEN l.created_at >= DATE_SUB(NOW(), INTERVAL 1 HOUR) THEN 1 END) as recent_count,
                    COUNT(DISTINCT l.user_id) as unique_users
                FROM logs l
            """
            cursor.execute(stats_query)
            stats = cursor.fetchone()

            # Create pagination object (same as manage students)
            class Pagination:
                def __init__(self, page, per_page, total):
                    self.page = page
                    self.per_page = per_page
                    self.total = total
                    self.pages = max(1, (total + per_page - 1) // per_page)
                    self.has_prev = page > 1 and total > 0
                    self.has_next = page < self.pages and total > 0
                    self.prev_num = page - 1 if self.has_prev else None
                    self.next_num = page + 1 if self.has_next else None

                def iter_pages(self, left_edge=2, left_current=2, right_current=3, right_edge=2):
                    """Generate page numbers for pagination display"""
                    if self.pages <= 1:
                        return

                    last = self.pages
                    for num in range(1, last + 1):
                        if num <= left_edge or \
                                (self.page - left_current - 1 < num < self.page + right_current) or \
                                num > last - right_edge:
                            yield num
                        elif num == left_edge + 1 or num == last - right_edge:
                            yield None  # This creates the "..." gaps

            # Create pagination instance
            pagination = Pagination(page, per_page, total_logs) if total_logs > 0 else None

            return render_template('admin/logs.html',
                                   logs=logs,
                                   pagination=pagination,
                                   available_roles=available_roles,
                                   today_count=stats['today_count'],
                                   unique_users=stats['unique_users'],
                                   recent_count=stats['recent_count'],
                                   now=datetime.now())

    except Exception as e:
        flash(f'Error loading logs: {str(e)}', 'error')
        return render_template('admin/logs.html',
                               logs=[],
                               pagination=None,
                               available_roles=[],
                               today_count=0,
                               unique_users=0,
                               recent_count=0,
                               now=datetime.now())
    finally:
        connection.close()


@admin_bp.route('/logs/clear', methods=['POST'])
@login_required
@admin_required
def clear_old_logs():
    """Clear logs older than 90 days"""
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            delete_query = "DELETE FROM logs WHERE created_at < DATE_SUB(NOW(), INTERVAL 90 DAY)"
            cursor.execute(delete_query)
            deleted_count = cursor.rowcount
            connection.commit()

            return jsonify({
                'success': True,
                'message': f'Successfully cleared {deleted_count} old log entries'
            })
    except Exception as e:
        connection.rollback()
        return jsonify({
            'success': False,
            'message': 'Error clearing old logs'
        }), 500
    finally:
        connection.close()


@admin_bp.route('/profile')
@login_required
@admin_required
def profile():
    """Display admin profile page"""
    try:
        connection = get_db_connection()
        with connection.cursor() as cursor:
            # Get current user data
            cursor.execute("SELECT * FROM users WHERE id = %s", (current_user.id,))
            current_user_data = cursor.fetchone()

            if not current_user_data:
                flash('User not found', 'error')
                return redirect(url_for('admin.dashboard'))

            # Get system stats for the sidebar
            cursor.execute("SELECT COUNT(*) as count FROM students WHERE is_active = TRUE")
            total_students = cursor.fetchone()['count']

            cursor.execute("SELECT COUNT(*) as count FROM courses WHERE is_active = TRUE")
            total_courses = cursor.fetchone()['count']

            cursor.execute("SELECT COUNT(*) as count FROM users WHERE is_active = TRUE")
            total_users = cursor.fetchone()['count']

            cursor.execute("""
                SELECT COUNT(*) as count FROM payments 
                WHERE payment_date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
            """)
            recent_payments = cursor.fetchone()['count']

            cursor.execute("SELECT COUNT(*) as count FROM logs")
            total_logs = cursor.fetchone()['count']

            stats = {
                'total_students': total_students,
                'total_courses': total_courses,
                'total_users': total_users,
                'recent_payments': recent_payments,
                'total_logs': total_logs
            }

            return render_template('admin/profile.html',
                                   current_user=current_user_data,
                                   stats=stats)

    except Exception as e:
        flash(f'Error loading profile: {str(e)}', 'error')
        return redirect(url_for('admin.dashboard'))
    finally:
        connection.close()


@admin_bp.route('/profile/update', methods=['POST'])
@login_required
@admin_required
def update_profile():
    """Update admin profile"""
    try:
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip()

        if not name or not email:
            flash('Name and email are required', 'error')
            return redirect(url_for('admin.profile'))

        # Validate email format
        email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        if not re.match(email_pattern, email):
            flash('Please enter a valid email address', 'error')
            return redirect(url_for('admin.profile'))

        connection = get_db_connection()
        try:
            with connection.cursor() as cursor:
                # Check if email is already taken by another user
                cursor.execute("""
                    SELECT id FROM users WHERE email = %s AND id != %s
                """, (email, current_user.id))

                if cursor.fetchone():
                    flash('Email is already taken by another user', 'error')
                    return redirect(url_for('admin.profile'))

                # Update user profile
                cursor.execute("""
                    UPDATE users 
                    SET name = %s, email = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (name, email, current_user.id))

                connection.commit()

                flash('Profile updated successfully!', 'success')

        finally:
            connection.close()

    except Exception as e:
        flash(f'Error updating profile: {str(e)}', 'error')

    return redirect(url_for('admin.profile'))


@admin_bp.route('/profile/change-password', methods=['POST'])
@login_required
@admin_required
def change_password():
    """Change user password"""
    try:
        current_password = request.form.get('current_password', '')
        new_password = request.form.get('new_password', '')
        confirm_password = request.form.get('confirm_password', '')

        if not all([current_password, new_password, confirm_password]):
            flash('All password fields are required', 'error')
            return redirect(url_for('admin.profile'))

        if new_password != confirm_password:
            flash('New passwords do not match', 'error')
            return redirect(url_for('admin.profile'))

        if len(new_password) < 8:
            flash('Password must be at least 8 characters long', 'error')
            return redirect(url_for('admin.profile'))

        connection = get_db_connection()
        try:
            with connection.cursor() as cursor:
                # Get current user
                cursor.execute("SELECT password_hash FROM users WHERE id = %s", (current_user.id,))
                user = cursor.fetchone()

                if not user:
                    flash('User not found', 'error')
                    return redirect(url_for('admin.profile'))

                # Verify current password (you'll need to import werkzeug.security)
                from werkzeug.security import check_password_hash, generate_password_hash

                if not check_password_hash(user['password_hash'], current_password):
                    flash('Current password is incorrect', 'error')
                    return redirect(url_for('admin.profile'))

                # Update password
                new_password_hash = generate_password_hash(new_password)
                cursor.execute("""
                    UPDATE users 
                    SET password_hash = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (new_password_hash, current_user.id))

                connection.commit()

                flash('Password changed successfully!', 'success')

        finally:
            connection.close()

    except Exception as e:
        flash(f'Error changing password: {str(e)}', 'error')

    return redirect(url_for('admin.profile'))