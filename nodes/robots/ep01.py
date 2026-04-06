import time
import socket
import threading
import math
import sys
import os
from unittest.mock import MagicMock
from nodes.base import BaseNode, BaseRobotDriver
from core.engine import generate_uuid, PortType, write_log, HwStatus

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    cv2 = None
    HAS_CV2 = False

try:
    from flask import Flask, Response
    HAS_FLASK = True
except ImportError:
    Flask = None
    Response = None
    HAS_FLASK = False

# ================= [EP Globals & Network] =================
EP_USE_MEDIA_MOCK = os.getenv("EP_USE_MEDIA_MOCK", "0").strip().lower() in ("1", "true", "yes", "on")
if EP_USE_MEDIA_MOCK:
    sys.modules['libmedia_codec'] = MagicMock()
    sys.modules['libmedia_codec.media_codec'] = MagicMock()
    write_log("EP: libmedia_codec mock enabled (EP_USE_MEDIA_MOCK=1)")

try:
    from robomaster import robot
    HAS_ROBOMASTER_SDK = True
except ImportError as e:
    HAS_ROBOMASTER_SDK = False
    write_log(f"Warning: 'robomaster' module not found. ({e})")

ep_cmd_sock = None
ep_robot_inst = None
EP_IP = "192.168.42.2" # USB 테더링 기본 IP (라우터 연결 시 해당 IP로 변경 필요)
EP_PORT = 40924

ep_dashboard = {"hw_link": "Offline", "sn": "Unknown", "conn_type": "None"}
ep_state = {
    "battery": -1,
    "pos_x": 0.0,
    "pos_y": 0.0,
    "speed": 0.0,
    "accel_x": 0.0,
    "accel_y": 0.0,
    "accel_z": 0.0,
}
ep_node_intent = {"vx": 0.0, "vy": 0.0, "wz": 0.0, "stop": False, "trigger_time": time.monotonic()}
ep_target_vel = {'vx': 0.0, 'vy': 0.0, 'vz': 0.0} # vz = yaw

ep_camera_state = {
    "status": "Stopped",
    "source": "none",
    "url": "rtsp://192.168.42.2/live",
}

_ep_cam_lock = threading.Lock()
_ep_cam_cap = None
_ep_cam_sdk_started = False
_ep_cam_last_frame = None
_ep_flask_app = Flask(__name__) if HAS_FLASK else None
_ep_flask_latest_jpg = None
_ep_flask_lock = threading.Lock()
_ep_flask_thread_started = False

if HAS_FLASK:
    @_ep_flask_app.route('/ep_video_feed')
    def _ep_video_feed():
        def generate():
            while True:
                frame = None
                with _ep_flask_lock:
                    frame = _ep_flask_latest_jpg
                if frame is not None:
                    yield b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + frame + b'\r\n'
                time.sleep(0.03)

        return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=frame')

def init_ep_network(ip=EP_IP):
    global EP_IP
    EP_IP = ip
    if not HAS_ROBOMASTER_SDK:
        ep_dashboard["hw_link"] = "Simulation"
        return
    ep_dashboard["hw_link"] = "Offline"

def ep_sub_pos(info):
    ep_state['pos_x'], ep_state['pos_y'], _ = info

def ep_sub_vel(info):
    ep_state['speed'] = math.sqrt(info[0] ** 2 + info[1] ** 2)

def ep_sub_bat(info):
    ep_state['battery'] = int(info[0]) if isinstance(info, (tuple, list)) else int(info)

def ep_sub_imu(info):
    ep_state['accel_x'], ep_state['accel_y'], ep_state['accel_z'] = info[:3]

