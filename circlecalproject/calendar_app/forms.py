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


class InviteSignupForm(UserCreationForm):
    """Signup form for invite flow: do not ask for username, generate one."""
    email = forms.EmailField(required=True, widget=forms.EmailInput(attrs={'readonly': 'readonly'}))

    class Meta:
        model = User
        fields = ("email", "password1", "password2")

    def _generate_username(self, base_email):
        base = slugify(base_email.split('@')[0]) or 'user'
        username = base
        i = 1
        UserModel = get_user_model()
        while UserModel.objects.filter(username__iexact=username).exists():
            i += 1
            username = f"{base}{i}"
        return username

    def save(self, commit=True):
        user = super().save(commit=False)
        # Ensure username exists
        if not getattr(user, 'username', None):
            user.username = self._generate_username(self.cleaned_data.get('email', 'user'))
        user.email = self.cleaned_data.get('email')
        if commit:
            user.save()
        return user


class ContactForm(forms.Form):
    business_name = forms.CharField(max_length=160, required=True, label='Business Name')
    name = forms.CharField(max_length=120, required=True)
    email = forms.EmailField(required=True)

    SUBJECT_CHOICES = [
        ('billing_subscription', 'Billing / Subscription / Stripe'),
        ('booking_link', 'Booking link not working'),
        ('calendar_availability', 'Calendar availability / time slots'),
        ('cancellations_refunds', 'Cancellations / refunds policy'),
        ('notifications_emails', 'Notifications / confirmation emails'),
        ('public_booking', 'Public booking page issue'),
        ('services_setup', 'Services setup / service settings'),
        ('staff_team', 'Staff / team access and roles'),
        ('timezone', 'Timezone or time display issue'),
        ('login_security', 'Login / password / 2FA help'),
        ('bug_report', 'Bug report'),
        ('feature_request', 'Feature request'),
        ('other', 'Other (write your own subject)'),
    ]
    subject = forms.ChoiceField(choices=SUBJECT_CHOICES, required=True)
    other_subject = forms.CharField(max_length=160, required=False, label='Other Subject')
    message = forms.CharField(required=True, widget=forms.Textarea(attrs={
        'rows': 6,
    }))

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('subject') == 'other':
            other = (cleaned.get('other_subject') or '').strip()
            if not other:
                self.add_error('other_subject', 'Please enter a subject.')
            else:
                cleaned['other_subject'] = other
        return cleaned