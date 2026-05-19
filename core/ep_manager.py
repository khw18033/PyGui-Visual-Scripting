"""Simple EP manager to spawn worker processes and send IPC JSON commands.
This module provides a minimal API for the GUI to start/stop workers and send commands.
"""
import os
import sys
import json
import socket
import subprocess
import threading
import time

WORKERS = {}


def _send_and_recv(host, port, obj, timeout=2.0):
    data = json.dumps(obj, ensure_ascii=False) + '\n'
    with socket.create_connection((host, port), timeout=timeout) as s:
        s.sendall(data.encode('utf-8'))
        s.settimeout(timeout)
        resp = b''
        try:
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                resp += chunk
                if b'\n' in resp:
                    line, _ = resp.split(b'\n', 1)
                    return json.loads(line.decode('utf-8'))
        except socket.timeout:
            return None


def spawn_worker(instance_id, worker_port, ep_ip, ep_port, flask_port=None):
    env = os.environ.copy()
    env['EP_INSTANCE'] = instance_id
    env['WORKER_PORT'] = str(worker_port)
    env['EP_IP'] = ep_ip
    env['EP_PORT'] = str(ep_port)
    if flask_port:
        env['FLASK_PORT'] = str(flask_port)

    cmd = [sys.executable, '-u', os.path.join('nodes', 'robots', 'ep01_worker.py')]
    p = subprocess.Popen(cmd, env=env)
    WORKERS[instance_id] = {'proc': p, 'port': worker_port, 'ep_ip': ep_ip, 'ep_port': ep_port}
    # allow process to initialize
    time.sleep(0.3)
    return p


def stop_worker(instance_id):
    w = WORKERS.get(instance_id)
    if not w:
        return False
    p = w.get('proc')
    try:
        p.terminate()
    except Exception:
        pass
    WORKERS.pop(instance_id, None)
    return True


def start_workers(configs):
    """configs: list of dicts with keys id, port, ep_ip, ep_port, flask (optional)
    """
    procs = {}
    for c in configs:
        p = spawn_worker(c['id'], c['port'], c['ep_ip'], c['ep_port'], c.get('flask'))
        procs[c['id']] = p
    return procs


def send_cmd(instance_id, cmd, args=None, req_id=1, timeout=2.0):
    w = WORKERS.get(instance_id)
    if not w:
        return None
    port = w['port']
    try:
        req = {'type': 'cmd', 'req_id': req_id, 'cmd': cmd, 'args': args or {}}
        return _send_and_recv('127.0.0.1', port, req, timeout=timeout)
    except Exception:
        return None


def get_state(instance_id, timeout=1.0):
    return send_cmd(instance_id, 'get_state', {}, req_id=99, timeout=timeout)


def list_workers():
    return list(WORKERS.keys())
