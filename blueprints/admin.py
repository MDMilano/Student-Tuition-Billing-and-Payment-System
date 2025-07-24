from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from functools import wraps
from models.user import User
from models.log import Log, ImportantActivityLogger
from utils.helpers import log_activity, admin_required
import pymysql
from config import Config

from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash, make_response
import pymysql
from config import Config
import csv
from io import StringIO
from datetime import datetime
import re
from datetime import datetime, timedelta

admin_bp = Blueprint('admin', __name__)


@admin_bp.route('/dashboard')
@login_required
@admin_required
def dashboard():
    connection = User.get_db_connection()
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
                SELECT l.action, l.created_at, u.name as user_name, u.role,
                       l.table_name, l.record_id
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
                    c.code as course_code,
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
    """Main students management page with search, filter, and pagination"""
    try:
        connection = User.get_db_connection()
        with connection.cursor() as cursor:
            # Get all active courses for filter dropdown
            cursor.execute("SELECT id, name, price FROM courses WHERE is_active = TRUE ORDER BY name")
            courses = cursor.fetchall()

            # Build query with filters
            where_conditions = ["s.is_active = TRUE"]
            params = []

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
            status_filter = request.args.get('status_filter', '').strip()

            # Base query with JOINs
            base_query = """
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
                WHERE {}
                GROUP BY s.id, s.student_id, s.first_name, s.last_name, s.email, 
                         s.phone, s.address, s.course_id, s.enrollment_date, s.is_active,
                         c.name, c.price
            """.format(" AND ".join(where_conditions))

            # Add payment status filter after getting the data
            if status_filter:
                if status_filter == 'paid':
                    base_query += " HAVING (c.price - COALESCE(SUM(p.amount_paid), 0)) = 0 AND c.price > 0"
                elif status_filter == 'partial':
                    base_query += " HAVING COALESCE(SUM(p.amount_paid), 0) > 0 AND (c.price - COALESCE(SUM(p.amount_paid), 0)) > 0"
                elif status_filter == 'unpaid':
                    base_query += " HAVING COALESCE(SUM(p.amount_paid), 0) = 0"

            # Add ordering
            base_query += " ORDER BY s.created_at DESC"

            # Pagination
            page = request.args.get('page', 1, type=int)
            per_page = 10
            offset = (page - 1) * per_page

            # Get total count for pagination
            count_query = f"""
                SELECT COUNT(*) as total FROM (
                    {base_query}
                ) as subquery
            """
            cursor.execute(count_query, params)
            total_students = cursor.fetchone()['total']

            # Get paginated results
            paginated_query = f"{base_query} LIMIT %s OFFSET %s"
            cursor.execute(paginated_query, params + [per_page, offset])
            students = cursor.fetchall()

            # Calculate pagination info
            total_pages = (total_students + per_page - 1) // per_page
            has_prev = page > 1
            has_next = page < total_pages

            # Create pagination object
            class Pagination:
                def __init__(self, page, per_page, total, items):
                    self.page = page
                    self.per_page = per_page
                    self.total = total
                    self.items = items
                    self.pages = total_pages
                    self.has_prev = has_prev
                    self.has_next = has_next
                    self.prev_num = page - 1 if has_prev else None
                    self.next_num = page + 1 if has_next else None

                def iter_pages(self, left_edge=2, left_current=2, right_current=3, right_edge=2):
                    last = self.pages
                    for num in range(1, last + 1):
                        if num <= left_edge or \
                                (self.page - left_current - 1 < num < self.page + right_current) or \
                                num > last - right_edge:
                            yield num

            pagination = Pagination(page, per_page, total_students, students)

            return render_template('admin/manage_students.html',
                                   students=students,
                                   courses=courses,
                                   pagination=pagination)

    except Exception as e:
        flash(f'Error loading students: {str(e)}', 'error')
        return render_template('admin/manage_students.html', students=[], courses=[])
    finally:
        connection.close()


