import hashlib
import secrets
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from loguru import logger
from rest_framework import generics, status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView

from accounts.models import Employer, VerificationDocument, Worker
from accounts.serializers import (
    CustomTokenObtainSerializer,
    EmployerProfileSerializer,
    LocationSerializer,
    MeSerializer,
    PublicEmployerSerializer,
    PublicWorkerSerializer,
    RegistrationSerializer,
    VerificationDocumentSerializer,
    WorkerProfileSerializer,
)
from accounts.services import ensure_profile, get_employer, get_worker, maybe_activate_profiles, profiles_of
from core.constants import ROLE_EMPLOYER, ROLE_WORKER
from core.models import PhoneOTP, User
from core.permissions import IsEmployer, IsWorker
from core.utils import haversine_km, normalize_phone, send_sms
from payments.models import Wallet


def tokens_for(user):
    refresh = RefreshToken.for_user(user)
    return {"refresh": str(refresh), "access": str(refresh.access_token)}


class _RegistrationView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"
    role = None

    def post(self, request, *args, **kwargs):
        serializer = RegistrationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        photo = data.pop("profile_photo", None)

        with transaction.atomic():
            user = User.objects.create_user(
                phone_number=data["phone_number"],
                email=data.get("email"),
                password=data["password"],
                full_name=data["full_name"].strip(),
                role=self.role,
            )
            model = Worker if self.role == ROLE_WORKER else Employer
            profile = model.objects.create(user=user, profile_photo=photo)
            Wallet.objects.get_or_create(user=user)

        logger.info(f"{self.role.title()} registered user_id={user.id}")
        return Response(
            {**tokens_for(user), "user": MeSerializer(user, context={"request": request}).data},
            status=status.HTTP_201_CREATED,
        )


class WorkerRegistration(_RegistrationView):
    role = ROLE_WORKER


class EmployerRegistration(_RegistrationView):
    role = ROLE_EMPLOYER


class LoginView(TokenObtainPairView):
    permission_classes = [AllowAny]
    serializer_class = CustomTokenObtainSerializer
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"


class MeView(generics.RetrieveUpdateAPIView):
    serializer_class = MeSerializer

    def get_object(self):
        return self.request.user


class WorkerProfileView(generics.RetrieveUpdateAPIView):
    serializer_class = WorkerProfileSerializer
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get_object(self):
        worker = get_worker(self.request.user)
        if worker is None:
            from rest_framework.exceptions import NotFound

            raise NotFound("You don't have a worker profile yet. Switch to Worker mode to create one.")
        return worker


class EmployerProfileView(generics.RetrieveUpdateAPIView):
    serializer_class = EmployerProfileSerializer
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get_object(self):
        employer = get_employer(self.request.user)
        if employer is None:
            from rest_framework.exceptions import NotFound

            raise NotFound("You don't have an employer profile yet. Switch to Employer mode to create one.")
        return employer


class UpdateLocationView(APIView):
    """Save the device location on every profile the user has (used for matching and distance)."""

    def post(self, request):
        serializer = LocationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        lat = Decimal(str(round(serializer.validated_data["latitude"], 6)))
        lng = Decimal(str(round(serializer.validated_data["longitude"], 6)))
        label = serializer.validated_data.get("location")
        for profile in profiles_of(request.user):
            profile.latitude, profile.longitude = lat, lng
            fields = ["latitude", "longitude", "updated_at"]
            if label:
                profile.location = label
                fields.append("location")
            profile.save(update_fields=fields)
        return Response({"latitude": lat, "longitude": lng, "location": label})


class AgreeTermsView(APIView):
    """Record acceptance of the Worker / Employer agreement for the current mode."""

    def post(self, request):
        if not request.data.get("agree"):
            return Response({"error": "You need to tick the agreement to continue."}, status=400)
        profile = get_worker(request.user) if request.user.role == ROLE_WORKER else get_employer(request.user)
        if profile is None:
            return Response({"error": "No profile for the current mode."}, status=400)
        profile.agreed_terms_at = timezone.now()
        profile.save(update_fields=["agreed_terms_at", "updated_at"])
        return Response(MeSerializer(request.user, context={"request": request}).data)


