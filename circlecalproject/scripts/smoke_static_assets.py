import sys
import urllib.request

BASE = "http://127.0.0.1:8000"
URLS = [
    f"{BASE}/static/vendor/jquery/jquery-3.6.0.min.js",
    f"{BASE}/static/vendor/select2/4.1.0-rc.0/css/select2.min.css",
    f"{BASE}/static/vendor/select2/4.1.0-rc.0/js/select2.min.js",
    f"{BASE}/static/vendor/fullcalendar/5.11.3/css/main.min.css",
    f"{BASE}/static/vendor/fullcalendar/5.11.3/js/main.min.js",
    f"{BASE}/static/vendor/cropperjs/1.5.13/cropper.min.css",
    f"{BASE}/static/vendor/cropperjs/1.5.13/cropper.min.js",
    f"{BASE}/static/css/tailwind.min.css",
]


def head(url: str) -> int:
    req = urllib.request.Request(url, method="HEAD")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return int(resp.status)


def main() -> int:
    failures = 0
    for url in URLS:
        try:
            status = head(url)
            print(f"OK   {status}  {url}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAIL       {url} :: {exc}")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
