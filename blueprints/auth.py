from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify
from models.user import User
from utils.email_utils import send_otp_email
from utils.helpers import generate_otp, log_activity
import pymysql
from config import Config
from datetime import datetime, timedelta
from database.init_db import get_db_connection

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
        connection = get_db_connection()
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
        connection = get_db_connection()
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


@auth_bp.route('/resend-otp', methods=['POST'])
def resend_otp():
    """Resend OTP for password reset"""
    if 'reset_email' not in session:
        return jsonify({'success': False, 'message': 'Session expired. Please start over.'}), 400

    email = session['reset_email']

    # Check rate limiting - only allow resend every 60 seconds
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            # Check if there's a recent OTP (within last minute)
            cursor.execute('''
                SELECT created_at FROM password_resets 
                WHERE email = %s AND created_at > %s
                ORDER BY created_at DESC LIMIT 1
            ''', (email, datetime.now() - timedelta(minutes=1)))

            recent_otp = cursor.fetchone()
            if recent_otp:
                return jsonify({
                    'success': False,
                    'message': 'Please wait 60 seconds before requesting another OTP.'
                }), 429

            # Generate new OTP
            otp = generate_otp()
            expires_at = datetime.now() + timedelta(minutes=10)

            # Mark old OTPs as used
            cursor.execute('''
                UPDATE password_resets SET is_used = TRUE 
                WHERE email = %s AND is_used = FALSE
            ''', (email,))

            # Insert new OTP
            cursor.execute('''
                INSERT INTO password_resets (email, otp, expires_at)
                VALUES (%s, %s, %s)
            ''', (email, otp, expires_at))

            connection.commit()

            # Send OTP email
            if send_otp_email(email, otp):
                return jsonify({
                    'success': True,
                    'message': 'New OTP has been sent to your email address.'
                })
            else:
                return jsonify({
                    'success': False,
                    'message': 'Failed to send OTP. Please try again.'
                }), 500

    except Exception as e:
        connection.rollback()
        return jsonify({
            'success': False,
            'message': 'An error occurred. Please try again.'
        }), 500
    finally:
        connection.close()


@auth_bp.route('/reset-password', methods=['GET', 'POST'])
def reset_password():
    if 'reset_email' not in session or not session.get('otp_verified'):
        return redirect(url_for('auth.forgot_password'))

    if request.method == 'POST':
        password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')

        if not password or not confirm_password:
            flash('Please fill in all fields.', 'error')
            return render_template('reset_password.html')

        if password != confirm_password:
            flash('Passwords do not match.', 'error')
            return render_template('reset_password.html')

        # Enhanced password validation to match frontend requirements
        if len(password) < 8:
            flash('Password must be at least 8 characters long.', 'error')
            return render_template('reset_password.html')

        if not any(c.isupper() for c in password):
            flash('Password must contain at least one uppercase letter.', 'error')
            return render_template('reset_password.html')

        if not any(c.islower() for c in password):
            flash('Password must contain at least one lowercase letter.', 'error')
            return render_template('reset_password.html')

        if not any(c.isdigit() for c in password):
            flash('Password must contain at least one number.', 'error')
            return render_template('reset_password.html')

        # Update password
        if User.update_password(session['reset_email'], password):
            # Mark OTP as used
            connection = get_db_connection()
            try:
                with connection.cursor() as cursor:
                    cursor.execute('''
                        UPDATE password_resets SET is_used = TRUE 
                        WHERE email = %s AND is_used = FALSE
                    ''', (session['reset_email'],))
                    connection.commit()
            finally:
                connection.close()

            # Log the activity
            try:
                log_activity(session['reset_email'], 'password_reset', 'Password reset successfully')
            except:
                pass  # Don't fail if logging fails

            # Clear session
            session.pop('reset_email', None)
            session.pop('otp_verified', None)

            flash('Password has been reset successfully. Please login with your new password.', 'success')
            return redirect(url_for('index'))
        else:
            flash('Failed to reset password. Please try again.', 'error')

    return render_template('reset_password.html')