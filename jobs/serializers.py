from django.utils import timezone
from rest_framework import serializers

from jobs.models import Category, Jobs, Skills


class SkillsSerializer(serializers.ModelSerializer):
    category = serializers.SlugRelatedField(slug_field="name", queryset=Category.objects.all())

    class Meta:
        model = Skills
        fields = ["id", "name", "category"]


class CategorySerializer(serializers.ModelSerializer):
    skills = serializers.SlugRelatedField(many=True, read_only=True, slug_field="name")

    class Meta:
        model = Category
        fields = ["id", "name", "icon", "skills"]


class JobSerializer(serializers.ModelSerializer):
    employer = serializers.CharField(source="employer.user.full_name", read_only=True)
    employer_id = serializers.IntegerField(source="employer.id", read_only=True)
    employer_rating = serializers.DecimalField(source="employer.rating", max_digits=3, decimal_places=2, read_only=True)
    employer_verified = serializers.BooleanField(source="employer.is_verified", read_only=True)
    employer_photo = serializers.ImageField(source="employer.profile_photo", read_only=True)
    category = serializers.SlugRelatedField(slug_field="name", queryset=Category.objects.all())
    required_skills = serializers.SlugRelatedField(many=True, slug_field="name", queryset=Skills.objects.all(), required=False)
    distance_km = serializers.FloatField(read_only=True, required=False, default=None)
    interested_count = serializers.SerializerMethodField()
    my_request = serializers.SerializerMethodField()

    class Meta:
        model = Jobs
        fields = [
            "id", "employer", "employer_id", "employer_rating", "employer_verified", "employer_photo",
            "title", "description", "category", "required_skills", "offered_price", "location",
            "latitude", "longitude", "preferred_radius", "scheduled_date", "scheduled_time",
            "estimated_duration", "status", "created_at", "updated_at", "is_posted",
            "distance_km", "interested_count", "my_request",
        ]
        read_only_fields = ["id", "employer", "status", "created_at", "updated_at", "is_posted"]
        extra_kwargs = {"title": {"required": False}}

    def get_interested_count(self, obj):
        return obj.hiring_requests.filter(status__in=["pending", "negotiating"]).count()

    def get_my_request(self, obj):
        """For a worker: their hiring request on this job, if any."""
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return None
        hr = obj.hiring_requests.filter(worker__user=request.user).first()
        if not hr:
            return None
        booking = getattr(hr, "booking", None)
        return {"id": hr.id, "status": hr.status, "current_amount": hr.current_amount,
                "last_offer_by": hr.last_offer_by, "booking_id": booking.id if booking else None}

    def validate_offered_price(self, value):
        if value <= 0:
            raise serializers.ValidationError("Budget must be more than zero.")
        return value

    def validate(self, attrs):
        date = attrs.get("scheduled_date")
        if date and date < timezone.localdate():
            raise serializers.ValidationError({"scheduled_date": "Pick today or a later date."})
        if not attrs.get("title") and not self.instance:
            category = attrs.get("category")
            attrs["title"] = f"{category.name} Job" if category else "Job"
        return attrs
