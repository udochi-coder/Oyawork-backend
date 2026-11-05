from decimal import Decimal

from django.conf import settings
from django.db.models import Q
from django.shortcuts import get_object_or_404
from loguru import logger
from rest_framework import mixins, status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from accounts.models import Worker
from accounts.services import get_employer, get_worker
from bookings import services
from bookings.models import Booking, HiringRequest
from bookings.serializers import (
    BookingDetailsSerializer,
    BookingSerializer,
    EvidenceSerializer,
    HiringRequestCreateSerializer,
    HiringRequestSerializer,
    LocationPingSerializer,
    OfferSerializer,
    PaySerializer,
)
from core.constants import ROLE_ADMIN
from jobs.models import Jobs
from payments import services as payments
from tracking.models import JobEvidence, LocationPing


def _scope_for(user, qs, worker_path, employer_path):
    """Limit a queryset to rows this user takes part in (in either mode)."""
    if user.is_staff or user.role == ROLE_ADMIN:
        return qs
    worker = get_worker(user)
    employer = get_employer(user)
    cond = Q(pk__in=[])
    if worker:
        cond |= Q(**{worker_path: worker})
    if employer:
        cond |= Q(**{employer_path: employer})
    return qs.filter(cond)


class HiringRequestViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin, GenericViewSet):
    """Offers and counter offers between an employer and a worker for one job.

    list:   ?job=<id>  ?status=pending,negotiating  ?as=worker|employer
    create: employer sends a job to a worker (swipe right / Hire): {job, worker, amount?, message?}
    """

    serializer_class = HiringRequestSerializer

    def get_queryset(self):
        qs = HiringRequest.objects.select_related("job__employer__user", "job__category", "worker__user").prefetch_related(
            "negotiations__sender", "worker__skills"
        )
        user = self.request.user
        params = self.request.query_params
        role = params.get("as")
        if role == "worker":
            qs = qs.filter(worker__user=user)
        elif role == "employer":
            qs = qs.filter(job__employer__user=user)
        else:
            qs = _scope_for(user, qs, "worker", "job__employer")
        if params.get("job"):
            qs = qs.filter(job_id=params["job"])
        if params.get("status"):
            qs = qs.filter(status__in=params["status"].split(","))
        return qs

    def create(self, request, *args, **kwargs):
        employer = get_employer(request.user)
        if not employer or request.user.role != "employer":
            raise PermissionDenied("Switch to Employer mode to hire a worker.")
        if employer.status != "active":
            raise PermissionDenied("Finish verifying your account before hiring.")
        data = HiringRequestCreateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        job = get_object_or_404(Jobs, pk=data.validated_data["job"], employer=employer)
        worker = get_object_or_404(Worker, pk=data.validated_data["worker"])
        hr = services.open_request(job, worker, "employer", data.validated_data.get("amount"), data.validated_data["message"])
        logger.success(f"Hiring request id={hr.id} job={job.id} worker={worker.id}")
        return Response(self.get_serializer(hr).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def accept(self, request, pk=None):
        booking = services.accept_request(self.get_object(), request.user)
        return Response(BookingSerializer(booking, context=self.get_serializer_context()).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        hr = services.reject_request(self.get_object(), request.user)
        return Response(self.get_serializer(self.get_queryset().get(pk=hr.pk)).data)

    @action(detail=True, methods=["post"])
    def counter(self, request, pk=None):
        data = OfferSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        hr = services.counter_offer(self.get_object(), request.user, data.validated_data.get("amount"), data.validated_data["message"])
        return Response(self.get_serializer(self.get_queryset().get(pk=hr.pk)).data, status=status.HTTP_201_CREATED)


class BookingViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, GenericViewSet):
    """A confirmed hire, from payment to completion.

    list: ?status=scheduled,en_route  ?active=1  ?as=worker|employer
    """

    serializer_class = BookingSerializer

    def get_queryset(self):
        qs = Booking.objects.select_related(
            "hiring_request__job__employer__user", "hiring_request__job__category", "hiring_request__worker__user"
        ).prefetch_related("evidence", "hiring_request__worker__skills")
        user = self.request.user
        params = self.request.query_params
        role = params.get("as")
        if role == "worker":
            qs = qs.filter(hiring_request__worker__user=user)
        elif role == "employer":
            qs = qs.filter(hiring_request__job__employer__user=user)
        else:
            qs = _scope_for(user, qs, "hiring_request__worker", "hiring_request__job__employer")
        if params.get("status"):
            qs = qs.filter(status__in=params["status"].split(","))
        if params.get("active") == "1":
            qs = qs.filter(status__in=Booking.ACTIVE_STATUSES)
        return qs

    def _respond(self, booking, code=status.HTTP_200_OK):
        booking.refresh_from_db()
        return Response(self.get_serializer(booking).data, status=code)

    @action(detail=True, methods=["post"])
    def details(self, request, pk=None):
        data = BookingDetailsSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        return self._respond(services.update_details(self.get_object(), request.user, data.validated_data))

    @action(detail=True, methods=["post"])
    def pay(self, request, pk=None):
        booking = self.get_object()
        if booking.party_of(request.user) != "employer":
            raise PermissionDenied("Only the employer pays for a booking.")
        if booking.status != "pending_payment":
            raise ValidationError({"error": "This booking is already paid."})
        method = PaySerializer(data=request.data)
        method.is_valid(raise_exception=True)
        method = method.validated_data["method"]

        if method == "wallet":
            try:
                payments.debit(request.user, booking.agreed_price, "escrow_payment", f"Escrow: {booking.job.title}", booking)
            except payments.PaymentError as exc:
                raise ValidationError({"error": str(exc)})
            payments.fund_escrow(booking, "wallet", payments.new_reference("OWW"))
            return self._respond(booking)

        if payments.paystack_enabled():
            try:
                intent = payments.start_checkout(request.user, booking.agreed_price, "escrow", method, booking)
            except payments.PaymentError as exc:
                raise ValidationError({"error": f"Payment could not start: {exc}"})
            return Response({"authorization_url": intent.authorization_url, "reference": intent.reference}, status=202)

        if settings.DEMO_PAYMENTS:
            payments.fund_escrow(booking, method, payments.new_reference("DEMO"))
            return self._respond(booking)
        return Response({"error": "Card and bank payments are not configured yet."}, status=503)

    @action(detail=True, methods=["post"], url_path="en-route")
    def en_route(self, request, pk=None):
        return self._respond(services.start_journey(self.get_object(), request.user))

    @action(detail=True, methods=["post"])
    def arrive(self, request, pk=None):
        return self._respond(services.mark_arrived(self.get_object(), request.user))

    @action(detail=True, methods=["post"], url_path="verify-code")
    def verify_code(self, request, pk=None):
        return self._respond(services.verify_code(self.get_object(), request.user, request.data.get("code")))

    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        return self._respond(services.mark_completed(self.get_object(), request.user))

    @action(detail=True, methods=["post"])
    def confirm(self, request, pk=None):
        return self._respond(services.confirm_and_release(self.get_object(), request.user))

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        return self._respond(services.cancel_booking(self.get_object(), request.user, request.data.get("reason", "")))

    @action(detail=True, methods=["post"])
    def location(self, request, pk=None):
        booking = self.get_object()
        if booking.party_of(request.user) != "worker":
            raise PermissionDenied("Only the worker shares location.")
        if booking.status not in ("en_route", "arrived"):
            raise ValidationError({"error": "Location is only shared while you are on the way."})
        data = LocationPingSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        LocationPing.objects.create(
            booking=booking,
            latitude=Decimal(str(round(data.validated_data["latitude"], 6))),
            longitude=Decimal(str(round(data.validated_data["longitude"], 6))),
            accuracy_m=data.validated_data.get("accuracy_m"),
        )
        return Response(self.get_serializer(booking).data["tracking"], status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], parser_classes=[MultiPartParser, FormParser, JSONParser])
    def evidence(self, request, pk=None):
        booking = self.get_object()
        if booking.party_of(request.user) != "worker":
            raise PermissionDenied("Only the worker uploads job evidence.")
        if booking.status not in ("in_progress", "completed", "disputed"):
            raise ValidationError({"error": "Evidence can be added once the job has started."})
        data = EvidenceSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        photo = data.validated_data["photo"]
        if photo.size > 10 * 1024 * 1024:
            raise ValidationError({"photo": "Photos must be 10MB or smaller."})
        item = JobEvidence.objects.create(booking=booking, uploaded_by=request.user, **data.validated_data)
        return Response(EvidenceSerializer(item, context=self.get_serializer_context()).data, status=status.HTTP_201_CREATED)