@admin_bp.route('/student/add', methods=['POST'])
@login_required
@admin_required
def add_student():
    """Add new student with important activity logging"""
    try:
        connection = User.get_db_connection()
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
                # Log security event for duplicate attempt
                ImportantActivityLogger.log_security_event(
                    user_id=current_user.id,
                    event_type='duplicate_entry_attempt',
                    description=f"Attempted to create student with existing ID: {request.form['student_id']}",
                    severity='WARNING'
                )
                flash('Student ID already exists', 'error')
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
            student_db_id = cursor.lastrowid

            # Log important student addition
            student_data = {
                'student_id': request.form['student_id'],
                'first_name': request.form['first_name'],
                'last_name': request.form['last_name'],
                'email': request.form['email'],
                'course_id': request.form['course_id']
            }

            ImportantActivityLogger.log_student_activity(
                user_id=current_user.id,
                action='add',
                student_data=student_data,
                student_id=student_db_id
            )

            flash('Student added successfully!', 'success')

    except Exception as e:
        # Log error for failed student creation
        ImportantActivityLogger.log_security_event(
            user_id=current_user.id,
            event_type='system_error',
            description=f"Failed to create student: {str(e)}",
            severity='ERROR'
        )
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
        connection = User.get_db_connection()
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
        connection = User.get_db_connection()
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
        connection = User.get_db_connection()
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
        connection = User.get_db_connection()
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


@admin_bp.route('/student/<int:student_id>')
@login_required
@admin_required
def view_student(student_id):
    """View student details"""
    try:
        connection = User.get_db_connection()
        with connection.cursor() as cursor:
            # Get student details with course info
            cursor.execute("""
                SELECT 
                    s.id, s.student_id, s.first_name, s.last_name, s.email, s.phone,
                    s.address, s.enrollment_date, s.is_active, s.created_at,
                    c.name as course_name, c.price as course_price,
                    COALESCE(SUM(p.amount_paid), 0) as total_paid
                FROM students s
                LEFT JOIN courses c ON s.course_id = c.id
                LEFT JOIN payments p ON s.id = p.student_id
                WHERE s.id = %s
                GROUP BY s.id, s.student_id, s.first_name, s.last_name, s.email, 
                         s.phone, s.address, s.enrollment_date, s.is_active, s.created_at,
                         c.name, c.price
            """, (student_id,))

            student = cursor.fetchone()
            if not student:
                flash('Student not found', 'error')
                return redirect(url_for('admin.students'))

            # Get payment history
            cursor.execute("""
                SELECT p.id, p.amount_paid, p.payment_method, p.reference_number,
                       p.payment_date, p.notes, u.name as collected_by_name
                FROM payments p
                JOIN users u ON p.collected_by = u.id
                WHERE p.student_id = %s
                ORDER BY p.payment_date DESC, p.created_at DESC
            """, (student_id,))

            payments = cursor.fetchall()

            return render_template('admin/view_student.html', student=student, payments=payments)

    except Exception as e:
        flash(f'Error loading student: {str(e)}', 'error')
        return redirect(url_for('admin.students'))
    finally:
        connection.close()


