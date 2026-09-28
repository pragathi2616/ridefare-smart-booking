from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
import joblib
import sqlite3
import re as _re
import requests
import re
import os
import secrets
import json
from datetime import datetime, timedelta, timezone
from statistics import mean

try:
    from google import genai
except ImportError:
    genai = None

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")
app.config["ADMIN_USERNAME"] = os.environ.get("ADMIN_USERNAME", "admin")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("COOKIE_SECURE", "0") == "1"

# DATABASE_DIR lets you point the SQLite file at a persistent disk in
# production (e.g. a Render disk mount) instead of the app folder,
# which is wiped on every deploy on most free hosting tiers.
DATA_DIR = os.environ.get("DATABASE_DIR", os.path.dirname(__file__))
os.makedirs(DATA_DIR, exist_ok=True)

DB_PATH = os.path.join(DATA_DIR, "users.db")
MODEL_PATH = os.path.join(os.path.dirname(__file__), "model.pkl")

# Optional Gemini AI support. If no API key is configured, the built-in
# RideFare support assistant remains available.
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.7-flash")
gemini_client = None
if GEMINI_API_KEY and genai is not None:
    try:
        gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception as e:
        print("Gemini initialization error:", e)

# =========================
# LOAD MODEL
# =========================
saved_data = joblib.load(MODEL_PATH)

model = saved_data["model"]
le = saved_data["label_encoder"]
accuracy = saved_data["accuracy"]
model_metrics = saved_data.get("metrics", {
    "Random Forest": {"r2": accuracy, "mae": None, "rmse": None}
})


# =========================
# DATABASE
# =========================
# The app runs locally with SQLite and switches automatically to PostgreSQL
# when DATABASE_URL is provided by the hosting platform (for example Render).
# This keeps the student project easy to run locally while making the same
# codebase suitable for a shared, persistent online database.
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

try:
    import psycopg2
    from psycopg2 import IntegrityError as PostgresIntegrityError
except ImportError:
    psycopg2 = None
    PostgresIntegrityError = Exception

USE_POSTGRES = DATABASE_URL.startswith(("postgresql://", "postgres://"))
if USE_POSTGRES and psycopg2 is None:
    raise RuntimeError("DATABASE_URL is set, but psycopg2-binary is not installed.")

DATA_DIR = os.environ.get("DATABASE_DIR", os.path.dirname(__file__))
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "users.db")
MODEL_PATH = os.path.join(os.path.dirname(__file__), "model.pkl")


def _pg_sql(sql):
    """Convert the project's SQLite-style ? placeholders to PostgreSQL %s."""
    return sql.replace("?", "%s")


class DBConnection:
    def __init__(self, raw, postgres=False):
        self.raw = raw
        self.postgres = postgres

    def cursor(self):
        return DBCursor(self.raw.cursor(), self.postgres)

    def commit(self):
        self.raw.commit()

    def rollback(self):
        self.raw.rollback()

    def close(self):
        self.raw.close()


class DBCursor:
    def __init__(self, raw, postgres=False):
        self.raw = raw
        self.postgres = postgres
        self.lastrowid = None

    def execute(self, sql, params=None):
        sql_to_run = _pg_sql(sql) if self.postgres else sql
        if self.postgres and re.match(r"\s*INSERT\s+INTO\s+([\w]+)", sql, re.I) and "RETURNING" not in sql.upper():
            table = re.match(r"\s*INSERT\s+INTO\s+([\w]+)", sql, re.I).group(1)
            sql_to_run = sql_to_run.rstrip().rstrip(';') + " RETURNING id"
            self.raw.execute(sql_to_run, params or ())
            row = self.raw.fetchone()
            self.lastrowid = row[0] if row else None
            return self
        self.raw.execute(sql_to_run, params or ())
        return self

    def fetchone(self):
        return self.raw.fetchone()

    def fetchall(self):
        return self.raw.fetchall()

    def close(self):
        self.raw.close()


def get_db():
    if USE_POSTGRES:
        raw = psycopg2.connect(DATABASE_URL, connect_timeout=10)
        return DBConnection(raw, postgres=True)
    return DBConnection(sqlite3.connect(DB_PATH), postgres=False)


def _table_columns(c, table):
    if USE_POSTGRES:
        c.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=?", (table,))
        return [row[0] for row in c.fetchall()]
    return [row[1] for row in c.execute(f"PRAGMA table_info({table})")]


