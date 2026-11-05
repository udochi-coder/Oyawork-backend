from rest_framework import serializers

from reviews.models import Review


class ReviewSerializer(serializers.ModelSerializer):
    reviewer_name = serializers.CharField(source="reviewer.full_name", read_only=True)
    reviewer_role = serializers.SerializerMethodField()
    reviewee_name = serializers.CharField(source="reviewee.full_name", read_only=True)
    job_title = serializers.CharField(source="booking.job.title", read_only=True)
    job_location = serializers.CharField(source="booking.job.location", read_only=True)

    class Meta:
        model = Review
        fields = ["id", "booking", "reviewer_name", "reviewer_role", "reviewee", "reviewee_name", "reviewee_role",
                  "rating", "comment", "job_title", "job_location", "created_at"]
        read_only_fields = ["id", "reviewee", "reviewee_role", "created_at"]

    def get_reviewer_role(self, obj):
        return "employer" if obj.reviewee_role == "worker" else "worker"
