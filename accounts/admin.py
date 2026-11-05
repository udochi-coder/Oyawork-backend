from django.contrib import admin
from django.utils import timezone

from .models import Employer, VerificationDocument, Worker


@admin.action(description="Activate (verify) selected profiles")
def activate(modeladmin, request, queryset):
    queryset.update(status="active")


@admin.action(description="Suspend selected profiles")
def suspend(modeladmin, request, queryset):
    queryset.update(status="suspended")


@admin.action(description="Ban selected profiles")
def ban(modeladmin, request, queryset):
    queryset.update(status="banned")


@admin.register(Worker)
class WorkerAdmin(admin.ModelAdmin):
    list_display = ("user", "email", "status", "availability_status", "rating", "reliability_score", "completed_jobs")
    search_fields = ("user__email", "user__full_name", "user__phone_number")
    list_filter = ("status", "availability_status", "experience_level")
    filter_horizontal = ("skills",)
    actions = [activate, suspend, ban]


@admin.register(Employer)
class EmployerAdmin(admin.ModelAdmin):
    list_display = ("user", "email", "status", "employer_type", "rating", "completed_jobs")
    search_fields = ("user__email", "user__full_name", "user__phone_number")
    list_filter = ("status", "employer_type")
    actions = [activate, suspend, ban]


@admin.register(VerificationDocument)
class VerificationDocumentAdmin(admin.ModelAdmin):
    list_display = ("user", "doc_type", "number", "status", "note", "created_at")
    list_filter = ("doc_type", "status")
    search_fields = ("user__full_name", "user__phone_number", "number")
    readonly_fields = ("provider_result",)
    actions = ["approve", "reject", "recheck"]

    @admin.action(description="Approve selected documents (and activate the person)")
    def approve(self, request, queryset):
        from accounts.services import activate_if_verified

        queryset.update(status="approved", reviewed_at=timezone.now(), note="Approved by admin")
        for user in {doc.user for doc in queryset.select_related("user")}:
            activate_if_verified(user)

    @admin.action(description="Reject selected documents")
    def reject(self, request, queryset):
        queryset.update(status="rejected", reviewed_at=timezone.now(), note="Rejected after review. Please upload it again.")

    @admin.action(description="Re-run automatic NIN + selfie check (Dojah)")
    def recheck(self, request, queryset):
        from accounts import identity
        from accounts.services import activate_if_verified

        if not identity.enabled():
            self.message_user(request, "Dojah isn't set up: add DOJAH_APP_ID and DOJAH_SECRET_KEY on Render.", level="error")
            return
        for user in {doc.user for doc in queryset.select_related("user")}:
            outcome = identity.check_identity(user, force=True)
            activate_if_verified(user)
            self.message_user(request, f"{user}: {outcome or 'needs both a NIN and a selfie'}")
