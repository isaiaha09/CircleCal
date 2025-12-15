import os, smtplib, traceback
p = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(p):
    with open(p,"r",encoding="utf-8") as f:
        for line in f:
            line=line.strip()
            if not line or line.startswith("#"): continue
            if "=" in line:
                k,v=line.split("=",1); os.environ.setdefault(k.strip(), v.strip().strip("\'\""))
host = os.getenv("EMAIL_HOST","smtp-relay.brevo.com")
port = int(os.getenv("EMAIL_PORT","587"))
user = os.getenv("BREVO_SMTP_USER")
pw = os.getenv("BREVO_SMTP_PASSWORD")
to = "emailtestappworks@gmail.com"   # replace with address you control
from_addr = user or os.getenv("DEFAULT_FROM_EMAIL") or "noreply@localhost"
msg = "Subject: SMTP debug test\\r\\n\\r\\nThis is a debug test."
try:
    s = smtplib.SMTP(host, port, timeout=15)
    s.set_debuglevel(1)
    s.ehlo()
    s.starttls()
    s.ehlo()
    if user and pw:
        s.login(user, pw)
    s.sendmail(from_addr, [to], msg)
    s.quit()
    print('SMTP send ok')
except Exception:
    traceback.print_exc()
