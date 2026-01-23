from pyngrok import ngrok

# iOS/Expo Go is much more reliable with HTTPS than plain HTTP.
t = ngrok.connect(8000, bind_tls=True)
print("Public URL:", t.public_url)
input("Press Enter to close tunnel...")
ngrok.disconnect(t.public_url)
