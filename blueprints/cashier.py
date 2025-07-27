from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from utils.helpers import log_activity, cashier_required
from models.user import User
import pymysql
from config import Config
from datetime import date
from database.init_db import get_db_connection
from flask import send_file
import io
import pandas as pd
from flask import jsonify, request
from decimal import Decimal


cashier_bp = Blueprint('cashier', __name__)


@cashier_bp.route('/dashboard')
@login_required
@cashier_required
def dashboard():
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            # Get total students
            cursor.execute("SELECT COUNT(*) as count FROM students WHERE is_active = TRUE")
            total_students = cursor.fetchone()['count']

            # Get payment status counts
            cursor.execute('''
                SELECT 
                    s.id,
                    c.price as total_fee,
                    COALESCE(SUM(p.amount_paid), 0) as amount_paid
                FROM students s
                JOIN courses c ON s.course_id = c.id
                LEFT JOIN payments p ON s.id = p.student_id
                WHERE s.is_active = TRUE
                GROUP BY s.id, c.price
            ''')

            students_data = cursor.fetchall()
            paid_count = 0
            partial_count = 0
            unpaid_count = 0

            for student in students_data:
                if student['amount_paid'] >= student['total_fee']:
                    paid_count += 1
                elif student['amount_paid'] > 0:
                    partial_count += 1
                else:
                    unpaid_count += 1

            # Total payments collected today by this cashier
            cursor.execute('''
                SELECT 
                    COALESCE(SUM(amount_paid), 0) as total_collected,
                    COUNT(*) as payment_count
                FROM payments
                WHERE collected_by = %s
                  AND payment_date >= CURDATE()
                  AND payment_date < CURDATE() + INTERVAL 1 DAY
            ''', (current_user.id,))
            today_stats = cursor.fetchone()

            # Total pending amount (partial + unpaid)
            cursor.execute('''
                SELECT 
                    COALESCE(SUM(c.price) - SUM(p.amount_paid), 0.00) AS total_pending_amount,
                    COUNT(*) AS pending_count
                FROM students s
                JOIN courses c ON s.course_id = c.id
                LEFT JOIN payments p ON s.id = p.student_id
                WHERE s.is_active = TRUE
                GROUP BY s.id
                HAVING SUM(c.price) > COALESCE(SUM(p.amount_paid), 0)
            ''')
            pending_stats = cursor.fetchone()

            # For displaying monthly
            cursor.execute('''
                SELECT 
                    COALESCE(SUM(amount_paid), 0.00) AS total_monthly_collected,
                    COUNT(*) AS monthly_payment_count
                FROM payments
                WHERE collected_by = %s
                  AND payment_date >= DATE_FORMAT(CURDATE(), '%%Y-%%m-01')
                  AND payment_date < DATE_FORMAT(CURDATE() + INTERVAL 1 MONTH, '%%Y-%%m-01')
            ''', (current_user.id,))
            monthly_stats = cursor.fetchone()

            # For the payment method to display dynamically
            cursor.execute("""
                SELECT payment_method, COUNT(*) AS count
                FROM payments
                GROUP BY payment_method
            """)
            results = cursor.fetchall()

            # Default counts
            data = {
                'cash': 0,
                'gcash': 0,
                'bank': 0,
            }

            total = 0

            for row in results:
                method = row['payment_method'].lower()
                count = row['count']
                total += count

                if 'cash' in method:
                    data['cash'] += count
                elif 'gcash' in method or 'maya' in method:
                    data['gcash'] += count
                elif 'bank' in method:
                    data['bank'] += count

            # Compute percentages
            percentages = {
                'cash': round((data['cash'] / total) * 100) if total else 0,
                'gcash': round((data['gcash'] / total) * 100) if total else 0,
                'bank': round((data['bank'] / total) * 100) if total else 0,
            }

            # For recent payments
            # For recent payments
            cursor.execute('''
                SELECT 
                    p.created_at AS time,
                    CONCAT(s.first_name, ' ', s.last_name) AS student,
                    p.amount_paid AS amount,
                    p.payment_method AS method,
                    CASE 
                        WHEN p.amount_paid >= c.price THEN 'paid'
                        WHEN p.amount_paid > 0 THEN 'partial'
                        ELSE 'unpaid'
                    END AS status
                FROM payments p
                JOIN students s ON p.student_id = s.id
                JOIN courses c ON s.course_id = c.id
                WHERE p.collected_by = %s
                ORDER BY p.created_at DESC
                LIMIT 5
            ''', (current_user.id,))
            recent_payments = cursor.fetchall()


    finally:
        connection.close()

    stats = {
        'today_collections': today_stats['total_collected'],
        'today_payments': today_stats['payment_count'],
        'pending_payments': pending_stats['pending_count'],
        'pending_amount': pending_stats['total_pending_amount'],
        'students_handled': total_students,
        'monthly_collections': monthly_stats['total_monthly_collected'],
        'monthly_payments': monthly_stats['monthly_payment_count']
    }

    return render_template('cashier/dashboard.html',
                           stats=stats,
                           paid_count=paid_count,
                           partial_count=partial_count,
                           unpaid_count=unpaid_count,
                           recent_payments=recent_payments,
                           payment_data=percentages)



