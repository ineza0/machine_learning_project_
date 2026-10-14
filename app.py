
import os
import hashlib
import secrets
import string
import traceback

import joblib
from flask import (
    Flask, request, jsonify, render_template, session,
    redirect, url_for, send_file, Response
)
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import generate_password_hash

from auth import *
from db_config import get_db_connection
from report import generate_pdf_report, generate_excel_report


# =========================================================
# APPLICATION CONFIGURATION
# =========================================================
app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

app.secret_key = os.getenv("SECRET_KEY", "change-this-secret-key")

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("FLASK_ENV") == "production"
)


# =========================================================
# GOOGLE SEARCH CONSOLE VERIFICATION
# =========================================================
GOOGLE_VERIFICATION_FILENAME = os.getenv(
    "GOOGLE_VERIFICATION_FILENAME", ""
).strip().lstrip("/")

GOOGLE_VERIFICATION_CONTENT = os.getenv(
    "GOOGLE_VERIFICATION_CONTENT", ""
).strip()

if (
    GOOGLE_VERIFICATION_FILENAME.startswith("google")
    and GOOGLE_VERIFICATION_FILENAME.endswith(".html")
    and "/" not in GOOGLE_VERIFICATION_FILENAME
    and "\\" not in GOOGLE_VERIFICATION_FILENAME
):
    def google_verification():
        return Response(
            GOOGLE_VERIFICATION_CONTENT,
            mimetype="text/html"
        )

    app.add_url_rule(
        "/" + GOOGLE_VERIFICATION_FILENAME,
        endpoint="google_verification",
        view_func=google_verification,
        methods=["GET"]
    )


# =========================================================
# LOAD MACHINE LEARNING MODEL
# =========================================================
try:
    model = joblib.load("student_model.pkl")
    print("ML model loaded")
except Exception as e:
    print(f"Model not loaded: {e}")
    model = None


# =========================================================
# GOOGLE SIGN-IN
# =========================================================
GOOGLE_ON = bool(
    os.getenv("GOOGLE_CLIENT_ID")
    and os.getenv("GOOGLE_CLIENT_SECRET")
)

if GOOGLE_ON:
    from authlib.integrations.flask_client import OAuth

    oauth = OAuth(app)

    oauth.register(
        name="google",
        client_id=os.getenv("GOOGLE_CLIENT_ID"),
        client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
        server_metadata_url=(
            "https://accounts.google.com/"
            ".well-known/openid-configuration"
        ),
        client_kwargs={"scope": "openid email profile"}
    )


