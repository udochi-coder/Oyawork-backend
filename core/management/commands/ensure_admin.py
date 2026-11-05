"""Create (or update the password of) an admin account from environment variables.

Used by build.sh on Render, whose free plan has no shell for `createsuperuser`.
    ADMIN_PHONE_NUMBER=08000000000 ADMIN_PASSWORD=... [ADMIN_FULL_NAME=...] [ADMIN_EMAIL=...]
"""
import os

from django.core.management.base import BaseCommand

from core.models import User
from core.utils import normalize_phone


class Command(BaseCommand):
    help = "Create the admin account described by ADMIN_* environment variables"

    def handle(self, *args, **options):
        phone = normalize_phone(os.getenv("ADMIN_PHONE_NUMBER", ""))
        password = os.getenv("ADMIN_PASSWORD", "")
        if not phone or not password:
            self.stdout.write("ADMIN_PHONE_NUMBER / ADMIN_PASSWORD not set; skipping admin setup.")
            return
        user, created = User.objects.get_or_create(
            phone_number=phone,
            defaults={"full_name": os.getenv("ADMIN_FULL_NAME", "OyaWork Admin"), "email": os.getenv("ADMIN_EMAIL") or None,
                      "role": "admin", "is_staff": True, "is_superuser": True, "phone_verified": True},
        )
        user.is_staff = user.is_superuser = True
        user.set_password(password)
        user.save()
        self.stdout.write(self.style.SUCCESS(f"Admin {'created' if created else 'updated'}: {phone}"))
