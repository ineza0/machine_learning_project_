import os
import mysql.connector


def get_db_connection():
    return mysql.connector.connect(
        host=os.getenv('DB_HOST'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        database=os.getenv('DB_NAME', 'student_prediction'),
        port=int(os.getenv('DB_PORT', '4000')),
        ssl_verify_cert=False,
        ssl_verify_identity=False
    )
