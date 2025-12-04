import urllib.request, json, datetime
org='pw-e2e-1764916643'
svc='pw-e2e-1764916643-svc'
now=datetime.datetime.utcnow()
for delta in range(0,4):
    d=now+datetime.timedelta(days=delta)
    start=d.replace(hour=0,minute=0,second=0,microsecond=0)
    end=d.replace(hour=23,minute=59,second=0,microsecond=0)
    s=start.strftime('%Y-%m-%dT%H:%M:00')
    e=end.strftime('%Y-%m-%dT%H:%M:00')
    url=f'http://127.0.0.1:8000/bus/{org}/services/{svc}/availability/?start={s}&end={e}&inc=30'
    print('Query',delta,'->',s)
    try:
        with urllib.request.urlopen(url,timeout=10) as r:
            data=r.read().decode()
            print('  status',r.status,'len',len(data))
            try:
                arr=json.loads(data)
                print('  slots:', len(arr))
                if len(arr):
                    print('   first',arr[0])
            except Exception:
                print('  non-json response')
    except Exception as ex:
        print('  error',ex)
print('done')
