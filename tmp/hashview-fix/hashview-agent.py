import argparse
import os
import socket
import uuid
import json
import secrets
import hashlib
import sys
import psutil
import re
import signal
import builtins
import time
import subprocess
from contextlib import suppress
from threading import Thread
from datetime import datetime, timedelta


parser = argparse.ArgumentParser()
parser.add_argument("--debug", action="store_true", help="increase output verbosity")
args = parser.parse_args()

# Build Config

# ----------------------------------------------
# Manifest management (JSON based)
# ----------------------------------------------
class Manifest:
    """
    Simple JSON‑based manifest manager.
    Keeps the manifest data in memory and writes to disk only when
    `save()` is called.
    """
    def __init__(self, path):
        self.path = path
        self.data = {}
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r") as f:
                    self.data = json.load(f)
            except Exception:
                # Corrupt or empty file – start fresh
                self.data = {}
        else:
            self.data = {}

    def save(self):
        # Ensure directory exists
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w") as f:
            json.dump(self.data, f, indent=2, sort_keys=True)

# Global manifest instances
rules_manifest = Manifest("control/rules_manifest.json")
wordlists_manifest = Manifest("control/wordlists_manifest.json")
if not os.path.exists('agent/config.conf'):

    # Time to ask some questions
    print('\nInitial setup detected. Hashview Agent will now prompt you to setup the config fild ./agent/config/config.conf.\n')
    server = input('Enter IP address or FQDN of the hashview server: ')
    while len(server) == 0:
        print('Error: Value must be an IP address or FQDN. Can not be blank: ')
        server = input("Enter IP address or FQDN of the hashview server: ")
    port = input('Enter the port of the hashview server: ')
    while len(port) == 0:
        print('Error: You must provide a port. By default Hashview Server runs on 8443: ')
        port = input("Enter the port of the hashview server: ")
    use_tls = input('Does the Hashview server use SSL/TLS? [y/N]:')
    if use_tls == 'y' or use_tls == 'Y':
        use_tls = True
    else:
        use_tls = False

    hostname = socket.gethostname()
    name = input('Enter the name of this Hashview Agent [Hit Enter for: ' + hostname + ']: ')
    if len(name) == 0:
        name = hostname
    
    # Generate UUID
    agent_uuid = uuid.uuid4()

    hashcat_path = input('Enter the path to a local install of hashcat: ')
    while not os.path.exists(hashcat_path):
        print("Error: File not found.")
        hashcat_path = input('Enter the path to a local install of hashcat: ')    


    # Write config file
    config = open("agent/config.conf", "w")
    config.write("[HASHVIEW]\n")
    config.write("server = " + str(server) + "\n")
    config.write("port = " + str(port) + "\n")
    config.write("use_ssl = " + str(use_tls) + "\n\n")

    config.write("[AGENT]\n")
    config.write("name = " + str(name) + "\n")
    config.write("uuid = " + str(agent_uuid) + "\n")
    config.write("HC_BIN_PATH = " + str(hashcat_path) + "\n")

    config.close()

from agent.api import api    

HC_FAIL_MARKERS = (
    'cuInit unknown error',
    'No OpenCL, HIP or CUDA compatible platform',
    'cuMemAlloc failed',
)
# hashcat: 0 = at least one crack, 1 = keyspace exhausted without cracks.
# Anything else (255 backend init failure, negative signal death, ...) is a failure.
HC_OK_RETURNCODES = (0, 1)

def _pid_gone(pid):
    try:
        p = psutil.Process(pid)
        return (not p.is_running()) or p.status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return True

def _signal_group(pid, sig):
    # start_new_session=True makes the shell a process-group leader, so
    # killpg reaches hashcat and tee as well (os.kill alone only hits sh).
    try:
        os.killpg(os.getpgid(pid), sig)
    except (ProcessLookupError, PermissionError, OSError):
        with suppress(ProcessLookupError, PermissionError):
            os.kill(pid, sig)

