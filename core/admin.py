from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import Notification, User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ("full_name", "phone_number", "email", "role", "phone_verified", "email_verified", "is_active")
    list_filter = ("role", "is_active", "phone_verified", "is_staff")
    fieldsets = (
        (None, {"fields": ("phone_number", "email", "password")}),
        ("Personal info", {"fields": ("full_name", "phone_verified", "email_verified")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "role", "groups", "user_permissions")}),
        ("Important dates", {"fields": ("last_login", "created_at", "updated_at")}),
    )
    readonly_fields = ("created_at", "updated_at")
    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ("phone_number", "email", "full_name", "role", "password1", "password2"),
        }),
    )
    search_fields = ("email", "full_name", "phone_number")
    ordering = ("-created_at",)


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("user", "title", "kind", "is_read", "created_at")
    list_filter = ("kind", "is_read")
    search_fields = ("title", "user__full_name")
