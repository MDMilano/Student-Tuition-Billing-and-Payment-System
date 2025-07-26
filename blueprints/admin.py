from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from functools import wraps
from models.user import User
from utils.helpers import log_activity, admin_required
import pymysql
from config import Config
from database.db import get_db_connection

admin_bp = Blueprint('admin', __name__)


@admin_bp.route('/dashboard')
@login_required
@admin_required
def dashboard():
    # connection = User.get_db_connection()
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            # Get total students
            cursor.execute("SELECT COUNT(*) as count FROM students WHERE is_active = TRUE")
            total_students = cursor.fetchone()['count']

            # Get total courses
            cursor.execute("SELECT COUNT(*) as count FROM courses WHERE is_active = TRUE")
            total_courses = cursor.fetchone()['count']

            # Get total payments collected
            cursor.execute("SELECT COALESCE(SUM(amount_paid), 0) as total FROM payments")
            total_payments = cursor.fetchone()['total']

            # Get total cashier
            cursor.execute("SELECT COUNT(*) as count FROM users WHERE role = 'cashier' AND is_active = TRUE")
            total_active_cashiers = cursor.fetchone()['count']

            # Get recent activities
            cursor.execute('''
                SELECT l.*, u.name as user_name 
                FROM logs l 
                JOIN users u ON l.user_id = u.id 
                ORDER BY l.created_at DESC 
                LIMIT 10
            ''')
            recent_logs = cursor.fetchall()

    finally:
        connection.close()

    return render_template('admin/dashboard.html',
                           total_students=total_students,
                           total_courses=total_courses,
                           total_payments=total_payments,
                           total_active_cashiers=total_active_cashiers,
                           recent_logs=recent_logs)


@admin_bp.route('/students')
@login_required
@admin_required
def students():
#     connection = User.get_db_connection()
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute('''
                SELECT s.*, c.name as course_name, c.price as course_price
                FROM students s
                LEFT JOIN courses c ON s.course_id = c.id
                WHERE s.is_active = TRUE
                ORDER BY s.created_at DESC
            ''')
            students = cursor.fetchall()

            cursor.execute("SELECT * FROM courses WHERE is_active = TRUE ORDER BY name")
            courses = cursor.fetchall()

    finally:
        connection.close()

    return render_template('admin/manage_students.html', students=students, courses=courses)


@admin_bp.route('/students/add', methods=['POST'])
@login_required
@admin_required
def add_student():
    student_id = request.form.get('student_id')
    first_name = request.form.get('first_name')
    last_name = request.form.get('last_name')
    email = request.form.get('email')
    phone = request.form.get('phone')
    address = request.form.get('address')
    course_id = request.form.get('course')
    enrollment_date = request.form.get('enrollment_date')

    if not all([student_id, first_name, last_name, email, phone, address, enrollment_date, course_id]):
        flash('Please fill in all required fields.', 'error')
        return redirect(url_for('admin.students'))

#     connection = User.get_db_connection()
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute('''
                INSERT INTO students (student_id, first_name, last_name, email, phone, address, course_id, enrollment_date)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ''', (student_id, first_name, last_name, email, phone, address, course_id, enrollment_date))
            connection.commit()

            log_activity(current_user.id, f"Added student: {first_name} {last_name} ({student_id})", 'students',
                         cursor.lastrowid)
            flash('Student added successfully.', 'success')

    except pymysql.IntegrityError as e:
        if 'student_id' in str(e):
            flash('Student ID already exists.', 'error')
        elif 'email' in str(e):
            flash('Email already exists.', 'error')
        else:
            flash('Error adding student.', 'error')
    finally:
        connection.close()

    return redirect(url_for('admin.students'))


@admin_bp.route('/courses')
@login_required
@admin_required
def courses():
#     connection = User.get_db_connection()
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM courses WHERE is_active = TRUE ORDER BY name")
            courses = cursor.fetchall()
    finally:
        connection.close()

    return render_template('admin/manage_courses.html', courses=courses)


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

#     connection = User.get_db_connection()
    connection = get_db_connection()
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


@admin_bp.route('/cashiers')
@login_required
@admin_required
def cashiers():
    cashiers = User.get_all_cashiers()
    return render_template('admin/manage_cashiers.html', cashiers=cashiers)


@admin_bp.route('/cashiers/add', methods=['POST'])
@login_required
@admin_required
def add_cashier():
    name = request.form.get('name')
    email = request.form.get('email')
    password = request.form.get('password')

    if not all([name, email, password]):
        flash('Please fill in all required fields.', 'error')
        return redirect(url_for('admin.cashiers'))

    try:
        user_id = User.create(name, email, password, 'cashier')
        log_activity(current_user.id, f"Added cashier: {name} ({email})", 'users', user_id)
        flash('Cashier added successfully.', 'success')
    except:
        flash('Email already exists.', 'error')

    return redirect(url_for('admin.cashiers'))


@admin_bp.route('/cashiers/toggle/<int:cashier_id>')
@login_required
@admin_required
def toggle_cashier(cashier_id):
    if User.toggle_active(cashier_id):
        log_activity(current_user.id, f"Toggled cashier status: ID {cashier_id}", 'users', cashier_id)
        flash('Cashier status updated.', 'success')
    else:
        flash('Error updating cashier status.', 'error')

    return redirect(url_for('admin.cashiers'))


@admin_bp.route('/logs')
@login_required
@admin_required
def logs():
    page = request.args.get('page', 1, type=int)
    per_page = Config.LOGS_PER_PAGE

#     connection = User.get_db_connection()
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute('''
                SELECT l.*, u.name as user_name 
                FROM logs l 
                JOIN users u ON l.user_id = u.id 
                ORDER BY l.created_at DESC 
                LIMIT %s OFFSET %s
            ''', (per_page, (page - 1) * per_page))
            logs = cursor.fetchall()

            cursor.execute("SELECT COUNT(*) as count FROM logs")
            total_logs = cursor.fetchone()['count']

    finally:
        connection.close()

    return render_template('admin/logs.html', logs=logs, page=page, per_page=per_page, total_logs=total_logs)


@admin_bp.route('/profile')
@login_required
@admin_required
def profile():
    return render_template('admin/profile.html')