def init_db():
    conn = get_db()
    c = conn.cursor()

    if USE_POSTGRES:
        schema = [
            """CREATE TABLE IF NOT EXISTS users(
                id BIGSERIAL PRIMARY KEY,
                username TEXT UNIQUE,
                email TEXT,
                phone TEXT,
                password TEXT,
                reset_token TEXT,
                reset_expiry TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS rides(
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                name TEXT,
                phone TEXT,
                vehicle TEXT,
                passengers INTEGER,
                pickup_name TEXT,
                drop_name TEXT,
                distance_km DOUBLE PRECISION,
                fare DOUBLE PRECISION,
                created_at TEXT,
                status TEXT DEFAULT 'Awaiting Payment',
                driver_name TEXT,
                driver_phone TEXT,
                estimated_time INTEGER,
                fare_breakdown TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS payments(
                id BIGSERIAL PRIMARY KEY,
                ride_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                amount DOUBLE PRECISION,
                method TEXT,
                card_last4 TEXT,
                status TEXT,
                transaction_id TEXT,
                created_at TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS complaints(
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                ride_id BIGINT,
                category TEXT NOT NULL,
                message TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Open',
                created_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS feedback(
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                ride_id BIGINT,
                rating INTEGER NOT NULL,
                comment TEXT,
                created_at TEXT NOT NULL
            )""",
        ]
        for statement in schema:
            c.execute(statement)
        # Safe upgrades for databases created by an earlier project version.
        for table, col, typ, default in [
            ("users", "phone", "TEXT", "NULL"),
            ("users", "reset_token", "TEXT", "NULL"),
            ("users", "reset_expiry", "TEXT", "NULL"),
            ("rides", "status", "TEXT", "'Awaiting Payment'"),
            ("rides", "driver_name", "TEXT", "NULL"),
            ("rides", "driver_phone", "TEXT", "NULL"),
            ("rides", "estimated_time", "INTEGER", "NULL"),
            ("rides", "fare_breakdown", "TEXT", "NULL"),
        ]:
            if col not in _table_columns(c, table):
                c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ} DEFAULT {default}")
    else:
        c.execute("""CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, email TEXT,
            phone TEXT, password TEXT, reset_token TEXT, reset_expiry TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS rides(
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, name TEXT,
            phone TEXT, vehicle TEXT, passengers INTEGER, pickup_name TEXT, drop_name TEXT,
            distance_km REAL, fare REAL, created_at TEXT, status TEXT DEFAULT 'Awaiting Payment',
            driver_name TEXT, driver_phone TEXT, estimated_time INTEGER, fare_breakdown TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS payments(
            id INTEGER PRIMARY KEY AUTOINCREMENT, ride_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
            amount REAL, method TEXT, card_last4 TEXT, status TEXT, transaction_id TEXT, created_at TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS complaints(
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, ride_id INTEGER,
            category TEXT NOT NULL, message TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'Open', created_at TEXT NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS feedback(
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, ride_id INTEGER,
            rating INTEGER NOT NULL, comment TEXT, created_at TEXT NOT NULL)""")

    admin_username = os.environ.get("ADMIN_USERNAME", "admin")
    admin_password = os.environ.get("ADMIN_PASSWORD", "Admin@123")
    c.execute("SELECT id FROM users WHERE username = ?", (admin_username,))
    if not c.fetchone():
        c.execute(
            "INSERT INTO users(username, email, phone, password) VALUES (?, ?, ?, ?)",
            (admin_username, "admin@ridefare.local", "", generate_password_hash(admin_password))
        )

    conn.commit()
    conn.close()


init_db()


# =========================
# DISTANCE FUNCTION
# =========================
def get_distance(pickup, drop):
    try:
        url = (
            f"https://router.project-osrm.org/route/v1/driving/"
            f"{pickup};{drop}?overview=false"
        )

        response = requests.get(url, timeout=10)
        response.raise_for_status()

        result = response.json()

        if result.get("routes"):
            distance_km = result["routes"][0]["distance"] / 1000
            return distance_km

        return 0

    except Exception as e:
        print("Distance error:", e)
        return 0


# =========================
# AUTHENTICATION
# =========================
@app.route("/", methods=["GET", "POST"])
def auth():

    if request.method == "POST":

        action = request.form.get("action")
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip()
        phone = request.form.get("phone", "").strip()
        password = request.form.get("password", "")

        # =========================
        # BASIC VALIDATION
        # =========================
        if not username or not password:
            flash("Please enter username and password.")
            return redirect(url_for("auth"))

        conn = get_db()
        c = conn.cursor()

        # =========================
        # REGISTER
        # =========================
        if action == "register":

            if not email:
                conn.close()
                flash("Please enter your email.")
                return redirect(url_for("auth"))

            try:

                hashed_password = generate_password_hash(password)

                c.execute(
                    """
                    INSERT INTO users(username, email, phone, password)
                    VALUES (?, ?, ?, ?)
                    """,
                    (username, email, phone, hashed_password)
                )

                conn.commit()

                new_user_id = c.lastrowid

                # --------------------------------
                # AUTOMATIC LOGIN AFTER REGISTER
                # --------------------------------
                session["user"] = username
                session["user_id"] = new_user_id

                conn.close()

                # --------------------------------
                # GO DIRECTLY TO HOME
                # --------------------------------
                return redirect(url_for("home"))

            except (sqlite3.IntegrityError, PostgresIntegrityError):

                conn.close()

                flash(
                    "Username already exists. "
                    "Please choose a different username."
                )

                return redirect(url_for("auth"))

            except Exception as e:

                conn.close()

                print("Registration error:", e)

                flash("Registration failed. Please try again.")

                return redirect(url_for("auth"))

        # =========================
        # LOGIN
        # =========================
        elif action == "login":

            c.execute(
                """
                SELECT id, username, email, password
                FROM users
                WHERE username = ?
                """,
                (username,)
            )

            user = c.fetchone()

            conn.close()

            if user and check_password_hash(user[3], password):

                # Store logged-in username + id
                session["user"] = user[1]
                session["user_id"] = user[0]

                # Go to Home
                return redirect(url_for("home"))

            else:

                flash("Invalid username or password.")

                return redirect(url_for("auth"))

        # =========================
        # INVALID ACTION
        # =========================
        else:

            conn.close()

            flash("Invalid request.")

            return redirect(url_for("auth"))

    return render_template("auth.html")