def google_get_or_create_user(email, name):
    username = (
        email if len(email) <= 50
        else "g_" + hashlib.sha256(
            email.encode()
        ).hexdigest()[:20]
    )

    conn = get_db_connection()
    cur = conn.cursor(dictionary=True)

    try:
        cur.execute(
            "SELECT username FROM users WHERE email=%s LIMIT 1",
            (email,)
        )
        row = cur.fetchone()

        if row:
            return row["username"]

        cur.execute(
            "SELECT username FROM users WHERE username=%s LIMIT 1",
            (username,)
        )

        if cur.fetchone():
            username = "g_" + secrets.token_hex(12)

        random_value = hashlib.sha256(
            secrets.token_bytes(32)
        ).hexdigest()

        cur.execute(
            """
            INSERT INTO users
                (username, email, password, full_name, role)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                username,
                email,
                generate_password_hash(random_value),
                name,
                "user"
            )
        )

        conn.commit()
        return username

    finally:
        cur.close()
        conn.close()


@app.route("/auth/google")
def google_login():
    if not GOOGLE_ON:
        return redirect("/login?error=google_disabled")

    return oauth.google.authorize_redirect(
        request.url_root.rstrip("/") + "/auth/google/callback"
    )


@app.route("/auth/google/callback")
def google_callback():
    if not GOOGLE_ON:
        return redirect("/login?error=google_disabled")

    try:
        info = (
            oauth.google.authorize_access_token().get("userinfo") or {}
        )

        email = (info.get("email") or "").strip().lower()

        if not email or not info.get("email_verified"):
            return redirect("/login?error=google_unverified")

        name = info.get("name") or email.split("@")[0]

        session.clear()
        session["username"] = google_get_or_create_user(email, name)

        return redirect(url_for("dashboard"))

    except Exception:
        app.logger.exception("Google sign-in failed")
        return redirect("/login?error=google_failed")


# =========================================================
# COMMON PAGE ROUTES
# =========================================================
@app.route("/")
def home():
    if "username" in session:
        return redirect(url_for("dashboard"))

    return redirect(url_for("login_page"))


@app.route("/login")
def login_page():
    return render_template("login.html")


@app.route("/signup")
def signup_page():
    return render_template("signup.html")


@app.route("/forgot")
def forgot_page():
    return render_template("forgot.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login_page"))


# =========================================================
# ROLE-BASED DASHBOARD
# =========================================================
@app.route("/dashboard")
def dashboard():
    if "username" not in session:
        return redirect(url_for("login_page"))

    conn = None
    cur = None

    try:
        conn = get_db_connection()
        cur = conn.cursor(dictionary=True)

        cur.execute(
            """
            SELECT id, username, full_name, role
            FROM users
            WHERE username = %s
            LIMIT 1
            """,
            (session["username"],)
        )

        user = cur.fetchone()

        if not user:
            session.clear()
            return redirect(url_for("login_page"))

        # The global platform role stored in users.role takes priority.
        # A school membership must never downgrade PLATFORM_ADMIN.
        account_role = str(user.get("role") or "USER").strip().upper()
        platform_roles = {"PLATFORM_ADMIN", "SUPER_ADMIN", "ADMIN"}

        role = account_role
        if account_role not in platform_roles:
            cur.execute(
                """
                SELECT member_role
                FROM school_members
                WHERE user_id = %s
                ORDER BY id DESC
                LIMIT 1
                """,
                (user["id"],)
            )
            membership = cur.fetchone()
            member_role = str(
                (membership or {}).get("member_role") or ""
            ).strip().upper()

            if member_role in {"STUDENT", "TEACHER", "HEADMASTER"}:
                role = member_role

        # Normalize legacy global admin role to the intended dashboard role.
        if role in {"SUPER_ADMIN", "ADMIN"}:
            role = "PLATFORM_ADMIN"

        session["user_id"] = user["id"]
        session["role"] = role

        templates = {
            "STUDENT": "student_dashboard.html",
            "TEACHER": "teacher_dashboard.html",
            "HEADMASTER": "headmaster_dashboard.html",
            "PLATFORM_ADMIN": "platform_admin_dashboard.html",
            "SUPER_ADMIN": "platform_admin_dashboard.html",
            "ADMIN": "platform_admin_dashboard.html"
        }

        template = templates.get(role, "index.html")

        template_path = os.path.join(
            app.root_path,
            "templates",
            template
        )

        # Use the existing ML dashboard until a role template exists.
        if not os.path.isfile(template_path):
            template = "index.html"

        return render_template(
            template,
            username=user["username"],
            full_name=user.get("full_name") or user["username"],
            role=role
        )

    except Exception:
        app.logger.exception("Could not load dashboard")
        return "Could not load your dashboard. Please try again.", 500

    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


# =========================================================
# ERROR HELPER
# =========================================================
def server_error(e):
    app.logger.exception("Server error: %s", e)

    return jsonify({
        "success": False,
        "message": "Server error. Please try again."
    }), 500


# =========================================================
# LOGIN API
# =========================================================
@app.route("/api/login", methods=["POST"])
def api_login():
    try:
        data = request.get_json(silent=True) or {}

        username = str(data.get("username", "")).strip()
        password = data.get("password", "")

        if not username or not isinstance(password, str) or not password:
            return jsonify({
                "success": False,
                "message": "Enter your username and password."
            }), 400

        ok, msg = login_user(username, password)

        if ok:
            session.clear()
            session["username"] = username

            return jsonify({
                "success": True,
                "message": msg,
                "redirect": url_for("dashboard")
            })

        return jsonify({
            "success": False,
            "message": msg
        }), 401

    except Exception as e:
        return server_error(e)


# =========================================================
# UNIQUE SCHOOL CODE
# =========================================================
def generate_unique_school_code(cur):
    alphabet = string.ascii_uppercase + string.digits

    for _ in range(10):
        code = "SP-" + "".join(
            secrets.choice(alphabet) for _ in range(10)
        )

        cur.execute(
            "SELECT id FROM schools WHERE school_code=%s LIMIT 1",
            (code,)
        )

        if not cur.fetchone():
            return code

    raise RuntimeError("Could not generate a unique school code.")


# =========================================================
# SIGNUP API
# HEADMASTER CREATES A SCHOOL.
# TEACHER/STUDENT JOIN USING AN INVITATION.
# =========================================================
@app.route("/api/signup", methods=["POST"])
def api_signup():
    conn = None
    cur = None

    try:
        data = request.get_json(silent=True) or {}

        full_name = str(data.get("full_name", "")).strip()
        username = str(data.get("username", "")).strip()
        email = str(data.get("email", "")).strip().lower()
        password = data.get("password", "")
        requested_role = str(
            data.get("role", "")
        ).strip().upper()

        terms = data.get("terms", False)

        invitation_code = str(
            data.get("invitation_code", "")
        ).strip()

        school_name = str(
            data.get("school_name", "")
        ).strip()

        education_level = str(
            data.get("education_level", "")
        ).strip().upper()

        approval_mode = str(
            data.get("approval_mode", "HEADMASTER")
        ).strip().upper()

        if not all([full_name, username, email, password]):
            return jsonify({
                "success": False,
                "message": "Please complete all required fields."
            }), 400

        if not isinstance(password, str) or len(password) < 8:
            return jsonify({
                "success": False,
                "message": "Password must contain at least 8 characters."
            }), 400

        if (
            len(full_name) > 200
            or len(username) > 100
            or len(email) > 150
        ):
            return jsonify({
                "success": False,
                "message": "One or more fields are too long."
            }), 400

        if requested_role not in (
            "STUDENT", "TEACHER", "HEADMASTER"
        ):
            return jsonify({
                "success": False,
                "message": "Choose a valid account role."
            }), 400

        terms_accepted = (
            terms is True
            or terms == 1
            or (
                isinstance(terms, str)
                and terms.strip().lower() in ("true", "on", "1")
            )
        )

        if not terms_accepted:
            return jsonify({
                "success": False,
                "message": "Please confirm the account information."
            }), 400

        if requested_role == "HEADMASTER":
            valid_levels = {
                "PRIMARY", "O_LEVEL", "A_LEVEL",
                "UNIVERSITY", "MIXED"
            }

            if not school_name or len(school_name) > 200:
                return jsonify({
                    "success": False,
                    "message": "Enter a valid school name."
                }), 400

            if education_level not in valid_levels:
                return jsonify({
                    "success": False,
                    "message": "Choose a valid education level."
                }), 400

            if approval_mode not in ("TEACHER", "HEADMASTER"):
                return jsonify({
                    "success": False,
                    "message": "Choose a valid grade approval method."
                }), 400

        elif not invitation_code:
            return jsonify({
                "success": False,
                "message": "School invitation code is required."
            }), 400

        conn = get_db_connection()
        cur = conn.cursor(dictionary=True)
        conn.start_transaction()

        cur.execute(
            "SELECT id FROM users WHERE username=%s LIMIT 1",
            (username,)
        )

        if cur.fetchone():
            conn.rollback()
            return jsonify({
                "success": False,
                "message": "Username already exists."
            }), 409

        cur.execute(
            "SELECT id FROM users WHERE email=%s LIMIT 1",
            (email,)
        )

        if cur.fetchone():
            conn.rollback()
            return jsonify({
                "success": False,
                "message": "Email address already exists."
            }), 409

        school_id = None
        class_id = None
        invitation_id = None

        if requested_role in ("STUDENT", "TEACHER"):
            cur.execute(
                """
                SELECT
                    id, school_id, invited_role, email,
                    class_id, used_by, expires_at
                FROM school_invitations
                WHERE invitation_code=%s
                LIMIT 1
                FOR UPDATE
                """,
                (invitation_code,)
            )

            invitation = cur.fetchone()

            if not invitation:
                conn.rollback()
                return jsonify({
                    "success": False,
                    "message": "Invalid school invitation code."
                }), 400

            if invitation["used_by"] is not None:
                conn.rollback()
                return jsonify({
                    "success": False,
                    "message": "This invitation code has already been used."
                }), 400

            cur.execute("SELECT CURRENT_TIMESTAMP AS db_now")
            db_now = cur.fetchone()["db_now"]
            expires_at = invitation.get("expires_at")

            if expires_at is not None and expires_at <= db_now:
                conn.rollback()
                return jsonify({
                    "success": False,
                    "message": "This invitation code has expired."
                }), 400

            invited_role = str(
                invitation["invited_role"]
            ).strip().upper()

            if invited_role not in ("STUDENT", "TEACHER"):
                conn.rollback()
                return jsonify({
                    "success": False,
                    "message": "This invitation is not valid for signup."
                }), 403

            if requested_role != invited_role:
                conn.rollback()
                return jsonify({
                    "success": False,
                    "message": "Your role does not match the invitation."
                }), 403

            invited_email = (
                invitation.get("email") or ""
            ).strip().lower()

            if invited_email and invited_email != email:
                conn.rollback()
                return jsonify({
                    "success": False,
                    "message": (
                        "This invitation is assigned to another email."
                    )
                }), 403

            school_id = invitation["school_id"]
            class_id = invitation["class_id"]
            invitation_id = invitation["id"]

            cur.execute(
                "SELECT id FROM schools WHERE id=%s LIMIT 1",
                (school_id,)
            )

            if not cur.fetchone():
                conn.rollback()
                return jsonify({
                    "success": False,
                    "message": "The school was not found."
                }), 400

        invited_role = requested_role
        password_hash = generate_password_hash(password)

        cur.execute(
            """
            INSERT INTO users
                (username, email, password, full_name, role)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                username,
                email,
                password_hash,
                full_name,
                invited_role.lower()
            )
        )

        user_id = cur.lastrowid

        if invited_role == "HEADMASTER":
            school_code = generate_unique_school_code(cur)

            cur.execute(
                """
                INSERT INTO schools
                    (
                        school_name, school_code,
                        education_level, approval_mode
                    )
                VALUES (%s, %s, %s, %s)
                """,
                (
                    school_name,
                    school_code,
                    education_level,
                    approval_mode
                )
            )

            school_id = cur.lastrowid

            cur.execute(
                """
                INSERT INTO school_members
                    (school_id, user_id, member_role)
                VALUES (%s, %s, 'HEADMASTER')
                """,
                (school_id, user_id)
            )

        else:
            cur.execute(
                """
                INSERT INTO school_members
                    (school_id, user_id, member_role)
                VALUES (%s, %s, %s)
                """,
                (school_id, user_id, invited_role)
            )

            if invited_role == "STUDENT":
                cur.execute(
                    """
                    INSERT INTO students
                        (student_name, user_id, school_id, class_id)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (full_name, user_id, school_id, class_id)
                )

            cur.execute(
                """
                UPDATE school_invitations
                SET used_by=%s,
                    used_at=CURRENT_TIMESTAMP
                WHERE id=%s
                  AND used_by IS NULL
                  AND (
                      expires_at IS NULL
                      OR expires_at > CURRENT_TIMESTAMP
                  )
                """,
                (user_id, invitation_id)
            )

            if cur.rowcount != 1:
                raise RuntimeError(
                    "Invitation was already used or has expired."
                )

        conn.commit()

        return jsonify({
            "success": True,
            "message": "Account created successfully. You can now log in.",
            "redirect": url_for("login_page")
        }), 201

    except Exception:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass

        app.logger.exception("Signup failed")

        return jsonify({
            "success": False,
            "message": (
                "Registration failed. Please check your details "
                "and try again."
            )
        }), 500

    finally:
        if cur:
            try:
                cur.close()
            except Exception:
                pass

        if conn:
            try:
                conn.close()
            except Exception:
                pass


# =========================================================
# FORGOT PASSWORD: GET SECURITY QUESTION
# =========================================================
@app.route("/api/get-security-question", methods=["POST"])
def get_question():
    try:
        data = request.get_json(silent=True) or {}
        username = str(data.get("username", "")).strip()

        if not username:
            return jsonify({
                "success": False,
                "message": "Enter your username."
            }), 400

        question = get_security_question(username)

        if question:
            return jsonify({
                "success": True,
                "question": question
            })

        return jsonify({
            "success": False,
            "message": "User not found."
        }), 404

    except Exception as e:
        return server_error(e)


# =========================================================
# FORGOT PASSWORD: VERIFY ANSWER
# =========================================================
@app.route("/api/verify-security-answer", methods=["POST"])
def verify_ans():
    try:
        data = request.get_json(silent=True) or {}

        username = str(data.get("username", "")).strip()
        answer = data.get("answer", "")

        if not username or not isinstance(answer, str) or not answer.strip():
            return jsonify({
                "success": False,
                "message": "Enter your username and security answer."
            }), 400

        ok = verify_security_answer(username, answer)

        return jsonify({
            "success": bool(ok),
            "message": (
                "Security answer verified."
                if ok else "Incorrect username or security answer."
            )
        }), (200 if ok else 400)

    except Exception as e:
        return server_error(e)


# =========================================================
# FORGOT PASSWORD: RESET PASSWORD
# =========================================================
@app.route("/api/reset-password", methods=["POST"])
def reset_pwd():
    try:
        data = request.get_json(silent=True) or {}

        username = str(data.get("username", "")).strip()
        answer = data.get("answer", "")
        new_password = data.get("new_password", "")
        confirm_password = data.get("confirm_password", "")

        if not username:
            return jsonify({
                "success": False,
                "message": "Enter your username."
            }), 400

        if not isinstance(answer, str) or not answer.strip():
            return jsonify({
                "success": False,
                "message": "Enter your security answer."
            }), 400

        if not isinstance(new_password, str) or len(new_password) < 8:
            return jsonify({
                "success": False,
                "message": "Password must contain at least 8 characters."
            }), 400

        if not isinstance(confirm_password, str):
            return jsonify({
                "success": False,
                "message": "Confirm your new password."
            }), 400

        if new_password != confirm_password:
            return jsonify({
                "success": False,
                "message": "The passwords do not match."
            }), 400

        # Always verify the answer again before resetting.
        if not verify_security_answer(username, answer):
            return jsonify({
                "success": False,
                "message": "Incorrect username or security answer."
            }), 400

        ok, msg = force_reset_password(username, new_password)

        return jsonify({
            "success": bool(ok),
            "message": msg
        }), (200 if ok else 400)

    except Exception as e:
        return server_error(e)


# =========================================================
# SYSTEM STATUS
# =========================================================
@app.route("/api/system-status")
def system_status():
    conn = None
    cur = None

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("SELECT COUNT(*) FROM users")
        users_count = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM predictions")
        predictions_count = cur.fetchone()[0]

        return jsonify({
            "success": True,
            "authentication": "Online",
            "ml_predictor": (
                "Ready" if model is not None else "Model missing"
            ),
            "database_name": "TiDB Cloud",
            "users": int(users_count),
            "predictions": int(predictions_count)
        })

    except Exception:
        app.logger.exception("System status failed")
        return jsonify({"success": False}), 500

    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


# =========================================================
# MACHINE LEARNING PREDICTION
# =========================================================
@app.route("/api/predict", methods=["POST"])
def predict():
    if "username" not in session:
        return jsonify({
            "success": False,
            "error": "Login required"
        }), 401

    if model is None:
        return jsonify({
            "success": False,
            "error": "Model not loaded. Check student_model.pkl."
        }), 503

    conn = None
    cur = None

    try:
        student_name = request.form.get("student_name", "").strip()
        subject = request.form.get("subject", "").strip()

        if not student_name or not subject:
            return jsonify({
                "success": False,
                "error": "Student name and subject are required."
            }), 400

        try:
            hours = float(request.form["study_hours"])
            attendance = float(request.form["attendance"])
            assignment = float(request.form["assignment_score"])
            cat = float(request.form["cat_score"])
            practical = float(request.form["practical_score"])
        except (KeyError, TypeError, ValueError):
            return jsonify({
                "success": False,
                "error": "Fill in every field using valid numbers."
            }), 400

        values = (attendance, assignment, cat, practical)

        if not (
            0 <= hours <= 20
            and all(0 <= value <= 100 for value in values)
        ):
            return jsonify({
                "success": False,
                "error": "Check the score ranges."
            }), 400

        features = [[hours, attendance, assignment, cat, practical]]
        prediction_value = int(model.predict(features)[0])

        probability = 0.0

        if hasattr(model, "predict_proba"):
            probabilities = model.predict_proba(features)[0]
            probability = float(probabilities[prediction_value] * 100)

        result = "PASS" if prediction_value == 1 else "FAIL"

        conn = get_db_connection()
        cur = conn.cursor()

        # Find a matching student record.
        cur.execute(
            """
            SELECT id
            FROM students
            WHERE student_name=%s
            LIMIT 1
            """,
            (student_name,)
        )

        row = cur.fetchone()

        if row:
            student_id = row[0]
        else:
            # Legacy predictor support.
            # This does not link the new record to a school or account.
            cur.execute(
                "INSERT INTO students (student_name) VALUES (%s)",
                (student_name,)
            )
            student_id = cur.lastrowid

        cur.execute(
            """
            INSERT INTO predictions
            (
                student_id, username, subject, study_hours,
                attendance, assignment, cat, practical,
                prediction, probability
            )
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                student_id,
                session["username"],
                subject,
                hours,
                attendance,
                assignment,
                cat,
                practical,
                result,
                round(probability / 100, 4)
            )
        )

        conn.commit()

        return jsonify({
            "success": True,
            "student_name": student_name,
            "subject": subject,
            "result": result,
            "confidence": round(probability, 2)
        })

    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        return server_error(e)

    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


