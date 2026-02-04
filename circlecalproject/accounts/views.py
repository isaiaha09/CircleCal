from django.shortcuts import render
from django.shortcuts import render, redirect
from django.db import transaction
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from .forms import ProfileForm, StaffAuthenticationForm
from .models import LoginActivity, Membership, Invite, MobileSSOToken
from billing.models import Subscription
from django.views.decorators.http import require_POST
from django.views.decorators.http import require_GET
from django.views.decorators.http import require_http_methods
from django.contrib.auth import logout
from django.urls import reverse
from two_factor.views import LoginView as TwoFactorLoginView
from django.http import HttpResponseRedirect
from django.contrib.auth import get_user_model
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.contrib.auth import authenticate, login
from django.contrib.auth import get_user_model
from django.conf import settings
from django.http import HttpResponse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.core import signing
from django.views.decorators.csrf import csrf_exempt

# Create your views here.


@require_GET
def mobile_sso_consume_view(request, token: str):
	"""Consume a one-time token and establish a session for WebView usage.

	2FA is enforced during the native login (JWT issuance). This view should not
	prompt for OTP; it only establishes a web session for the already-authenticated
	mobile flow.
	"""
	def _return_to_app(reason: str) -> HttpResponse:
		# The OS auth-session browser may not show/copy full URLs. Give the user a
		# single tap escape hatch back into the native app.
		dl = f"circlecal://stripe-return?status=error&reason={reason}" if reason else "circlecal://stripe-return?status=error"
		html = f"""<!doctype html>
<html lang=\"en\">
<head>
	<meta charset=\"utf-8\" />
	<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
	<title>Return to CircleCal</title>
	<style>
		body{{font-family:system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;margin:0;padding:24px;background:#f8fafc;color:#0f172a;}}
		.card{{max-width:520px;margin:0 auto;background:#fff;border:1px solid #e2e8f0;border-radius:14px;padding:18px;}}
		h1{{font-size:18px;margin:0 0 8px;}}
		p{{margin:0 0 12px;color:#334155;line-height:1.4;}}
		a.btn{{display:inline-block;margin-top:6px;background:#2563eb;color:#fff;text-decoration:none;padding:10px 14px;border-radius:10px;font-weight:700;}}
		.muted{{margin-top:10px;font-size:12px;color:#64748b;}}
	</style>
</head>
<body>
	<div class=\"card\">
		<h1>Return to CircleCal</h1>
		<p>Your login link expired or was already used. Tap below to return to the CircleCal app and sign in again.</p>
		<a class=\"btn\" href=\"{dl}\">Open CircleCal</a>
		<div class=\"muted\">If nothing happens, close this browser window and open the CircleCal app manually.</div>
	</div>
	<script>
		// Best-effort auto-bounce back to the app.
		try {{ window.location.href = {dl!r}; }} catch (e) {{}}
	</script>
</body>
</html>"""
		return HttpResponse(html, content_type='text/html', status=200)

	next_url = (request.GET.get('next') or '/').strip() or '/'
	if not url_has_allowed_host_and_scheme(
		next_url,
		allowed_hosts={request.get_host()},
		require_https=request.is_secure(),
	):
		next_url = '/'

	# Consume token and establish web session.
	now = timezone.now()
	user = None
	# Signed-token fallback: sig_<signed-payload>
	try:
		if isinstance(token, str) and token.startswith('sig_'):
			data = signing.loads(token[4:], salt='cc_mobile_sso', max_age=60 * 60 * 2)
			exp = int((data or {}).get('exp') or 0)
			uid = int((data or {}).get('uid') or 0)
			if (not uid) or (exp and int(now.timestamp()) > exp):
				return _return_to_app('sso_expired')
			User = get_user_model()
			user = User.objects.filter(id=uid).first()
			if user is None:
				return _return_to_app('sso_expired')
		else:
			with transaction.atomic():
				tok = (
					MobileSSOToken.objects.select_for_update()
					.filter(token=token, used_at__isnull=True, expires_at__gte=now)
					.select_related('user')
					.first()
				)
				if tok is None:
					return _return_to_app('sso_expired')
				# Mark used immediately to prevent the token being replayed in another browser.
				tok.used_at = now
				tok.save(update_fields=['used_at'])
				user = getattr(tok, 'user', None)
	except Exception:
		return _return_to_app('sso_expired')
	backend = 'django.contrib.auth.backends.ModelBackend'
	try:
		backend = (getattr(settings, 'AUTHENTICATION_BACKENDS', None) or [backend])[0]
	except Exception:
		backend = 'django.contrib.auth.backends.ModelBackend'

	login(request, user, backend=backend)
	# In app-mode, prefer session cookies that expire when the browser closes.
	# This reduces "sticky" sign-in when the user cancels onboarding.
	try:
		ua = (request.META.get('HTTP_USER_AGENT') or '')
		is_app_ua = 'CircleCalApp' in ua
		cc_app_param = (request.GET.get('cc_app') == '1')
		if is_app_ua or cc_app_param:
			request.session.set_expiry(0)
	except Exception:
		pass

	# Also record that this session is part of a mobile app flow when the
	# SSO link is being used to enter cc_app=1 pages (even if the UA is a
	# system browser auth-session, which won't include CircleCalApp).
	try:
		next_marker = (next_url or '')
		if (request.GET.get('cc_app') == '1') or ('cc_app=1' in next_marker):
			request.session['cc_app_flow'] = True
	except Exception:
		pass
	return redirect(next_url)