def connect_ep_thread_func(conn_mode):
    global ep_robot_inst

    if not HAS_ROBOMASTER_SDK:
        ep_dashboard["hw_link"] = "Simulation"
        write_log("EP_DEBUG: 'robomaster' SDK not found. Skipping connection.")
        return

    ep_dashboard["hw_link"] = f"Connecting ({conn_mode.upper()})..."
    write_log(f"System: Attempting EP Connection via {conn_mode.upper()}...")

    if ep_robot_inst is not None:
        write_log("EP_DEBUG: Cleaning up previous robot instance...")
        try:
            ep_robot_inst.close()
            write_log("EP_DEBUG: Previous instance closed.")
        except Exception as e:
            write_log(f"EP_DEBUG: Error closing previous instance: {e}")

    try:
        write_log("EP_DEBUG: Instantiating robot.Robot()...")
        ep_robot_inst = robot.Robot()

        write_log(f"EP_DEBUG: Calling initialize(conn_type='{conn_mode}')...")
        ep_robot_inst.initialize(conn_type=conn_mode)
        write_log("EP_DEBUG: Initialize completed successfully.")

        write_log("EP_DEBUG: Getting Serial Number...")
        ep_dashboard["sn"] = ep_robot_inst.get_sn()

        ep_dashboard["hw_link"] = f"Online ({conn_mode.upper()})"
        ep_dashboard["conn_type"] = conn_mode.upper()
        write_log(f"System: EP Connected! (SN: {ep_dashboard['sn']})")

        write_log("EP_DEBUG: Subscribing to telemetry (Pos, Vel, Bat, IMU)...")
        ep_robot_inst.chassis.sub_position(freq=1, callback=ep_sub_pos)
        ep_robot_inst.chassis.sub_velocity(freq=5, callback=ep_sub_vel)
        ep_robot_inst.battery.sub_battery_info(freq=1, callback=ep_sub_bat)
        ep_robot_inst.chassis.sub_imu(freq=10, callback=ep_sub_imu)
        write_log("EP_DEBUG: All subscriptions active.")

    except Exception as e:
        ep_robot_inst = None
        ep_dashboard["hw_link"] = "Offline"
        import traceback
        error_details = traceback.format_exc()
        write_log(f"EP Connect Error (Detailed): {e}")
        print(f"\n[EP_CRITICAL_ERROR_TRACE]\n{error_details}\n")

def btn_connect_ep_sta(sender=None, app_data=None):
    threading.Thread(target=connect_ep_thread_func, args=("sta",), daemon=True).start()

def btn_connect_ep_ap(sender=None, app_data=None):
    threading.Thread(target=connect_ep_thread_func, args=("ap",), daemon=True).start()

def send_ep_command(cmd_str):
    global ep_cmd_sock

    if ep_robot_inst is not None:
        try:
            if cmd_str == "led_red":
                ep_robot_inst.led.set_led(comp="all", r=255, g=0, b=0, effect="on")
                return True
            if cmd_str == "led_blue":
                ep_robot_inst.led.set_led(comp="all", r=0, g=0, b=255, effect="on")
                return True
            if cmd_str == "blaster_fire":
                ep_robot_inst.blaster.fire(times=1)
                return True
            if cmd_str == "arm_center":
                ep_robot_inst.robotic_arm.moveto(x=100, y=100)
                return True
            if cmd_str == "grip_open":
                ep_robot_inst.robotic_gripper.open(power=50)
                return True
            if cmd_str == "grip_close":
                ep_robot_inst.robotic_gripper.close(power=50)
                return True
        except Exception:
            pass

    udp_map = {
        "led_red": "led control comp all r 255 g 0 b 0 effect solid;",
        "led_blue": "led control comp all r 0 g 0 b 255 effect solid;",
        "blaster_fire": "blaster fire;",
        "arm_center": "robotic_arm moveto x 100 y 100;",
        "grip_open": "robotic_gripper open 1;",
        "grip_close": "robotic_gripper close 1;",
    }
    raw = udp_map.get(cmd_str)
    if raw:
        try:
            if ep_cmd_sock is None:
                ep_cmd_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                ep_cmd_sock.settimeout(0.5)
            ep_cmd_sock.sendto(raw.encode(), (EP_IP, EP_PORT))
            return True
        except Exception:
            pass
    return False

def stop_ep_camera_pipeline():
    global _ep_cam_cap, _ep_cam_sdk_started, _ep_cam_last_frame

    with _ep_cam_lock:
        if _ep_cam_cap is not None:
            try:
                _ep_cam_cap.release()
            except Exception:
                pass
            _ep_cam_cap = None

        if _ep_cam_sdk_started and ep_robot_inst is not None:
            try:
                ep_robot_inst.camera.stop_video_stream()
            except Exception:
                pass
            _ep_cam_sdk_started = False

        _ep_cam_last_frame = None
        ep_camera_state['status'] = 'Stopped'
        ep_camera_state['source'] = 'none'

def ep_status_thread():
    ep_comm_thread()