class SwitchModeView(APIView):
    """Switch between Worker and Employer mode, creating the other profile the first time."""

    def post(self, request):
        role = request.data.get("role")
        if role not in (ROLE_WORKER, ROLE_EMPLOYER):
            return Response({"error": "role must be 'worker' or 'employer'."}, status=400)
        with transaction.atomic():
            ensure_profile(request.user, role)
            request.user.role = role
            request.user.save(update_fields=["role", "updated_at"])
        return Response(MeSerializer(request.user, context={"request": request}).data)


class VerificationDocumentView(generics.ListCreateAPIView):
    serializer_class = VerificationDocumentSerializer
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get_queryset(self):
        return VerificationDocument.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        doc = serializer.save(user=self.request.user)
        # A fresh submission replaces older pending ones of the same kind.
        VerificationDocument.objects.filter(
            user=self.request.user, doc_type=doc.doc_type, status="pending"
        ).exclude(pk=doc.pk).delete()
        maybe_activate_profiles(self.request.user)

    def create(self, request, *args, **kwargs):
        response = super().create(request, *args, **kwargs)
        response.data["me"] = MeSerializer(request.user, context={"request": request}).data
        return response


def _hash_code(user_id, code):
    return hashlib.sha256(f"{user_id}:{code}:{settings.SECRET_KEY}".encode()).hexdigest()


