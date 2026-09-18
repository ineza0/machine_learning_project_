from db_config import get_db_connection
from werkzeug.security import generate_password_hash, check_password_hash


def register_user(username, password, question, answer):
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute('SELECT id FROM users WHERE username=%s', (username,))
        if cur.fetchone(): return False, 'Username already exists'
        cur.execute('''INSERT INTO users (username,password,security_question,security_answer)
                       VALUES (%s,%s,%s,%s)''',
                    (username, generate_password_hash(password), question,
                     generate_password_hash(answer.lower().strip())))
        conn.commit(); return True, 'Account created'
    except Exception as e:
        conn.rollback(); return False, str(e)
    finally: cur.close(); conn.close()


def login_user(username, password):
    conn = get_db_connection(); cur = conn.cursor(dictionary=True)
    try:
        cur.execute('SELECT password FROM users WHERE username=%s', (username,))
        user = cur.fetchone()
        if not user: return False, 'Username not found'
        return (True, 'Login successful') if check_password_hash(user['password'], password) else (False, 'Wrong password')
    finally: cur.close(); conn.close()


def get_security_question(username):
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute('SELECT security_question FROM users WHERE username=%s', (username,))
        row = cur.fetchone(); return row[0] if row else None
    finally: cur.close(); conn.close()


def verify_security_answer(username, answer):
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute('SELECT security_answer FROM users WHERE username=%s', (username,))
        row = cur.fetchone()
        return bool(row and check_password_hash(row[0], answer.lower().strip()))
    finally: cur.close(); conn.close()


def force_reset_password(username, new_password):
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute('SELECT id FROM users WHERE username=%s', (username,))
        if not cur.fetchone(): return False, 'User not found'
        cur.execute('UPDATE users SET password=%s WHERE username=%s',
                    (generate_password_hash(new_password), username))
        conn.commit(); return True, 'Password reset successfully'
    except Exception as e:
        conn.rollback(); return False, str(e)
    finally: cur.close(); conn.close()
