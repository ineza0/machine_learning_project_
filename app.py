from flask import Flask, request, jsonify, render_template, session, redirect, url_for, send_file
import joblib
import traceback
import os
from auth import *
from db_config import get_db_connection
from report import generate_pdf_report, generate_excel_report

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'change-this-secret-key')

try:
    model = joblib.load('student_model.pkl')
    print('ML model loaded')
except Exception as e:
    print(f'Model not loaded: {e}')
    model = None

@app.route('/')
def home():
    return redirect(url_for('dashboard' if 'username' in session else 'login_page'))

@app.route('/login')
def login_page(): return render_template('login.html')
@app.route('/signup')
def signup_page(): return render_template('signup.html')
@app.route('/forgot')
def forgot_page(): return render_template('forgot.html')

@app.route('/api/login', methods=['POST'])
def api_login():
    try:
        data=request.get_json() or {}; username=data.get('username','').strip(); password=data.get('password','')
        ok,msg=login_user(username,password)
        if ok:
            session['username']=username
            return jsonify({'success':True,'message':msg,'redirect':url_for('dashboard')})
        return jsonify({'success':False,'error':msg})
    except Exception as e: return jsonify({'success':False,'error':str(e)}),500

@app.route('/api/signup', methods=['POST'])
def api_signup():
    try:
        d=request.get_json() or {}; username=d.get('username','').strip(); password=d.get('password',''); q=d.get('security_question','').strip(); a=d.get('security_answer','').strip()
        if len(password)<4: return jsonify({'success':False,'error':'Password too short (min 4)'})
        if not a: return jsonify({'success':False,'error':'Security answer required'})
        ok,msg=register_user(username,password,q,a)
        return jsonify({'success':ok,'message':msg} if ok else {'success':False,'error':msg})
    except Exception as e: return jsonify({'success':False,'error':str(e)}),500

@app.route('/api/get-security-question', methods=['POST'])
def get_question():
    try:
        username=(request.get_json() or {}).get('username','').strip(); q=get_security_question(username)
        return jsonify({'success':True,'question':q} if q else {'success':False,'error':'User not found'})
    except Exception as e: return jsonify({'success':False,'error':str(e)}),500

@app.route('/api/verify-security-answer', methods=['POST'])
def verify_ans():
    try:
        d=request.get_json() or {}; ok=verify_security_answer(d.get('username','').strip(),d.get('answer',''))
        return jsonify({'success':ok, **({} if ok else {'error':'Wrong answer'})})
    except Exception as e: return jsonify({'success':False,'error':str(e)}),500

@app.route('/api/reset-password', methods=['POST'])
def reset_pwd():
    try:
        d=request.get_json() or {}; p=d.get('new_password','')
        if len(p)<4: return jsonify({'success':False,'error':'Password too short'})
        ok,msg=force_reset_password(d.get('username','').strip(),p)
        return jsonify({'success':ok,'message':msg} if ok else {'success':False,'error':msg})
    except Exception as e: return jsonify({'success':False,'error':str(e)}),500

@app.route('/logout')
def logout():
    session.clear(); return redirect(url_for('login_page'))

@app.route('/dashboard')
def dashboard():
    if 'username' not in session: return redirect(url_for('login_page'))
    return render_template('index.html',username=session['username'])