@require_GET
def mobile_app_logout_view(request):
	"""Log out the Django session for the native app WebView.

	We intentionally allow GET here (unlike the standard LogoutView POST)
	but restrict it to requests coming from the native app WebView UA marker.
	"""
	ua = (request.META.get('HTTP_USER_AGENT') or '')
	if 'CircleCalApp' not in ua:
		return HttpResponse('Not found', status=404)

	next_url = (request.GET.get('next') or '/').strip() or '/'
	# Allow deep-link redirects back into the native app for this UA-gated endpoint.
	try:
		if next_url.startswith('circlecal://'):
			logout(request)
			return redirect(next_url)
	except Exception:
		pass
	if not url_has_allowed_host_and_scheme(
		next_url,
		allowed_hosts={request.get_host()},
		require_https=request.is_secure(),
	):
		next_url = '/'

	logout(request)
	return redirect(next_url)


@csrf_exempt
@require_POST
def auto_logout_view(request):
	"""Best-effort logout for onboarding cancellation.

	Used by `navigator.sendBeacon()` / fetch(keepalive) on onboarding pages when the
	user closes the tab/app. Logging out is not a security boundary and is safe to
	allow without CSRF.
	"""
	try:
		logout(request)
	except Exception:
		pass
	resp = HttpResponse('', status=204)
	# Clear main session cookies, but leave post_login_redirect in place
	# so a user who logs back in can be routed back to onboarding.
	try:
		resp.delete_cookie(getattr(settings, 'SESSION_COOKIE_NAME', 'sessionid'))
		resp.delete_cookie(getattr(settings, 'CSRF_COOKIE_NAME', 'csrftoken'))
	except Exception:
		pass
	try:
		resp['Cache-Control'] = 'no-store'
	except Exception:
		pass
	return resp
