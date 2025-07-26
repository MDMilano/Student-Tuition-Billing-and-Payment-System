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


cashier_bp = Blueprint('cashier', __name__)


@cashier_bp.route('/dashboard')
@login_required
@cashier_required
def dashboard():
    # connection = User.get_db_connection()
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




            # Recent payments collected by this cashier
            # cursor.execute('''
            #     SELECT p.*, s.first_name, s.last_name, s.student_id
            #     FROM payments p
            #     JOIN students s ON p.student_id = s.id
            #     WHERE p.collected_by = %s
            #     ORDER BY p.created_at DESC
            #     LIMIT 10
            # ''', (current_user.id,))
            # cashier_recent_payments = cursor.fetchall()

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
            #added the pending count
            cursor.execute('''
                SELECT 
                    COALESCE(SUM(sb.balance), 0.00) AS total_pending_amount,
                    COUNT(*) AS pending_count
                FROM student_balances sb
                JOIN payments p ON p.billing_id = sb.id
                WHERE sb.status IN ('unpaid', 'partial')
                  AND p.collected_by = %s
            ''', (current_user.id,))

            pending_stats = cursor.fetchone()



            #for displaying monthly
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





            #for the payment method to display dynamixcally
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


            #for recent payments
            cursor.execute('''
                SELECT 
                    p.created_at AS time,
                    CONCAT(s.first_name, ' ', s.last_name) AS student,
                    p.amount_paid AS amount,
                    p.payment_method AS method,
                    sb.status
                FROM payments p
                JOIN students s ON p.student_id = s.id
                LEFT JOIN student_balances sb ON sb.student_id = s.id
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
        'monthly_collections' : monthly_stats['total_monthly_collected'],
        'monthly_payments' : monthly_stats['monthly_payment_count']
    }

    return render_template('cashier/dashboard.html',
                           stats=stats,
                           paid_count=paid_count,
                           # partial_count=partial_count,
                           # unpaid_count=unpaid_count,
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
                    s.*,
                    c.name AS course_name,
                    c.price AS total_fee,
                    sb.total_due,
                    sb.total_paid,
                    sb.balance,
                    sb.status,
                    COALESCE(SUM(p.amount_paid), 0) AS amount_paid,
                    (COALESCE(sb.total_due, 0) - COALESCE(SUM(p.amount_paid), 0)) AS adjusted_balance,
                    latest_payment.latest_payment_date,
                    latest_payment.latest_payment_amount
                FROM students s
                LEFT JOIN courses c ON s.course_id = c.id
                LEFT JOIN student_balances sb ON sb.student_id = s.id
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
                GROUP BY 
                    s.id, c.name, c.price, sb.total_due, sb.total_paid, sb.balance, sb.status, latest_payment.latest_payment_date, latest_payment.latest_payment_amount
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
                    query += "amount_paid >= c.price"
                elif status_filter == 'partial':
                    query += "amount_paid > 0 AND amount_paid < c.price"
                elif status_filter == 'unpaid':
                    query += "amount_paid = 0"

            # Final ORDER BY clause
            query += " ORDER BY s.created_at DESC"

            cursor.execute(query, params)
            students = cursor.fetchall()

            # Process students data
            for student in students:
                if student['status']:
                    if student['status'] == 'paid':
                        student['status_display'] = 'Paid'
                        student['status_class'] = 'success'
                    elif student['status'] == 'partial':
                        student['status_display'] = 'Partial'
                        student['status_class'] = 'warning'
                    else:
                        student['status_display'] = 'Unpaid'
                        student['status_class'] = 'danger'
                else:
                    student['status_display'] = 'No Billing'
                    student['status_class'] = 'secondary'
                    student['total_due'] = 0
                    student['total_paid'] = 0
                    student['balance'] = 0

            # Get courses for filter
            cursor.execute("SELECT * FROM courses WHERE is_active = TRUE ORDER BY name")
            courses = cursor.fetchall()

            # Get summary counts
            cursor.execute('''
                SELECT 
                    SUM(CASE WHEN sb.status = 'paid' THEN 1 ELSE 0 END) as fully_paid,
                    SUM(CASE WHEN sb.status = 'partial' THEN 1 ELSE 0 END) as partially_paid,
                    SUM(CASE WHEN sb.status = 'unpaid' THEN 1 ELSE 0 END) as unpaid,
                    COUNT(DISTINCT s.id) as total_students
                FROM students s
                LEFT JOIN student_balances sb ON s.id = sb.student_id
                WHERE s.is_active = TRUE
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
                        sb.total_due AS totalFee,
                        sb.total_paid AS paidAmount,
                        sb.balance
                    FROM students s
                    LEFT JOIN courses c ON s.course_id = c.id
                    LEFT JOIN student_balances sb ON sb.student_id = s.id
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
                       sb.total_due, sb.total_paid, sb.balance
                FROM students s
                LEFT JOIN courses c ON s.course_id = c.id
                LEFT JOIN student_balances sb ON sb.student_id = s.id
                WHERE s.student_id LIKE %s OR CONCAT(s.first_name, ' ', s.last_name) LIKE %s
                ORDER BY s.id DESC
                LIMIT 1
            ''', (f'%{query}%', f'%{query}%'))

            student = cursor.fetchone()
            print(student)

            if student:
                return jsonify({
                    'id': student['id'],
                    'sid': student['student_id'],
                    'name': student['name'],
                    'course': student['course'],
                    'totalFee': float(student['total_due'] or 0),
                    'paidAmount': float(student['total_paid'] or 0),
                    'balance': float(student['balance'] or 0)
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

    if request.method == 'POST':
        amount = request.form.get('amount')
        method = request.form.get('payment_method')
        reference = request.form.get('reference_number', '')
        notes = request.form.get('notes', '')
        billing_id = request.form.get('billing_id')

        if not amount or not method:
            flash('Please fill in all required fields.', 'error')
            return redirect(url_for('cashier.collect_payment', student_id=student_id))

        try:
            amount = float(amount)
            if amount <= 0:
                flash('Amount must be greater than zero.', 'error')
                return redirect(url_for('cashier.collect_payment', student_id=student_id))
        except ValueError:
            flash('Invalid amount.', 'error')
            return redirect(url_for('cashier.collect_payment', student_id=student_id))

        try:
            with connection.cursor() as cursor:
                # Insert payment record
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

                    # Optionally, you can also update the balance if needed, but since you mentioned it is auto-computed, this may not be necessary.
                    # If balance is computed in the database, you can skip this step.
                    # cursor.execute('''
                    #     UPDATE student_balances
                    #     SET balance = total_due - total_paid
                    #     WHERE id = %s
                    # ''', (billing_id,))

                # Get student info for logging
                cursor.execute("SELECT first_name, last_name, student_id FROM students WHERE id = %s", (student_id,))
                student = cursor.fetchone()

                log_activity(current_user.id,
                             f"Collected payment of ₱{amount:,.2f} from {student['first_name']} {student['last_name']} ({student['student_id']})",
                             'payments', payment_id)

                flash(f'Payment of ₱{amount:,.2f} collected successfully.', 'success')
                return redirect(url_for('cashier.students'))

        except Exception as e:
            flash(f'Error processing payment: {str(e)}', 'error')

        finally:
            connection.close()

    # GET request - show payment form
    try:
        with connection.cursor() as cursor:
            # Get student info with balances
            cursor.execute('''
                SELECT 
                    s.*,
                    c.name as course_name,
                    sb.id as billing_id,
                    sb.total_due,
                    sb.total_paid,
                    sb.balance,
                    sb.status,
                    sb.semester,
                    sb.from_year,
                    sb.to_year
                FROM students s
                LEFT JOIN courses c ON s.course_id = c.id
                LEFT JOIN student_balances sb ON s.id = sb.student_id
                WHERE s.id = %s AND s.is_active = TRUE
            ''', (student_id,))

            student_data = cursor.fetchall()
            if not student_data:
                flash('Student not found.', 'error')
                return redirect(url_for('cashier.students'))

            # Group by student and collect all billing records
            student = student_data[0]
            student['billings'] = []

            for row in student_data:
                if row['billing_id']:
                    student['billings'].append({
                        'id': row['billing_id'],
                        'semester': row['semester'],
                        'from_year': row['from_year'],
                        'to_year': row['to_year'],
                        'total_due': row['total_due'],
                        'total_paid': row['total_paid'],
                        'balance': row['balance'],
                        'status': row['status']
                    })
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
                    sb.status AS status
                FROM payments p
                JOIN students s ON p.student_id = s.id
                LEFT JOIN courses c ON s.course_id = c.id
                LEFT JOIN student_balances sb ON sb.id = p.billing_id
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
            #pagination
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
                SELECT p.*, u.name AS collected_by_name, s.first_name, s.last_name
                FROM payments p
                JOIN users u ON p.collected_by = u.id
                JOIN students s ON p.student_id = s.id
                ORDER BY p.payment_date DESC, p.created_at DESC
            ''')
            payments = cursor.fetchall()

            cursor.execute('''
                SELECT 
                    p.id,
                    p.created_at AS datetime,
                    CONCAT(s.first_name, ' ', s.last_name) AS student,
                    s.student_id AS student_number,
                    c.code AS course,
                    p.amount_paid AS amount,
                    p.payment_method AS method,
                    sb.status AS status,
                    p.reference_number AS reference,
                    p.notes,
                    u.name AS collected_by
                FROM payments p
                JOIN students s ON p.student_id = s.id
                LEFT JOIN courses c ON s.course_id = c.id
                LEFT JOIN student_balances sb ON sb.id = p.billing_id
                LEFT JOIN users u ON p.collected_by = u.id
                ORDER BY p.created_at DESC
                LIMIT %s OFFSET %s
            ''', (per_page, offset))
            payment_history = cursor.fetchall()


            #courses:
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
                           showing_end=showing_end,)


@cashier_bp.route('/profile')
@login_required
@cashier_required
def profile():
    return render_template('cashier/profile.html')