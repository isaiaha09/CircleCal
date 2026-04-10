# CircleCal

CircleCal is a scheduling and booking platform I built around a simple goal: make it easier for a business to manage appointments, services, staff, and customer bookings without needing a bunch of disconnected tools.

This project grew into a full-stack app with:

- a Django web platform for the main product and admin workflows
- a React Native / Expo mobile app for on-the-go business use
- Stripe-powered billing and payment onboarding
- organization and role-aware access control
- public booking pages, embedded booking flows, and branded domain support

I would describe the codebase as practical and product-focused. I did not try to build a giant enterprise framework from day one. I started with the core booking workflow and then kept layering in the features that a real scheduling business app needs.

## What the app does

At a high level, CircleCal lets a business:

- create a public-facing booking experience
- define services and scheduling rules
- manage staff and team roles
- track bookings and booking changes
- handle subscriptions and payment setup
- support both web and mobile workflows

For customers, the main experience is booking a service through a clean public page.

For business users, the main experience is managing availability, appointments, services, team members, billing, and booking operations from a dashboard-style interface.

## Why I built it this way

When I started putting this project together, I wanted the app to feel straightforward on the surface but still have enough backend structure to support growth.

Some examples of decisions I made while building it:

- I used Django because it let me move quickly on forms, auth, admin, and data models.
- I kept the public booking pages server-rendered so core booking flows stay fast and easy to control.
- I added API endpoints for the mobile app instead of trying to force the whole product into one frontend.
- I leaned on Django admin and then customized it so I could manage the system without building every internal screen from scratch.
- I added role and organization awareness because a booking app stops being useful pretty quickly if every user can see and change everything.

I am not presenting this as a perfect architecture. It is more accurate to say this is a real project that evolved as features became necessary.

## Main features

### Booking and scheduling

- Public booking pages by business and service
- Service-specific booking flows
- Availability rules and schedule customization
- Buffers, overrides, busy-time blocking, and service constraints
- Customer cancellation and rescheduling flows
- Calendar invite support through `.ics` generation
- Booking audit history, exports, and undo tools

### Multi-business and team support

- Organization/business model with per-organization membership
- Roles for owner, admin, manager, and staff
- Team and team membership support
- Organization-scoped dashboards and workflows
- Logic to route users to the correct workspace after sign-in

### Billing and payments

- Plan and subscription models
- Stripe subscription support
- Stripe Connect onboarding for business payout/payment flows
- Payment status tracking on bookings
- Invoice metadata and invoice action logging
- Support for offline payment instructions where applicable

### Branding and customization

- Business-specific booking pages
- Embeddable booking widgets
- Hosted booking subdomains
- Custom domain support with verification fields and Cloudflare integration hooks
- Tailwind-based styling for the web UI
- Branded authentication and password reset templates
- Admin theme customization through Unfold

### Web and mobile product support

- Django web app for full business management
- Expo / React Native mobile app
- API endpoints under `api/v1/` for mobile and web clients
- JWT-ready authentication support for mobile
- Mobile SSO token flow for WebView handoff into Django sessions
- Expo push notification token storage and backend support
- PWA assets and offline fallback support on the web side

## Backend and security notes

This is one of the areas I spent more time on because scheduling apps deal with real customer data, user roles, and business-specific records.

### Authentication and authorization

- Django authentication is the base layer for the web app.
- Password reset and account management use branded templates and custom routes.
- Two-factor authentication is wired in with `django-otp` and `two_factor`.
- Mobile/API auth support is set up with DRF and SimpleJWT when those packages are installed.
- Membership roles are used to decide what a user can do inside an organization.

### Security protections

- Django Axes is used for login attempt rate limiting / lockout protection.
- Bot protection support is included through Cloudflare Turnstile environment variables.
- Custom middleware handles organization context, hosted subdomains, custom domains, canonical redirects, and admin pin behavior.
- PostgreSQL row-level security context middleware is part of the stack for stronger tenant isolation where enabled.
- Sensitive settings are expected to come from environment variables.

### Admin and operational visibility

- Customized Django admin with a branded admin experience
- Custom admin dashboard with KPIs
- Admin analytics views
- Booking audit / undo support
- Login activity tracking
- Invoice action logs

I intentionally kept the admin side as a serious part of the product, not just an afterthought, because it is one of the fastest ways to support operations while the user-facing product is still evolving.

## Tech stack

### Backend

- Python
- Django
- Django ORM
- Django admin
- Django REST Framework
- SimpleJWT
- django-otp / two-factor auth
- django-axes
- Stripe
- Cloudflare integration hooks

### Frontend

- Django templates for the main web app
- Tailwind CSS for styling
- JavaScript-based enhancements in the web UI
- React Native with Expo for mobile
- TypeScript in the mobile app

### Data / deployment

- SQLite for local development in this repo
- PostgreSQL-oriented middleware and production settings support
- Render deployment configuration for the Django app

## Project structure

This is the part I would want another developer to read first before jumping into the code.

- `circlecalproject/`
	Main Django application workspace.
- `circlecalproject/circlecalproject/`
	Project settings, URLs, WSGI/ASGI, API modules, and shared backend configuration.
- `circlecalproject/accounts/`
	User profile, organization/business, membership, invites, team membership, login activity, and mobile/device identity related models.
- `circlecalproject/bookings/`
	Booking domain logic, notifications, calendar invite generation, and booking views/templates.