@login_required
def profile_view(request):
	user = request.user
	from .models import Profile
	profile, _ = Profile.objects.get_or_create(user=user)

	# Whether the account has 2FA configured (i.e., at least one confirmed OTP device).
	# Note: request.user.is_verified reflects whether the current session completed OTP,
	# which is not the same as 2FA being enabled on the account.
	two_factor_enabled = False
	try:
		from django_otp import devices_for_user as _devices_for_user
		two_factor_enabled = any(True for _d in _devices_for_user(user, confirmed=True))
	except Exception:
		two_factor_enabled = False

	# Used to keep onboarding resume behavior consistent: if the user has no org yet,
	# we want post-login redirect to return them to Choose Business rather than Profile.
	try:
		has_any_org = Membership.objects.filter(user=user, is_active=True).exists()
	except Exception:
		has_any_org = True

	def _annotate_membership_plan_features(membership_qs):
		"""Attach per-org feature flags used by the Profile UI.

		We intentionally compute this per membership (org), since users can belong
		to multiple businesses with different plans.
		"""
		items = list(membership_qs)
		for m in items:
			org_obj = getattr(m, 'organization', None)
			eligible = False
			try:
				from billing.utils import get_plan_slug, get_subscription, PRO_SLUG, TEAM_SLUG
				sub = get_subscription(org_obj)
				plan_slug = get_plan_slug(org_obj)
				is_trialing = bool(sub and getattr(sub, 'status', '') == 'trialing')
				is_active = True
				try:
					is_active = bool(sub and callable(getattr(sub, 'is_active', None)) and sub.is_active())
				except Exception:
					is_active = True
				eligible = bool((plan_slug in {PRO_SLUG, TEAM_SLUG}) and (not is_trialing) and (sub is not None) and is_active)
			except Exception:
				eligible = False

			# Used by profile.html to enable/disable buttons.
			try:
				m.can_use_embed_widget = eligible
			except Exception:
				pass
			try:
				m.can_use_custom_domain = eligible
			except Exception:
				pass
		return items

	# Business-level offline payment info (owner-only)
	org = getattr(request, 'organization', None)
	is_owner_for_org = False
	try:
		if org is not None:
			is_owner_for_org = bool(
				(getattr(org, 'owner_id', None) == getattr(user, 'id', None))
				or Membership.objects.filter(user=user, organization=org, is_active=True, role='owner').exists()
			)
	except Exception:
		is_owner_for_org = False
	org_offline_venmo = ''
	org_offline_zelle = ''
	can_use_offline_payment_methods = False
	stripe_connected_account_url = None
	stripe_express_dashboard_url = None
	try:
		if org is not None:
			from bookings.models import OrgSettings
			settings_obj, _ = OrgSettings.objects.get_or_create(organization=org)
			org_offline_venmo = (getattr(settings_obj, 'offline_venmo', '') or '').strip()
			org_offline_zelle = (getattr(settings_obj, 'offline_zelle', '') or '').strip()

			# Pro/Team gating for offline payment methods (trial/basic blocked)
			try:
				from billing.utils import can_use_offline_payment_methods as _can_use_offline
				can_use_offline_payment_methods = bool(_can_use_offline(org))
			except Exception:
				can_use_offline_payment_methods = False

			# Stripe connected account link (test vs live)
			try:
				acct_id = getattr(org, 'stripe_connect_account_id', None)
				if acct_id:
					secret = str(getattr(settings, 'STRIPE_SECRET_KEY', '') or '')
					is_test = secret.startswith('sk_test')
					base = 'https://dashboard.stripe.com/test/connect/accounts/' if is_test else 'https://dashboard.stripe.com/connect/accounts/'
					stripe_connected_account_url = base + str(acct_id)
					try:
						stripe_express_dashboard_url = reverse('billing:stripe_express_dashboard', kwargs={'org_slug': org.slug})
					except Exception:
						stripe_express_dashboard_url = None
			except Exception:
				stripe_connected_account_url = None
				stripe_express_dashboard_url = None
	except Exception:
		org_offline_venmo = ''
		org_offline_zelle = ''
		can_use_offline_payment_methods = False
		stripe_connected_account_url = None
		stripe_express_dashboard_url = None

	if request.method == "POST":
		# Enforce First/Last Name requirement.
		first_name_required = (request.POST.get("first_name") or "").strip()
		last_name_required = (request.POST.get("last_name") or "").strip()
		if not first_name_required or not last_name_required:
			# Keep typed values in-memory for re-render.
			try:
				user.first_name = first_name_required
				user.last_name = last_name_required
			except Exception:
				pass
			messages.error(request, "Please enter your First Name and Last Name, then click Save Changes.")
			form = ProfileForm(request.POST, request.FILES, instance=profile)
			# Don't save anything yet.
			activities = LoginActivity.objects.filter(user=user).only('timestamp','ip_address','user_agent')[:5]
			# Billing summary based on current org in request (if any)
			sub = None
			org2 = getattr(request, 'organization', None)
			if org2 is not None:
				try:
					sub = Subscription.objects.select_related('plan').get(organization=org2)
				except Subscription.DoesNotExist:
					sub = None

			# Stripe connect modal context (avoid NameError; do not auto-open here)
			stripe_connect_start_url = None
			stripe_connect_modal_enabled = False
			stripe_connect_modal_auto_open = False
			try:
				stripe_configured = bool(getattr(settings, 'STRIPE_SECRET_KEY', None))
				needs_connect = bool(org2 is not None) and bool(is_owner_for_org) and stripe_configured and not bool(getattr(org2, 'stripe_connect_charges_enabled', False))
				if needs_connect:
					stripe_connect_modal_enabled = True
					stripe_connect_start_url = reverse('billing:stripe_connect_start', kwargs={'org_slug': org2.slug})
					try:
						ua = (request.META.get('HTTP_USER_AGENT') or '')
						is_app = (
							('circlecalapp' in ua.lower())
							or (request.GET.get('cc_app') == '1')
							or (request.COOKIES.get('cc_app') == '1')
							or bool(request.session.get('cc_app_flow'))
						)
						if is_app and stripe_connect_start_url and ('cc_app=1' not in stripe_connect_start_url):
							joiner = '&' if ('?' in stripe_connect_start_url) else '?'
							stripe_connect_start_url = f"{stripe_connect_start_url}{joiner}cc_app=1"
					except Exception:
						pass
			except Exception:
				stripe_connect_modal_enabled = False
				stripe_connect_start_url = None
				stripe_connect_modal_auto_open = False

			return render(request, "accounts/profile.html", {
				"form": form,
				"user": user,
				"two_factor_enabled": two_factor_enabled,
				"activities": activities,
				"subscription": sub,
				"organization": org2,
				"memberships": _annotate_membership_plan_features(Membership.objects.filter(user=user).select_related('organization')),
				"pending_invites": Invite.objects.filter(email=user.email, accepted=False).select_related('organization') if user.email else [],
				"is_owner_for_org": is_owner_for_org,
				"org_offline_venmo": org_offline_venmo,
				"org_offline_zelle": org_offline_zelle,
				"can_use_offline_payment_methods": can_use_offline_payment_methods,
				"stripe_connected_account_url": stripe_connected_account_url,
				"stripe_express_dashboard_url": stripe_express_dashboard_url,
				"stripe_connect_modal_enabled": stripe_connect_modal_enabled,
				"stripe_connect_start_url": stripe_connect_start_url,
				"stripe_connect_modal_auto_open": stripe_connect_modal_auto_open,
			})

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

		# Save org-level offline payment info (owner-only)
		try:
			if org is not None and is_owner_for_org and can_use_offline_payment_methods:
				from bookings.models import OrgSettings
				settings_obj, _ = OrgSettings.objects.get_or_create(organization=org)
				settings_obj.offline_venmo = (request.POST.get('offline_venmo') or '').strip()
				settings_obj.offline_zelle = (request.POST.get('offline_zelle') or '').strip()
				settings_obj.save(update_fields=['offline_venmo', 'offline_zelle'])
				org_offline_venmo = (getattr(settings_obj, 'offline_venmo', '') or '').strip()
				org_offline_zelle = (getattr(settings_obj, 'offline_zelle', '') or '').strip()
		except Exception:
			# Fail open: do not block profile saving
			pass

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

	stripe_connect_start_url = None
	stripe_connect_modal_enabled = False
	stripe_connect_modal_auto_open = False
	try:
		try:
			stripe_connect_modal_auto_open = bool(request.session.pop('cc_auto_open_stripe_connect_modal', False))
		except Exception:
			stripe_connect_modal_auto_open = False

		stripe_configured = bool(getattr(settings, 'STRIPE_SECRET_KEY', None))
		needs_connect = bool(org is not None) and bool(is_owner_for_org) and stripe_configured and not bool(getattr(org, 'stripe_connect_charges_enabled', False))
		if needs_connect:
			stripe_connect_modal_enabled = True
			stripe_connect_start_url = reverse('billing:stripe_connect_start', kwargs={'org_slug': org.slug})
			try:
				ua = (request.META.get('HTTP_USER_AGENT') or '')
				is_app = (
					('circlecalapp' in ua.lower())
					or (request.GET.get('cc_app') == '1')
					or (request.COOKIES.get('cc_app') == '1')
					or bool(request.session.get('cc_app_flow'))
				)
				if is_app and stripe_connect_start_url and ('cc_app=1' not in stripe_connect_start_url):
					joiner = '&' if ('?' in stripe_connect_start_url) else '?'
					stripe_connect_start_url = f"{stripe_connect_start_url}{joiner}cc_app=1"
			except Exception:
				pass
	except Exception:
		stripe_connect_modal_enabled = False
		stripe_connect_start_url = None

	# Team/org info
	memberships = _annotate_membership_plan_features(Membership.objects.filter(user=user).select_related('organization'))
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
		"two_factor_enabled": two_factor_enabled,
		"activities": activities,
		"subscription": sub,
		"organization": org,
		"memberships": memberships,
		"pending_invites": pending_invites,
		"is_owner_for_org": is_owner_for_org,
		"org_offline_venmo": org_offline_venmo,
		"org_offline_zelle": org_offline_zelle,
		"can_use_offline_payment_methods": can_use_offline_payment_methods,
		"stripe_connected_account_url": stripe_connected_account_url,
		"stripe_express_dashboard_url": stripe_express_dashboard_url,
		"stripe_connect_modal_enabled": stripe_connect_modal_enabled,
		"stripe_connect_start_url": stripe_connect_start_url,
		"stripe_connect_modal_auto_open": stripe_connect_modal_auto_open,
	})

	if incomplete:
		try:
			if has_any_org:
				resp.set_cookie('post_login_redirect', request.path, max_age=60*60*24)
			else:
				resp.set_cookie('post_login_redirect', reverse('calendar_app:choose_business'), max_age=60*60*24)
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

		# Allow middleware to show a one-time trial warning/expired message right
		# after login when it has org/subscription context.
		try:
			self.request.session['cc_post_login_check_trial'] = True
		except Exception:
			pass

		# If an owner/admin logs in but Stripe Connect isn't completed yet for their
		# current org, route them to Profile and auto-open the Stripe modal. This
		# ensures they see the message before being sent to Stripe.
		try:
			user = getattr(self.request, 'user', None)
			if user and user.is_authenticated and getattr(settings, 'STRIPE_SECRET_KEY', None):
				mem = Membership.objects.filter(user=user, is_active=True).select_related('organization').first()
				org = getattr(mem, 'organization', None) if mem else None
				if org is not None:
					is_owner_admin = bool(getattr(org, 'owner_id', None) == getattr(user, 'id', None)) or bool(getattr(mem, 'role', None) in ['owner', 'admin'])
					connected = bool(getattr(org, 'stripe_connect_charges_enabled', False)) and bool(getattr(org, 'stripe_connect_account_id', None))
					if is_owner_admin and not connected:
						try:
							self.request.session['cc_auto_open_stripe_connect_modal'] = True
						except Exception:
							pass
						return redirect('accounts:profile')
		except Exception:
			pass

		# If the session contains a pending invite token, attach the user to
		# the invited organization after successful authentication.
		try:
			token = self.request.session.pop('pending_invite', None)
			if token and getattr(self.request, 'user', None) and self.request.user.is_authenticated:
				from .models import Membership
				from accounts.models import Invite
				inv = Invite.objects.filter(token=token, accepted=False, email__iexact=self.request.user.email).first()
				if inv:
					Membership.objects.get_or_create(
						user=self.request.user,
						organization=inv.organization,
						defaults={'role': inv.role}
					)
					inv.accepted = True
					inv.save()
		except Exception:
			# Don't block login on invite processing errors
				pass
		# If a cookie was set for post-login redirect, redirect there.
		redirect_to = self.request.COOKIES.get('post_login_redirect')
		if redirect_to:
			# Remove cookie and redirect
			resp = HttpResponseRedirect(redirect_to)
			resp.delete_cookie('post_login_redirect')
			return resp
		return response

	def done(self, form_list, **kwargs):
		"""Finalize two-factor login and allow post-login hooks.

		two_factor's LoginView uses `done()` as the successful authentication hook
		(not FormView's `form_valid()`), so overrides that need to change the final
		redirect must be implemented here.
		"""
		response = super().done(form_list, **kwargs)
		# Mirror the session flag from form_valid so middleware can run post-login checks.
		try:
			self.request.session['cc_post_login_check_trial'] = True
		except Exception:
			pass
		return response


