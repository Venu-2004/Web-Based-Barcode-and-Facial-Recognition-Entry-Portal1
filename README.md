# Multi-Factor Authentication System with Face Recognition, QR Code Scanning, and Geolocation Verification

## Database (SQLite)

The project now uses a SQLite database file named `mfa_auth.db` in the project root.

### `users` table
- `user_id` (TEXT, primary key) — e.g., `99220040128`
- `user_name` (TEXT)
- `face_id` (INTEGER, unique)
- `created_at` (TEXT, UTC ISO timestamp)
- `updated_at` (TEXT, UTC ISO timestamp)

### `auth_logs` table
- `id` (INTEGER, primary key, autoincrement)
- `user_id` (TEXT)
- `user_name` (TEXT)
- `login_method` (TEXT) — `qr_code`, `face_liveness`, `post_auth_location_check`
- `status` (TEXT)
- `authenticated_at` (TEXT, UTC ISO timestamp)
- `logged_in_at` (TEXT, UTC ISO timestamp)
- `auth_latitude` (REAL)
- `auth_longitude` (REAL)
- `auth_distance_meters` (REAL)
- `auth_location_text` (TEXT)
- `ip_address` (TEXT)
- `user_agent` (TEXT)
- `notes` (TEXT)
- `created_at` (TEXT, UTC ISO timestamp)

## Admin APIs to View Database Data

After admin login:
- `GET /admin/db/users`
- `GET /admin/db/auth-logs?limit=100`

These APIs return JSON so you can directly inspect stored users and authentication history.

## SMTP setup for QR e-mail

If you see message `QR code generated, but email could not be sent`, configure SMTP settings.

1. Copy `.env.example` to `.env`
2. Fill your real e-mail credentials
3. Restart the app

Required values:
- `MFA_SENDER_EMAIL`
- `MFA_SMTP_USER`
- `MFA_SMTP_PASSWORD`

Optional values:
- `MFA_SMTP_HOST` (auto-detected for gmail/outlook/yahoo/icloud if empty)
- `MFA_SMTP_PORT` (default `587`)
- `MFA_SMTP_USE_TLS` (default `true`)
