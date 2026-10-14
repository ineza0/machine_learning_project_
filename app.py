
import os
import hashlib
import secrets
import traceback

import joblib
from flask import (
    Flask, request, jsonify, render_template, session,
    redirect, url_for, send_file, Response
)
from werkzeug.middleware.proxy_fix import ProxyFix

from auth import *
from db_config import get_db_connection
from report import generate_pdf_report, generate_excel_report


app = Flask(__name__)

app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

app.secret_key = os.getenv("SECRET_KEY", "change-this-secret-key")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("FLASK_ENV") == "production"
)


# ---------------------------------------------------------
# Google Search Console verification
# ---------------------------------------------------------
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


# ---------------------------------------------------------
# Load machine-learning model
# ---------------------------------------------------------
try:
    model = joblib.load("student_model.pkl")
    print("ML model loaded")
except Exception as e:
    print(f"Model not loaded: {e}")
    model = None


# ---------------------------------------------------------
# Google Sign-In
# ---------------------------------------------------------
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
        cur.execute("SHOW COLUMNS FROM users")
        cols = [c["Field"] for c in cur.fetchall()]

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
            return username

        random_value = hashlib.sha256(
            secrets.token_bytes(32)
        ).hexdigest()

        data = {"username": username}

        for column in cols:
            lower = column.lower()

            if lower == "email":
                data[column] = email
            elif lower in ("name", "full_name", "fullname"):
                data[column] = name
            elif lower == "role":
                data[column] = "user"
            elif "question" in lower:
                data[column] = "Signed up with Google"
            elif "answer" in lower or "pass" in lower:
                data[column] = random_value

        columns_sql = ",".join(f"`{c}`" for c in data)
        placeholders = ",".join(["%s"] * len(data))

        cur.execute(
            f"INSERT INTO users ({columns_sql}) VALUES ({placeholders})",
            tuple(data.values())
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

        email = (info.get("email") or "").lower()

        if not email or not info.get("email_verified"):
            return redirect("/login?error=google_unverified")

        name = info.get("name") or email.split("@")[0]
        session["username"] = google_get_or_create_user(email, name)

        return redirect(url_for("dashboard"))

    except Exception:
        traceback.print_exc()
        return redirect("/login?error=google_failed")


# ---------------------------------------------------------
# Pages
# ---------------------------------------------------------
@app.route("/")
def home():
    return redirect(
        url_for("dashboard" if "username" in session else "login_page")
    )


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


@app.route("/dashboard")
def dashboard():
    if "username" not in session:
        return redirect(url_for("login_page"))

    return render_template(
        "index.html",
        username=session["username"]
    )


# ---------------------------------------------------------
# Error response helper
# ---------------------------------------------------------
def server_error(e):
    traceback.print_exc()
    return jsonify({
        "success": False,
        "message": "Server error. Please try again."
    }), 500


# ---------------------------------------------------------
# Login API
# ---------------------------------------------------------
@app.route("/api/login", methods=["POST"])
def api_login():
    try:
        data = request.get_json(silent=True) or {}

        username = str(data.get("username", "")).strip()
        password = data.get("password", "")

        ok, msg = login_user(username, password)

        if ok:
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


# ---------------------------------------------------------
# Signup API: invitation-based school registration
# ---------------------------------------------------------
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
        invitation_code = str(
            data.get("invitation_code", "")
        ).strip()
        requested_role = str(data.get("role", "")).strip().upper()
        terms = data.get("terms", False)

        if not all([
            full_name, username, email, password, invitation_code
        ]):
            return jsonify({
                "success": False,
                "message": "Please complete all required fields."
            }), 400

        if not isinstance(password, str) or len(password) < 8:
            return jsonify({
                "success": False,
                "message": "Password must contain at least 8 characters."
            }), 400

        if len(full_name) > 200 or len(username) > 100 or len(email) > 150:
            return jsonify({
                "success": False,
                "message": "One or more fields are too long."
            }), 400

        if requested_role not in ("STUDENT", "TEACHER", "HEADMASTER"):
            return jsonify({
                "success": False,
                "message": "Choose a valid account role."
            }), 400

        if terms not in (True, 1, "true", "on"):
            return jsonify({
                "success": False,
                "message": "Please confirm the account information."
            }), 400

        # Check the invitation in the database.
        conn = get_db_connection()
        cur = conn.cursor(dictionary=True)

        cur.execute(
            """
            SELECT id, school_id, invited_role, email, class_id,
                   used_by, expires_at
            FROM school_invitations
            WHERE invitation_code = %s
            LIMIT 1
            """,
            (invitation_code,)
        )
        invitation = cur.fetchone()

        if not invitation:
            return jsonify({
                "success": False,
                "message": "Invalid school invitation code."
            }), 400

        if invitation["used_by"] is not None:
            return jsonify({
                "success": False,
                "message": "This invitation code has already been used."
            }), 400

        cur.execute("SELECT CURRENT_TIMESTAMP AS db_now")
        db_now = cur.fetchone()["db_now"]

        expires_at = invitation["expires_at"]

        if expires_at is not None and expires_at <= db_now:
            return jsonify({
                "success": False,
                "message": "This invitation code has expired."
            }), 400

        invited_role = str(invitation["invited_role"]).upper()

        # The role in the database invitation is authoritative.
        if requested_role != invited_role:
            return jsonify({
                "success": False,
                "message": (
                    "Your selected role does not match the invitation."
                )
            }), 403

        invited_email = (invitation.get("email") or "").strip().lower()

        if invited_email and invited_email != email:
            return jsonify({
                "success": False,
                "message": (
                    "This invitation is assigned to another email address."
                )
            }), 403

        school_id = invitation["school_id"]
        class_id = invitation["class_id"]
        invitation_id = invitation["id"]

        cur.close()
        cur = None
        conn.close()
        conn = None

        # Create the account using the existing auth.py function.
        # It should hash the password and return (success, message).
        ok, msg = register_user(
            username,
            password,
            None,
            None,
            full_name,
            email
        )

        if not ok:
            return jsonify({
                "success": False,
                "message": str(msg or "Could not create the account.")
            }), 400

        # Add school membership and, for students, their student profile.
        conn = get_db_connection()
        cur = conn.cursor(dictionary=True)

        cur.execute(
            "SELECT id FROM users WHERE username=%s LIMIT 1",
            (username,)
        )
        user = cur.fetchone()

        if not user:
            raise RuntimeError("User record could not be found.")

        user_id = user["id"]

        cur.execute(
            """
            UPDATE users
            SET full_name=%s, email=%s, role=%s
            WHERE id=%s
            """,
            (full_name, email, invited_role.lower(), user_id)
        )

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

        # Only mark the invitation used if it is still available.
        cur.execute(
            """
            UPDATE school_invitations
            SET used_by=%s, used_at=CURRENT_TIMESTAMP
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
                "The invitation was already used or has expired."
            )

        conn.commit()

        return jsonify({
            "success": True,
            "message": "Account created successfully. You can now log in.",
            "redirect": url_for("login_page")
        }), 201

    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass

        traceback.print_exc()

        return jsonify({
            "success": False,
            "message": (
                "Registration failed. Check the invitation and account "
                "details, then contact the administrator if the problem persists."
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


# ---------------------------------------------------------
# Forgot password: security question
# ---------------------------------------------------------
@app.route("/api/get-security-question", methods=["POST"])
def get_question():
    try:
        data = request.get_json(silent=True) or {}
        username = str(data.get("username", "")).strip()

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


@app.route("/api/verify-security-answer", methods=["POST"])
def verify_ans():
    try:
        data = request.get_json(silent=True) or {}

        ok = verify_security_answer(
            str(data.get("username", "")).strip(),
            data.get("answer", "")
        )

        return jsonify({
            "success": bool(ok),
            "message": "" if ok else "Wrong security answer."
        }), (200 if ok else 400)

    except Exception as e:
        return server_error(e)


@app.route("/api/reset-password", methods=["POST"])
def reset_pwd():
    try:
        data = request.get_json(silent=True) or {}
        new_password = data.get("new_password", "")

        if not isinstance(new_password, str) or len(new_password) < 8:
            return jsonify({
                "success": False,
                "message": "Password must contain at least 8 characters."
            }), 400

        ok, msg = force_reset_password(
            str(data.get("username", "")).strip(),
            new_password
        )

        return jsonify({
            "success": bool(ok),
            "message": msg
        }), (200 if ok else 400)

    except Exception as e:
        return server_error(e)


# ---------------------------------------------------------
# System status
# ---------------------------------------------------------
@app.route("/api/system-status")
def system_status():
    conn = None
    cur = None

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("SELECT COUNT(*) FROM users")
        users = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM predictions")
        predictions = cur.fetchone()[0]

        return jsonify({
            "success": True,
            "authentication": "Online",
            "ml_predictor": "Ready" if model is not None else "Model missing",
            "database_name": "TiDB Cloud",
            "users": int(users),
            "predictions": int(predictions)
        })

    except Exception:
        traceback.print_exc()
        return jsonify({"success": False}), 500

    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


# ---------------------------------------------------------
# Machine-learning prediction
# ---------------------------------------------------------
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

        hours = float(request.form["study_hours"])
        attendance = float(request.form["attendance"])
        assignment = float(request.form["assignment_score"])
        cat = float(request.form["cat_score"])
        practical = float(request.form["practical_score"])

        if not (
            0 <= hours <= 20
            and all(0 <= value <= 100 for value in (
                attendance, assignment, cat, practical
            ))
        ):
            return jsonify({
                "success": False,
                "error": "Check the score ranges."
            }), 400

        features = [[hours, attendance, assignment, cat, practical]]
        prediction_value = int(model.predict(features)[0])

        probability = 0.0
        if hasattr(model, "predict_proba"):
            probability = float(
                model.predict_proba(features)[0][prediction_value] * 100
            )

        result = "PASS" if prediction_value == 1 else "FAIL"

        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute(
            """
            SELECT id FROM students
            WHERE student_name=%s
            LIMIT 1
            """,
            (student_name,)
        )
        row = cur.fetchone()

        if row:
            student_id = row[0]
        else:
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

    except (KeyError, ValueError):
        return jsonify({
            "success": False,
            "error": "Fill in every field using valid numbers."
        }), 400

    except Exception as e:
        return server_error(e)

    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


# ---------------------------------------------------------
# Prediction history
# ---------------------------------------------------------
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
                p.subject, p.study_hours, p.attendance,
                p.assignment, p.cat, p.practical,
                p.prediction,
                ROUND(p.probability * 100, 2) AS confidence
            FROM predictions p
            LEFT JOIN students s ON p.student_id=s.id
            WHERE p.username=%s
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


# ---------------------------------------------------------
# Statistics
# ---------------------------------------------------------
@app.route("/api/stats")
def stats():
    empty = {
        "total": 0,
        "passed": 0,
        "failed": 0,
        "pass_rate": 0,
        "subjects": []
    }

    if "username" not in session:
        return jsonify(empty), 401

    conn = None
    cur = None

    try:
        conn = get_db_connection()
        cur = conn.cursor(dictionary=True)

        cur.execute(
            """
            SELECT
                COUNT(*) AS total,
                COALESCE(SUM(prediction='PASS'), 0) AS passed,
                COALESCE(SUM(prediction='FAIL'), 0) AS failed
            FROM predictions
            WHERE username=%s
            """,
            (session["username"],)
        )

        data = cur.fetchone()
        total = int(data["total"])
        passed = int(data["passed"])
        failed = int(data["failed"])

        cur.execute(
            """
            SELECT
                subject,
                COUNT(*) AS total,
                SUM(prediction='PASS') AS passed,
                SUM(prediction='FAIL') AS failed
            FROM predictions
            WHERE username=%s
            GROUP BY subject
            ORDER BY total DESC
            """,
            (session["username"],)
        )

        subjects = []

        for row in cur.fetchall():
            subject_total = int(row["total"])
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

        return jsonify({
            "total": total,
            "passed": passed,
            "failed": failed,
            "pass_rate": round(passed / total * 100, 2) if total else 0,
            "subjects": subjects
        })

    except Exception as e:
        return server_error(e)

    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


# ---------------------------------------------------------
# PDF report
# ---------------------------------------------------------
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
        traceback.print_exc()
        return "Could not generate PDF report.", 500


# ---------------------------------------------------------
# Excel report
# ---------------------------------------------------------
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
        traceback.print_exc()
        return "Could not generate Excel report.", 500


# ---------------------------------------------------------
# Start application
# ---------------------------------------------------------
if __name__ == "__main__":
    app.run(debug=False)
