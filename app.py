import os
import hashlib
import secrets
import traceback

import joblib
from flask import (
    Flask,
    request,
    jsonify,
    render_template,
    session,
    redirect,
    url_for,
    send_file,
    Response
)
from werkzeug.middleware.proxy_fix import ProxyFix

from auth import *                       # login_user, register_user, ...
from db_config import get_db_connection
from report import generate_pdf_report, generate_excel_report


app = Flask(__name__)

app.wsgi_app = ProxyFix(
    app.wsgi_app,
    x_proto=1,
    x_host=1
)

app.secret_key = os.getenv(
    'SECRET_KEY',
    'change-this-secret-key'
)

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax'
)


# ------------------------------------------------------------------
# Google Search Console HTML Verification
# ------------------------------------------------------------------
#
# In Render Environment Variables add:
#
# GOOGLE_VERIFICATION_FILENAME
# GOOGLE_VERIFICATION_CONTENT
#
# Example:
#
# GOOGLE_VERIFICATION_FILENAME=google1234567890abcdef.html
#
# GOOGLE_VERIFICATION_CONTENT=google-site-verification: google1234567890abcdef.html
#
# IMPORTANT:
# Use the EXACT filename and EXACT content provided by Google.
# ------------------------------------------------------------------

GOOGLE_VERIFICATION_FILENAME = os.getenv(
    'GOOGLE_VERIFICATION_FILENAME',
    ''
).strip()

GOOGLE_VERIFICATION_CONTENT = os.getenv(
    'GOOGLE_VERIFICATION_CONTENT',
    ''
).strip()


if GOOGLE_VERIFICATION_FILENAME:

    # Remove accidental leading slash
    GOOGLE_VERIFICATION_FILENAME = GOOGLE_VERIFICATION_FILENAME.lstrip('/')

    # Only allow Google's expected .html verification filename
    if (
        GOOGLE_VERIFICATION_FILENAME.startswith('google')
        and GOOGLE_VERIFICATION_FILENAME.endswith('.html')
    ):

        def google_verification():
            return Response(
                GOOGLE_VERIFICATION_CONTENT,
                mimetype='text/html'
            )

        app.add_url_rule(
            '/' + GOOGLE_VERIFICATION_FILENAME,
            endpoint='google_verification',
            view_func=google_verification,
            methods=['GET']
        )


# ------------------------------------------------------------------
# ML model
# ------------------------------------------------------------------
try:
    model = joblib.load('student_model.pkl')
    print('ML model loaded')
except Exception as e:
    print(f'Model not loaded: {e}')
    model = None


# ------------------------------------------------------------------
# Google Sign-In
# ------------------------------------------------------------------
GOOGLE_ON = bool(
    os.getenv('GOOGLE_CLIENT_ID')
    and os.getenv('GOOGLE_CLIENT_SECRET')
)

if GOOGLE_ON:
    from authlib.integrations.flask_client import OAuth

    oauth = OAuth(app)

    oauth.register(
        name='google',
        client_id=os.getenv('GOOGLE_CLIENT_ID'),
        client_secret=os.getenv('GOOGLE_CLIENT_SECRET'),
        server_metadata_url=(
            'https://accounts.google.com/'
            '.well-known/openid-configuration'
        ),
        client_kwargs={
            'scope': 'openid email profile'
        },
    )


def google_get_or_create_user(email, name):
    """
    Return the username of the user with this Google email,
    creating the row if needed.

    It reads the real columns of your users table.
    """

    username = (
        email
        if len(email) <= 50
        else 'g_' + hashlib.md5(
            email.encode()
        ).hexdigest()[:20]
    )

    conn = get_db_connection()
    cur = conn.cursor(dictionary=True)

    try:
        cur.execute('SHOW COLUMNS FROM users')

        cols = [
            c['Field']
            for c in cur.fetchall()
        ]

        if 'email' in cols:
            cur.execute(
                'SELECT username FROM users '
                'WHERE email=%s LIMIT 1',
                (email,)
            )

            row = cur.fetchone()

            if row:
                return row['username']

        cur.execute(
            'SELECT username FROM users '
            'WHERE username=%s LIMIT 1',
            (username,)
        )

        if cur.fetchone():
            return username

        random_value = hashlib.sha256(
            secrets.token_bytes(32)
        ).hexdigest()

        data = {
            'username': username
        }

        for c in cols:

            lc = c.lower()

            if lc == 'email':
                data[c] = email

            elif lc in (
                'name',
                'full_name',
                'fullname'
            ):
                data[c] = name

            elif 'question' in lc:
                data[c] = 'Signed up with Google'

            elif (
                'answer' in lc
                or 'pass' in lc
            ):
                data[c] = random_value

        names = ','.join(
            f'`{k}`'
            for k in data
        )

        marks = ','.join(
            ['%s'] * len(data)
        )

        cur.execute(
            f'''
            INSERT INTO users ({names})
            VALUES ({marks})
            ''',
            tuple(data.values())
        )

        conn.commit()

        return username

    finally:
        cur.close()
        conn.close()


