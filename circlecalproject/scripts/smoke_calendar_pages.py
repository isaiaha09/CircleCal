import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BASE_URL = "http://127.0.0.1:8000"

STATIC_URLS = [
    f"{BASE_URL}/static/vendor/jquery/jquery-3.6.0.min.js",
    f"{BASE_URL}/static/vendor/select2/4.1.0-rc.0/css/select2.min.css",
    f"{BASE_URL}/static/vendor/select2/4.1.0-rc.0/js/select2.min.js",
    f"{BASE_URL}/static/vendor/fullcalendar/5.11.3/css/main.min.css",
    f"{BASE_URL}/static/vendor/fullcalendar/5.11.3/js/main.min.js",
    f"{BASE_URL}/static/vendor/cropperjs/1.5.13/cropper.min.css",
    f"{BASE_URL}/static/vendor/cropperjs/1.5.13/cropper.min.js",
    f"{BASE_URL}/static/css/tailwind.min.css",
]


def _run_manage(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "manage.py", *args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )


def _server_is_up(url: str = f"{BASE_URL}/") -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as r:
            return int(getattr(r, "status", 0)) in (200, 301, 302)
    except Exception:
        return False


def _http_status(url: str, method: str = "GET") -> int:
    req = urllib.request.Request(url, method=method)
    with urllib.request.urlopen(req, timeout=10) as r:
        return int(getattr(r, "status", 0))


def main() -> int:
    print("== Django migrate (best-effort) ==")
    mig = _run_manage("migrate", "--noinput")
    if mig.returncode != 0:
        print("MIGRATE FAILED")
        print(mig.stdout)
        print(mig.stderr)
        return 1

    print("== Seed E2E org/service ==")
    seed = _run_manage("seed_e2e")
    if seed.returncode != 0:
        print("SEED FAILED")
        print(seed.stdout)
        print(seed.stderr)
        return 1

    last_line = (seed.stdout or "").strip().splitlines()[-1]
    info = json.loads(last_line)
    org_slug = info.get("org_slug")
    service_slug = info.get("service_slug")
    if not org_slug or not service_slug:
        print("SEED OUTPUT DID NOT INCLUDE org_slug/service_slug")
        print(seed.stdout)
        return 1

    print(f"Seeded: org={org_slug} service={service_slug}")

    print("== Start dev server ==")
    log_path = os.path.join(PROJECT_ROOT, "scripts", "smoke_devserver.log")
    with open(log_path, "a", encoding="utf-8") as logfile:
        server_proc = subprocess.Popen(
            [sys.executable, "manage.py", "runserver", "127.0.0.1:8000"],
            cwd=PROJECT_ROOT,
            stdout=logfile,
            stderr=logfile,
        )

    try:
        for _ in range(30):
            if _server_is_up():
                break
            time.sleep(1)
        else:
            print("Dev server did not start in time.")
            print(f"Check log: {log_path}")
            return 1

        print("== Page GETs (expect 200 or redirect, not 500) ==")
        page_urls = [
            f"{BASE_URL}/demo/",
            f"{BASE_URL}/bus/{org_slug}/services/",
            f"{BASE_URL}/bus/{org_slug}/calendar/",
        ]
        for u in page_urls:
            try:
                status = _http_status(u, method="GET")
                ok = status in (200, 301, 302)
                print(("OK  " if ok else "BAD ") + f"{status}  {u}")
            except urllib.error.HTTPError as e:
                # still a response
                status = int(getattr(e, "code", 0) or 0)
                ok = status in (200, 301, 302)
                print(("OK  " if ok else "BAD ") + f"{status}  {u}")
            except Exception as e:
                print(f"FAIL {u} :: {e}")

        print("== Static asset HEADs ==")
        for u in STATIC_URLS:
            try:
                status = _http_status(u, method="HEAD")
                ok = status == 200
                print(("OK  " if ok else "BAD ") + f"{status}  {u}")
            except urllib.error.HTTPError as e:
                status = int(getattr(e, "code", 0) or 0)
                ok = status == 200
                print(("OK  " if ok else "BAD ") + f"{status}  {u}")
            except Exception as e:
                print(f"FAIL {u} :: {e}")

        print(f"Done. Devserver log: {log_path}")
        return 0
    finally:
        try:
            server_proc.terminate()
        except Exception:
            pass
        try:
            server_proc.wait(timeout=5)
        except Exception:
            try:
                server_proc.kill()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
