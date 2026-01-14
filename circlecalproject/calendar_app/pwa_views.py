import json

from django.http import HttpResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET


@require_GET
@never_cache
def manifest_webmanifest(request):
    # Notes/corrections vs the user-provided manifest:
    # - Django serves static assets under /static/ by default, so icon src uses /static/icons/...
    # - iOS ignores most manifest fields; it uses apple-touch-icon + startup images.
    data = {
        "name": "CircleCal",
        "short_name": "CircleCal",
        "description": "CircleCal is a focused booking and scheduling platform for small businesses.",
        "id": "/",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": "#ffffff",
        "theme_color": "#3b82f6",
        "screenshots": [
            {
                "src": "/static/icons/screenshots/screenshot-1080x1920.png",
                "sizes": "1080x1920",
                "type": "image/png",
                "form_factor": "narrow",
            },
            {
                "src": "/static/icons/screenshots/screenshot-1920x1080.png",
                "sizes": "1920x1080",
                "type": "image/png",
                "form_factor": "wide",
            },
        ],
        "icons": [
            {
                "src": "/static/icons/icon-192x192.png",
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "any",
            },
            {
                "src": "/static/icons/icon-384x384.png",
                "sizes": "384x384",
                "type": "image/png",
                "purpose": "any",
            },
            {
                "src": "/static/icons/icon-512x512.png",
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "any",
            },
            {
                "src": "/static/icons/icon-192x192-maskable.png",
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "maskable",
            },
            {
                "src": "/static/icons/icon-512x512-maskable.png",
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "maskable",
            },
        ],
    }

    return HttpResponse(
        json.dumps(data, separators=(",", ":"), ensure_ascii=False),
        content_type="application/manifest+json",
    )


@require_GET
@never_cache
def manifest_json(request):
    # Serve the same manifest at /manifest.json for compatibility with tooling.
    return manifest_webmanifest(request)


@require_GET
@never_cache
def service_worker(request):
    js = """/* CircleCal service worker (online-only)
 *
 * Goal: allow installability, but do NOT let the app operate offline.
 * Strategy:
 * - No precaching; no runtime caching.
 * - All requests are network-only.
 * - If offline and this is a navigation request, show a simple offline page.
 */

const OFFLINE_HTML = `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <meta name="theme-color" content="#3b82f6" />
  <title>Offline • CircleCal</title>
  <style>
    body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;margin:0;padding:32px;background:#0b1220;color:#e5e7eb;}
    .card{max-width:720px;margin:0 auto;background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.12);border-radius:16px;padding:20px;}
    h1{margin:0 0 8px;font-size:22px;}
    p{margin:0 0 14px;line-height:1.5;color:rgba(229,231,235,0.9)}
    button{background:#2563eb;color:#fff;border:0;border-radius:12px;padding:10px 14px;font-weight:600;cursor:pointer}
  </style>
</head>
<body>
  <div class="card">
    <h1>You’re offline</h1>
    <p>CircleCal requires an internet connection.</p>
    <button onclick="location.reload()">Try again</button>
  </div>
</body>
</html>`;

self.addEventListener('install', (event) => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  // Network-only for everything. If offline, navigations get a friendly page.
  event.respondWith((async () => {
    try {
      return await fetch(req);
    } catch (e) {
      if (req.mode === 'navigate') {
        return new Response(OFFLINE_HTML, {
          status: 503,
          headers: { 'Content-Type': 'text/html; charset=utf-8' },
        });
      }
      return new Response('', { status: 503 });
    }
  })());
});
"""

    return HttpResponse(js, content_type="application/javascript")


@require_GET
@never_cache
def offline_page(request):
    html = """<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <meta name=\"theme-color\" content=\"#3b82f6\" />
  <title>Offline • CircleCal</title>
  <style>
    body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;margin:0;padding:32px;background:#0b1220;color:#e5e7eb;}
    .card{max-width:720px;margin:0 auto;background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.12);border-radius:16px;padding:20px;}
    h1{margin:0 0 8px;font-size:22px;}
    p{margin:0 0 14px;line-height:1.5;color:rgba(229,231,235,0.9)}
    a{color:#93c5fd}
    button{background:#2563eb;color:#fff;border:0;border-radius:12px;padding:10px 14px;font-weight:600;cursor:pointer}
  </style>
</head>
<body>
  <div class=\"card\">
    <h1>You’re offline</h1>
    <p>CircleCal needs an internet connection to load scheduling and booking data.</p>
    <button onclick=\"location.reload()\">Try again</button>
  </div>
</body>
</html>"""

    return HttpResponse(html, content_type="text/html; charset=utf-8")
