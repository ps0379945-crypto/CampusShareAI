import os
import sqlite3
import hashlib
import secrets
import hmac
from datetime import datetime, date

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "campusshare.db")


# ------------------------------------------------------------
# CONNECTION AND TRANSACTION HELPERS
# ------------------------------------------------------------

def get_connection():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def _columns(conn, table):
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def _ensure_column(conn, table, column, definition):
    if column not in _columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def create_tables():
    """Create missing tables and add only missing columns; never erase existing data."""
    conn = get_connection()
    try:
        conn.execute("PRAGMA journal_mode = WAL")

        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'Student',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS resources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                category TEXT NOT NULL,
                location TEXT NOT NULL,
                availability TEXT NOT NULL DEFAULT 'Available',
                description TEXT,
                eligibility TEXT,
                owner_id INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                resource_id INTEGER,
                resource_name TEXT NOT NULL,
                status TEXT DEFAULT 'Pending',
                user_id INTEGER,
                booking_date TEXT,
                start_time TEXT,
                end_time TEXT,
                eligibility_checked INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS activity_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action TEXT NOT NULL,
                resource TEXT NOT NULL,
                details TEXT,
                time TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                user_id INTEGER
            );

            CREATE TABLE IF NOT EXISTS waitlist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id INTEGER,
                user_id INTEGER,
                resource_id INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS handover_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id INTEGER,
                stage TEXT NOT NULL,
                "condition" TEXT NOT NULL,
                notes TEXT,
                recorded_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS incident_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id INTEGER,
                reported_by INTEGER,
                incident_type TEXT NOT NULL,
                description TEXT NOT NULL,
                status TEXT DEFAULT 'Open',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                message TEXT NOT NULL,
                is_read INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # Additive migrations for older versions only.
        migrations = {
            "users": [
                ("created_at", "TEXT"),
            ],
            "resources": [
                ("category", "TEXT"),
                ("location", "TEXT"),
                ("availability", "TEXT DEFAULT 'Available'"),
                ("description", "TEXT"),
                ("eligibility", "TEXT"),
                ("owner_id", "INTEGER"),
                ("created_at", "TEXT"),
            ],
            "requests": [
                ("resource_id", "INTEGER"),
                ("resource_name", "TEXT"),
                ("status", "TEXT DEFAULT 'Pending'"),
                ("user_id", "INTEGER"),
                ("booking_date", "TEXT"),
                ("start_time", "TEXT"),
                ("end_time", "TEXT"),
                ("eligibility_checked", "INTEGER DEFAULT 0"),
                ("created_at", "TEXT"),
            ],
            "activity_logs": [
                ("action", "TEXT"),
                ("resource", "TEXT"),
                ("details", "TEXT"),
                ("time", "TEXT"),
                ("user_id", "INTEGER"),
            ],
            "waitlist": [
                ("request_id", "INTEGER"),
                ("user_id", "INTEGER"),
                ("resource_id", "INTEGER"),
                ("created_at", "TEXT"),
            ],
            "handover_records": [
                ("request_id", "INTEGER"),
                ("stage", "TEXT"),
                ("condition", "TEXT"),
                ("notes", "TEXT"),
                ("recorded_at", "TEXT"),
            ],
            "incident_reports": [
                ("request_id", "INTEGER"),
                ("reported_by", "INTEGER"),
                ("incident_type", "TEXT"),
                ("description", "TEXT"),
                ("status", "TEXT DEFAULT 'Open'"),
                ("created_at", "TEXT"),
            ],
            "notifications": [
                ("user_id", "INTEGER"),
                ("message", "TEXT"),
                ("is_read", "INTEGER DEFAULT 0"),
                ("created_at", "TEXT"),
            ],
        }

        for table, definitions in migrations.items():
            for column, definition in definitions:
                _ensure_column(conn, table, column, definition)

        conn.commit()
    finally:
        conn.close()


