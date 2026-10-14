

from flask import (
    Blueprint, render_template, jsonify, session,
    redirect, request, abort
)
from functools import wraps
from db_config import get_db_connection
from datetime import datetime

admin_bp = Blueprint("platform_admin", __name__, url_prefix="/platform-admin")


# ---------------------------------------------------------
# DATABASE HELPERS
# ---------------------------------------------------------

def query_db(sql, params=()):
    """Run a SELECT query and return rows as dictionaries."""
    conn = get_db_connection()
    if conn is None:
        raise RuntimeError("Database connection failed.")

    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(sql, params)
        rows = cursor.fetchall()
        return rows
    finally:
        try:
            cursor.close()
        except Exception:
            pass
        conn.close()


def get_current_user():
    """
    Adapt this function if your existing login stores a different
    session key. Prefer user_id; username is the fallback.
    """
    user_id = session.get("user_id")
    username = session.get("username")

    if user_id:
        rows = query_db(
            "SELECT id, username, email, full_name, role "
            "FROM users WHERE id = %s LIMIT 1",
            (user_id,)
        )
    elif username:
        rows = query_db(
            "SELECT id, username, email, full_name, role "
            "FROM users WHERE username = %s LIMIT 1",
            (username,)
        )
    else:
        return None

    return rows[0] if rows else None


def platform_admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        try:
            user = get_current_user()
        except Exception:
            abort(503, description="Unable to verify your account.")

        if not user:
            return redirect("/login")

        role = str(user.get("role") or "").upper()

        if role not in ("PLATFORM_ADMIN", "SUPER_ADMIN"):
            abort(403, description="Platform Admin access is required.")

        return view(*args, **kwargs)

    return wrapped


# ---------------------------------------------------------
# DASHBOARD
# ---------------------------------------------------------

@admin_bp.route("/")
@platform_admin_required
def dashboard():
    return render_template("platform_admin_dashboard.html")


# ---------------------------------------------------------
# PLATFORM OVERVIEW
# ---------------------------------------------------------

@admin_bp.route("/api/overview")
@platform_admin_required
def overview():
    try:
        users = query_db(
            "SELECT COUNT(*) AS total FROM users"
        )[0]["total"]

        schools = query_db(
            "SELECT COUNT(*) AS total FROM schools"
        )[0]["total"]

        students = query_db(
            "SELECT COUNT(*) AS total FROM students"
        )[0]["total"]

        predictions = query_db(
            "SELECT COUNT(*) AS total FROM predictions"
        )[0]["total"]

        return jsonify({
            "success": True,
            "users": int(users or 0),
            "schools": int(schools or 0),
            "students": int(students or 0),
            "predictions": int(predictions or 0)
        })
    except Exception:
        return jsonify({
            "success": False,
            "message": "Could not load platform statistics."
        }), 500


# ---------------------------------------------------------
# SCHOOLS
# ---------------------------------------------------------

@admin_bp.route("/api/schools")
@platform_admin_required
def list_schools():
    try:
        rows = query_db("""
            SELECT
                s.id,
                s.school_name,
                s.school_code,
                s.education_level,
                s.approval_mode,
                s.created_at,
                (
                    SELECT COUNT(*)
                    FROM school_members sm
                    WHERE sm.school_id = s.id
                ) AS members_count
            FROM schools s
            ORDER BY s.id DESC
            LIMIT 200
        """)

        for row in rows:
            created = row.get("created_at")
            if isinstance(created, datetime):
                row["created_at"] = created.strftime("%Y-%m-%d")
            elif created is not None:
                row["created_at"] = str(created)

        return jsonify({"success": True, "schools": rows})
    except Exception:
        return jsonify({
            "success": False,
            "message": "Could not load schools. Check the schools table."
        }), 500


# ---------------------------------------------------------
# USERS
# ---------------------------------------------------------

@admin_bp.route("/api/users")
@platform_admin_required
def list_users():
    try:
        rows = query_db("""
            SELECT id, username, email, full_name, role
            FROM users
            ORDER BY id DESC
            LIMIT 200
        """)

        # Avoid exposing passwords or password hashes.
        return jsonify({"success": True, "users": rows})
    except Exception:
        return jsonify({
            "success": False,
            "message": "Could not load users. Check the users table."
        }), 500


# ---------------------------------------------------------
# AI-STYLE PLATFORM ANALYSIS
# This is rule-based analysis, not an external LLM.
# ---------------------------------------------------------

@admin_bp.route("/api/ai-analysis")
@platform_admin_required
def ai_analysis():
    try:
        total_users = int(
            query_db("SELECT COUNT(*) AS total FROM users")[0]["total"] or 0
        )
        total_schools = int(
            query_db("SELECT COUNT(*) AS total FROM schools")[0]["total"] or 0
        )
        total_students = int(
            query_db("SELECT COUNT(*) AS total FROM students")[0]["total"] or 0
        )
        total_predictions = int(
            query_db("SELECT COUNT(*) AS total FROM predictions")[0]["total"] or 0
        )

        recommendations = []

        if total_schools == 0:
            recommendations.append({
                "level": "warning",
                "title": "No schools registered",
                "detail": "Register or invite the first school to begin using StudentPredict."
            })

        if total_users == 0:
            recommendations.append({
                "level": "warning",
                "title": "No user accounts found",
                "detail": "Check account registration and database connectivity."
            })

        if total_students > 0 and total_predictions == 0:
            recommendations.append({
                "level": "info",
                "title": "No predictions recorded",
                "detail": "Verify that the prediction workflow saves results successfully."
            })

        if total_schools > 0 and total_students == 0:
            recommendations.append({
                "level": "info",
                "title": "No student records found",
                "detail": "Check student invitations, school membership and student creation."
            })

        if total_users > 0 and total_schools > 0:
            recommendations.append({
                "level": "success",
                "title": "Platform data is available",
                "detail": "Review school membership and role permissions regularly."
            })

        if not recommendations:
            recommendations.append({
                "level": "info",
                "title": "Platform is ready for monitoring",
                "detail": "Collect more usage data to make the analysis more informative."
            })

        return jsonify({
            "success": True,
            "analysis_type": "rule_based",
            "metrics": {
                "users": total_users,
                "schools": total_schools,
                "students": total_students,
                "predictions": total_predictions
            },
            "recommendations": recommendations
        })
    except Exception:
        return jsonify({
            "success": False,
            "message": "Analysis failed. Check database connection and table names."
        }), 500