@cashier_bp.route('/students')
@login_required
@cashier_required
def students():
    course_filter = request.args.get('course', '')
    status_filter = request.args.get('status', '')
    search_query = request.args.get('search', '')

    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            # Base query
            query = '''
                SELECT 
                    s.id,
                    s.student_id AS sid,
                    CONCAT(s.first_name, ' ', s.last_name) AS name,
                    c.name AS course_name,
                    c.price AS total_fee,  -- Total due is the course price
                    COALESCE(SUM(p.amount_paid), 0) AS total_paid,  -- Total paid from payments
                    (c.price - COALESCE(SUM(p.amount_paid), 0)) AS balance,  -- Calculate balance
                    latest_payment.latest_payment_date,
                    latest_payment.latest_payment_amount,
                    CASE 
                        WHEN COALESCE(SUM(p.amount_paid), 0) >= c.price THEN 'paid'
                        WHEN COALESCE(SUM(p.amount_paid), 0) > 0 THEN 'partial'
                        ELSE 'unpaid'
                    END AS status  -- Determine payment status
                FROM students s
                LEFT JOIN courses c ON s.course_id = c.id
                LEFT JOIN payments p ON s.id = p.student_id
                LEFT JOIN (
                    SELECT 
                        p.student_id, 
                        p.amount_paid AS latest_payment_amount, 
                        p.created_at AS latest_payment_date
                    FROM 
                        payments p
                    INNER JOIN (
                        SELECT 
                            student_id, 
                            MAX(created_at) AS max_date
                        FROM 
                            payments
                        GROUP BY 
                            student_id
                    ) AS latest ON p.student_id = latest.student_id AND p.created_at = latest.max_date
                ) AS latest_payment ON latest_payment.student_id = s.id
                WHERE s.is_active = TRUE
            '''

            params = []

            # Add search filter
            if search_query:
                query += " AND (s.first_name LIKE %s OR s.last_name LIKE %s OR s.student_id LIKE %s OR s.email LIKE %s)"
                search_param = f"%{search_query}%"
                params.extend([search_param, search_param, search_param, search_param])

            if course_filter:
                query += " AND c.id = %s"
                params.append(course_filter)

            # Add HAVING clause based on status filter
            if status_filter:
                query += " HAVING "
                if status_filter == 'paid':
                    query += " total_paid >= total_fee"
                elif status_filter == 'partial':
                    query += " total_paid > 0 AND total_paid < total_fee"
                elif status_filter == 'unpaid':
                    query += " total_paid = 0"

            # Group by clause
            query += '''
                GROUP BY 
                    s.id, c.name, c.price, latest_payment.latest_payment_date, latest_payment.latest_payment_amount
                ORDER BY s.created_at DESC
            '''

            cursor.execute(query, params)
            students = cursor.fetchall()

            # Process students data
            for student in students:
                # The status is already calculated in the SQL query
                student['status_display'] = student['status']  # Use the status from the query
                if student['status'] == 'paid':
                    student['status_class'] = 'success'
                elif student['status'] == 'partial':
                    student['status_class'] = 'warning'
                else:
                    student['status_class'] = 'danger'

            # Get courses for filter
            cursor.execute("SELECT * FROM courses WHERE is_active = TRUE ORDER BY name")
            courses = cursor.fetchall()

            # Get summary counts
            cursor.execute('''
                SELECT 
                    SUM(CASE WHEN total_paid >= price THEN 1 ELSE 0 END) as fully_paid,
                    SUM(CASE WHEN total_paid > 0 AND total_paid < price THEN 1 ELSE 0 END) as partially_paid,
                    SUM(CASE WHEN total_paid = 0 THEN 1 ELSE 0 END) as unpaid,
                    COUNT(DISTINCT id) as total_students
                FROM (
                    SELECT 
                        s.id,
                        s.student_id,
                        COALESCE(SUM(p.amount_paid), 0) AS total_paid,
                        c.price
                    FROM students s
                    LEFT JOIN courses c ON s.course_id = c.id
                    LEFT JOIN payments p ON s.id = p.student_id
                    WHERE s.is_active = TRUE
                    GROUP BY s.id, c.price
                ) AS student_totals
            ''')
            summary = cursor.fetchone()

    finally:
        connection.close()

    return render_template('cashier/students.html',
                           students=students,
                           courses=courses,
                           course_filter=course_filter,
                           status_filter=status_filter,
                           search_query=search_query,
                           summary=summary,
                           total_students=len(students))







