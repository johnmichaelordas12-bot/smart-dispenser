import pymysql
pymysql.install_as_MySQLdb()
from flask import Flask, render_template, request, jsonify, redirect, url_for, session, send_file
from flask_sqlalchemy import SQLAlchemy
from flask import jsonify, request
from flask_migrate import Migrate
from datetime import datetime, timedelta
from datetime import datetime as dt
from werkzeug.security import generate_password_hash, check_password_hash
from apscheduler.schedulers.background import BackgroundScheduler
from functools import wraps
from sqlalchemy import func
import json
import pandas as pd
import os
from io import BytesIO
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from models import db, User, Slot, Medicine, MedicineSchedule, Intake, Notification

import numpy as np

import joblib
import pandas as pd
from datetime import datetime, timedelta

try:
    MODEL_BUNDLE = joblib.load("model_lgbm.pkl")
    MODEL = MODEL_BUNDLE["model"]
    FEATURES = MODEL_BUNDLE.get("features", [])
    WINDOW_MINUTES = int(MODEL_BUNDLE.get("window_minutes", 60))
except Exception as e:
    MODEL_BUNDLE = {}
    MODEL = None
    FEATURES = []
    WINDOW_MINUTES = 60
    print("ML MODEL LOAD ERROR:", e)






import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


import pytz
tz = pytz.timezone("Asia/Manila")
UTC = pytz.UTC


GMAIL_SENDER = os.getenv("GMAIL_SENDER")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")

def to_utc_naive(dt_aware):
    # dt_aware: timezone-aware datetime (Asia/Manila)
    return dt_aware.astimezone(UTC).replace(tzinfo=None)


def now_ph():
    return datetime.now(tz)

def current_period_ph(dt):
    return "AM" if dt.hour < 12 else "PM"

def current_slot_number_ph(dt=None):
    dt = dt or now_ph()
    # Monday=0 .. Sunday=6
    weekday = dt.weekday()
    base = weekday * 2 + 1
    return base if dt.hour < 12 else base + 1

def ph_date_of_utc(dt_utc):
    # dt_utc is stored as UTC naive/aware
    local = to_manila(dt_utc)
    return local.date() if local else None


def to_manila(dt):
    if not dt:
        return None
    # MySQL DATETIME -> naive datetime (no tzinfo). Treat it as UTC.
    if dt.tzinfo is None:
        dt = UTC.localize(dt)
    return dt.astimezone(tz)

def format_hhmm_to_ampm(hhmm):
    if not hhmm or hhmm == "-":
        return "-"
    try:
        t = datetime.strptime(hhmm, "%H:%M")
        return t.strftime("%I:%M %p")
    except:
        return hhmm


# ---------- Config ----------
from config import API_KEY, SERVER_PORT, DEFAULT_SLOTS

# ---------- App Setup ----------
app = Flask(__name__)
app.secret_key = "supersecretkey"
db_user = os.getenv("MYSQLUSER")
db_pass = os.getenv("MYSQLPASSWORD")
db_host = os.getenv("MYSQLHOST")
db_name = os.getenv("MYSQLDATABASE")
db_port = os.getenv("MYSQLPORT", "3306")

app.config['SQLALCHEMY_DATABASE_URI'] = f"mysql+pymysql://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)
migrate = Migrate(app, db)

@app.route('/test', methods=['GET'])
def test():
    return jsonify({"status": "Flask reachable"})

