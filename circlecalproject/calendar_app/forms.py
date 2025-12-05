from django import forms
from accounts.models import Business as Organization
from django.utils.text import slugify
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm



class OrganizationCreateForm(forms.ModelForm):
    """
    Simple org-create form:
    - user enters name
    - slug auto-generated from name
    - ensures slug uniqueness
    """
    # Common curated timezone choices for the organization form
    COMMON_TIMEZONES = [
        'America/New_York', 'America/Chicago', 'America/Denver', 'America/Los_Angeles',
        'America/Phoenix', 'America/Anchorage', 'Pacific/Honolulu',
        'Europe/London', 'Europe/Paris', 'Europe/Berlin',
        'Asia/Tokyo', 'Asia/Shanghai', 'Asia/Dubai',
        'Australia/Sydney', 'UTC'
    ]

    timezone = forms.ChoiceField(choices=[(t, t) for t in COMMON_TIMEZONES], required=True, initial='UTC', label='Timezone')

    class Meta:
        model = Organization
        fields = ["name", "timezone"]

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if not name:
            raise forms.ValidationError("Organization name is required.")
        return name

    def clean(self):
        cleaned = super().clean()
        name = cleaned.get("name")
        if not name:
            return cleaned

        base_slug = slugify(name)
        slug = base_slug
        i = 1

        # ensure unique slug
        while Organization.objects.filter(slug=slug).exists():
            i += 1
            slug = f"{base_slug}-{i}"

        cleaned["slug"] = slug
        return cleaned
    


User = get_user_model()


class SignupForm(UserCreationForm):
    email = forms.EmailField(required=True)

    class Meta:
        model = User
        fields = ("username", "email", "password1", "password2")