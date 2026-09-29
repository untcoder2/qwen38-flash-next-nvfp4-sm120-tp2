import json, threading, urllib.request


def agent(i):
    body = {"text": "Перечисли числа с %d по %d по одному в строке, ничего лишнего." % (i * 5000 + 1, (i + 1) * 5000),
            "sampling_params": {"max_new_tokens": 5000, "temperature": 0}}
    req = urllib.request.Request("http://192.168.2.127:1234/generate",
                                 data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=600).read()


ths = [threading.Thread(target=agent, args=(i,)) for i in range(4)]
[t.start() for t in ths]
[t.join() for t in ths]
print("burst готов")