# ------------------------------------------------------------
# PASSWORDS AND USERS
# Existing database stores SHA-256 password_hash and a 32-char salt.
# Verification supports the common original salt/password orderings.
# New registrations use SHA-256(password + salt).
# ------------------------------------------------------------

def _make_password_hash(password, salt):
    return hashlib.sha256((password + salt).encode("utf-8")).hexdigest()


def _verify_stored_password(password, stored_hash, salt):
    # Try common SHA-256 salted formats used by earlier versions.
    candidates = [
        hashlib.sha256((password + salt).encode("utf-8")).hexdigest(),
        hashlib.sha256((salt + password).encode("utf-8")).hexdigest(),
    ]

    # Also support common PBKDF2-SHA256 formats. The existing database
    # stores a 32-character salt and a 64-character hexadecimal hash.
    salt_values = [salt.encode("utf-8")]
    try:
        decoded_salt = bytes.fromhex(salt)
        if decoded_salt not in salt_values:
            salt_values.append(decoded_salt)
    except ValueError:
        pass

    for salt_value in salt_values:
        for iterations in (100000, 200000, 260000):
            candidates.append(
                hashlib.pbkdf2_hmac(
                    "sha256",
                    password.encode("utf-8"),
                    salt_value,
                    iterations
                ).hex()
            )

    return any(hmac.compare_digest(stored_hash, candidate) for candidate in candidates)


def register_user(name, email, password):
    conn = get_connection()
    try:
        salt = secrets.token_hex(16)
        password_hash = _make_password_hash(password, salt)

        conn.execute("""
            INSERT INTO users (name, email, password_hash, salt, role)
            VALUES (?, ?, ?, ?, 'Student')
        """, (name.strip(), email.strip().lower(), password_hash, salt))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        conn.rollback()
        return False
    except sqlite3.Error:
        conn.rollback()
        return False
    finally:
        conn.close()


def authenticate_user(email, password):
    conn = get_connection()
    try:
        user = conn.execute(
            "SELECT * FROM users WHERE lower(email) = lower(?)",
            (email.strip(),)
        ).fetchone()

        if user is None:
            return None

        # This is the schema used by the user's existing database.
        stored_hash = user["password_hash"]
        salt = user["salt"]

        if _verify_stored_password(password, stored_hash, salt):
            return dict(user)

        return None
    except (sqlite3.Error, KeyError, TypeError):
        return None
    finally:
        conn.close()


def get_users():
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT id, name, email, role, created_at
            FROM users
            ORDER BY id DESC
        """).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def update_user_role(user_id, role):
    if role not in ("Student", "Resource Owner"):
        return False, "Invalid role."

    conn = get_connection()
    try:
        user = conn.execute(
            "SELECT role FROM users WHERE id = ?", (user_id,)
        ).fetchone()

        if user is None:
            return False, "User not found."
        if user["role"] == "Admin":
            return False, "Admin role cannot be changed."

        conn.execute(
            "UPDATE users SET role = ? WHERE id = ?",
            (role, user_id)
        )
        conn.commit()
        return True, "User role updated."
    except sqlite3.Error as error:
        conn.rollback()
        return False, str(error)
    finally:
        conn.close()


# ------------------------------------------------------------
# RESOURCES
# app.py call:
# add_resource(name, category, location, availability,
#              description, eligibility, owner_id=...)
# ------------------------------------------------------------

def add_resource(name, category, location, availability="Available",
                 description="", eligibility="All Students", owner_id=None):
    conn = get_connection()
    try:
        cursor = conn.execute("""
            INSERT INTO resources
                (name, category, location, availability,
                 description, eligibility, owner_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            name.strip(), category, location.strip(), availability,
            description, eligibility, owner_id
        ))
        resource_id = cursor.lastrowid
        conn.commit()
        return resource_id
    except sqlite3.Error:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_resources(owner_id=None):
    conn = get_connection()
    try:
        if owner_id is None:
            rows = conn.execute("""
                SELECT r.*, u.name AS owner_name
                FROM resources r
                LEFT JOIN users u ON u.id = r.owner_id
                ORDER BY r.id DESC
            """).fetchall()
        else:
            rows = conn.execute("""
                SELECT r.*, u.name AS owner_name
                FROM resources r
                LEFT JOIN users u ON u.id = r.owner_id
                WHERE r.owner_id = ?
                ORDER BY r.id DESC
            """, (owner_id,)).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_resource(resource_id):
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM resources WHERE id = ?", (resource_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_resource_availability(resource_id, availability):
    if availability not in ("Available", "Unavailable", "Maintenance"):
        return False, "Invalid availability."

    conn = get_connection()
    try:
        cursor = conn.execute(
            "UPDATE resources SET availability = ? WHERE id = ?",
            (availability, resource_id)
        )
        conn.commit()
        if cursor.rowcount == 0:
            return False, "Resource not found."
        return True, "Resource availability updated."
    except sqlite3.Error as error:
        conn.rollback()
        return False, str(error)
    finally:
        conn.close()