# =========================================================
# PREDICTION HISTORY
# =========================================================
@app.route("/api/history")
def history():
    if "username" not in session:
        return jsonify([]), 401

    conn = None
    cur = None

    try:
        conn = get_db_connection()
        cur = conn.cursor(dictionary=True)

        cur.execute(
            """
            SELECT
                p.created_at AS timestamp,
                COALESCE(s.student_name, 'Unknown') AS student_name,
                p.subject,
                p.study_hours,
                p.attendance,
                p.assignment,
                p.cat,
                p.practical,
                p.prediction,
                ROUND(p.probability * 100, 2) AS confidence
            FROM predictions p
            LEFT JOIN students s ON p.student_id = s.id
            WHERE p.username = %s
            ORDER BY p.id DESC
            """,
            (session["username"],)
        )

        rows = cur.fetchall()

        for row in rows:
            if row.get("timestamp"):
                row["timestamp"] = row["timestamp"].strftime(
                    "%Y-%m-%d %H:%M:%S"
                )

            for key in (
                "study_hours", "attendance", "assignment",
                "cat", "practical", "confidence"
            ):
                if row.get(key) is not None:
                    row[key] = float(row[key])

        return jsonify(rows)

    except Exception as e:
        return server_error(e)

    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


# =========================================================
# STATISTICS
# =========================================================
@app.route("/api/stats")
def stats():
    empty = {
        "success": True,
        "total": 0,
        "passed": 0,
        "failed": 0,
        "pass_rate": 0,
        "subjects": [],
        "trend": [],
        "result_chart": []
    }

    if "username" not in session:
        return jsonify({
            **empty,
            "success": False,
            "message": "Login required"
        }), 401

    conn = None
    cur = None

    try:
        conn = get_db_connection()
        cur = conn.cursor(dictionary=True)
        username = session["username"]

        cur.execute(
            """
            SELECT
                COUNT(*) AS total,
                COALESCE(SUM(prediction = 'PASS'), 0) AS passed,
                COALESCE(SUM(prediction = 'FAIL'), 0) AS failed
            FROM predictions
            WHERE username = %s
            """,
            (username,)
        )

        summary = cur.fetchone() or {}

        total = int(summary.get("total") or 0)
        passed = int(summary.get("passed") or 0)
        failed = int(summary.get("failed") or 0)

        pass_rate = round(
            passed / total * 100, 2
        ) if total else 0

        cur.execute(
            """
            SELECT
                subject,
                COUNT(*) AS total,
                COALESCE(SUM(prediction = 'PASS'), 0) AS passed,
                COALESCE(SUM(prediction = 'FAIL'), 0) AS failed
            FROM predictions
            WHERE username = %s
            GROUP BY subject
            ORDER BY total DESC, subject ASC
            """,
            (username,)
        )

        subjects = []

        for row in cur.fetchall():
            subject_total = int(row["total"] or 0)
            subject_passed = int(row["passed"] or 0)
            subject_failed = int(row["failed"] or 0)

            subjects.append({
                "subject": row["subject"],
                "total": subject_total,
                "passed": subject_passed,
                "failed": subject_failed,
                "pass_rate": round(
                    subject_passed / subject_total * 100, 2
                ) if subject_total else 0
            })

        cur.execute(
            """
            SELECT month, total, passed, failed
            FROM (
                SELECT
                    DATE_FORMAT(created_at, '%Y-%m') AS month,
                    COUNT(*) AS total,
                    COALESCE(SUM(prediction = 'PASS'), 0) AS passed,
                    COALESCE(SUM(prediction = 'FAIL'), 0) AS failed
                FROM predictions
                WHERE username = %s
                GROUP BY DATE_FORMAT(created_at, '%Y-%m')
                ORDER BY month DESC
                LIMIT 12
            ) AS monthly_results
            ORDER BY month ASC
            """,
            (username,)
        )

        trend = []

        for row in cur.fetchall():
            trend.append({
                "month": str(row["month"]),
                "total": int(row["total"] or 0),
                "passed": int(row["passed"] or 0),
                "failed": int(row["failed"] or 0)
            })

        return jsonify({
            "success": True,
            "total": total,
            "passed": passed,
            "failed": failed,
            "pass_rate": pass_rate,
            "subjects": subjects,
            "trend": trend,
            "result_chart": [
                {"name": "Passed", "value": passed},
                {"name": "Failed", "value": failed}
            ]
        })

    except Exception as e:
        return server_error(e)

    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