@cashier_bp.route('/view-collect-payment', methods=['GET', 'POST'])
@login_required
@cashier_required
def view_collect_payment():
    connection = get_db_connection()
    recent_students = []  # Initialize variable so it's always defined
    try:
        with connection.cursor() as cursor:
            # Get the 5 most recently added students (ordered by ID or a timestamp column)
            cursor.execute('''
                SELECT 
                    s.id, 
                    s.student_id AS sid, 
                    CONCAT(s.first_name, ' ', s.last_name) AS name,
                    c.name AS course,
                    c.price AS totalFee,  -- Total due is the course price
                    COALESCE(SUM(p.amount_paid), 0) AS paidAmount,  -- Total paid from payments
                    (c.price - COALESCE(SUM(p.amount_paid), 0)) AS balance  -- Calculate balance directly in SQL
                FROM students s
                LEFT JOIN courses c ON s.course_id = c.id
                LEFT JOIN payments p ON s.id = p.student_id
                WHERE s.is_active = TRUE  -- Ensure only active students are considered
                GROUP BY s.id, c.id  -- Group by student and course
                ORDER BY s.id DESC
                LIMIT 5
            ''')
            recent_students = cursor.fetchall()

    except Exception as e:
        flash(f'Error: {str(e)}', 'error')

    finally:
        connection.close()

    return render_template('cashier/collect_payment.html',
                           recent_students=recent_students)