# =========================
# MAJOR PROJECT HELPERS
# =========================
def is_admin():
    return session.get("user") == os.environ.get("ADMIN_USERNAME", "admin")


def require_admin():
    if "user" not in session:
        return redirect(url_for("auth"))
    if not is_admin():
        flash("Administrator access is required.")
        return redirect(url_for("home"))
    return None


def simulate_driver_assignment(ride_id):
    """Assign a demo driver after successful payment; no real dispatch occurs."""
    drivers = [
        ("Arun Kumar", "98XXXXXX21", 5),
        ("Rahul S", "97XXXXXX64", 7),
        ("Kiran M", "99XXXXXX18", 4),
        ("Vijay P", "96XXXXXX52", 6),
    ]
    driver_name, driver_phone, eta = drivers[(ride_id - 1) % len(drivers)]
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "UPDATE rides SET status='Driver Assigned', driver_name=?, driver_phone=?, estimated_time=? WHERE id=?",
        (driver_name, driver_phone, eta, ride_id)
    )
    conn.commit()
    conn.close()
    return driver_name, driver_phone, eta


# =========================
# HOME
# =========================
@app.route("/home")
def home():

    if "user" not in session:
        return redirect(url_for("auth"))

    return render_template(
        "home.html",
        user=session["user"]
    )


# =========================
# BOOK RIDE PAGE
# =========================
@app.route("/book")
def book():

    if "user" not in session:
        return redirect(url_for("auth"))

    return render_template("book.html")


