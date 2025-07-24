from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from utils.helpers import log_activity, cashier_required
from models.user import User
import pymysql
from config import Config
from datetime import date

cashier_bp = Blueprint('cashier', __name__)


@cashier_bp.route('/dashboard')
@login_required
@cashier_required
def dashboard():
    connection = User.get_db_connection()
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
            cursor.execute('''
                SELECT p.*, s.first_name, s.last_name, s.student_id
                FROM payments p
                JOIN students s ON p.student_id = s.id
                WHERE p.collected_by = %s
                ORDER BY p.created_at DESC
                LIMIT 10
            ''', (current_user.id,))
            recent_payments = cursor.fetchall()

    finally:
        connection.close()

    return render_template('cashier/dashboard.html',
                           total_students=total_students,
                           paid_count=paid_count,
                           partial_count=partial_count,
                           unpaid_count=unpaid_count,
                           recent_payments=recent_payments)


@cashier_bp.route('/students')
@login_required
@cashier_required
def students():
    course_filter = request.args.get('course', '')
    status_filter = request.args.get('status', '')

    connection = User.get_db_connection()
    try:
        with connection.cursor() as cursor:
            # Base query
            query = '''
                SELECT 
                    s.*,
                    c.name as course_name,
                    c.price as total_fee,
                    COALESCE(SUM(p.amount_paid), 0) as amount_paid,
                    (c.price - COALESCE(SUM(p.amount_paid), 0)) as balance
                FROM students s
                JOIN courses c ON s.course_id = c.id
                LEFT JOIN payments p ON s.id = p.student_id
                WHERE s.is_active = TRUE
            '''

            params = []

            if course_filter:
                query += " AND c.id = %s"
                params.append(course_filter)

            query += " GROUP BY s.id, c.price"

            if status_filter:
                if status_filter == 'paid':
                    query += " HAVING amount_paid >= c.price"
                elif status_filter == 'partial':
                    query += " HAVING amount_paid > 0 AND amount_paid < c.price"
                elif status_filter == 'unpaid':
                    query += " HAVING amount_paid = 0"

            query += " ORDER BY s.created_at DESC"

            cursor.execute(query, params)
            students = cursor.fetchall()

            # Add status to each student
            for student in students:
                if student['amount_paid'] >= student['total_fee']:
                    student['status'] = 'Paid'
                    student['status_class'] = 'success'
                elif student['amount_paid'] > 0:
                    student['status'] = 'Partial'
                    student['status_class'] = 'warning'
                else:
                    student['status'] = 'Unpaid'
                    student['status_class'] = 'danger'

            # Get courses for filter
            cursor.execute("SELECT * FROM courses WHERE is_active = TRUE ORDER BY name")
            courses = cursor.fetchall()

    finally:
        connection.close()

    return render_template('cashier/students.html',
                           students=students,
                           courses=courses,
                           course_filter=course_filter,
                           status_filter=status_filter)


@cashier_bp.route('/collect-payment/<int:student_id>', methods=['GET', 'POST'])
@login_required
@cashier_required
def collect_payment(student_id):
    connection = User.get_db_connection()

    if request.method == 'POST':
        amount = request.form.get('amount')
        method = request.form.get('method')
        reference = request.form.get('reference')
        notes = request.form.get('notes')

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
                cursor.execute('''
                    INSERT INTO payments (student_id, amount_paid, payment_method, reference_number, payment_date, collected_by, notes)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                ''', (student_id, amount, method, reference, date.today(), current_user.id, notes))
                connection.commit()

                # Get student info for logging
                cursor.execute("SELECT first_name, last_name, student_id FROM students WHERE id = %s", (student_id,))
                student = cursor.fetchone()

                log_activity(current_user.id,
                             f"Collected payment of ₱{amount:,.2f} from {student['first_name']} {student['last_name']} ({student['student_id']})",
                             'payments', cursor.lastrowid)

                flash(f'Payment of ₱{amount:,.2f} collected successfully.', 'success')
                return redirect(url_for('cashier.students'))

        finally:
            connection.close()

    # GET request - show payment form
    try:
        with connection.cursor() as cursor:
            cursor.execute('''
                SELECT 
                    s.*,
                    c.name as course_name,
                    c.price as total_fee,
                    COALESCE(SUM(p.amount_paid), 0) as amount_paid
                FROM students s
                JOIN courses c ON s.course_id = c.id
                LEFT JOIN payments p ON s.id = p.student_id
                WHERE s.id = %s AND s.is_active = TRUE
                GROUP BY s.id, c.price
            ''', (student_id,))

            student = cursor.fetchone()
            if not student:
                flash('Student not found.', 'error')
                return redirect(url_for('cashier.students'))

            student['balance'] = student['total_fee'] - student['amount_paid']

    finally:
        connection.close()

    return render_template('cashier/collect_payment.html', student=student)


@cashier_bp.route('/payment-history/<int:student_id>')
@login_required
@cashier_required
def payment_history(student_id):
    connection = User.get_db_connection()
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


@cashier_bp.route('/profile')
@login_required
@cashier_required
def profile():
    return render_template('cashier/profile.html')