@cashier_bp.route('/api/search-student', methods=['GET'])
@login_required
@cashier_required
def api_search_student():
    query = request.args.get('query', '').strip()

    if not query:
        return jsonify({'error': 'Missing search query'}), 400

    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute('''
                SELECT s.id, s.student_id, CONCAT(s.first_name, ' ', s.last_name) AS name,
                       c.name AS course,
                       c.price AS total_due,  -- Total due is the course price
                       COALESCE(SUM(p.amount_paid), 0) AS total_paid  -- Total paid from payments
                FROM students s
                LEFT JOIN courses c ON s.course_id = c.id
                LEFT JOIN payments p ON s.id = p.student_id
                WHERE s.student_id LIKE %s OR CONCAT(s.first_name, ' ', s.last_name) LIKE %s
                GROUP BY s.id, c.id  -- Group by student and course
                ORDER BY s.id DESC
                LIMIT 1
            ''', (f'%{query}%', f'%{query}%'))

            student = cursor.fetchone()
            print(student)

            if student:
                # Calculate balance
                total_due = student['total_due']
                total_paid = student['total_paid']
                balance = total_due - total_paid  # Calculate balance

                return jsonify({
                    'id': student['id'],
                    'sid': student['student_id'],
                    'name': student['name'],
                    'course': student['course'],
                    'totalFee': float(total_due or 0),
                    'paidAmount': float(total_paid or 0),
                    'balance': float(balance or 0)  # Return calculated balance
                })
            else:
                return jsonify({'error': 'Student not found'}), 404
    finally:
        connection.close()


@cashier_bp.route('/collect-payment/<int:student_id>', methods=['GET', 'POST'])
@login_required
@cashier_required
def collect_payment(student_id):
    connection = get_db_connection()
    student = None  # Initialize student variable to ensure it's defined
    try:
        if request.method == 'POST':
            amount = request.form.get('amount')
            method = request.form.get('payment_method')
            reference = request.form.get('reference_number', '')
            notes = request.form.get('notes', '')
            billing_id = request.form.get('billing_id')

            if not amount or not method:
                flash('Please fill in all required fields.', 'error')
                return redirect(url_for('cashier.view_collect_payment'))

            try:
                amount = Decimal(amount)  # Convert to Decimal
                if amount <= 0:
                    flash('Amount must be greater than zero.', 'error')
                    return redirect(url_for('cashier.view_collect_payment'))
            except Exception as e:
                flash(f'Error: {str(e)}', 'error')
                return redirect(url_for('cashier.view_collect_payment'))

            with connection.cursor() as cursor:
                # Get the student's total due and total paid
                cursor.execute('''
                    SELECT 
                        s.id AS student_id,
                        CONCAT(s.first_name, ' ', s.last_name) AS name,
                        c.price AS total_due,
                        COALESCE(SUM(p.amount_paid), 0) AS total_paid
                    FROM students s
                    LEFT JOIN courses c ON s.course_id = c.id
                    LEFT JOIN payments p ON s.id = p.student_id
                    WHERE s.id = %s
                    GROUP BY s.id, s.first_name, s.last_name;
                ''', (student_id,))
                student_data = cursor.fetchone()

                if not student_data:
                    flash('Student not found.', 'error')
                    return redirect(url_for('cashier.students'))

                total_due = Decimal(student_data['total_due'])  # Convert to Decimal
                total_paid = Decimal(student_data['total_paid'])  # Convert to Decimal
                balance = total_due - total_paid

                # Check if the balance is already zero
                if balance <= 0:
                    flash('The student has already paid in full. No further payments are required.', 'error')
                    return redirect(url_for('cashier.view_collect_payment'))

                # Calculate the new total paid
                new_total_paid = total_paid + amount

                # Check if the new total paid exceeds the total due
                if new_total_paid > total_due:
                    flash(f'The payment amount of ₱{amount:,.2f} exceeds the total due of ₱{total_due:,.2f}. Payment cannot be processed.', 'error')
                    return redirect(url_for('cashier.view_collect_payment'))

                # Insert payment record only if all checks pass
                cursor.execute('''
                    INSERT INTO payments (student_id, billing_id, amount_paid, payment_method, reference_number, payment_date, collected_by, notes)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ''', (
                    student_id, billing_id if billing_id else None, amount, method, reference, date.today(),
                    current_user.id, notes))

                payment_id = cursor.lastrowid

                # Update total_paid in student_balances if billing_id provided
                if student_id:
                    cursor.execute('''
                        UPDATE student_balances 
                        SET total_paid = total_paid + %s
                        WHERE student_id = %s
                    ''', (amount, student_id))

                # Get student info for logging
                log_activity(current_user.id,
                             f"Collected payment of ₱{amount:,.2f} from {student_data['name']} ({student_data['student_id']})",
                             'payments', payment_id)

                flash(f'Payment of ₱{amount:,.2f} collected successfully.', 'success')
                return redirect(url_for('cashier.students'))

        # GET request - show payment form
        with connection.cursor() as cursor:
            # Get student info with course details
            cursor.execute('''
                SELECT 
                    s.id AS student_id,
                    CONCAT(s.first_name, ' ', s.last_name) AS name,
                    c.price AS total_due,
                    COALESCE(SUM(p.amount_paid), 0) AS total_paid
                FROM students s
                LEFT JOIN courses c ON s.course_id = c.id
                LEFT JOIN payments p ON s.id = p.student_id
                WHERE s.id = %s AND s.is_active = TRUE
                GROUP BY s.id, s.first_name, s.last_name;
            ''', (student_id,))

            student_data = cursor.fetchall()
            if not student_data:
                flash('Student not found.', 'error')
                return redirect(url_for('cashier.students'))

            # Get the student data
            student = student_data[0]

            # Calculate balance
            total_due = Decimal(student['total_due'])  # Convert to Decimal
            total_paid = Decimal(student['total_paid'])  # Convert to Decimal
            balance = total_due - total_paid
            status = 'unpaid' if total_paid == 0 else 'paid' if total_paid >= total_due else 'partial'

            # Add billing information to student data
            student['billings'] = [{
                'semester': '1st',  # You can set this based on your logic
                'from_year': 2024,  # Replace with actual year logic
                'to_year': 2025,  # Replace with actual year logic
                'total_fee': total_due,
                'total_paid': total_paid,
                'balance': balance,
                'status': status
            }]

    except Exception as e:
        flash(f'An unexpected error occurred: {str(e)}', 'error')
    finally:
        connection.close()

    return render_template('cashier/collect_payment.html', student=student)



