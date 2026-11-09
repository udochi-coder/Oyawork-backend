import re
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from core.models import User

LIVE = dict(DEMO_OTP=False, TERMII_API_KEY="", BREVO_API_KEY="xkeysib-test", EMAIL_FROM_ADDRESS="hello@oyawork.ng")


def code_in(mock_send):
    return re.search(r"\b(\d{6})\b", mock_send.call_args[0][2]).group(1)


@override_settings(**LIVE)
class EmailCodeTest(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        r = self.client.post("/api/worker_register/", {"full_name": "Mary Johnson", "phone_number": "08023456789",
                                                       "password": "Str0ng-Passw0rd!"}, format="json")
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {r.data['access']}")

    def test_verify_email_then_reset_by_email(self):
        me = self.client.get("/api/me/").data
        self.assertEqual(me["verification"]["channels"], {"sms": False, "email": True})
        with mock.patch("accounts.views.send_email", return_value=True) as send:
            r = self.client.post("/api/me/verification/email/send/", {"email": "Mary@Example.com"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertNotIn("demo_code", r.data)
        self.assertEqual(send.call_args[0][0], "mary@example.com")
        self.assertEqual(self.client.post("/api/me/verification/email/verify/", {"code": "x"}, format="json").status_code, 400)
        r = self.client.post("/api/me/verification/email/verify/", {"code": code_in(send)}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual((r.data["email"], r.data["email_verified"]), ("mary@example.com", True))
        self.assertEqual(r.data["verification"]["email"], "verified")

        anon = APIClient()
        with mock.patch("accounts.views.send_email", return_value=True) as send:
            r = anon.post("/api/auth/password-reset/", {"identifier": "MARY@example.com"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("demo_code", r.data)
        r = anon.post("/api/auth/password-reset/confirm/", {"identifier": "mary@example.com", "code": code_in(send),
                                                            "new_password": "New-Passw0rd-2026"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(anon.post("/api/auth/login/", {"phone_number": "08023456789", "password": "New-Passw0rd-2026"},
                                   format="json").status_code, 200)

    def test_reset_by_phone_falls_back_to_email(self):
        User.objects.filter(phone_number="08023456789").update(email="mary@example.com")
        with mock.patch("accounts.views.send_email", return_value=True) as send, mock.patch("accounts.views.send_sms") as sms:
            r = APIClient().post("/api/auth/password-reset/", {"phone_number": "0802 345 6789"}, format="json")
        sms.assert_not_called()  # no Termii key
        self.assertEqual(send.call_args[0][0], "mary@example.com")
        self.assertEqual(r.status_code, 200)

    def test_unknown_account_and_no_email_answer_the_same(self):
        with mock.patch("accounts.views.send_email", return_value=True) as send:
            a = APIClient().post("/api/auth/password-reset/", {"identifier": "nobody@example.com"}, format="json")
            b = APIClient().post("/api/auth/password-reset/", {"identifier": "08023456789"}, format="json")  # has no email
        send.assert_not_called()
        self.assertEqual(a.data, b.data)

    def test_email_taken_by_someone_else(self):
        User.objects.create_user("08011112222", email="taken@example.com", password="x", full_name="Other")
        r = self.client.post("/api/me/verification/email/send/", {"email": "Taken@example.com"}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_changing_email_clears_verified(self):
        User.objects.filter(phone_number="08023456789").update(email="mary@example.com", email_verified=True)
        r = self.client.patch("/api/me/", {"email": "new@example.com"}, format="json")
        self.assertFalse(r.data["email_verified"])


class BrevoRequestTest(TestCase):
    @override_settings(BREVO_API_KEY="xkeysib-test", EMAIL_FROM_ADDRESS="hello@oyawork.ng", EMAIL_FROM_NAME="OyaWork")
    def test_payload(self):
        from core.utils import send_email

        with mock.patch("requests.post") as post:
            post.return_value.status_code = 201
            self.assertTrue(send_email("a@b.com", "Hi", "Line one\n\nLine <two>"))
        url, kwargs = post.call_args[0][0], post.call_args[1]
        self.assertEqual(url, "https://api.brevo.com/v3/smtp/email")
        self.assertEqual(kwargs["headers"]["api-key"], "xkeysib-test")
        self.assertEqual(kwargs["json"]["sender"], {"name": "OyaWork", "email": "hello@oyawork.ng"})
        self.assertIn("&lt;two&gt;", kwargs["json"]["htmlContent"])

    @override_settings(BREVO_API_KEY="")
    def test_not_configured(self):
        from core.utils import send_email

        with mock.patch("requests.post") as post:
            self.assertFalse(send_email("a@b.com", "Hi", "x"))
        post.assert_not_called()