# ---------- API Key Decorator ----------
# ---------- API Key Decorator ----------
def require_api_key(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        key = request.headers.get("x-api-key")
        if not key or key != API_KEY:
            return jsonify({"error": "Unauthorized - invalid or missing API key"}), 401
        return f(*args, **kwargs)
    return decorated

# ---------- Login / Role Decorators ----------
def login_required_page(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login_page'))
        return f(*args, **kwargs)
    return decorated

def admin_required_page(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login_page'))

        u = User.query.get(session['user_id'])
        if not u or getattr(u, "role", "user") != "admin":
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated

def admin_required_api(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({"error": "Unauthorized"}), 401

        u = User.query.get(session['user_id'])
        if not u or getattr(u, "role", "user") != "admin":
            return jsonify({"error": "Forbidden (Admin only)"}), 403
        return f(*args, **kwargs)
    return decorated

@app.route('/settings')
@admin_required_page
def settings_page():
    return render_template('settings.html')


@app.route('/users')
@admin_required_page
def users_page():
    users = User.query.order_by(User.id.desc()).all()
    return render_template('users.html', users=users)


@app.route('/api/users/<int:user_id>', methods=['DELETE'])
@admin_required_api
def delete_user(user_id):
    user = User.query.get(user_id)

    if not user:
        return jsonify({"error": "User not found"}), 404

    if user.id == session.get('user_id'):
        return jsonify({"error": "You cannot delete your own admin account while logged in"}), 400

    try:
        db.session.delete(user)
        db.session.commit()
        return jsonify({"message": "User deleted successfully"}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"Delete failed: {str(e)}"}), 500

# ---------- Auth Routes ----------
@app.route('/register', methods=['GET'])
@admin_required_page
def register_page():
    return render_template('register.html')

@app.route('/register', methods=['POST'])
@admin_required_api
def register_user():
    data = request.json or {}
    name = (data.get('name') or "").strip()
    email = (data.get('email') or "").strip().lower()
    password = data.get('password') or ""

    if not all([name, email, password]):
        return jsonify({"error": "All fields are required"}), 400

    if User.query.filter_by(email=email).first():
        return jsonify({"error": "Email already registered"}), 400

    hashed_password = generate_password_hash(password)

    # ✅ Force role="user" (patient/user)
    new_user = User(name=name, email=email, password=hashed_password, role="user")
    db.session.add(new_user)
    db.session.commit()
    return jsonify({"message": "User account created successfully!"}), 201

@app.route('/login', methods=['GET'])
def login_page():
    return render_template('login.html')


@app.route('/login', methods=['POST'])
def login_user():
    try:
        data = request.get_json(silent=True) or request.form

        print("LOGIN DATA:", data)

        username = (data.get('username') or "").strip()
        password = data.get('password') or ""

        user = User.query.filter_by(name=username).first()

        if not user or not check_password_hash(user.password, password):
            return jsonify({"error": "Invalid username or password"}), 401

        session['user_id'] = user.id
        session['user_name'] = user.name
        session['user_role'] = getattr(user, "role", "user")

        return jsonify({"message": "Login successful"}), 200

    except Exception as e:
        print("LOGIN ERROR:", e)
        return jsonify({"error": "Server error"}), 500

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login_page'))



# ---------- Dashboard ----------
@app.route('/')
@login_required_page
def dashboard():
    return render_template('dashboard.html', user_name=session.get('user_name', 'User'))

@app.route('/api/dashboard_analytics', methods=['GET'])
@login_required_page
def dashboard_analytics():
    rows = _get_dashboard_cycle_intakes()

    total = len(rows)
    taken = sum(1 for r in rows if r.taken)
    missed = total - taken
    compliance = round((taken / total * 100) if total > 0 else 0, 2)

    cycle_start, cycle_end = _get_current_cycle_date_range()

    return jsonify({
        "total": total,
        "taken": taken,
        "missed": missed,
        "compliance": compliance,
        "cycle_start": cycle_start.isoformat() if cycle_start else None,
        "cycle_end": cycle_end.isoformat() if cycle_end else None
    })

# ---------- Manage Page ----------
@app.route("/manage", methods=["GET"])
@login_required_page
def manage():
    slots = Slot.query.order_by(Slot.slot_number).all()
    medicines = Medicine.query.order_by(Medicine.name).all()
    return render_template("manage.html", slots=slots, medicines=medicines)

def _day_number_from_date(target_date):
    if isinstance(target_date, datetime):
        target_date = target_date.date()

    today = now_ph().date()

    # Current cycle is relative to TODAY
    # Today is always the reference point.

    diff_days = (target_date - today).days

    # Today = Day 1
    return (diff_days % 4) + 1

def _parse_hhmm(time_str):
    return dt.strptime(time_str, "%H:%M").time()


def _schedule_sort_key(sched):
    sched_date = sched.date or now_ph().date()
    sched_time = _parse_hhmm(sched.time) if sched.time else dt.strptime("00:00", "%H:%M").time()
    return (sched_date, sched_time, sched.id)

def _is_am_time(time_str):
    return int(time_str.split(":")[0]) < 12


def _allowed_slot_numbers(day_number, is_am):
    if day_number == 1:
        return [1, 2] if is_am else [3, 4]
    elif day_number == 2:
        return [5, 6] if is_am else [7, 8]
    elif day_number == 3:
        return [9, 10] if is_am else [11, 12]
    else:
        return [13] if is_am else [14]


def _schedule_datetime(schedule):
    return dt.combine(schedule.date, dt.strptime(schedule.time, "%H:%M").time())

def _get_current_cycle_anchor_date():
    """
    Returns the anchor date of the CURRENT active cycle.
    Rule:
    - Look for the earliest active schedule date.
    - That earliest active schedule date becomes Day 1 anchor of the current cycle.
    - If there are no active schedules, there is no current cycle.
    """
    first_active = (
        MedicineSchedule.query
        .filter(MedicineSchedule.is_active == True)
        .filter(MedicineSchedule.date.isnot(None))
        .order_by(MedicineSchedule.date.asc(), MedicineSchedule.time.asc(), MedicineSchedule.id.asc())
        .first()
    )

    if not first_active or not first_active.date:
        return None

    return first_active.date


def _get_current_cycle_date_range():
    """
    Current cycle = 4-day window starting from current active anchor.
    Example:
      anchor = 2026-03-15
      cycle dates = 2026-03-15 to 2026-03-18
    """
    anchor = _get_current_cycle_anchor_date()
    if not anchor:
        return None, None

    cycle_start = anchor
    cycle_end = anchor + timedelta(days=3)
    return cycle_start, cycle_end


def _get_dashboard_cycle_intakes():
    """
    Returns all intake rows that belong to the CURRENT cycle only.
    We identify cycle membership using the related MedicineSchedule.date,
    not by intake creation time.

    Important:
    - includes already completed schedules in the current cycle
    - once there are no active schedules left, dashboard becomes 0
    - when a new cycle starts, dashboard automatically resets
    """
    cycle_start, cycle_end = _get_current_cycle_date_range()

    if not cycle_start or not cycle_end:
        return []

    rows = (
        Intake.query
        .join(MedicineSchedule, Intake.scheduled_id == MedicineSchedule.id)
        .join(Slot, Intake.slot_id == Slot.id)
        .filter(MedicineSchedule.date.isnot(None))
        .filter(MedicineSchedule.date >= cycle_start)
        .filter(MedicineSchedule.date <= cycle_end)
        .filter(Slot.slot_number >= 1, Slot.slot_number <= 14)
        .all()
    )

    return rows


def _rebuild_slots_by_cycle():
    """
    Reassign active schedules according to:
    - 4-day cycle
    - AM/PM slot pools
    - chronological order inside each pool
    """
    active_schedules = (
        MedicineSchedule.query
        .filter(MedicineSchedule.is_active == True)
        .filter(MedicineSchedule.date.isnot(None))
        .filter(MedicineSchedule.time.isnot(None))
        .all()
    )

    grouped = {}

    for sched in active_schedules:
        day_number = _day_number_from_date(sched.date)
        is_am = _is_am_time(sched.time)
        key = (day_number, is_am)

        if key not in grouped:
            grouped[key] = []
        grouped[key].append(sched)

    # sort each pool chronologically
    for key in grouped:
        grouped[key].sort(key=lambda s: (_schedule_datetime(s), s.id))

    # assign schedules only inside their valid slot pool
    for (day_number, is_am), schedules in grouped.items():
        allowed_numbers = _allowed_slot_numbers(day_number, is_am)

        if len(schedules) > len(allowed_numbers):
            label = "AM" if is_am else "PM"
            raise ValueError(
                f"Too many schedules for Day {day_number} {label}. "
                f"Allowed slots: {allowed_numbers}"
            )

        for i, sched in enumerate(schedules):
            slot_number = allowed_numbers[i]
            slot = Slot.query.filter_by(slot_number=slot_number).first()
            if not slot:
                raise ValueError(f"Slot {slot_number} not found.")
            sched.slot_id = slot.id

@app.route("/create_admin")
def create_admin():
    from werkzeug.security import generate_password_hash

    if not User.query.filter_by(name="admin").first():
        user = User(
            name="admin",
            email="admin@gmail.com",
            password=generate_password_hash("admin"),
            role="admin"
        )
        db.session.add(user)
        db.session.commit()
        return "Admin created"
    
    return "Admin already exists"

@app.route("/api/schedules", methods=["POST"])
@admin_required_api
def create_schedule():
    data = request.json or {}

    med_name = (data.get("medicine_name") or "").strip()
    date_str = (data.get("date") or "").strip()   # "YYYY-MM-DD"
    time_str = (data.get("time") or "").strip()   # "HH:MM"

    if not med_name or not date_str or not time_str:
        return jsonify({"error": "medicine_name, date, and time are required"}), 400

    # --- validate date ---
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
    except Exception:
        return jsonify({"error": "Invalid date format. Use YYYY-MM-DD"}), 400

    # ✅ block past dates
    if d < now_ph().date():
        return jsonify({"error": "Past dates are not allowed."}), 400

    # --- validate time ---
    try:
        datetime.strptime(time_str, "%H:%M")
    except Exception:
        return jsonify({"error": "Invalid time format. Use HH:MM"}), 400

    # AM/PM based on time
    hour = int(time_str.split(":")[0])
    is_am = hour < 12

    # find or create medicine
    med = Medicine.query.filter(func.lower(Medicine.name) == med_name.lower()).first()
    if not med:
        med = Medicine(name=med_name)
        db.session.add(med)
        db.session.flush()

    # determine day_number (1..4 cycle)
    day_number = _day_number_from_date(d)

    # ✅ FIXED SLOT MAP per day_number + period
    # Day 1: AM (1,2) PM (3,4)
    # Day 2: AM (5,6) PM (7,8)
    # Day 3: AM (9,10) PM (11,12)
    # Day 4: AM (13) PM (14)
    if day_number == 1:
        candidate_slot_numbers = [1, 2] if is_am else [3, 4]
    elif day_number == 2:
        candidate_slot_numbers = [5, 6] if is_am else [7, 8]
    elif day_number == 3:
        candidate_slot_numbers = [9, 10] if is_am else [11, 12]
    else:  # day_number == 4
        candidate_slot_numbers = [13] if is_am else [14]

    # pick first empty slot among candidates
    chosen_slot = None
    for sn in candidate_slot_numbers:
        s = Slot.query.filter_by(slot_number=sn).first()
        if not s:
            continue

        # ✅ slot considered "occupied" if it already has ANY active schedule
        occupied = (
            MedicineSchedule.query
            .filter_by(slot_id=s.id, is_active=True)
            .first()
        )
        if not occupied:
            chosen_slot = s
            break

    if not chosen_slot:
        # AM group full or PM group full for that day cycle
        return jsonify({"error": "this slot has already have a scheduled time"}), 409

    # create schedule (one per slot)
    sched = MedicineSchedule(
        slot_id=chosen_slot.id,
        medicine_id=med.id,
        date=d,
        time=time_str,
        is_active=True,
        status="Active"
    )
    db.session.add(sched)

    try:
        db.session.flush()
        _rebuild_slots_by_cycle()
        db.session.commit()
        db.session.refresh(sched)
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"Create failed: {str(e)}"}), 400

    new_slot = Slot.query.get(sched.slot_id)

    return jsonify({
        "message": "Schedule created",
        "schedule_id": sched.id,
        "slot_number": new_slot.slot_number if new_slot else None,
        "day_number": _day_number_from_date(d),
        "period": "AM" if _is_am_time(time_str) else "PM"
    }), 201

@app.route("/api/slots/<int:slot_number>/schedule", methods=["PUT"])
@admin_required_api
def update_slot_schedule(slot_number):
    data = request.json or {}

    med_name = (data.get("medicine_name") or "").strip()
    date_str = (data.get("date") or "").strip()
    time_str = (data.get("time") or "").strip()

    if not med_name or not date_str or not time_str:
        return jsonify({"error": "medicine_name, date, and time are required"}), 400

    try:
        d = dt.strptime(date_str, "%Y-%m-%d").date()
    except Exception:
        return jsonify({"error": "Invalid date format. Use YYYY-MM-DD"}), 400

    if d < now_ph().date():
        return jsonify({"error": "Past dates are not allowed."}), 400

    try:
        dt.strptime(time_str, "%H:%M")
    except Exception:
        return jsonify({"error": "Invalid time format. Use HH:MM"}), 400

    slot = Slot.query.filter_by(slot_number=slot_number).first()
    if not slot:
        return jsonify({"error": "Slot not found"}), 404

    # find or create medicine
    med = Medicine.query.filter(func.lower(Medicine.name) == med_name.lower()).first()
    if not med:
        med = Medicine(name=med_name)
        db.session.add(med)
        db.session.flush()

    # get current active schedule of this slot
    old_sched = (
        MedicineSchedule.query
        .filter_by(slot_id=slot.id, is_active=True)
        .order_by(MedicineSchedule.id.desc())
        .first()
    )

    if not old_sched:
        return jsonify({"error": f"No active schedule found in Slot {slot_number}"}), 404

    # update the SAME active schedule
    old_sched.medicine_id = med.id
    old_sched.date = d
    old_sched.time = time_str
    old_sched.status = "Active"

    try:
        _rebuild_slots_by_cycle()
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"Reorder failed: {str(e)}"}), 400

    # get new slot after reorder
    db.session.refresh(old_sched)
    new_slot = Slot.query.get(old_sched.slot_id)

    return jsonify({
        "message": "Slot schedule updated and reordered successfully",
        "schedule_id": old_sched.id,
        "old_slot_number": slot_number,
        "new_slot_number": new_slot.slot_number if new_slot else slot_number,
        "date": old_sched.date.isoformat(),
        "time": old_sched.time,
        "medicine_name": med.name
    }), 200

