import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from config import Config

def send_otp_email(email, otp):
    try:
        # Create message
        msg = MIMEMultipart()
        msg['From'] = Config.MAIL_USERNAME
        msg['To'] = email
        msg['Subject'] = "Password Reset OTP - Student Billing System"

        # Email body
        body = f'''
        <html>
        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
            <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
                <div style="background: linear-gradient(135deg, #4dd0e1 0%, #26c6da 100%); padding: 30px; text-align: center; border-radius: 10px 10px 0 0;">
                    <h1 style="color: white; margin: 0;">Student Billing System</h1>
                    <p style="color: white; margin: 10px 0 0 0;">Password Reset Request</p>
                </div>

                <div style="background: white; padding: 30px; border: 1px solid #ddd; border-radius: 0 0 10px 10px;">
                    <p>Hello,</p>
                    <p>You have requested to reset your password. Please use the following OTP to proceed:</p>

                    <div style="text-align: center; margin: 30px 0;">
                        <div style="background: #f8f9fa; border: 2px dashed #4dd0e1; padding: 20px; border-radius: 10px; display: inline-block;">
                            <span style="font-size: 32px; font-weight: bold; color: #4dd0e1; letter-spacing: 5px;">{otp}</span>
                        </div>
                    </div>

                    <p><strong>Important:</strong></p>
                    <ul>
                        <li>This OTP is valid for 10 minutes only</li>
                        <li>Do not share this OTP with anyone</li>
                        <li>If you didn't request this, please ignore this email</li>
                    </ul>

                    <hr style="border: none; border-top: 1px solid #eee; margin: 30px 0;">

                    <p style="font-size: 12px; color: #666; text-align: center;">
                        This is an automated email. Please do not reply to this message.<br>
                        © 2025 Student Billing System
                    </p>
                </div>
            </div>
        </body>
        </html>
        '''

        msg.attach(MIMEText(body, 'html'))

        # Connect to server and send email
        server = smtplib.SMTP(Config.MAIL_SERVER, Config.MAIL_PORT)
        server.starttls()
        server.login(Config.MAIL_USERNAME, Config.MAIL_PASSWORD)

        text = msg.as_string()
        server.sendmail(Config.MAIL_USERNAME, email, text)
        server.quit()

        return True
    except Exception as e:
        print(f"Error sending email: {e}")
        return False


def send_login_credentials_email(name, email, temporary_password):
    """Send login credentials to newly created cashier account"""
    try:
        # Create message
        msg = MIMEMultipart()
        msg['From'] = Config.MAIL_USERNAME
        msg['To'] = email
        msg['Subject'] = "Welcome to Student Billing System - Login Credentials"

        # Email body
        body = f'''
        <html>
        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
            <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
                <div style="background: linear-gradient(135deg, #4dd0e1 0%, #26c6da 100%); padding: 30px; text-align: center; border-radius: 10px 10px 0 0;">
                    <h1 style="color: white; margin: 0;">Student Billing System</h1>
                    <p style="color: white; margin: 10px 0 0 0;">Welcome to the Team!</p>
                </div>

                <div style="background: white; padding: 30px; border: 1px solid #ddd; border-radius: 0 0 10px 10px;">
                    <p>Hello <strong>{name}</strong>,</p>
                    <p>Welcome to the Student Billing System! Your cashier account has been successfully created. Below are your login credentials:</p>

                    <div style="text-align: center; margin: 30px 0;">
                        <div style="background: #f8f9fa; border: 2px dashed #4dd0e1; padding: 20px; border-radius: 10px; display: inline-block;">
                            <div style="margin-bottom: 15px;">
                                <strong style="color: #4dd0e1;">Email Address:</strong><br>
                                <span style="font-size: 16px; font-weight: bold; color: #333;">{email}</span>
                            </div>
                            <div>
                                <strong style="color: #4dd0e1;">Temporary Password:</strong><br>
                                <span style="font-size: 24px; font-weight: bold; color: #4dd0e1; letter-spacing: 2px; font-family: monospace;">{temporary_password}</span>
                            </div>
                        </div>
                    </div>

                    <p><strong>Getting Started:</strong></p>
                    <ul>
                        <li>Use the credentials above to log into your account</li>
                        <li>Change your password immediately after login</li>
                        <li>Familiarize yourself with the cashier dashboard</li>
                        <li>Contact your administrator if you need assistance</li>
                    </ul>

                    <p><strong>Important:</strong></p>
                    <ul>
                        <li>Keep your login credentials secure and confidential</li>
                        <li>Do not share your password with anyone</li>
                        <li>Change your password immediately after first login</li>
                        <li>Log out properly after each session</li>
                    </ul>

                    <hr style="border: none; border-top: 1px solid #eee; margin: 30px 0;">

                    <p style="font-size: 12px; color: #666; text-align: center;">
                        This is an automated email. Please do not reply to this message.<br>
                        If you have any questions, please contact your system administrator.<br>
                        © 2025 Student Billing System
                    </p>
                </div>
            </div>
        </body>
        </html>
        '''

        msg.attach(MIMEText(body, 'html'))

        # Connect to server and send email
        server = smtplib.SMTP(Config.MAIL_SERVER, Config.MAIL_PORT)
        server.starttls()
        server.login(Config.MAIL_USERNAME, Config.MAIL_PASSWORD)

        text = msg.as_string()
        server.sendmail(Config.MAIL_USERNAME, email, text)
        server.quit()

        return True
    except Exception as e:
        print(f"Error sending login credentials email: {e}")
        return False