def _term_then_kill(pid, grace=5):
    """SIGTERM, wait up to `grace` seconds for exit, then SIGKILL."""
    pid = int(pid)
    if _pid_gone(pid):
        return
    with suppress(ProcessLookupError, PermissionError):
        os.kill(pid, signal.SIGTERM)
    deadline = time.time() + grace
    while time.time() < deadline:
        if _pid_gone(pid):
            return
        time.sleep(0.5)
    print('[!] PID %d survived SIGTERM, sending SIGKILL' % pid)
    with suppress(ProcessLookupError, PermissionError):
        os.kill(pid, signal.SIGKILL)

def run_command(command, timeout=None):
    """
    Run a shell command. Returns (returncode, stdout, stderr); returncode is
    None on OSError. On `timeout` the whole process group gets SIGTERM, then
    SIGKILL after another 5 seconds, so a wedged hashcat cannot pin the agent
    in 'Working' forever.
    """
    try:
        cmd = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        try:
            output, error = cmd.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            print('[!] run_command: no exit after %ss, terminating process group' % timeout)
            _signal_group(cmd.pid, signal.SIGTERM)
            try:
                output, error = cmd.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                _signal_group(cmd.pid, signal.SIGKILL)
                output, error = cmd.communicate()

        if(error):
            print(error)
            if 'hashfile is empty or corrupt' not in str(error):
                if 'Terminated' in str(error):
                    # do not sys.exit() the worker thread here: the caller
                    # inspects returncode/stderr and reports the task status
                    return cmd.returncode, output, error
                else:
                    api.sendError(str(error))
                    return cmd.returncode, output, error
        return cmd.returncode, output, error
    except OSError as e: 
        print("inside exception", e)
        api.sendError(str(e))
        #sys.exit()
        return None, None, str(e)

def send_heartbeat(agent_status, hc_status):
    return api.heartbeat(agent_status, hc_status)

def getHashcatPid():
    if sys.platform == 'win32':
        print('Hashview-Agent doesn\'t currecntly work on windows. PR\'s welcome :)')
        sys.exit()
    else:
        for proc in psutil.process_iter():
            try:
                pinfo = proc.as_dict(attrs=['pid', 'name', 'cmdline'])
                # In the future we should change this to session id
                if 'hashcat' in pinfo['name'].lower():
                    for cli_args in pinfo['cmdline']:
                        if 'hc_cracked_' in cli_args:
                            return pinfo['pid']
            except:
                return False
    return False