# ---------- API: Get Slots ----------
@app.route("/api/slots", methods=["GET"])
def get_slots():
    slots = Slot.query.order_by(Slot.slot_number).all()
    result = []

    for s in slots:
        schedules = (
            MedicineSchedule.query
            .filter_by(slot_id=s.id, is_active=True)
            .order_by(MedicineSchedule.date.asc(), MedicineSchedule.time.asc())
            .all()
        )

        med_list = [{
            "id": sch.id,
            "name": sch.medicine.name if sch.medicine else "-",
            "time": sch.time,
            "date": sch.date.isoformat() if sch.date else None
        } for sch in schedules]

        result.append({
            "slot_number": s.slot_number,
            "day_number": s.day_number,
            "slot_in_day": s.slot_in_day,
            "medicines": med_list
        })

    return jsonify(result)


# ---------- API: Delete Slot ----------
@app.route("/api/slots/<int:slot_number>", methods=["DELETE"])
@admin_required_api
def delete_slot(slot_number):
    slot = Slot.query.filter_by(slot_number=slot_number).first()
    if not slot:
        return jsonify({"error": "Slot not found"}), 404

    # ✅ Soft delete schedules
    MedicineSchedule.query.filter_by(slot_id=slot.id, is_active=True).update(
        {"is_active": False},
        synchronize_session=False
    )
    db.session.commit()

    return jsonify({"message": f"Slot {slot_number} cleared successfully"})


@app.route('/api/intake', methods=['POST'])
def intake():
    data = request.json or {}

    slot_id = data.get('slot_id')
    scheduled_id = data.get('scheduled_id')  # MUST be provided
    taken = bool(data.get('taken', True))
    notification_id = data.get('notification_id')

    if not slot_id or not scheduled_id:
        return jsonify({"error": "slot_id and scheduled_id are required"}), 400

    sched = MedicineSchedule.query.get(int(scheduled_id))
    if not sched:
        return jsonify({"error": "Invalid scheduled_id"}), 400

    # ✅ scheduled_time should be the alarm time, not "now"
    scheduled_time_utc = datetime.utcnow()
    if sched.date and sched.time:
        try:
            h, m = map(int, sched.time.split(":"))
            local_alarm = tz.localize(datetime(sched.date.year, sched.date.month, sched.date.day, h, m, 0))
            scheduled_time_utc = to_utc_naive(local_alarm)
        except:
            pass

    now_utc = datetime.utcnow()

    new_intake = Intake(
        slot_id=slot_id,
        scheduled_id=sched.id,
        notification_id=notification_id if notification_id else None,
        scheduled_time=scheduled_time_utc,          # ✅ alarm datetime
        taken=taken,
        taken_at=now_utc if taken else None         # ✅ actual taken time
    )

    db.session.add(new_intake)
    db.session.commit()

    return jsonify({"message": "Intake recorded successfully"})

# ---------- Analytics ----------
@app.route('/analytics')
@login_required_page
def analytics_page():
    return render_template('analytics.html', user_name=session.get('user_name', 'User'))

@app.route('/api/analytics', methods=['GET'])
@login_required_page
def analytics():
    total = Intake.query.count()
    taken = Intake.query.filter_by(taken=True).count()
    missed = total - taken
    compliance = round((taken/total*100) if total>0 else 0,2)
    return jsonify({
        "total": total,
        "taken": taken,
        "missed": missed,
        "compliance": compliance
    })

