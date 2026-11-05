from django.contrib import admin

from .models import Category, Jobs, Skills


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "icon")
    search_fields = ("name",)


@admin.register(Skills)
class SkillsAdmin(admin.ModelAdmin):
    list_display = ("name", "category")
    list_filter = ("category",)
    search_fields = ("name",)


@admin.register(Jobs)
class JobsAdmin(admin.ModelAdmin):
    list_display = ("title", "employer", "category", "offered_price", "location", "scheduled_date", "status", "created_at")
    list_filter = ("status", "category")
    search_fields = ("title", "location", "employer__user__full_name")