# ------------------------------------------------------------------
# Google Login
# ------------------------------------------------------------------

@app.route('/auth/google')
def google_login():

    if not GOOGLE_ON:
        return redirect(
            '/login?error=google_disabled'
        )

    return oauth.google.authorize_redirect(
        request.url_root.rstrip('/')
        + '/auth/google/callback'
    )


@app.route('/auth/google/callback')
def google_callback():

    if not GOOGLE_ON:
        return redirect(
            '/login?error=google_disabled'
        )

    try:

        info = (
            oauth.google
            .authorize_access_token()
            .get('userinfo')
            or {}
        )

        email = (
            info.get('email')
            or ''
        ).lower()

        if (
            not email
            or not info.get('email_verified')
        ):
            return redirect(
                '/login?error=google_unverified'
            )

        name = (
            info.get('name')
            or email.split('@')[0]
        )

        session['username'] = (
            google_get_or_create_user(
                email,
                name
            )
        )

        return redirect(
            url_for('dashboard')
        )

    except Exception:

        traceback.print_exc()

        return redirect(
            '/login?error=google_failed'
        )


# ------------------------------------------------------------------
# Pages
# ------------------------------------------------------------------

@app.route('/')
def home():

    return redirect(
        url_for(
            'dashboard'
            if 'username' in session
            else 'login_page'
        )
    )


@app.route('/login')
def login_page():

    return render_template(
        'login.html'
    )


@app.route('/signup')
def signup_page():

    return render_template(
        'signup.html'
    )


@app.route('/forgot')
def forgot_page():

    return render_template(
        'forgot.html'
    )


@app.route('/logout')
def logout():

    session.clear()

    return redirect(
        url_for('login_page')
    )


@app.route('/dashboard')
def dashboard():

    if 'username' not in session:
        return redirect(
            url_for('login_page')
        )

    return render_template(
        'index.html',
        username=session['username']
    )


# ------------------------------------------------------------------
# Auth API
# ------------------------------------------------------------------

def server_error(e):

    traceback.print_exc()

    return jsonify({
        'success': False,
        'error': 'Server error. Please try again.'
    }), 500


@app.route('/api/login', methods=['POST'])
def api_login():

    try:

        data = request.get_json() or {}

        username = (
            data.get('username', '')
            .strip()
        )

        password = data.get(
            'password',
            ''
        )

        ok, msg = login_user(
            username,
            password
        )

        if ok:

            session['username'] = username

            return jsonify({
                'success': True,
                'message': msg,
                'redirect': url_for(
                    'dashboard'
                )
            })

        return jsonify({
            'success': False,
            'error': msg
        })

    except Exception as e:

        return server_error(e)


@app.route('/api/signup', methods=['POST'])
def api_signup():

    try:

        d = request.get_json() or {}

        username = (
            d.get('username', '')
            .strip()
        )

        password = d.get(
            'password',
            ''
        )

        q = (
            d.get(
                'security_question',
                ''
            )
            .strip()
        )

        a = (
            d.get(
                'security_answer',
                ''
            )
            .strip()
        )

        if len(password) < 4:

            return jsonify({
                'success': False,
                'error': (
                    'Password too short (min 4)'
                )
            })

        if not a:

            return jsonify({
                'success': False,
                'error': (
                    'Security answer required'
                )
            })

        ok, msg = register_user(
            username,
            password,
            q,
            a
        )

        return jsonify(
            {
                'success': ok,
                'message': msg
            }
            if ok
            else
            {
                'success': False,
                'error': msg
            }
        )

    except Exception as e:

        return server_error(e)


@app.route(
    '/api/get-security-question',
    methods=['POST']
)
def get_question():

    try:

        username = (
            request
            .get_json()
            .get('username', '')
            .strip()
        )

        q = get_security_question(
            username
        )

        return jsonify(
            {
                'success': True,
                'question': q
            }
            if q
            else
            {
                'success': False,
                'error': 'User not found'
            }
        )

    except Exception as e:

        return server_error(e)


@app.route(
    '/api/verify-security-answer',
    methods=['POST']
)
def verify_ans():

    try:

        d = request.get_json() or {}

        ok = verify_security_answer(
            d.get('username', '').strip(),
            d.get('answer', '')
        )

        return jsonify({
            'success': ok,
            **(
                {}
                if ok
                else {
                    'error': 'Wrong answer'
                }
            )
        })

    except Exception as e:

        return server_error(e)


