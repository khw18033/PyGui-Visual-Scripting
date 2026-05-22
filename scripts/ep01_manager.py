import os
import sys
import time
import json
import socket
import subprocess
import threading


def send_and_recv(host, port, obj, timeout=2.0):
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
    return p


def main():
    # Example config for two workers
    workers = [
        {'id': 'ep01_a', 'port': 12000, 'ep_ip': '192.168.42.10', 'ep_port': 40900, 'flask': 5050},
        {'id': 'ep01_b', 'port': 12001, 'ep_ip': '192.168.42.11', 'ep_port': 40901, 'flask': 5051},
    ]

    procs = []
    try:
        for w in workers:
            print(f"Spawning worker {w['id']} on port {w['port']}")
            p = spawn_worker(w['id'], w['port'], w['ep_ip'], w['ep_port'], w['flask'])
            procs.append((w, p))

        # give workers time to start
        time.sleep(1.2)

        # send connect commands
        for w, p in procs:
            req = {'type': 'cmd', 'req_id': 1, 'cmd': 'connect', 'args': {'conn_type': 'sta'}}
            try:
                resp = send_and_recv('127.0.0.1', w['port'], req)
                print(f"connect resp from {w['id']}: {resp}")
            except Exception as e:
                print('error sending connect', e)

        # query state after a short wait
        time.sleep(1.0)
        for w, p in procs:
            try:
                req = {'type': 'cmd', 'req_id': 2, 'cmd': 'get_state', 'args': {}}
                resp = send_and_recv('127.0.0.1', w['port'], req)
                print(f"state from {w['id']}: {resp}")
            except Exception as e:
                print('error getting state', e)

        print('\nManager interactive prompt. Type: "drive <id> x y z" or "action <id> name" or "quit"')
        while True:
            line = input('> ').strip()
            if not line:
                continue
            if line == 'quit':
                break
            parts = line.split()
            if parts[0] == 'drive' and len(parts) >= 5:
                _, iid, sx, sy, sz = parts[:5]
                target = next((t for t, _p in procs if t['id'] == iid), None)
                if not target:
                    print('unknown id')
                    continue
                req = {'type': 'cmd', 'req_id': 10, 'cmd': 'drive_wheels', 'args': {'x': float(sx), 'y': float(sy), 'z': float(sz)}}
                resp = send_and_recv('127.0.0.1', target['port'], req)
                print('resp', resp)
            elif parts[0] == 'action' and len(parts) >= 3:
                _, iid, name = parts[:3]
                target = next((t for t, _p in procs if t['id'] == iid), None)
                if not target:
                    print('unknown id')
                    continue
                req = {'type': 'cmd', 'req_id': 11, 'cmd': 'action', 'args': {'name': name}}
                resp = send_and_recv('127.0.0.1', target['port'], req)
                print('resp', resp)
            else:
                print('unknown command')

    finally:
        print('terminating workers...')
        for w, p in procs:
            try:
                p.terminate()
            except Exception:
                pass


if __name__ == '__main__':
    main()
