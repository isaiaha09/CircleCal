from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
import time
import subprocess
import sys
import json
import os
import urllib.request
import urllib.error
import datetime
import pytest

# Self-contained Playwright E2E: ensures server is running, seeds test data,
# exercises booking modal and asserts booking success. Adds defensive logging
# to help debugging on CI or local runs. It also tears down the seeded org after the test.

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
DEVSERVER_LOG = os.path.join(PROJECT_ROOT, 'tests', 'e2e', 'devserver.log')


def server_is_up(url='http://127.0.0.1:8000/'):
    try:
        with urllib.request.urlopen(url, timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def start_dev_server():
    logfile = open(DEVSERVER_LOG, 'a')
    proc = subprocess.Popen([
        sys.executable, 'manage.py', 'runserver', '127.0.0.1:8000'
    ], cwd=PROJECT_ROOT, stdout=logfile, stderr=logfile)
    # Wait for server to come up
    for _ in range(30):
        if server_is_up():
            return proc
        time.sleep(1)
    # timed out
    raise RuntimeError('Dev server did not start in time; check tests/e2e/devserver.log')


def seed_test_data():
    # Prefer using the management command we added: `manage.py seed_e2e`
    proc = subprocess.run([sys.executable, 'manage.py', 'seed_e2e'], cwd=PROJECT_ROOT, capture_output=True, text=True)
    if proc.returncode != 0:
        err = (proc.stderr or '').lower()
        if 'modulenotfounderror' in err or 'module not found' in err:
            return None, None
        # Unknown failure, propagate for debugging
        raise RuntimeError(f"Seeding test data failed:\nSTDOUT: {proc.stdout}\nSTDERR: {proc.stderr}")
    out_lines = proc.stdout.strip().splitlines()
    if not out_lines:
        return None, None
    last = out_lines[-1]
    try:
        info = json.loads(last)
        return info.get('org_slug'), info.get('service_slug')
    except Exception:
        return None, None


def dump_debug(page, svc_url):
    # Collect helpful debugging artifacts
    try:
        html = page.content()
    except Exception:
        html = '<unable to fetch page.content()>'
    devlog = '<devserver.log not available>'
    try:
        with open(DEVSERVER_LOG, 'r', encoding='utf-8', errors='ignore') as f:
            devlog = f.read()[-10000:]
    except Exception:
        pass
    return f"URL: {svc_url}\n--- Page snapshot ---\n{html[:3000]}\n--- end snapshot ---\n--- tail of devserver.log ---\n{devlog}\n--- end log ---"


def test_public_booking_flow():
    server_proc = None
    created_org = None
    try:
        if not server_is_up():
            server_proc = start_dev_server()
        # Seed test org/service
        org_slug, service_slug = seed_test_data()
        if org_slug:
            created_org = org_slug
            svc_url = f'http://127.0.0.1:8000/bus/{org_slug}/service/{service_slug}/'
        else:
            svc_url = None

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            # Navigate to the seeded service page, or fall back to finding a public service link
            if svc_url:
                resp = page.goto(svc_url, wait_until='networkidle')
                assert resp is not None, f'No response when navigating to {svc_url}'
                if resp.status != 200:
                    debug = dump_debug(page, svc_url)
                    raise RuntimeError(f'Unexpected status {resp.status} for {svc_url}\n{debug}')
            else:
                # fallback: try to find a public org/service from the root page
                resp = page.goto('http://127.0.0.1:8000/', wait_until='networkidle')
                assert resp is not None and resp.status == 200, 'Root page not reachable during fallback'
                # prefer direct service links, otherwise follow /bus/ org links
                link = page.query_selector('a[href*="/service/"]') or page.query_selector('a[href^="/bus/"]')
                if not link:
                    # No seeded data and no public links to fall back to; skip the test locally.
                    pytest.skip("No seeded test data and no public org/service found on root page. Install full requirements or seed data to run E2E.")
                link.click()
                page.wait_for_load_state('networkidle')

            # Wait for the calendar to initialize by waiting for day circles to be present
            try:
                page.wait_for_selector('.day-circle', timeout=15000)
            except PlaywrightTimeoutError:
                debug = dump_debug(page, svc_url)
                raise AssertionError(f"Calendar did not render on service page (no .day-circle found)\n{debug}")

            # Pick the first enabled day (not .disabled)
            day = page.query_selector('.day-circle:not(.disabled)')
            if not day:
                debug = dump_debug(page, svc_url)
                raise AssertionError(f"No enabled day found in the calendar to open time slots\n{debug}")
            day.click()

            # Wait for the time modal and at least one time block to appear
            try:
                page.wait_for_selector('#timeModal', timeout=8000)
                page.wait_for_selector('#timeSlots .time-circle', timeout=10000)
            except PlaywrightTimeoutError:
                debug = dump_debug(page, svc_url)
                raise AssertionError(f"Time slots modal did not appear or no time blocks available\n{debug}")

            # Click the first available time block
            time_block = page.query_selector('#timeSlots .time-circle')
            if time_block is None:
                debug = dump_debug(page, svc_url)
                raise AssertionError(f"No clickable time block found\n{debug}")
            time_block.click()

            # Wait for booking form inputs
            try:
                page.wait_for_selector('form input[name="client_name"]', timeout=6000)
                page.wait_for_selector('form input[name="client_email"]', timeout=6000)
            except PlaywrightTimeoutError:
                debug = dump_debug(page, svc_url)
                raise AssertionError(f"Booking form did not appear after selecting a time block\n{debug}")

            # Fill and submit
            page.fill('form input[name="client_name"]', 'Playwright Test')
            page.fill('form input[name="client_email"]', 'pwtest@example.com')

            with page.expect_navigation(timeout=12000):
                page.click('#bookNowBtn')

            # Verify success
            if 'booking_success' not in page.url:
                debug = dump_debug(page, svc_url)
                raise AssertionError(f"Expected booking success in URL, got {page.url}\n{debug}")

            browser.close()
    finally:
        # Teardown: remove seeded test org (cascades services/bookings)
        if created_org:
            try:
                teardown_script = f"from accounts.models import Business as Organization; Organization.objects.filter(slug='{created_org}').delete(); print('deleted')"
                subprocess.run([sys.executable, 'manage.py', 'shell', '-c', teardown_script], cwd=PROJECT_ROOT)
            except Exception:
                pass
        # If we started a dev server for the test, terminate it
        if server_proc:
            try:
                server_proc.terminate()
            except Exception:
                pass
