import datetime
import io
from decimal import Decimal

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from PIL import Image
from rest_framework.test import APIClient

from accounts.models import Employer, Worker
from core.models import Notification, User
from payments.models import Wallet

LEKKI = (6.4474, 3.4720)
LEKKI_NEAR = (6.4550, 3.4800)
IKEJA = (6.6018, 3.3515)


def image_file(name="photo.png"):
    buf = io.BytesIO()
    Image.new("RGB", (10, 10), "green").save(buf, "PNG")
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")


@override_settings(AUTO_APPROVE_VERIFICATION=True, DEMO_PAYMENTS=True, PAYSTACK_SECRET_KEY="", DEMO_OTP=True)
class OyaWorkFlowTest(TestCase):
    def register(self, role, name, phone, password="Str0ng-Passw0rd!"):
        client = APIClient()
        resp = client.post(f"/api/{role}_register/", {"full_name": name, "phone_number": phone, "password": password,
                                                     "confirm_password": password}, format="json")
        self.assertEqual(resp.status_code, 201, resp.content)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {resp.data['access']}")
        return client, resp.data["user"]

    def verify(self, client):
        r = client.post("/api/me/verification/", {"doc_type": "nin", "number": "12345678901"}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        r = client.post("/api/me/verification/", {"doc_type": "utility_bill", "file": image_file("bill.png")}, format="multipart")
        self.assertEqual(r.status_code, 201, r.content)
        return r.data["me"]

    def setUp(self):
        cache.clear()  # reset sign-up throttling between tests
        self.emp, emp_user = self.register("employer", "David James", "+234 803 123 4567")
        self.assertEqual(emp_user["phone_number"], "08031234567")
        self.wrk, _ = self.register("worker", "Mary Johnson", "08023456789")
        self.wrk2, _ = self.register("worker", "Grace Alao", "08055555555")
        for c in (self.emp, self.wrk, self.wrk2):
            me = self.verify(c)
        self.assertTrue(me["verification"]["complete"])
        self.emp.post("/api/me/location/", {"latitude": LEKKI[0], "longitude": LEKKI[1], "location": "Lekki Phase 1, Lagos"}, format="json")
        self.wrk.post("/api/me/location/", {"latitude": LEKKI_NEAR[0], "longitude": LEKKI_NEAR[1]}, format="json")
        self.wrk2.post("/api/me/location/", {"latitude": LEKKI_NEAR[0], "longitude": LEKKI_NEAR[1]}, format="json")
        for c in (self.wrk, self.wrk2):
            r = c.patch("/api/me/worker/", {"skills": ["Cleaning", "Laundry"], "experience_level": "experienced",
                                            "availability_schedule": ["available_now", "weekends"], "preferred_radius": 10},
                        format="json")
            self.assertEqual(r.status_code, 200, r.content)

    def post_job(self, price="15000"):
        r = self.emp.post("/api/jobs/", {
            "category": "Cleaning", "required_skills": ["Cleaning"],
            "description": "Clean a 3-bedroom apartment including the kitchen and bathrooms.",
            "offered_price": price, "scheduled_date": (timezone.localdate() + datetime.timedelta(days=1)).isoformat(),
            "scheduled_time": "10:00",
        }, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        return r.data

    def test_login_with_any_phone_format(self):
        r = APIClient().post("/api/auth/login", {"phone_number": "+2348023456789", "password": "Str0ng-Passw0rd!"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.data["user"]["role"], "worker")
        self.assertIn("access", r.data)

    def test_duplicate_phone_rejected(self):
        r = APIClient().post("/api/worker_register/", {"full_name": "X", "phone_number": "0802 345 6789", "password": "Str0ng-Passw0rd!"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("phone_number", r.data)

    def test_pending_worker_sees_no_jobs(self):
        c, _ = self.register("worker", "New Person", "08099999999")
        self.post_job()
        self.assertEqual(c.get("/api/jobs/").data, [])

    def test_full_job_lifecycle(self):
        job = self.post_job()
        self.assertEqual(job["title"], "Cleaning Job")
        self.assertEqual(job["matched_workers"], 2)
        self.assertTrue(Notification.objects.filter(user__full_name="Mary Johnson", kind="job").exists())

        # Worker sees the nearby job with distance
        jobs = self.wrk.get("/api/jobs/").data
        self.assertEqual(len(jobs), 1)
        self.assertLess(jobs[0]["distance_km"], 3)

        # Mary counters at 18,000, Grace accepts the budget
        r = self.wrk.post(f"/api/jobs/{job['id']}/respond/", {"amount": "18000", "message": "Big apartment"}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        hr_id = r.data["id"]
        self.wrk2.post(f"/api/jobs/{job['id']}/respond/", {}, format="json")
        interested = self.emp.get(f"/api/jobs/{job['id']}/interested/").data
        self.assertEqual(len(interested), 2)

        # Worker can't accept their own offer; employer counters 16,000; worker accepts
        self.assertEqual(self.wrk.post(f"/api/hiring-requests/{hr_id}/accept/").status_code, 400)
        r = self.emp.post(f"/api/hiring-requests/{hr_id}/counter/", {"amount": "16000"}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(len(r.data["negotiations"]), 2)
        r = self.wrk.post(f"/api/hiring-requests/{hr_id}/accept/")
        self.assertEqual(r.status_code, 201, r.content)
        booking_id = r.data["id"]
        self.assertEqual(Decimal(r.data["agreed_price"]), Decimal("16000"))
        # Grace's request expired
        self.assertEqual(self.wrk2.get("/api/hiring-requests/").data[0]["status"], "expired")

        # Booking for someone else, then pay (demo)
        r = self.emp.post(f"/api/bookings/{booking_id}/details/", {
            "for_someone_else": True, "recipient_name": "Mama Grace", "recipient_relationship": "Mother",
            "recipient_phone": "08031234567", "service_address": "Lekki Phase 1"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(self.wrk.post(f"/api/bookings/{booking_id}/en-route/").status_code, 400)  # not paid yet
        r = self.emp.post(f"/api/bookings/{booking_id}/pay/", {"method": "bank_transfer"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.data["status"], "scheduled")
        self.assertEqual(r.data["escrow"]["status"], "held")

        # En route -> OyaCode visible to employer only
        r = self.wrk.post(f"/api/bookings/{booking_id}/en-route/")
        self.assertEqual(r.data["status"], "en_route")
        self.assertIsNone(r.data["oyacode"])
        code = self.emp.get(f"/api/bookings/{booking_id}/").data["oyacode"]
        self.assertEqual(len(code), 4)
        r = self.wrk.post(f"/api/bookings/{booking_id}/location/", {"latitude": 6.45, "longitude": 3.475}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertIsNotNone(r.data["eta_minutes"])
        self.wrk.post(f"/api/bookings/{booking_id}/arrive/")
        bad = "0000" if code != "0000" else "1111"
        self.assertEqual(self.wrk.post(f"/api/bookings/{booking_id}/verify-code/", {"code": bad}, format="json").status_code, 400)
        r = self.wrk.post(f"/api/bookings/{booking_id}/verify-code/", {"code": code}, format="json")
        self.assertEqual(r.data["status"], "in_progress")

        # Evidence + complete + confirm
        r = self.wrk.post(f"/api/bookings/{booking_id}/evidence/", {"kind": "after", "photo": image_file()}, format="multipart")
        self.assertEqual(r.status_code, 201, r.content)
        self.wrk.post(f"/api/bookings/{booking_id}/complete/")
        self.assertEqual(self.wrk.post(f"/api/bookings/{booking_id}/confirm/").status_code, 403)
        r = self.emp.post(f"/api/bookings/{booking_id}/confirm/")
        self.assertEqual(r.data["status"], "confirmed")
        wallet = self.wrk.get("/api/wallet/").data
        self.assertEqual(Decimal(wallet["balance"]), Decimal("16000"))
        self.assertEqual(wallet["transactions"][0]["kind"], "earning")

        # Mutual review
        r = self.emp.post("/api/reviews/", {"booking": booking_id, "rating": 5, "comment": "Spotless"}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        r = self.wrk.post("/api/reviews/", {"booking": booking_id, "rating": 4, "comment": "Polite"}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(self.emp.post("/api/reviews/", {"booking": booking_id, "rating": 5}, format="json").status_code, 400)
        worker = Worker.objects.get(user__full_name="Mary Johnson")
        self.assertEqual(worker.rating, Decimal("5.00"))
        self.assertEqual(worker.completed_jobs, 1)
        self.assertEqual(worker.reliability_score, Decimal("100.00"))
        self.assertEqual(Employer.objects.get().rating, Decimal("4.00"))

        # Withdraw
        r = self.wrk.post("/api/wallet/withdraw/", {"amount": "10000", "bank_name": "GTBank", "account_number": "0123456789"}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(Decimal(self.wrk.get("/api/wallet/").data["balance"]), Decimal("6000"))
        r = self.wrk.post("/api/wallet/withdraw/", {"amount": "10000", "bank_name": "GTBank", "account_number": "0123456789"}, format="json")
        self.assertEqual(r.status_code, 400)

    def _booked(self):
        job = self.post_job()
        wid = Worker.objects.get(user__full_name="Mary Johnson").pk
        r = self.emp.post("/api/hiring-requests/", {"job": job["id"], "worker": wid}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        r = self.wrk.post(f"/api/hiring-requests/{r.data['id']}/accept/")
        booking_id = r.data["id"]
        return booking_id

    def test_wallet_payment_and_cancel_refund(self):
        booking_id = self._booked()
        r = self.emp.post(f"/api/bookings/{booking_id}/pay/", {"method": "wallet"}, format="json")
        self.assertEqual(r.status_code, 400)  # empty wallet
        self.emp.post("/api/wallet/fund/", {"amount": "20000"}, format="json")
        r = self.emp.post(f"/api/bookings/{booking_id}/pay/", {"method": "wallet"}, format="json")
        self.assertEqual(r.data["status"], "scheduled")
        self.assertEqual(Wallet.objects.get(user__full_name="David James").balance, Decimal("5000"))
        r = self.wrk.post(f"/api/bookings/{booking_id}/cancel/", {"reason": "Sick"}, format="json")
        self.assertEqual(r.data["status"], "cancelled")
        self.assertEqual(Wallet.objects.get(user__full_name="David James").balance, Decimal("20000"))
        self.assertEqual(r.data["job"]["status"], "open")

    def test_dispute_freezes_and_admin_refund(self):
        from reports.models import Dispute
        from reports.services import resolve_dispute

        booking_id = self._booked()
        self.emp.post(f"/api/bookings/{booking_id}/pay/", {"method": "card"}, format="json")
        r = self.emp.post("/api/disputes/", {"booking": booking_id, "reason": "job_not_completed", "details": "Left early",
                                             "files": [image_file()]}, format="multipart")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.data["status"], "payment_held")
        self.assertEqual(len(r.data["timeline"]), 5)
        self.assertEqual(self.emp.get(f"/api/bookings/{booking_id}/").data["escrow"]["status"], "frozen")
        resolve_dispute(Dispute.objects.get(), "refund_employer")
        self.assertEqual(Wallet.objects.get(user__full_name="David James").balance, Decimal("15000"))

    def test_swipe_search_and_profiles(self):
        workers = self.emp.get("/api/workers/?category=Cleaning").data
        self.assertEqual(len(workers), 2)
        self.assertIsNotNone(workers[0]["distance_km"])
        far = self.emp.get(f"/api/workers/?lat={IKEJA[0]}&lng={IKEJA[1]}&radius=5").data
        self.assertEqual(far, [])
        self.assertEqual(self.wrk.get("/api/workers/").status_code, 403)
        r = self.wrk.get(f"/api/employers/{Employer.objects.get().pk}/")
        self.assertEqual(r.status_code, 200)

    def test_switch_mode_and_chat(self):
        r = self.wrk.post("/api/me/switch-mode/", {"role": "employer"}, format="json")
        self.assertEqual(r.data["role"], "employer")
        self.assertEqual(r.data["employer_profile"]["status"], "active")
        self.wrk.post("/api/me/switch-mode/", {"role": "worker"}, format="json")
        emp_user = User.objects.get(full_name="David James")
        r = self.wrk.post("/api/conversations/", {"user_id": emp_user.pk}, format="json")
        convo = r.data["id"]
        self.wrk.post(f"/api/conversations/{convo}/messages/", {"body": "Yes, I am available."}, format="json")
        r = self.emp.get("/api/conversations/")
        self.assertEqual(r.data[0]["unread"], 1)
        msgs = self.emp.get(f"/api/conversations/{convo}/messages/").data
        self.assertFalse(msgs[0]["is_mine"])
        self.assertEqual(self.emp.get("/api/conversations/").data[0]["unread"], 0)

    def test_phone_otp(self):
        r = self.wrk.post("/api/me/verification/phone/send/")
        self.assertIn("demo_code", r.data)
        self.assertEqual(self.wrk.post("/api/me/verification/phone/verify/", {"code": "x"}, format="json").status_code, 400)
        r = self.wrk.post("/api/me/verification/phone/verify/", {"code": r.data["demo_code"]}, format="json")
        self.assertTrue(r.data["phone_verified"])

    def test_notifications_and_health(self):
        self.post_job()
        r = self.wrk.get("/api/notifications/")
        self.assertGreaterEqual(r.data["unread"], 1)
        self.wrk.post("/api/notifications/mark-all-read/")
        self.assertEqual(self.wrk.get("/api/notifications/").data["unread"], 0)
        self.assertEqual(APIClient().get("/api/health/").status_code, 200)
        self.assertEqual(APIClient().get("/api/categories/").status_code, 200)  # public, used during sign-up
        self.assertEqual(APIClient().get("/api/jobs/").status_code, 401)
        self.assertEqual(len(self.wrk.get("/api/categories/").data), 16)

    def test_password_reset(self):
        anon = APIClient()
        r = anon.post("/api/auth/password-reset/", {"phone_number": "08099990000"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("demo_code", r.data)  # unknown number: same answer, no code
        r = anon.post("/api/auth/password-reset/", {"phone_number": "+234 802 345 6789"}, format="json")
        code = r.data["demo_code"]
        bad = anon.post("/api/auth/password-reset/confirm/", {"phone_number": "08023456789", "code": "000000" if code != "000000" else "111111",
                                                            "new_password": "New-Passw0rd-2026"}, format="json")
        self.assertEqual(bad.status_code, 400)
        weak = anon.post("/api/auth/password-reset/confirm/", {"phone_number": "08023456789", "code": code, "new_password": "123"}, format="json")
        self.assertIn("new_password", weak.data)
        r = anon.post("/api/auth/password-reset/confirm/", {"phone_number": "08023456789", "code": code, "new_password": "New-Passw0rd-2026"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertIn("access", r.data)
        again = anon.post("/api/auth/password-reset/confirm/", {"phone_number": "08023456789", "code": code, "new_password": "Other-Passw0rd-2026"}, format="json")
        self.assertEqual(again.status_code, 400)  # code is single-use
        r = anon.post("/api/auth/login/", {"phone_number": "08023456789", "password": "New-Passw0rd-2026"}, format="json")
        self.assertEqual(r.status_code, 200)
