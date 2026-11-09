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
from core.utils import haversine_km, normalize_phone, send_email, send_sms, sms_enabled
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


def _find_account(identifier):
    """Look an account up by phone number or email address."""
    identifier = (identifier or "").strip()
    if "@" in identifier:
        return User.objects.filter(email__iexact=identifier, is_active=True).first()
    return User.objects.filter(phone_number=normalize_phone(identifier), is_active=True).first()


def _new_code(user, purpose, target=""):
    code = f"{secrets.randbelow(10**6):06d}"
    PhoneOTP.objects.filter(user=user, purpose=purpose, used=False).update(used=True)
    PhoneOTP.objects.create(user=user, purpose=purpose, target=target, code_hash=_hash_code(user.id, code),
                            expires_at=timezone.now() + timedelta(minutes=10))
    return code


class SendEmailCodeView(APIView):
    """Email a 6-digit code to confirm an email address (it becomes the account email once confirmed)."""

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "otp"

    def post(self, request):
        from django.core.validators import validate_email
        from django.core.exceptions import ValidationError as DjangoValidationError

        user = request.user
        email = (request.data.get("email") or user.email or "").strip().lower()
        try:
            validate_email(email)
        except DjangoValidationError:
            return Response({"email": ["Enter a valid email address."]}, status=400)
        if User.objects.filter(email__iexact=email).exclude(pk=user.pk).exists():
            return Response({"email": ["An account with this email already exists."]}, status=400)
        code = _new_code(user, "email", target=email)
        sent = send_email(email, f"Your OyaWork code: {code}",
                          f"Hi {user.full_name.split()[0] if user.full_name else ''},\n\n"
                          f"Your OyaWork email verification code is {code}. It expires in 10 minutes.\n\n"
                          "If you didn't ask for this, you can ignore this email.")
        body = {"sent": sent, "email": email}
        if not sent and settings.DEMO_OTP:
            body["demo_code"] = code
        elif not sent:
            return Response({"error": "We couldn't send the email. Please try again shortly."}, status=503)
        return Response(body)


class VerifyEmailCodeView(APIView):
    def post(self, request):
        user = request.user
        code = str(request.data.get("code", "")).strip()
        otp = PhoneOTP.objects.filter(user=user, purpose="email", used=False, expires_at__gt=timezone.now()).first()
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
        if User.objects.filter(email__iexact=otp.target).exclude(pk=user.pk).exists():
            return Response({"error": "An account with this email already exists."}, status=400)
        otp.used = True
        otp.save(update_fields=["used"])
        user.email = otp.target
        user.email_verified = True
        user.save(update_fields=["email", "email_verified", "updated_at"])
        return Response(MeSerializer(user, context={"request": request}).data)


class PasswordResetRequestView(APIView):
    """Step 1 of 'Forgot password': send a 6-digit code by SMS (when Termii is set up) or to the
    account's email (when Brevo is set up). The person can enter their phone number or email.

    Always answers the same way whether or not an account exists, so the endpoint can't be used
    to find out who is registered."""

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "otp"

    def post(self, request):
        identifier = request.data.get("identifier") or request.data.get("email") or request.data.get("phone_number")
        body = {"sent": True, "detail": "If this matches an OyaWork account, we've sent it a reset code."}
        user = _find_account(identifier)
        if not user:
            return Response(body)
        code = _new_code(user, "reset")
        message = (f"Your OyaWork password reset code is {code}. It expires in 10 minutes. "
                   "If you didn't ask for this, ignore this message.")
        sent = False
        by_email = "@" in (identifier or "")
        if not by_email and sms_enabled():
            sent = send_sms(user.phone_number, message)
        if not sent and user.email:
            sent = send_email(user.email, f"Reset your OyaWork password: {code}",
                              f"Hi {user.full_name.split()[0] if user.full_name else ''},\n\n{message}")
        if not sent and settings.DEMO_OTP:
            body["demo_code"] = code
        elif not sent:
            logger.warning(f"Password reset code for user_id={user.id} could not be delivered (no SMS/email channel)")
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

        identifier = request.data.get("identifier") or request.data.get("email") or request.data.get("phone_number")
        code = str(request.data.get("code", "")).strip()
        password = request.data.get("new_password") or ""
        invalid = Response({"code": ["That code is not correct or has expired."]}, status=400)
        user = _find_account(identifier)
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
        user.save(update_fields=["password", "updated_at"])
        logger.info(f"Password reset for user_id={user.id}")
        return Response({**tokens_for(user), "user": MeSerializer(user, context={"request": request}).data})
