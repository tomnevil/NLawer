import psycopg2

try:
    conn = psycopg2.connect(host="localhost", port=5433, user="nlawer", password="nlawer", dbname="nlawer")
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("DROP DATABASE IF EXISTS nlawer_mig")
    cur.execute("CREATE DATABASE nlawer_mig")
    print("created nlawer_mig OK")
except Exception as e:
    print("create failed:", repr(e))
