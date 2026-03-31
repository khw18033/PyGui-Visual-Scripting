import os
import time
import json
import socket
import threading

from nodes.base import BaseNode, BaseRobotDriver
from core.engine import generate_uuid, PortType, write_log, node_registry
import core.engine as engine_module

try:
    import cv2
    import numpy as np
    HAS_CV2 = True
except ImportError:
    cv2 = None
    np = None
    HAS_CV2 = False

try:
    from flask import Flask, Response
    HAS_FLASK = True
except ImportError:
    Flask = None
    Response = None
    HAS_FLASK = False


# ================= [Go1 Globals] =================
go1_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
go1_sock.setblocking(False)

GO1_IP = "192.168.12.1"
GO1_PORT = 8082

GO1_UNITY_IP = "192.168.50.246"
UNITY_STATE_PORT = 15101
UNITY_CMD_PORT = 15102
UNITY_RX_PORT = 15100

V_MAX = 0.8
S_MAX = 0.8
W_MAX = 2.5

GO1_SEND_INTERVAL = 0.05  # 20Hz
UNITY_TIMEOUT = 0.2

go1_target_vel = {
    'vx': 0.0,
    'vy': 0.0,
    'vyaw': 0.0,
    'body_height': 0.0,
}

go1_unity_data = {
    'vx': 0.0,
    'vy': 0.0,
    'wz': 0.0,
    'body_height': 0.0,
    'estop': 0,
    'active': False,
    'last_rx': 0.0,
}

go1_dashboard = {
    "status": "Idle",
    "hw_link": "Offline",
    "unity_link": "Waiting",
}


def _clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def _has_go1_nodes():
    return any(n.type_str.startswith("GO1_") for n in node_registry.values())


def get_go1_rtsp_url():
    return f"rtsp://{GO1_IP}:8554/live"


def _build_drive_packet(vx, vy, vyaw, body_height):
    return f"cmd_vel {vx:.3f} {vy:.3f} {vyaw:.3f} {body_height:.3f}"


def _send_go1_command(cmd_str):
    try:
        go1_sock.sendto(cmd_str.encode('utf-8'), (GO1_IP, GO1_PORT))
        return True
    except Exception:
        return False


def go1_keepalive_thread():
    sock_rx_unity = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock_rx_unity.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock_rx_unity.bind(("0.0.0.0", UNITY_RX_PORT))
        sock_rx_unity.setblocking(False)
    except Exception:
        pass

    sock_tx_state = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock_tx_cmd = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    seq = 0
    world_x = 0.0
    world_z = 0.0
    last_time = time.monotonic()

    while True:
        now = time.monotonic()
        dt = max(1e-3, now - last_time)
        last_time = now

        while True:
            try:
                raw, _ = sock_rx_unity.recvfrom(512)
            except Exception:
                break
            try:
                parts = raw.decode('utf-8', errors='ignore').strip().split()
                if len(parts) >= 4:
                    go1_unity_data['vx'] = float(parts[0])
                    go1_unity_data['vy'] = float(parts[1])
                    go1_unity_data['wz'] = float(parts[2])
                    go1_unity_data['estop'] = int(parts[3])
                    if len(parts) >= 5:
                        go1_unity_data['body_height'] = float(parts[4])
                    go1_unity_data['last_rx'] = now
            except Exception:
                continue

        go1_unity_data['active'] = (now - go1_unity_data.get('last_rx', 0.0)) <= UNITY_TIMEOUT
        go1_dashboard['unity_link'] = "Active" if go1_unity_data['active'] else "Waiting"

        use_unity = go1_unity_data['active'] and go1_unity_data.get('estop', 0) == 0
        if use_unity:
            tx = _clamp(float(go1_unity_data['vx']), -V_MAX, V_MAX)
            ty = _clamp(float(go1_unity_data['vy']), -S_MAX, S_MAX)
            tz = _clamp(float(go1_unity_data['wz']), -W_MAX, W_MAX)
            bh = _clamp(float(go1_unity_data.get('body_height', go1_target_vel['body_height'])), -0.12, 0.12)
            go1_target_vel.update({'vx': tx, 'vy': ty, 'vyaw': tz, 'body_height': bh})

        running = bool(engine_module.is_running)
        active_nodes = _has_go1_nodes()

        vx = _clamp(float(go1_target_vel['vx']), -V_MAX, V_MAX) if (running and active_nodes) else 0.0
        vy = _clamp(float(go1_target_vel['vy']), -S_MAX, S_MAX) if (running and active_nodes) else 0.0
        vyaw = _clamp(float(go1_target_vel['vyaw']), -W_MAX, W_MAX) if (running and active_nodes) else 0.0
        body_height = _clamp(float(go1_target_vel['body_height']), -0.12, 0.12)

        sent = _send_go1_command(_build_drive_packet(vx, vy, vyaw, body_height))
        go1_dashboard['hw_link'] = "Online" if sent else "Offline"

        if running and active_nodes and (abs(vx) > 1e-4 or abs(vy) > 1e-4 or abs(vyaw) > 1e-4):
            go1_dashboard['status'] = "Running"
        else:
            go1_dashboard['status'] = "Idle"

        world_x += vx * dt
        world_z += vy * dt

        seq += 1
        estop = 1 if (abs(vx) < 1e-6 and abs(vy) < 1e-6 and abs(vyaw) < 1e-6) else 0
        state_msg = (
            f"{seq} {time.time() * 1000.0:.1f} {world_x:.6f} {world_z:.6f} "
            f"{vyaw:.6f} {vx:.3f} {vy:.3f} {vyaw:.3f} {estop} 2"
        )
        cmd_msg = f"{vx:.3f} {vy:.3f} {vyaw:.3f} {estop}"

        try:
            sock_tx_state.sendto(state_msg.encode('utf-8'), (GO1_UNITY_IP, UNITY_STATE_PORT))
            sock_tx_cmd.sendto(cmd_msg.encode('utf-8'), (GO1_UNITY_IP, UNITY_CMD_PORT))
        except Exception:
            pass

        time.sleep(GO1_SEND_INTERVAL)