# =========================
# PREDICT FARE
# =========================
@app.route("/predict", methods=["POST"])
def predict():

    if "user" not in session:
        return redirect(url_for("auth"))

    # =========================
    # GET FORM DATA
    # =========================
    name = request.form.get("name", "").strip()
    phone = request.form.get("phone", "").strip()
    vehicle = request.form.get("vehicle")
    passengers = request.form.get("passengers")
    ac_type = request.form.get("ac_type", "Non-AC")

    pickup = request.form.get("pickup")
    drop = request.form.get("drop")

    pickup_name = request.form.get("pickup_name")
    drop_name = request.form.get("drop_name")


    # =========================
    # PHONE VALIDATION
    # =========================
    if not phone or not re.fullmatch(r"[6-9]\d{9}", phone):

        flash("Invalid phone number. Enter a valid 10-digit number.")

        return redirect(url_for("book"))


    # =========================
    # PASSENGER VALIDATION
    # =========================
    if not passengers:

        flash("Please enter passenger count.")

        return redirect(url_for("book"))

    try:
        passengers = int(passengers)

    except ValueError:

        flash("Invalid passenger count.")

        return redirect(url_for("book"))


    if passengers < 1:

        flash("Passenger count must be at least 1.")

        return redirect(url_for("book"))


    # =========================
    # VEHICLE VALIDATION
    # =========================
    valid_vehicles = [
        "Auto",
        "Bike",
        "Scooty",
        "Uber Go",
        "Go Non AC",
        "Premier",
        "Uber XL"
    ]

    if vehicle not in valid_vehicles:

        flash("Invalid vehicle selected.")

        return redirect(url_for("book"))


    # =========================
    # VEHICLE CAPACITY
    # =========================
    vehicle_capacity = {

        "Bike": 1,

        "Scooty": 1,

        "Auto": 3,

        "Uber Go": 4,

        "Go Non AC": 4,

        "Premier": 4,

        "Uber XL": 6
    }


    maximum_passengers = vehicle_capacity.get(vehicle, 4)

    if passengers > maximum_passengers:

        flash(
            f"Only {maximum_passengers} passengers "
            f"are allowed in {vehicle}."
        )

        return redirect(url_for("book"))


    # =========================
    # CHECK PICKUP/DROP
    # =========================
    if not pickup or not drop:

        flash("Please select both pickup and drop locations.")

        return redirect(url_for("book"))


    # =========================
    # CALCULATE DISTANCE
    # =========================
    distance = get_distance(pickup, drop)

    if distance <= 0:

        flash(
            "Unable to calculate the route. "
            "Please select valid pickup and drop locations."
        )

        return redirect(url_for("book"))


    # =========================
    # ENCODE VEHICLE
    # =========================
    try:

        vehicle_encoded = le.transform([vehicle])[0]

    except Exception:

        flash(
            f"{vehicle} is not supported by the trained ML model."
        )

        return redirect(url_for("book"))


    # =========================
    # ML PREDICTION
    # =========================
    try:

        prediction = model.predict(
            [[distance, vehicle_encoded]]
        )[0]

    except Exception as e:

        print("Model prediction error:", e)

        flash("Unable to predict fare.")

        return redirect(url_for("book"))


    # =========================
    # TRANSPARENT FARE BREAKDOWN
    # =========================
    ml_fare = max(float(prediction), 0.0)
    base_fare = 40.0
    distance_component = ml_fare * 0.35
    model_component = ml_fare * 0.65

    fare_multiplier = {
        "Auto": 0.8, "Bike": 0.5, "Scooty": 0.4,
        "Uber Go": 1.0, "Go Non AC": 0.9, "Premier": 1.3, "Uber XL": 1.6
    }
    vehicle_factor = fare_multiplier.get(vehicle, 1.0)
    before_passenger = (base_fare + distance_component + model_component) * vehicle_factor
    passenger_surcharge = before_passenger * max(passengers - 1, 0) * 0.05
    ac_surcharge = before_passenger * 0.08 if ac_type == "AC" and vehicle not in ["Bike", "Scooty", "Auto"] else 0.0
    final_fare = round(max(before_passenger + passenger_surcharge + ac_surcharge, 50.0), 2)
    fare_breakdown = json.dumps({
        "Base fare": round(base_fare, 2),
        "Distance/model component": round(distance_component + model_component, 2),
        "Vehicle factor": round(vehicle_factor, 2),
        "Passenger surcharge": round(passenger_surcharge, 2),
        "AC surcharge": round(ac_surcharge, 2),
        "Final fare": final_fare,
    })
    estimated_time = max(5, round(distance * 3.2))


    # =========================
    # SAVE TO RIDE HISTORY
    # =========================
    new_ride_id = None

    try:
        conn = get_db()
        c = conn.cursor()

        c.execute(
            """
            INSERT INTO rides(
                user_id, name, phone, vehicle, passengers,
                pickup_name, drop_name, distance_km, fare, created_at,
                status, estimated_time, fare_breakdown
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session.get("user_id"),
                name,
                phone,
                vehicle,
                passengers,
                pickup_name,
                drop_name,
                round(distance, 2),
                final_fare,
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "Awaiting Payment",
                estimated_time,
                fare_breakdown
            )
        )

        conn.commit()
        new_ride_id = c.lastrowid
        conn.close()

    except Exception as e:
        print("Ride history save error:", e)


    # =========================
    # RESULT PAGE
    # =========================
    return render_template(
        "result.html",

        name=name,

        phone=phone,

        vehicle=vehicle,

        prediction=final_fare,

        distance=round(distance, 2),

        accuracy=round(accuracy * 100, 2),

        pickup_name=pickup_name,

        drop_name=drop_name,

        passengers=passengers,
        ac_type=ac_type,
        fare_breakdown=json.loads(fare_breakdown),
        estimated_time=estimated_time,
        ride_id=new_ride_id
    )


# =========================
# RIDE HISTORY
# =========================
@app.route("/history")
def history():

    if "user" not in session:
        return redirect(url_for("auth"))

    conn = get_db()
    c = conn.cursor()

    c.execute(
        """
        SELECT r.id, r.vehicle, r.passengers, r.pickup_name, r.drop_name,
               r.distance_km, r.fare, r.created_at,
               (
                   SELECT p.status FROM payments p
                   WHERE p.ride_id = r.id
                   ORDER BY p.id DESC LIMIT 1
               ) AS payment_status,
               (
                   SELECT p.id FROM payments p
                   WHERE p.ride_id = r.id
                   ORDER BY p.id DESC LIMIT 1
               ) AS payment_id
        FROM rides r
        WHERE r.user_id = ?
        ORDER BY r.created_at DESC
        """,
        (session.get("user_id"),)
    )

    rides = c.fetchall()
    conn.close()

    return render_template("history.html", rides=rides)


# =========================
# PROFILE
# =========================
@app.route("/profile", methods=["GET", "POST"])
def profile():

    if "user" not in session:
        return redirect(url_for("auth"))

    user_id = session.get("user_id")

    conn = get_db()
    c = conn.cursor()

    if request.method == "POST":

        form_type = request.form.get("form_type")

        # --------------------------------
        # UPDATE EMAIL / PHONE
        # --------------------------------
        if form_type == "details":

            email = request.form.get("email", "").strip()
            phone = request.form.get("phone", "").strip()

            if not email:
                conn.close()
                flash("Email cannot be empty.")
                return redirect(url_for("profile"))

            c.execute(
                "UPDATE users SET email = ?, phone = ? WHERE id = ?",
                (email, phone, user_id)
            )
            conn.commit()
            conn.close()

            flash("Profile updated successfully.")
            return redirect(url_for("profile"))

        # --------------------------------
        # CHANGE PASSWORD
        # --------------------------------
        elif form_type == "password":

            current_password = request.form.get("current_password", "")
            new_password = request.form.get("new_password", "")
            confirm_password = request.form.get("confirm_password", "")

            c.execute("SELECT password FROM users WHERE id = ?", (user_id,))
            row = c.fetchone()

            if not row or not check_password_hash(row[0], current_password):
                conn.close()
                flash("Current password is incorrect.")
                return redirect(url_for("profile"))

            if not new_password or new_password != confirm_password:
                conn.close()
                flash("New passwords do not match.")
                return redirect(url_for("profile"))

            c.execute(
                "UPDATE users SET password = ? WHERE id = ?",
                (generate_password_hash(new_password), user_id)
            )
            conn.commit()
            conn.close()

            flash("Password changed successfully.")
            return redirect(url_for("profile"))

        else:
            conn.close()
            flash("Invalid request.")
            return redirect(url_for("profile"))

    # GET
    c.execute(
        "SELECT username, email, phone FROM users WHERE id = ?",
        (user_id,)
    )
    user_row = c.fetchone()

    c.execute("SELECT COUNT(*) FROM rides WHERE user_id = ?", (user_id,))
    ride_count = c.fetchone()[0]

    conn.close()

    return render_template(
        "profile.html",
        username=user_row[0],
        email=user_row[1],
        phone=user_row[2],
        ride_count=ride_count
    )


# =========================
# FORGOT PASSWORD
# =========================
@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():

    if request.method == "POST":

        identifier = request.form.get("identifier", "").strip()

        if not identifier:
            flash("Enter your username or email.")
            return redirect(url_for("forgot_password"))

        conn = get_db()
        c = conn.cursor()

        c.execute(
            "SELECT id FROM users WHERE username = ? OR email = ?",
            (identifier, identifier)
        )
        user_row = c.fetchone()

        reset_link = None

        if user_row:
            token = secrets.token_urlsafe(32)
            expiry = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()

            c.execute(
                "UPDATE users SET reset_token = ?, reset_expiry = ? WHERE id = ?",
                (token, expiry, user_row[0])
            )
            conn.commit()

            reset_link = url_for("reset_password", token=token, _external=True)

            # --------------------------------------------------
            # In a real app this link would be emailed to the
            # user, not shown on screen. There's no mail server
            # configured here, so we surface it directly (and
            # log it) purely so the flow works end-to-end.
            # --------------------------------------------------
            print(f"[password reset] link for '{identifier}': {reset_link}")

        conn.close()

        # Always show the same generic message, whether or not the
        # account exists, so this can't be used to check which
        # usernames/emails are registered.
        flash("If an account exists, a reset link has been generated below.")

        return render_template("forgot_password.html", reset_link=reset_link)

    return render_template("forgot_password.html", reset_link=None)


# =========================
# RESET PASSWORD
# =========================
@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):

    conn = get_db()
    c = conn.cursor()

    c.execute(
        "SELECT id, reset_expiry FROM users WHERE reset_token = ?",
        (token,)
    )
    user_row = c.fetchone()

    valid = False

    if user_row:
        try:
            expiry = datetime.fromisoformat(user_row[1])
            valid = datetime.now(timezone.utc) < expiry
        except (TypeError, ValueError):
            valid = False

    if not valid:
        conn.close()
        flash("This reset link is invalid or has expired.")
        return redirect(url_for("forgot_password"))

    if request.method == "POST":

        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not new_password or new_password != confirm_password:
            conn.close()
            flash("Passwords do not match.")
            return render_template("reset_password.html", token=token)

        c.execute(
            """
            UPDATE users
            SET password = ?, reset_token = NULL, reset_expiry = NULL
            WHERE id = ?
            """,
            (generate_password_hash(new_password), user_row[0])
        )
        conn.commit()
        conn.close()

        flash("Password reset successfully. You can now log in.")
        return redirect(url_for("auth"))

    conn.close()
    return render_template("reset_password.html", token=token)


# =========================
# HEALTH CHECK (RENDER)
# =========================
@app.route("/health")
def health():
    # Deployment health check: verify both the web process and database are reachable.
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT 1")
        c.fetchone()
        conn.close()
        db_status = "postgresql" if USE_POSTGRES else "sqlite"
        return jsonify({"status": "ok", "service": "RideFare", "database": db_status}), 200
    except Exception as exc:
        return jsonify({"status": "degraded", "service": "RideFare", "error": str(exc)}), 503


# =========================
# AI SUPPORT CHAT
# =========================
def support_reply(message):
    """Lightweight built-in support assistant.

    This works without an external API key, so the Render demo remains
    deployable for free. It answers common RideFare questions using the
    application's actual features.
    """
    text = (message or "").strip().lower()

    if not text:
        return "Hi! I'm RideFare Support. Ask me about booking, fare prediction, the map, payments, profile, login, or password reset."

    # Greetings
    if any(word in text for word in ["hello", "hi", "hey", "hai"]):
        return "Hi! 👋 I'm RideFare Support. How can I help you today?"

    # Booking
    if any(word in text for word in ["book", "booking", "ride"]):
        return "To book a ride, open **Book a ride**, select your pickup and drop locations on the map, choose a vehicle and passenger count, then click **Predict Fare**."

    # Fare
    if any(word in text for word in ["fare", "price", "cost", "amount", "prediction"]):
        return "Your fare is predicted by the Random Forest model using the calculated road distance and vehicle type. Passenger count and vehicle pricing rules are then applied."

    # Map / distance
    if any(word in text for word in ["map", "distance", "pickup", "drop", "location", "route"]):
        return "The booking page uses Leaflet with OpenStreetMap. Select pickup and drop locations and the app uses OSRM to calculate the driving distance in kilometres."

    # Vehicles
    if any(word in text for word in ["vehicle", "bike", "scooty", "auto", "uber", "premier", "xl"]):
        return "Available vehicles are Auto, Bike, Scooty, Uber Go, Go Non AC, Premier, and Uber XL. Passenger limits are checked automatically before fare prediction."

    # Login / registration
    if any(word in text for word in ["login", "log in", "sign in", "register", "signup", "account"]):
        return "Use the Login/Register page. After successful registration or login, you'll be taken to the Home page. If login fails, check your username and password."

    # Forgot password
    if any(word in text for word in ["forgot", "password", "reset"]):
        return "Click **Forgot your password?** on the login page. Enter your username or email, open the generated reset link, and set a new password. The demo shows the link on screen instead of sending email."

    # Profile
    if "profile" in text or "email" in text or "phone" in text:
        return "Open **Profile** from the navigation bar to update your email or phone, or use the Change Password section to set a new password."

    # History
    if any(word in text for word in ["history", "previous", "past ride", "old ride"]):
        return "Open **History** to view your previous fare predictions, distances, vehicles, and payment status."

    # Payment
    if any(word in text for word in ["payment", "pay", "upi", "card", "receipt"]):
        return "After a ride prediction, you can use the payment page from History. The current project provides a demo/sandbox payment flow and a payment receipt."

    # Render/deployment
    if any(word in text for word in ["render", "deploy", "deployment", "live", "hosting"]):
        return "This project is Render-ready. Push the project to GitHub, create a Render Web Service, use `pip install -r requirements.txt` as the build command and `gunicorn app:app --bind 0.0.0.0:$PORT` as the start command."

    # Technical support
    if any(word in text for word in ["error", "not working", "problem", "issue", "failed"]):
        return "Try refreshing the page first. For booking problems, make sure both pickup and drop are selected and that the route can be calculated. For login problems, verify your credentials. You can also check the Render logs for deployment errors."

    return "I can help with booking, fare prediction, Leaflet maps, distance calculation, vehicles, login, forgot password, profile, ride history, payments, and Render deployment. What would you like to know?"


@app.route("/api/support-chat", methods=["POST"])
def support_chat():
    payload = request.get_json(silent=True) or {}
    message = str(payload.get("message", "")).strip()

    if len(message) > 500:
        return jsonify({"reply": "Please keep your question under 500 characters."}), 400

    # Use Gemini when a key is configured; otherwise use the built-in
    # support assistant so the application still works without an API key.
    if gemini_client and message:
        system_context = """
You are RideFare AI Support, the assistant for a Flask ride-fare prediction web app.
Answer clearly and briefly. Stay focused on the application's features:
login/register, forgot password, profile, booking, pickup/drop locations,
Leaflet/OpenStreetMap map, OSRM road distance, Random Forest fare prediction,
vehicle types (Bike, Scooty, Auto, Uber Go, Go Non AC, Premier, Uber XL),
passenger limits, payment demo, ride history, and Render deployment.
Do not claim that the app has real-time drivers, live GPS, real payment processing,
or other functionality unless the user asks about a feature that actually exists.
If a question is unrelated, politely say you are focused on RideFare support.
"""
        try:
            response = gemini_client.models.generate_content(
                model=GEMINI_MODEL,
                contents=f"{system_context}\n\nUser question: {message}",
            )
            reply = (response.text or "").strip()
            if reply:
                return jsonify({"reply": reply, "source": "gemini"}), 200
        except Exception as e:
            print("Gemini support error:", e)

    return jsonify({"reply": support_reply(message), "source": "builtin"}), 200


# =========================
# PAYMENT
# =========================
def get_ride_for_user(ride_id, user_id):
    """Fetch a ride only if it belongs to the given user."""
    conn = get_db()
    c = conn.cursor()

    c.execute(
        """
        SELECT id, vehicle, passengers, pickup_name, drop_name,
               distance_km, fare, created_at
        FROM rides
        WHERE id = ? AND user_id = ?
        """,
        (ride_id, user_id)
    )
    ride = c.fetchone()
    conn.close()

    return ride


@app.route("/payment/<int:ride_id>")
def payment(ride_id):

    if "user" not in session:
        return redirect(url_for("auth"))

    ride = get_ride_for_user(ride_id, session.get("user_id"))

    if not ride:
        flash("Ride not found.")
        return redirect(url_for("history"))

    # If this ride already has a successful payment, go straight to the receipt
    conn = get_db()
    c = conn.cursor()
    c.execute(
        """
        SELECT id FROM payments
        WHERE ride_id = ? AND status = 'success'
        ORDER BY id DESC LIMIT 1
        """,
        (ride_id,)
    )
    existing = c.fetchone()
    conn.close()

    if existing:
        return redirect(url_for("payment_receipt", payment_id=existing[0]))

    return render_template("payment.html", ride=ride)


@app.route("/payment/<int:ride_id>/pay", methods=["POST"])
def process_payment(ride_id):

    if "user" not in session:
        return redirect(url_for("auth"))

    user_id = session.get("user_id")
    ride = get_ride_for_user(ride_id, user_id)

    if not ride:
        flash("Ride not found.")
        return redirect(url_for("history"))

    method = request.form.get("method")  # "card" or "upi"
    fare = ride[6]

    card_last4 = None
    status = "success"

    # =========================
    # DEMO / SANDBOX PAYMENT ONLY
    # =========================
    # This does NOT talk to a real payment processor. It validates the
    # form, keeps only a masked card fragment (never the full number
    # or CVV), and simulates an approval. See README for how to swap
    # in a real gateway (Razorpay / Stripe) here.
    if method == "card":

        card_number = re.sub(r"\s+", "", request.form.get("card_number", ""))
        card_name = request.form.get("card_name", "").strip()
        expiry = request.form.get("expiry", "").strip()
        cvv = request.form.get("cvv", "").strip()

        if not (card_name and re.fullmatch(r"\d{13,19}", card_number)
                and re.fullmatch(r"(0[1-9]|1[0-2])\/\d{2}", expiry)
                and re.fullmatch(r"\d{3,4}", cvv)):
            flash("Please enter valid card details.")
            return redirect(url_for("payment", ride_id=ride_id))

        card_last4 = card_number[-4:]

        # Simple sandbox rule so the "declined" path is demonstrable:
        # a card ending in 0000 always fails, like test cards on real
        # gateways.
        if card_number.endswith("0000"):
            status = "failed"

    elif method == "upi":

        upi_id = request.form.get("upi_id", "").strip()

        if not re.fullmatch(r"[\w.\-]{2,}@[a-zA-Z]{2,}", upi_id):
            flash("Please enter a valid UPI ID.")
            return redirect(url_for("payment", ride_id=ride_id))

    else:
        flash("Please choose a payment method.")
        return redirect(url_for("payment", ride_id=ride_id))

    transaction_id = "TXN" + secrets.token_hex(6).upper()

    conn = get_db()
    c = conn.cursor()

    c.execute(
        """
        INSERT INTO payments(
            ride_id, user_id, amount, method, card_last4,
            status, transaction_id, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ride_id,
            user_id,
            fare,
            method,
            card_last4,
            status,
            transaction_id,
            datetime.now(timezone.utc).isoformat(timespec="seconds")
        )
    )

    conn.commit()
    payment_id = c.lastrowid
    conn.close()

    if status == "success":
        simulate_driver_assignment(ride_id)

    return redirect(url_for("payment_receipt", payment_id=payment_id))