def update_resource(resource_id, name, category, location,
                    availability, description, eligibility,
                    owner_id=None):

    conn = get_connection()

    try:
        cursor = conn.execute("""
            UPDATE resources
            SET name = ?,
                category = ?,
                location = ?,
                availability = ?,
                description = ?,
                eligibility = ?,
                owner_id = ?
            WHERE id = ?
        """, (
            name, category, location, availability,
            description, eligibility, owner_id, resource_id
        ))

        conn.commit()

        if cursor.rowcount == 0:
            return False, "Resource not found."

        return True, "Resource updated successfully."

    except sqlite3.Error as error:
        conn.rollback()
        return False, str(error)

    finally:
        conn.close()

def delete_resource(resource_id):
    conn = get_connection()
    try:
        has_requests = conn.execute(
            "SELECT 1 FROM requests WHERE resource_id = ? LIMIT 1",
            (resource_id,)
        ).fetchone()
        if has_requests:
            return False, "Resource has booking history and cannot be deleted."

        conn.execute("DELETE FROM resources WHERE id = ?", (resource_id,))
        conn.commit()
        return True, "Resource deleted."
    except sqlite3.Error as error:
        conn.rollback()
        return False, str(error)
    finally:
        conn.close()


# ------------------------------------------------------------
# ELIGIBILITY
# app.py unpacks: eligible, eligibility_message = check_eligibility(...)
# ------------------------------------------------------------

def _check_eligibility_with_connection(conn, user_id, resource_id):
    """Check a user's role against a resource's saved eligibility rule."""
    user = conn.execute(
        "SELECT role FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    resource = conn.execute(
        "SELECT eligibility FROM resources WHERE id = ?", (resource_id,)
    ).fetchone()

    if user is None:
        return False, "User account not found."
    if resource is None:
        return False, "Resource not found."

    role = str(user["role"] or "").strip().lower()
    rule = str(resource["eligibility"] or "All Students").strip().lower()
    role = role.replace("_", " ").replace("-", " ")
    rule = rule.replace("_", " ").replace("-", " ")

    if rule in ("all students", "all", "everyone", "students", "student"):
        allowed = role in ("student", "students")
    elif rule in ("resource owners", "resource owner", "owners"):
        allowed = role in ("resource owner", "resource owners")
    elif rule in ("faculty", "faculty only"):
        allowed = role == "faculty"
    elif "faculty" in rule and "manual verification" in rule:
        return False, (
            "This resource requires faculty verification. "
            "Please contact the resource owner or administrator."
        )
    elif "robotics" in rule and "student" in rule:
        # This prototype stores user roles but not student departments.
        # It can confirm that the account is a student, but cannot verify
        # whether the student belongs to the Robotics department.
        allowed = role in ("student", "students")
    else:
        return False, (
            "This resource has a specific eligibility rule that the system "
            "cannot verify yet. Please contact the resource owner."
        )

    if allowed:
        return True, "You are eligible to request this resource."
    return False, "This resource is not available for your account role."


