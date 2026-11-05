from django.contrib import admin, messages

from .models import Dispute, DisputeEvidence
from .services import resolve_dispute


class EvidenceInline(admin.TabularInline):
    model = DisputeEvidence
    extra = 0


@admin.register(Dispute)
class DisputeAdmin(admin.ModelAdmin):
    list_display = ("ticket", "booking", "reason", "raised_by_role", "status", "resolution", "created_at")
    list_filter = ("status", "reason", "resolution")
    search_fields = ("ticket", "details")
    inlines = [EvidenceInline]
    actions = ["mark_evidence_review", "mark_parties_contacted", "release_to_worker", "refund_employer", "split_payment"]

    @admin.action(description="Step: evidence reviewed")
    def mark_evidence_review(self, request, queryset):
        queryset.exclude(status="resolved").update(status="evidence_review")

    @admin.action(description="Step: both parties contacted")
    def mark_parties_contacted(self, request, queryset):
        queryset.exclude(status="resolved").update(status="parties_contacted")

    def _resolve(self, request, queryset, outcome):
        for dispute in queryset.exclude(status="resolved"):
            try:
                resolve_dispute(dispute, outcome)
            except Exception as exc:
                self.message_user(request, f"#{dispute.ticket}: {exc}", messages.ERROR)

    @admin.action(description="Decide: release payment to worker")
    def release_to_worker(self, request, queryset):
        self._resolve(request, queryset, "release_worker")

    @admin.action(description="Decide: refund the employer")
    def refund_employer(self, request, queryset):
        self._resolve(request, queryset, "refund_employer")

    @admin.action(description="Decide: split payment 50/50")
    def split_payment(self, request, queryset):
        self._resolve(request, queryset, "split")
