from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from . import views

urlpatterns = [
    # Original endpoints (kept as-is)
    path("worker_register/", views.WorkerRegistration.as_view(), name="worker_register"),
    path("employer_register/", views.EmployerRegistration.as_view(), name="employer_register"),
    path("auth/login", views.LoginView.as_view(), name="login"),
    # Same login with a trailing slash, plus token refresh
    path("auth/login/", views.LoginView.as_view(), name="login_slash"),
    path("auth/password-reset/", views.PasswordResetRequestView.as_view(), name="password_reset"),
    path("auth/password-reset/confirm/", views.PasswordResetConfirmView.as_view(), name="password_reset_confirm"),
    path("auth/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    # The signed-in user
    path("me/", views.MeView.as_view(), name="me"),
    path("me/worker/", views.WorkerProfileView.as_view(), name="me_worker"),
    path("me/employer/", views.EmployerProfileView.as_view(), name="me_employer"),
    path("me/location/", views.UpdateLocationView.as_view(), name="me_location"),
    path("me/agree-terms/", views.AgreeTermsView.as_view(), name="me_agree_terms"),
    path("me/switch-mode/", views.SwitchModeView.as_view(), name="me_switch_mode"),
    path("me/verification/", views.VerificationDocumentView.as_view(), name="me_verification"),
    path("me/verification/phone/send/", views.SendPhoneOTPView.as_view(), name="phone_otp_send"),
    path("me/verification/phone/verify/", views.VerifyPhoneOTPView.as_view(), name="phone_otp_verify"),
    path("me/verification/email/send/", views.SendEmailCodeView.as_view(), name="email_code_send"),
    path("me/verification/email/verify/", views.VerifyEmailCodeView.as_view(), name="email_code_verify"),
    # Public profiles + search
    path("workers/", views.WorkerSearchView.as_view(), name="worker_search"),
    path("workers/<int:pk>/", views.WorkerDetailView.as_view(), name="worker_detail"),
    path("employers/<int:pk>/", views.EmployerDetailView.as_view(), name="employer_detail"),
]