@app.route(
    '/api/reset-password',
    methods=['POST']
)
def reset_pwd():

    try:

        d = request.get_json() or {}

        p = d.get(
            'new_password',
            ''
        )

        if len(p) < 4:

            return jsonify({
                'success': False,
                'error': 'Password too short'
            })

        ok, msg = force_reset_password(
            d.get('username', '').strip(),
            p
        )

        return jsonify(
            {
                'success': ok,
                'message': msg
            }
            if ok
            else
            {
                'success': False,
                'error': msg
            }
        )

    except Exception as e:

        return server_error(e)


# ------------------------------------------------------------------
# System status
# ------------------------------------------------------------------

@app.route('/api/system-status')
def system_status():

    try:

        conn = get_db_connection()
        cur = conn.cursor()

        try:

            cur.execute(
                'SELECT COUNT(*) FROM users'
            )

            users = cur.fetchone()[0]

            cur.execute(
                'SELECT COUNT(*) FROM predictions'
            )

            predictions = cur.fetchone()[0]

        finally:

            cur.close()
            conn.close()

        return jsonify({
            'success': True,
            'authentication': 'Online',
            'ml_predictor': (
                'Ready'
                if model
                else 'Model missing'
            ),
            'database_name': 'TiDB Cloud',
            'users': int(users),
            'predictions': int(predictions)
        })

    except Exception:

        traceback.print_exc()

        return jsonify({
            'success': False
        })


# ------------------------------------------------------------------
# Prediction
# ------------------------------------------------------------------

@app.route(
    '/api/predict',
    methods=['POST']
)
def predict():

    if 'username' not in session:

        return jsonify({
            'success': False,
            'error': 'Login required'
        })

    if model is None:

        return jsonify({
            'success': False,
            'error': (
                'Model not loaded. '
                'Run train_model.py'
            )
        })

    try:

        student_name = (
            request
            .form
            .get('student_name', '')
            .strip()
        )

        subject = (
            request
            .form
            .get('subject', '')
            .strip()
        )

        if not student_name or not subject:

            return jsonify({
                'success': False,
                'error': (
                    'Student name and subject '
                    'are required'
                )
            })

        hours = float(
            request.form['study_hours']
        )

        att = float(
            request.form['attendance']
        )

        ass = float(
            request.form['assignment_score']
        )

        cat = float(
            request.form['cat_score']
        )

        pract = float(
            request.form['practical_score']
        )

        if not (
            0 <= hours <= 20
            and all(
                0 <= v <= 100
                for v in (
                    att,
                    ass,
                    cat,
                    pract
                )
            )
        ):

            return jsonify({
                'success': False,
                'error': (
                    'Check the score ranges'
                )
            })

        features = [[
            hours,
            att,
            ass,
            cat,
            pract
        ]]

        pred = int(
            model.predict(features)[0]
        )

        prob = float(
            model.predict_proba(
                features
            )[0][pred] * 100
        )

        result = (
            'PASS'
            if pred == 1
            else 'FAIL'
        )

        conn = get_db_connection()
        cur = conn.cursor()

        try:

            cur.execute(
                '''
                SELECT id
                FROM students
                WHERE student_name=%s
                LIMIT 1
                ''',
                (student_name,)
            )

            row = cur.fetchone()

            if row:

                sid = row[0]

            else:

                cur.execute(
                    '''
                    INSERT INTO students
                    (student_name)
                    VALUES (%s)
                    ''',
                    (student_name,)
                )

                sid = cur.lastrowid

            cur.execute(
                '''
                INSERT INTO predictions
                (
                    student_id,
                    username,
                    subject,
                    study_hours,
                    attendance,
                    assignment,
                    cat,
                    practical,
                    prediction,
                    probability
                )
                VALUES
                (
                    %s,%s,%s,%s,%s,
                    %s,%s,%s,%s,%s
                )
                ''',
                (
                    sid,
                    session['username'],
                    subject,
                    hours,
                    att,
                    ass,
                    cat,
                    pract,
                    result,
                    round(prob / 100, 4)
                )
            )

            conn.commit()

        finally:

            cur.close()
            conn.close()

        return jsonify({
            'success': True,
            'student_name': student_name,
            'subject': subject,
            'result': result,
            'confidence': round(
                prob,
                2
            )
        })

    except (
        KeyError,
        ValueError
    ):

        return jsonify({
            'success': False,
            'error': (
                'Please fill in all fields '
                'with valid numbers'
            )
        })

    except Exception as e:

        return server_error(e)


# ------------------------------------------------------------------
# History
# ------------------------------------------------------------------

