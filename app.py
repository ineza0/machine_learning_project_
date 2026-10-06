@app.route('/api/predict', methods=['POST'])
def predict():

    if 'username' not in session:
        return jsonify({'success': False, 'error': 'Login required'})

    if model is None:
        return jsonify({
            'success': False,
            'error': 'Model not loaded. Run train_model.py'
        })

    try:
        student_name = request.form.get('student_name', '').strip()
        subject = request.form.get('subject', '').strip()

        if not student_name or not subject:
            return jsonify({
                'success': False,
                'error': 'Student name and subject are required'
            })

        def num(*names):
            for n in names:
                v = request.form.get(n)
                if v is not None and str(v).strip() != '':
                    return float(v)
            raise ValueError('missing ' + names[0])

        hours = num('study_hours')
        att = num('attendance')
        ass = num('assignment_score', 'assignment')
        cat = num('cat_score', 'cat')
        pract = num('practical_score', 'practical')

        if not (
            0 <= hours <= 24
            and all(0 <= v <= 100 for v in (att, ass, cat, pract))
        ):
            return jsonify({
                'success': False,
                'error': 'Check the score ranges'
            })

        features = [[hours, att, ass, cat, pract]]

        pred = int(model.predict(features)[0])
        prob = float(model.predict_proba(features)[0][pred] * 100)
        result = 'PASS' if pred == 1 else 'FAIL'

        conn = get_db_connection()
        cur = conn.cursor()

        try:
            cur.execute(
                'SELECT id FROM students WHERE student_name=%s LIMIT 1',
                (student_name,)
            )
            row = cur.fetchone()

            if row:
                sid = row[0]
            else:
                cur.execute(
                    'INSERT INTO students (student_name) VALUES (%s)',
                    (student_name,)
                )
                sid = cur.lastrowid

            cur.execute(
                '''
                INSERT INTO predictions
                (student_id, username, subject, study_hours, attendance,
                 assignment, cat, practical, prediction, probability)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ''',
                (
                    sid, session['username'], subject, hours, att,
                    ass, cat, pract, result, round(prob / 100, 4)
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
            'confidence': round(prob, 2)
        })

    except (KeyError, ValueError):
        return jsonify({
            'success': False,
            'error': 'Please fill in all fields with valid numbers'
        })

    except Exception as e:
        return server_error(e)
