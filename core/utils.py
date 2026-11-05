import math
import re
from decimal import Decimal

from django.conf import settings
from loguru import logger


def normalize_phone(raw):
    """Turn '+234 802 345 6789', '2348023456789' or '8023456789' into '08023456789'."""
    digits = re.sub(r"\D", "", str(raw or ""))
    if digits.startswith("234") and len(digits) == 13:
        digits = "0" + digits[3:]
    elif len(digits) == 10 and not digits.startswith("0"):
        digits = "0" + digits
    return digits


def is_valid_nigerian_phone(phone):
    return bool(re.fullmatch(r"0[789][01]\d{8}", phone or ""))


def haversine_km(lat1, lng1, lat2, lng2):
    """Great-circle distance in km, or None if any coordinate is missing."""
    if None in (lat1, lng1, lat2, lng2):
        return None
    lat1, lng1, lat2, lng2 = (math.radians(float(v)) for v in (lat1, lng1, lat2, lng2))
    dlat, dlng = lat2 - lat1, lng2 - lng1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return round(6371 * 2 * math.asin(math.sqrt(a)), 2)


def eta_minutes(distance_km, speed_kmh=20):
    """Rough Lagos traffic ETA."""
    if distance_km is None:
        return None
    return max(1, round(distance_km / speed_kmh * 60))


def money(value):
    return Decimal(value).quantize(Decimal("0.01"))


def notify(user, title, body="", kind="system", **data):
    from core.models import Notification

    if user is None:
        return None
    try:
        return Notification.objects.create(user=user, title=title, body=body, kind=kind, data=data)
    except Exception as exc:  # notifications must never break the main action
        logger.exception(f"Could not create notification for user={user.pk}: {exc}")
        return None


def send_sms(phone, message):
    """Send an SMS through Termii when configured. Returns True when sent."""
    if not settings.TERMII_API_KEY:
        logger.info(f"[SMS not sent: TERMII_API_KEY missing] {phone}: {message}")
        return False
    import requests

    intl = "234" + phone[1:] if phone.startswith("0") else phone
    try:
        resp = requests.post(
            "https://api.ng.termii.com/api/sms/send",
            json={
                "to": intl,
                "from": settings.TERMII_SENDER_ID,
                "sms": message,
                "type": "plain",
                "channel": settings.TERMII_CHANNEL,
                "api_key": settings.TERMII_API_KEY,
            },
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except Exception as exc:
        logger.error(f"Termii SMS failed for {phone}: {exc}")
        return False
