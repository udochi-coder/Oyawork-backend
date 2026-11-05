from django.db import IntegrityError, transaction
from rest_framework import mixins, status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from accounts.services import refresh_rating
from core.utils import notify
from reviews.models import Review
from reviews.serializers import ReviewSerializer


class ReviewViewSet(mixins.ListModelMixin, mixins.CreateModelMixin, GenericViewSet):
    """list: ?user=<user id>&role=worker|employer  or  ?booking=<id>
    create: {booking, rating (1-5), comment} - the reviewee is the other side of the booking."""

    serializer_class = ReviewSerializer

    def get_queryset(self):
        qs = Review.objects.select_related("reviewer", "reviewee", "booking__hiring_request__job")
        params = self.request.query_params
        if params.get("user"):
            qs = qs.filter(reviewee_id=params["user"])
        if params.get("role"):
            qs = qs.filter(reviewee_role=params["role"])
        if params.get("booking"):
            qs = qs.filter(booking_id=params["booking"])
        if not any(params.get(k) for k in ("user", "booking")):
            qs = qs.filter(reviewee=self.request.user)
        return qs[:50]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        booking = serializer.validated_data["booking"]
        party = booking.party_of(request.user)
        if party is None:
            raise PermissionDenied("You can only review bookings you took part in.")
        if booking.status != "confirmed":
            raise ValidationError({"error": "You can leave a review once the job is complete and paid."})
        if party == "employer":
            reviewee, role, profile = booking.worker.user, "worker", booking.worker
        else:
            reviewee, role, profile = booking.employer.user, "employer", booking.employer
        if Review.objects.filter(booking=booking, reviewer=request.user).exists():
            raise ValidationError({"error": "You have already reviewed this booking."})
        try:
            with transaction.atomic():
                review = serializer.save(reviewer=request.user, reviewee=reviewee, reviewee_role=role)
        except IntegrityError:
            raise ValidationError({"error": "You have already reviewed this booking."})
        refresh_rating(profile, reviewee, role)
        notify(reviewee, f"{request.user.full_name} rated you {review.rating} stars", review.comment[:120], kind="review",
               booking=booking.pk)
        return Response(self.get_serializer(review).data, status=status.HTTP_201_CREATED)