- `circlecalproject/calendar_app/`
	Main web application views, middleware, forms, admin customization, public pages, scheduling UI, PWA pieces, and business-facing workflows.
- `circlecalproject/billing/`
	Plans, subscriptions, invoice metadata, Stripe-related billing logic, and billing views.
- `circlecalproject/static/`
	Static assets, vendor assets, CSS sources, and built frontend files.
- `circlecalproject/templates/`
	Shared templates.
- `circlecalproject/tests/`
	Project-level test coverage.
- `mobile/`
	Expo / React Native mobile app.

## Features by backend area

### Accounts and organization layer

This part of the app is not just basic auth. It carries a lot of the SaaS structure.

- user profiles and avatars
- business/organization records
- organization memberships and role assignments
- invites and invitation acceptance flows
- team and team membership records
- device tokens for push notifications
- mobile SSO tokens for app-to-web session handoff

### Booking domain

This is the product core.

- customer bookings
- service-based scheduling
- organization-scoped calendars
- resource scheduling support
- booking emails and reminders
- audit and export support
- payment status awareness

### Billing domain

This part supports turning the app into an actual SaaS product instead of just a scheduling tool.

- plan catalog
- subscriptions
- upgrade/downgrade flows
- Stripe metadata
- Stripe Connect state for businesses
- invoice visibility/action history

## Local development setup

The project currently has a Django app and a mobile app. I normally treat them as two related workspaces that share the same product domain.

### 1. Clone and create a virtual environment

From the repo root:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### 2. Install Python dependencies

```powershell
cd circlecalproject
pip install -r requirements-dev.txt
```

### 3. Create the Django environment file

Copy the example file:

```powershell
Copy-Item .env.example .env
```

At minimum, set values for:

- `SECRET_KEY`
- Stripe keys if you want billing locally
- email settings if you want real email behavior

### 4. Run migrations and seed billing plans

```powershell
python manage.py migrate
python manage.py seed_plans
```

### 5. Install frontend build dependency for CSS

In `circlecalproject/`:

```powershell
npm install
npm run build:css
```

If you are actively changing styles:

```powershell
npm run watch:css
```

### 6. Start the Django server

```powershell
python manage.py runserver
```

Default local URL:

- `http://127.0.0.1:8000`

## Mobile app setup

From the repo root:

```powershell
cd mobile
npm install
Copy-Item .env.example .env
npm run start
```

Useful alternatives:

- `npm run start:tunnel` for easier phone testing when LAN networking is unreliable
- `npm run android`
- `npm run ios`
- `npm run web`

The mobile app reads its API base URL from environment/app config. For local testing, point it to the Django server or tunnel URL you are actually using.

## Local mobile testing note

This was the original note in this file, and it is still relevant:

1. Make sure the device and laptop are on the same Wi-Fi if you are using LAN mode.
2. If you use a local IP like `http://192.168.x.x:8000`, that host needs to be allowed in Django settings and used by the mobile app config.
3. If LAN mode is unreliable, use Expo tunnel mode or ngrok.

## Environment notes

### Django `.env`

The example environment file already shows the main categories I use:

- Django secret key
- Stripe configuration
- email / SMTP configuration
- optional site URL values

There are also other environment-driven settings in the project for:

- Turnstile bot protection
- Expo push notifications
- mobile auth refresh lifetime
- custom host/subdomain behavior

### Mobile `.env`

The main public variable is:

- `EXPO_PUBLIC_API_BASE_URL`

That lets the app point at production, localhost, LAN IP, or a tunnel URL depending on the testing scenario.

## Deployment notes

There is a `render.yaml` file in the Django project that shows the current deployment shape for the web app.

That setup includes:

- Python 3.12
- dependency install during build
- Tailwind CSS build step
- `collectstatic`
- migrations on startup
- `seed_plans`
- `ensure_superuser`
- Gunicorn app start

For production, the expectation is that secrets are supplied through the hosting environment, not committed into the repo.

## How I think about the architecture

If I had to explain the project simply to another developer, I would say it like this:

- Django handles the product core, data model, auth, admin, public pages, and most business workflows.
- The mobile app exists because staff and owners need faster operational access than a desktop browser always gives them.
- Billing, roles, and org scoping are not side features. They are part of the app's actual backbone.
- Admin customization matters here because internal visibility saves time when debugging user issues or reviewing business activity.

## Things I intentionally kept practical

I want this README to sound like the project it is.

- I did not try to abstract everything into a perfect architecture too early.
- I used Django admin where it made sense instead of rebuilding every internal tool from scratch.
- I let the web app and mobile app share the same domain rules, but not necessarily the same UI approach.
- I added security layers gradually as the app started looking more like a real SaaS product.
- I kept room for production-oriented features like custom domains, org isolation, Stripe, and mobile auth even if every part did not arrive at the same time.

## If another developer is onboarding to this project

The best order to understand the codebase is:

1. Read Django settings and top-level URLs.
2. Look at `accounts` models to understand organizations, roles, and user relationships.
3. Look at `bookings` and `calendar_app` to understand the actual product behavior.
4. Look at `billing` to understand plan gating and payment flows.
5. Look at the mobile app only after the backend domain model makes sense.

That order mirrors how the app is actually structured.

## Current state of the project

CircleCal is not just a prototype anymore. It has working product layers across:

- booking and scheduling
- account and organization management
- role-aware permissions
- admin tooling
- billing
- mobile support
- deployment configuration

At the same time, it is still a codebase that has clearly grown over time, and this README is meant to reflect that honestly.