@app.route('/api/analytics_detail', methods=['GET'])
@login_required_page
def analytics_detail():
    # --- summary (reuse your existing logic) ---
    total = Intake.query.count()
    taken = Intake.query.filter_by(taken=True).count()
    missed = total - taken
    compliance = round((taken / total * 100) if total > 0 else 0, 2)

    # --- Compliance trend last 30 days (PH date) ---
    today = now_ph().date()
    labels = []
    trend = []

    for i in range(29, -1, -1):
        d = today - timedelta(days=i)
        # count taken/missed by PH date
        taken_d = 0
        total_d = 0

        rows = Intake.query.all()  # small project: OK; if big dataset we optimize later
        for r in rows:
            if not r.scheduled_time:
                continue
            local_date = to_manila(r.scheduled_time).date()
            if local_date == d:
                total_d += 1
                if r.taken:
                    taken_d += 1

        labels.append(d.strftime("%b %d"))
        trend.append(round((taken_d / total_d * 100) if total_d > 0 else 0, 2))

    # --- Daily pills taken per slot (last 7 days) ---
    # We use Slot 1..14, but you can keep only the slots that exist in DB.
    days = []
    for i in range(6, -1, -1):
        days.append((today - timedelta(days=i)))

    slot_numbers = list(range(1, 15))  # Slot 1..14
    daily_slot_data = []  # list of arrays, one per slot

    # Preload all intakes once
    all_intakes = Intake.query.all()

    for sn in slot_numbers:
        counts = []
        for d in days:
            c = 0
            for r in all_intakes:
                if not r.scheduled_time or not r.slot_id:
                    continue
                # match slot number
                if r.slot and r.slot.slot_number != sn:
                    continue
                # match PH date and taken only (as per your chart title)
                if to_manila(r.scheduled_time).date() == d and r.taken:
                    c += 1
            counts.append(c)
        daily_slot_data.append(counts)

    # --- Slot distribution (taken + missed or taken only? choose one)
    # Here: total intakes per slot (taken + missed)
    slot_distribution = []
    for sn in slot_numbers:
        c = 0
        for r in all_intakes:
            if r.slot and r.slot.slot_number == sn:
                c += 1
        slot_distribution.append(c)

    return jsonify({
        "total": total,
        "taken": taken,
        "missed": missed,
        "compliance": compliance,

        "trend_labels": labels,
        "trend_data": trend,

        "days_labels": [d.strftime("%a") for d in days],  # Mon Tue...
        "slot_labels": [f"Slot {sn}" for sn in slot_numbers],
        "daily_slot_data": daily_slot_data,   # [slot][day]
        "slot_distribution": slot_distribution
    })

# ---------- History ----------
@app.route('/history')
@login_required_page
def history_page():
    return render_template('history.html', user_name=session.get('user_name', 'User'))

@app.route('/api/history', methods=['GET'])
def history_api():
    if not validate_history_date_range():
        return jsonify({"error": "Invalid date format. Use YYYY-MM-DD"}), 400

    data = get_history_filtered_rows()

    history = []
    for row in data:
        history.append({
            "medicine": row["Medicine"],
            "date": row["Date"],
            "scheduled_time": row["Scheduled Time"],
            "taken_at": row["Taken At"],
            "status": row["Status"]
        })

    return jsonify(history)

def get_history_filtered_rows():
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')

    parsed_start = None
    parsed_end = None

    if start_date:
        parsed_start = datetime.strptime(start_date, "%Y-%m-%d").date()
    if end_date:
        parsed_end = datetime.strptime(end_date, "%Y-%m-%d").date()

    records = Intake.query.order_by(Intake.scheduled_time.desc(), Intake.id.desc()).all()
    data = []

    for intake in records:
        medicine_name = "-"
        date_str = "-"
        scheduled_time_str = "-"
        taken_at_str = "Missed"
        local_date = None

        sched = intake.schedule

        if sched and sched.medicine:
            medicine_name = sched.medicine.name

        if intake.scheduled_time:
            local_sched = to_manila(intake.scheduled_time)
            local_date = local_sched.date()
            date_str = local_sched.strftime("%Y-%m-%d")
            scheduled_time_str = local_sched.strftime("%I:%M %p")

        elif sched and sched.date and sched.time:
            try:
                h, m = map(int, sched.time.split(":"))
                local_alarm = tz.localize(datetime(
                    sched.date.year,
                    sched.date.month,
                    sched.date.day,
                    h,
                    m,
                    0
                ))
                local_date = local_alarm.date()
                date_str = local_alarm.strftime("%Y-%m-%d")
                scheduled_time_str = local_alarm.strftime("%I:%M %p")
            except Exception:
                pass

        elif intake.notification and intake.notification.scheduled_datetime:
            local_occ = to_manila(intake.notification.scheduled_datetime)
            local_date = local_occ.date()
            date_str = local_occ.strftime("%Y-%m-%d")
            scheduled_time_str = local_occ.strftime("%I:%M %p")

        if parsed_start and local_date and local_date < parsed_start:
            continue
        if parsed_end and local_date and local_date > parsed_end:
            continue

        if intake.taken_at:
            local_taken = to_manila(intake.taken_at)
            taken_at_str = local_taken.strftime("%I:%M %p")

        data.append({
            "Medicine": medicine_name,
            "Date": date_str,
            "Scheduled Time": scheduled_time_str,
            "Taken At": taken_at_str,
            "Status": "Taken" if intake.taken else "Missed"
        })

    return data


def validate_history_date_range():
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')

    try:
        if start_date:
            datetime.strptime(start_date, "%Y-%m-%d")
        if end_date:
            datetime.strptime(end_date, "%Y-%m-%d")
    except ValueError:
        return False

    return True
# ---------- Export EXCEL / PDF ----------
def build_history_export_data():
    records = Intake.query.order_by(Intake.scheduled_time.desc(), Intake.id.desc()).all()
    data = []

    for intake in records:
        medicine_name = "-"
        date_str = "-"
        scheduled_time_str = "-"
        taken_at_str = "Missed"

        sched = intake.schedule

        # medicine
        if sched and sched.medicine:
            medicine_name = sched.medicine.name

        # scheduled date/time
        if intake.scheduled_time:
            local_sched = to_manila(intake.scheduled_time)
            date_str = local_sched.strftime("%Y-%m-%d")
            scheduled_time_str = local_sched.strftime("%I:%M %p")

        elif sched and sched.date and sched.time:
            try:
                h, m = map(int, sched.time.split(":"))
                local_alarm = tz.localize(datetime(
                    sched.date.year,
                    sched.date.month,
                    sched.date.day,
                    h,
                    m,
                    0
                ))
                date_str = local_alarm.strftime("%Y-%m-%d")
                scheduled_time_str = local_alarm.strftime("%I:%M %p")
            except Exception:
                pass

        elif intake.notification and intake.notification.scheduled_datetime:
            local_occ = to_manila(intake.notification.scheduled_datetime)
            date_str = local_occ.strftime("%Y-%m-%d")
            scheduled_time_str = local_occ.strftime("%I:%M %p")

        # taken at
        if intake.taken_at:
            local_taken = to_manila(intake.taken_at)
            taken_at_str = local_taken.strftime("%I:%M %p")

        data.append({
            "Medicine": medicine_name,
            "Date": date_str,
            "Scheduled Time": scheduled_time_str,
            "Taken At": taken_at_str,
            "Status": "Taken" if intake.taken else "Missed"
        })

    return data


