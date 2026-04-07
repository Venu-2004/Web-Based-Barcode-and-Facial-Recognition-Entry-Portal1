import cv2
try:
    from pyzbar.pyzbar import decode, ZBarSymbol
except Exception as e:
    decode = None
    ZBarSymbol = None
    print(f"!!! WARNING: pyzbar unavailable; using OpenCV QR fallback decoder. Error: {e}")
from flask import Flask, render_template, request, jsonify, session, redirect, url_for, Response, flash
import numpy as np
import base64
import os
from PIL import Image, ImageDraw, ImageFont
import time
import threading
import csv
import sqlite3
import qrcode
import re
import smtplib
from email.message import EmailMessage
from pathlib import Path
import shutil
from functools import wraps
from math import radians, cos, sin, asin, sqrt
from datetime import datetime, timezone

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None

# --- 1. App Configuration and Setup ---
app = Flask(__name__)
app.secret_key = 'your-super-secret-key-for-flask'

OFFICE_COORDINATES = (9.7196, 77.56310)
MAX_DISTANCE_METERS = 100000
OFFICE_NAME = "Kalasalingam Academy"

# --- Define paths ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')
DATASET_DIR = os.path.join(BASE_DIR, 'dataset')
TRAINER_DIR = os.path.join(BASE_DIR, 'trainer')
QR_OUTPUT_DIR = os.path.join(BASE_DIR, 'QR_generated')
USER_DB_FILE = os.path.join(BASE_DIR, 'users.csv')
SQLITE_DB_FILE = os.path.join(BASE_DIR, 'mfa_auth.db')
CASCADE_PATH = os.path.join(DATA_DIR, 'haarcascade_frontalface_default.xml')
EYE_CASCADE_PATH = os.path.join(DATA_DIR, 'haarcascade_eye.xml')
TRAINER_FILE = os.path.join(TRAINER_DIR, 'trainer.yml')
PROTOTXT_PATH = os.path.join(DATA_DIR, 'MobileNetSSD_deploy.prototxt.txt')
MODEL_PATH = os.path.join(DATA_DIR, 'MobileNetSSD_deploy.caffemodel')

ENV_FILE_PATH = Path(BASE_DIR) / '.env'
ENV_EXAMPLE_FILE_PATH = Path(BASE_DIR) / '.env.example'

if not ENV_FILE_PATH.exists() and ENV_EXAMPLE_FILE_PATH.exists():
    try:
        shutil.copy(ENV_EXAMPLE_FILE_PATH, ENV_FILE_PATH)
        print("INFO: Created .env from .env.example. Update SMTP values in .env to enable QR e-mail sending.")
    except Exception as env_copy_error:
        print(f"WARNING: Could not auto-create .env file. Error: {env_copy_error}")

if load_dotenv is not None:
    load_dotenv(dotenv_path=ENV_FILE_PATH)