@admin_bp.route('/students/export')
@login_required
@admin_required
def export_students():
    """Export students to CSV"""
    try:
        connection = User.get_db_connection()
        with connection.cursor() as cursor:
            # Get all students with course and payment info
            cursor.execute("""
                SELECT 
                    s.student_id, s.first_name, s.last_name, s.email, s.phone,
                    s.address, s.enrollment_date, 
                    c.name as course_name, c.price as course_price,
                    COALESCE(SUM(p.amount_paid), 0) as total_paid,
                    (c.price - COALESCE(SUM(p.amount_paid), 0)) as balance,
                    CASE 
                        WHEN COALESCE(SUM(p.amount_paid), 0) = 0 THEN 'Unpaid'
                        WHEN (c.price - COALESCE(SUM(p.amount_paid), 0)) = 0 THEN 'Fully Paid'
                        ELSE 'Partially Paid'
                    END as payment_status
                FROM students s
                LEFT JOIN courses c ON s.course_id = c.id
                LEFT JOIN payments p ON s.id = p.student_id
                WHERE s.is_active = TRUE
                GROUP BY s.id, s.student_id, s.first_name, s.last_name, s.email, 
                         s.phone, s.address, s.enrollment_date, c.name, c.price
                ORDER BY s.student_id
            """)

            students = cursor.fetchall()

            # Create CSV
            output = StringIO()
            writer = csv.writer(output)

            # Write header
            writer.writerow([
                'Student ID', 'First Name', 'Last Name', 'Email', 'Phone',
                'Address', 'Enrollment Date', 'Course', 'Course Price',
                'Total Paid', 'Balance', 'Payment Status'
            ])

            # Write data
            for student in students:
                writer.writerow([
                    student['student_id'],
                    student['first_name'],
                    student['last_name'],
                    student['email'],
                    student['phone'] or '',
                    student['address'] or '',
                    student['enrollment_date'].strftime('%Y-%m-%d') if student['enrollment_date'] else '',
                    student['course_name'] or '',
                    f"₱{student['course_price']:,.2f}" if student['course_price'] else '₱0.00',
                    f"₱{student['total_paid']:,.2f}",
                    f"₱{student['balance']:,.2f}" if student['balance'] else '₱0.00',
                    student['payment_status']
                ])

            # Create response
            response = make_response(output.getvalue())
            response.headers['Content-Type'] = 'text/csv'
            response.headers[
                'Content-Disposition'] = f'attachment; filename=students_export_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'

            return response

    except Exception as e:
        flash(f'Error exporting students: {str(e)}', 'error')
        return redirect(url_for('admin.students'))
    finally:
        connection.close()


# Helper function to generate next student ID
def generate_student_id():
    """Generate next student ID in format STU-YYYY-###"""
    try:
        connection = User.get_db_connection()
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

            return f'STU-{current_year}-{next_number:03d}'

    except Exception:
        # Fallback to basic format
        return f'STU-{datetime.now().year}-001'
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
    connection = User.get_db_connection()
    try:
        with connection.cursor() as cursor:
            # Get courses
            cursor.execute("SELECT * FROM courses WHERE is_active = TRUE ORDER BY name")
            courses = cursor.fetchall()

            # Get statistics
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

            # Average students per course
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
    code = request.form.get('code')
    price = request.form.get('price')
    description = request.form.get('description')

    if not all([name, code, price]):
        flash('Please fill in all required fields.', 'error')
        return redirect(url_for('admin.courses'))

    connection = User.get_db_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute('''
                INSERT INTO courses (name, code, price, description)
                VALUES (%s, %s, %s, %s)
            ''', (name, code, price, description))
            connection.commit()

            log_activity(current_user.id, f"Added course: {name} ({code})", 'courses', cursor.lastrowid)
            flash('Course added successfully.', 'success')

    except pymysql.IntegrityError:
        flash('Course code already exists.', 'error')
    finally:
        connection.close()

    return redirect(url_for('admin.courses'))


