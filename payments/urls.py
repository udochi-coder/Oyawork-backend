from django.urls import path

from payments import views

urlpatterns = [
    path("wallet/", views.WalletView.as_view(), name="wallet"),
    path("wallet/withdraw/", views.WithdrawView.as_view(), name="wallet_withdraw"),
    path("wallet/fund/", views.FundWalletView.as_view(), name="wallet_fund"),
    path("wallet/banks/", views.BanksView.as_view(), name="wallet_banks"),
    path("wallet/resolve-account/", views.ResolveAccountView.as_view(), name="wallet_resolve_account"),
    path("payments/verify/", views.VerifyPaymentView.as_view(), name="payment_verify"),
    path("payments/paystack/webhook/", views.paystack_webhook, name="paystack_webhook"),
]