def login_choice_view(request):
	"""Render a simple page allowing the user to choose Owner vs Staff/Manager/Admin login."""
	return TemplateResponse(request, 'registration/login_choice.html', {})


class StaffLoginView(CustomLoginView):
	"""Login view for staff/managers/GMs only. After successful authentication,
	verify the user has a Membership with role 'manager', 'staff', or 'admin'. If not,
	log them out and show an error message.
	"""

	def done(self, form_list, **kwargs):
		# First complete the normal two-factor flow (logs the user in and returns a redirect)
		response = super().done(form_list, **kwargs)
		user = getattr(self.request, 'user', None)
		if user and user.is_authenticated:
			# Special-case: allow the CircleCal platform superuser to reach /admin from the
			# Staff/Manager login *only* when running inside the installed PWA (no URL bar).
			try:
				is_pwa = (
					(self.request.COOKIES.get('cc_pwa_standalone') == '1')
					or (self.request.POST.get('cc_pwa_standalone') == '1')
				)
			except Exception:
				is_pwa = False
			if getattr(user, 'is_superuser', False):
				if is_pwa:
					admin_prefix = '/' + (getattr(settings, 'ADMIN_PATH', 'admin') or 'admin').strip('/') + '/'
					return redirect(admin_prefix)
				try:
					from django.contrib.auth import logout
					logout(self.request)
				except Exception:
					pass
				from django.urls import reverse
				return redirect(f"{reverse('accounts:login_staff')}?pwa_only=1")

			# Normal staff/manager/admin enforcement
			try:
				from .models import Membership
				has_role = Membership.objects.filter(user=user, role__in=['manager', 'staff', 'admin']).exists()
			except Exception:
				has_role = False
			if not has_role:
				try:
					from django.contrib.auth import logout
					logout(self.request)
				except Exception:
					pass
				return TemplateResponse(self.request, 'registration/login.html', {
					'form': getattr(self, 'get_form_class', lambda: None)(),
					'error': 'This login path is for staff, managers, and GMs only. Use the Owner login if you are an owner.'
				})
		# If super() already returned a redirect (e.g. honoring post_login_redirect cookie),
		# inspect the target. For staff/manager logins we should NOT send them to the
		# generic onboarding `create-business` path; prefer their org dashboard instead.
		from django.http import HttpResponseRedirect
		if isinstance(response, HttpResponseRedirect):
			try:
				from django.urls import reverse
				loc = response.get('Location') or getattr(response, 'url', '')
				# If the redirect points to post_login or the create_business path, override it
				post_login_path = reverse('calendar_app:post_login')
				create_path = reverse('calendar_app:create_business')
				if loc and (loc.endswith(post_login_path) or loc.endswith(create_path) or 'create-business' in str(loc)):
					# fall through to dashboard redirect logic below
					pass
				else:
					return response
			except Exception:
				# If anything goes wrong inspecting the redirect, preserve original response
				return response
		# For staff/manager users, prefer sending them directly to their org dashboard.
		try:
			mem = Membership.objects.filter(user=user, role__in=['manager', 'staff', 'admin'], is_active=True).select_related('organization').first()
			if mem and getattr(mem, 'organization', None):
				return redirect('calendar_app:dashboard', org_slug=mem.organization.slug)
		except Exception:
			pass
		return response

	# Use the staff-specific authentication form so error messages and labels
	# reference email instead of username.
	form_class = StaffAuthenticationForm

	def dispatch(self, request, *args, **kwargs):
		"""Allow staff to submit their email in the username field.

		If the POST contains an email address in `username`, translate it to
		the corresponding user's `username` value so Django's authentication
		backend (which expects `username`) can authenticate correctly.
		"""
		# If the invite token is present on GET (from invite link), persist it
		if request.method == 'GET':
			try:
				token = request.GET.get('pending_invite')
				if token:
					request.session['pending_invite'] = token
			except Exception:
				pass

		if request.method == 'POST':
			try:
				data = request.POST.copy()
				username_val = data.get('username')
				if username_val and '@' in username_val:
					from django.contrib.auth import get_user_model
					User = get_user_model()
					u = User.objects.filter(email__iexact=username_val).first()
					if u:
						data['username'] = u.get_username()
						request.POST = data
			except Exception:
				# Don't block login flow on translation errors; let normal
				# authentication handle failures and surface useful errors.
				pass
		return super().dispatch(request, *args, **kwargs)


