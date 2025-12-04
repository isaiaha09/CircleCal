from django.shortcuts import render
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from .forms import ProfileForm
from .models import LoginActivity, Membership, Invite
from billing.models import Subscription
from django.views.decorators.http import require_POST
from django.contrib.auth import logout
from django.urls import reverse

# Create your views here.
@login_required
def profile_view(request):
	user = request.user
	from .models import Profile
	profile, _ = Profile.objects.get_or_create(user=user)
	if request.method == "POST":
		# Update user core fields if provided
		username = request.POST.get("username")
		email = request.POST.get("email")
		first_name = request.POST.get("first_name")
		last_name = request.POST.get("last_name")
		if username:
			user.username = username
		if email is not None:
			user.email = email
		if first_name is not None:
			user.first_name = first_name
		if last_name is not None:
			user.last_name = last_name
		user.save()

		form = ProfileForm(request.POST, request.FILES, instance=profile)
		if form.is_valid():
			form.save()
			messages.success(request, "Profile updated successfully.")
			return redirect("accounts:profile")
		else:
			# Surface validation errors to the user
			from django.utils.html import format_html_join
			error_list = []
			for field, errs in form.errors.items():
				for e in errs:
					error_list.append(f"{field}: {e}")
			if error_list:
				messages.error(request, "Please fix the errors below: " + "; ".join(error_list))
	else:
		form = ProfileForm(instance=profile)

	# Recent login activity (last 5)
	activities = LoginActivity.objects.filter(user=user).only('timestamp','ip_address','user_agent')[:5]

	# Billing summary based on current org in request (if any)
	sub = None
	org = getattr(request, 'organization', None)
	if org is not None:
		try:
			sub = Subscription.objects.select_related('plan').get(organization=org)
		except Subscription.DoesNotExist:
			sub = None

	# Team/org info
	memberships = Membership.objects.filter(user=user).select_related('organization')
	pending_invites = Invite.objects.filter(email=user.email, accepted=False).select_related('organization') if user.email else []

	return render(request, "accounts/profile.html", {
		"form": form,
		"user": user,
		"activities": activities,
		"subscription": sub,
		"organization": org,
		"memberships": memberships,
		"pending_invites": pending_invites,
	})


@login_required
@require_POST
def delete_account_view(request):
	# Capture the user before logout
	u = request.user
	# Log the user out first to drop session
	logout(request)
	# Delete the account
	try:
		u.delete()
		messages.success(request, "Your account has been deleted.")
	except Exception:
		messages.error(request, "We couldn't delete your account right now. Please try again.")
	return redirect("calendar_app:home")