@admin_bp.route('/courses/edit/<int:course_id>', methods=['POST'])
@login_required
@admin_required
def edit_course(course_id):
    name = request.form.get('name')
    code = request.form.get('code')
    price = request.form.get('price')
    description = request.form.get('description')

    if not all([name, code, price]):
        flash('Please fill in all required fields.', 'error')
        return redirect(url_for('admin.courses'))

    connection = User.get_db_connection()
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
                SET name = %s, code = %s, price = %s, description = %s, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
            ''', (name, code, price, description, course_id))
            connection.commit()

            log_activity(current_user.id, f"Updated course: {name} ({code})", 'courses', course_id)
            flash('Course updated successfully.', 'success')

    except pymysql.IntegrityError:
        flash('Course code already exists.', 'error')
    except Exception as e:
        flash('An error occurred while updating the course.', 'error')
    finally:
        connection.close()

    return redirect(url_for('admin.courses'))


@admin_bp.route('/courses/delete/<int:course_id>', methods=['POST'])
@login_required
@admin_required
def delete_course(course_id):
    connection = User.get_db_connection()
    try:
        with connection.cursor() as cursor:
            # Check if course exists
            cursor.execute("SELECT * FROM courses WHERE id = %s AND is_active = TRUE", (course_id,))
            course = cursor.fetchone()

            if not course:
                flash('Course not found.', 'error')
                return redirect(url_for('admin.courses'))

            # Check if course has enrolled students
            cursor.execute("SELECT COUNT(*) as student_count FROM students WHERE course_id = %s AND is_active = TRUE",
                           (course_id,))
            student_count = cursor.fetchone()['student_count']

            if student_count > 0:
                flash(f'Cannot delete course. It has {student_count} enrolled student(s).', 'error')
                return redirect(url_for('admin.courses'))

            # Soft delete the course
            cursor.execute('''
                UPDATE courses 
                SET is_active = FALSE, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
            ''', (course_id,))
            connection.commit()

            log_activity(current_user.id, f"Deleted course: {course['name']} ({course['code']})", 'courses', course_id)
            flash('Course deleted successfully.', 'success')

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
    connection = User.get_db_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM courses WHERE id = %s AND is_active = TRUE", (course_id,))
            course = cursor.fetchone()

            if not course:
                return jsonify({'error': 'Course not found'}), 404

            return jsonify({
                'id': course['id'],
                'name': course['name'],
                'code': course['code'],
                'price': float(course['price']),
                'description': course['description'] or ''
            })
    finally:
        connection.close()


@admin_bp.route('/courses/<int:course_id>/students', methods=['GET'])
@login_required
@admin_required
def get_course_students(course_id):
    """API endpoint to get students enrolled in a course"""
    connection = User.get_db_connection()
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
    finally:
        connection.close()


@admin_bp.route('/cashiers')
@login_required
@admin_required
def cashiers():
    """Display all cashiers with proper data from database"""
    cashiers = User.get_all_cashiers()
    return render_template('admin/manage_cashiers.html', cashiers=cashiers)


@admin_bp.route('/cashiers/add', methods=['POST'])
@login_required
@admin_required
def add_cashier():
    """Add new cashier with validation"""
    name = request.form.get('name', '').strip()
    email = request.form.get('email', '').strip().lower()
    password = request.form.get('password', '')

    # Validation
    if not all([name, email, password]):
        flash('Please fill in all required fields.', 'error')
        return redirect(url_for('admin.cashiers'))

    if len(password) < 6:
        flash('Password must be at least 6 characters long.', 'error')
        return redirect(url_for('admin.cashiers'))

    # Validate email format
    import re
    email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    if not re.match(email_pattern, email):
        flash('Please enter a valid email address.', 'error')
        return redirect(url_for('admin.cashiers'))

    try:
        user_id = User.create(name, email, password, 'cashier')
        log_activity(current_user.id, f"Added cashier: {name} ({email})", 'users', user_id)
        flash('Cashier added successfully.', 'success')
    except Exception as e:
        if 'Duplicate entry' in str(e) or 'email already exists' in str(e).lower():
            flash('Email address already exists. Please use a different email.', 'error')
        else:
            flash('Error adding cashier. Please try again.', 'error')

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
    import re
    email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    if not re.match(email_pattern, email):
        flash('Please enter a valid email address.', 'error')
        return redirect(url_for('admin.cashiers'))

    try:
        if User.update_cashier(cashier_id, name, email):
            log_activity(current_user.id, f"Updated cashier: {name} ({email})", 'users', cashier_id)
            flash('Cashier updated successfully.', 'success')
        else:
            flash('Error updating cashier.', 'error')
    except Exception as e:
        if 'Duplicate entry' in str(e):
            flash('Email address already exists. Please use a different email.', 'error')
        else:
            flash('Error updating cashier. Please try again.', 'error')

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
            log_activity(current_user.id, f"Cashier {status}: {cashier.name}", 'users', cashier_id)
            flash(f'Cashier {status} successfully.', 'success')
        else:
            flash('Error updating cashier status.', 'error')
    except Exception as e:
        flash('Error updating cashier status.', 'error')

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
            log_activity(current_user.id, f"Deleted cashier: {cashier.name}", 'users', cashier_id)
            flash('Cashier deleted successfully.', 'success')
        else:
            flash('Error deleting cashier.', 'error')
    except Exception as e:
        flash('Error deleting cashier.', 'error')

    return redirect(url_for('admin.cashiers'))


from datetime import datetime, timedelta
from flask import request, jsonify


@admin_bp.route('/logs')
@admin_bp.route('/logs/<int:page>')
@login_required
@admin_required
def logs(page=1):
    """Display system logs with pagination"""
    per_page = 20  # Number of logs per page

    # Get logs with pagination
    logs_data = Log.get_paginated_logs(page, per_page)
    logs = logs_data['logs']
    pagination = logs_data['pagination']

    # Calculate statistics
    stats = Log.get_log_statistics()

    return render_template('admin/logs.html',
                           logs=logs,
                           pagination=pagination,
                           today_count=stats['today_count'],
                           unique_users=stats['unique_users'],
                           recent_count=stats['recent_count'],
                           now=datetime.now())


@admin_bp.route('/logs/<int:log_id>/details')
@login_required
@admin_required
def log_details(log_id):
    """Get detailed information for a specific log"""
    log = Log.get_by_id(log_id)
    if not log:
        return jsonify({'error': 'Log not found'}), 404

    return jsonify({
        'id': log.id,
        'created_at': log.created_at.strftime('%Y-%m-%d %H:%M:%S') if log.created_at else None,
        'user_name': log.user_name,
        'action': log.action,
        'table_name': log.table_name,
        'record_id': log.record_id,
        'user_agent': log.user_agent
    })


@admin_bp.route('/logs/clear', methods=['POST'])
@login_required
@admin_required
def clear_old_logs():
    """Clear logs older than 90 days"""
    try:
        deleted_count = Log.clear_old_logs(days=90)
        log_activity(current_user.id, f"Cleared {deleted_count} old log entries", 'logs')

        return jsonify({
            'success': True,
            'message': f'Successfully cleared {deleted_count} old log entries'
        })
    except Exception as e:
        print(f"Error clearing logs: {e}")
        return jsonify({
            'success': False,
            'message': 'Error clearing old logs'
        }), 500

@admin_bp.route('/profile')
@login_required
@admin_required
def profile():
    try:
        connection = mysql.connection
        cursor = connection.cursor()

        # Fetch current user’s profile (admin)
        cursor.execute("SELECT id, name, email, role, is_active, created_at, updated_at FROM users WHERE id = %s", (current_user.id,))
        user = cursor.fetchone()

        if user:
            profile = {
                "user_id": f"ADM-{user[0]:04d}",
                "full_name": user[1],
                "first_name": user[1].split()[0] if ' ' in user[1] else user[1],
                "last_name": user[1].split()[-1] if ' ' in user[1] else '',
                "email": user[2],
                "position": "System Administrator",
                "is_active": user[4],
                "created_date": user[5].strftime('%B %d, %Y'),
                "last_updated": user[6].strftime('%B %d, %Y'),
                "last_login": session.get("last_login", "Today, 10:00 AM"),
                "avatar_url": None,  # Add this if you store avatar uploads
                "phone": "+63 912 345 6789",  # You can add phone and address columns to users table
                "address": "Manila, Philippines",
                "bio": "Committed to streamlining school operations through technology.",
                "date_of_birth": "1995-07-15",  # Add this to DB if needed
            }
        else:
            profile = {}

        # For activity stats
        activity = {}

        # Count students added
        cursor.execute("SELECT COUNT(*) FROM students")
        activity["students_added"] = cursor.fetchone()[0]

        # Count courses created
        cursor.execute("SELECT COUNT(*) FROM courses")
        activity["courses_created"] = cursor.fetchone()[0]

        # Count cashiers managed (users with role='cashier')
        cursor.execute("SELECT COUNT(*) FROM users WHERE role = 'cashier'")
        activity["cashiers_managed"] = cursor.fetchone()[0]

        # Count login sessions (just an example — update if you have login logs)
        cursor.execute("SELECT COUNT(*) FROM logs WHERE user_id = %s", (current_user.id,))
        activity["login_sessions"] = cursor.fetchone()[0]

        return render_template("admin/profile.html", profile=profile, activity=activity)

    except Exception as e:
        print("Error fetching profile:", e)
        return render_template("admin/profile.html", profile={}, activity={})