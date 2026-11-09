from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from accounts.models import Employer, VerificationDocument, Worker
from accounts.services import get_employer, get_worker, verification_summary
from core.models import User
from core.utils import haversine_km, is_valid_nigerian_phone, normalize_phone
from jobs.models import Skills


class RegistrationSerializer(serializers.Serializer):
    full_name = serializers.CharField(max_length=100)
    phone_number = serializers.CharField(max_length=20)
    email = serializers.EmailField(required=False, allow_blank=True, allow_null=True)
    password = serializers.CharField(write_only=True, min_length=8)
    confirm_password = serializers.CharField(write_only=True, required=False)
    profile_photo = serializers.ImageField(required=False, allow_null=True)

    def validate_phone_number(self, value):
        phone = normalize_phone(value)
        if not is_valid_nigerian_phone(phone):
            raise serializers.ValidationError("Enter a valid Nigerian phone number, e.g. 0803 123 4567.")
        if User.objects.filter(phone_number=phone).exists():
            raise serializers.ValidationError("An account with this phone number already exists.")
        return phone

    def validate_email(self, value):
        if not value:
            return None
        value = value.strip().lower()
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("An account with this email already exists.")
        return value

    def validate(self, attrs):
        confirm = attrs.pop("confirm_password", None)
        if confirm is not None and confirm != attrs["password"]:
            raise serializers.ValidationError({"confirm_password": "Passwords do not match."})
        validate_password(attrs["password"], User(full_name=attrs["full_name"], phone_number=attrs["phone_number"]))
        return attrs


# Kept so existing imports keep working.
EmployerRegistrationSerializer = RegistrationSerializer
WorkerRegistrationSerializer = RegistrationSerializer


class CustomTokenObtainSerializer(TokenObtainPairSerializer):
    """Log in with phone number + password. Accepts any common phone format."""

    def validate(self, attrs):
        attrs[self.username_field] = normalize_phone(attrs.get(self.username_field))
        data = super().validate(attrs)
        data["user"] = MeSerializer(self.user, context=self.context).data
        return data


class SkillNameField(serializers.SlugRelatedField):
    def __init__(self, **kwargs):
        super().__init__(slug_field="name", queryset=Skills.objects.all(), **kwargs)


class WorkerProfileSerializer(serializers.ModelSerializer):
    skills = SkillNameField(many=True, required=False)

    class Meta:
        model = Worker
        fields = [
            "id", "profile_photo", "status", "bio", "address", "location", "latitude", "longitude",
            "experience_years", "experience_level", "availability_schedule", "daily_rate",
            "availability_status", "preferred_radius", "skills",
            "rating", "review_count", "reliability_score", "completed_jobs", "agreed_terms_at", "created_at",
        ]
        read_only_fields = [
            "id", "status", "rating", "review_count", "reliability_score", "completed_jobs",
            "agreed_terms_at", "created_at",
        ]

    def validate_availability_schedule(self, value):
        allowed = {"available_now", "weekdays", "weekends", "custom"}
        if not isinstance(value, list) or not set(value) <= allowed:
            raise serializers.ValidationError(f"Use a list containing any of: {', '.join(sorted(allowed))}.")
        return value

    def validate_preferred_radius(self, value):
        if not 1 <= value <= 50:
            raise serializers.ValidationError("Choose a radius between 1 and 50 km.")
        return value


class EmployerProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = Employer
        fields = [
            "id", "profile_photo", "status", "employer_type", "bio", "address", "location", "latitude",
            "longitude", "preferred_radius", "rating", "review_count", "completed_jobs", "agreed_terms_at",
            "created_at",
        ]
        read_only_fields = ["id", "status", "rating", "review_count", "completed_jobs", "agreed_terms_at", "created_at"]