# ================= [Go1 Driver/Control Nodes] =================
class Go1RobotDriver(BaseRobotDriver):
    def __init__(self):
        self.last_write_time = 0.0
        self.write_interval = GO1_SEND_INTERVAL

    def get_ui_schema(self):
        return [
            ('vx', "Vx In", 0.0),
            ('vy', "Vy In", 0.0),
            ('vyaw', "Yaw In", 0.0),
            ('body_height', "BodyH", 0.0),
        ]

    def get_settings_schema(self):
        return [
            ('speed_scale', "Speed", 1.0),
        ]

    def execute_command(self, inputs, settings):
        scale = max(0.0, float(settings.get('speed_scale', 1.0)))

        if inputs.get('vx') is not None:
            go1_target_vel['vx'] = _clamp(float(inputs['vx']) * scale, -V_MAX, V_MAX)
        if inputs.get('vy') is not None:
            go1_target_vel['vy'] = _clamp(float(inputs['vy']) * scale, -S_MAX, S_MAX)
        if inputs.get('vyaw') is not None:
            go1_target_vel['vyaw'] = _clamp(float(inputs['vyaw']) * scale, -W_MAX, W_MAX)
        if inputs.get('body_height') is not None:
            go1_target_vel['body_height'] = _clamp(float(inputs['body_height']), -0.12, 0.12)

        return dict(go1_target_vel)


class Go1ActionNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "Go1 Action", "GO1_ACTION")
        self.in_flow = generate_uuid()
        self.inputs[self.in_flow] = PortType.FLOW
        self.out_flow = generate_uuid()
        self.outputs[self.out_flow] = PortType.FLOW
        self.state['action'] = "Stand Up"

    def execute(self):
        action = self.state.get('action', 'Stand Up')

        if action == "Stand Up":
            _send_go1_command("stand")
        elif action == "Lie Down":
            _send_go1_command("down")
            go1_target_vel.update({'vx': 0.0, 'vy': 0.0, 'vyaw': 0.0})
        elif action == "Walk Mode":
            _send_go1_command("walk")
        elif action == "Dance":
            _send_go1_command("dance")

        write_log(f"Go1 Action: {action}")
        return self.out_flow


