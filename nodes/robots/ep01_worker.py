import os
import sys
import time
import json
import socket
import threading
import traceback
from typing import Optional

try:
    from nodes.robots import ep01 as ep01_mod
except Exception:
    # allow running the worker from repo root even if package imports differ
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
    from nodes.robots import ep01 as ep01_mod


DEFAULT_PORT = int(os.getenv('WORKER_PORT', '12000'))
HOST = '127.0.0.1'


def send_json(conn, obj):
    data = json.dumps(obj, ensure_ascii=False) + '\n'
    conn.sendall(data.encode('utf-8'))


class WorkerServer(threading.Thread):
    def __init__(self, port=DEFAULT_PORT):
        super().__init__(daemon=True)
        self.port = port
        self.sock = None
        self.should_stop = threading.Event()
        self.client_handlers = []

    def run(self):
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.sock.bind((HOST, self.port))
            self.sock.listen(5)
            print(f"[worker] IPC server listening on {HOST}:{self.port}")
            while not self.should_stop.is_set():
                try:
                    self.sock.settimeout(0.5)
                    conn, addr = self.sock.accept()
                    handler = threading.Thread(target=self.handle_client, args=(conn, addr), daemon=True)
                    handler.start()
                    self.client_handlers.append(handler)
                except socket.timeout:
                    continue
        except Exception as e:
            print('[worker] server error:', e)
            traceback.print_exc()
        finally:
            try:
                if self.sock:
                    self.sock.close()
            except Exception:
                pass

    def stop(self):
        self.should_stop.set()
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass

    def handle_client(self, conn: socket.socket, addr):
        with conn:
            buf = b''
            try:
                while True:
                    data = conn.recv(4096)
                    if not data:
                        break
                    buf += data
                    while b'\n' in buf:
                        line, buf = buf.split(b'\n', 1)
                        try:
                            msg = json.loads(line.decode('utf-8'))
                        except Exception as e:
                            send_json(conn, {'type': 'error', 'msg': f'json decode error: {e}'})
                            continue
                        resp = self.process_cmd(msg)
                        if resp is not None:
                            send_json(conn, resp)
            except Exception as e:
                print('[worker] client handler error', e)
                traceback.print_exc()

    def process_cmd(self, msg: dict) -> Optional[dict]:
        try:
            mtype = msg.get('type')
            if mtype == 'cmd':
                cmd = msg.get('cmd')
                req_id = msg.get('req_id')
                args = msg.get('args') or {}
                # dispatch
                if cmd == 'connect':
                    # call connect in a background thread to avoid blocking
                    conn_type = args.get('conn_type', 'sta')
                    sn = args.get('sn')
                    robot_ip = args.get('robot_ip')
                    try:
                        ep01_mod.ensure_ep_comm_thread_started()
                    except Exception:
                        pass
                    t = threading.Thread(target=ep01_mod.connect_ep_thread_func, args=(conn_type, sn, robot_ip), daemon=True)
                    t.start()
                    return {'type': 'resp', 'req_id': req_id, 'ok': True, 'result': {'msg': 'connect started'}}
                elif cmd == 'scan_sta':
                    try:
                        timeout = float(args.get('timeout', 3.0))
                    except Exception:
                        timeout = 3.0
                    try:
                        robots = ep01_mod.scan_ep_sta_robots(timeout=timeout)
                        return {'type': 'resp', 'req_id': req_id, 'ok': True, 'result': {'robots': robots}}
                    except Exception as e:
                        return {'type': 'resp', 'req_id': req_id, 'ok': False, 'result': {'error': str(e)}}
                elif cmd == 'disconnect':
                    try:
                        if ep01_mod.ep_robot_inst is not None:
                            try:
                                ep01_mod.ep_robot_inst.close()
                            except Exception:
                                pass
                            ep01_mod.ep_robot_inst = None
                        ep01_mod.ep_dashboard['hw_link'] = 'Offline'
                        return {'type': 'resp', 'req_id': req_id, 'ok': True, 'result': {'msg': 'disconnected'}}
                    except Exception as e:
                        return {'type': 'resp', 'req_id': req_id, 'ok': False, 'result': {'error': str(e)}}
                elif cmd == 'action':
                    name = args.get('name')
                    ok = False
                    try:
                        ok = ep01_mod.send_ep_command(name)
                    except Exception as e:
                        return {'type': 'resp', 'req_id': req_id, 'ok': False, 'result': {'error': str(e)}}
                    return {'type': 'resp', 'req_id': req_id, 'ok': ok, 'result': {}}
                elif cmd == 'drive_wheels':
                    x = float(args.get('x', 0.0))
                    y = float(args.get('y', 0.0))
                    z = float(args.get('z', 0.0))
                    try:
                        # set intent; ep_comm_thread in this worker will pick it up
                        ep01_mod.ep_node_intent['vx'] = x
                        ep01_mod.ep_node_intent['vy'] = y
                        ep01_mod.ep_node_intent['wz'] = z
                        ep01_mod.ep_node_intent['trigger_time'] = time.monotonic()
                        return {'type': 'resp', 'req_id': req_id, 'ok': True, 'result': {}}
                    except Exception as e:
                        return {'type': 'resp', 'req_id': req_id, 'ok': False, 'result': {'error': str(e)}}
                elif cmd == 'get_state':
                    try:
                        state = {
                            'ep_dashboard': ep01_mod.ep_dashboard,
                            'ep_state': ep01_mod.ep_state,
                            'ep_camera_state': ep01_mod.ep_camera_state,
                            'ep_server_json_data': ep01_mod.ep_server_json_data,
                        }
                        return {'type': 'resp', 'req_id': req_id, 'ok': True, 'result': state}
                    except Exception as e:
                        return {'type': 'resp', 'req_id': req_id, 'ok': False, 'result': {'error': str(e)}}
            else:
                return {'type': 'error', 'msg': 'unsupported message type'}
        except Exception as e:
            traceback.print_exc()
            return {'type': 'error', 'msg': str(e)}


def state_broadcaster(stop_event: threading.Event, port: int, interval: float = 1.0):
    # Connect to manager if it listens? For simplicity, we won't push unsolicited messages.
    # Instead this placeholder exists to allow future push implementation.
    while not stop_event.is_set():
        time.sleep(interval)


def main():
    port = int(os.getenv('WORKER_PORT', str(DEFAULT_PORT)))
    instance = os.getenv('EP_INSTANCE', 'ep01')
    print(f"[worker:{instance}] starting on port {port}")

    server = WorkerServer(port=port)
    server.start()

    stop_event = threading.Event()
    broadcaster = threading.Thread(target=state_broadcaster, args=(stop_event, port), daemon=True)
    broadcaster.start()

    try:
        # keep main thread alive
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print('[worker] stopping...')
    finally:
        stop_event.set()
        server.stop()


if __name__ == '__main__':
    main()
