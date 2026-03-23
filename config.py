import os

# ==============================
# SYSTEM SETTINGS
# ==============================
API_KEY = "SMARTDISPENSER12345"
SERVER_PORT = 5000

DEFAULT_SLOTS = 14
MAX_SLOTS = 50

# ==============================
# EMAIL CONFIG
# ==============================
GMAIL_SENDER = "smartdispenser.system@gmail.com"
GMAIL_APP_PASSWORD = "bncq nmgg nlqe jymo"

# ==============================
# DATABASE CONFIG (RAILWAY)
# ==============================
MYSQL_HOST = os.getenv("MYSQLHOST")
MYSQL_USER = os.getenv("MYSQLUSER")
MYSQL_PASSWORD = os.getenv("MYSQLPASSWORD")
MYSQL_DB = os.getenv("MYSQLDATABASE")
MYSQL_PORT = int(os.getenv("MYSQLPORT", 3306))