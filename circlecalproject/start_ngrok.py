from pyngrok import ngrok
t = ngrok.connect(8000, bind_tls=False)
print("Public HTTP URL:", t.public_url)
input("Press Enter to close tunnel...")
ngrok.disconnect(t.public_url)