def check_eligibility(user_id, resource_id):
    """Public app.py function: check_eligibility(user_id, resource_id)."""
    conn = get_connection()
    try:
        return _check_eligibility_with_connection(conn, user_id, resource_id)
    except sqlite3.Error as error:
        return False, f"Eligibility check failed: {error}"
    finally:
        conn.close()



# ------------------------------------------------------------
# NOTIFICATIONS
# Pass conn=conn when already inside a write transaction.
# This prevents the previous nested-connection lock.
# ------------------------------------------------------------

def add_notification(user_id, message, conn=None):
    if not user_id:
        return

    if conn is not None:
        conn.execute("""
            INSERT INTO notifications (user_id, message)
            VALUES (?, ?)
        """, (user_id, message))
        return

    own_conn = get_connection()
    try:
        own_conn.execute("""
            INSERT INTO notifications (user_id, message)
            VALUES (?, ?)
        """, (user_id, message))
        own_conn.commit()
    finally:
        own_conn.close()


def get_notifications(user_id):
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT * FROM notifications
            WHERE user_id = ?
            ORDER BY id DESC
        """, (user_id,)).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def mark_notification_read(notification_id, user_id=None):
    """Mark a notification read; scope to its owner when user_id is supplied."""
    conn = get_connection()
    try:
        if user_id is None:
            cursor = conn.execute(
                "UPDATE notifications SET is_read = 1 WHERE id = ?",
                (notification_id,)
            )
        else:
            cursor = conn.execute(
                "UPDATE notifications SET is_read = 1 WHERE id = ? AND user_id = ?",
                (notification_id, user_id)
            )
        conn.commit()
        return cursor.rowcount > 0
    except sqlite3.Error:
        conn.rollback()
        return False
    finally:
        conn.close()



# ------------------------------------------------------------
# BOOKING REQUESTS
# ------------------------------------------------------------

def add_request(resource_id, resource_name, user_id, booking_date,
                start_time, end_time, eligibility_checked=0):
    conn = get_connection()
    try:
        selected_date = date.fromisoformat(str(booking_date))
        datetime.strptime(str(start_time), "%H:%M")
        datetime.strptime(str(end_time), "%H:%M")

        if selected_date < date.today():
            return False, "You cannot book a past date."
        if start_time >= end_time:
            return False, "End time must be later than start time."

        resource = conn.execute(
            "SELECT * FROM resources WHERE id = ?", (resource_id,)
        ).fetchone()

        if resource is None:
            return False, "Resource not found."
        if resource["availability"] != "Available":
            return False, "This resource is currently unavailable."

        eligible, eligibility_message = _check_eligibility_with_connection(
            conn, user_id, resource_id
        )
        if not eligible:
            return False, eligibility_message

        duplicate = conn.execute("""
            SELECT id FROM requests
            WHERE user_id = ? AND resource_id = ? AND booking_date = ?
              AND status IN ('Pending', 'Approved', 'Checked In', 'Waitlisted')
            LIMIT 1
        """, (user_id, resource_id, str(booking_date))).fetchone()

        if duplicate:
            return False, "You already have an active request for this resource on this date."

        conn.execute("""
            INSERT INTO requests
                (resource_id, resource_name, user_id, booking_date,
                 start_time, end_time, status, eligibility_checked)
            VALUES (?, ?, ?, ?, ?, ?, 'Pending', ?)
        """, (
            resource_id, resource_name, user_id, str(booking_date),
            str(start_time), str(end_time), int(bool(eligibility_checked))
        ))

        # Same connection as the booking insert: no nested SQLite writer.
        if resource["owner_id"]:
            add_notification(
                resource["owner_id"],
                f"New booking request for {resource_name}.",
                conn=conn
            )

        conn.commit()
        return True, "Booking request submitted successfully."

    except ValueError:
        conn.rollback()
        return False, "Please enter a valid booking date and time."
    except sqlite3.Error as error:
        conn.rollback()
        return False, f"Booking failed: {error}"
    finally:
        conn.close()


def get_requests(user_id=None, owner_id=None):
    conn = get_connection()
    try:
        query = """
            SELECT q.*,
                   u.name AS student_name,
                   u.email AS student_email,
                   r.owner_id AS resource_owner_id,
                   r.location AS resource_location
            FROM requests q
            LEFT JOIN users u ON u.id = q.user_id
            LEFT JOIN resources r ON r.id = q.resource_id
        """
        conditions = []
        values = []

        if user_id is not None:
            conditions.append("q.user_id = ?")
            values.append(user_id)
        if owner_id is not None:
            conditions.append("r.owner_id = ?")
            values.append(owner_id)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY q.id DESC"
        rows = conn.execute(query, values).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def update_request_status(request_id, status):
    allowed = {
        "Pending", "Approved", "Rejected", "Waitlisted",
        "Checked In", "Completed", "Cancelled"
    }
    if status not in allowed:
        return False, "Invalid request status."

    conn = get_connection()
    try:
        request = conn.execute(
            "SELECT * FROM requests WHERE id = ?", (request_id,)
        ).fetchone()

        if request is None:
            return False, "Booking request not found."

        if status == "Approved":
            conflict = conn.execute("""
                SELECT id FROM requests
                WHERE resource_id = ?
                  AND booking_date = ?
                  AND id != ?
                  AND status IN ('Approved', 'Checked In')
                  AND start_time < ?
                  AND end_time > ?
                LIMIT 1
            """, (
                request["resource_id"], request["booking_date"], request_id,
                request["end_time"], request["start_time"]
            )).fetchone()

            if conflict:
                return False, "This resource is already booked during that time."

        conn.execute(
            "UPDATE requests SET status = ? WHERE id = ?",
            (status, request_id)
        )

        if status == "Waitlisted":
            conn.execute("""
                INSERT INTO waitlist (request_id, user_id, resource_id)
                VALUES (?, ?, ?)
            """, (
                request_id, request["user_id"], request["resource_id"]
            ))
        else:
            conn.execute(
                "DELETE FROM waitlist WHERE request_id = ?",
                (request_id,)
            )

        add_notification(
            request["user_id"],
            f"Your request for {request['resource_name']} is now {status}.",
            conn=conn
        )

        conn.commit()
        return True, f"Request {status.lower()} successfully."

    except sqlite3.IntegrityError as error:
        conn.rollback()
        return False, str(error)
    except sqlite3.Error as error:
        conn.rollback()
        return False, f"Could not update request: {error}"
    finally:
        conn.close()


# ------------------------------------------------------------
# WAITLIST
# ------------------------------------------------------------

def get_waitlist():
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT w.*,
                   u.name AS student_name,
                   u.email AS student_email,
                   r.name AS resource_name,
                   q.booking_date, q.start_time, q.end_time,
                   q.status AS request_status
            FROM waitlist w
            LEFT JOIN users u ON u.id = w.user_id
            LEFT JOIN resources r ON r.id = w.resource_id
            LEFT JOIN requests q ON q.id = w.request_id
            ORDER BY w.id ASC
        """).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def remove_from_waitlist(request_id):
    conn = get_connection()
    try:
        conn.execute(
            "DELETE FROM waitlist WHERE request_id = ?", (request_id,)
        )
        conn.commit()
        return True
    except sqlite3.Error:
        conn.rollback()
        return False
    finally:
        conn.close()


