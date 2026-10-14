
import subprocess, ssl, time, urllib.request, sys
_CTX = ssl.create_default_context(cafile="/home/molochko/penny/tls/ca.pem")
print(time.strftime("%H:%M:%S"), "restart via sudo -n"); sys.stdout.flush()
r = subprocess.run("sudo -n /usr/bin/systemctl restart sglang", shell=True, capture_output=True, text=True)
print("rc:", r.returncode, r.stderr.strip()[:200]); sys.stdout.flush()
for i in range(150):
    time.sleep(10)
    try:
        urllib.request.urlopen("https://127.0.0.1:1234/health_generate", timeout=10, context=_CTX)
        print(time.strftime("%H:%M:%S"), "HEALTH_OK after", i*10, "s"); sys.exit(0)
    except Exception:
        if i % 6 == 0:
            print(time.strftime("%H:%M:%S"), "waiting", i*10, "s", flush=True)
print("HEALTH_TIMEOUT"); sys.exit(3)