@app.route("/payment/receipt/<int:payment_id>")
def payment_receipt(payment_id):

    if "user" not in session:
        return redirect(url_for("auth"))

    conn = get_db()
    c = conn.cursor()

    c.execute(
        """
        SELECT p.id, p.amount, p.method, p.card_last4, p.status,
               p.transaction_id, p.created_at,
               r.vehicle, r.pickup_name, r.drop_name, r.distance_km,
               r.status, r.driver_name, r.driver_phone, r.estimated_time,
               p.ride_id
        FROM payments p
        JOIN rides r ON r.id = p.ride_id
        WHERE p.id = ? AND p.user_id = ?
        """,
        (payment_id, session.get("user_id"))
    )
    payment_row = c.fetchone()
    conn.close()

    if not payment_row:
        flash("Payment not found.")
        return redirect(url_for("history"))

    return render_template("payment_receipt.html", p=payment_row)



# =========================
# RIDE DETAILS / TRACKING (DEMO)
# =========================
@app.route("/ride/<int:ride_id>")
def ride_details(ride_id):
    if "user" not in session:
        return redirect(url_for("auth"))
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT id, vehicle, passengers, pickup_name, drop_name, distance_km,
               fare, created_at, status, driver_name, driver_phone, estimated_time,
               fare_breakdown
        FROM rides WHERE id=? AND user_id=?
    """, (ride_id, session.get("user_id")))
    ride = c.fetchone()
    conn.close()
    if not ride:
        flash("Ride not found.")
        return redirect(url_for("history"))
    breakdown = json.loads(ride[12]) if ride[12] else {}
    return render_template("ride_details.html", ride=ride, breakdown=breakdown)


# =========================
# COMPLAINTS & FEEDBACK
# =========================
@app.route("/complaints", methods=["GET", "POST"])
def complaints():
    if "user" not in session:
        return redirect(url_for("auth"))
    user_id = session.get("user_id")
    conn = get_db()
    c = conn.cursor()
    if request.method == "POST":
        category = request.form.get("category", "").strip()
        message = request.form.get("message", "").strip()
        ride_id = request.form.get("ride_id") or None
        if category and message:
            c.execute(
                "INSERT INTO complaints(user_id, ride_id, category, message, created_at) VALUES (?,?,?,?,?)",
                (user_id, ride_id, category, message, datetime.now(timezone.utc).isoformat(timespec="seconds"))
            )
            conn.commit()
            flash("Complaint submitted. The support team can review it from the admin dashboard.")
        else:
            flash("Please select a category and describe the issue.")
        conn.close()
        return redirect(url_for("complaints"))
    c.execute("SELECT id, pickup_name, drop_name, created_at FROM rides WHERE user_id=? ORDER BY id DESC", (user_id,))
    rides = c.fetchall()
    c.execute("SELECT id, ride_id, category, message, status, created_at FROM complaints WHERE user_id=? ORDER BY id DESC", (user_id,))
    items = c.fetchall()
    conn.close()
    return render_template("complaints.html", rides=rides, complaints=items)


@app.route("/feedback/<int:ride_id>", methods=["GET", "POST"])
def feedback(ride_id):
    if "user" not in session:
        return redirect(url_for("auth"))
    user_id = session.get("user_id")
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id, vehicle, pickup_name, drop_name, fare FROM rides WHERE id=? AND user_id=?", (ride_id, user_id))
    ride = c.fetchone()
    if not ride:
        conn.close()
        flash("Ride not found.")
        return redirect(url_for("history"))
    if request.method == "POST":
        try:
            rating = int(request.form.get("rating", "0"))
        except ValueError:
            rating = 0
        comment = request.form.get("comment", "").strip()
        if rating not in range(1, 6):
            flash("Rating must be between 1 and 5.")
        else:
            c.execute("SELECT id FROM feedback WHERE user_id=? AND ride_id=?", (user_id, ride_id))
            if c.fetchone():
                c.execute("UPDATE feedback SET rating=?, comment=?, created_at=? WHERE user_id=? AND ride_id=?",
                          (rating, comment, datetime.now(timezone.utc).isoformat(timespec="seconds"), user_id, ride_id))
            else:
                c.execute("INSERT INTO feedback(user_id, ride_id, rating, comment, created_at) VALUES (?,?,?,?,?)",
                          (user_id, ride_id, rating, comment, datetime.now(timezone.utc).isoformat(timespec="seconds")))
            conn.commit()
            flash("Thank you for your feedback.")
            conn.close()
            return redirect(url_for("ride_details", ride_id=ride_id))
    c.execute("SELECT rating, comment FROM feedback WHERE user_id=? AND ride_id=?", (user_id, ride_id))
    existing_feedback = c.fetchone()
    conn.close()
    return render_template("feedback.html", ride=ride, feedback=existing_feedback)


# =========================
# ADMIN DASHBOARD
# =========================
@app.route("/admin")
def admin_dashboard():
    guard = require_admin()
    if guard:
        return guard
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM users")
    users_count = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM rides")
    rides_count = c.fetchone()[0]
    c.execute("SELECT COALESCE(SUM(amount),0) FROM payments WHERE status='success'")
    revenue = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM complaints WHERE status='Open'")
    open_complaints = c.fetchone()[0]
    c.execute("SELECT ROUND(AVG(rating),2) FROM feedback")
    avg_rating = c.fetchone()[0] or 0
    c.execute("""SELECT r.id, u.username, r.vehicle, r.pickup_name, r.drop_name, r.fare,
                        r.status, r.created_at
                 FROM rides r JOIN users u ON u.id=r.user_id ORDER BY r.id DESC LIMIT 12""")
    recent_rides = c.fetchall()
    c.execute("""SELECT c.id, u.username, c.category, c.message, c.status, c.created_at
                 FROM complaints c JOIN users u ON u.id=c.user_id
                 ORDER BY c.id DESC LIMIT 12""")
    recent_complaints = c.fetchall()
    c.execute("""SELECT r.vehicle, COUNT(*) FROM rides r GROUP BY r.vehicle ORDER BY COUNT(*) DESC""")
    vehicle_stats = c.fetchall()
    conn.close()
    return render_template(
        "admin.html", users_count=users_count, rides_count=rides_count,
        revenue=round(revenue,2), open_complaints=open_complaints,
        avg_rating=avg_rating, recent_rides=recent_rides,
        recent_complaints=recent_complaints, vehicle_stats=vehicle_stats,
        model_metrics=model_metrics, accuracy=round(accuracy*100,2)
    )


@app.route("/admin/complaint/<int:complaint_id>/close", methods=["POST"])
def close_complaint(complaint_id):
    guard = require_admin()
    if guard:
        return guard
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE complaints SET status='Resolved' WHERE id=?", (complaint_id,))
    conn.commit()
    conn.close()
    flash("Complaint marked as resolved.")
    return redirect(url_for("admin_dashboard"))


# =========================
# LOGOUT
# =========================
@app.route("/logout")
def logout():

    session.clear()

    return render_template("logout.html")


# =========================
# RUN APPLICATION
# =========================
if __name__ == "__main__":
    app.run(debug=True)