# ------------------------------------------------------------
# HANDOVER / CHECK-IN / CHECK-OUT
# app.py call: add_handover_record(request_id, stage, condition, notes)
# ------------------------------------------------------------

def add_handover_record(request_id, stage, condition, notes=""):
    if stage not in ("Check In", "Check Out"):
        return False, "Invalid handover stage."

    conn = get_connection()
    try:
        request = conn.execute(
            "SELECT * FROM requests WHERE id = ?", (request_id,)
        ).fetchone()

        if request is None:
            return False, "Booking request not found."

        if stage == "Check In":
            if request["status"] != "Approved":
                return False, "Only approved bookings can be checked in."
            new_status = "Checked In"
        else:
            if request["status"] != "Checked In":
                return False, "Check in before checking out."
            new_status = "Completed"

        conn.execute("""
            INSERT INTO handover_records (request_id, stage, "condition", notes)
            VALUES (?, ?, ?, ?)
        """, (request_id, stage, condition, notes))

        conn.execute(
            "UPDATE requests SET status = ? WHERE id = ?",
            (new_status, request_id)
        )

        conn.commit()
        return True, f"{stage} recorded successfully."
    except sqlite3.Error as error:
        conn.rollback()
        return False, str(error)
    finally:
        conn.close()


def get_handover_records():
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT h.*, q.resource_name, u.name AS student_name
            FROM handover_records h
            LEFT JOIN requests q ON q.id = h.request_id
            LEFT JOIN users u ON u.id = q.user_id
            ORDER BY h.id DESC
        """).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


# ------------------------------------------------------------
# INCIDENT REPORTS
# app.py call: add_incident_report(request_id, user_id, type, description)
# ------------------------------------------------------------

def add_incident_report(request_id, user_id, incident_type, description):
    conn = get_connection()
    try:
        request = conn.execute("""
            SELECT id FROM requests
            WHERE id = ? AND user_id = ?
        """, (request_id, user_id)).fetchone()

        if request is None:
            return False, "Booking request not found for this user."
        if not str(description).strip():
            return False, "Please enter an incident description."

        conn.execute("""
            INSERT INTO incident_reports
                (request_id, reported_by, incident_type, description, status)
            VALUES (?, ?, ?, ?, 'Open')
        """, (
            request_id, user_id, incident_type, description.strip()
        ))
        conn.commit()
        return True, "Incident report submitted successfully."
    except sqlite3.Error as error:
        conn.rollback()
        return False, str(error)
    finally:
        conn.close()


def get_incident_reports():
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT i.*,
                   u.name AS student_name,
                   u.email AS student_email,
                   q.resource_name
            FROM incident_reports i
            LEFT JOIN users u ON u.id = i.reported_by
            LEFT JOIN requests q ON q.id = i.request_id
            ORDER BY i.id DESC
        """).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def update_incident_status(incident_id, status):
    allowed = ("Open", "Under Review", "Resolved", "Closed", "Dismissed")
    if status not in allowed:
        return False, "Invalid incident status."

    conn = get_connection()
    try:

        cursor = conn.execute(
            "UPDATE incident_reports SET status = ? WHERE id = ?",
            (status, incident_id)
        )
        conn.commit()
        if cursor.rowcount == 0:
            return False, "Incident not found."
        return True, "Incident status updated."
    except sqlite3.Error as error:
        conn.rollback()
        return False, str(error)
    finally:
        conn.close()


# ------------------------------------------------------------
# ACTIVITY LOGS
# Supports either:
# add_activity_log(user_id, action, resource, details)
# or add_activity_log(user_id, action, details)
# ------------------------------------------------------------

def add_activity_log(*args):
    if len(args) == 4:
        user_id, action, resource, details = args
    elif len(args) == 3:
        user_id, action, details = args
        resource = ""
    else:
        raise TypeError("add_activity_log expects 3 or 4 arguments.")

    conn = get_connection()
    try:
        conn.execute("""
            INSERT INTO activity_logs (user_id, action, resource, details, time)
            VALUES (?, ?, ?, ?, ?)
        """, (
            user_id, action, resource, details,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ))
        conn.commit()
        return True
    except sqlite3.Error:
        conn.rollback()
        return False
    finally:
        conn.close()


def get_activity_logs():
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT a.*, u.name AS user_name
            FROM activity_logs a
            LEFT JOIN users u ON u.id = a.user_id
            ORDER BY a.id DESC
        """).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()
