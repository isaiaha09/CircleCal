import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE','circlecalproject.settings_prod')
import django
django.setup()
from django.urls import resolve, Resolver404

for path in ['/cancel/48/', '/cancel/48']:
    try:
        r = resolve(path)
        print(path, '->', r.func, 'namespaces:', r.namespaces, 'url_name:', r.url_name)
    except Resolver404:
        print(path, '-> Resolver404')
