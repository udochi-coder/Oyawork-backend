from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.identity import names_agree
from accounts.models import VerificationDocument, Worker
from tests.test_flow import image_file

DOJAH = dict(DOJAH_APP_ID="app", DOJAH_SECRET_KEY="secret", DOJAH_BASE_URL="https://sandbox.dojah.io", DOJAH_SELFIE_THRESHOLD=90)


def dojah_reply(status=200, match=True, first="MARY", last="JOHNSON", score=97.5):
    if status != 200:
        return status, {"error": "NIN not found"}
    return 200, {"entity": {"nin": "1*********1", "firstname": first, "surname": last, "photo": "BASE64PHOTO",
                            "selfie_verification": {"confidence_value": score, "match": match}}}


@override_settings(AUTO_APPROVE_VERIFICATION=True, **DOJAH)
class DojahIdentityTest(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        r = self.client.post("/api/worker_register/", {"full_name": "Mary Johnson", "phone_number": "08023456789",
                                                       "password": "Str0ng-Passw0rd!"}, format="json")
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {r.data['access']}")

    def submit(self, reply):
        with mock.patch("accounts.identity.call_dojah", return_value=reply) as call:
            self.client.post("/api/me/verification/", {"doc_type": "nin", "number": "12345678901"}, format="json")
            self.client.post("/api/me/verification/", {"doc_type": "utility_bill", "file": image_file("bill.png")}, format="multipart")
            r = self.client.post("/api/me/verification/", {"doc_type": "selfie", "file": image_file("me.png")}, format="multipart")
        return r.data["me"]["verification"], call

    def worker_status(self):
        return Worker.objects.get(user__phone_number="08023456789").status

    def test_match_verifies_and_activates(self):
        v, call = self.submit(dojah_reply())
        self.assertEqual(call.call_count, 1)
        self.assertEqual(call.call_args[0][0], "12345678901")
        self.assertEqual((v["identity"], v["face"], v["address"]), ("verified", "verified", "verified"))
        self.assertTrue(v["face_required"])
        self.assertEqual(self.worker_status(), "active")
        stored = VerificationDocument.objects.get(doc_type="nin").provider_result
        self.assertNotIn("photo", stored["entity"])  # the NIN photo is never stored

    def test_selfie_needed_before_activation(self):
        with mock.patch("accounts.identity.call_dojah") as call:
            self.client.post("/api/me/verification/", {"doc_type": "nin", "number": "12345678901"}, format="json")
            r = self.client.post("/api/me/verification/", {"doc_type": "utility_bill", "file": image_file("bill.png")}, format="multipart")
        call.assert_not_called()
        self.assertFalse(r.data["me"]["verification"]["complete"])
        self.assertEqual(self.worker_status(), "pending")

    def test_face_mismatch_rejects_selfie(self):
        v, _ = self.submit(dojah_reply(match=False, score=41))
        self.assertEqual(v["face"], "missing")
        self.assertIn("didn't match", v["notes"]["face"])
        self.assertEqual(self.worker_status(), "pending")
        # Retaking the selfie runs the check again.
        with mock.patch("accounts.identity.call_dojah", return_value=dojah_reply()):
            r = self.client.post("/api/me/verification/", {"doc_type": "selfie", "file": image_file("me2.png")}, format="multipart")
        self.assertEqual(r.data["me"]["verification"]["face"], "verified")
        self.assertEqual(self.worker_status(), "active")

    def test_unknown_nin_rejected(self):
        v, _ = self.submit(dojah_reply(status=404))
        self.assertEqual(v["identity"], "missing")
        self.assertIn("couldn't find this NIN", v["notes"]["identity"])

    def test_name_mismatch_waits_for_admin(self):
        v, _ = self.submit(dojah_reply(first="CHIDI", last="OKAFOR"))
        self.assertEqual(v["identity"], "submitted")
        self.assertEqual(self.worker_status(), "pending")
        self.assertIn("Chidi Okafor", VerificationDocument.objects.get(doc_type="nin").note)  # admin sees the name
        self.assertNotIn("Chidi", str(v["notes"]))  # the person doesn't
        self.assertIn("checking this by hand", v["notes"]["identity"])

    def test_dojah_down_waits_for_admin(self):
        v, _ = self.submit((None, {}))
        self.assertEqual((v["identity"], v["face"]), ("submitted", "submitted"))
        self.assertEqual(self.worker_status(), "pending")

    def test_names_agree(self):
        self.assertTrue(names_agree("Mary Johnson", {"firstname": "JOHNSON", "middlename": "ADA", "surname": "MARY"}))
        self.assertTrue(names_agree("Mary Ada Johnson", {"firstname": "MARY", "surname": "JOHNSON"}))
        self.assertFalse(names_agree("Mary Johnson", {"firstname": "MARY", "surname": "OKAFOR"}))


@override_settings(AUTO_APPROVE_VERIFICATION=True, DOJAH_APP_ID="", DOJAH_SECRET_KEY="")
class WithoutDojahTest(TestCase):
    def test_selfie_optional_and_never_called(self):
        cache.clear()
        c = APIClient()
        r = c.post("/api/worker_register/", {"full_name": "Ade Bello", "phone_number": "08011112222", "password": "Str0ng-Passw0rd!"}, format="json")
        c.credentials(HTTP_AUTHORIZATION=f"Bearer {r.data['access']}")
        with mock.patch("accounts.identity.call_dojah") as call:
            c.post("/api/me/verification/", {"doc_type": "nin", "number": "12345678901"}, format="json")
            r = c.post("/api/me/verification/", {"doc_type": "utility_bill", "file": image_file("bill.png")}, format="multipart")
        call.assert_not_called()
        self.assertTrue(r.data["me"]["verification"]["complete"])
        self.assertFalse(r.data["me"]["verification"]["face_required"])