# =========================================================
# STUDENT OFFICIAL GRADES API
# =========================================================
@app.route("/api/student/grades", methods=["GET"])
def get_student_grades():
    if "username" not in session:
        return jsonify({
            "success": False,
            "message": "Login required."
        }), 401

    conn = None
    cur = None

    try:
        conn = get_db_connection()
        cur = conn.cursor(dictionary=True)

        cur.execute(
            """
            SELECT id, username, role
            FROM users
            WHERE username = %s
            LIMIT 1
            """,
            (session["username"],)
        )

        user = cur.fetchone()

        if not user:
            session.clear()
            return jsonify({
                "success": False,
                "message": "User account not found."
            }), 401

        # Do not expose grades to non-student accounts.
        cur.execute(
            """
            SELECT id, school_id, class_id
            FROM students
            WHERE user_id = %s
            LIMIT 1
            """,
            (user["id"],)
        )

        student = cur.fetchone()

        if not student:
            return jsonify({
                "success": True,
                "grades": [],
                "message": "No student profile is linked to this account."
            })

        subject_columns = _table_columns(cur, "subjects")
        assignment_columns = _table_columns(cur, "teacher_assignments")
        class_columns = _table_columns(cur, "classes")
        subject_label_col = next(
            (c for c in ("subject_name", "name", "title") if c in subject_columns),
            None
        )
        class_label_col = "class_name" if "class_name" in class_columns else None

        subject_select = (
            f"sub.`{subject_label_col}` AS subject_name"
            if subject_label_col and "subject_id" in assignment_columns
            else "'Unknown subject' AS subject_name"
        )
        class_select = (
            "cl.`class_name` AS class_name"
            if class_label_col and "class_id" in assignment_columns
            else "'—' AS class_name"
        )
        joins = " LEFT JOIN teacher_assignments ta ON ta.id = sg.assignment_id "
        if subject_label_col and "subject_id" in assignment_columns:
            joins += " LEFT JOIN subjects sub ON sub.id = ta.subject_id "
        if class_label_col and "class_id" in assignment_columns:
            joins += " LEFT JOIN classes cl ON cl.id = ta.class_id "

        cur.execute(
            f"""
            SELECT
                sg.id AS grade_id,
                sg.score,
                sg.max_score,
                sg.term,
                sg.academic_year,
                sg.status,
                sg.created_at,
                {subject_select},
                {class_select}
            FROM student_grades AS sg
            {joins}
            WHERE sg.student_id = %s
            ORDER BY sg.created_at DESC, sg.id DESC
            """,
            (student["id"],)
        )

        grades = []

        for row in cur.fetchall():
            score = float(row["score"])
            max_score = float(row["max_score"])
            status = str(row["status"] or "PENDING").upper()

            grades.append({
                "grade_id": row["grade_id"],
                "subject": row["subject_name"] or "Unknown subject",
                "class_name": row["class_name"] or "—",
                "score": score,
                "max_score": max_score,
                "percentage": round(
                    score / max_score * 100, 2
                ) if max_score > 0 else 0,
                "term": row["term"] or "—",
                "academic_year": row["academic_year"] or "—",
                "status": status,
                "official": status == "APPROVED",
                "created_at": (
                    row["created_at"].strftime("%Y-%m-%d %H:%M")
                    if row["created_at"] else None
                )
            })

        return jsonify({
            "success": True,
            "grades": grades
        })

    except Exception as e:
        return server_error(e)

    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


