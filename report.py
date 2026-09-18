import io
from datetime import datetime
import pandas as pd
import matplotlib
matplotlib.use('Agg')
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors
from reportlab.lib.units import inch
from db_config import get_db_connection


def get_user_df(username):
    conn = get_db_connection()
    cur = conn.cursor(dictionary=True)
    try:
        # Return the native DATETIME. Formatting dates in SQL with % tokens
        # conflicts with mysql-connector-python parameter parsing.
        cur.execute("""
            SELECT p.created_at AS timestamp,
                   COALESCE(s.student_name,'Unknown') AS student_name,
                   p.subject, p.study_hours, p.attendance, p.assignment,
                   p.cat, p.practical, p.prediction,
                   ROUND(p.probability*100,2) AS confidence
            FROM predictions p
            LEFT JOIN students s ON p.student_id=s.id
            WHERE p.username=%s
            ORDER BY p.id DESC
        """, (username,))
        rows = cur.fetchall()
        df = pd.DataFrame(rows)
        if not df.empty and 'timestamp' in df.columns:
            df['timestamp'] = pd.to_datetime(df['timestamp'], errors='coerce').dt.strftime('%Y-%m-%d %H:%M:%S')
        return df
    finally:
        cur.close()
        conn.close()


def generate_pdf_report(username):
    df = get_user_df(username)
    if df.empty:
        return None

    passed = int((df['prediction'] == 'PASS').sum())
    failed = int((df['prediction'] == 'FAIL').sum())
    total = len(df)
    rate = passed / total * 100 if total else 0

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4,
                            topMargin=.5*inch, bottomMargin=.5*inch,
                            leftMargin=.45*inch, rightMargin=.45*inch)
    styles = getSampleStyleSheet()
    story = [
        Paragraph('Student Performance Report', styles['Title']),
        Paragraph(f'<b>Username:</b> {username}', styles['Normal']),
        Paragraph(f'<b>Date:</b> {datetime.now():%Y-%m-%d %H:%M:%S}', styles['Normal']),
        Spacer(1, 15)
    ]

    summary = [
        ['Metric', 'Value'],
        ['Total Predictions', total],
        ['Passed', passed],
        ['Failed', failed],
        ['Pass Rate', f'{rate:.2f}%'],
        ['Fail Rate', f'{100-rate:.2f}%']
    ]
    t = Table(summary, colWidths=[2.7*inch, 2.2*inch])
    t.setStyle(TableStyle([
        ('BACKGROUND',(0,0),(-1,0),colors.HexColor('#667eea')),
        ('TEXTCOLOR',(0,0),(-1,0),colors.white),
        ('GRID',(0,0),(-1,-1),.5,colors.grey),
        ('ALIGN',(0,0),(-1,-1),'CENTER'),
        ('BOTTOMPADDING',(0,0),(-1,-1),6)
    ]))
    story += [t, Spacer(1, 18), Paragraph('Prediction History', styles['Heading2'])]

    hist = [['Date','Student','Subject','Result','Confidence']]
    for r in df.head(100).itertuples(index=False):
        hist.append([
            str(r.timestamp)[:16], str(r.student_name), str(r.subject),
            str(r.prediction), f'{r.confidence}%'
        ])
    ht = Table(hist, colWidths=[1.15*inch, 1.45*inch, 1.55*inch, .75*inch, .85*inch], repeatRows=1)
    ht.setStyle(TableStyle([
        ('BACKGROUND',(0,0),(-1,0),colors.HexColor('#667eea')),
        ('TEXTCOLOR',(0,0),(-1,0),colors.white),
        ('GRID',(0,0),(-1,-1),.4,colors.grey),
        ('FONTSIZE',(0,0),(-1,-1),8),
        ('ALIGN',(0,0),(-1,-1),'CENTER'),
        ('VALIGN',(0,0),(-1,-1),'MIDDLE')
    ]))
    story.append(ht)
    doc.build(story)
    buffer.seek(0)
    return buffer


def generate_excel_report(username):
    df = get_user_df(username)
    if df.empty:
        return None

    passed = int((df['prediction'] == 'PASS').sum())
    failed = int((df['prediction'] == 'FAIL').sum())
    total = len(df)

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
        summary = pd.DataFrame({
            'Metric': ['Username','Total Predictions','Passed','Failed','Pass Rate','Fail Rate'],
            'Value': [username, total, passed, failed,
                      f'{passed/total*100:.2f}%' if total else '0.00%',
                      f'{failed/total*100:.2f}%' if total else '0.00%']
        })
        summary.to_excel(writer, sheet_name='Summary', index=False)
        df.to_excel(writer, sheet_name='Prediction History', index=False)

        subject_stats = (df.groupby('subject')['prediction']
                         .agg(total='size',
                              passed=lambda x: (x == 'PASS').sum(),
                              failed=lambda x: (x == 'FAIL').sum())
                         .reset_index())
        subject_stats['pass_rate'] = (subject_stats['passed'] / subject_stats['total'] * 100).round(2)
        subject_stats.to_excel(writer, sheet_name='Subject Statistics', index=False)

        # Simple formatting
        for ws in writer.book.worksheets:
            ws.freeze_panes = 'A2'
            for col in ws.columns:
                max_len = max(len(str(c.value or '')) for c in col)
                ws.column_dimensions[col[0].column_letter].width = min(max(max_len + 2, 12), 35)

    buffer.seek(0)
    return buffer
