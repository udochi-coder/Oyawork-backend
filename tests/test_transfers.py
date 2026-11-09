from decimal import Decimal
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from payments import services
from payments.models import Transaction

BANKS = [{"name": "Guaranty Trust Bank", "code": "058", "active": True}, {"name": "Access Bank", "code": "044", "active": True}]


def fake_paystack(transfer_status="pending", resolve_ok=True, transfer_error=None):
    calls = []

    def call(method, path, **kw):
        calls.append((method, path, kw))
        if path == "/bank":
            return BANKS
        if path == "/bank/resolve":
            if not resolve_ok:
                raise services.PaymentError("Could not resolve account name")
            return {"account_name": "PEACE ASHIEGBU", "account_number": kw["params"]["account_number"]}
        if path == "/transferrecipient":
            return {"recipient_code": "RCP_abc"}
        if path == "/transfer":
            if transfer_error:
                raise services.PaymentError(transfer_error)
            return {"transfer_code": "TRF_xyz", "status": transfer_status, "reference": kw["json"]["reference"]}
        raise AssertionError(path)

    return call, calls


@override_settings(PAYSTACK_SECRET_KEY="sk_test_x", PAYSTACK_TRANSFERS=True, DEMO_PAYMENTS=False)
class TransferTest(TestCase):
    def setUp(self):
        cache.clear()
        self.c = APIClient()
        r = self.c.post("/api/worker_register/", {"full_name": "Peace Ashiegbu", "phone_number": "08071551236",
                                                  "password": "Str0ng-Passw0rd!"}, format="json")
        self.c.credentials(HTTP_AUTHORIZATION=f"Bearer {r.data['access']}")
        from core.models import User
        self.user = User.objects.get(phone_number="08071551236")
        services.credit(self.user, 5000, "deposit", "Top-up")

    def withdraw(self, fake, **extra):
        call, calls = fake
        with mock.patch("payments.services._paystack", side_effect=call):
            r = self.c.post("/api/wallet/withdraw/", {"amount": "2000", "bank_name": "x", "bank_code": "058",
                                                      "account_number": "0123456789", **extra}, format="json")
        return r, calls

    def balance(self):
        return services.get_wallet(self.user).balance

    def test_banks_and_resolve(self):
        call, _ = fake_paystack()
        with mock.patch("payments.services._paystack", side_effect=call):
            r = self.c.get("/api/wallet/banks/")
            self.assertEqual((r.data["auto"], r.data["banks"][0]["name"]), (True, "Access Bank"))
            r = self.c.get("/api/wallet/resolve-account/", {"account_number": "0123456789", "bank_code": "058"})
            self.assertEqual(r.data["account_name"], "PEACE ASHIEGBU")

    def test_withdraw_sends_transfer_and_webhook_completes_it(self):
        r, calls = self.withdraw(fake_paystack("pending"))
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.data["status"], "pending")
        transfer = [c for c in calls if c[1] == "/transfer"][0][2]["json"]
        self.assertEqual(transfer["amount"], 200000)  # kobo
        self.assertEqual(transfer["reference"], transfer["reference"].lower())
        self.assertEqual(self.balance(), Decimal("3000.00"))
        txn = Transaction.objects.get(kind="withdrawal")
        self.assertEqual((txn.bank_name, txn.account_name), ("Guaranty Trust Bank", "PEACE ASHIEGBU"))
        services.handle_webhook({"event": "transfer.success", "data": {"reference": txn.reference}})
        txn.refresh_from_db()
        self.assertEqual(txn.status, "success")

    def test_failed_webhook_refunds_once(self):
        self.withdraw(fake_paystack("pending"))
        txn = Transaction.objects.get(kind="withdrawal")
        for _ in range(2):
            services.handle_webhook({"event": "transfer.failed", "data": {"reference": txn.reference.upper(), "reason": "Bank down"}})
        self.assertEqual(self.balance(), Decimal("5000.00"))
        txn.refresh_from_db()
        self.assertEqual((txn.status, txn.failure_reason), ("failed", "Bank down"))

    def test_paystack_error_refunds_immediately(self):
        r, _ = self.withdraw(fake_paystack(transfer_error="Your balance is not enough to fulfil this request"))
        self.assertEqual(r.status_code, 400)
        self.assertIn("back in your wallet", str(r.data))
        self.assertEqual(self.balance(), Decimal("5000.00"))

    def test_unknown_account_moves_no_money(self):
        r, calls = self.withdraw(fake_paystack(resolve_ok=False))
        self.assertIn("account_number", r.data)
        self.assertEqual(self.balance(), Decimal("5000.00"))
        self.assertFalse([c for c in calls if c[1] == "/transfer"])

    def test_bank_required(self):
        r, _ = self.withdraw(fake_paystack(), bank_code="")
        self.assertIn("bank_code", r.data)

    @override_settings(PAYSTACK_SECRET_KEY="")
    def test_manual_mode_without_paystack(self):
        r = self.c.post("/api/wallet/withdraw/", {"amount": "2000", "bank_name": "GTBank", "account_number": "0123456789"}, format="json")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(self.c.get("/api/wallet/banks/").data["auto"], False)