# =========================================================
# PDF REPORT
# =========================================================
@app.route("/report/pdf")
def report_pdf():
    if "username" not in session:
        return redirect(url_for("login_page"))

    try:
        file_path = generate_pdf_report(session["username"])

        if not file_path:
            return "No predictions yet", 404

        return send_file(
            file_path,
            as_attachment=True,
            download_name="student_prediction_report.pdf",
            mimetype="application/pdf"
        )

    except Exception:
        app.logger.exception("PDF report generation failed")
        return "Could not generate PDF report.", 500


# =========================================================
# EXCEL REPORT
# =========================================================
@app.route("/report/excel")
def report_excel():
    if "username" not in session:
        return redirect(url_for("login_page"))

    try:
        file_path = generate_excel_report(session["username"])

        if not file_path:
            return "No predictions yet", 404

        return send_file(
            file_path,
            as_attachment=True,
            download_name="student_prediction_report.xlsx",
            mimetype=(
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            )
        )

    except Exception:
        app.logger.exception("Excel report generation failed")
        return "Could not generate Excel report.", 500



# =========================================================
# ROLE-BASED API HELPERS AND DASHBOARDS
# =========================================================
def _table_columns(cur, table_name):
    """Return column names for a table in the configured TiDB database."""
    cur.execute(
        """
        SELECT COLUMN_NAME
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s
        """,
        (table_name,)
    )
    return {row["COLUMN_NAME"] for row in cur.fetchall()}


