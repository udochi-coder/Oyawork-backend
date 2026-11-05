"""Create demo workers and an employer around Lagos so the app isn't empty.

    python manage.py seed_demo            # password for every demo account: OyaWork-Demo-2026
    python manage.py seed_demo --delete   # remove them again
"""
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from accounts.models import Employer, VerificationDocument, Worker
from core.models import User
from jobs.models import Skills
from payments.models import Wallet

PASSWORD = "OyaWork-Demo-2026"
WORKERS = [
    ("Mary Johnson", "08010000001", ["Cleaning", "Laundry", "Ironing"], (6.4474, 3.4720), "Lekki Phase 1", "10000",
     "Over 5 years of verified household service experience in Lagos. Known for punctuality and attention to detail."),
    ("Sunday Udoh", "08010000002", ["Plumbing", "General Handyman"], (6.5095, 3.3711), "Yaba", "12000",
     "Plumbing and repairs. Leaks, taps, water heaters, pumping machines."),
    ("Grace Alao", "08010000003", ["Cleaning", "Cooking", "Elderly Care"], (6.4550, 3.4800), "Lekki", "9000",
     "Home cleaning and elderly care. Patient, gentle, reliable."),
    ("Tunde Bakare", "08010000004", ["Electrician", "Generator Repair"], (6.5170, 3.3920), "Akoka", "15000",
     "Generator mechanic and electrician. Wiring, inverters, generator servicing."),
    ("Ada Eze", "08010000005", ["Babysitting", "Cleaning", "Laundry"], (6.4300, 3.4210), "Victoria Island", "8000",
     "Live-out nanny and cleaner. First-aid trained."),
    ("Chinedu Okafor", "08010000006", ["Painting", "Carpentry"], (6.6018, 3.3515), "Ikeja", "14000",
     "Interior and exterior painting, furniture repairs."),
]
EMPLOYER = ("David James", "08010000100", (6.4474, 3.4720), "Lekki Phase 1, Lagos")


class Command(BaseCommand):
    help = "Create (or delete) demo OyaWork accounts in Lagos"

    def add_arguments(self, parser):
        parser.add_argument("--delete", action="store_true")

    @transaction.atomic
    def handle(self, *args, **opts):
        phones = [w[1] for w in WORKERS] + [EMPLOYER[1]]
        if opts["delete"]:
            n, _ = User.objects.filter(phone_number__in=phones).delete()
            self.stdout.write(self.style.SUCCESS(f"Deleted {n} demo objects"))
            return

        for name, phone, skills, (lat, lng), area, rate, bio in WORKERS:
            user, created = User.objects.get_or_create(phone_number=phone, defaults={"full_name": name, "role": "worker"})
            if created:
                user.set_password(PASSWORD)
                user.phone_verified = True
                user.save()
            worker, _ = Worker.objects.get_or_create(user=user)
            worker.status, worker.bio, worker.location = "active", bio, f"{area}, Lagos"
            worker.latitude, worker.longitude = Decimal(str(lat)), Decimal(str(lng))
            worker.daily_rate, worker.experience_level = Decimal(rate), "experienced"
            worker.availability_schedule = ["available_now", "weekdays"]
            worker.preferred_radius, worker.agreed_terms_at = 15, timezone.now()
            worker.save()
            worker.skills.set(Skills.objects.filter(name__in=skills))
            Wallet.objects.get_or_create(user=user)
            VerificationDocument.objects.get_or_create(user=user, doc_type="nin", defaults={"number": "00000000000", "status": "approved"})

        name, phone, (lat, lng), area = EMPLOYER
        user, created = User.objects.get_or_create(phone_number=phone, defaults={"full_name": name, "role": "employer"})
        if created:
            user.set_password(PASSWORD)
            user.phone_verified = True
            user.save()
        employer, _ = Employer.objects.get_or_create(user=user)
        employer.status, employer.location, employer.address = "active", area, area
        employer.latitude, employer.longitude, employer.agreed_terms_at = Decimal(str(lat)), Decimal(str(lng)), timezone.now()
        employer.bio = "Property owner and manager in Lekki Phase 1, Lagos."
        employer.save()
        Wallet.objects.get_or_create(user=user)

        self.stdout.write(self.style.SUCCESS(
            f"Demo accounts ready. Employer: {EMPLOYER[1]}, workers: {', '.join(w[1] for w in WORKERS)}. Password: {PASSWORD}"
        ))