@cashier_bp.route('/payment-history/<int:student_id>')
@login_required
@cashier_required
def payment_history(student_id):
#     connection = User.get_db_connection()
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            # Get student info
            cursor.execute('''
                SELECT s.*, c.name as course_name, c.price as total_fee
                FROM students s
                JOIN courses c ON s.course_id = c.id
                WHERE s.id = %s
            ''', (student_id,))
            student = cursor.fetchone()

            if not student:
                flash('Student not found.', 'error')
                return redirect(url_for('cashier.students'))

            # Get payment history
            cursor.execute('''
                SELECT p.*, u.name as collected_by_name
                FROM payments p
                JOIN users u ON p.collected_by = u.id
                WHERE p.student_id = %s
                ORDER BY p.payment_date DESC, p.created_at DESC
            ''', (student_id,))
            payments = cursor.fetchall()

            # Calculate totals
            total_paid = sum(payment['amount_paid'] for payment in payments)
            balance = student['total_fee'] - total_paid

    finally:
        connection.close()

    return render_template('cashier/payment_history.html',
                           student=student,
                           payments=payments,
                           total_paid=total_paid,
                           balance=balance)


@cashier_bp.route('/export/payments')
@login_required
@cashier_required
def export_payments():
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT 
                    p.reference_number AS reference,
                    p.created_at AS datetime,
                    CONCAT(s.first_name, ' ', s.last_name) AS student,
                    s.student_id AS student_number,
                    c.name AS course,
                    p.amount_paid AS amount,
                    p.payment_method AS method,
                    CASE 
                        WHEN p.amount_paid >= c.price THEN 'paid'
                        WHEN p.amount_paid > 0 THEN 'partial'
                        ELSE 'unpaid'
                    END AS status
                FROM payments p
                JOIN students s ON p.student_id = s.id
                LEFT JOIN courses c ON s.course_id = c.id
                ORDER BY p.created_at DESC
            """)
            payments = cursor.fetchall()

        # Convert to DataFrame
        df = pd.DataFrame(payments)
        df.columns = ['Reference No.', 'Date & Time', 'Student Name', 'Student ID', 'Course',
                      'Amount', 'Method', 'Status']

        # Create Excel file in memory
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df.to_excel(writer, index=False, sheet_name='Payments')

        output.seek(0)
        return send_file(
            output,
            download_name='payment_history.xlsx',
            as_attachment=True,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )

    finally:
        connection.close()




@cashier_bp.route('/payment-history-all')
@login_required
@cashier_required
def payment_history_all():
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            # Pagination
            page = request.args.get('page', default=1, type=int)
            per_page = request.args.get('per_page', default=25, type=int)
            offset = (page - 1) * per_page

            # Total number of payments
            cursor.execute("SELECT COUNT(*) as total_payments FROM payments")
            total_payments = cursor.fetchone()['total_payments']

            # Total amount paid
            cursor.execute("SELECT COALESCE(SUM(amount_paid), 0.00) as total_amount FROM payments")
            total_amount = cursor.fetchone()['total_amount']

            # Today's total amount
            cursor.execute("""
                SELECT COALESCE(SUM(amount_paid), 0.00) as todays_total
                FROM payments
                WHERE DATE(created_at) = CURDATE()
            """)
            todays_total = cursor.fetchone()['todays_total']

            # Monthly total amount
            cursor.execute("""
                SELECT COALESCE(SUM(amount_paid), 0.00) as monthly_total
                FROM payments
                WHERE created_at >= DATE_FORMAT(CURDATE(), '%Y-%m-01')
                  AND created_at < DATE_FORMAT(CURDATE() + INTERVAL 1 MONTH, '%Y-%m-01')
            """)
            monthly_total = cursor.fetchone()['monthly_total']

            # Get all payment history (regardless of student)
            cursor.execute('''
                SELECT 
                    p.id,
                    p.created_at AS datetime,
                    CONCAT(s.first_name, ' ', s.last_name) AS student,
                    s.student_id AS student_number,
                    c.code AS course,
                    p.amount_paid AS amount,
                    p.payment_method AS method,
                    CASE 
                        WHEN p.amount_paid >= c.price THEN 'paid'
                        WHEN p.amount_paid > 0 THEN 'partial'
                        ELSE 'unpaid'
                    END AS status,
                    p.reference_number AS reference,
                    p.notes,
                    u.name AS collected_by
                FROM payments p
                JOIN students s ON p.student_id = s.id
                LEFT JOIN courses c ON s.course_id = c.id
                LEFT JOIN users u ON p.collected_by = u.id
                ORDER BY p.created_at DESC
                LIMIT %s OFFSET %s
            ''', (per_page, offset))
            payment_history = cursor.fetchall()

            # Get distinct active courses
            cursor.execute("SELECT id, name FROM courses WHERE is_active = TRUE ORDER BY name")
            courses = cursor.fetchall()

            showing_end = min(offset + per_page, total_payments)

    finally:
        connection.close()

    summary = {
        'total_payments': total_payments,
        'total_amount': total_amount,
        'todays_total': todays_total,
        'monthly_total': monthly_total
    }

    return render_template('cashier/payment_history.html',
                           summary=summary,
                           payment_history=payment_history,
                           courses=courses,
                           page=page,
                           per_page=per_page,
                           offset=offset,
                           total_payments=total_payments,
                           showing_end=showing_end)



@cashier_bp.route('/profile')
@login_required
@cashier_required
def profile():
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            # Fetch user details from the database
            cursor.execute('''
                SELECT name, email
                FROM users
                WHERE id = %s
            ''', (current_user.id,))
            user = cursor.fetchone()

            if not user:
                flash('User  not found.', 'error')
                return redirect(url_for('cashier.dashboard'))

            # Prepare user data to pass to the template
            user_data = {
                'name': user['name'],
                'email': user['email'],
            }

    finally:
        connection.close()

    return render_template('cashier/profile.html', current_user=user_data)
