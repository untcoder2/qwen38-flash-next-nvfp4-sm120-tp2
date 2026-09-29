import requests
import json
from agent.config import Config
# to supress SSL Error messages
import urllib3
import builtins
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from requests.packages.urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter

# Retry(total=100, backoff_factor=1) could keep the agent hammering a dead
# panel for an hour; 5 retries with an explicit socket timeout fail fast and
# let systemd Restart=always re-connect the whole agent promptly.
retries = Retry(total=5, backoff_factor=1)
adapter = HTTPAdapter(max_retries=retries)
http = requests.Session()
http.mount("https://", adapter)
http.mount("http://", adapter)

# (connect, read) timeout pairs. Downloads use a longer read timeout: it
# applies per socket read, so a big streamed file never trips it as long as
# bytes keep flowing.
HTTP_TIMEOUT = (10, 30)
DOWNLOAD_TIMEOUT = (10, 60)
DOWNLOAD_CHUNK_SIZE = 1048576

def get(url):
    path = ''
    if Config.USE_SSL == 'True':
        path += 'https://'
    else:
        path += 'http://'

    with open('VERSION.TXT', 'r') as f:
        version = f.readline().strip('\n')

    cookie = {
        'uuid': Config.UUID,
        'name': Config.NAME,
        'agent_version': version
    }

    path += Config.HASHVIEW_SERVER + ':' + Config.HASHVIEW_PORT + url

    if builtins.state == 'debug':
        print('[DEBUG] http.py->GET: ' + path)
        print('[DEBUG] http.py->GET: ' + str(cookie))

    response = http.get(path, verify=False, cookies=cookie, timeout=HTTP_TIMEOUT)
    if response.status_code == 200:
        return response.content
    else:
        print('[!] HTTP POST (response): Got an unexpected return code:' + str(response.status_code))

def get_stream(url, dest_path):
    """
    Download url to dest_path in chunks instead of buffering the whole body
    in RAM (wordlists can be tens of GB). Returns bytes written, or None on
    a non-200 response.
    """
    path = ''
    if Config.USE_SSL == 'True':
        path += 'https://'
    else:
        path += 'http://'

    with open('VERSION.TXT', 'r') as f:
        version = f.readline().strip('\n')

    cookie = {
        'uuid': Config.UUID,
        'name': Config.NAME,
        'agent_version': version
    }

    path += Config.HASHVIEW_SERVER + ':' + Config.HASHVIEW_PORT + url

    if builtins.state == 'debug':
        print('[DEBUG] http.py->GET_STREAM: ' + path)
        print('[DEBUG] http.py->GET_STREAM: ' + str(cookie))

    response = http.get(path, verify=False, cookies=cookie, stream=True, timeout=DOWNLOAD_TIMEOUT)
    if response.status_code == 200:
        written = 0
        try:
            with open(dest_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE):
                    if chunk:
                        f.write(chunk)
                        written += len(chunk)
        finally:
            response.close()
        return written
    else:
        print('[!] HTTP GET_STREAM (response): Got an unexpected return code:' + str(response.status_code))
        response.close()
        return None

def post(url, data):
    path = ''
    if Config.USE_SSL == 'True':
        path += 'https://'
    else:
        path += 'http://'

    with open('VERSION.TXT', 'r') as f:
        version = f.readline().strip('\n')

    path += Config.HASHVIEW_SERVER + ':' + Config.HASHVIEW_PORT + url
    headers = {'Content-type': 'application/json', 'Accept': 'text/plain'}
    cookie = {
        'uuid': Config.UUID,
        'name': Config.NAME,
        'agent_version': version
    }

    if builtins.state == 'debug':
        print('[DEBUG] http.py->POST: ' + str(path))
        print('[DEBUG] http.py->POST: ' + str(data))
        print('[DEBUG] http.py->POST: ' + str(cookie))

    # put in try/catch statement for timeouts etc.
    response = http.post(path, data=json.dumps(data), verify=False, cookies=cookie, headers=headers, timeout=HTTP_TIMEOUT)
    if response.status_code == 200:
        return response.text
    else:
        print('[!] HTTP POST (response): Got an unexpected return code:' + str(response.status_code))