@login_required
@require_POST
def delete_account_view(request):
	# Detect mobile app WebView traffic (UA marker).
	try:
		ua = (request.META.get('HTTP_USER_AGENT') or '')
		is_app_mode = bool('CircleCalApp' in ua)
	except Exception:
		is_app_mode = False

	# Verify password was provided and matches
	password = request.POST.get('password')
	u = request.user
	if not password or not u.check_password(password):
		messages.error(request, "Password incorrect. Account not deleted.")
		return redirect('accounts:profile')

	# Disconnect Stripe + delete businesses owned by the user (cascades to related bookings, billing, invites, etc.)
	try:
		from .models import Business
		from .emails import send_account_deleted_email
		owned = list(Business.objects.filter(owner=u))
		business_names = [getattr(b, 'name', '') for b in owned if getattr(b, 'name', '')]
		# Disconnect Stripe in CircleCal (does not delete Stripe account)
		for b in owned:
			try:
				if getattr(b, 'stripe_connect_account_id', None):
					b.stripe_connect_account_id = None
					b.stripe_connect_details_submitted = False
					b.stripe_connect_charges_enabled = False
					b.stripe_connect_payouts_enabled = False
					b.save(update_fields=[
						'stripe_connect_account_id',
						'stripe_connect_details_submitted',
						'stripe_connect_charges_enabled',
						'stripe_connect_payouts_enabled',
					])
			except Exception:
				pass

		# Send email confirmation before deleting user
		try:
			send_account_deleted_email(u, business_names=business_names)
		except Exception:
			pass

		for b in owned:
			try:
				b.delete()
			except Exception:
				# Continue deleting others even if one fails
				pass
	except Exception:
		# If import or deletion fails, continue to attempt user deletion
		pass

	# Log the user out first to drop session
	logout(request)
	# Delete the account
	try:
		u.delete()
		messages.success(request, "Your account has been deleted.")
	except Exception:
		messages.error(request, "We couldn't delete your account right now. Please try again.")

	# In the mobile app, redirect to the app-logout endpoint so native can sign out and show a toast.
	if is_app_mode:
		try:
			return redirect(reverse('accounts:mobile_logout') + '?next=/&cc_flash=deleted')
		except Exception:
			return redirect('/')

	return redirect("calendar_app:home")