@app.route('/api/history')
def history():

    if 'username' not in session:
        return jsonify([])

    conn = cur = None

    try:

        conn = get_db_connection()

        cur = conn.cursor(
            dictionary=True
        )

        cur.execute(
            '''
            SELECT
                p.created_at AS timestamp,
                COALESCE(
                    s.student_name,
                    'Unknown'
                ) AS student_name,
                p.subject,
                p.study_hours,
                p.attendance,
                p.assignment,
                p.cat,
                p.practical,
                p.prediction,
                ROUND(
                    p.probability * 100,
                    2
                ) AS confidence
            FROM predictions p
            LEFT JOIN students s
                ON p.student_id = s.id
            WHERE p.username=%s
            ORDER BY p.id DESC
            ''',
            (session['username'],)
        )

        rows = []

        for r in cur.fetchall():

            if r.get('timestamp') is not None:

                r['timestamp'] = (
                    r['timestamp']
                    .strftime(
                        '%Y-%m-%d %H:%M:%S'
                    )
                )

            for key in (
                'study_hours',
                'attendance',
                'assignment',
                'cat',
                'practical',
                'confidence'
            ):

                if r.get(key) is not None:

                    r[key] = float(
                        r[key]
                    )

            rows.append(r)

        return jsonify(rows)

    except Exception as e:

        return server_error(e)

    finally:

        if cur:
            cur.close()

        if conn:
            conn.close()


# ------------------------------------------------------------------
# Statistics
# ------------------------------------------------------------------

@app.route('/api/stats')
def stats():

    empty = {
        'total': 0,
        'passed': 0,
        'failed': 0,
        'pass_rate': 0,
        'subjects': []
    }

    if 'username' not in session:
        return jsonify(empty)

    conn = cur = None

    try:

        conn = get_db_connection()

        cur = conn.cursor(
            dictionary=True
        )

        cur.execute(
            '''
            SELECT
                COUNT(*) total,
                COALESCE(
                    SUM(prediction='PASS'),
                    0
                ) passed,
                COALESCE(
                    SUM(prediction='FAIL'),
                    0
                ) failed
            FROM predictions
            WHERE username=%s
            ''',
            (session['username'],)
        )

        s = cur.fetchone()

        total = int(
            s['total']
        )

        passed = int(
            s['passed']
        )

        failed = int(
            s['failed']
        )

        cur.execute(
            '''
            SELECT
                subject,
                COUNT(*) total,
                SUM(prediction='PASS') passed,
                SUM(prediction='FAIL') failed
            FROM predictions
            WHERE username=%s
            GROUP BY subject
            ORDER BY total DESC
            ''',
            (session['username'],)
        )

        subjects = []

        for x in cur.fetchall():

            t = int(
                x['total']
            )

            p = int(
                x['passed'] or 0
            )

            f = int(
                x['failed'] or 0
            )

            subjects.append({
                'subject': x['subject'],
                'total': t,
                'passed': p,
                'failed': f,
                'pass_rate': (
                    round(
                        p / t * 100,
                        2
                    )
                    if t
                    else 0
                )
            })

        return jsonify({
            'total': total,
            'passed': passed,
            'failed': failed,
            'pass_rate': (
                round(
                    passed / total * 100,
                    2
                )
                if total
                else 0
            ),
            'subjects': subjects
        })

    except Exception as e:

        return server_error(e)

    finally:

        if cur:
            cur.close()

        if conn:
            conn.close()


# ------------------------------------------------------------------
# Reports
# ------------------------------------------------------------------

@app.route('/report/pdf')
def report_pdf():

    if 'username' not in session:

        return redirect(
            url_for('login_page')
        )

    try:

        f = generate_pdf_report(
            session['username']
        )

        if not f:

            return (
                'No predictions yet',
                404
            )

        return send_file(
            f,
            as_attachment=True,
            download_name=(
                'student_prediction_report.pdf'
            ),
            mimetype='application/pdf'
        )

    except Exception as e:

        traceback.print_exc()

        return (
            f'Could not generate PDF report: {e}',
            500
        )


@app.route('/report/excel')
def report_excel():

    if 'username' not in session:

        return redirect(
            url_for('login_page')
        )

    try:

        f = generate_excel_report(
            session['username']
        )

        if not f:

            return (
                'No predictions yet',
                404
            )

        return send_file(
            f,
            as_attachment=True,
            download_name=(
                'student_prediction_report.xlsx'
            ),
            mimetype=(
                'application/vnd.openxmlformats-'
                'officedocument.spreadsheetml.sheet'
            )
        )

    except Exception as e:

        traceback.print_exc()

        return (
            f'Could not generate Excel report: {e}',
            500
        )


# ------------------------------------------------------------------
# Run
# ------------------------------------------------------------------

if __name__ == '__main__':

    app.run(
        debug=False
    )