@app.route('/export/history/excel')
@login_required_page
def export_excel():
    if not validate_history_date_range():
        return jsonify({"error": "Invalid date format. Use YYYY-MM-DD"}), 400

    data = get_history_filtered_rows()
    df = pd.DataFrame(data)

    output = BytesIO()
    printed_at = now_ph().strftime("%B %d, %Y %I:%M %p")
    prepared_by = session.get('user_name', 'User')
    start_date = request.args.get('start_date') or "All"
    end_date = request.args.get('end_date') or "All"

    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='History Log', startrow=5)

        ws = writer.sheets['History Log']

        ws["A1"] = "MEDISINA"
        ws["A2"] = "Medication History Report"
        ws["A3"] = f"Prepared by: {prepared_by}"
        ws["D3"] = f"Date and Time Printed: {printed_at}"
        ws["A4"] = f"Filter Range: {start_date} to {end_date}"

        for column_cells in ws.columns:
            max_length = 0
            column_letter = column_cells[0].column_letter

            for cell in column_cells:
                try:
                    cell_value = str(cell.value) if cell.value is not None else ""
                    if len(cell_value) > max_length:
                        max_length = len(cell_value)
                except:
                    pass

            ws.column_dimensions[column_letter].width = max_length + 3

    output.seek(0)
    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name='history_report.xlsx'
    )

@app.route('/export/history/pdf')
@login_required_page
def export_pdf():
    if not validate_history_date_range():
        return jsonify({"error": "Invalid date format. Use YYYY-MM-DD"}), 400

    data = get_history_filtered_rows()

    output = BytesIO()
    c = canvas.Canvas(output, pagesize=letter)
    width, height = letter

    printed_at = now_ph().strftime("%B %d, %Y %I:%M %p")
    prepared_by = session.get('user_name', 'User')
    start_date = request.args.get('start_date') or "All"
    end_date = request.args.get('end_date') or "All"

    def draw_header(page_no):
        c.setFont("Helvetica-Bold", 16)
        c.drawCentredString(width / 2, height - 50, "MEDISINA")

        c.setFont("Helvetica-Bold", 13)
        c.drawCentredString(width / 2, height - 72, "Medication History Report")

        c.setLineWidth(1)
        c.line(40, height - 82, width - 40, height - 82)

        c.setFont("Helvetica", 10)
        c.drawString(40, height - 100, f"Prepared by: {prepared_by}")
        c.drawRightString(width - 40, height - 100, f"Date and Time Printed: {printed_at}")
        c.drawString(40, height - 115, f"Filter Range: {start_date} to {end_date}")

        c.setFont("Helvetica-Bold", 10)
        y = height - 140
        c.drawString(40, y, "Medicine")
        c.drawString(160, y, "Date")
        c.drawString(245, y, "Scheduled Time")
        c.drawString(345, y, "Taken At")
        c.drawString(445, y, "Status")

        c.line(40, y - 5, width - 40, y - 5)
        return y - 22

    page_no = 1
    y = draw_header(page_no)

    c.setFont("Helvetica", 9)

    if not data:
        c.drawString(40, y, "No history found for the selected date range.")
    else:
        for row in data:
            medicine = str(row["Medicine"])[:22]
            date_val = str(row["Date"])
            sched_time = str(row["Scheduled Time"])
            taken_at = str(row["Taken At"])
            status = str(row["Status"])

            c.drawString(40, y, medicine)
            c.drawString(160, y, date_val)
            c.drawString(245, y, sched_time)
            c.drawString(345, y, taken_at)
            c.drawString(445, y, status)

            y -= 18

            if y < 60:
                c.setFont("Helvetica-Oblique", 9)
                c.drawRightString(width - 40, 30, f"Page {page_no}")
                c.showPage()

                page_no += 1
                y = draw_header(page_no)
                c.setFont("Helvetica", 9)

    c.setFont("Helvetica-Oblique", 9)
    c.drawRightString(width - 40, 30, f"Page {page_no}")

    c.save()
    output.seek(0)

    return send_file(
        output,
        mimetype='application/pdf',
        as_attachment=True,
        download_name='history_report.pdf'
    )

# ---------- Notifications ----------

# ---------- Notifications Scheduler ----------
scheduler = BackgroundScheduler(daemon=True)

def generate_notifications_for_now():
    with app.app_context():
        now_local = datetime.now(tz)
        today_local = now_local.date()

        # buffer: next 2 minutes
        window_start_local = now_local.replace(second=0, microsecond=0)
        window_end_local = window_start_local + timedelta(minutes=2)

        # ✅ only schedules for TODAY
        todays_schedules = (
            MedicineSchedule.query
            .filter(MedicineSchedule.is_active == True)
            .filter(MedicineSchedule.date == today_local)
            .all()
        )

        for sched in todays_schedules:
            if not sched.time:
                continue
            try:
                hour, minute = map(int, sched.time.split(":"))
            except:
                continue

            # ✅ use sched.date, not now_local.date
            scheduled_local = tz.localize(datetime(
                sched.date.year, sched.date.month, sched.date.day, hour, minute, 0
            ))

            if window_start_local <= scheduled_local < window_end_local:
                scheduled_utc = to_utc_naive(scheduled_local)

                exists = Notification.query.filter_by(
                    scheduled_id=sched.id,
                    status='pending',
                    scheduled_datetime=scheduled_utc
                ).first()

                if not exists:
                    db.session.add(Notification(
                        scheduled_id=sched.id,
                        message=f"Time to take {sched.medicine.name}",
                        status="pending",
                        scheduled_datetime=scheduled_utc
                    ))

        db.session.commit()

@app.route('/api/notifications/pending', methods=['GET'])
@login_required_page
def get_pending_notifications():
    now_utc = datetime.utcnow()

    notes = (
        Notification.query
        .filter(Notification.status == 'pending')
        .filter(Notification.scheduled_datetime.isnot(None))
        .filter(Notification.scheduled_datetime >= now_utc - timedelta(minutes=30))
        .order_by(Notification.scheduled_datetime.asc())
        .all()
    )

    return jsonify([{
        "id": n.id,
        "message": n.message,
        "scheduled_datetime": to_manila(n.scheduled_datetime).isoformat() if n.scheduled_datetime else None
    } for n in notes])

@app.route('/api/notifications/<int:note_id>/ack', methods=['POST'])
def ack_notification(note_id):
    data = request.json or {}
    action = data.get('action')

    note = Notification.query.get_or_404(note_id)

    if action == 'taken':
        note.status = 'acknowledged'
    elif action == 'missed':
        note.status = 'missed'
    elif action == 'snooze':
        note.status = 'snoozed'
        snooze_minutes = int(data.get('minutes', 5))
        note.scheduled_datetime += timedelta(minutes=snooze_minutes)
    else:
        return jsonify({"error": "Invalid action"}), 400

    db.session.commit()
    return jsonify({"message": "Updated", "action": action})


@app.route('/api/notifications/recent', methods=['GET'])
@login_required_page
def recent_notifications():
    notes = (
        Notification.query
        .filter(Notification.status.in_(["acknowledged", "missed", "snoozed"]))
        .order_by(Notification.scheduled_datetime.desc(), Notification.id.desc())
        .limit(20)
        .all()
    )

    payload = []
    for n in notes:
        dt = to_manila(n.scheduled_datetime) if n.scheduled_datetime else None

        if n.status == "acknowledged":
            status_label = "Taken"
        elif n.status == "missed":
            status_label = "Missed"
        else:
            status_label = "Snoozed"

        payload.append({
            "id": n.id,
            "message": n.message,
            "status": status_label,
            "scheduled_datetime": dt.isoformat() if dt else None
        })

    return jsonify(payload)

