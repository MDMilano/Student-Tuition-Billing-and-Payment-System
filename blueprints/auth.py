from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from models.user import User
from utils.email_utils import send_otp_email
from utils.helpers import generate_otp, log_activity
import pymysql
from config import Config
from datetime import datetime, timedelta

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email')

        if not email:
            flash('Please enter your email address.', 'error')
            return render_template('forgot_password.html')

        # Check if user exists
        user = User.get_by_email(email)
        if not user:
            flash('If this email exists in our system, you will receive an OTP shortly.', 'info')
            return render_template('forgot_password.html')

        # Generate OTP
        otp = generate_otp()
        expires_at = datetime.now() + timedelta(minutes=10)

        # Save OTP to database
        connection = User.get_db_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute('''
                    INSERT INTO password_resets (email, otp, expires_at)
                    VALUES (%s, %s, %s)
                ''', (email, otp, expires_at))
                connection.commit()
        finally:
            connection.close()

        # Send OTP email
        if send_otp_email(email, otp):
            session['reset_email'] = email
            flash('OTP has been sent to your email address.', 'success')
            return redirect(url_for('auth.verify_otp'))
        else:
            flash('Failed to send OTP. Please try again.', 'error')

    return render_template('forgot_password.html')


@auth_bp.route('/verify-otp', methods=['GET', 'POST'])
def verify_otp():
    if 'reset_email' not in session:
        return redirect(url_for('auth.forgot_password'))

    if request.method == 'POST':
        otp = request.form.get('otp')

        if not otp:
            flash('Please enter the OTP.', 'error')
            return render_template('verify_otp.html')

        # Verify OTP
        connection = User.get_db_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute('''
                    SELECT id FROM password_resets 
                    WHERE email = %s AND otp = %s AND expires_at > NOW() AND is_used = FALSE
                    ORDER BY created_at DESC LIMIT 1
                ''', (session['reset_email'], otp))

                if cursor.fetchone():
                    session['otp_verified'] = True
                    return redirect(url_for('auth.reset_password'))
                else:
                    flash('Invalid or expired OTP.', 'error')
        finally:
            connection.close()

    return render_template('verify_otp.html')


@auth_bp.route('/reset-password', methods=['GET', 'POST'])
def reset_password():
    if 'reset_email' not in session or not session.get('otp_verified'):
        return redirect(url_for('auth.forgot_password'))

    if request.method == 'POST':
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')

        if not password or not confirm_password:
            flash('Please fill in all fields.', 'error')
            return render_template('reset_password.html')

        if password != confirm_password:
            flash('Passwords do not match.', 'error')
            return render_template('reset_password.html')

        if len(password) < 6:
            flash('Password must be at least 6 characters long.', 'error')
            return render_template('reset_password.html')

        # Update password
        if User.update_password(session['reset_email'], password):
            # Mark OTP as used
            connection = User.get_db_connection()
            try:
                with connection.cursor() as cursor:
                    cursor.execute('''
                        UPDATE password_resets SET is_used = TRUE 
                        WHERE email = %s AND is_used = FALSE
                    ''', (session['reset_email'],))
                    connection.commit()
            finally:
                connection.close()

            # Clear session
            session.pop('reset_email', None)
            session.pop('otp_verified', None)

            flash('Password has been reset successfully. Please login with your new password.', 'success')
            return redirect(url_for('index'))
        else:
            flash('Failed to reset password. Please try again.', 'error')

    return render_template('reset_password.html')