@login_required
@require_POST
def deactivate_account_view(request):
	# Detect mobile app WebView traffic (UA marker).
	try:
		ua = (request.META.get('HTTP_USER_AGENT') or '')
		is_app_mode = bool('CircleCalApp' in ua)
	except Exception:
		is_app_mode = False

	# Require current password to confirm
	password = request.POST.get('password')
	u = request.user
	if not password or not u.check_password(password):
		messages.error(request, "Password incorrect. Account not deactivated.")
		return redirect('accounts:profile')

	# Soft-deactivate: set is_active to False and logout
	try:
		# Delete profile picture on deactivation to avoid retaining storage.
		try:
			from .models import Profile
			p = Profile.objects.filter(user=u).first()
			if p and getattr(p, 'avatar', None):
				try:
					# Deletes from storage (Cloudinary/GCS/local) and clears DB field.
					p.avatar.delete(save=True)
				except Exception:
					pass
		except Exception:
			pass

		# Disconnect Stripe in CircleCal for businesses owned by this user
		try:
			from .models import Business
			from .emails import send_account_deactivated_email
			owned = list(Business.objects.filter(owner=u))
			business_names = [getattr(b, 'name', '') for b in owned if getattr(b, 'name', '')]
			for b in owned:
				try:
					if getattr(b, 'stripe_connect_account_id', None):
						b.stripe_connect_account_id = None
						b.stripe_connect_details_submitted = False
						b.stripe_connect_charges_enabled = False
						b.stripe_connect_payouts_enabled = False
						b.save(update_fields=[
							'stripe_connect_account_id',
							'stripe_connect_details_submitted',
							'stripe_connect_charges_enabled',
							'stripe_connect_payouts_enabled',
						])
				except Exception:
					pass

			# Send email confirmation
			try:
				send_account_deactivated_email(u, business_names=business_names)
			except Exception:
				pass
		except Exception:
			pass

		u.is_active = False
		u.save()
		logout(request)
		messages.success(request, "Your account has been deactivated. If you had connected Stripe in CircleCal, it has been disconnected here (your Stripe account itself is not deleted). To permanently delete your Stripe account, do that directly in Stripe.")
	except Exception:
		messages.error(request, "We couldn't deactivate your account right now. Please try again.")

	# In the mobile app, redirect to the app-logout endpoint so native can sign out and show a toast.
	# Reactivation for deactivated accounts is web-only.
	if is_app_mode:
		try:
			return redirect(reverse('accounts:mobile_logout') + '?next=/&cc_flash=deactivated')
		except Exception:
			return redirect('/')

	return redirect('calendar_app:home')


