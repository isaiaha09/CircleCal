from django.shortcuts import render
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from .forms import ProfileForm
from .models import LoginActivity, Membership, Invite
from billing.models import Subscription

from django.contrib.auth import logout
from django.urls import reverse
from two_factor.views import LoginView as TwoFactorLoginView
from django.http import HttpResponseRedirect

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


	# If profile is incomplete, set a cookie so that a logout before finishing
	# will return the user to this profile page after they log back in.
	try:
		incomplete = not (profile.avatar or (profile.timezone and profile.timezone != 'UTC'))
	except Exception:
		incomplete = True

	resp = render(request, "accounts/profile.html", {
		"form": form,
		"user": user,
		"activities": activities,
		"subscription": sub,
		"organization": org,
		"memberships": memberships,
		"pending_invites": pending_invites,
	})

	if incomplete:
		try:
			resp.set_cookie('post_login_redirect', request.path, max_age=60*60*24)
		except Exception:
			pass

	# If user submitted the form and it was valid we may want to redirect
	# to the dashboard (if they have a business) rather than stay on profile.
	# However the view above already handles POST behavior and redirects on success.
	return resp


class CustomLoginView(TwoFactorLoginView):
	"""Wrap the two-factor login view to honor a `post_login_redirect` cookie.

	If the cookie exists, after successful authentication redirect the user
	to that path and remove the cookie.
	"""

	def form_valid(self, form):
		# Default response from TwoFactorLoginView
		response = super().form_valid(form)
		# If a cookie was set for post-login redirect, redirect there.
		redirect_to = self.request.COOKIES.get('post_login_redirect')
		if redirect_to:
			# Remove cookie and redirect
			resp = HttpResponseRedirect(redirect_to)
			resp.delete_cookie('post_login_redirect')
			return resp
		return response


@login_required
def delete_account_view(request):
	# For backward compatibility the old endpoint performed a hard delete.
	# New behavior: archive owned businesses then delete the user row.
	u = request.user
	# Show a confirmation page listing businesses owned by the user
	from .models import Business
	owned = Business.objects.filter(owner=u, is_archived=False)

	if request.method == 'GET':
		return render(request, 'accounts/account_confirm_delete.html', {'owned': owned})

	# POST -> perform archive of businesses and delete account after password check
	password = request.POST.get('password', '')
	if not u.check_password(password):
		# Render the confirmation page with an inline error (do not set a site-wide message)
		return render(request, 'accounts/account_confirm_delete.html', {
			'owned': owned,
			'error': 'Password incorrect. Please try again.'
		}, status=400)

	# Archive businesses owned by this user to preserve business data
	try:
		Business.objects.filter(owner=u).update(is_archived=True)
	except Exception:
		return render(request, 'accounts/account_confirm_delete.html', {
			'owned': owned,
			'error': "We couldn't archive your businesses. Please try again later."
		}, status=500)

	# Logout and delete the user
	logout(request)
	try:
		u.delete()
	except Exception:
		# If deletion failed, render confirmation with error rather than setting messages
		return render(request, 'accounts/account_confirm_delete.html', {
			'owned': owned,
			'error': "We couldn't delete your account right now. Please try again."
		}, status=500)

	# Successful deletion: redirect to home without a site-wide message
	return redirect('calendar_app:home')


@login_required
def deactivate_account_view(request):
	u = request.user
	if request.method == 'GET':
		return render(request, 'accounts/account_confirm_deactivate.html')

	# POST -> require password then deactivate
	password = request.POST.get('password', '')
	if not u.check_password(password):
		# Render confirmation with inline error
		return render(request, 'accounts/account_confirm_deactivate.html', {
			'error': 'Password incorrect. Please try again.'
		}, status=400)

	u.is_active = False
	u.save()
	logout(request)

	# Redirect to home without a site-wide message
	return redirect('calendar_app:home')