def ep_comm_thread():
    global ep_node_intent, ep_robot_inst
    is_moving = False

    while True:
        time.sleep(0.05)
        if ep_robot_inst is None or ep_dashboard.get("hw_link", "Offline") == "Offline":
            continue

        tnow = time.monotonic()
        active = (tnow - ep_node_intent['trigger_time']) < 0.2

        if ep_node_intent['stop'] or not active:
            if is_moving:
                write_log("EP: stop sequence start (Active Brake -> Wheel Lock)")
                try:
                    ep_robot_inst.chassis.drive_speed(x=0, y=0, z=0, timeout=0.1)
                    time.sleep(0.05)
                    for _ in range(3):
                        ep_robot_inst.chassis.drive_wheels(w1=0, w2=0, w3=0, w4=0)
                        time.sleep(0.1)
                except Exception as e:
                    write_log(f"EP Brake Error: {e}")
                is_moving = False
                ep_node_intent['stop'] = False
            ep_target_vel['vx'] = 0.0
            ep_target_vel['vy'] = 0.0
            ep_target_vel['vz'] = 0.0
            continue

        try:
            ep_robot_inst.chassis.drive_speed(
                x=ep_node_intent['vx'],
                y=ep_node_intent['vy'],
                z=ep_node_intent['wz'],
                timeout=0.5,
            )
            is_moving = True
            ep_target_vel['vx'] = ep_node_intent['vx']
            ep_target_vel['vy'] = ep_node_intent['vy']
            ep_target_vel['vz'] = ep_node_intent['wz']
        except Exception:
            pass

        if ep_cmd_sock:
            try:
                # 배터리 정보 폴링 (기본 SDK 명세)
                ep_cmd_sock.sendto(b"battery ?;", (EP_IP, EP_PORT))
                data, _ = ep_cmd_sock.recvfrom(1024)
                res = data.decode('utf-8').strip()
                if res.isdigit():
                    ep_state['battery'] = int(res)
                    ep_dashboard["hw_link"] = "Online"
                
                # 위치 정보 폴링 (필요 시 push 명령으로 대체 가능)
                ep_cmd_sock.sendto(b"chassis position ?;", (EP_IP, EP_PORT))
                pos_data, _ = ep_cmd_sock.recvfrom(1024)
                pos_res = pos_data.decode('utf-8').strip().split()
                if len(pos_res) >= 3:
                    ep_state['pos_x'] = float(pos_res[0])
                    ep_state['pos_y'] = float(pos_res[1])
            except Exception:
                pass

# ================= [EP Hardware Nodes] =================

class EPRobotDriver(BaseRobotDriver):
    def get_ui_schema(self):
        return [('vx', "Vx(m/s)", 0.0), ('vy', "Vy(m/s)", 0.0), ('wz', "Wz(deg/s)", 0.0)]
        
    def get_settings_schema(self):
        return []

    def execute_command(self, inputs, settings):
        global ep_node_intent

        vx_val = inputs.get('vx')
        vy_val = inputs.get('vy')
        wz_val = inputs.get('wz')
        if wz_val is None:
            wz_val = inputs.get('vz')

        if vx_val is not None or vy_val is not None or wz_val is not None:
            ep_node_intent['vx'] = float(vx_val or 0.0)
            ep_node_intent['vy'] = float(vy_val or 0.0)
            ep_node_intent['wz'] = float(wz_val or 0.0)
            ep_node_intent['trigger_time'] = time.monotonic()

        ep_target_vel['vx'] = ep_node_intent['vx']
        ep_target_vel['vy'] = ep_node_intent['vy']
        ep_target_vel['vz'] = ep_node_intent['wz']

        return ep_target_vel

class EPKeyboardNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "Keyboard (EP)", "EP_KEYBOARD")
        self.in_flow = generate_uuid()
        self.inputs[self.in_flow] = PortType.FLOW
        self.out_vx = generate_uuid()
        self.outputs[self.out_vx] = PortType.DATA
        self.out_vy = generate_uuid()
        self.outputs[self.out_vy] = PortType.DATA
        self.out_wz = generate_uuid()
        self.outputs[self.out_wz] = PortType.DATA
        self.out_flow = generate_uuid()
        self.outputs[self.out_flow] = PortType.FLOW

    def execute(self):
        if self.state.get('is_focused', False):
            return self.out_flow

        vx = 0.0
        vy = 0.0
        wz = 0.0
        ep_v_max = 0.5
        ep_w_max = 60.0

        key_mode = self.state.get('keys', 'WASD')
        if key_mode == 'WASD':
            if self.state.get('W'):
                vx = ep_v_max
            if self.state.get('S'):
                vx = -ep_v_max
            if self.state.get('A'):
                vy = -ep_v_max
            if self.state.get('D'):
                vy = ep_v_max
        else:
            if self.state.get('UP'):
                vx = ep_v_max
            if self.state.get('DOWN'):
                vx = -ep_v_max
            if self.state.get('LEFT'):
                vy = -ep_v_max
            if self.state.get('RIGHT'):
                vy = ep_v_max

        if self.state.get('Q'):
            wz = -ep_w_max
        if self.state.get('E'):
            wz = ep_w_max
        if self.state.get('SPACE'):
            ep_node_intent['stop'] = True

        if vx or vy or wz:
            ep_node_intent['vx'] = vx
            ep_node_intent['vy'] = vy
            ep_node_intent['wz'] = wz
            ep_node_intent['trigger_time'] = time.monotonic()

        self.output_data[self.out_vx] = vx
        self.output_data[self.out_vy] = vy
        self.output_data[self.out_wz] = wz
        return self.out_flow

class EPCameraSourceNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "EP Camera Source", "EP_CAM_SRC")
        self.out_frame = generate_uuid()
        self.outputs[self.out_frame] = PortType.DATA
        self.state['url'] = ep_camera_state.get('url', 'rtsp://192.168.42.2/live')
        self.state['prefer_sdk'] = True

    def _start_sdk_camera(self):
        global _ep_cam_sdk_started
        if ep_robot_inst is None:
            return False
        try:
            if not _ep_cam_sdk_started:
                ep_robot_inst.camera.start_video_stream(display=False)
                _ep_cam_sdk_started = True
            ep_camera_state['status'] = 'Running'
            ep_camera_state['source'] = 'sdk'
            return True
        except Exception as e:
            write_log(f"EP Camera SDK start failed: {e}")
            return False

    def _start_cv_camera(self):
        global _ep_cam_cap
        if not HAS_CV2:
            return False
        url = str(self.state.get('url', ep_camera_state.get('url', 'rtsp://192.168.42.2/live'))).strip()
        if not url:
            return False
        ep_camera_state['url'] = url
        try:
            cap = cv2.VideoCapture(url)
            if cap.isOpened():
                _ep_cam_cap = cap
                ep_camera_state['status'] = 'Running'
                ep_camera_state['source'] = 'cv'
                return True
            cap.release()
        except Exception as e:
            write_log(f"EP Camera CV open failed: {e}")
        return False

    def execute(self):
        global _ep_cam_last_frame

        frame = None
        prefer_sdk = bool(self.state.get('prefer_sdk', True))

        with _ep_cam_lock:
            if prefer_sdk and ep_robot_inst is not None:
                if self._start_sdk_camera():
                    try:
                        frame = ep_robot_inst.camera.read_cv2_image(strategy='newest', timeout=0.2)
                    except Exception:
                        frame = None

            if frame is None:
                if _ep_cam_cap is None:
                    self._start_cv_camera()
                if _ep_cam_cap is not None:
                    try:
                        ok, raw = _ep_cam_cap.read()
                        if ok:
                            frame = raw
                            ep_camera_state['status'] = 'Running'
                            ep_camera_state['source'] = 'cv'
                    except Exception:
                        frame = None

            if frame is not None:
                _ep_cam_last_frame = frame
            self.output_data[self.out_frame] = _ep_cam_last_frame

            if _ep_cam_last_frame is None:
                ep_camera_state['status'] = 'Stopped'

        return None

class EPCameraStreamNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "EP Camera Stream", "EP_CAM_STREAM")
        self.in_frame = generate_uuid()
        self.inputs[self.in_frame] = PortType.DATA
        self.state['port'] = 5050
        self.state['is_running'] = False
        self._started_local = False

    def _start_server_once(self):
        global _ep_flask_thread_started
        if not HAS_FLASK or _ep_flask_app is None:
            return
        if _ep_flask_thread_started:
            return

        port = int(self.state.get('port', 5050))

        def run_server():
            _ep_flask_app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

        threading.Thread(target=run_server, daemon=True).start()
        _ep_flask_thread_started = True
        write_log(f"EP Flask Stream Started: http://0.0.0.0:{port}/ep_video_feed")

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
                    with _ep_flask_lock:
                        global _ep_flask_latest_jpg
                        _ep_flask_latest_jpg = buf.tobytes()

        return None

class EPActionNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "EP Action", "EP_ACTION")
        self.in_flow = generate_uuid(); self.inputs[self.in_flow] = PortType.FLOW
        self.out_flow = generate_uuid(); self.outputs[self.out_flow] = PortType.FLOW
        self.state['action'] = "LED Red"

    def execute(self):
        action = self.state.get("action", "LED Red")

        cmd_name = ""
        if action == "LED Red":
            cmd_name = "led_red"
        elif action == "LED Blue":
            cmd_name = "led_blue"
        elif action == "Blaster Fire":
            cmd_name = "blaster_fire"
        elif action == "Arm Center":
            cmd_name = "arm_center"
        elif action == "Grip Open":
            cmd_name = "grip_open"
        elif action == "Grip Close":
            cmd_name = "grip_close"

        if cmd_name:
            send_ep_command(cmd_name)
            write_log(f"EP Action: {action}")
            
        return self.out_flow