def sync_rules():
    """
    Synchronise local rule files with the server using JSON manifests.
    """
    print('Syncing local rules with server.')
    response = api.rules_list()
    server_entries = json.loads(response)
    new_manifest = {}

    for entry in server_entries:
        rule_id = str(entry['id'])
        remote_checksum = entry['checksum']
        filename = entry['path'].split('/')[-1]

        local_entry = rules_manifest.data.get(rule_id)

        if local_entry:
            # Existing entry – verify checksum
            if local_entry['checksum'] != remote_checksum:
                print('Checksum mismatch for rule', rule_id)
                old_path = os.path.join('control/rules', local_entry['filename'])
                if os.path.exists(old_path):
                    os.remove(old_path)

                # Download and verify new file
                random_hex = secrets.token_hex(8)
                compressed = api.get_rules_file(entry['id'])
                tmp_gz = os.path.join('control/tmp', f'{random_hex}.gz')
                with open(tmp_gz, 'wb') as f:
                    f.write(compressed)

                run_command(f'gunzip {tmp_gz}')
                tmp_file = os.path.join('control/tmp', random_hex)

                sha256 = hashlib.sha256()
                with open(tmp_file, 'rb') as f:
                    for block in iter(lambda: f.read(4096), b''):
                        sha256.update(block)
                local_checksum = sha256.hexdigest()
                print('Local:', local_checksum, 'Remote:', remote_checksum)

                if local_checksum == remote_checksum:
                    dest = os.path.join('control/rules', filename)
                    run_command(f'mv {tmp_file} {dest}')
                    new_manifest[rule_id] = {'checksum': local_checksum, 'filename': filename}
                else:
                    print('Checksum verification failed for rule', rule_id)
                    os.remove(tmp_file)
            else:
                new_manifest[rule_id] = local_entry
        else:
            # New rule – download
            print('Downloading new rule', rule_id)
            random_hex = secrets.token_hex(8)
            compressed = api.get_rules_file(entry['id'])
            tmp_gz = os.path.join('control/tmp', f'{random_hex}.gz')
            with open(tmp_gz, 'wb') as f:
                f.write(compressed)

            run_command(f'gunzip {tmp_gz}')
            tmp_file = os.path.join('control/tmp', random_hex)

            sha256 = hashlib.sha256()
            with open(tmp_file, 'rb') as f:
                for block in iter(lambda: f.read(4096), b''):
                    sha256.update(block)
            local_checksum = sha256.hexdigest()
            print('Local:', local_checksum, 'Remote:', remote_checksum)

            if local_checksum == remote_checksum:
                dest = os.path.join('control/rules', filename)
                run_command(f'mv {tmp_file} {dest}')
                new_manifest[rule_id] = {'checksum': local_checksum, 'filename': filename}
            else:
                print('Checksum verification failed for new rule', rule_id)
                os.remove(tmp_file)

    if new_manifest != rules_manifest.data:
        rules_manifest.data = new_manifest
        rules_manifest.save()
        print('Rules manifest updated.')
    else:
        print('Rules manifest unchanged.')

def sync_wordlists():
    """
    Synchronise local wordlist files with the server using JSON manifests.
    """
    print('Syncing local wordlists with server.')
    response = api.getWordlists()
    server_entries = json.loads(response)
    new_manifest = {}

    for entry in server_entries:
        wl_id = str(entry['id'])
        remote_checksum = entry['checksum']
        filename = entry['path'].split('/')[-1]

        local_entry = wordlists_manifest.data.get(wl_id)

        if local_entry:
            print(f"Found existing wordlist {wl_id} in local manifest.")
            # Existing entry – verify checksum
            if local_entry['checksum'] != remote_checksum:
                print('Checksum mismatch for wordlist', wl_id)
                old_path = os.path.join('control/wordlists', local_entry['filename'])
                if os.path.exists(old_path):
                    os.remove(old_path)

                # Download and verify new file
                random_hex = secrets.token_hex(8)
                tmp_gz = os.path.join('control/tmp', f'{random_hex}.gz')
                written = api.get_wordlists_file(entry['id'], tmp_gz)
                if not written:
                    print('[!] Skipping wordlist (no file from server)', wl_id)
                    with suppress(OSError):
                        os.remove(tmp_gz)
                    continue

                run_command(f'gunzip {tmp_gz}', timeout=7200)
                tmp_file = os.path.join('control/tmp', random_hex)

                sha256 = hashlib.sha256()
                with open(tmp_file, 'rb') as f:
                    for block in iter(lambda: f.read(4096), b''):
                        sha256.update(block)
                local_checksum = sha256.hexdigest()
                print('Local:', local_checksum, 'Remote:', remote_checksum)

                if local_checksum == remote_checksum:
                    dest = os.path.join('control/wordlists', filename)
                    run_command(f'mv {tmp_file} {dest}')
                    new_manifest[wl_id] = {'checksum': local_checksum, 'filename': filename}
                else:
                    print('Checksum verification failed for wordlist', wl_id)
                    os.remove(tmp_file)
            else:
                new_manifest[wl_id] = local_entry
        else:
            # New wordlist – download
            print('Downloading new wordlist', wl_id)
            random_hex = secrets.token_hex(8)
            tmp_gz = os.path.join('control/tmp', f'{random_hex}.gz')
            written = api.get_wordlists_file(entry['id'], tmp_gz)
            if not written:
                print('[!] Skipping wordlist (no file from server)', wl_id)
                with suppress(OSError):
                    os.remove(tmp_gz)
                continue

            run_command(f'gunzip {tmp_gz}', timeout=7200)
            tmp_file = os.path.join('control/tmp', random_hex)

            sha256 = hashlib.sha256()
            with open(tmp_file, 'rb') as f:
                for block in iter(lambda: f.read(4096), b''):
                    sha256.update(block)
            local_checksum = sha256.hexdigest()

            if local_checksum == remote_checksum:
                dest = os.path.join('control/wordlists', filename)
                run_command(f'mv {tmp_file} {dest}')
                new_manifest[wl_id] = {'checksum': local_checksum, 'filename': filename}
            else:
                print('Checksum verification failed for new wordlist', wl_id)
                os.remove(tmp_file)

    if new_manifest != wordlists_manifest.data:
        wordlists_manifest.data = new_manifest
        wordlists_manifest.save()
        print('Wordlists manifest updated.')
    else:
        print('Wordlists manifest unchanged.')