def _load_session_user(cur):
    if not session.get("username"):
        return None
    cur.execute(
        "SELECT id, username, full_name, role FROM users WHERE username=%s LIMIT 1",
        (session["username"],)
    )
    user = cur.fetchone()
    if not user:
        session.clear()
        return None

    account_role = str(user.get("role") or "USER").strip().upper()
    if account_role in {"PLATFORM_ADMIN", "SUPER_ADMIN", "ADMIN"}:
        role = "PLATFORM_ADMIN"
        membership = None
    else:
        cur.execute(
            """
            SELECT id, school_id, user_id, member_role
            FROM school_members
            WHERE user_id=%s
            ORDER BY id DESC
            LIMIT 1
            """,
            (user["id"],)
        )
        membership = cur.fetchone()
        member_role = str((membership or {}).get("member_role") or "").upper()
        role = member_role if member_role in {
            "STUDENT", "TEACHER", "HEADMASTER"
        } else account_role

    session["user_id"] = user["id"]
    session["role"] = role
    if membership:
        session["school_id"] = membership.get("school_id")
        session["membership_id"] = membership.get("id")
    return {"user": user, "role": role, "membership": membership}


def _role_error(required_roles):
    if not session.get("username"):
        return jsonify({"success": False, "message": "Login required."}), 401
    role = str(session.get("role") or "").upper()
    if role not in set(required_roles):
        return jsonify({"success": False, "message": "Permission denied."}), 403
    return None


