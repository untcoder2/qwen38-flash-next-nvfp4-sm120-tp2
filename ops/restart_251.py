
import subprocess, ssl, time, urllib.request, sys
_CTX = ssl.create_default_context(cafile="/home/molochko/penny/tls/ca.pem")
print(time.strftime("%H:%M:%S"), "docker stop penny...")
r = subprocess.run("docker stop -t 30 penny", shell=True, capture_output=True, text=True)
print("stop rc:", r.returncode, r.stdout.strip(), r.stderr.strip()[:200])
print(time.strftime("%H:%M:%S"), "start via serve-penny.sh")
subprocess.Popen("cd /home/molochko/vllm && setsid ./serve-penny.sh > /home/molochko/penny/serve-penny.out 2>&1 < /dev/null", shell=True)
for i in range(120):
    time.sleep(10)
    try:
        urllib.request.urlopen("https://127.0.0.1:1234/health_generate", timeout=10, context=_CTX)
        print(time.strftime("%H:%M:%S"), "HEALTH_OK after", i*10, "s"); sys.exit(0)
    except Exception:
        if i % 6 == 0:
            st = subprocess.run("docker ps -a --filter name=penny --format '{{.Status}}'", shell=True, capture_output=True, text=True).stdout.strip()
            print(time.strftime("%H:%M:%S"), f"waiting ({i*10}s) container: {st[:60]}")
print("HEALTH_TIMEOUT 20min"); sys.exit(3)
