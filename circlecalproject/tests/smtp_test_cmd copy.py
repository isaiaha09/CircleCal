# -*- coding: utf-8 -*-
import os
import smtplib
import traceback

# If environment variables aren't set in PowerShell, try loading .env from project root.
env_path = os.path.join(os.path.dirname(__file__), '.env')
if os.path.exists(env_path):
    with open(env_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '=' in line:
                k, v = line.split('=', 1)
                k = k.strip()
                v = v.strip().strip("'\"")
                # only set if not already in environment
                if not os.environ.get(k):
                    os.environ[k] = v

host = 'smtp-relay.brevo.com'
port = 587
user = os.getenv('BREVO_SMTP_USER')
pw = os.getenv('BREVO_SMTP_PASSWORD')
to = ['you@example.com']   # <- replace with an address you control
from_addr = os.getenv('DEFAULT_FROM_EMAIL') or user or 'noreply@localhost'
msg = "Subject: CircleCal test\r\n\r\nThis is a test."

try:
    s = smtplib.SMTP(host, port, timeout=15)
    s.ehlo()
    s.starttls()
    s.ehlo()
    if user and pw:
        s.login(user, pw)
    s.sendmail(from_addr, to, msg)
    s.quit()
    print('SMTP send ok')
except Exception:
    traceback.print_exc()
