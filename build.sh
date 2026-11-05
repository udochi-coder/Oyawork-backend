#!/usr/bin/env bash
# Render build command for the OyaWork API.
set -o errexit

pip install --upgrade pip
pip install -r requirements.txt
python manage.py collectstatic --no-input
python manage.py migrate --no-input

# Optional admin account (Render's free plan has no shell for createsuperuser).
# Set ADMIN_PHONE_NUMBER + ADMIN_PASSWORD, deploy once, then you can remove them.
python manage.py ensure_admin