@app.route('/api/predict', methods=['POST'])
def predict():
    if 'username' not in session: return jsonify({'success':False,'error':'Login required'})
    if model is None: return jsonify({'success':False,'error':'Model not loaded. Run train_model.py'})
    try:
        student_name=request.form.get('student_name','').strip()
        subject=request.form.get('subject','').strip()
        if not student_name or not subject: return jsonify({'success':False,'error':'Student name and subject are required'})
        hours=float(request.form['study_hours']); att=float(request.form['attendance']); ass=float(request.form['assignment_score']); cat=float(request.form['cat_score']); pract=float(request.form['practical_score'])
        if not (0<=hours<=20 and 0<=att<=100 and 0<=ass<=100 and 0<=cat<=100 and 0<=pract<=100):
            return jsonify({'success':False,'error':'Check the score ranges'})
        pred=int(model.predict([[hours,att,ass,cat,pract]])[0])
        prob=float(model.predict_proba([[hours,att,ass,cat,pract]])[0][pred]*100)
        result='PASS' if pred==1 else 'FAIL'
        conn=get_db_connection(); cur=conn.cursor()
        try:
            cur.execute('SELECT id FROM students WHERE student_name=%s LIMIT 1',(student_name,)); row=cur.fetchone()
            if row: sid=row[0]
            else:
                cur.execute('INSERT INTO students (student_name) VALUES (%s)',(student_name,)); sid=cur.lastrowid
            cur.execute('''INSERT INTO predictions (student_id, username, subject, study_hours, attendance, assignment, cat, practical, prediction, probability) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',(sid,session['username'],subject,hours,att,ass,cat,pract,result,round(prob/100,4)))
            conn.commit()
        finally: cur.close(); conn.close()
        return jsonify({'success':True,'student_name':student_name,'subject':subject,'result':result,'confidence':round(prob,2)})
    except Exception as e:
        traceback.print_exc(); return jsonify({'success':False,'error':str(e)}),500

@app.route('/api/history')
def history():
    if 'username' not in session: return jsonify([])
    conn = cur = None
    try:
        conn = get_db_connection(); cur = conn.cursor(dictionary=True)
        # Do not use MySQL DATE_FORMAT here. mysql-connector treats % sequences
        # as parameter markers when prepared statements are used. Return the
        # native DATETIME and format it in Python instead.
        cur.execute("""
            SELECT p.created_at AS timestamp,
                   COALESCE(s.student_name, 'Unknown') AS student_name,
                   p.subject, p.study_hours, p.attendance,
                   p.assignment, p.cat, p.practical, p.prediction,
                   ROUND(p.probability*100,2) AS confidence
            FROM predictions p
            LEFT JOIN students s ON p.student_id=s.id
            WHERE p.username=%s
            ORDER BY p.id DESC
        """, (session['username'],))
        rows = []
        for r in cur.fetchall():
            if r.get('timestamp') is not None:
                r['timestamp'] = r['timestamp'].strftime('%Y-%m-%d %H:%M:%S')
            for key in ('study_hours', 'attendance', 'assignment', 'cat', 'practical', 'confidence'):
                if r.get(key) is not None:
                    r[key] = float(r[key])
            rows.append(r)
        return jsonify(rows)
    except Exception as e:
        traceback.print_exc()
        return jsonify({'success':False,'error':str(e)}), 500
    finally:
        if cur: cur.close()
        if conn: conn.close()

@app.route('/api/stats')
def stats():
    if 'username' not in session: return jsonify({'total':0,'passed':0,'failed':0,'pass_rate':0,'subjects':[]})
    conn=get_db_connection(); cur=conn.cursor(dictionary=True)
    try:
        cur.execute("""SELECT COUNT(*) total,COALESCE(SUM(prediction='PASS'),0) passed,COALESCE(SUM(prediction='FAIL'),0) failed FROM predictions WHERE username=%s""",(session['username'],)); s=cur.fetchone(); total=int(s['total']); passed=int(s['passed']); failed=int(s['failed'])
        cur.execute("""SELECT subject,COUNT(*) total,SUM(prediction='PASS') passed,SUM(prediction='FAIL') failed FROM predictions WHERE username=%s GROUP BY subject ORDER BY total DESC""",(session['username'],)); subjects=cur.fetchall()
        for x in subjects: x['pass_rate']=round(x['passed']/x['total']*100,2) if x['total'] else 0
        return jsonify({'total':total,'passed':passed,'failed':failed,'pass_rate':round(passed/total*100,2) if total else 0,'subjects':subjects})
    finally:
        cur.close(); conn.close()

@app.route('/report/pdf')
def report_pdf():
    if 'username' not in session: return redirect(url_for('login_page'))
    try:
        f = generate_pdf_report(session['username'])
        if not f: return ('No predictions yet', 404)
        return send_file(f, as_attachment=True, download_name='student_prediction_report.pdf', mimetype='application/pdf')
    except Exception as e:
        traceback.print_exc()
        return (f'Could not generate PDF report: {e}', 500)

@app.route('/report/excel')
def report_excel():
    if 'username' not in session: return redirect(url_for('login_page'))
    try:
        f = generate_excel_report(session['username'])
        if not f: return ('No predictions yet', 404)
        return send_file(f, as_attachment=True, download_name='student_prediction_report.xlsx', mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    except Exception as e:
        traceback.print_exc()
        return (f'Could not generate Excel report: {e}', 500)

if __name__=='__main__': app.run(debug=False)