# ---------- Hardware APIs ----------
@app.route('/api/hardware/next_dose', methods=['GET'])
@require_api_key
def hardware_next_dose():
    now_local = now_ph()
    today = now_local.date()

    todays = (
        MedicineSchedule.query
        .filter(MedicineSchedule.is_active == True)
        .filter(MedicineSchedule.date == today)
        .filter(MedicineSchedule.time.isnot(None))
        .all()
    )

    best_dt = None

    for sched in todays:
        try:
            h, m = map(int, sched.time.split(":"))
        except Exception:
            continue

        sched_dt = now_local.replace(hour=h, minute=m, second=0, microsecond=0)

        if sched_dt <= now_local:
            continue

        if best_dt is None or sched_dt < best_dt:
            best_dt = sched_dt

    if not best_dt:
        return jsonify({"next_dose": "--:--"})

    return jsonify({"next_dose": best_dt.strftime("%H:%M")})

@app.route("/api/hardware/current_slot", methods=["GET"])
@require_api_key
def hardware_current_slot():
    dt = now_ph()
    return jsonify({
        "slot_number": current_slot_number_ph(dt),
        "period": current_period_ph(dt),
        "weekday": dt.weekday(),  # Monday=0
        "datetime": dt.isoformat()
    })

@app.route('/api/hardware/next_schedule', methods=['GET']) 
@require_api_key
def hardware_next_schedule():
    now_local = now_ph()
    today = now_local.date()

    todays = (
        MedicineSchedule.query
        .filter(MedicineSchedule.is_active == True)
        .filter(MedicineSchedule.date == today)
        .filter(MedicineSchedule.time.isnot(None))
        .join(Slot, MedicineSchedule.slot_id == Slot.id)
        .order_by(MedicineSchedule.time.asc(), Slot.slot_number.asc())
        .all()
    )

    if not todays:
        return jsonify({
            "scheduled_id": None,
            "slot_number": None,
            "time": "--:--",
            "date": today.isoformat(),
            "medicine_name": None,
            "medicine_id": None
        })

    # 1) FIRST: get earliest unresolved schedule for today
    for sched in todays:
        try:
            h, m = map(int, sched.time.split(":"))
            sched_local = tz.localize(datetime(
                sched.date.year,
                sched.date.month,
                sched.date.day,
                h,
                m,
                0
            ))
            sched_utc = to_utc_naive(sched_local)
        except Exception:
            continue

        # check if already logged in intake
        existing_intake = (
            Intake.query
            .filter(Intake.scheduled_id == sched.id)
            .filter(Intake.scheduled_time == sched_utc)
            .first()
        )

        # if not yet logged, this is still the current schedule to serve
        if not existing_intake:
            return jsonify({
                "scheduled_id": sched.id,
                "slot_number": sched.slot.slot_number if sched.slot else None,
                "time": sched.time,
                "date": sched.date.isoformat() if sched.date else today.isoformat(),
                "medicine_name": sched.medicine.name if sched.medicine else None,
                "medicine_id": sched.medicine_id if sched else None
            })

    # 2) fallback: all today's schedules already logged
    return jsonify({
        "scheduled_id": None,
        "slot_number": None,
        "time": "--:--",
        "date": today.isoformat(),
        "medicine_name": None,
        "medicine_id": None
    })