class MeSerializer(serializers.ModelSerializer):
    worker_profile = serializers.SerializerMethodField()
    employer_profile = serializers.SerializerMethodField()
    verification = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id", "full_name", "phone_number", "email", "email_verified", "role", "phone_verified",
            "worker_profile", "employer_profile", "verification", "created_at",
        ]
        read_only_fields = ["id", "phone_number", "role", "phone_verified", "email_verified", "created_at"]

    def update(self, instance, validated_data):
        if "email" in validated_data and (validated_data["email"] or None) != (instance.email or None):
            instance.email_verified = False
        return super().update(instance, validated_data)

    def get_worker_profile(self, user):
        worker = get_worker(user)
        return WorkerProfileSerializer(worker, context=self.context).data if worker else None

    def get_employer_profile(self, user):
        employer = get_employer(user)
        return EmployerProfileSerializer(employer, context=self.context).data if employer else None

    def get_verification(self, user):
        return verification_summary(user)

    def validate_email(self, value):
        if not value:
            return None
        if User.objects.filter(email__iexact=value).exclude(pk=self.instance.pk).exists():
            raise serializers.ValidationError("An account with this email already exists.")
        return value.lower()


class _DistanceMixin(serializers.Serializer):
    distance_km = serializers.SerializerMethodField()

    def get_distance_km(self, obj):
        origin = self.context.get("origin")
        if not origin:
            return getattr(obj, "distance_km", None)
        return haversine_km(origin[0], origin[1], obj.latitude, obj.longitude)


class PublicWorkerSerializer(_DistanceMixin, serializers.ModelSerializer):
    user_id = serializers.IntegerField(source="user.id", read_only=True)
    full_name = serializers.CharField(source="user.full_name", read_only=True)
    skills = serializers.SlugRelatedField(many=True, read_only=True, slug_field="name")
    is_verified = serializers.BooleanField(read_only=True)

    class Meta:
        model = Worker
        fields = [
            "id", "user_id", "full_name", "profile_photo", "bio", "location", "skills", "experience_level",
            "experience_years", "daily_rate", "availability_status", "rating", "review_count",
            "reliability_score", "completed_jobs", "is_verified", "distance_km", "created_at",
        ]


class PublicEmployerSerializer(_DistanceMixin, serializers.ModelSerializer):
    user_id = serializers.IntegerField(source="user.id", read_only=True)
    full_name = serializers.CharField(source="user.full_name", read_only=True)
    is_verified = serializers.BooleanField(read_only=True)
    jobs_posted = serializers.SerializerMethodField()
    verification = serializers.SerializerMethodField()

    class Meta:
        model = Employer
        fields = [
            "id", "user_id", "full_name", "profile_photo", "employer_type", "bio", "location", "rating",
            "review_count", "completed_jobs", "jobs_posted", "is_verified", "verification", "distance_km",
            "created_at",
        ]

    def get_jobs_posted(self, obj):
        return obj.jobs.count()

    def get_verification(self, obj):
        summary = verification_summary(obj.user)
        from payments.models import Escrow

        return {
            "identity": summary["identity"] == "verified",
            "address": summary["address"] == "verified",
            "payment": Escrow.objects.filter(booking__hiring_request__job__employer=obj).exists(),
        }


class VerificationDocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = VerificationDocument
        fields = ["id", "doc_type", "number", "file", "status", "note", "created_at"]
        read_only_fields = ["id", "status", "note", "created_at"]

    def validate(self, attrs):
        doc_type = attrs.get("doc_type")
        if doc_type == "nin":
            number = (attrs.get("number") or "").replace(" ", "")
            if not (number.isdigit() and len(number) == 11):
                raise serializers.ValidationError({"number": "A NIN is 11 digits."})
            attrs["number"] = number
        elif not attrs.get("file"):
            raise serializers.ValidationError({"file": "Upload a photo or PDF of the document."})
        upload = attrs.get("file")
        if upload and upload.size > 10 * 1024 * 1024:
            raise serializers.ValidationError({"file": "Files must be 10MB or smaller."})
        return attrs


class LocationSerializer(serializers.Serializer):
    latitude = serializers.FloatField(min_value=-90, max_value=90)
    longitude = serializers.FloatField(min_value=-180, max_value=180)
    location = serializers.CharField(max_length=255, required=False, allow_blank=True)