os.makedirs(DATASET_DIR, exist_ok=True)
os.makedirs(TRAINER_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(QR_OUTPUT_DIR, exist_ok=True)

# --- 2. Load Models and Data at Startup ---
face_detector = cv2.CascadeClassifier(CASCADE_PATH)
eye_detector = cv2.CascadeClassifier(EYE_CASCADE_PATH)
recognizer = cv2.face.LBPHFaceRecognizer_create()
qr_detector = cv2.QRCodeDetector()

if face_detector.empty():
    print(f"!!! FATAL ERROR: Could not load face detector from {CASCADE_PATH}")
if eye_detector.empty():
    print(f"!!! FATAL ERROR: Could not load eye detector from {EYE_CASCADE_PATH}")

try:
    person_net = cv2.dnn.readNetFromCaffe(PROTOTXT_PATH, MODEL_PATH)
    print("Successfully loaded Person Detection model for tailgating.")
except cv2.error as e:
    person_net = None
    print(f"!!! WARNING: Could not load Person Detection model. Tailgating feature will be disabled. Error: {e}")

# In-memory data stores
face_id_to_user_map = {}
authorized_users_for_qr = {}
liveness_auth_data = { 'status': 'pending', 'user': None, 'message': None, 'lock': threading.Lock() }
tailgating_event_data = { 'detected': False, 'lock': threading.Lock() }

def get_db_connection():
    conn = sqlite3.connect(SQLITE_DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()

def get_request_metadata():
    return request.remote_addr, request.headers.get('User-Agent', '')

def init_database():
    conn = get_db_connection()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            user_name TEXT NOT NULL,
            email TEXT,
            face_id INTEGER NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS auth_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            user_name TEXT,
            login_method TEXT NOT NULL,
            status TEXT NOT NULL,
            authenticated_at TEXT,
            logged_in_at TEXT,
            auth_latitude REAL,
            auth_longitude REAL,
            auth_distance_meters REAL,
            auth_location_text TEXT,
            ip_address TEXT,
            user_agent TEXT,
            notes TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    ''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_auth_logs_user_id ON auth_logs(user_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_auth_logs_created_at ON auth_logs(created_at)')

    user_columns = {row['name'] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    if 'email' not in user_columns:
        conn.execute('ALTER TABLE users ADD COLUMN email TEXT')

    conn.commit()
    conn.close()

def is_valid_email(email):
    return bool(re.match(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$", email or ""))

def infer_smtp_settings_from_email(email_address):
    if not email_address or '@' not in email_address:
        return None, None

    domain = email_address.split('@', 1)[1].lower().strip()
    mapping = {
        'gmail.com': ('smtp.gmail.com', 587),
        'outlook.com': ('smtp.office365.com', 587),
        'hotmail.com': ('smtp.office365.com', 587),
        'live.com': ('smtp.office365.com', 587),
        'yahoo.com': ('smtp.mail.yahoo.com', 587),
        'icloud.com': ('smtp.mail.me.com', 587)
    }
    return mapping.get(domain, (None, None))

def send_qr_email_to_user(user_email, user_name, user_id, qr_file_path):
    sender_email = os.getenv('MFA_SENDER_EMAIL') or os.getenv('MFA_SMTP_USER')
    smtp_host = os.getenv('MFA_SMTP_HOST')
    smtp_port = int(os.getenv('MFA_SMTP_PORT', '587'))
    inferred_host, inferred_port = infer_smtp_settings_from_email(sender_email)
    smtp_host = smtp_host or inferred_host
    smtp_port = smtp_port if os.getenv('MFA_SMTP_PORT') else (inferred_port or smtp_port)

    smtp_user = os.getenv('MFA_SMTP_USER')
    smtp_password = os.getenv('MFA_SMTP_PASSWORD')
    use_tls = os.getenv('MFA_SMTP_USE_TLS', 'true').lower() in ('1', 'true', 'yes')

    if not sender_email:
        return False, 'SMTP sender not configured. Open .env and set MFA_SENDER_EMAIL and MFA_SMTP_USER.'

    if not smtp_host:
        return False, 'SMTP host not configured. Set MFA_SMTP_HOST in .env or use a supported sender domain (gmail/outlook/yahoo/icloud).'

    if smtp_user and not smtp_password:
        return False, 'SMTP password missing. Set MFA_SMTP_PASSWORD in .env (for Gmail, use App Password).'

    message = EmailMessage()
    message['Subject'] = f"Your MFA QR Code - User ID {user_id}"
    message['From'] = sender_email
    message['To'] = user_email
    message.set_content(
        f"Hello {user_name},\n\n"
        f"Attached is your QR code image for future login.\n"
        f"User ID: {user_id}\n\n"
        f"Please keep this QR code secure.\n\n"
        f"- MFA System"
    )

    with open(qr_file_path, 'rb') as qr_file:
        qr_bytes = qr_file.read()
    message.add_attachment(qr_bytes, maintype='image', subtype='png', filename=os.path.basename(qr_file_path))

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as server:
            if use_tls:
                server.starttls()
            if smtp_user and smtp_password:
                server.login(smtp_user, smtp_password)
            server.send_message(message)
        return True, None
    except smtplib.SMTPAuthenticationError as e:
        details = str(e)
        if 'gmail.com' in (smtp_host or '').lower() or 'BadCredentials' in details or '5.7.8' in details:
            return False, (
                'Gmail authentication failed (535). Use MFA_SMTP_USER as your full Gmail address and '
                'MFA_SMTP_PASSWORD as a Google App Password (not your normal Gmail password). '
                'Also make sure 2-Step Verification is enabled on that Google account.'
            )
        return False, f'SMTP authentication failed: {details}'
    except Exception as e:
        return False, str(e)

def migrate_users_csv_to_db_if_needed():
    if not os.path.exists(USER_DB_FILE):
        return
    conn = get_db_connection()
    existing_count = conn.execute('SELECT COUNT(*) AS count FROM users').fetchone()['count']
    if existing_count > 0:
        conn.close()
        return

    try:
        with open(USER_DB_FILE, mode='r', newline='', encoding='utf-8') as csvfile:
            reader = csv.DictReader(csvfile)
            now = utc_now_iso()
            for row in reader:
                user_id = (row.get('id') or row.get('user_id') or '').strip()
                user_name = (row.get('name') or row.get('user_name') or '').strip()
                user_email = (row.get('email') or row.get('user_email') or '').strip().lower()
                face_id_raw = row.get('face_id')
                if not user_id or not user_name or face_id_raw is None:
                    continue
                try:
                    face_id = int(face_id_raw)
                except ValueError:
                    continue

                conn.execute(
                    '''
                    INSERT OR IGNORE INTO users (user_id, user_name, email, face_id, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ''',
                    (user_id, user_name, user_email if user_email else None, face_id, now, now)
                )
        conn.commit()
    except Exception as e:
        print(f"WARNING: Could not migrate users from CSV to SQLite. Error: {e}")
    finally:
        conn.close()

def create_auth_log(user_id, user_name, login_method, status, authenticated_at=None, logged_in_at=None,
                    auth_latitude=None, auth_longitude=None, auth_distance_meters=None,
                    auth_location_text=None, notes=None):
    ip_address, user_agent = get_request_metadata()
    conn = get_db_connection()
    cursor = conn.execute(
        '''
        INSERT INTO auth_logs (
            user_id, user_name, login_method, status, authenticated_at, logged_in_at,
            auth_latitude, auth_longitude, auth_distance_meters, auth_location_text,
            ip_address, user_agent, notes, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (
            user_id, user_name, login_method, status, authenticated_at, logged_in_at,
            auth_latitude, auth_longitude, auth_distance_meters, auth_location_text,
            ip_address, user_agent, notes, utc_now_iso()
        )
    )
    conn.commit()
    log_id = cursor.lastrowid
    conn.close()
    return log_id

def update_auth_log(log_id, status=None, logged_in_at=None, auth_latitude=None, auth_longitude=None,
                    auth_distance_meters=None, auth_location_text=None, notes=None):
    if not log_id:
        return

    conn = get_db_connection()
    row = conn.execute('SELECT * FROM auth_logs WHERE id = ?', (log_id,)).fetchone()
    if row is None:
        conn.close()
        return

    conn.execute(
        '''
        UPDATE auth_logs
        SET status = ?,
            logged_in_at = ?,
            auth_latitude = ?,
            auth_longitude = ?,
            auth_distance_meters = ?,
            auth_location_text = ?,
            notes = ?
        WHERE id = ?
        ''',
        (
            status if status is not None else row['status'],
            logged_in_at if logged_in_at is not None else row['logged_in_at'],
            auth_latitude if auth_latitude is not None else row['auth_latitude'],
            auth_longitude if auth_longitude is not None else row['auth_longitude'],
            auth_distance_meters if auth_distance_meters is not None else row['auth_distance_meters'],
            auth_location_text if auth_location_text is not None else row['auth_location_text'],
            notes if notes is not None else row['notes'],
            log_id
        )
    )
    conn.commit()
    conn.close()

def load_user_data():
    global authorized_users_for_qr, face_id_to_user_map
    users_for_qr = {}
    face_id_to_user_map.clear()
    try:
        conn = get_db_connection()
        rows = conn.execute('SELECT user_id, user_name, face_id FROM users').fetchall()
        for row in rows:
            users_for_qr[row['user_id']] = row['user_name']
            face_id_to_user_map[int(row['face_id'])] = {'user_id': row['user_id'], 'name': row['user_name']}
        conn.close()
        print(f"Successfully loaded {len(users_for_qr)} users.")
        authorized_users_for_qr = users_for_qr
    except Exception as e:
        print(f"FATAL ERROR: Could not read user database '{SQLITE_DB_FILE}'. Error: {e}")

init_database()
migrate_users_csv_to_db_if_needed()
load_user_data()

def find_qr_code_in_image(image):
    try:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

        if decode is not None:
            decoded_objects = decode(gray, symbols=[ZBarSymbol.QRCODE]) if ZBarSymbol else decode(gray)
            for obj in decoded_objects:
                value = obj.data.decode("utf-8").strip()
                if value:
                    return value

        value, _, _ = qr_detector.detectAndDecode(gray)
        if value and value.strip():
            return value.strip()

        multi_found, decoded_values, _, _ = qr_detector.detectAndDecodeMulti(gray)
        if multi_found:
            for decoded_value in decoded_values:
                if decoded_value and decoded_value.strip():
                    return decoded_value.strip()

        return None
    except Exception: return None

def generate_qr_png_for_user_id(user_id):
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4
    )
    qr.add_data(user_id)
    qr.make(fit=True)

    qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGB")

    label_padding = 12
    label_height = 34
    canvas = Image.new("RGB", (qr_img.width, qr_img.height + label_height + label_padding), "white")
    canvas.paste(qr_img, (0, 0))

    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default()
    label_text = user_id
    text_bbox = draw.textbbox((0, 0), label_text, font=font)
    text_width = text_bbox[2] - text_bbox[0]
    text_x = max(0, (canvas.width - text_width) // 2)
    text_y = qr_img.height + 10
    draw.text((text_x, text_y), label_text, fill="black", font=font)

    safe_file_name = ''.join(ch for ch in user_id if ch.isalnum() or ch in ('-', '_')) or 'user_id'
    output_path = os.path.join(QR_OUTPUT_DIR, f"{safe_file_name}.png")
    canvas.save(output_path)
    return output_path

def get_images_and_labels_for_training(path):
    image_paths = [os.path.join(path, f) for f in os.listdir(path)]
    face_samples, ids = [], []
    for image_path in image_paths:
        try:
            pil_image = Image.open(image_path).convert('L')
            img_numpy = np.array(pil_image, 'uint8')
            face_id = int(os.path.split(image_path)[-1].split(".")[1])
            faces = face_detector.detectMultiScale(img_numpy)
            for (x, y, w, h) in faces:
                face_samples.append(img_numpy[y:y+h, x:x+w])
                ids.append(face_id)
        except Exception as e:
            print(f"Warning: Skipping image {image_path}: {e}")
    return face_samples, ids


def perform_face_recognition(image_gray, face_roi):
    if not os.path.exists(TRAINER_FILE) or os.path.getsize(TRAINER_FILE) == 0:
        return None, "Face model file not found or is empty. Please train the model."
    try:
        recognizer.read(TRAINER_FILE)
        (x, y, w, h) = face_roi
        predicted_face_id, confidence = recognizer.predict(image_gray[y:y+h, x:x+w])
        threshold = 75.0
        if confidence < threshold:
            user_details = face_id_to_user_map.get(predicted_face_id)
            if user_details:
                return user_details, "Recognition successful."
        return None, "User not recognized."
    except cv2.error as e:
        return None, "Model data is invalid. Please re-train the model."
    except Exception as e:
        return None, "An unexpected error occurred."

def detect_persons_in_frame(frame):
    if person_net is None: return frame, 0
    (h, w) = frame.shape[:2]
    blob = cv2.dnn.blobFromImage(cv2.resize(frame, (300, 300)), 0.007843, (300, 300), 127.5)
    person_net.setInput(blob)
    detections = person_net.forward()
    person_count = 0
    CLASSES = ["background", "aeroplane", "bicycle", "bird", "boat", "bottle", "bus", "car", "cat", "chair", "cow", "diningtable", "dog", "horse", "motorbike", "person", "pottedplant", "sheep", "sofa", "train", "tvmonitor"]
    for i in np.arange(0, detections.shape[2]):
        confidence = detections[0, 0, i, 2]
        if confidence > 0.4:
            idx = int(detections[0, 0, i, 1])
            if CLASSES[idx] == "person":
                person_count += 1
    return frame, person_count

def haversine(lon1, lat1, lon2, lat2):
    lon1, lat1, lon2, lat2 = map(radians, [lon1, lat1, lon2, lat2])
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    c = 2 * asin(sqrt(a))
    r = 6371
    return c * r


# =====================================================================
# ================== AUTHENTICATION DECORATORS ========================
# =====================================================================

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('is_admin'):
            flash('You must be an admin to access this page.', 'warning')
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_name' not in session:
            flash("You must be logged in to view this page.", "warning")
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

# =====================================================================
# ======================== ADMIN ROUTES ===============================
# =====================================================================

@app.route('/admin_login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if username == 'admin' and password == 'admin@1234':
            session['is_admin'] = True
            flash('Admin login successful!', 'success')
            return redirect(url_for('add_user'))
        else:
            flash('Invalid credentials. Please try again.', 'error')
            return redirect(url_for('admin_login'))
    return render_template('admin_login.html')

@app.route('/admin_logout')
def admin_logout():
    session.pop('is_admin', None)
    flash('You have been logged out from the admin panel.', 'info')
    return redirect(url_for('admin_login'))


# =====================================================================
# ============= MAIN APPLICATION ROUTES (Public and User) =============
# =====================================================================

@app.route('/')
def index():
    if 'user_id' in session: return redirect(url_for('dashboard')) # MODIFIED
    return render_template('index.html')

@app.route('/face_auth')
def face_auth_page():
    if 'user_id' in session: return redirect(url_for('dashboard')) # MODIFIED
    if face_detector.empty() or eye_detector.empty():
        flash("Liveness detection models not loaded. Please check server logs.", "error")
        return redirect(url_for('index'))
    with liveness_auth_data['lock']:
        liveness_auth_data['status'] = 'pending'
        liveness_auth_data['user'] = None
        liveness_auth_data['message'] = None
    return render_template('face_auth.html')

# ================= NEW DASHBOARD ROUTE (REPLACES MAP) ================
@app.route('/dashboard')
@login_required
def dashboard():
    # This is the main page for a logged-in user.
    return render_template('dashboard.html', user_name=session['user_name'])
# =====================================================================

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    session.pop('user_name', None)
    flash("You have been successfully logged out.", "success")
    return redirect(url_for('index'))

@app.route('/entry_monitoring')
@login_required
def entry_monitoring():
    with tailgating_event_data['lock']:
        tailgating_event_data['detected'] = False
    return render_template('entry_monitoring.html', user_name=session['user_name'])

@app.route('/tailgating_feed')
def tailgating_feed():
    def generate():
        cam = cv2.VideoCapture(0)
        start_time = time.time()
        monitoring_duration = 8
        while time.time() - start_time < monitoring_duration:
            success, frame = cam.read()
            if not success: break
            _, person_count = detect_persons_in_frame(frame.copy())
            if person_count > 1:
                with tailgating_event_data['lock']:
                    tailgating_event_data['detected'] = True
            ret, buffer = cv2.imencode('.jpg', frame)
            yield (b'--frame\r\n' b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')
        cam.release()
    return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/check_tailgating_status')
def check_tailgating_status():
    with tailgating_event_data['lock']:
        detected = tailgating_event_data['detected']
    if detected:
        session.clear()
        return jsonify({'tailgating_detected': True, 'redirect_url': url_for('index')})
    return jsonify({'tailgating_detected': False})

@app.route('/liveness_feed')
def liveness_feed():
    def generate():
        cam = cv2.VideoCapture(0)
        EYE_CLOSED_FRAMES, REQUIRED_BLINKS, closed_counter, total_blinks = 3, 2, 0, 0
        eyes_were_open = True
        start_time, timeout = time.time(), 20
        timed_out = True
        while time.time() - start_time < timeout:
            with liveness_auth_data['lock']:
                if liveness_auth_data['status'] != 'pending': break
            success, frame = cam.read()
            if not success: break
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = face_detector.detectMultiScale(gray, 1.3, 5, minSize=(100, 100))
            if len(faces) > 0:
                (face_x, face_y, face_w, face_h) = faces[0]
                cv2.rectangle(frame, (face_x, face_y), (face_x + face_w, face_y + face_h), (255, 0, 0), 2)
                face_roi_gray = gray[face_y:face_y + face_h, face_x:face_x + face_w]
                eyes = eye_detector.detectMultiScale(face_roi_gray)
                if len(eyes) == 0:
                    closed_counter += 1
                    eyes_were_open = False
                else:
                    if not eyes_were_open and closed_counter >= EYE_CLOSED_FRAMES:
                        total_blinks += 1
                    eyes_were_open = True
                    closed_counter = 0
                blink_text = f"Blinks: {total_blinks}/{REQUIRED_BLINKS}"
                font = cv2.FONT_HERSHEY_SIMPLEX
                font_scale = 0.95
                thickness = 3
                (text_width, text_height), baseline = cv2.getTextSize(blink_text, font, font_scale, thickness)
                text_x = max(12, frame.shape[1] - text_width - 24)
                text_y = 42

                cv2.rectangle(
                    frame,
                    (text_x - 12, text_y - text_height - 12),
                    (text_x + text_width + 12, text_y + baseline + 8),
                    (0, 0, 0),
                    -1
                )
                cv2.putText(frame, blink_text, (text_x, text_y), font, font_scale, (255, 255, 255), thickness)
                if total_blinks >= REQUIRED_BLINKS:
                    timed_out = False
                    user, message = perform_face_recognition(gray, (face_x, face_y, face_w, face_h))
                    with liveness_auth_data['lock']:
                        if user:
                            liveness_auth_data['status'] = 'success'
                            liveness_auth_data['user'] = user
                        else:
                            liveness_auth_data['status'] = 'fail'
                            liveness_auth_data['message'] = message
                    break
            ret, buffer = cv2.imencode('.jpg', frame)
            yield (b'--frame\r\n' b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')
        with liveness_auth_data['lock']:
            if timed_out and liveness_auth_data['status'] == 'pending':
                liveness_auth_data['status'] = 'fail'
                liveness_auth_data['message'] = 'Liveness check timed out. Please try again.'
        cam.release()
    return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/check_liveness_status')
def check_liveness_status():
    with liveness_auth_data['lock']:
        status = liveness_auth_data['status']
        user = liveness_auth_data.get('user')
        message = liveness_auth_data.get('message', 'An unknown error occurred.')

    if status == 'success' and user:
        auth_time = utc_now_iso()
        create_auth_log(
            user_id=user['user_id'],
            user_name=user['name'],
            login_method='face_liveness',
            status='login_success',
            authenticated_at=auth_time,
            logged_in_at=auth_time,
            notes='Face liveness flow success'
        )
        session['user_id'] = user['user_id']
        session['user_name'] = user['name']
        return jsonify({'status': 'success', 'redirect_url': url_for('entry_monitoring')})
    elif status == 'fail':
        return jsonify({'status': 'fail', 'message': f'Liveness confirmed, but auth failed. Reason: {message}'})
    else:
        return jsonify({'status': 'pending'})

@app.route('/scan_qr', methods=['POST'])
def scan_qr():
    data = request.get_json()
    if not data or 'image' not in data: return jsonify({'status': 'error', 'message': 'No image data.'}), 400
    try:
        _, encoded = data['image'].split(",", 1)
        img = cv2.imdecode(np.frombuffer(base64.b64decode(encoded), np.uint8), cv2.IMREAD_COLOR)
    except Exception: return jsonify({'status': 'error', 'message': 'Invalid image data.'}), 400
    if img is None: return jsonify({'status': 'error', 'message': 'Could not decode image.'}), 400

    scanned_id = find_qr_code_in_image(img)
    if scanned_id and scanned_id in authorized_users_for_qr:
        auth_log_id = create_auth_log(
            user_id=scanned_id,
            user_name=authorized_users_for_qr[scanned_id],
            login_method='qr_code',
            status='authenticated_pending_location',
            authenticated_at=utc_now_iso(),
            notes='QR validated, waiting for location check'
        )
        session['pending_location_check_id'] = scanned_id
        session['pending_auth_log_id'] = auth_log_id
        return jsonify({'status': 'success', 'redirect_url': url_for('location_check')})
    else:
        message = "QR code ID not recognized." if scanned_id else "No QR code found."
        return jsonify({'status': 'not_found', 'message': message})

@app.route('/location_check')
def location_check():
    if 'pending_location_check_id' not in session:
        flash("Please scan your QR code first.", "warning")
        return redirect(url_for('index'))
    return render_template('location_check.html')

@app.route('/verify_location_and_login', methods=['POST'])
def verify_location_and_login():
    if 'pending_location_check_id' not in session:
        return jsonify({'status': 'error', 'message': 'No pending user session.'}), 403

    user_id = session.pop('pending_location_check_id')
    auth_log_id = session.pop('pending_auth_log_id', None)
    data = request.get_json()
    lat, lon = data.get('latitude'), data.get('longitude')
    office_lat, office_lon = OFFICE_COORDINATES
    distance_km = haversine(lon, lat, office_lon, office_lat)
    distance_meters = distance_km * 1000

    if distance_meters <= MAX_DISTANCE_METERS:
        user_name = authorized_users_for_qr[user_id]
        session['user_id'] = user_id
        session['user_name'] = user_name
        update_auth_log(
            auth_log_id,
            status='login_success',
            logged_in_at=utc_now_iso(),
            auth_latitude=lat,
            auth_longitude=lon,
            auth_distance_meters=distance_meters,
            auth_location_text=f"lat={lat}, lon={lon}",
            notes='Location verified and login granted'
        )
        return jsonify({
            'status': 'success',
            'message': f"On-site location confirmed near {OFFICE_NAME} ({int(distance_meters)}m). Access granted.",
            'redirect_url': url_for('dashboard') # MODIFIED
        })
    else:
        update_auth_log(
            auth_log_id,
            status='location_check_failed',
            auth_latitude=lat,
            auth_longitude=lon,
            auth_distance_meters=distance_meters,
            auth_location_text=f"lat={lat}, lon={lon}",
            notes='Location too far, login denied'
        )
        return jsonify({
            'status': 'fail',
            'message': f"Access Denied: You are {distance_km:.2f}km away from {OFFICE_NAME}."
        })
    
@app.route('/post_auth_location_check')
@login_required
def post_auth_location_check():
    """Renders the page that will perform the final location check."""
    with tailgating_event_data['lock']:
        if tailgating_event_data['detected']:
            session.clear()
            flash("Tailgating detected. Please login again.", "error")
            return redirect(url_for('index'))
    return render_template('post_auth_location_check.html')

# ✅ STEP 2: NEW ROUTE TO VERIFY THE LOCATION AND GRANT/DENY DASHBOARD ACCESS
@app.route('/verify_final_location', methods=['POST'])
@login_required
def verify_final_location():
    """Receives coords, checks distance, and decides final access."""
    data = request.get_json()
    if not data or 'latitude' not in data or 'longitude' not in data:
        return jsonify({'status': 'error', 'message': 'Invalid location data.'}), 400

    user_lat, user_lon = data.get('latitude'), data.get('longitude')
    office_lat, office_lon = OFFICE_COORDINATES

    distance_meters = haversine(user_lon, user_lat, office_lon, office_lat)

    if distance_meters <= MAX_DISTANCE_METERS:
        create_auth_log(
            user_id=session.get('user_id'),
            user_name=session.get('user_name'),
            login_method='post_auth_location_check',
            status='location_check_success',
            authenticated_at=utc_now_iso(),
            auth_latitude=user_lat,
            auth_longitude=user_lon,
            auth_distance_meters=distance_meters,
            auth_location_text=f"lat={user_lat}, lon={user_lon}",
            notes='Post-auth location check passed'
        )
        # User is near, grant access to dashboard
        flash(f"Location confirmed near {OFFICE_NAME} ({int(distance_meters)}m). Welcome!", "success")
        return jsonify({
            'status': 'success',
            'redirect_url': url_for('dashboard')
        })
    else:
        # User is too far, log them out and deny access
        user_name = session.get('user_name', 'User')
        create_auth_log(
            user_id=session.get('user_id'),
            user_name=user_name,
            login_method='post_auth_location_check',
            status='location_check_failed',
            authenticated_at=utc_now_iso(),
            auth_latitude=user_lat,
            auth_longitude=user_lon,
            auth_distance_meters=distance_meters,
            auth_location_text=f"lat={user_lat}, lon={user_lon}",
            notes='Post-auth location check failed; session cleared'
        )
        session.clear() # Log the user out completely
        flash(f"Access for {user_name} denied. You are {distance_meters/1000:.2f}km away from {OFFICE_NAME}.", "error")
        return jsonify({
            'status': 'fail',
            'message': f'You are too far from {OFFICE_NAME} to access the dashboard.',
            'redirect_url': url_for('index') # Send them back to the login page
        })

# =====================================================================
# =================== SECURED ADMIN ROUTES ============================
# =====================================================================
@app.route('/add_user', methods=['POST', 'GET'])
@admin_required
def add_user():
    if request.method == 'POST':
        long_user_id = request.form.get('user_id')
        user_name = request.form.get('user_name', '').strip()
        user_email = request.form.get('user_email', '').strip().lower()
        if not long_user_id or not user_name or not user_email:
            flash("User ID, Name, and E-mail are required.", "error")
            return redirect(url_for('add_user'))
        if not is_valid_email(user_email):
            flash("Please enter a valid E-mail address.", "error")
            return redirect(url_for('add_user'))
        if '.' in user_name:
            flash("Usernames cannot contain periods.", "error")
            return redirect(url_for('add_user'))
        if long_user_id in authorized_users_for_qr:
            flash(f"User ID {long_user_id} already exists.", "error")
            return redirect(url_for('add_user'))
        new_face_id = max(face_id_to_user_map.keys()) + 1 if face_id_to_user_map else 1
        now = utc_now_iso()
        conn = None
        qr_output_path = None
        try:
            conn = get_db_connection()
            conn.execute(
                '''
                INSERT INTO users (user_id, user_name, email, face_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ''',
                (long_user_id, user_name, user_email, new_face_id, now, now)
            )

            qr_output_path = generate_qr_png_for_user_id(long_user_id)
            conn.commit()
        except sqlite3.IntegrityError:
            if conn:
                conn.rollback()
            flash(f"User ID {long_user_id} already exists.", "error")
            return redirect(url_for('add_user'))
        except Exception as e:
            if conn:
                conn.rollback()
            flash(f"Could not add user or generate QR code. Error: {e}", "error")
            return redirect(url_for('add_user'))
        finally:
            if conn:
                conn.close()

        if qr_output_path:
            sent, email_error = send_qr_email_to_user(user_email, user_name, long_user_id, qr_output_path)
            if sent:
                flash(f"QR code was generated and emailed to {user_email}.", "info")
            else:
                flash(f"QR code generated, but email could not be sent ({email_error}).", "warning")

        load_user_data()
        flash(f"User {user_name} added. Now capture face.", "success")
        return redirect(url_for('capture_face', face_id=new_face_id, user_name=user_name))
    return render_template('add_user.html')

@app.route('/capture/<int:face_id>/<string:user_name>')
@admin_required
def capture_face(face_id, user_name):
    return render_template('capture.html', face_id=face_id, user_name=user_name)

@app.route('/capture_feed/<int:face_id>/<string:user_name>')
@admin_required
def capture_feed(face_id, user_name):
    def generate():
        cam = cv2.VideoCapture(0)
        count, max_images = 0, 50
        while count < max_images:
            success, frame = cam.read()
            if not success: break
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = face_detector.detectMultiScale(gray, 1.3, 5, minSize=(100, 100))
            for (x, y, w, h) in faces:
                cv2.rectangle(frame, (x, y), (x+w, y+h), (255, 0, 0), 2)
                count += 1
                file_path = os.path.join(DATASET_DIR, f"User.{face_id}.{user_name}.{count}.jpg")
                cv2.imwrite(file_path, gray[y:y+h, x:x+w])
                progress_text = f"Captured: {count}/{max_images}"
                cv2.putText(frame, progress_text, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
                time.sleep(0.1)
            ret, buffer = cv2.imencode('.jpg', frame)
            yield (b'--frame\r\n' b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')
        cam.release()
    return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/train')
@admin_required
def train_page():
    return render_template('train.html')

@app.route('/train_model')
@admin_required
def train_model():
    flash("Training started. This might take a moment...", "info")
    faces, ids = get_images_and_labels_for_training(DATASET_DIR)
    if not faces:
        flash("No faces found in dataset to train.", "error")
        return redirect(url_for('train_page'))
    recognizer.train(faces, np.array(ids))
    recognizer.write(TRAINER_FILE)
    num_users = len(np.unique(ids))
    flash(f"Model trained successfully on {num_users} user(s).", "success")
    return redirect(url_for('index'))

@app.route('/admin/db')
@admin_required
def admin_db_view():
    conn = get_db_connection()
    users = conn.execute(
        '''
        SELECT user_id, user_name, email, face_id, created_at, updated_at
        FROM users
        ORDER BY created_at DESC
        '''
    ).fetchall()

    logs = conn.execute(
        '''
        SELECT id, user_id, user_name, login_method, status,
               authenticated_at, logged_in_at,
               auth_latitude, auth_longitude, auth_distance_meters, auth_location_text,
               ip_address, user_agent, notes, created_at
        FROM auth_logs
        ORDER BY id DESC
        LIMIT 200
        '''
    ).fetchall()
    conn.close()

    return render_template(
        'admin_db.html',
        users=[dict(row) for row in users],
        logs=[dict(row) for row in logs]
    )

def _normalize_optional_text(value):
    text = (value or '').strip()
    if not text or text == '-':
        return None
    return text

def _parse_optional_float(value, field_name):
    parsed_value = _normalize_optional_text(value)
    if parsed_value is None:
        return None
    try:
        return float(parsed_value)
    except ValueError:
        raise ValueError(f"{field_name} must be a valid number.")

@app.route('/admin/db/users/<string:user_id>', methods=['PUT', 'DELETE'])
@admin_required
def admin_db_user_actions(user_id):
    conn = get_db_connection()
    existing_user = conn.execute(
        'SELECT user_id, created_at, updated_at FROM users WHERE user_id = ?',
        (user_id,)
    ).fetchone()
    if not existing_user:
        conn.close()
        return jsonify({'status': 'error', 'message': f'User {user_id} not found.'}), 404

    if request.method == 'DELETE':
        try:
            conn.execute('BEGIN')
            conn.execute('UPDATE auth_logs SET user_id = NULL WHERE user_id = ?', (user_id,))
            conn.execute('DELETE FROM users WHERE user_id = ?', (user_id,))
            conn.commit()
        except Exception as e:
            conn.rollback()
            conn.close()
            return jsonify({'status': 'error', 'message': str(e)}), 400
        finally:
            conn.close()

        qr_file_name = ''.join(ch for ch in user_id if ch.isalnum() or ch in ('-', '_')) or 'user_id'
        qr_path = os.path.join(QR_OUTPUT_DIR, f'{qr_file_name}.png')
        if os.path.exists(qr_path):
            try:
                os.remove(qr_path)
            except Exception:
                pass

        load_user_data()
        return jsonify({'status': 'success', 'message': f'User {user_id} deleted successfully.'})

    payload = request.get_json(silent=True) or {}
    new_user_id = (payload.get('user_id') or '').strip()
    user_name = (payload.get('user_name') or '').strip()
    email = (payload.get('email') or '').strip().lower()
    face_id_raw = payload.get('face_id')
    created_at = _normalize_optional_text(payload.get('created_at')) or existing_user['created_at']
    updated_at = _normalize_optional_text(payload.get('updated_at')) or utc_now_iso()

    if not new_user_id or not user_name:
        conn.close()
        return jsonify({'status': 'error', 'message': 'User ID and User Name are required.'}), 400

    if '.' in user_name:
        conn.close()
        return jsonify({'status': 'error', 'message': 'User Name cannot contain periods.'}), 400

    if email and not is_valid_email(email):
        conn.close()
        return jsonify({'status': 'error', 'message': 'Invalid email format.'}), 400

    try:
        face_id = int(str(face_id_raw).strip())
    except Exception:
        conn.close()
        return jsonify({'status': 'error', 'message': 'Face ID must be an integer.'}), 400

    user_conflict = conn.execute('SELECT user_id FROM users WHERE user_id = ? AND user_id != ?', (new_user_id, user_id)).fetchone()
    if user_conflict:
        conn.close()
        return jsonify({'status': 'error', 'message': f'User ID {new_user_id} already exists.'}), 400

    face_conflict = conn.execute('SELECT user_id FROM users WHERE face_id = ? AND user_id != ?', (face_id, user_id)).fetchone()
    if face_conflict:
        conn.close()
        return jsonify({'status': 'error', 'message': f'Face ID {face_id} is already assigned.'}), 400

    now = utc_now_iso()
    try:
        conn.execute('BEGIN')
        conn.execute(
            '''
            UPDATE users
            SET user_id = ?, user_name = ?, email = ?, face_id = ?, created_at = ?, updated_at = ?
            WHERE user_id = ?
            ''',
            (new_user_id, user_name, email if email else None, face_id, created_at, updated_at, user_id)
        )

        if new_user_id != user_id:
            conn.execute('UPDATE auth_logs SET user_id = ?, user_name = ? WHERE user_id = ?', (new_user_id, user_name, user_id))
        else:
            conn.execute('UPDATE auth_logs SET user_name = ? WHERE user_id = ?', (user_name, new_user_id))

        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        return jsonify({'status': 'error', 'message': str(e)}), 400
    finally:
        conn.close()

    if new_user_id != user_id:
        old_qr_file = ''.join(ch for ch in user_id if ch.isalnum() or ch in ('-', '_')) or 'user_id'
        new_qr_file = ''.join(ch for ch in new_user_id if ch.isalnum() or ch in ('-', '_')) or 'user_id'
        old_qr_path = os.path.join(QR_OUTPUT_DIR, f'{old_qr_file}.png')
        new_qr_path = os.path.join(QR_OUTPUT_DIR, f'{new_qr_file}.png')
        if os.path.exists(old_qr_path) and old_qr_path != new_qr_path:
            try:
                os.replace(old_qr_path, new_qr_path)
            except Exception:
                pass

    load_user_data()
    return jsonify({'status': 'success', 'message': f'User {new_user_id} updated successfully.'})

@app.route('/admin/db/auth-logs/<int:log_id>', methods=['PUT', 'DELETE'])
@admin_required
def admin_db_auth_log_actions(log_id):
    conn = get_db_connection()
    existing_log = conn.execute('SELECT id FROM auth_logs WHERE id = ?', (log_id,)).fetchone()
    if not existing_log:
        conn.close()
        return jsonify({'status': 'error', 'message': f'Log ID {log_id} not found.'}), 404

    if request.method == 'DELETE':
        conn.execute('DELETE FROM auth_logs WHERE id = ?', (log_id,))
        conn.commit()
        conn.close()
        return jsonify({'status': 'success', 'message': f'Log ID {log_id} deleted successfully.'})

    payload = request.get_json(silent=True) or {}

    try:
        user_id = _normalize_optional_text(payload.get('user_id'))
        user_name = _normalize_optional_text(payload.get('user_name'))
        login_method = _normalize_optional_text(payload.get('login_method'))
        status = _normalize_optional_text(payload.get('status'))
        authenticated_at = _normalize_optional_text(payload.get('authenticated_at'))
        logged_in_at = _normalize_optional_text(payload.get('logged_in_at'))
        auth_latitude = _parse_optional_float(payload.get('auth_latitude'), 'Latitude')
        auth_longitude = _parse_optional_float(payload.get('auth_longitude'), 'Longitude')
        auth_distance_meters = _parse_optional_float(payload.get('auth_distance_meters'), 'Distance')
        auth_location_text = _normalize_optional_text(payload.get('auth_location_text'))
        ip_address = _normalize_optional_text(payload.get('ip_address'))
        user_agent = _normalize_optional_text(payload.get('user_agent'))
        notes = _normalize_optional_text(payload.get('notes'))
        created_at = _normalize_optional_text(payload.get('created_at'))
    except ValueError as e:
        conn.close()
        return jsonify({'status': 'error', 'message': str(e)}), 400

    if not login_method or not status:
        conn.close()
        return jsonify({'status': 'error', 'message': 'Method and Status are required.'}), 400

    conn.execute(
        '''
        UPDATE auth_logs
        SET user_id = ?,
            user_name = ?,
            login_method = ?,
            status = ?,
            authenticated_at = ?,
            logged_in_at = ?,
            auth_latitude = ?,
            auth_longitude = ?,
            auth_distance_meters = ?,
            auth_location_text = ?,
            ip_address = ?,
            user_agent = ?,
            notes = ?,
            created_at = ?
        WHERE id = ?
        ''',
        (
            user_id,
            user_name,
            login_method,
            status,
            authenticated_at,
            logged_in_at,
            auth_latitude,
            auth_longitude,
            auth_distance_meters,
            auth_location_text,
            ip_address,
            user_agent,
            notes,
            created_at,
            log_id
        )
    )
    conn.commit()
    conn.close()
    return jsonify({'status': 'success', 'message': f'Log ID {log_id} updated successfully.'})

@app.route('/admin/db/users')
@admin_required
def admin_db_users():
    conn = get_db_connection()
    rows = conn.execute(
        '''
        SELECT user_id, user_name, email, face_id, created_at, updated_at
        FROM users
        ORDER BY created_at DESC
        '''
    ).fetchall()
    conn.close()
    return jsonify({'count': len(rows), 'users': [dict(row) for row in rows]})

@app.route('/admin/db/auth-logs')
@admin_required
def admin_db_auth_logs():
    try:
        limit = max(1, min(int(request.args.get('limit', 100)), 1000))
    except ValueError:
        limit = 100

    conn = get_db_connection()
    rows = conn.execute(
        '''
        SELECT id, user_id, user_name, login_method, status,
               authenticated_at, logged_in_at,
               auth_latitude, auth_longitude, auth_distance_meters, auth_location_text,
               ip_address, user_agent, notes, created_at
        FROM auth_logs
        ORDER BY id DESC
        LIMIT ?
        ''',
        (limit,)
    ).fetchall()
    conn.close()
    return jsonify({'count': len(rows), 'limit': limit, 'logs': [dict(row) for row in rows]})

@app.route('/admin/resend-qr/<string:user_id>', methods=['POST'])
@admin_required
def admin_resend_qr(user_id):
    conn = get_db_connection()
    user = conn.execute(
        '''
        SELECT user_id, user_name, email
        FROM users
        WHERE user_id = ?
        ''',
        (user_id,)
    ).fetchone()
    conn.close()

    if not user:
        flash(f"User ID {user_id} was not found.", "error")
        return redirect(url_for('admin_db_view'))

    user_email = (user['email'] or '').strip()
    if not user_email:
        flash(f"User {user['user_name']} does not have an email address saved.", "warning")
        return redirect(url_for('admin_db_view'))

    if not is_valid_email(user_email):
        flash(f"Saved email for user {user['user_name']} is invalid.", "error")
        return redirect(url_for('admin_db_view'))

    qr_path = os.path.join(QR_OUTPUT_DIR, f"{''.join(ch for ch in user_id if ch.isalnum() or ch in ('-', '_')) or 'user_id'}.png")
    if not os.path.exists(qr_path):
        qr_path = generate_qr_png_for_user_id(user_id)

    sent, email_error = send_qr_email_to_user(user_email, user['user_name'], user_id, qr_path)
    if sent:
        flash(f"QR code was emailed to {user_email}.", "success")
    else:
        flash(f"Could not send email to {user_email}. Error: {email_error}", "error")

    return redirect(url_for('admin_db_view'))

if __name__ == "__main__":
    app.run(debug=True, host='0.0.0.0', port=5000)