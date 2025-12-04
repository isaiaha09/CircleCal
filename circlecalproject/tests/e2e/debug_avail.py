import subprocess, json, urllib.request, urllib.error, datetime, sys

# Seed E2E data
p = subprocess.run([sys.executable, 'manage.py', 'seed_e2e'], capture_output=True, text=True)
if p.returncode != 0:
    print('Seed failed:', p.stderr)
    sys.exit(2)
try:
    info = json.loads(p.stdout.strip().splitlines()[-1])
    org = info['org_slug']
    svc = info['service_slug']
except Exception as e:
    print('Failed to parse seed output:', e)
    sys.exit(2)

print('Using org:', org, 'service:', svc)

# Build start/end for today
now = datetime.datetime.utcnow()
start = now.replace(hour=0, minute=0, second=0, microsecond=0)
end = now.replace(hour=23, minute=59, second=0, microsecond=0)

def to_iso_local(dt):
    return dt.strftime('%Y-%m-%dT%H:%M:00')

batch_url = f'http://127.0.0.1:8000/bus/{org}/services/{svc}/availability/batch/?start={to_iso_local(start)}&end={to_iso_local(end)}'
serv_url = f'http://127.0.0.1:8000/bus/{org}/services/{svc}/availability/?start={to_iso_local(start)}&end={to_iso_local(end)}&inc=30'

print('\nBatch URL:', batch_url)
print('Service URL:', serv_url)

for label, url in [('batch', batch_url), ('service', serv_url)]:
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            data = r.read().decode('utf-8')
            print(f'\n{label} response code:', r.status)
            print(data[:10000])
    except urllib.error.HTTPError as he:
        print(label, 'HTTP error', he.code, he.read().decode('utf-8')[:5000])
    except Exception as e:
        print(label, 'error:', e)

print('\nDone')
