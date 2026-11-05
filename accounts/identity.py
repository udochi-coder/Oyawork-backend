"""Automatic NIN + selfie checks through Dojah (https://docs.dojah.io).

When DOJAH_APP_ID and DOJAH_SECRET_KEY are set, a person's NIN and selfie are sent
to Dojah as soon as both are uploaded. Dojah looks the NIN up in the NIMC database
and compares the selfie with the NIN photo:

* match + name agrees  -> NIN and selfie are approved automatically
* NIN not found        -> NIN rejected, the person is asked to re-enter it
* face doesn't match   -> selfie rejected, the person is asked to retake it
* names differ, or Dojah is unreachable -> left "pending" for an admin to review

Address documents are not covered by Dojah; they follow AUTO_APPROVE_VERIFICATION.
"""
import base64
import re

from django.conf import settings
from django.utils import timezone
from loguru import logger

# Never keep these in our database: they are the person's NIN photo/signature.
_DROP_KEYS = {"photo", "image", "signature", "selfie_image"}


def enabled():
    return bool(settings.DOJAH_APP_ID and settings.DOJAH_SECRET_KEY)


def _names(text):
    return {part for part in re.split(r"[^a-z]+", (text or "").lower()) if len(part) > 1}


def names_agree(full_name, entity):
    """True when at least two of the account's names appear on the NIN record
    (or one, if the account only has one name). Order and middle names don't matter."""
    ours = _names(full_name)
    theirs = _names(" ".join(str(entity.get(k) or "") for k in ("firstname", "first_name", "middlename", "middle_name", "surname", "last_name")))
    if not ours or not theirs:
        return False
    return len(ours & theirs) >= min(2, len(ours))


def _clean(data):
    if isinstance(data, dict):
        return {k: _clean(v) for k, v in data.items() if k not in _DROP_KEYS}
    if isinstance(data, list):
        return [_clean(v) for v in data]
    return data


def _selfie_base64(doc):
    doc.file.open("rb")
    try:
        return base64.b64encode(doc.file.read()).decode()
    finally:
        doc.file.close()


def call_dojah(nin, selfie_b64):
    """Returns (status_code, json). status_code is None when Dojah couldn't be reached."""
    import requests

    try:
        resp = requests.post(
            f"{settings.DOJAH_BASE_URL.rstrip('/')}/api/v1/kyc/nin/verify",
            headers={"AppId": settings.DOJAH_APP_ID, "Authorization": settings.DOJAH_SECRET_KEY},
            json={"nin": nin, "selfie_image": selfie_b64, "threshold": settings.DOJAH_SELFIE_THRESHOLD},
            timeout=30,
        )
    except Exception as exc:
        logger.error(f"Dojah request failed: {exc}")
        return None, {}
    try:
        body = resp.json()
    except ValueError:
        body = {}
    return resp.status_code, body


def _set(doc, status, note, result=None):
    doc.status = status
    doc.note = note[:255]
    doc.reviewed_at = timezone.now() if status != "pending" else None
    if result is not None:
        doc.provider_result = result
    doc.save(update_fields=["status", "note", "reviewed_at", "provider_result"])


def check_identity(user, force=False):
    """Run the Dojah check if the person has a pending NIN and a pending (or approved) selfie.
    Returns a short outcome string, or None when there was nothing to check."""
    if not enabled():
        return None
    docs = user.verification_documents
    nin_doc = docs.filter(doc_type="nin").exclude(status="rejected").first()
    selfie = docs.filter(doc_type="selfie").exclude(status="rejected").first()
    if not nin_doc or not selfie or not selfie.file:
        return None
    if not force and nin_doc.status == "approved" and selfie.status == "approved":
        return None
    if not force and nin_doc.provider_result and selfie.provider_result and nin_doc.status == "pending":
        # Already checked and waiting for an admin (e.g. names differ); don't pay for it twice.
        return None

    try:
        selfie_b64 = _selfie_base64(selfie)
    except Exception as exc:
        logger.error(f"Couldn't read selfie {selfie.pk} for Dojah: {exc}")
        return "error"

    code, body = call_dojah(nin_doc.number, selfie_b64)
    result = _clean(body)
    entity = body.get("entity") or {}

    if code is None or code >= 500 or code in (401, 403, 429):
        note = "Automatic check unavailable; waiting for manual review."
        _set(nin_doc, "pending", note, result or {"http_status": code})
        _set(selfie, "pending", note, result or {"http_status": code})
        return "unavailable"

    if code != 200 or not entity:
        message = str(body.get("error") or "")
        _set(nin_doc, "rejected", "We couldn't find this NIN. Check the 11 digits and try again.", {"http_status": code, "error": message})
        return "nin_not_found"

    match = (entity.get("selfie_verification") or {}).get("match")
    confidence = (entity.get("selfie_verification") or {}).get("confidence_value")
    if not match:
        _set(selfie, "rejected", "Your selfie didn't match your NIN photo. Retake it in good light, facing the camera.", result)
        nin_doc.note = f"NIN found; selfie didn't match (score {confidence})."[:255]
        nin_doc.save(update_fields=["note"])
        return "face_mismatch"

    if not names_agree(user.full_name, entity):
        on_record = " ".join(str(entity.get(k) or "") for k in ("firstname", "middlename", "surname")).split()
        note = f"Face matched, but the NIN is registered to {' '.join(on_record).title()}. Needs manual review."
        _set(nin_doc, "pending", note, result)
        _set(selfie, "pending", note, result)
        return "name_mismatch"

    note = f"Verified by Dojah (face match {confidence})."
    _set(nin_doc, "approved", note, result)
    _set(selfie, "approved", note, result)
    return "verified"