# ---------- Hardware Pill Status POST (Direct Intake Save) ----------
@app.route('/api/hardware/pill_status', methods=['POST'])
@require_api_key
def hardware_pill_status():
    data = request.json or {}

    # incoming
    slot_number = data.get("slot_number")
    status = (data.get("status") or "").strip()          # "Taken" or "Missed"
    scheduled_id = data.get("scheduled_id")              # optional (template schedule id)
    notification_id = data.get("notification_id")        # ✅ NEW (best)

    if not slot_number or status not in ("Taken", "Missed"):
        return jsonify({"error": "Missing/invalid slot_number or status"}), 400

    # --- PH time helpers ---
    now_local = datetime.now(tz)   # Asia/Manila aware
    today_local = now_local.date()
    now_utc = datetime.utcnow()    # store UTC naive

    # --- Enforce Slot 1..14 weekly mapping (Mon AM=1, Mon PM=2 ... Sun PM=14) ---
    # ✅ NEW: slot lookup uses the slot_number sent by device
    slot = Slot.query.filter_by(slot_number=int(slot_number)).first()
    if not slot:
        return jsonify({"error": f"Slot not found for slot_number={slot_number}"}), 400
    

    chosen_note = None
    chosen_schedule = None
    # ✅ NEW: Ensure schedule is for TODAY (PH date-based schedules)
    if chosen_schedule and chosen_schedule.date and chosen_schedule.time:
        try:
            h, m = map(int, chosen_schedule.time.split(":"))
            sched_local = tz.localize(datetime(
                chosen_schedule.date.year,
                chosen_schedule.date.month,
                chosen_schedule.date.day,
                h,
                m,
                0
            ))

            diff_minutes = (now_local - sched_local).total_seconds() / 60.0

            # allow a small early allowance only
            if diff_minutes < -1:
                return jsonify({
                    "error": f"Too early for this schedule. Matched {chosen_schedule.medicine.name} at {chosen_schedule.time}, but current time is {now_local.strftime('%H:%M')}."
                }), 400

        except Exception:
            pass
    # =========================================================
    # STEP 1: Best path — use notification_id (exact occurrence)
    # =========================================================
    if notification_id:
        try:
            chosen_note = Notification.query.get(int(notification_id))
        except Exception:
            chosen_note = None

        if chosen_note and chosen_note.status != "pending":
            chosen_note = None

        if chosen_note:
            chosen_schedule = MedicineSchedule.query.get(chosen_note.scheduled_id)

            # Safety: ensure schedule belongs to the same slot + active
            if (not chosen_schedule or
                chosen_schedule.slot_id != slot.id or
                not getattr(chosen_schedule, "is_active", True)):
                chosen_note = None
                chosen_schedule = None

    # =========================================================
    # STEP 2: If hardware sends scheduled_id, use it (template)
    # =========================================================
    if not chosen_schedule and scheduled_id:
        try:
            chosen_schedule = MedicineSchedule.query.get(int(scheduled_id))
        except Exception:
            chosen_schedule = None

        if chosen_schedule and (chosen_schedule.slot_id != slot.id or not getattr(chosen_schedule, "is_active", True)):
            chosen_schedule = None

    # =========================================================
    # STEP 3: Fallback — choose closest active schedule by time
    # =========================================================
    TOLERANCE_MINUTES = 5

    if not chosen_schedule:
        active_schedules = (
            MedicineSchedule.query
            .filter_by(slot_id=slot.id, is_active=True)
            .filter(MedicineSchedule.date == today_local)
            .order_by(MedicineSchedule.time.asc())
            .all()
        )

        for sch in active_schedules:
            if not sch.time:
                continue

            try:
                h, m = map(int, sch.time.split(":"))
                sched_local = tz.localize(datetime(
                    sch.date.year,
                    sch.date.month,
                    sch.date.day,
                    h,
                    m,
                    0
                ))
            except Exception:
                continue

            diff_minutes = (now_local - sched_local).total_seconds() / 60.0

            # allow only due or slightly late schedules
            if -1 <= diff_minutes <= TOLERANCE_MINUTES:
                chosen_schedule = sch
                break

    # =========================================================
    # STEP 4: If we have a schedule but no note, link to pending
    # =========================================================
    if chosen_schedule and not chosen_note:
        # pick the closest pending notification for this schedule (today-ish)

        start_local = tz.localize(datetime(today_local.year, today_local.month, today_local.day, 0, 0, 0))
        end_local   = start_local + timedelta(days=1)

        start_utc = to_utc_naive(start_local)
        end_utc   = to_utc_naive(end_local)

        chosen_note = (
            Notification.query
            .filter(Notification.scheduled_id == chosen_schedule.id)
            .filter(Notification.status == "pending")
            .filter(Notification.scheduled_datetime >= start_utc)
            .filter(Notification.scheduled_datetime < end_utc)
            .order_by(Notification.scheduled_datetime.asc())
            .first()
        )

    # =========================================================
    # STEP 5: If still none, do NOT lose the event (unscheduled)
    # =========================================================
    if not chosen_schedule:
        return jsonify({
            "error": "No matching active schedule found for this slot/time."
        }), 400

    # =========================================================
    # STEP 6: UPSERT intake (one row per notification occurrence)
    # =========================================================
    if chosen_note and chosen_note.scheduled_datetime:
        scheduled_time_utc = chosen_note.scheduled_datetime
    else:
        try:
            h, m = map(int, chosen_schedule.time.split(":"))
            local_alarm = tz.localize(datetime(
                chosen_schedule.date.year,
                chosen_schedule.date.month,
                chosen_schedule.date.day,
                h,
                m,
                0
            ))
            scheduled_time_utc = to_utc_naive(local_alarm)
        except Exception:
            scheduled_time_utc = now_utc
    taken_at_utc = now_utc if status == "Taken" else None

    existing = None
    if chosen_note:
        existing = Intake.query.filter_by(notification_id=chosen_note.id).first()

    if existing:
        existing.slot_id = slot.id
        existing.scheduled_id = chosen_schedule.id
        existing.notification_id = chosen_note.id if chosen_note else None
        existing.scheduled_time = scheduled_time_utc
        existing.taken = (status == "Taken")
        existing.taken_at = taken_at_utc
    else:
        db.session.add(Intake(
            slot_id=slot.id,
            scheduled_id=chosen_schedule.id,
            notification_id=chosen_note.id if chosen_note else None,  # ✅ NEW
            scheduled_time=scheduled_time_utc,
            taken=(status == "Taken"),
            taken_at=taken_at_utc
        ))

    # =========================================================
    # STEP 7: Update notification status/message (if any)
    # =========================================================
    med_name = chosen_schedule.medicine.name if chosen_schedule and chosen_schedule.medicine else "medication"

    if chosen_note:
        if status == "Taken":
            chosen_note.status = "acknowledged"
            chosen_note.message = f"The {med_name} has already been taken"
        else:
            chosen_note.status = "missed"
            chosen_note.message = "Scheduled medication missed!"

    if chosen_schedule:
        chosen_schedule.is_active = False
        chosen_schedule.status = "Done" if status == "Taken" else "Missed"

    db.session.commit()

    # =========================================================
    # STEP 8: Send email AFTER commit (do not block success)
    # =========================================================
    try:
        recipients = get_recipient_emails(EMAIL_ALERT_TARGET)
        local_time_str = now_local.strftime("%Y-%m-%d %I:%M %p")
        slot_label = f"Slot {slot.slot_number}" if slot else "-"

        if status == "Taken":
            subject = "Medication Taken Confirmation"
            body = f"""
            <html>
            <body>
                <h3>Medication Update</h3>
                <p>The patient has <b>TAKEN</b> the medicine: <b>{med_name}</b>.</p>
                <p><b>Slot:</b> {slot_label}</p>
                <p><b>Status:</b> Taken</p>
                <p><b>Time:</b> {local_time_str}</p>
            </body>
            </html>
            """
        else:
            subject = "Medication Missed Alert"
            body = f"""
            <html>
            <body>
                <h3>Medication Update</h3>
                <p>The patient has <b>MISSED</b> the medicine: <b>{med_name}</b>.</p>
                <p><b>Slot:</b> {slot_label}</p>
                <p><b>Status:</b> Missed</p>
                <p><b>Time:</b> {local_time_str}</p>
            </body>
            </html>
            """

        send_status_email(recipients, subject, body)

    except Exception as e:
        print("EMAIL ERROR:", e)
        
    # ✅ ALWAYS return success
    return jsonify({
        "message": "Intake saved successfully",
        "slot_number": slot.slot_number,
        "scheduled_id": chosen_schedule.id if chosen_schedule else None,
        "notification_id": chosen_note.id if chosen_note else None,
        "medicine": med_name,
        "taken": status,
        "ph_datetime": now_local.isoformat()
    }), 200


EMAIL_ALERT_TARGET = "all"   # "admin", "users", or "all"

def get_recipient_emails(target="all"):
    """
    Returns deduplicated recipient emails based on role target:
    - admin  => all admin emails
    - users  => all patient/user emails
    - all    => both admins and users
    """
    target = (target or "all").lower().strip()

    q = User.query.filter(User.email.isnot(None))
    users = q.all()

    emails = []
    for u in users:
        email = (u.email or "").strip().lower()
        role = (getattr(u, "role", "user") or "user").strip().lower()

        if not email:
            continue

        if target == "admin" and role == "admin":
            emails.append(email)
        elif target == "users" and role == "user":
            emails.append(email)
        elif target == "all":
            emails.append(email)

    # remove duplicates while preserving order
    deduped = list(dict.fromkeys(emails))
    return deduped


import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ✅ USE ENV VARIABLES (IMPORTANT)
GMAIL_SENDER = os.getenv("GMAIL_SENDER")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")


def send_status_email(to_emails, subject: str, html_body: str):
    """
    Sends the same email to one or many recipients.
    Sends individually so recipient addresses stay private.
    """

    try:
        # ✅ Normalize recipients
        if isinstance(to_emails, str):
            to_emails = [to_emails]

        to_emails = [e.strip().lower() for e in (to_emails or []) if e and e.strip()]
        to_emails = list(dict.fromkeys(to_emails))  # remove duplicates

        print("EMAIL DEBUG => recipients:", to_emails)
        print("EMAIL DEBUG => sender:", GMAIL_SENDER)
        print("EMAIL DEBUG => password exists:", bool(GMAIL_APP_PASSWORD))

        # ❌ No recipients
        if not to_emails:
            raise Exception("Recipient email list is empty")

        # ❌ Missing credentials
        if not GMAIL_SENDER or not GMAIL_APP_PASSWORD:
            raise Exception("Gmail credentials not set in environment variables")

        # ✅ Remove accidental spaces in password
        app_password = GMAIL_APP_PASSWORD.replace(" ", "").strip()

        # ✅ Connect to Gmail SMTP
        server = smtplib.SMTP("smtp.gmail.com", 587, timeout=30)
        server.set_debuglevel(1)  # 🔥 shows SMTP logs

        server.ehlo()
        server.starttls()
        server.ehlo()

        # ✅ Login
        server.login(GMAIL_SENDER, app_password)
        print("EMAIL DEBUG => LOGIN SUCCESS")

        # ✅ Send emails one by one
        for to_email in to_emails:
            try:
                msg = MIMEMultipart("alternative")
                msg["From"] = GMAIL_SENDER
                msg["To"] = to_email
                msg["Subject"] = subject

                msg.attach(MIMEText(html_body, "html"))

                server.sendmail(GMAIL_SENDER, to_email, msg.as_string())
                print("EMAIL DEBUG => SENT OK TO:", to_email)

            except Exception as send_err:
                print(f"EMAIL ERROR sending to {to_email}:", send_err)

        server.quit()
        print("EMAIL DEBUG => ALL DONE")

    except Exception as e:
        print("EMAIL ERROR (MAIN):", str(e))
        raise