@app.route("/api/me", methods=["GET"])
def api_me():
    """Return the signed-in user's effective role for frontend navigation."""
    if not session.get("username"):
        return jsonify({"success": False, "message": "Login required."}), 401
    conn = cur = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(dictionary=True)
        data = _load_session_user(cur)
        if not data:
            return jsonify({"success": False, "message": "Account not found."}), 401
        user = data["user"]
        membership = data["membership"] or {}
        return jsonify({
            "success": True,
            "username": user["username"],
            "full_name": user.get("full_name") or user["username"],
            "role": data["role"],
            "school_id": membership.get("school_id")
        })
    except Exception as e:
        return server_error(e)
    finally:
        if cur: cur.close()
        if conn: conn.close()


@app.route("/api/platform-admin/overview", methods=["GET"])
def platform_admin_overview():
    """Platform-wide counts only; intentionally does not return student grades."""
    if not session.get("username"):
        return jsonify({"success": False, "message": "Login required."}), 401
    conn = cur = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(dictionary=True)
        identity = _load_session_user(cur)
        if not identity or identity["role"] != "PLATFORM_ADMIN":
            return jsonify({"success": False, "message": "Platform admin only."}), 403

        result = {}
        for key, table in (
            ("users", "users"), ("schools", "schools"),
            ("students", "students"), ("predictions", "predictions")
        ):
            cur.execute(f"SELECT COUNT(*) AS total FROM `{table}`")
            result[key] = int((cur.fetchone() or {}).get("total") or 0)

        cur.execute(
            """
            SELECT s.id, s.school_name, s.education_level, s.approval_mode,
                   COUNT(DISTINCT sm.id) AS members
            FROM schools s
            LEFT JOIN school_members sm ON sm.school_id=s.id
            GROUP BY s.id, s.school_name, s.education_level, s.approval_mode
            ORDER BY s.school_name
            """
        )
        result["school_list"] = cur.fetchall()
        result["success"] = True
        return jsonify(result)
    except Exception as e:
        return server_error(e)
    finally:
        if cur: cur.close()
        if conn: conn.close()


@app.route("/api/headmaster/overview", methods=["GET"])
def headmaster_overview():
    """Statistics and pending grade count restricted to the headmaster's own school."""
    if not session.get("username"):
        return jsonify({"success": False, "message": "Login required."}), 401
    conn = cur = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(dictionary=True)
        identity = _load_session_user(cur)
        if not identity or identity["role"] != "HEADMASTER":
            return jsonify({"success": False, "message": "Headmaster only."}), 403
        membership = identity["membership"] or {}
        school_id = membership.get("school_id")
        if not school_id:
            return jsonify({"success": False, "message": "No school linked to account."}), 400

        cur.execute("SELECT school_name, education_level, approval_mode FROM schools WHERE id=%s", (school_id,))
        school = cur.fetchone()
        cur.execute("SELECT COUNT(*) AS total FROM school_members WHERE school_id=%s", (school_id,))
        members = int((cur.fetchone() or {}).get("total") or 0)
        cur.execute("SELECT COUNT(*) AS total FROM students WHERE school_id=%s", (school_id,))
        students = int((cur.fetchone() or {}).get("total") or 0)
        cur.execute(
            """
            SELECT COUNT(*) AS total
            FROM student_grades sg
            JOIN students st ON st.id=sg.student_id
            WHERE st.school_id=%s AND sg.status='PENDING'
            """,
            (school_id,)
        )
        pending = int((cur.fetchone() or {}).get("total") or 0)
        return jsonify({
            "success": True, "school": school or {}, "members": members,
            "students": students, "pending_grades": pending
        })
    except Exception as e:
        return server_error(e)
    finally:
        if cur: cur.close()
        if conn: conn.close()


@app.route("/api/headmaster/pending-grades", methods=["GET"])
def headmaster_pending_grades():
    if not session.get("username"):
        return jsonify({"success": False, "message": "Login required."}), 401
    conn = cur = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(dictionary=True)
        identity = _load_session_user(cur)
        if not identity or identity["role"] != "HEADMASTER":
            return jsonify({"success": False, "message": "Headmaster only."}), 403
        membership = identity["membership"] or {}
        school_id = membership.get("school_id")
        cur.execute("SELECT approval_mode FROM schools WHERE id=%s", (school_id,))
        school = cur.fetchone()
        if not school or str(school.get("approval_mode") or "").upper() != "HEADMASTER":
            return jsonify({"success": True, "grades": [], "message": "This school does not use headmaster approval."})
        cur.execute(
            """
            SELECT sg.id AS grade_id, sg.student_id, st.student_name,
                   sg.assignment_id, sg.score, sg.max_score, sg.term,
                   sg.academic_year, sg.entered_by, sg.created_at
            FROM student_grades sg
            JOIN students st ON st.id=sg.student_id
            WHERE st.school_id=%s AND sg.status='PENDING'
            ORDER BY sg.created_at DESC, sg.id DESC
            LIMIT 500
            """,
            (school_id,)
        )
        grades = cur.fetchall()
        for row in grades:
            if row.get("created_at"):
                row["created_at"] = row["created_at"].strftime("%Y-%m-%d %H:%M:%S")
            for field in ("score", "max_score"):
                if row.get(field) is not None:
                    row[field] = float(row[field])
        return jsonify({"success": True, "grades": grades})
    except Exception as e:
        return server_error(e)
    finally:
        if cur: cur.close()
        if conn: conn.close()


