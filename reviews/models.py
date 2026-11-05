from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class Review(models.Model):
    """Two-way rating: the employer rates the worker and the worker rates the employer."""

    booking = models.ForeignKey("bookings.Booking", on_delete=models.CASCADE, related_name="reviews")
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="reviews_given")
    reviewee = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="reviews_received")
    # Which of the reviewee's profiles the rating counts towards.
    reviewee_role = models.CharField(max_length=10, choices=[("worker", "Worker"), ("employer", "Employer")])
    rating = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "review"
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["booking", "reviewer"], name="one_review_per_booking_per_reviewer")]

    def __str__(self):
        return f"{self.reviewer} -> {self.reviewee}: {self.rating}"
