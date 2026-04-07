# Multi-Factor Authentication System with Face Recognition, QR Code Scanning, and Geolocation Verification

A web-based entry portal that combines **QR/Barcode scanning**, **facial recognition with liveness detection**, and **GPS-based geolocation verification** for secure multi-factor authentication.

## Features

- **QR Code Authentication** — Scan a QR code linked to a registered user ID
- **Face Recognition** — LBPH-based facial recognition with blink-based liveness detection
- **Geolocation Verification** — GPS distance check to ensure the user is within a permitted radius
- **Tailgating Detection** — Uses MobileNet SSD to detect multiple persons at entry
- **Admin Panel** — Add users, capture face data, train the recognizer, and view auth logs
- **Email Notifications** — Auto-sends QR codes to registered users via SMTP
- **SQLite Database** — Stores users and authentication logs

## Tech Stack

- **Backend:** Python, Flask
- **Frontend:** HTML, CSS, JavaScript (Jinja2 templates)
- **Face Detection:** OpenCV (Haar Cascades, LBPH Face Recognizer)
- **Object Detection:** MobileNet SSD (Caffe model)
- **QR Decoding:** pyzbar / OpenCV QR fallback
- **Database:** SQLite

## Prerequisites

- **Python 3.8+** installed on your system
- **pip** (Python package manager)
- A webcam (for face capture and recognition)
- (Optional) [Graphviz](https://graphviz.org/download/) if you want to generate the user flow diagram

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/Venu-2004/Web-Based-Barcode-and-Facial-Recognition-Entry-Portal1.git
cd Web-Based-Barcode-and-Facial-Recognition-Entry-Portal1
```

### 2. Install Python dependencies

```bash
pip install flask opencv-contrib-python numpy Pillow qrcode pyzbar python-dotenv
```

> **Note (Windows):** `pyzbar` requires the [Visual C++ Redistributable](https://aka.ms/vs/17/release/vc_redist.x64.exe). If `pyzbar` fails to import, the app will automatically fall back to OpenCV's built-in QR decoder.

### 3. Configure email (optional)

To enable QR code email delivery:

1. Copy `.env.example` to `.env`
2. Update the SMTP credentials in `.env`:

```
MFA_SENDER_EMAIL=your_email@gmail.com
MFA_SMTP_USER=your_email@gmail.com
MFA_SMTP_PASSWORD=your_app_password_here
```

> For Gmail, generate an [App Password](https://myaccount.google.com/apppasswords) (requires 2FA enabled).

## Running the Application

```bash
python app.py
```

The app will start on **http://127.0.0.1:5000**. Open this URL in your browser.

## Usage

### Authentication Paths

| Path | Description |
|------|-------------|
| **QR Code Scan** | Scan your registered QR code, then pass geolocation verification |
| **Face Recognition** | Complete blink-based liveness detection, then face match (>75% confidence) |
| **Admin Portal** | Login to add users, capture face samples (50 frames), and train the model |

### Admin Workflow

1. Go to the **Admin Portal** and log in
2. **Add a new user** — Enter user ID and name
3. **Capture face** — The system captures 50 face frames via webcam
4. **Train model** — Trains the LBPH recognizer on all captured faces
5. A QR code is generated and saved in the `QR_generated/` folder

### API Endpoints (after admin login)

- `GET /admin/db/users` — View all registered users (JSON)
- `GET /admin/db/auth-logs?limit=100` — View authentication logs (JSON)

## Project Structure

```
├── app.py                  # Main Flask application
├── main_code.py            # Standalone Tkinter-based face recognition app
├── image.py                # User flow diagram generator (Graphviz)
├── templates/              # HTML templates (Jinja2)
│   ├── index.html          # Landing page
│   ├── face_auth.html      # Face recognition + liveness
│   ├── capture.html        # Face data capture
│   ├── train.html          # Model training
│   ├── dashboard.html      # User dashboard
│   ├── admin_login.html    # Admin login
│   ├── add_user.html       # Add new user form
│   ├── admin_db.html       # Admin database viewer
│   ├── location_check.html # Geolocation verification
│   ├── entry_monitoring.html # Entry monitoring with tailgating detection
│   └── layout.html         # Base template
├── data/                   # Pre-trained models and cascades
│   ├── haarcascade_frontalface_default.xml
│   ├── haarcascade_eye.xml
│   ├── MobileNetSSD_deploy.caffemodel
│   └── MobileNetSSD_deploy.prototxt.txt
├── dataset/                # Captured face images
├── trainer/                # Trained LBPH model (trainer.yml)
├── QR_generated/           # Generated QR code images
├── users.csv               # User registry
├── .env.example            # SMTP config template
└── README.md
```

## Configuration

You can adjust these values in `app.py`:

| Variable | Default | Description |
|----------|---------|-------------|
| `OFFICE_COORDINATES` | `(9.7196, 77.56310)` | GPS coordinates of the authorized location |
| `MAX_DISTANCE_METERS` | `100000` | Maximum allowed distance from office (meters) |
| `OFFICE_NAME` | `Kalasalingam Academy` | Name displayed for the authorized location |

## License

This project is for educational purposes.