@login_required
def deactivate_confirm_view(request):
	# Render a confirmation page explaining consequences and a password form
	return TemplateResponse(request, 'accounts/deactivate_confirm.html', {})


@login_required
def delete_confirm_view(request):
	# Compute counts of items that will be removed to provide clearer messaging
	u = request.user
	deletable_items = []
	try:
		from .models import Business, Membership, Invite, Profile, LoginActivity
		from bookings.models import Booking, Service
		from billing.models import InvoiceMeta, PaymentMethod

		owned_businesses = Business.objects.filter(owner=u)
		owned_business_count = owned_businesses.count()
		services_count = Service.objects.filter(organization__in=owned_businesses).count() if owned_business_count else 0
		bookings_count = Booking.objects.filter(organization__in=owned_businesses).count() if owned_business_count else 0
		memberships_count = Membership.objects.filter(user=u).count()
		invites_count = Invite.objects.filter(email__iexact=u.email).count() if u.email else 0
		invoices_count = InvoiceMeta.objects.filter(organization__in=owned_businesses).count() if owned_business_count else 0
		payment_methods_count = PaymentMethod.objects.filter(organization__in=owned_businesses).count() if owned_business_count else 0
		audit_count = LoginActivity.objects.filter(user=u).count()
		profile = None
		try:
			profile = Profile.objects.filter(user=u).first()
		except Exception:
			profile = None

		# Build list of (description, count or None)
		deletable_items = [
			("Your user account and profile", None),
			("Profile picture and uploaded media", 1 if getattr(profile, 'avatar', None) else 0),
			("Businesses you own (includes services, availability and settings)", owned_business_count),
			("Services under your businesses", services_count),
			("Bookings and calendar events tied to your businesses", bookings_count),
			("Team memberships and invites", memberships_count + invites_count),
			("Invoices, subscriptions and payment methods tied to your businesses", invoices_count + payment_methods_count),
			("Audit logs and login activity", audit_count),
			("Any local cached billing metadata (applied discounts, invoice metadata)", None),
		]
	except Exception:
		# If anything fails, fall back to a generic list
		deletable_items = [
			("Your user account and profile", None),
			("Profile picture and uploaded media", None),
			("Any Businesses you own (and their services, bookings, availability and settings)", None),
			("Team memberships and invites", None),
			("Bookings and calendar events tied to your businesses", None),
			("Invoices, subscriptions and payment methods tied to your businesses", None),
			("Audit logs and login activity", None),
		]

	return TemplateResponse(request, 'accounts/delete_confirm.html', {"deletable_items": deletable_items})


