
from db_config import get_db_connection
from werkzeug.security import generate_password_hash, check_password_hash


def register_user(
    username,
    password,
    question=None,
    answer=None,
    full_name=None,
    email=None
):
    """Register a new user while preserving compatibility with old calls."""

    username = (username or "").strip()
    full_name = (full_name or "").strip() or None
    email = (email or "").strip().lower() or None

    if not username:
        return False, "Username is required"

    if not password or len(password) < 8:
        return False, "Password must contain at least 8 characters"

    question = (question or "").strip() or None
    answer = (answer or "").strip().lower() or None

    conn = None
    cur = None

    try:
        conn = get_db_connection()
        cur = conn.cursor(dictionary=True)

        # Check username
        cur.execute(
            "SELECT id FROM users WHERE username = %s",
            (username,)
        )
        if cur.fetchone():
            return False, "Username already exists"

        # Check email when provided
        if email:
            cur.execute(
                "SELECT id FROM users WHERE email = %s",
                (email,)
            )
            if cur.fetchone():
                return False, "Email already exists"

        hashed_password = generate_password_hash(password)

        hashed_answer = (
            generate_password_hash(answer) if answer else None
        )

        # Do not accept a privileged role from the signup form.
        # School roles must be granted through an authorized workflow.
        cur.execute(
            """
            INSERT INTO users
                (
                    username,
                    email,
                    password,
                    full_name,
                    role,
                    security_question,
                    security_answer
                )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                username,
                email,
                hashed_password,
                full_name,
                "student",
                question,
                hashed_answer
            )
        )

        conn.commit()
        return True, "Account created successfully"

    except Exception as e:
        if conn:
            conn.rollback()

        # Keep technical details in server logs, not in the user response.
        print(f"Registration error: {type(e).__name__}: {e}")
        return False, "Registration failed. Check your details and try again."

    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def login_user(username, password):
    conn = None
    cur = None

    try:
        conn = get_db_connection()
        cur = conn.cursor(dictionary=True)

        cur.execute(
            "SELECT password FROM users WHERE username = %s",
            ((username or "").strip(),)
        )
        user = cur.fetchone()

        if not user:
            return False, "Username not found"

        if check_password_hash(user["password"], password or ""):
            return True, "Login successful"

        return False, "Wrong password"

    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def get_security_question(username):
    conn = None
    cur = None

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute(
            "SELECT security_question FROM users WHERE username = %s",
            ((username or "").strip(),)
        )
        row = cur.fetchone()

        return row[0] if row else None

    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def verify_security_answer(username, answer):
    if not answer:
        return False

    conn = None
    cur = None

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute(
            "SELECT security_answer FROM users WHERE username = %s",
            ((username or "").strip(),)
        )
        row = cur.fetchone()

        if not row or not row[0]:
            return False

        return check_password_hash(
            row[0],
            answer.strip().lower()
        )

    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def force_reset_password(username, new_password):
    username = (username or "").strip()

    if not new_password or len(new_password) < 8:
        return False, "Password must contain at least 8 characters"

    conn = None
    cur = None

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute(
            "SELECT id FROM users WHERE username = %s",
            (username,)
        )

        if not cur.fetchone():
            return False, "User not found"

        cur.execute(
            "UPDATE users SET password = %s WHERE username = %s",
            (
                generate_password_hash(new_password),
                username
            )
        )

        conn.commit()
        return True, "Password reset successfully"

    except Exception as e:
        if conn:
            conn.rollback()

        print(f"Password reset error: {type(e).__name__}: {e}")
        return False, "Password reset failed. Please try again."

    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()
