from django.contrib import admin

from .models import Review


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ("reviewer", "reviewee", "reviewee_role", "rating", "created_at")
    list_filter = ("rating", "reviewee_role")