class SendPhoneOTPView(APIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "otp"

    def post(self, request):
        user = request.user
        code = f"{secrets.randbelow(10**6):06d}"
        PhoneOTP.objects.filter(user=user, purpose="verify", used=False).update(used=True)
        PhoneOTP.objects.create(user=user, purpose="verify", code_hash=_hash_code(user.id, code), expires_at=timezone.now() + timedelta(minutes=10))
        sent = send_sms(user.phone_number, f"Your OyaWork verification code is {code}. It expires in 10 minutes.")
        body = {"sent": sent, "phone_number": user.phone_number}
        if not sent and settings.DEMO_OTP:
            body["demo_code"] = code
        elif not sent:
            return Response({"error": "We couldn't send the SMS. Please try again shortly."}, status=503)
        return Response(body)


class VerifyPhoneOTPView(APIView):
    def post(self, request):
        user = request.user
        code = str(request.data.get("code", "")).strip()
        otp = PhoneOTP.objects.filter(user=user, purpose="verify", used=False, expires_at__gt=timezone.now()).first()
        if not otp:
            return Response({"error": "The code has expired. Request a new one."}, status=400)
        if otp.attempts >= 5:
            otp.used = True
            otp.save(update_fields=["used"])
            return Response({"error": "Too many attempts. Request a new code."}, status=400)
        if not secrets.compare_digest(otp.code_hash, _hash_code(user.id, code)):
            otp.attempts += 1
            otp.save(update_fields=["attempts"])
            return Response({"error": "That code is not correct."}, status=400)
        otp.used = True
        otp.save(update_fields=["used"])
        user.phone_verified = True
        user.save(update_fields=["phone_verified", "updated_at"])
        return Response(MeSerializer(user, context={"request": request}).data)


def _origin_for(request, profile):
    lat, lng = request.query_params.get("lat"), request.query_params.get("lng")
    try:
        if lat and lng:
            return float(lat), float(lng)
    except ValueError:
        pass
    if profile and profile.latitude is not None:
        return float(profile.latitude), float(profile.longitude)
    return None


class WorkerSearchView(generics.ListAPIView):
    """Employer 'Find Nearby Workers' / swipe deck.

    Query params: skill, category, q, lat, lng, radius (km), available=1
    """

    serializer_class = PublicWorkerSerializer
    permission_classes = [IsEmployer]

    def get_queryset(self):
        params = self.request.query_params
        qs = Worker.objects.filter(status="active", user__is_active=True).exclude(user=self.request.user)
        qs = qs.select_related("user").prefetch_related("skills")
        if params.get("available", "1") == "1":
            qs = qs.filter(availability_status="available")
        if params.get("skill"):
            qs = qs.filter(skills__name__iexact=params["skill"])
        if params.get("category"):
            qs = qs.filter(skills__category__name__iexact=params["category"])
        if params.get("q"):
            term = params["q"]
            qs = qs.filter(Q(user__full_name__icontains=term) | Q(skills__name__icontains=term) | Q(bio__icontains=term))
        return qs.distinct()

    def list(self, request, *args, **kwargs):
        employer = get_employer(request.user)
        origin = _origin_for(request, employer)
        radius = float(request.query_params.get("radius") or (employer.preferred_radius if employer else 10))
        workers = list(self.get_queryset()[:300])
        results = []
        for worker in workers:
            worker.distance_km = haversine_km(*(origin or (None, None)), worker.latitude, worker.longitude)
            # Workers without a saved location are still shown (at the end) so a new city isn't empty.
            if worker.distance_km is not None and worker.distance_km > max(radius, worker.preferred_radius):
                continue
            results.append(worker)
        results.sort(key=lambda w: (w.distance_km is None, w.distance_km or 0, -float(w.rating)))
        data = PublicWorkerSerializer(results[:50], many=True, context={"request": request}).data
        return Response(data)


class WorkerDetailView(generics.RetrieveAPIView):
    serializer_class = PublicWorkerSerializer
    queryset = Worker.objects.select_related("user").prefetch_related("skills")

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        me = get_employer(self.request.user) or get_worker(self.request.user)
        origin = _origin_for(self.request, me)
        if origin:
            ctx["origin"] = origin
        return ctx


class EmployerDetailView(generics.RetrieveAPIView):
    serializer_class = PublicEmployerSerializer
    queryset = Employer.objects.select_related("user")

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        me = get_worker(self.request.user) or get_employer(self.request.user)
        origin = _origin_for(self.request, me)
        if origin:
            ctx["origin"] = origin
        return ctx


class PasswordResetRequestView(APIView):
    """Step 1 of 'Forgot password': text a 6-digit code to the phone number.

    Always answers the same way whether or not the number has an account, so the
    endpoint can't be used to find out who is registered."""

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "otp"

    def post(self, request):
        phone = normalize_phone(request.data.get("phone_number"))
        body = {"sent": True, "phone_number": phone,
                "detail": "If this number has an OyaWork account, we've sent it a reset code."}
        user = User.objects.filter(phone_number=phone, is_active=True).first()
        if not user:
            return Response(body)
        code = f"{secrets.randbelow(10**6):06d}"
        PhoneOTP.objects.filter(user=user, purpose="reset", used=False).update(used=True)
        PhoneOTP.objects.create(user=user, purpose="reset", code_hash=_hash_code(user.id, code),
                                expires_at=timezone.now() + timedelta(minutes=10))
        sent = send_sms(user.phone_number, f"Your OyaWork password reset code is {code}. It expires in 10 minutes. "
                                           "If you didn't ask for this, ignore this message.")
        if not sent and settings.DEMO_OTP:
            body["demo_code"] = code
        elif not sent:
            return Response({"error": "We couldn't send the SMS. Please try again shortly."}, status=503)
        return Response(body)


class PasswordResetConfirmView(APIView):
    """Step 2: check the code, set the new password and log the person in."""

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"

    def post(self, request):
        from django.contrib.auth.password_validation import validate_password
        from django.core.exceptions import ValidationError as DjangoValidationError

        phone = normalize_phone(request.data.get("phone_number"))
        code = str(request.data.get("code", "")).strip()
        password = request.data.get("new_password") or ""
        invalid = Response({"code": ["That code is not correct or has expired."]}, status=400)
        user = User.objects.filter(phone_number=phone, is_active=True).first()
        if not user:
            return invalid
        otp = PhoneOTP.objects.filter(user=user, purpose="reset", used=False, expires_at__gt=timezone.now()).first()
        if not otp or otp.attempts >= 5:
            return invalid
        if not secrets.compare_digest(otp.code_hash, _hash_code(user.id, code)):
            otp.attempts += 1
            otp.save(update_fields=["attempts"])
            return invalid
        try:
            validate_password(password, user)
        except DjangoValidationError as exc:
            return Response({"new_password": list(exc.messages)}, status=400)
        otp.used = True
        otp.save(update_fields=["used"])
        user.set_password(password)
        # Receiving the SMS proves they own the number.
        user.phone_verified = True
        user.save(update_fields=["password", "phone_verified", "updated_at"])
        logger.info(f"Password reset for user_id={user.id}")
        return Response({**tokens_for(user), "user": MeSerializer(user, context={"request": request}).data})