@app.route("/api/test_email", methods=["GET"])
def test_email():
    target = request.args.get("target", EMAIL_ALERT_TARGET)  # admin / users / all
    recipients = get_recipient_emails(target)

    try:
        send_status_email(
            recipients,
            "Test Email - Smart Dispenser",
            """
            <html>
            <body>
                <h3>Smart Dispenser Test Email</h3>
                <p>This is a test email from your Smart Dispenser system.</p>
            </body>
            </html>
            """
        )
        return jsonify({
            "ok": True,
            "target": target,
            "sent_to": recipients
        })
    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e),
            "target": target,
            "sent_to": recipients
        }), 500

@app.route('/api/notifications/test_create', methods=['GET'])
@login_required_page
def create_test_notification():
    now_local = now_ph()
    now_utc = datetime.utcnow()

    note = Notification(
        scheduled_id=None,
        message="Test notification from dashboard",
        status="pending",
        scheduled_datetime=now_utc
    )
    db.session.add(note)
    db.session.commit()

    return jsonify({"message": "Test notification created", "id": note.id})

@app.route('/api/hardware/pending_notifications', methods=['GET'])
@require_api_key
def hardware_pending_notifications():
    now_local = datetime.now(tz)

    # window: last 5 minutes to next 2 minutes
    start_local = now_local - timedelta(minutes=5)
    end_local = now_local + timedelta(minutes=2)

    start_utc = to_utc_naive(start_local)
    end_utc = to_utc_naive(end_local)

    notes = (
        Notification.query
        .filter(Notification.status == 'pending')
        .filter(Notification.scheduled_datetime >= start_utc)
        .filter(Notification.scheduled_datetime <= end_utc)
        .order_by(Notification.scheduled_datetime.asc())
        .all()
    )

    return jsonify([
        {
            "notification_id": n.id,
            "scheduled_id": n.scheduled_id,
            "slot_number": n.schedule.slot.slot_number if n.schedule and n.schedule.slot else None,
            "medicine_name": n.schedule.medicine.name if n.schedule and n.schedule.medicine else None,
            "medicine_id": n.schedule.medicine_id if n.schedule else None,
            "scheduled_time_local": to_manila(n.scheduled_datetime).isoformat() if n.scheduled_datetime else None
        }
        for n in notes
    ])

    
@app.route("/api/hardware/predict_risk", methods=["POST"])
@require_api_key
def predict_risk():
    try:
        if MODEL is None:
            return jsonify({"error": "ML model not loaded"}), 500

        data = request.get_json(force=True) or {}

        slot_number = data.get("slot_number")
        medicine_id = data.get("medicine_id")
        hour = data.get("hour")
        minute = data.get("minute")
        dow = data.get("dow")

        if slot_number is None:
            return jsonify({"error": "slot_number required"}), 400

        if medicine_id is None:
            return jsonify({"error": "medicine_id required"}), 400

        now_utc = datetime.utcnow()
        cutoff = now_utc - timedelta(days=7)

        q = (
            Intake.query
            .join(Slot, Intake.slot_id == Slot.id)
            .filter(Slot.slot_number == int(slot_number))
            .filter(Intake.scheduled_time.isnot(None))
            .filter(Intake.scheduled_time >= cutoff)
        )

        history_count_last_7 = q.count()
        missed_last_7 = q.filter(Intake.taken == False).count()
        taken_last_7 = q.filter(Intake.taken == True).count()

        adherence_rate_last_7 = (
            taken_last_7 / history_count_last_7
            if history_count_last_7 > 0 else 0.0
        )

        row = {
            "slot_number": int(slot_number),
            "sched_hour": int(hour) if hour is not None else 0,
            "sched_minute": int(minute) if minute is not None else 0,
            "sched_dow": int(dow) if dow is not None else 0,
            "medicine_id": int(medicine_id),
            "taken_last_7": int(taken_last_7),
            "missed_last_7": int(missed_last_7),
            "adherence_rate_last_7": float(adherence_rate_last_7),
        }

        X = pd.DataFrame([row], columns=FEATURES)

        classes = list(MODEL.classes_)
        miss_index = classes.index(1)
        prob = float(MODEL.predict_proba(X)[0][miss_index])

        return jsonify({
            "risk": round(prob, 4),
            "window_minutes": WINDOW_MINUTES,
            "features_used": row
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/hardware/predict_risk_mock", methods=["GET"])
@require_api_key
def predict_risk_mock():
    return jsonify({"risk": 0.95})

def seed_slots_4day():
    """
    Ensures Slot table has 14 slots with:
    Day 1-3: 4 slots each
    Day 4: 2 slots
    slot_number 1..14
    """
    existing = Slot.query.count()
    if existing >= 14:
        # If slots exist, we update day_number/slot_in_day for slot_number 1..14
        for sn in range(1, 15):
            s = Slot.query.filter_by(slot_number=sn).first()
            if not s:
                continue

            if 1 <= sn <= 4:
                s.day_number = 1
                s.slot_in_day = sn
            elif 5 <= sn <= 8:
                s.day_number = 2
                s.slot_in_day = sn - 4
            elif 9 <= sn <= 12:
                s.day_number = 3
                s.slot_in_day = sn - 8
            else:  # 13-14
                s.day_number = 4
                s.slot_in_day = sn - 12  # 1..2

        db.session.commit()
        return

    # If table empty or <14, create missing ones
    for sn in range(1, 15):
        s = Slot.query.filter_by(slot_number=sn).first()
        if not s:
            s = Slot(slot_number=sn, day_number=1, slot_in_day=1)
            db.session.add(s)
            db.session.flush()

        if 1 <= sn <= 4:
            s.day_number = 1
            s.slot_in_day = sn
        elif 5 <= sn <= 8:
            s.day_number = 2
            s.slot_in_day = sn - 4
        elif 9 <= sn <= 12:
            s.day_number = 3
            s.slot_in_day = sn - 8
        else:
            s.day_number = 4
            s.slot_in_day = sn - 12

    db.session.commit()

# ---------- Initialize Database ----------
with app.app_context():
    try:
        db.create_all()
        seed_slots_4day()
        print("DB INIT SUCCESS")
    except Exception as e:
        print("DB INIT ERROR:", e)

# ---------- Scheduler ----------
if not scheduler.running:
    scheduler.add_job(
        func=generate_notifications_for_now,
        trigger="interval",
        seconds=30,
        id="generate_notifications_job",
        replace_existing=True
    )
    scheduler.start()

# ---------- Run App (for local only) ----------
if __name__ == '__main__':
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))


