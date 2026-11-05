from django.shortcuts import get_object_or_404
from rest_framework import mixins, status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from bookings.models import Booking
from bookings.views import _scope_for
from reports.models import Dispute
from reports.serializers import DisputeSerializer
from reports.services import open_dispute


class DisputeViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin, GenericViewSet):
    """Report an issue on a booking (multipart: booking, reason, details, files[])."""

    serializer_class = DisputeSerializer
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get_queryset(self):
        qs = Dispute.objects.select_related("booking__hiring_request__worker__user", "booking__hiring_request__job")
        return _scope_for(self.request.user, qs, "booking__hiring_request__worker", "booking__hiring_request__job__employer")

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        booking = get_object_or_404(
            _scope_for(request.user, Booking.objects.all(), "hiring_request__worker", "hiring_request__job__employer"),
            pk=serializer.validated_data["booking"].pk,
        )
        files = request.FILES.getlist("files")[:6]
        dispute = open_dispute(booking, request.user, serializer.validated_data["reason"],
                               serializer.validated_data.get("details", ""), files)
        return Response(self.get_serializer(dispute).data, status=status.HTTP_201_CREATED)
