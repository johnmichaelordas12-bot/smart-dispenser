from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

# -----------------------------
# USER MODEL
# -----------------------------
# models.py
class User(db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)

    # ✅ NEW: role-based access
    role = db.Column(db.String(20), nullable=False, default="user")

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
# -----------------------------
# SLOT MODEL (FIXED + FUTURE)
# -----------------------------
class Slot(db.Model):
    __tablename__ = 'slots'

    id = db.Column(db.Integer, primary_key=True)
    slot_number = db.Column(db.Integer, nullable=False)   # 1–14 fixed, more later
    day_number = db.Column(db.Integer, nullable=False)   # 1..4
    slot_in_day = db.Column(db.Integer, nullable=False)  # 1..4 (or 1..2 on day4)

    schedules = db.relationship(
        'MedicineSchedule',
        back_populates='slot',
        cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<Slot {self.slot_number} day={self.day_number} slot_in_day={self.slot_in_day}>"

# -----------------------------
# MEDICINE MODEL
# -----------------------------
class Medicine(db.Model):
    __tablename__ = 'medicines'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)

# -----------------------------
# MEDICINE SCHEDULE (MULTIPLE PER SLOT)
# -----------------------------
class MedicineSchedule(db.Model):
    __tablename__ = 'medicine_schedule'

    id = db.Column(db.Integer, primary_key=True)
    medicine_id = db.Column(db.Integer, db.ForeignKey('medicines.id'), nullable=False)
    slot_id = db.Column(db.Integer, db.ForeignKey('slots.id'), nullable=False)
    time = db.Column(db.String(10))  # HH:MM

    is_active = db.Column(db.Boolean, default=True)  # ✅ ADD THIS

    date = db.Column(db.Date, nullable=True)
    status = db.Column(db.String(20), default="Active")   # optional

    medicine = db.relationship('Medicine')
    slot = db.relationship('Slot', back_populates='schedules')

# -----------------------------
# INTAKE MODEL
# -----------------------------
class Intake(db.Model):
    __tablename__ = 'intake'

    id = db.Column(db.Integer, primary_key=True)

    slot_id = db.Column(db.Integer, db.ForeignKey('slots.id'))
    scheduled_id = db.Column(db.Integer, db.ForeignKey('medicine_schedule.id'))

    # ✅ NEW: link each intake to the actual notification occurrence
    notification_id = db.Column(db.Integer, db.ForeignKey('notifications.id'), nullable=True)

    scheduled_time = db.Column(db.DateTime)
    taken = db.Column(db.Boolean, default=False)
    taken_at = db.Column(db.DateTime)

    slot = db.relationship('Slot')
    schedule = db.relationship('MedicineSchedule', foreign_keys=[scheduled_id])

    # ✅ NEW relationship
    notification = db.relationship('Notification', foreign_keys=[notification_id])

# -----------------------------
# NOTIFICATION MODEL
# -----------------------------
class Notification(db.Model):
    __tablename__ = 'notifications'

    id = db.Column(db.Integer, primary_key=True)

    scheduled_id = db.Column(db.Integer, db.ForeignKey('medicine_schedule.id'))  # MATCH DB

    message = db.Column(db.String(255))
    status = db.Column(db.String(50), default='pending')
    scheduled_datetime = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    schedule = db.relationship('MedicineSchedule', foreign_keys=[scheduled_id])
