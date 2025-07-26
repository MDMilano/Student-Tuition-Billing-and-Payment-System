import pymysql
from config import Config

def get_working_connection():
    for port in range(3306, 3310):
        try:
            connection = pymysql.connect(
                host=Config.MYSQL_HOST,
                port=port,
                user=Config.MYSQL_USER,
                password=Config.MYSQL_PASSWORD,
                connect_timeout=1
            )
            return connection, port
        except pymysql.err.OperationalError:
            print(f"❌ Failed to connect on port {port}, trying next...")
        except Exception as e:
            print(f"⚠️ Error on port {port}: {e}")

    raise Exception("Unable to connect to MySQL on ports 3306–3309")

def create_database():
    # connection = pymysql.connect(
    #     host=Config.MYSQL_HOST,
    #     user=Config.MYSQL_USER,
    #     password=Config.MYSQL_PASSWORD
    # )
    '''For automatically connecting to database without specific ports from 3306 hanggang 3309'''
    connection, working_port = get_working_connection()

    try:
        with connection.cursor() as cursor:
            cursor.execute(f"CREATE DATABASE IF NOT EXISTS {Config.MYSQL_DB}")
        connection.commit()
        print(f"Database '{Config.MYSQL_DB}' created successfully!")
    except Exception as e:
        print(f"Error creating database: {e}")
    finally:
        connection.close()

    return working_port

def create_tables(port):
    connection = pymysql.connect(
        host=Config.MYSQL_HOST,
        port=port,
        user=Config.MYSQL_USER,
        password=Config.MYSQL_PASSWORD,
        database=Config.MYSQL_DB
    )

    try:
        with connection.cursor() as cursor:
            # Users table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    name VARCHAR(100) NOT NULL,
                    email VARCHAR(100) UNIQUE NOT NULL,
                    password_hash VARCHAR(255) NOT NULL,
                    role ENUM('admin', 'cashier') NOT NULL,
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                )
            ''')

            # Courses table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS courses (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    name VARCHAR(100) NOT NULL,
                    code VARCHAR(20) UNIQUE NOT NULL,
                    price DECIMAL(10,2) NOT NULL,
                    description TEXT,
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                )
            ''')

            # Students table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS students (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    student_id VARCHAR(20) UNIQUE NOT NULL,
                    first_name VARCHAR(50) NOT NULL,
                    last_name VARCHAR(50) NOT NULL,
                    email VARCHAR(100) UNIQUE NOT NULL,
                    phone VARCHAR(20),
                    address TEXT,
                    course_id INT,
                    enrollment_date DATE,
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    FOREIGN KEY (course_id) REFERENCES courses(id)
                )
            ''')

            # ✅ Student Balances table (NEW)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS student_balances (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    student_id INT NOT NULL,
                    semester ENUM('1st', '2nd') NOT NULL,
                    from_year YEAR NOT NULL,
                    to_year YEAR NOT NULL,
                    total_due DECIMAL(10,2) NOT NULL DEFAULT 0.00,
                    total_paid DECIMAL(10,2) NOT NULL DEFAULT 0.00,
                    balance DECIMAL(10,2) AS (total_due - total_paid) STORED,
                    
                    -- ✅ Auto-calculated payment status
                    status VARCHAR(10) AS (
                        CASE
                            WHEN total_paid = 0 THEN 'unpaid'
                            WHEN total_paid >= total_due THEN 'paid'
                            ELSE 'partial'
                        END
                    ) STORED,
                
                    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                
                    FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE,
                    CONSTRAINT unique_term_balance UNIQUE (student_id, semester, from_year, to_year)
                );
            ''')

            # Payments table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS payments (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    student_id INT NOT NULL,
                    billing_id INT,  -- Nullable FK to student_balances
                    amount_paid DECIMAL(10,2) NOT NULL,
                    payment_method ENUM('cash', 'gcash', 'bank_transfer') NOT NULL,
                    reference_number VARCHAR(50),
                    payment_date DATE NOT NULL,
                    collected_by INT NOT NULL,
                    notes TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (student_id) REFERENCES students(id),
                    FOREIGN KEY (billing_id) REFERENCES student_balances(id) ON DELETE SET NULL,
                    FOREIGN KEY (collected_by) REFERENCES users(id)
                )
            ''')

            # Logs table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS logs (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    user_id INT NOT NULL,
                    action VARCHAR(255) NOT NULL,
                    role TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
            ''')

            # Password resets table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS password_resets (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    email VARCHAR(100) NOT NULL,
                    otp VARCHAR(6) NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expires_at TIMESTAMP DEFAULT (CURRENT_TIMESTAMP + INTERVAL 15 MINUTE),
                    is_used BOOLEAN DEFAULT FALSE
                )
            ''')

        connection.commit()
        print("All tables created successfully!")

        # Create default admin user
        create_default_admin(connection)

    except Exception as e:
        print(f"Error creating tables: {e}")
    finally:
        connection.close()


def create_default_admin(connection):
    from werkzeug.security import generate_password_hash

    try:
        with connection.cursor() as cursor:
            # Check if admin exists
            cursor.execute("SELECT id FROM users WHERE email = 'admin@school.com'")
            if cursor.fetchone():
                print("Default admin already exists!")
                return

            # Create default admin
            password_hash = generate_password_hash('admin123')
            cursor.execute('''
                INSERT INTO users (name, email, password_hash, role)
                VALUES ('System Administrator', 'admin@school.com', %s, 'admin')
            ''', (password_hash,))

            connection.commit()
            print("Default admin created: admin@school.com / admin123")

    except Exception as e:
        print(f"Error creating default admin: {e}")

def get_db_connection():
    _, port = get_working_connection()
    return pymysql.connect(
        host=Config.MYSQL_HOST,
        port=port,
        user=Config.MYSQL_USER,
        password=Config.MYSQL_PASSWORD,
        database=Config.MYSQL_DB,
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True
    )


if __name__ == '__main__':
    # create_database()
    # create_tables()

    working_port = create_database()
    create_tables(working_port)