from django.contrib import admin

from .models import Escrow, PaymentIntent, Transaction, Wallet


@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    list_display = ("user", "balance", "updated_at")
    search_fields = ("user__full_name", "user__phone_number")


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ("wallet", "kind", "amount", "status", "description", "created_at")
    list_filter = ("kind", "status")
    search_fields = ("reference", "wallet__user__full_name", "account_number")
    actions = ["mark_paid_out"]

    @admin.action(description="Mark selected withdrawals as paid out")
    def mark_paid_out(self, request, queryset):
        queryset.filter(kind="withdrawal", status="pending").update(status="success")


@admin.register(Escrow)
class EscrowAdmin(admin.ModelAdmin):
    list_display = ("booking", "amount", "status", "method", "created_at", "released_at")
    list_filter = ("status", "method")


@admin.register(PaymentIntent)
class PaymentIntentAdmin(admin.ModelAdmin):
    list_display = ("reference", "user", "purpose", "amount", "status", "created_at")
    list_filter = ("purpose", "status")