def jobTasks(job_task_id):
    return api.jobTasks(job_task_id)

def jobs(job_id):
    return api.jobs(job_id)

def tasks(task_id):
    return api.tasks(task_id)

def getWordlists():
    return api.getWordlists()

def updateDynamicWordlists(wordlist_id):
    return api.updateDynamicWordlists(wordlist_id)

def download_hashfile(job_id, jobtask_id, hashfile_id):
    # Note we are not compressing our hashfile
    dest = 'control/hashes/hashfile_' + str(job_id) + '_' + str(jobtask_id) + '.txt'
    written = api.get_hashfile(hashfile_id, dest)
    if not written:
        print('[!] hashview-agent.py->download_hashfile() Failed to download hashfile ' + str(hashfile_id))

def replaceHashcatBinPath(cmd):
    from agent.config import Config
    return cmd.replace('@HASHCATBINPATH@', Config.HC_BIN_PATH)

def run_hashcat(cmd, result):
    # hashcat tasks run for hours by design: no wall-clock timeout here.
    # A wedged process is caught by the status-output watchdog in the main
    # loop (HC_STALL_TIMEOUT) which escalates SIGTERM -> SIGKILL.
    rc, output, error = run_command(cmd)
    if isinstance(error, bytes):
        error = error.decode('utf-8', errors='replace')
    result['returncode'] = rc
    result['stderr'] = error or ''
    #os.system(cmd)

