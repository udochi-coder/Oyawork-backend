import json

from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from core.utils import notify
from payments import services
from payments.models import PaymentIntent
from payments.serializers import FundSerializer, TransactionSerializer, WithdrawSerializer


class WalletView(APIView):
    def get(self, request):
        wallet = services.get_wallet(request.user)
        incoming, outgoing = services.pending_escrow_for(request.user)
        recent = wallet.transactions.all()[:30]
        return Response({
            "balance": wallet.balance,
            "pending_escrow": incoming if request.user.role == "worker" else outgoing,
            "pending_escrow_incoming": incoming,
            "pending_escrow_outgoing": outgoing,
            "paystack_enabled": services.paystack_enabled(),
            "demo_payments": settings.DEMO_PAYMENTS and not services.paystack_enabled(),
            "transactions": TransactionSerializer(recent, many=True).data,
        })


class WithdrawView(APIView):
    def post(self, request):
        data = WithdrawSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        v = data.validated_data
        auto = services.transfers_enabled()
        if auto:
            if not v.get("bank_code"):
                raise ValidationError({"bank_code": "Choose your bank from the list."})
            try:
                # Confirms the account exists and gives the real holder's name before any money moves.
                v["account_name"] = services.resolve_account(v["account_number"], v["bank_code"]) or v.get("account_name", "")
            except services.PaymentError:
                raise ValidationError({"account_number": "We couldn't find that account. Check the number and the bank."})
            bank = next((b for b in services.list_banks() if b["code"] == v["bank_code"]), None)
            if bank:
                v["bank_name"] = bank["name"]
        try:
            txn = services.debit(
                request.user, v["amount"], "withdrawal", f"Withdrawal to {v['bank_name']}",
                status="pending", bank_name=v["bank_name"], bank_code=v.get("bank_code", ""),
                account_number=v["account_number"], account_name=v.get("account_name", ""),
                reference=services.new_reference("owd").lower(),
            )
        except services.PaymentError as exc:
            raise ValidationError({"amount": str(exc)})
        if auto:
            txn = services.payout_withdrawal(txn)
            if txn.status == "failed":
                raise ValidationError({"error": "Paystack couldn't send this withdrawal, so the money is back in your wallet. "
                                                "Please try again later."})
        else:
            notify(request.user, f"Withdrawal of ₦{v['amount']:,.0f} requested",
                   "We'll send it to your bank within 24 hours.", kind="payment")
        return Response(TransactionSerializer(txn).data, status=status.HTTP_201_CREATED)


class BanksView(APIView):
    """Banks for the withdrawal form. `auto` says whether payouts go out automatically."""

    def get(self, request):
        if not services.transfers_enabled():
            return Response({"auto": False, "banks": []})
        try:
            return Response({"auto": True, "banks": services.list_banks()})
        except services.PaymentError as exc:
            return Response({"error": f"Couldn't load banks from Paystack: {exc}"}, status=502)


class ResolveAccountView(APIView):
    def get(self, request):
        number, code = request.query_params.get("account_number", ""), request.query_params.get("bank_code", "")
        if not (number.isdigit() and len(number) == 10 and code):
            return Response({"error": "Enter a 10-digit account number and choose a bank."}, status=400)
        if not services.transfers_enabled():
            return Response({"account_name": ""})
        try:
            return Response({"account_name": services.resolve_account(number, code)})
        except services.PaymentError:
            return Response({"error": "We couldn't find that account. Check the number and the bank."}, status=400)


class FundWalletView(APIView):
    """Top up the wallet with Paystack (or instantly in demo mode)."""

    def post(self, request):
        data = FundSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        amount, method = data.validated_data["amount"], data.validated_data["method"]
        if services.paystack_enabled():
            try:
                intent = services.start_checkout(request.user, amount, "wallet_topup", method)
            except services.PaymentError as exc:
                raise ValidationError({"error": str(exc)})
            return Response({"authorization_url": intent.authorization_url, "reference": intent.reference}, status=202)
        if settings.DEMO_PAYMENTS:
            txn = services.credit(request.user, amount, "deposit", "Wallet top-up (demo)", reference=services.new_reference("DEMO"))
            return Response(TransactionSerializer(txn).data, status=status.HTTP_201_CREATED)
        return Response({"error": "Payments are not configured yet."}, status=503)


class VerifyPaymentView(APIView):
    """Called by the frontend after Paystack redirects back with ?reference=..."""

    def get(self, request):
        reference = request.query_params.get("reference")
        intent = PaymentIntent.objects.filter(reference=reference, user=request.user).first()
        if not intent:
            return Response({"error": "Unknown payment reference."}, status=404)
        try:
            intent = services.verify_reference(reference)
        except services.PaymentError as exc:
            return Response({"error": str(exc)}, status=502)
        return Response({"status": intent.status, "purpose": intent.purpose, "booking": intent.booking_id})


@csrf_exempt
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def paystack_webhook(request):
    if not services.valid_paystack_signature(request.body, request.headers.get("x-paystack-signature", "")):
        return Response(status=401)
    services.handle_webhook(json.loads(request.body or b"{}"))
    return Response({"received": True})
