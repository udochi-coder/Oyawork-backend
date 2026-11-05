from django.contrib import admin

from .models import Booking, HiringRequest, Negotiation


class NegotiationInline(admin.TabularInline):
    model = Negotiation
    extra = 0
    readonly_fields = ("sender", "sender_role", "amount", "message", "created_at")


@admin.register(HiringRequest)
class HiringRequestAdmin(admin.ModelAdmin):
    list_display = ("job", "worker", "status", "current_amount", "initiated_by", "updated_at")
    list_filter = ("status", "initiated_by")
    inlines = [NegotiationInline]


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ("id", "__str__", "status", "agreed_price", "scheduled_date", "created_at")
    list_filter = ("status",)
    readonly_fields = ("oyacode",)
