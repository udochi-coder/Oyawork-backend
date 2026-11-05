from rest_framework import serializers

from accounts.serializers import PublicEmployerSerializer, PublicWorkerSerializer
from bookings.models import Booking, HiringRequest, Negotiation
from core.utils import eta_minutes, haversine_km
from tracking.models import JobEvidence


class JobSummarySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    title = serializers.CharField()
    description = serializers.CharField()
    category = serializers.CharField(source="category.name")
    offered_price = serializers.DecimalField(max_digits=10, decimal_places=2)
    location = serializers.CharField()
    latitude = serializers.DecimalField(max_digits=9, decimal_places=6)
    longitude = serializers.DecimalField(max_digits=9, decimal_places=6)
    scheduled_date = serializers.DateField()
    scheduled_time = serializers.TimeField()
    estimated_duration = serializers.IntegerField()
    status = serializers.CharField()


class NegotiationSerializer(serializers.ModelSerializer):
    sender_name = serializers.CharField(source="sender.full_name", read_only=True)

    class Meta:
        model = Negotiation
        fields = ["id", "sender_role", "sender_name", "amount", "message", "created_at"]


class HiringRequestSerializer(serializers.ModelSerializer):
    job = JobSummarySerializer(read_only=True)
    worker = PublicWorkerSerializer(read_only=True)
    employer = PublicEmployerSerializer(source="job.employer", read_only=True)
    negotiations = NegotiationSerializer(many=True, read_only=True)
    booking_id = serializers.SerializerMethodField()
    my_party = serializers.SerializerMethodField()
    can_accept = serializers.SerializerMethodField()
    distance_km = serializers.SerializerMethodField()

    class Meta:
        model = HiringRequest
        fields = [
            "id", "job", "worker", "employer", "initiated_by", "status", "current_amount", "last_offer_by",
            "message", "negotiations", "booking_id", "my_party", "can_accept", "distance_km", "created_at", "updated_at",
        ]

    def _user(self):
        request = self.context.get("request")
        return request.user if request else None

    def get_booking_id(self, obj):
        booking = getattr(obj, "booking", None) if hasattr(obj, "booking") else None
        return booking.pk if booking else None

    def get_my_party(self, obj):
        return obj.party_of(self._user())

    def get_can_accept(self, obj):
        party = obj.party_of(self._user())
        return bool(party and obj.is_open and obj.last_offer_by != party and obj.job.status == "open")

    def get_distance_km(self, obj):
        return haversine_km(obj.job.latitude, obj.job.longitude, obj.worker.latitude, obj.worker.longitude)


class HiringRequestCreateSerializer(serializers.Serializer):
    job = serializers.IntegerField()
    worker = serializers.IntegerField()
    amount = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, allow_null=True)
    message = serializers.CharField(required=False, allow_blank=True, default="")


class OfferSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, allow_null=True)
    message = serializers.CharField(required=False, allow_blank=True, default="")


class EvidenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = JobEvidence
        fields = ["id", "kind", "photo", "created_at"]
        read_only_fields = ["id", "created_at"]


class BookingSerializer(serializers.ModelSerializer):
    job = JobSummarySerializer(source="hiring_request.job", read_only=True)
    worker = PublicWorkerSerializer(source="hiring_request.worker", read_only=True)
    employer = PublicEmployerSerializer(source="hiring_request.job.employer", read_only=True)
    hiring_request_id = serializers.IntegerField(source="hiring_request.id", read_only=True)
    oyacode = serializers.SerializerMethodField()
    escrow = serializers.SerializerMethodField()
    my_party = serializers.SerializerMethodField()
    evidence = EvidenceSerializer(many=True, read_only=True)
    tracking = serializers.SerializerMethodField()
    has_reviewed = serializers.SerializerMethodField()
    dispute_id = serializers.SerializerMethodField()
    status_label = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = Booking
        fields = [
            "id", "hiring_request_id", "status", "status_label", "agreed_price", "scheduled_date", "scheduled_time",
            "for_someone_else", "recipient_name", "recipient_relationship", "recipient_phone",
            "service_address", "service_latitude", "service_longitude",
            "job", "worker", "employer", "oyacode", "escrow", "my_party", "evidence", "tracking",
            "has_reviewed", "dispute_id", "en_route_at", "arrived_at", "started_at", "completed_at",
            "confirmed_at", "cancelled_at", "cancelled_by_role", "cancel_reason", "created_at", "updated_at",
        ]
        read_only_fields = [f for f in fields if f not in (
            "for_someone_else", "recipient_name", "recipient_relationship", "recipient_phone",
            "service_address", "service_latitude", "service_longitude",
        )]

    def _user(self):
        request = self.context.get("request")
        return request.user if request else None

    def get_my_party(self, obj):
        return obj.party_of(self._user())

    def get_oyacode(self, obj):
        # Only the employer ever sees the code, and only once the worker is on the way.
        if obj.party_of(self._user()) == "employer" and obj.status in ("en_route", "arrived"):
            return obj.oyacode
        return None

    def get_escrow(self, obj):
        escrow = getattr(obj, "escrow", None) if hasattr(obj, "escrow") else None
        if not escrow:
            return None
        return {"status": escrow.status, "amount": escrow.amount, "method": escrow.method, "reference": escrow.reference}

    def get_tracking(self, obj):
        ping = obj.location_pings.first()
        dest_lat, dest_lng = obj.destination
        if not ping:
            return {"latitude": None, "longitude": None, "distance_km": None, "eta_minutes": None, "updated_at": None,
                    "destination": {"latitude": dest_lat, "longitude": dest_lng}}
        distance = haversine_km(ping.latitude, ping.longitude, dest_lat, dest_lng)
        return {
            "latitude": ping.latitude, "longitude": ping.longitude, "distance_km": distance,
            "eta_minutes": eta_minutes(distance), "updated_at": ping.created_at,
            "destination": {"latitude": dest_lat, "longitude": dest_lng},
        }

    def get_has_reviewed(self, obj):
        user = self._user()
        return bool(user and obj.reviews.filter(reviewer=user).exists())

    def get_dispute_id(self, obj):
        dispute = obj.disputes.order_by("-created_at").first()
        return dispute.pk if dispute else None


class BookingDetailsSerializer(serializers.Serializer):
    for_someone_else = serializers.BooleanField(required=False)
    recipient_name = serializers.CharField(required=False, allow_blank=True, max_length=100)
    recipient_relationship = serializers.CharField(required=False, allow_blank=True, max_length=50)
    recipient_phone = serializers.CharField(required=False, allow_blank=True, max_length=20)
    service_address = serializers.CharField(required=False, allow_blank=True, max_length=255)
    service_latitude = serializers.DecimalField(max_digits=9, decimal_places=6, required=False, allow_null=True)
    service_longitude = serializers.DecimalField(max_digits=9, decimal_places=6, required=False, allow_null=True)


class PaySerializer(serializers.Serializer):
    method = serializers.ChoiceField(choices=["card", "bank_transfer", "wallet"])


class LocationPingSerializer(serializers.Serializer):
    latitude = serializers.FloatField(min_value=-90, max_value=90)
    longitude = serializers.FloatField(min_value=-180, max_value=180)
    accuracy_m = serializers.FloatField(required=False, allow_null=True)
