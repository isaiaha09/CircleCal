from django.urls import path
from two_factor.views import (
    SetupView,
    QRGeneratorView,
    SetupCompleteView,
    ProfileView,
    BackupTokensView,
    DisableView,
)

app_name = "two_factor"

urlpatterns = [
    # Two-factor management under /accounts/two_factor/...
    path("two_factor/setup/", SetupView.as_view(), name="setup"),
    path("two_factor/qrcode/", QRGeneratorView.as_view(), name="qr"),
    path("two_factor/setup/complete/", SetupCompleteView.as_view(), name="setup_complete"),
    path("two_factor/backup/tokens/", BackupTokensView.as_view(), name="backup_tokens"),
    path("two_factor/", ProfileView.as_view(), name="profile"),
    path("two_factor/disable/", DisableView.as_view(), name="disable"),
]
