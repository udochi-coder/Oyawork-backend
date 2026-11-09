from rest_framework import serializers

from payments.models import Transaction


class TransactionSerializer(serializers.ModelSerializer):
    kind_label = serializers.CharField(source="get_kind_display", read_only=True)

    class Meta:
        model = Transaction
        fields = ["id", "kind", "kind_label", "amount", "status", "description", "reference", "booking",
                  "bank_name", "account_number", "created_at"]


class WithdrawSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=100)
    bank_name = serializers.CharField(max_length=100)
    account_number = serializers.RegexField(r"^\d{10}$", error_messages={"invalid": "Enter your 10-digit NUBAN account number."})
    account_name = serializers.CharField(max_length=100, required=False, allow_blank=True)
    # Paystack bank code (from GET /api/wallet/banks/); needed for automatic payouts.
    bank_code = serializers.CharField(max_length=20, required=False, allow_blank=True)


class FundSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=100)
    method = serializers.ChoiceField(choices=["card", "bank_transfer"], default="card")