class Go1KeyboardNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "Keyboard (Go1)", "GO1_KEYBOARD")
        self.out_vx = generate_uuid()
        self.outputs[self.out_vx] = PortType.DATA
        self.out_vy = generate_uuid()
        self.outputs[self.out_vy] = PortType.DATA
        self.out_vyaw = generate_uuid()
        self.outputs[self.out_vyaw] = PortType.DATA
        self.out_body_height = generate_uuid()
        self.outputs[self.out_body_height] = PortType.DATA
        self.out_flow = generate_uuid()
        self.outputs[self.out_flow] = PortType.FLOW

        self.step_v = 0.2
        self.step_yaw = 0.4
        self.step_body_h = 0.01
        self.cooldown = 0.05
        self.last_input_time = 0.0

    def execute(self):
        if self.state.get('is_focused', False):
            return self.out_flow

        now = time.time()
        if now - self.last_input_time > self.cooldown:
            vx = 0.0
            vy = 0.0
            vyaw = 0.0

            key_mode = self.state.get("keys", "WASD")
            if key_mode == "WASD":
                if self.state.get("W"):
                    vx = self.step_v
                if self.state.get("S"):
                    vx = -self.step_v
                if self.state.get("A"):
                    vy = self.step_v
                if self.state.get("D"):
                    vy = -self.step_v
            else:
                if self.state.get("UP"):
                    vx = self.step_v
                if self.state.get("DOWN"):
                    vx = -self.step_v
                if self.state.get("LEFT"):
                    vy = self.step_v
                if self.state.get("RIGHT"):
                    vy = -self.step_v

            if self.state.get("Q"):
                vyaw = self.step_yaw
            if self.state.get("E"):
                vyaw = -self.step_yaw

            if self.state.get("Z"):
                go1_target_vel['body_height'] = _clamp(go1_target_vel['body_height'] + self.step_body_h, -0.12, 0.12)
            if self.state.get("X"):
                go1_target_vel['body_height'] = _clamp(go1_target_vel['body_height'] - self.step_body_h, -0.12, 0.12)

            if vx != 0.0 or vy != 0.0 or vyaw != 0.0:
                go1_target_vel['vx'] = vx
                go1_target_vel['vy'] = vy
                go1_target_vel['vyaw'] = vyaw
                self.last_input_time = now

        self.output_data[self.out_vx] = go1_target_vel['vx']
        self.output_data[self.out_vy] = go1_target_vel['vy']
        self.output_data[self.out_vyaw] = go1_target_vel['vyaw']
        self.output_data[self.out_body_height] = go1_target_vel['body_height']
        return self.out_flow


class Go1UnityNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "Unity Logic (Go1)", "GO1_UNITY")
        self.data_in_id = generate_uuid()
        self.inputs[self.data_in_id] = PortType.DATA
        self.out_vx = generate_uuid()
        self.outputs[self.out_vx] = PortType.DATA
        self.out_vy = generate_uuid()
        self.outputs[self.out_vy] = PortType.DATA
        self.out_vyaw = generate_uuid()
        self.outputs[self.out_vyaw] = PortType.DATA
        self.out_body_height = generate_uuid()
        self.outputs[self.out_body_height] = PortType.DATA
        self.out_flow = generate_uuid()
        self.outputs[self.out_flow] = PortType.FLOW

        self.last_processed_json = ""

    def execute(self):
        raw_json = self.fetch_input_data(self.data_in_id)

        if raw_json and raw_json != self.last_processed_json:
            self.last_processed_json = raw_json
            try:
                payload = json.loads(raw_json)
                go1_unity_data['vx'] = float(payload.get('vx', go1_unity_data['vx']))
                go1_unity_data['vy'] = float(payload.get('vy', go1_unity_data['vy']))
                go1_unity_data['wz'] = float(payload.get('wz', go1_unity_data['wz']))
                go1_unity_data['estop'] = int(payload.get('estop', go1_unity_data['estop']))
                go1_unity_data['body_height'] = float(payload.get('body_height', go1_unity_data['body_height']))
                go1_unity_data['last_rx'] = time.monotonic()
            except Exception as e:
                write_log(f"Go1 Unity JSON Error: {e}")

        self.output_data[self.out_vx] = go1_unity_data['vx']
        self.output_data[self.out_vy] = go1_unity_data['vy']
        self.output_data[self.out_vyaw] = go1_unity_data['wz']
        self.output_data[self.out_body_height] = go1_unity_data.get('body_height', 0.0)
        return self.out_flow


# ================= [Vision Nodes] =================
_default_camera_matrix = None
_default_dist_coeffs = None
if HAS_CV2:
    try:
        calib_dir = "Calib_data"
        _default_camera_matrix = np.load(os.path.join(calib_dir, "K1.npy"))
        _default_dist_coeffs = np.load(os.path.join(calib_dir, "D1.npy"))
    except Exception:
        _default_camera_matrix = np.array([[640.0, 0.0, 320.0], [0.0, 640.0, 240.0], [0.0, 0.0, 1.0]], dtype=np.float32)
        _default_dist_coeffs = np.zeros((4, 1), dtype=np.float32)

_aruco_dict = None
_aruco_detector = None
if HAS_CV2 and hasattr(cv2, 'aruco'):
    try:
        _aruco_dict = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
        _aruco_detector = cv2.aruco.ArucoDetector(_aruco_dict, cv2.aruco.DetectorParameters())
    except Exception:
        _aruco_dict = None
        _aruco_detector = None


class VideoSourceNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "Video Source", "VIDEO_SRC")
        self.out_frame = generate_uuid()
        self.outputs[self.out_frame] = PortType.DATA

        self.state['url'] = get_go1_rtsp_url()
        self.state['is_running'] = False

        self._cap = None
        self._thread = None
        self._lock = threading.Lock()
        self._latest_frame = None
        self._running = False
        self._opened_url = ""

    def _reader_loop(self):
        while self._running:
            if not self._cap:
                time.sleep(0.05)
                continue
            ok, frame = self._cap.read()
            if ok:
                with self._lock:
                    self._latest_frame = frame
            else:
                time.sleep(0.05)

    def _start_capture(self, url):
        if not HAS_CV2:
            return
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass

        if str(url).isdigit():
            self._cap = cv2.VideoCapture(int(url))
        else:
            self._cap = cv2.VideoCapture(url)
        self._opened_url = str(url)

        if self._thread is None or not self._thread.is_alive():
            self._running = True
            self._thread = threading.Thread(target=self._reader_loop, daemon=True)
            self._thread.start()

    def _stop_capture(self):
        self._running = False
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None

    def execute(self):
        if not HAS_CV2:
            return None

        run_flag = bool(self.state.get('is_running', False))
        url = str(self.state.get('url', get_go1_rtsp_url()))

        if run_flag:
            if self._cap is None or self._opened_url != url:
                self._start_capture(url)
        else:
            self._stop_capture()
            self.output_data[self.out_frame] = None
            return None

        with self._lock:
            frame = self._latest_frame.copy() if self._latest_frame is not None else None

        self.output_data[self.out_frame] = frame
        return None


class FisheyeUndistortNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "Fisheye Undistort", "VIS_FISHEYE")
        self.in_frame = generate_uuid()
        self.inputs[self.in_frame] = PortType.DATA
        self.out_frame = generate_uuid()
        self.outputs[self.out_frame] = PortType.DATA

    def execute(self):
        frame = self.fetch_input_data(self.in_frame)
        if frame is None or not HAS_CV2:
            return None

        try:
            undistorted = cv2.fisheye.undistortImage(
                frame,
                _default_camera_matrix,
                _default_dist_coeffs,
                Knew=_default_camera_matrix,
            )
            self.output_data[self.out_frame] = undistorted
        except Exception:
            self.output_data[self.out_frame] = frame
        return None


class ArUcoDetectNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "ArUco Detect", "VIS_ARUCO")
        self.in_frame = generate_uuid()
        self.inputs[self.in_frame] = PortType.DATA
        self.out_frame = generate_uuid()
        self.outputs[self.out_frame] = PortType.DATA
        self.out_data = generate_uuid()
        self.outputs[self.out_data] = PortType.DATA

    def execute(self):
        frame = self.fetch_input_data(self.in_frame)
        if frame is None or not HAS_CV2 or _aruco_detector is None:
            self.output_data[self.out_frame] = frame
            self.output_data[self.out_data] = []
            return None

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        corners, ids, _ = _aruco_detector.detectMarkers(gray)

        detected = []
        draw = frame.copy()

        if ids is not None and len(ids) > 0:
            try:
                cv2.aruco.drawDetectedMarkers(draw, corners, ids)
            except Exception:
                pass

            for i, marker_id in enumerate(ids.flatten()):
                c = corners[i][0]
                cx = float((c[0][0] + c[2][0]) * 0.5)
                cy = float((c[0][1] + c[2][1]) * 0.5)
                detected.append({
                    "id": int(marker_id),
                    "cx": round(cx, 2),
                    "cy": round(cy, 2),
                })

        self.output_data[self.out_frame] = draw
        self.output_data[self.out_data] = detected
        return None


_flask_app = Flask(__name__) if HAS_FLASK else None
_flask_latest_jpg = None
_flask_lock = threading.Lock()
_flask_thread_started = False


if HAS_FLASK:
    @_flask_app.route('/video_feed')
    def _video_feed():
        def generate():
            while True:
                frame = None
                with _flask_lock:
                    frame = _flask_latest_jpg
                if frame is not None:
                    yield b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + frame + b'\r\n'
                time.sleep(0.03)

        return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=frame')


class FlaskStreamNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "Flask Stream", "VIS_FLASK")
        self.in_frame = generate_uuid()
        self.inputs[self.in_frame] = PortType.DATA
        self.state['port'] = 5000
        self.state['is_running'] = False
        self._started_local = False

    def _start_server_once(self):
        global _flask_thread_started
        if not HAS_FLASK or _flask_app is None:
            return
        if _flask_thread_started:
            return

        port = int(self.state.get('port', 5000))

        def run_server():
            _flask_app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

        threading.Thread(target=run_server, daemon=True).start()
        _flask_thread_started = True
        write_log(f"Flask Stream Started: http://0.0.0.0:{port}/video_feed")

    def execute(self):
        if not HAS_CV2 or not HAS_FLASK:
            return None

        if bool(self.state.get('is_running', False)):
            if not self._started_local:
                self._start_server_once()
                self._started_local = True

            frame = self.fetch_input_data(self.in_frame)
            if frame is not None:
                ok, buf = cv2.imencode('.jpg', frame)
                if ok:
                    with _flask_lock:
                        global _flask_latest_jpg
                        _flask_latest_jpg = buf.tobytes()

        return None