@require_POST
def reactivate_account_action(request):
	# Accepts POST with `email` and `password` and reactivates the account if credentials valid
	email = request.POST.get('email') or request.POST.get('username')
	password = request.POST.get('password')
	if not email or not password:
		# Render page with inline error so user sees it immediately
		return TemplateResponse(request, 'accounts/reactivate.html', {
			'error': 'Please provide email and password to reactivate your account.',
			'email': email or '',
		})

	User = get_user_model()
	try:
		user = User.objects.filter(email__iexact=email).first()
	except Exception:
		user = None

	if not user or not user.check_password(password):
		# Render with inline error and preserve entered email
		return TemplateResponse(request, 'accounts/reactivate.html', {
			'error': 'Invalid email or password. Please try again.',
			'email': email,
		})

	# Reactivate and log the user in.
	# Prefer using `authenticate()` so the returned user has a `backend` set.
	auth_user = None
	try:
		# First try authenticating directly with the provided email value
		auth_user = authenticate(request, username=email, password=password)
	except Exception:
		auth_user = None

	if not auth_user:
		# If the app uses username internally, try authenticating with that
		try:
			User = get_user_model()
			lookup = User.objects.filter(email__iexact=email).first()
			if lookup:
				try:
					auth_user = authenticate(request, username=lookup.username, password=password)
				except Exception:
					auth_user = None
		except Exception:
			auth_user = None

	# If authenticate() didn't return a user (multiple backends may require explicit backend),
	# fall back to setting the first configured backend on the retrieved user object.
	if not auth_user:
		backend_path = None
		try:
			backend_path = settings.AUTHENTICATION_BACKENDS[0]
		except Exception:
			backend_path = None
		if backend_path:
			user.backend = backend_path
			auth_user = user
		else:
			auth_user = user

	user.is_active = True
	user.save()
	login(request, auth_user)
	messages.success(request, 'Your account has been reactivated.')
	return redirect('accounts:profile')


def reactivate_account_view(request):
	# Show form to submit email + password to self-reactivate
	return TemplateResponse(request, 'accounts/reactivate.html', {})
