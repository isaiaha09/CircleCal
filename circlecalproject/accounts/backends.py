from django.contrib.auth.backends import ModelBackend
from django.contrib.auth import get_user_model


class EmailOrUsernameModelBackend(ModelBackend):
    """Authenticate using either username or email (case-insensitive).

    Intended to be placed before the default ModelBackend in
    `AUTHENTICATION_BACKENDS` so it is used for credential checks.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        UserModel = get_user_model()
        if username is None:
            username = kwargs.get(UserModel.USERNAME_FIELD)

        if username is None or password is None:
            return None

        # Try username exact match first (case-insensitive)
        user_qs = UserModel.objects.filter(username__iexact=username)

        # If no username match and looks like an email, try email lookup
        if not user_qs.exists() and '@' in username:
            user_qs = UserModel.objects.filter(email__iexact=username)

        for user in user_qs:
            try:
                if user.check_password(password) and self.user_can_authenticate(user):
                    return user
            except Exception:
                continue
        return None