def time_difference(future_timestamp):
    # Get the current time and calculate the difference
    now = datetime.now()
    future_time = datetime.fromtimestamp(future_timestamp)
    delta = future_time - now

    # If the time difference is negative (i.e., the timestamp is in the past), return immediately
    if delta.total_seconds() < 0:
        return "The specified time is in the past."

    # Calculate each time component
    years = delta.days // 365
    months = (delta.days % 365) // 30
    days = (delta.days % 365) % 30
    hours, remainder = divmod(delta.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    # Store non-zero values with their labels
    components = [
        (years, "year"),
        (months, "month"),
        (days, "day"),
        (hours, "hour"),
        (minutes, "minute"),
        (seconds, "second")
    ]

    # Filter out components that are zero
    components = [(value, name) for value, name in components if value > 0]

    # If there are fewer than two components, just return the available values
    if len(components) == 0:
        return "The specified time is very close to now."
    elif len(components) == 1:
        return f"{components[0][0]} {components[0][1]}{'s' if components[0][0] > 1 else ''}"
    
    # Return only the largest two components
    largest_two = components[:2]
    return ', '.join(f"{value} {name}{'s' if value > 1 else ''}" for value, name in largest_two)

def convert_speed(speed):
    if speed > 1000000000:
        return str(round((speed / 1000000000),1)) + " GH/s"
    elif speed > 1000000:
        return str(round((speed / 1000000), 1)) + " MH/s"
    elif speed > 1000:
        return str(round((speed / 1000), 1)) + " KH/s"
    else:
        return str(speed) + " H/s"

def hashcatParser(filepath):
    status = {}
    hashcat_output = open(filepath, 'r')
    for line in hashcat_output:
        # We itterate through entire file with the last value taking precidence
        if line.startswith('{'):
            # found json object
            json_data = json.loads(line)
            #status['Time_Started'] = json_data['time_start']
            status['Time_Estimated'] = "(" + time_difference(json_data['estimated_stop'])+ ")"
            status['Recovered'] = str(json_data['recovered_hashes'][0]) + "/" + str(json_data['recovered_hashes'][1])
            speed = 0
            devices = json_data['devices']
            for device in devices:
                speed = speed + device['speed']
            status['Speed #'] = convert_speed(speed)
    return status

def killHashcat(pid):
    if sys.platform == 'win32':
        print('Hashcat-agent is not supported on windows. But pull requests are welcome')
    else:
        # a wedged hashcat ignores SIGTERM; escalate to SIGKILL after 5s
        _term_then_kill(int(pid), grace=5)

HC_STALL_TIMEOUT = 900  # hashcat re-writes status JSON every 15s; 15 min silence = wedged

def stalled_hashcat_pid(grace=HC_STALL_TIMEOUT):
    """
    Return the pid of a hashcat process whose status output has not moved for
    `grace` seconds, else None. Catches both the wedged-in-Working loop and
    orphans left behind by an agent restart.
    """
    pid = getHashcatPid()
    if not pid:
        return None
    mtimes = []
    with suppress(OSError):
        for f in os.listdir('control/outfiles'):
            if f.startswith('hcoutput_'):
                mtimes.append(os.path.getmtime(os.path.join('control/outfiles', f)))
    if mtimes:
        last_seen = max(mtimes)
    else:
        # no status file yet: judge by process age (covers early startup and
        # orphans from before an agent restart)
        try:
            last_seen = psutil.Process(int(pid)).create_time()
        except psutil.Error:
            return None
    if time.time() - last_seen > grace:
        return pid
    return None

#def uploadCrackFile(file_path, hash_type, task_id):
#    return api.uploadCrackFile(file_path, hash_type, task_id)
def uploadCrackFile(file_path, job_task_id):
    return api.uploadCrackFile(file_path, job_task_id)

def getHashType(hashfile_id):
    return api.getHashType(hashfile_id)

def updateJobTask(job_task_id, task_status):
    return api.updateJobTask(job_task_id, task_status)    

def data_retention_cleanup():
    try:
        response = api.server_settings()
        server_settings = json.loads(response)
    except (KeyError, ValueError, TypeError):
        print('[INFO] hashview-agent.py->data_retention_cleanup() Skipping cleanup: '
              'server returned an unauthorized or unexpected response (agent may not be approved yet).')
        return

    if not server_settings or 'retention_period' not in server_settings[0]:
        print('[INFO] hashview-agent.py->data_retention_cleanup() Skipping cleanup: '
              'no retention_period in server settings.')
        return

    # if a value is set then we process
    if server_settings[0]['retention_period'] != 0:
        
        # Check and remove old files from tmp
        for file in os.listdir('control/tmp'):
            if os.stat('control/tmp/' + file).st_mtime < time.time() - server_settings[0]['retention_period'] * 86400 and file != '.gitignore':
                os.remove('control/tmp/' + file)
                print('[DEBUG] hashview-agent.py->data_retention_cleanup() Removed: control/tmp/' + file)        

        # check and remove files from outfiles
        for file in os.listdir('control/outfiles'):
            if file == '.gitignore':
                print('Found Git Ignore!')
            if os.stat('control/outfiles/' + file).st_mtime < time.time() - server_settings[0]['retention_period'] * 86400 and file != '.gitignore':
                os.remove('control/outfiles/' + file)
                print('[DEBUG] hashview-agent.py->data_retention_cleanup() Removed: control/outfiles/' + file) 

        # check and remove hashfiles
        for file in os.listdir('control/hashes'):
            if file == '.gitignore':
                print('Found Git Ignore!')
            if os.stat('control/hashes/' + file).st_mtime < time.time() - server_settings[0]['retention_period'] * 86400 and file != '.gitignore':
                os.remove('control/hashes/' + file)
                print('[DEBUG] hashview-agent.py->data_retention_cleanup() Removed: control/hashes/' + file)         

if __name__ == '__main__':
    from agent import config

    if args.debug:
        builtins.state = 'debug'
    else:
        builtins.state = 'normal'
    
    # Main loop
    while (1):
        agent_status = ''

        # Check data retention
        data_retention_cleanup()

        # Check if we're currently working on a task
        if getHashcatPid():
            agent_status = 'Working'
            response = send_heartbeat(agent_status, 'somevalue')
            if response['msg'] == 'Canceled':
                print("[*] Looks like we've been canceled.")
            # orphan / wedged hashcat (e.g. left over from an agent restart):
            # no status output for HC_STALL_TIMEOUT seconds -> SIGTERM->SIGKILL
            stalled = stalled_hashcat_pid()
            if stalled:
                print('[!] Stalled hashcat pid %s (no status output for %ss), killing' % (stalled, HC_STALL_TIMEOUT))
                killHashcat(stalled)
        else:
            agent_status = 'Idle'
            # Send Heartbeat
            response = send_heartbeat(agent_status, '')
            if response['msg'] == 'Go Away':
                print("[*] Agent is unauthorized to connect to this server. Please contact Hashview Admin to grant its access.")
            if response['msg'] == 'START':
                # We've been assigned a task
                # First we'll sync our rules
                sync_rules()
                # And our wordlists
                sync_wordlists()
                print("[*] We've been assigned Task Id: " + str(response['job_task_id']))
                job_task = jobTasks(response['job_task_id'])

                # Shouldnt be necessary, but server side sometimes doesnt get set
                updateJobTask(job_task['id'], 'Running')
                # Get the task so that we can get dictionary to find out if its dynamic, so that we can trigger an update 
                # we do a loop of all wordlists (instead of pulling directly) because the /vX/wordlists/<id> is reserved for downloading wordlists
                task = tasks(job_task['task_id'])

                wordlists_list = getWordlists()
                for wordlist in json.loads(wordlists_list):
                    if wordlist['id'] == task['wl_id']:
                        if wordlist['type'] == 'dynamic':
                            print('[*] Task is using a dynamic wordlist. Initiating update')
                            update_response = updateDynamicWordlists(wordlist['id'])
                
                            if update_response['msg'] != 'OK':
                                print('[!] Something broke during the updateing of the dynamic wordlist: ' + str(wordlist['id']))
                            else:
                                print('[*] Update Complete')
                            sync_wordlists()


                # Get Job, so that we can get our hashfile
                job = jobs(job_task['job_id'])

                # Download our hashfile. File name will be generated to match that of whats expected by the jobtask cmd.
                download_hashfile(job['id'], job_task['task_id'], job['hashfile_id'])

                cmd = replaceHashcatBinPath(job_task['command']) + ' --status-json | tee control/outfiles/hcoutput_' + str(job['id']) + '_' + str(job_task['id']) + '.txt'
                print(cmd)

                # run in thread
                hc_result = {}
                thread = Thread(target=run_hashcat, args=(cmd, hc_result))
                thread.start()
                
                was_canceled = False
                watchdog_tripped = False
                task_started = time.time()
                while thread.is_alive():
                    # we sleep 15 seconds because by default, the build crack cmd on hashview server tells hashcat to display output every 15 seconds.
                    time.sleep(15)
                    agent_status = 'Working'
                    try:
                        hc_status = hashcatParser('control/outfiles/hcoutput_' + str(job['id']) + '_' + str(job_task['id']) + '.txt')
                    except OSError:
                        hc_status = {}

                    response = send_heartbeat(agent_status, hc_status)
                    if response['msg'] == 'Canceled':
                        was_canceled = True
                        print('[*] We\'ve been canceled')
                        pid = getHashcatPid()
                        if pid:
                            killHashcat(pid)

                    # watchdog: hashcat re-writes status JSON every
                    # --status-timer (15) seconds; long silence = wedged
                    # process (GPU/driver hang), must not pin us in Working
                    try:
                        last_seen = max(os.path.getmtime('control/outfiles/hcoutput_' + str(job['id']) + '_' + str(job_task['id']) + '.txt'), task_started)
                    except OSError:
                        last_seen = task_started
                    if (not was_canceled
                            and time.time() - last_seen > HC_STALL_TIMEOUT):
                        watchdog_tripped = True
                        print('[!] hashcat produced no status output for %ss - killing it' % HC_STALL_TIMEOUT)
                        pid = getHashcatPid()
                        if pid:
                            killHashcat(pid)
                        thread.join(timeout=30)
                        if thread.is_alive():
                            break
                            
                    # upload cracks
                    crack_file = 'control/outfiles/hc_cracked_' + str(job['id']) + '_' + str(job_task['task_id']) + '.txt'
                    if os.path.exists(crack_file):
                        getHashTypeResponse = getHashType(job['hashfile_id'])
                        if getHashTypeResponse['msg'] == 'OK':
                            #uploadCrackFileResponse = uploadCrackFile(crack_file, getHashTypeResponse['hash_type'], str(job_task['task_id']))
                            uploadCrackFileResponse = uploadCrackFile(crack_file, str(job_task['id']))
                            if uploadCrackFileResponse['msg'] == 'OK':
                                print('[*] Upload Success!')
                    else:
                        print('[*] No Results. Skipping upload.')


                print('[*] Done working')

                # upload cracks
                crack_file = 'control/outfiles/hc_cracked_' + str(job['id']) + '_' + str(job_task['task_id']) + '.txt'
                if os.path.exists(crack_file):
                    getHashTypeResponse = getHashType(job['hashfile_id'])
                    if getHashTypeResponse['msg'] == 'OK':
                        #uploadCrackFileResponse = uploadCrackFile(crack_file, getHashTypeResponse['hash_type'], str(job_task['task_id']))
                        uploadCrackFileResponse = uploadCrackFile(crack_file, str(job_task['id']))
                        if uploadCrackFileResponse['msg'] == 'OK':
                            print('[*] Upload Success!')
                else:
                    print('[*] No Results. Skipping upload.')

                # Set status from what hashcat actually did (see run_hashcat:
                # rc + stderr are captured there)
                rc = hc_result.get('returncode')
                err_txt = hc_result.get('stderr') or ''
                if was_canceled:
                    # server owns the task state after a cancel; the old
                    # unconditional 'Completed' used to paper over this
                    print('[*] Task was canceled; leaving server-side status as-is')
                else:
                    final_status = 'Completed'
                    fail_reason = None
                    if watchdog_tripped:
                        fail_reason = 'watchdog: hashcat wedged (no status output for %ss)' % HC_STALL_TIMEOUT
                    elif rc not in HC_OK_RETURNCODES:
                        fail_reason = 'hashcat exited with rc=%s' % rc
                    else:
                        for marker in HC_FAIL_MARKERS:
                            if marker in err_txt:
                                fail_reason = 'hashcat stderr marker: %s' % marker
                                break
                    if fail_reason is not None:
                        final_status = 'Failed'
                        tail_err = err_txt[-1000:] if err_txt else '(no stderr captured)'
                        print('[!] Task failed: %s' % fail_reason)
                        with suppress(Exception):
                            api.sendError('%s | tail of stderr: %s' % (fail_reason, tail_err))
                    updateJobTaskResponse = updateJobTask(job_task['id'], final_status)
                    try:
                        if updateJobTaskResponse['msg'] == 'OK':
                            print('[*] Task Successfully Set to ' + final_status)
                        with suppress(Exception):
                            pass
                    finally:
                        pass

        print('[*] Sleeping')
        time.sleep(10)