@app.route("/api/headmaster/grades/<int:grade_id>/decision", methods=["POST"])
def headmaster_grade_decision(grade_id):
    """Approve/reject a pending grade only inside the headmaster's school."""
    if not session.get("username"):
        return jsonify({"success": False, "message": "Login required."}), 401
    data = request.get_json(silent=True) or {}
    decision = str(data.get("decision") or "").strip().upper()
    note = str(data.get("note") or "").strip()
    if decision not in {"APPROVED", "REJECTED"}:
        return jsonify({"success": False, "message": "Decision must be APPROVED or REJECTED."}), 400

    conn = cur = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(dictionary=True)
        conn.start_transaction()
        identity = _load_session_user(cur)
        if not identity or identity["role"] != "HEADMASTER":
            conn.rollback()
            return jsonify({"success": False, "message": "Headmaster only."}), 403
        membership = identity["membership"] or {}
        school_id = membership.get("school_id")
        cur.execute("SELECT approval_mode FROM schools WHERE id=%s", (school_id,))
        school = cur.fetchone()
        if not school or str(school.get("approval_mode") or "").upper() != "HEADMASTER":
            conn.rollback()
            return jsonify({"success": False, "message": "Headmaster approval is not enabled for this school."}), 403

        cur.execute(
            """
            SELECT sg.id, sg.status
            FROM student_grades sg
            JOIN students st ON st.id=sg.student_id
            WHERE sg.id=%s AND st.school_id=%s
            FOR UPDATE
            """,
            (grade_id, school_id)
        )
        grade = cur.fetchone()
        if not grade or str(grade.get("status") or "").upper() != "PENDING":
            conn.rollback()
            return jsonify({"success": False, "message": "Pending grade not found in your school."}), 404

        cur.execute("UPDATE student_grades SET status=%s WHERE id=%s AND status='PENDING'", (decision, grade_id))
        if cur.rowcount != 1:
            conn.rollback()
            return jsonify({"success": False, "message": "Grade has already been processed."}), 409

        # Record approval only for columns confirmed to exist in the database.
        approval_columns = _table_columns(cur, "grade_approvals")
        insert_values = {}
        if "grade_id" in approval_columns: insert_values["grade_id"] = grade_id
        if "approver_member_id" in approval_columns and membership.get("id") is not None:
            insert_values["approver_member_id"] = membership["id"]
        if "decision" in approval_columns: insert_values["decision"] = decision
        elif "status" in approval_columns: insert_values["status"] = decision
        if "note" in approval_columns: insert_values["note"] = note[:1000]
        elif "comments" in approval_columns: insert_values["comments"] = note[:1000]
        if "created_at" in approval_columns:
            insert_values["created_at"] = None  # use DB default if supported below
            insert_values.pop("created_at", None)
        if insert_values.get("grade_id") is not None and insert_values.get("approver_member_id") is not None:
            columns = list(insert_values)
            placeholders = ", ".join(["%s"] * len(columns))
            cur.execute(
                "INSERT INTO grade_approvals (" + ", ".join(f"`{c}`" for c in columns) +
                ") VALUES (" + placeholders + ")",
                tuple(insert_values[c] for c in columns)
            )
        conn.commit()
        return jsonify({"success": True, "message": f"Grade {decision.lower()}."})
    except Exception as e:
        if conn:
            try: conn.rollback()
            except Exception: pass
        return server_error(e)
    finally:
        if cur: cur.close()
        if conn: conn.close()


@app.route("/api/teacher/assignments", methods=["GET"])
def teacher_assignments_api():
    """List only assignments belonging to the logged-in teacher, adapting to common schema variants."""
    if not session.get("username"):
        return jsonify({"success": False, "message": "Login required."}), 401
    conn = cur = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(dictionary=True)
        identity = _load_session_user(cur)
        if not identity or identity["role"] != "TEACHER":
            return jsonify({"success": False, "message": "Teacher only."}), 403
        membership = identity["membership"] or {}
        cols = _table_columns(cur, "teacher_assignments")
        teacher_col = next((c for c in ("teacher_member_id", "teacher_id", "member_id", "teacher_user_id", "user_id") if c in cols), None)
        if not teacher_col:
            return jsonify({"success": False, "message": "teacher_assignments needs a teacher/member ID column."}), 409

        owner_id = membership.get("id") if teacher_col in {"teacher_member_id", "member_id"} else identity["user"]["id"]
        cur.execute(
            f"SELECT * FROM teacher_assignments WHERE `{teacher_col}`=%s ORDER BY id DESC LIMIT 500",
            (owner_id,)
        )
        assignments = cur.fetchall()
        for row in assignments:
            for key, value in list(row.items()):
                if hasattr(value, "strftime"):
                    row[key] = value.strftime("%Y-%m-%d %H:%M:%S")
        return jsonify({"success": True, "assignments": assignments})
    except Exception as e:
        return server_error(e)
    finally:
        if cur: cur.close()
        if conn: conn.close()


# =========================================================
# START APPLICATION
# =========================================================
if __name__ == "__main__":
    app.run(debug=False)
    
