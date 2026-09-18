from db_config import get_db_connection

try:
    db = get_db_connection()
    print("MySQL connection successful!")
    db.close()
except Exception as e:
    print("MySQL connection failed:")
    print(e)