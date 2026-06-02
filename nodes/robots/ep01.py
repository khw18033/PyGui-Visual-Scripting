import time
import socket
import threading
import math
import json
import sys
import os
import glob
import asyncio
from collections import deque
from unittest.mock import MagicMock
import urllib.request
import urllib.error
from nodes.base import BaseNode, BaseRobotDriver
from core.engine import generate_uuid, PortType, write_log, HwStatus
import core.engine as engine_module
from core.ep01_config import EP01_NETWORK_CONFIG, EP01_HARDWARE_CONFIG, EP01_CAMERA_CONFIG, EP01_MISSION_CONFIG

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

try:
    import aiohttp
    HAS_AIOHTTP = True
except ImportError:
    aiohttp = None
    HAS_AIOHTTP = False

# ================= [EP Globals & Network] =================
EP_USE_MEDIA_MOCK = os.getenv("EP_USE_MEDIA_MOCK", "0").strip().lower() in ("1", "true", "yes", "on")
if EP_USE_MEDIA_MOCK:
    sys.modules['libmedia_codec'] = MagicMock()
    sys.modules['libmedia_codec.media_codec'] = MagicMock()
    write_log("EP: libmedia_codec mock enabled (EP_USE_MEDIA_MOCK=1)")

try:
    from robomaster import robot
    from robomaster import conn as rm_conn
    from robomaster import config as rm_config
    HAS_ROBOMASTER_SDK = True
except ImportError as e:
    rm_conn = None
    rm_config = None
    HAS_ROBOMASTER_SDK = False
    write_log(f"Warning: 'robomaster' module not found. ({e})")

if rm_config is not None and not hasattr(rm_config, 'DEFAULT_CONN_PROTO'):
    try:
        rm_config.DEFAULT_CONN_PROTO = getattr(rm_config, 'DEFAULT_PROTO_TYPE', 'udp')
    except Exception:
        rm_config.DEFAULT_CONN_PROTO = 'udp'

ep_cmd_sock = None
ep_robot_inst = None
ep_drive_wheels_sender = None
ep_command_sender = None
EP_IP = EP01_NETWORK_CONFIG['ep_ip']
EP_PORT = EP01_NETWORK_CONFIG['ep_port']

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
    "url": EP01_CAMERA_CONFIG['camera_stream'].get('url', 'rtsp://192.168.42.2/live'),
}

ep_camera_save_state = dict(EP01_CAMERA_CONFIG['camera_save_state'])

ep_sender_state = {'status': 'Stopped'}
ep_sender_command_queue = deque()
ep_sender_active = False
EP_SENDER_TARGET_FPS = EP01_NETWORK_CONFIG['ep_sender_target_fps']
EP_SENDER_INTERVAL = EP01_CAMERA_CONFIG['sender']['interval']
_ep_sender_manager_started = False
_ep_sender_manager_lock = threading.Lock()
_ep_scan_lock = threading.Lock()

# EP Sender 실시간 폴더 (ref_code와 일치 - /dev/shm 사용 가능할 경우)
EP_SENDER_WATCH_FOLDER = EP01_NETWORK_CONFIG.get('ep_sender_watch_folder', "/dev/shm/ep01") if os.path.isdir("/dev/shm") else "Captured_Images/ep01_saved"

ep_server_json_data = {
    'raw_json': '',
    'seq': 0,
    'ts': 0.0,
    'connected': False,
    'fresh': False,
    'status': 'Idle',
    'source': '',
}

ep_arm_state = {
    "x": 100.0,
    "y": 100.0,
}

EP_ARM_STEP = EP01_HARDWARE_CONFIG['arm']['step_size']
EP_ARM_MIN = EP01_HARDWARE_CONFIG['arm']['min_position']
EP_ARM_MAX = EP01_HARDWARE_CONFIG['arm']['max_position']
EP_GRIPPER_POWER = EP01_HARDWARE_CONFIG['gripper']['power_level']

# ================= [EP Arm Action Queue - Non-blocking] =================
# 그리퍼/팔 제어 명령 큐: GUI 스레드에서는 큐에만 추가하고, 별도 스레드에서 처리
ep_arm_action_queue = []  # Queue of {'type': 'move'/'grip', 'params': {...}}
_ep_arm_lock = threading.Lock()
_ep_arm_worker_started = False
_ep_pending_arm_action = None  # 진행 중인 액션 추적
_ep_pending_action_start_time = None  # 액션 시작 시간
EP_ARM_ACTION_TIMEOUT = EP01_HARDWARE_CONFIG['arm']['action_timeout_sec']
EP_ARM_RETRY_DELAY = EP01_HARDWARE_CONFIG['arm']['retry_delay_sec']
EP_ARM_MAX_RETRY = EP01_HARDWARE_CONFIG['arm']['max_retries']

# Separate gripper queue and pending state to avoid gripper commands
# being blocked by long-running arm move commands.
ep_gripper_action_queue = []  # Queue of {'type':'grip', 'open': bool, 'retry': int}
_ep_gripper_lock = threading.Lock()
_ep_pending_gripper_action = None
_ep_pending_gripper_start_time = None
_ep_comm_thread_started = False

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

def _ep_move_arm(delta_x=0.0, delta_y=0.0):
    """팔을 상대 좌표로 이동 (큐를 거쳐 Non-blocking 처리)"""
    global ep_arm_action_queue
    
    # 큐에 추가만 하고 즉시 반환 (GUI 블로킹 방지)
    with _ep_arm_lock:
        ep_arm_action_queue.append({
            'type': 'move',
            'target_x': ep_arm_state['x'] + delta_x,
            'target_y': ep_arm_state['y'] + delta_y,
            'retry': 0
        })
        
    return True

def _ep_set_gripper(open_gripper):
    """그리퍼 제어 (큐를 거쳐 Non-blocking 처리)"""
    global ep_gripper_action_queue

    # 큐에 추가만 하고 즉시 반환 (GUI 블로킹 방지)
    with _ep_gripper_lock:
        ep_gripper_action_queue.append({
            'type': 'grip',
            'open': open_gripper,
            'retry': 0
        })

    return True

def _wait_for_action_completion(action_obj, timeout_sec=EP_ARM_ACTION_TIMEOUT):
    if action_obj is None:
        return
    waiter = getattr(action_obj, 'wait_for_completed', None)
    if callable(waiter):
        try:
            waiter(timeout=timeout_sec)
        except TypeError:
            waiter()


def set_ep_drive_wheels_sender(sender):
    global ep_drive_wheels_sender
    ep_drive_wheels_sender = sender


def _ep_velocity_to_wheels(vx, vy, wz):
    # Convert chassis velocity command into mecanum wheel RPM command.
    rpm_per_ms = 220.0
    rpm_per_deg = 2.2
    trans_x = float(vx) * rpm_per_ms
    trans_y = float(vy) * rpm_per_ms
    rot = -float(wz) * rpm_per_deg

    w1 = trans_x - trans_y + rot
    w2 = trans_x + trans_y - rot
    w3 = trans_x - trans_y - rot
    w4 = trans_x + trans_y + rot

    def _clamp(v):
        return int(max(-1000, min(1000, round(v))))

    return _clamp(w1), _clamp(w2), _clamp(w3), _clamp(w4)


def set_ep_command_sender(sender):
    global ep_command_sender
    ep_command_sender = sender


def _normalize_ep_conn_type(conn_mode):
    conn_mode = str(conn_mode or '').strip().lower()
    if rm_conn is None:
        return conn_mode
    if conn_mode == 'sta':
        return rm_conn.CONNECTION_WIFI_STA
    if conn_mode == 'ap':
        return rm_conn.CONNECTION_WIFI_AP
    if conn_mode == 'rndis':
        return rm_conn.CONNECTION_USB_RNDIS
    return conn_mode

def connect_ep_thread_func(conn_mode, sn=None, robot_ip=None):
    global ep_robot_inst, ep_cmd_sock
    previous_robot_ip = None

    if not HAS_ROBOMASTER_SDK:
        ep_dashboard["hw_link"] = "Simulation"
        write_log("EP_DEBUG: 'robomaster' SDK not found. Skipping connection.")
        return

    ep_dashboard["hw_link"] = f"Connecting ({conn_mode.upper()})..."
    write_log(f"System: Attempting EP Connection via {conn_mode.upper()}...")

    if ep_robot_inst is not None or ep_cmd_sock is not None:
        write_log("EP_DEBUG: Cleaning up previous EP runtime resources...")
        cleanup_ep_runtime()
        write_log("EP_DEBUG: Previous EP runtime resources cleared.")

    try:
        write_log("EP_DEBUG: Instantiating robot.Robot()...")
        ep_robot_inst = robot.Robot()

        if rm_config is not None and robot_ip:
            previous_robot_ip = getattr(rm_config, 'ROBOT_IP_STR', None)
            rm_config.ROBOT_IP_STR = robot_ip
            write_log(f"EP_DEBUG: Using discovered robot_ip='{robot_ip}' for connection.")

        normalized_conn_mode = _normalize_ep_conn_type(conn_mode)
        if sn:
            write_log(f"EP_DEBUG: Calling initialize(conn_type='{conn_mode}', proto_type='tcp', sn='{sn}')...")
            ep_robot_inst.initialize(conn_type=normalized_conn_mode, proto_type='tcp', sn=sn)
        else:
            write_log(f"EP_DEBUG: Calling initialize(conn_type='{conn_mode}', proto_type='tcp')...")
            ep_robot_inst.initialize(conn_type=normalized_conn_mode, proto_type='tcp')
        write_log("EP_DEBUG: Initialize completed successfully.")

        try:
            ep_robot_inst.set_robot_mode(mode="free")
            write_log("EP_DEBUG: Robot mode set to FREE.")
        except Exception as e:
            write_log(f"EP_DEBUG: Could not set FREE mode: {e}")

        write_log("EP_DEBUG: Getting Serial Number...")
        ep_dashboard["sn"] = ep_robot_inst.get_sn()

        ep_dashboard["hw_link"] = f"Online ({str(conn_mode).upper()})"
        ep_dashboard["conn_type"] = str(conn_mode).upper()
        write_log(f"System: EP Connected! (SN: {ep_dashboard['sn']})")

        with _ep_arm_lock:
            ep_arm_action_queue.clear()

        write_log("EP_DEBUG: Subscribing to telemetry (Pos, Vel, Bat, IMU)...")
        ep_robot_inst.chassis.sub_position(freq=1, callback=ep_sub_pos)
        ep_robot_inst.chassis.sub_velocity(freq=5, callback=ep_sub_vel)
        ep_robot_inst.battery.sub_battery_info(freq=1, callback=ep_sub_bat)
        ep_robot_inst.chassis.sub_imu(freq=10, callback=ep_sub_imu)
        write_log("EP_DEBUG: All subscriptions active.")

        if rm_config is not None and robot_ip:
            rm_config.ROBOT_IP_STR = previous_robot_ip

    except Exception as e:
        cleanup_ep_runtime()
        if rm_config is not None and robot_ip:
            try:
                rm_config.ROBOT_IP_STR = previous_robot_ip
            except Exception:
                pass
        import traceback
        error_details = traceback.format_exc()
        write_log(f"EP Connect Error (Detailed): {e}")
        print(f"\n[EP_CRITICAL_ERROR_TRACE]\n{error_details}\n")

def btn_connect_ep_sta(sender=None, app_data=None):
    threading.Thread(target=connect_ep_thread_func, args=("sta",), daemon=True).start()

def btn_connect_ep_ap(sender=None, app_data=None):
    threading.Thread(target=connect_ep_thread_func, args=("ap",), daemon=True).start()


def scan_ep_sta_robots(timeout=3.0):
    """Return [{'sn': ..., 'ip': ...}, ...] for EP robots broadcasting on STA network."""
    robots = []
    if not HAS_ROBOMASTER_SDK:
        return robots
    try:
        import robomaster.conn as rm_conn
    except Exception:
        return robots

    with _ep_scan_lock:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if hasattr(socket, 'SO_REUSEPORT'):
                try:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                except Exception:
                    pass
            sock.bind(("0.0.0.0", rm_conn.config.ROBOT_BROADCAST_PORT))
            sock.settimeout(1)
            start = time.time()
            seen = set()
            while time.time() - start < timeout:
                try:
                    data, ip = sock.recvfrom(1024)
                except socket.timeout:
                    continue
                except Exception:
                    continue
                try:
                    sn = rm_conn.get_sn_form_data(data)
                except Exception:
                    continue
                if not sn or sn in seen:
                    continue
                seen.add(sn)
                robots.append({'sn': sn, 'ip': ip[0]})
        finally:
            try:
                sock.close()
            except Exception:
                pass
    return robots

def send_ep_command(cmd_str):
    global ep_cmd_sock

    if callable(ep_command_sender):
        try:
            if ep_command_sender(cmd_str):
                return True
        except Exception:
            pass

    if ep_robot_inst is not None:
        try:
            if cmd_str == "led_red":
                ep_robot_inst.led.set_led(comp="all", r=255, g=0, b=0, effect="on")
                return True
            if cmd_str == "led_blue":
                ep_robot_inst.led.set_led(comp="all", r=0, g=0, b=255, effect="on")
                return True
            if cmd_str == "led_yellow":
                ep_robot_inst.led.set_led(comp="all", r=255, g=255, b=0, effect="on")
                return True
            if cmd_str == "led_green":
                ep_robot_inst.led.set_led(comp="all", r=0, g=255, b=0, effect="on")
                return True
            if cmd_str == "blaster_fire":
                ep_robot_inst.blaster.fire(times=1)
                return True
            if cmd_str == "arm_center":
                return _ep_move_arm(delta_x=(100.0 - ep_arm_state['x']), delta_y=(100.0 - ep_arm_state['y']))
            if cmd_str == "arm_up":
                return _ep_move_arm(delta_y=EP_ARM_STEP)
            if cmd_str == "arm_down":
                return _ep_move_arm(delta_y=-EP_ARM_STEP)
            if cmd_str == "arm_left":
                return _ep_move_arm(delta_x=-EP_ARM_STEP)
            if cmd_str == "arm_right":
                return _ep_move_arm(delta_x=EP_ARM_STEP)
            if cmd_str == "grip_open":
                return _ep_set_gripper(True)
            if cmd_str == "grip_close":
                return _ep_set_gripper(False)
        except Exception:
            pass

    udp_map = {
        "led_red":    "led control comp all r 255 g 0   b 0   effect solid;",
        "led_blue":   "led control comp all r 0   g 0   b 255 effect solid;",
        "led_yellow": "led control comp all r 255 g 255 b 0   effect solid;",
        "led_green":  "led control comp all r 0   g 255 b 0   effect solid;",
        "blaster_fire": "blaster fire;",
        "arm_center": "robotic_arm moveto x 100 y 100;",
        "grip_open": "gripper open 1;",
        "grip_close": "gripper close 1;",
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


def cleanup_ep_runtime():
    """Close EP transport resources and reset runtime state."""
    global ep_cmd_sock, ep_robot_inst, ep_node_intent, ep_target_vel

    try:
        stop_ep_camera_pipeline()
    except Exception:
        pass

    robot_inst = ep_robot_inst
    ep_robot_inst = None

    try:
        if robot_inst is not None:
            for obj, method_names in (
                (getattr(robot_inst, 'chassis', None), ('unsub_position', 'unsub_velocity', 'unsub_imu')),
                (getattr(robot_inst, 'battery', None), ('unsub_battery_info', 'unsub_battery')),
            ):
                for method_name in method_names:
                    method = getattr(obj, method_name, None) if obj is not None else None
                    if callable(method):
                        try:
                            method()
                        except Exception:
                            pass
            try:
                robot_inst.close()
            except Exception:
                pass
    finally:
        if ep_cmd_sock is not None:
            try:
                ep_cmd_sock.close()
            except Exception:
                pass
            ep_cmd_sock = None

        with _ep_arm_lock:
            ep_arm_action_queue.clear()
        with _ep_gripper_lock:
            ep_gripper_action_queue.clear()

        ep_node_intent = {'vx': 0.0, 'vy': 0.0, 'wz': 0.0, 'stop': True, 'trigger_time': time.monotonic()}
        ep_target_vel = {'vx': 0.0, 'vy': 0.0, 'vz': 0.0}

        ep_dashboard['hw_link'] = 'Offline'
        ep_dashboard['sn'] = 'Unknown'
        ep_dashboard['conn_type'] = 'None'
        ep_state['battery'] = -1
        ep_state['pos_x'] = 0.0
        ep_state['pos_y'] = 0.0
        ep_state['speed'] = 0.0
        ep_state['accel_x'] = 0.0
        ep_state['accel_y'] = 0.0
        ep_state['accel_z'] = 0.0


def _ep_extract_front_frame_index(path):
    name = os.path.basename(path)
    if not (name.startswith("front_") and name.endswith(".jpg")):
        return -1
    number_part = name[6:-4]
    return int(number_part) if number_part.isdigit() else -1


def _ep_is_file_stable(path, wait_sec=0.02):
    try:
        size1 = os.path.getsize(path)
        time.sleep(wait_sec)
        size2 = os.path.getsize(path)
        return size1 > 0 and size1 == size2
    except OSError:
        return False


def _ensure_ep_sender_manager_started():
    """EP sender manager를 단 한 번만 시작"""
    global _ep_sender_manager_started
    with _ep_sender_manager_lock:
        if _ep_sender_manager_started:
            return
        _ep_sender_manager_started = True
        threading.Thread(target=_ep_sender_manager_thread, daemon=True).start()
        write_log("[EP] Sender manager thread started")


async def _ep_send_image_async(session, filepath, camera_id, server_url):
    try:
        if not os.path.exists(filepath):
            return
        with open(filepath, 'rb') as f:
            file_data = f.read()

        form = aiohttp.FormData()
        form.add_field('camera_id', camera_id)
        form.add_field('file', file_data, filename=f"{camera_id}.jpg", content_type='image/jpeg')

        async with session.post(server_url, data=form, timeout=aiohttp.ClientTimeout(total=2.0)) as resp:
            if resp.status != 200:
                write_log(f"[EP Sender] Server error: {resp.status}")
    except asyncio.TimeoutError:
        write_log(f"[EP Sender] Timeout (skipping frame)")
    except Exception as e:
        write_log(f"[EP Sender] upload error: {e}")


async def _ep_camera_async_worker(config, server_url):
    global ep_sender_active

    folder = config["folder"]
    camera_id = config["id"]
    last_processed_file = None
    last_processed_idx = -1
    last_processed_mtime = 0.0

    os.makedirs(folder, exist_ok=True)

    try:
        async with aiohttp.ClientSession() as session:
            while ep_sender_active:
                cycle_start = time.time()
                files = glob.glob(os.path.join(folder, "*.jpg"))

                if files:
                    best_file = None
                    best_idx = -1
                    for f in files:
                        idx = _ep_extract_front_frame_index(f)
                        if idx > best_idx:
                            best_idx = idx
                            best_file = f

                    if best_file is None:
                        valid_files = []
                        for f in files:
                            try:
                                valid_files.append((os.path.getctime(f), f))
                            except OSError:
                                pass
                        if valid_files:
                            _, latest_file = max(valid_files)
                            if latest_file != last_processed_file and _ep_is_file_stable(latest_file):
                                last_processed_file = latest_file
                                await _ep_send_image_async(session, latest_file, camera_id, server_url)
                    else:
                        try:
                            current_mtime = os.path.getmtime(best_file)
                        except OSError:
                            current_mtime = 0.0

                        has_new_frame = (
                            (best_idx > last_processed_idx)
                            or (best_file != last_processed_file)
                            or (current_mtime > last_processed_mtime)
                        )

                        if has_new_frame and _ep_is_file_stable(best_file):
                            await _ep_send_image_async(session, best_file, camera_id, server_url)
                            last_processed_idx = best_idx
                            last_processed_file = best_file
                            last_processed_mtime = current_mtime

                await asyncio.sleep(max(0, EP_SENDER_INTERVAL - (time.time() - cycle_start)))
    except Exception as e:
        write_log(f"[EP Sender] worker error ({camera_id}): {e}")


def _ep_start_async_loop(config, server_url):
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_ep_camera_async_worker(config, server_url))
    except Exception:
        pass


def _ep_sender_manager_thread():
    """EP Sender 매니저: 큐 명령을 처리하고 비동기 워커 관리"""
    global ep_sender_active, ep_sender_state
    sender_threads = []

    write_log("[EP Sender Manager] Started")

    while True:
        if ep_sender_command_queue:
            cmd, url = ep_sender_command_queue.popleft()

            if cmd == 'START' and not ep_sender_active:
                # 저장 노드의 실제 폴더를 우선 사용하고, 없으면 기본 폴더로 fallback
                upload_folder = str(ep_camera_save_state.get('folder', '')).strip() or EP_SENDER_WATCH_FOLDER
                os.makedirs(upload_folder, exist_ok=True)
                ep_sender_active = True
                ep_sender_state['status'] = 'Running'
                write_log(f"[EP Sender] START | url={url} | folder={upload_folder}")

                config = {"folder": upload_folder, "id": "ep01_front"}
                s_thread = threading.Thread(target=_ep_start_async_loop, args=(config, url), daemon=True)
                s_thread.start()
                sender_threads.append(s_thread)

            elif cmd == 'STOP' and ep_sender_active:
                ep_sender_active = False
                ep_sender_state['status'] = 'Stopped'
                write_log("[EP Sender] STOP")
                sender_threads.clear()

        time.sleep(0.1)

def ep_status_thread():
    """EP 상태 모니터 및 통신 스레드 시작"""
    global _ep_arm_worker_started
    
    # Sender 매니저는 중복 없이 한 번만 시작
    _ensure_ep_sender_manager_started()
    
    # 통신 루프 실행
    ep_comm_thread()


def ensure_ep_comm_thread_started():
    global _ep_comm_thread_started
    if _ep_comm_thread_started:
        return
    _ep_comm_thread_started = True
    threading.Thread(target=ep_status_thread, daemon=True).start()

def ep_comm_thread():
    """
    EP 로봇 통신 스레드
    - 움직임 명령 처리 (drive_wheels)
    - 팔/그리퍼 액션 큐 처리
    - 떨림 방지: 이전 속도와 다를 때만 전송
    """
    global ep_node_intent, ep_robot_inst, _ep_pending_arm_action, _ep_pending_action_start_time
    is_moving = False
    last_vx = None
    last_vy = None
    last_wz = None
    last_send_time = 0.0
    KEEPALIVE_INTERVAL = 0.3  # SDK timeout=0.5 보다 짧게 재전송해 키 홀드 시 연속 주행

    while True:
        time.sleep(0.05)
        if ep_robot_inst is None or ep_dashboard.get("hw_link", "Offline") == "Offline":
            continue

        # ================= [팔/그리퍼 액션 큐 처리] =================
        # 이전 액션이 완료되었거나 타임아웃되었는지 확인
        if _ep_pending_arm_action is not None:
            elapsed = time.monotonic() - _ep_pending_action_start_time
            # 액션 타임아웃으로 간주하고 다음 액션 처리
            if elapsed > EP_ARM_ACTION_TIMEOUT:
                write_log(f"EP Arm: action timeout ({elapsed:.2f}s), moving to next action")
                _ep_pending_arm_action = None

        # 진행 중인 액션이 없으면, 큐에서 새로운 액션 꺼냄
        if _ep_pending_arm_action is None:
            action = None
            with _ep_arm_lock:
                if ep_arm_action_queue:
                    action = ep_arm_action_queue.pop(0)

            if action is not None:
                try:
                    if action['type'] == 'move':
                        x = float(action['target_x'])
                        y = float(action['target_y'])
                        write_log(f"EP: arm move ({x:.1f}, {y:.1f})")
                        ep_robot_inst.robotic_arm.moveto(x=x, y=y)
                        _ep_pending_arm_action = action
                        _ep_pending_action_start_time = time.monotonic()
                        # 팔 상태 업데이트
                        ep_arm_state['x'] = x
                        ep_arm_state['y'] = y
                        
                    elif action['type'] == 'grip':
                        opening = bool(action['open'])
                        write_log(f"EP: gripper {'open' if opening else 'close'}")
                        if opening:
                            ep_robot_inst.gripper.open(power=EP_GRIPPER_POWER)
                        else:
                            ep_robot_inst.gripper.close(power=EP_GRIPPER_POWER)
                        _ep_pending_arm_action = action
                        _ep_pending_action_start_time = time.monotonic()
                        
                except Exception as e:
                    msg = str(e)
                    write_log(f"EP arm action error: {msg}")
                    if "already performing" in msg.lower():
                        # 재시도 처리
                        retry = int(action.get('retry', 0)) + 1
                        if retry <= EP_ARM_MAX_RETRY:
                            action['retry'] = retry
                            time.sleep(EP_ARM_RETRY_DELAY)
                            with _ep_arm_lock:
                                ep_arm_action_queue.insert(0, action)
                            write_log(f"EP arm action: retry {retry}/{EP_ARM_MAX_RETRY}")
                        else:
                            write_log("EP arm action: max retries exceeded, dropped")
                            _ep_pending_arm_action = None
                    else:
                        # 다른 오류는 액션 취소
                        _ep_pending_arm_action = None

        # ================= [그리퍼 액션 큐 처리] =================
        # 그리퍼 별도 큐 및 완료 대기 스레드 처리
        global _ep_pending_gripper_action, _ep_pending_gripper_start_time
        if _ep_pending_gripper_action is not None:
            elapsed_g = time.monotonic() - _ep_pending_gripper_start_time
            if elapsed_g > EP_ARM_ACTION_TIMEOUT:
                write_log(f"EP Gripper: action timeout ({elapsed_g:.2f}s), moving to next action")
                _ep_pending_gripper_action = None

        if _ep_pending_gripper_action is None:
            gaction = None
            with _ep_gripper_lock:
                if ep_gripper_action_queue:
                    gaction = ep_gripper_action_queue.pop(0)

            if gaction is not None:
                try:
                    opening = bool(gaction['open'])
                    write_log(f"EP: gripper {'open' if opening else 'close'} (gripper queue)")
                    if opening:
                        ep_robot_inst.gripper.open(power=EP_GRIPPER_POWER)
                    else:
                        ep_robot_inst.gripper.close(power=EP_GRIPPER_POWER)
                    _ep_pending_gripper_action = gaction
                    _ep_pending_gripper_start_time = time.monotonic()

                    def _gripper_waiter(action_ref):
                        try:
                            _wait_for_action_completion(action_ref, timeout_sec=EP_ARM_ACTION_TIMEOUT)
                        except Exception:
                            pass
                        finally:
                            try:
                                global _ep_pending_gripper_action
                                if _ep_pending_gripper_action is action_ref:
                                    _ep_pending_gripper_action = None
                            except Exception:
                                pass

                    threading.Thread(target=_gripper_waiter, args=(_ep_pending_gripper_action,), daemon=True).start()

                except Exception as e:
                    msg = str(e)
                    write_log(f"EP gripper action error: {msg}")
                    if "already performing" in msg.lower():
                        retry = int(gaction.get('retry', 0)) + 1
                        if retry <= EP_ARM_MAX_RETRY:
                            gaction['retry'] = retry
                            time.sleep(EP_ARM_RETRY_DELAY)
                            with _ep_gripper_lock:
                                ep_gripper_action_queue.insert(0, gaction)
                            write_log(f"EP gripper action: retry {retry}/{EP_ARM_MAX_RETRY}")
                        else:
                            write_log("EP gripper action: max retries exceeded, dropped")
                            _ep_pending_gripper_action = None
                    else:
                        _ep_pending_gripper_action = None

        # ================= [주행 명령 처리 - 떨림 방지] =================
        tnow = time.monotonic()
        active = (tnow - ep_node_intent['trigger_time']) < 0.2

        if ep_node_intent['stop'] or not active:
            if is_moving:
                write_log("EP: stop signal (Clean Brake)")
                try:
                    # 정지 명령을 한 번만 전송
                    ep_robot_inst.chassis.drive_wheels(w1=0, w2=0, w3=0, w4=0, timeout=0.1)
                except Exception as e:
                    write_log(f"EP stop error: {e}")
                
                is_moving = False
                last_send_time = 0.0
                ep_node_intent['stop'] = False
                ep_node_intent['vx'] = 0.0
                ep_node_intent['vy'] = 0.0
                ep_node_intent['wz'] = 0.0
                last_vx = 0.0
                last_vy = 0.0
                last_wz = 0.0
                
            ep_target_vel['vx'] = 0.0
            ep_target_vel['vy'] = 0.0
            ep_target_vel['vz'] = 0.0
            continue

        # 속도값이 변경되었거나 SDK timeout 전에 keepalive 재전송
        vx = ep_node_intent['vx']
        vy = ep_node_intent['vy']
        wz = ep_node_intent['wz']

        changed = vx != last_vx or vy != last_vy or wz != last_wz
        needs_keepalive = is_moving and (tnow - last_send_time) > KEEPALIVE_INTERVAL
        if changed or needs_keepalive:
            try:
                w1, w2, w3, w4 = _ep_velocity_to_wheels(vx, vy, wz)
                ep_robot_inst.chassis.drive_wheels(w1=w1, w2=w2, w3=w3, w4=w4, timeout=0.5)
                is_moving = True
                last_vx = vx
                last_vy = vy
                last_wz = wz
                last_send_time = tnow
                ep_target_vel['vx'] = vx
                ep_target_vel['vy'] = vy
                ep_target_vel['vz'] = wz
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
        return [
            ('vx', "Vx(m/s)", 0.0),
            ('vy', "Vy(m/s)", 0.0),
            ('wz', "Wz(deg/s)", 0.0),
            ('arm_dx', "Arm dX", 0.0),
            ('arm_dy', "Arm dY", 0.0),
            ('grip_open', "Grip Open", 0.0),
            ('grip_close', "Grip Close", 0.0),
        ]
        
    def get_settings_schema(self):
        return []

    def execute_command(self, inputs, settings):
        global ep_node_intent

        vx_val = inputs.get('vx')
        vy_val = inputs.get('vy')
        wz_val = inputs.get('wz')
        if wz_val is None:
            wz_val = inputs.get('vz')

        arm_dx_val = inputs.get('arm_dx')
        arm_dy_val = inputs.get('arm_dy')
        grip_open_val = inputs.get('grip_open')
        grip_close_val = inputs.get('grip_close')

        if vx_val is not None or vy_val is not None or wz_val is not None:
            ep_node_intent['vx'] = float(vx_val or 0.0)
            ep_node_intent['vy'] = float(vy_val or 0.0)
            ep_node_intent['wz'] = float(wz_val or 0.0)
            if ep_node_intent['vx'] or ep_node_intent['vy'] or ep_node_intent['wz']:
                ep_node_intent['trigger_time'] = time.monotonic()

        arm_dx = float(arm_dx_val or 0.0)
        arm_dy = float(arm_dy_val or 0.0)
        if arm_dx or arm_dy:
            _ep_move_arm(delta_x=arm_dx, delta_y=arm_dy)

        grip_open_active = bool(grip_open_val)
        grip_close_active = bool(grip_close_val)
        if grip_open_active:
            _ep_set_gripper(True)
        elif grip_close_active:
            _ep_set_gripper(False)

        ep_target_vel['vx'] = ep_node_intent['vx']
        ep_target_vel['vy'] = ep_node_intent['vy']
        ep_target_vel['vz'] = ep_node_intent['wz']

        return {
            'vx': ep_target_vel['vx'],
            'vy': ep_target_vel['vy'],
            'vz': ep_target_vel['vz'],
            'arm_dx': arm_dx,
            'arm_dy': arm_dy,
            'grip_open': 1.0 if grip_open_active else 0.0,
            'grip_close': 1.0 if grip_close_active else 0.0,
        }

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
        self.out_arm_dx = generate_uuid()
        self.outputs[self.out_arm_dx] = PortType.DATA
        self.out_arm_dy = generate_uuid()
        self.outputs[self.out_arm_dy] = PortType.DATA
        self.out_grip_open = generate_uuid()
        self.outputs[self.out_grip_open] = PortType.DATA
        self.out_grip_close = generate_uuid()
        self.outputs[self.out_grip_close] = PortType.DATA
        self.out_flow = generate_uuid()
        self.outputs[self.out_flow] = PortType.FLOW
        self.arm_step = EP_ARM_STEP
        self.prev_keys = {}
        self.last_arm_input_time = 0.0

    def is_just_pressed(self, key):
        """키를 꾹 누르고 있을 때의 중복 실행을 막기 위해, 방금 눌린 순간만 True 반환"""
        current = bool(self.state.get(key))
        prev = self.prev_keys.get(key, False)
        self.prev_keys[key] = current
        return current and not prev

    def execute(self):
        if self.state.get('is_focused', False):
            return self.out_flow

        vx = 0.0
        vy = 0.0
        wz = 0.0
        ep_v_max = 0.5
        ep_w_max = 60.0
        arm_dx = 0.0
        arm_dy = 0.0
        grip_open = False
        grip_close = False

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

        if self.is_just_pressed('Z'): arm_dy = self.arm_step
        if self.is_just_pressed('X'): arm_dy = -self.arm_step
        if self.is_just_pressed('C'): arm_dx = -self.arm_step
        if self.is_just_pressed('V'): arm_dx = self.arm_step
        
        if self.is_just_pressed('U'):
            grip_open = True
        if self.is_just_pressed('J'):
            grip_close = True

        ep_node_intent['vx'] = vx
        ep_node_intent['vy'] = vy
        ep_node_intent['wz'] = wz
        if vx or vy or wz:
            ep_node_intent['trigger_time'] = time.monotonic()

        if arm_dx or arm_dy:
            _ep_move_arm(delta_x=arm_dx, delta_y=arm_dy)
            if arm_dy > 0:
                send_ep_command("arm_up")
            elif arm_dy < 0:
                send_ep_command("arm_down")
            if arm_dx > 0:
                send_ep_command("arm_right")
            elif arm_dx < 0:
                send_ep_command("arm_left")

        # Gripper: queue-based (each frame if pressed)
        if grip_open:
            _ep_set_gripper(True)
            send_ep_command("grip_open")
        elif grip_close:
            _ep_set_gripper(False)
            send_ep_command("grip_close")

        if callable(ep_drive_wheels_sender):
            try:
                ep_drive_wheels_sender(vx, vy, wz)
            except Exception:
                pass

        self.output_data[self.out_vx] = vx
        self.output_data[self.out_vy] = vy
        self.output_data[self.out_wz] = wz
        self.output_data[self.out_arm_dx] = arm_dx
        self.output_data[self.out_arm_dy] = arm_dy
        self.output_data[self.out_grip_open] = 1.0 if grip_open else 0.0
        self.output_data[self.out_grip_close] = 1.0 if grip_close else 0.0
        return self.out_flow

class EPCameraSourceNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "EP Camera Source", "EP_CAM_SRC")
        self.in_flow = generate_uuid()
        self.inputs[self.in_flow] = PortType.FLOW
        self.out_frame = generate_uuid()
        self.outputs[self.out_frame] = PortType.DATA
        self.out_flow = generate_uuid()
        self.outputs[self.out_flow] = PortType.FLOW
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

        return self.out_flow

class EPCameraStreamNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "EP Camera Stream", "EP_CAM_STREAM")
        self.in_flow = generate_uuid()
        self.inputs[self.in_flow] = PortType.FLOW
        self.in_frame = generate_uuid()
        self.inputs[self.in_frame] = PortType.DATA
        self.out_flow = generate_uuid()
        self.outputs[self.out_flow] = PortType.FLOW
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
            return self.out_flow

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

        return self.out_flow


class EPVideoFrameSaveNode(BaseNode):
    """EP 카메라 프레임을 지정 폴더에 저장하는 노드"""
    def __init__(self, node_id):
        super().__init__(node_id, "EP Video Save", "EP_VIS_SAVE")
        self.in_flow = generate_uuid()
        self.inputs[self.in_flow] = PortType.FLOW
        self.in_frame = generate_uuid()
        self.inputs[self.in_frame] = PortType.DATA
        self.out_flow = generate_uuid()
        self.outputs[self.out_flow] = PortType.FLOW

        self.state['folder'] = 'Captured_Images/ep01_saved'
        self.state['duration'] = 10.0
        self.state['use_timer'] = False
        self.state['max_frames'] = 100

        self._save_start_time = None
        self._frame_count = 0
        self._timer_completed_this_run = False
        self._frame_index = 0

    def _extract_frame_index(self, path):
        name = os.path.basename(path)
        if not (name.startswith("front_") and name.endswith(".jpg")):
            return -1
        number_part = name[6:-4]
        return int(number_part) if number_part.isdigit() else -1

    def _sync_frame_index_from_folder(self, folder):
        files = glob.glob(os.path.join(folder, "front_*.jpg"))
        max_idx = 0
        for path in files:
            idx = self._extract_frame_index(path)
            if idx > max_idx:
                max_idx = idx
        self._frame_index = max_idx

    def _prune_saved_frames(self, folder, max_frames):
        files = glob.glob(os.path.join(folder, "front_*.jpg"))
        if len(files) <= max_frames:
            return
        files.sort(key=lambda p: (self._extract_frame_index(p), os.path.getmtime(p)))
        for old_file in files[:len(files) - max_frames]:
            try:
                os.remove(old_file)
            except Exception:
                pass

    def execute(self):
        global ep_camera_save_state

        folder = str(self.state.get('folder', 'Captured_Images/ep01_saved')).strip() or 'Captured_Images/ep01_saved'
        is_saving = True
        duration = float(self.state.get('duration', 10.0))

        raw_use_timer = self.state.get('use_timer', False)
        if isinstance(raw_use_timer, str):
            use_timer = raw_use_timer.strip().lower() in ['1', 'true', 'yes', 'on']
        else:
            use_timer = bool(raw_use_timer)

        raw_max_frames = self.state.get('max_frames', 100)
        try:
            max_frames = max(1, int(float(raw_max_frames)))
        except Exception:
            max_frames = 100

        ep_camera_save_state['folder'] = folder
        ep_camera_save_state['duration'] = duration

        if not is_saving:
            self._timer_completed_this_run = False
            if self._save_start_time is not None:
                self._save_start_time = None
                ep_camera_save_state['status'] = 'Stopped'
                ep_camera_save_state['start_time'] = None
                ep_camera_save_state['frame_count'] = 0
            return self.out_flow

        if not self._save_start_time and not self._timer_completed_this_run:
            self._save_start_time = time.time()
            self._frame_count = 0
            ep_camera_save_state['status'] = 'Running'
            ep_camera_save_state['start_time'] = self._save_start_time
            try:
                os.makedirs(folder, exist_ok=True)
                self._sync_frame_index_from_folder(folder)
                write_log(f"[EP_VIS_SAVE] saving started: {folder}")
            except Exception as e:
                write_log(f"[EP_VIS_SAVE] failed to create folder: {e}")
                return self.out_flow

        if self._save_start_time and use_timer and duration > 0:
            elapsed = time.time() - self._save_start_time
            if elapsed > duration:
                write_log(f"[EP_VIS_SAVE] timer expired: {duration:.1f}s elapsed")
                self._save_start_time = None
                self._timer_completed_this_run = True
                ep_camera_save_state['status'] = 'Stopped'
                ep_camera_save_state['start_time'] = None
                ep_camera_save_state['frame_count'] = 0
                return self.out_flow

        frame = self.fetch_input_data(self.in_frame)
        if frame is not None and HAS_CV2 and self._save_start_time is not None:
            try:
                self._frame_index += 1
                filename = os.path.join(folder, f"front_{self._frame_index:06d}.jpg")
                success = cv2.imwrite(filename, frame)
                if success:
                    self._frame_count += 1
                    ep_camera_save_state['frame_count'] = self._frame_count
            except Exception as e:
                write_log(f"[EP_VIS_SAVE] frame save failed: {e}")

        if self._save_start_time is not None and not use_timer:
            self._prune_saved_frames(folder, max_frames)

        return self.out_flow


class EPServerSenderNode(BaseNode):
    """EP 저장 폴더 이미지를 원격 서버로 업로드하는 노드"""
    def __init__(self, node_id):
        super().__init__(node_id, "EP Server Sender", "EP_SERVER_SENDER")
        self.in_flow = generate_uuid()
        self.inputs[self.in_flow] = PortType.FLOW
        self.out_flow = generate_uuid()
        self.outputs[self.out_flow] = PortType.FLOW

        self.state['action'] = 'Start Sender'
        self.state['server_url'] = "http://210.110.250.33:5002/upload"

        self._last_action = None
        self._last_request_ts = 0.0

    def execute(self):
        global ep_sender_state, ep_sender_active

        if not HAS_AIOHTTP:
            return self.out_flow

        _ensure_ep_sender_manager_started()

        action = self.state.get('action', 'Start Sender')
        url = self.state.get('server_url', "http://210.110.250.33:5002/upload")
        now = time.monotonic()
        cooldown_ok = (now - self._last_request_ts) > 0.5

        if action != self._last_action:
            self._last_action = action

        if action == "Start Sender":
            if (not ep_sender_active) and ep_sender_state['status'] in ['Stopped', 'Stopping...'] and cooldown_ok:
                ep_sender_state['status'] = 'Starting...'
                ep_sender_command_queue.append(('START', url))
                self._last_request_ts = now
        elif action == "Stop Sender":
            if ep_sender_active and ep_sender_state['status'] in ['Running', 'Starting...'] and cooldown_ok:
                ep_sender_state['status'] = 'Stopping...'
                ep_sender_command_queue.append(('STOP', url))
                self._last_request_ts = now

        return self.out_flow


class EPServerJsonRecvNode(BaseNode):
    """EP01 JSON 파일 수신 노드 (Go1 JSON Receiver의 파일 수신 로직 기반)."""
    def __init__(self, node_id):
        super().__init__(node_id, "EP JSON Receiver", "EP_SERVER_JSON_RECV")
        self.in_flow = generate_uuid()
        self.inputs[self.in_flow] = PortType.FLOW
        self.out_flow = generate_uuid()
        self.outputs[self.out_flow] = PortType.FLOW

        self.out_raw_json = generate_uuid()
        self.outputs[self.out_raw_json] = PortType.DATA

        self.state['source'] = 'test_payloads/sample.json'
        self.state['poll_interval_sec'] = 0.1
        self.state['fresh_timeout_sec'] = 0.3

        self._last_poll_mono = 0.0
        self._last_ok_mono = 0.0
        self._last_raw_json = ''
        self._last_payload = {}
        self._last_seq = 0
        self._last_error = ''
        self._last_logged_raw = ''
        self._last_logged_error = ''

    def _read_source_text(self, source):
        source = str(source or '').strip()
        if not source:
            raise RuntimeError('source is empty')
        if not os.path.exists(source):
            raise FileNotFoundError(source)
        with open(source, 'r', encoding='utf-8') as f:
            return f.read()

    def _pick_payload(self, payload):
        if isinstance(payload, dict):
            for key in ('cmd', 'data', 'payload', 'command'):
                nested = payload.get(key)
                if isinstance(nested, dict):
                    return nested
            return payload
        if isinstance(payload, list) and payload:
            last_item = payload[-1]
            if isinstance(last_item, dict):
                return last_item
        return {}

    def _publish_state(self, raw_json, payload, connected, fresh, status, source):
        try:
            seq = int(payload.get('seq', self._last_seq))
        except Exception:
            seq = self._last_seq

        try:
            ts = float(payload.get('ts', payload.get('timestamp', time.time())))
        except Exception:
            ts = time.time()

        self._last_seq = seq
        self._last_raw_json = raw_json
        self._last_payload = dict(payload)
        if connected and fresh:
            self._last_ok_mono = time.monotonic()

        ep_server_json_data.update({
            'raw_json': raw_json,
            'seq': seq,
            'ts': ts,
            'connected': bool(connected),
            'fresh': bool(fresh),
            'status': status,
            'source': source,
        })

        self.output_data[self.out_raw_json] = raw_json

    def execute(self):
        source = str(self.state.get('source', '')).strip()
        poll_interval_sec = max(0.0, float(self.state.get('poll_interval_sec', 0.1)))
        fresh_timeout_sec = max(0.05, float(self.state.get('fresh_timeout_sec', 0.3)))

        now_mono = time.monotonic()
        should_poll = (now_mono - self._last_poll_mono) >= poll_interval_sec or not self._last_raw_json

        if should_poll:
            self._last_poll_mono = now_mono
            try:
                raw_json = self._read_source_text(source)
                parsed = json.loads(raw_json)
                payload = self._pick_payload(parsed)
                if not isinstance(payload, dict):
                    payload = {}

                if 'ts' not in payload and 'timestamp' not in payload:
                    payload['ts'] = time.time()

                self._last_error = ''
                self._publish_state(raw_json, payload, True, True, 'OK', source)

                raw_for_log = raw_json.strip()
                if raw_for_log != self._last_logged_raw:
                    write_log(f"[EP JSON RX] read ok | source={source}")
                    self._last_logged_raw = raw_for_log
                self._last_logged_error = ''
            except Exception as e:
                self._last_error = str(e)
                fresh = (now_mono - self._last_ok_mono) <= fresh_timeout_sec if self._last_ok_mono else False
                status = f'ERR: {e.__class__.__name__}'
                self._publish_state(self._last_raw_json, self._last_payload, False, fresh, status, source)
                if self._last_error != self._last_logged_error:
                    write_log(f"[EP JSON RX] read error | source={source} | {e.__class__.__name__}: {self._last_error}")
                    self._last_logged_error = self._last_error
        else:
            fresh = (now_mono - self._last_ok_mono) <= fresh_timeout_sec if self._last_ok_mono else False
            status = ep_server_json_data.get('status', 'Idle')
            if not fresh and status == 'OK':
                status = 'STALE'
            self._publish_state(self._last_raw_json, self._last_payload, bool(self._last_raw_json), fresh, status, source)

        return self.out_flow

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


# ================= [EP01 Mission System] =================
from core.mission_utils import (
    _coerce_bool, _coerce_float,
    _mission_signature, _normalize_mission_container,
    _extract_mission_id, _extract_mission_type,
    _extract_mission_post_action, _post_json_payload,
)

EP01_MISSION_PENDING_URL  = str(EP01_MISSION_CONFIG.get('pending_url',  'http://localhost:18080/ep01/pending'))
EP01_MISSION_DECISION_URL = str(EP01_MISSION_CONFIG.get('decision_url', 'http://localhost:18080/ep01/decision'))
EP01_MISSION_POLL_SEC     = float(EP01_MISSION_CONFIG.get('poll_interval_sec',   1.0))
EP01_MISSION_TIMEOUT_SEC  = float(EP01_MISSION_CONFIG.get('request_timeout_sec', 0.5))
EP01_MISSION_DECISION_MODE  = str(EP01_MISSION_CONFIG.get('decision_mode', 'accept_all'))
EP01_MISSION_ALLOWED_TYPES  = list(EP01_MISSION_CONFIG.get('allowed_mission_types', ['ep01', 'robot_action']))

_ep01_mission_run_id = 0

ep01_mission_state = {
    'status':          'Idle',
    'mission_id':      '',
    'mission_type':    '',
    'decision':        '',
    'decision_reason': '',
    'action_json':     '',
    'source':          EP01_MISSION_PENDING_URL,
    'last_error':      '',
    'updated_ts':      0.0,
}


def _get_ep01_dashboard_live():
    """Return (ep_dashboard, ep_state) reflecting actual connection (worker-mode aware)."""
    import sys as _sys
    for _name in ('ui.dpg_manager', 'dpg_manager', __name__, 'nodes.robots.ep01', 'ep01'):
        _mod = _sys.modules.get(_name)
        if _mod is None or not hasattr(_mod, 'ep_dashboard'):
            continue
        _dash = getattr(_mod, 'ep_dashboard', {})
        if 'Online' in _dash.get('hw_link', ''):
            return _dash, getattr(_mod, 'ep_state', {})
    return ep_dashboard, ep_state


def _evaluate_ep01_mission_conditions(conditions):
    if not isinstance(conditions, dict):
        return True, ''

    if _coerce_bool(conditions.get('ep01_connected', False), False):
        _ep_dash, _ = _get_ep01_dashboard_live()
        hw = _ep_dash.get('hw_link', 'Offline')
        write_log(f'[EP01 CONDITION] ep01 hw_link={hw!r}')
        if 'Online' not in hw:
            return False, f'ep01 not connected (hw_link={hw})'

    ep01_batt_min = conditions.get('ep01_battery_min')
    if ep01_batt_min is not None:
        _ep_dash, _ep_st = _get_ep01_dashboard_live()
        hw = _ep_dash.get('hw_link', 'Offline')
        if 'Online' not in hw:
            return False, 'ep01 not connected (cannot check battery)'
        min_pct = _coerce_float(ep01_batt_min, 0.0)
        batt = (_ep_st or {}).get('battery', -1)
        if batt < 0:
            return False, 'ep01 battery unknown'
        if batt < min_pct:
            return False, f'ep01 battery too low ({batt}% < {min_pct:.0f}%)'

    return True, ''


def _build_ep01_post_action_json(payload):
    action_payload = _extract_mission_post_action(payload)
    mission_id = _extract_mission_id(payload)

    if isinstance(action_payload, dict) and str(action_payload.get('type', '')).strip().lower() == 'sequence':
        return json.dumps({
            'mission_id': mission_id,
            'type': 'sequence',
            'steps': action_payload.get('steps', []),
        }, ensure_ascii=False)

    result = {'mission_id': mission_id}
    if isinstance(action_payload, dict):
        result.update({k: v for k, v in action_payload.items() if k != 'mission_id'})
    return json.dumps(result, ensure_ascii=False)


class EP01MissionReceiverNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "Mission Receiver (EP01)", "EP01_MISSION_RECV")
        self.in_flow = generate_uuid()
        self.inputs[self.in_flow] = PortType.FLOW
        self.out_raw_json = generate_uuid()
        self.outputs[self.out_raw_json] = PortType.DATA
        self.out_mission_id = generate_uuid()
        self.outputs[self.out_mission_id] = PortType.DATA
        self.out_has_mission = generate_uuid()
        self.outputs[self.out_has_mission] = PortType.DATA
        self.out_flow = generate_uuid()
        self.outputs[self.out_flow] = PortType.FLOW

        self.state['mode'] = 'HTTP'
        self.state['source'] = EP01_MISSION_PENDING_URL
        self.state['poll_interval_sec'] = EP01_MISSION_POLL_SEC
        self.state['request_timeout_sec'] = EP01_MISSION_TIMEOUT_SEC
        self._last_poll_mono = 0.0
        self._last_signature = ''
        self._last_raw_json = ''
        self._last_mission_id = ''
        self._last_has_mission = False
        self._new_mission_pulse = False
        self._last_run_generation = -1

    def _read_source_text(self, mode, source, timeout_sec):
        source = str(source or '').strip()
        if not source:
            raise RuntimeError('source is empty')
        if mode == 'FILE' or (not source.startswith('http://') and not source.startswith('https://')):
            if not os.path.exists(source):
                raise FileNotFoundError(source)
            with open(source, 'r', encoding='utf-8') as f:
                return f.read()
        req = urllib.request.Request(
            source,
            headers={'Accept': 'application/json', 'Cache-Control': 'no-cache', 'Pragma': 'no-cache'},
            method='GET',
        )
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            charset = resp.headers.get_content_charset() or 'utf-8'
            return resp.read().decode(charset, errors='replace')

    def execute(self):
        cur_gen = engine_module.run_generation
        if cur_gen != self._last_run_generation:
            self._last_run_generation = cur_gen
            global _ep01_mission_run_id
            _ep01_mission_run_id += 1
            self._last_signature = ''
            self._last_poll_mono = 0.0
            write_log("[EP01 MISSION RX] restart detected - resetting signature")

        mode = str(self.state.get('mode', 'HTTP')).strip().upper()
        source = str(self.state.get('source', EP01_MISSION_PENDING_URL)).strip()
        poll_sec = max(0.0, _coerce_float(self.state.get('poll_interval_sec', EP01_MISSION_POLL_SEC), EP01_MISSION_POLL_SEC))
        timeout_sec = max(0.2, _coerce_float(self.state.get('request_timeout_sec', EP01_MISSION_TIMEOUT_SEC), EP01_MISSION_TIMEOUT_SEC))
        now_mono = time.monotonic()

        if (now_mono - self._last_poll_mono) < poll_sec and self._last_raw_json:
            self.output_data[self.out_raw_json] = self._last_raw_json
            self.output_data[self.out_mission_id] = self._last_mission_id
            self.output_data[self.out_has_mission] = bool(self._last_has_mission)
            return self.out_flow if self._new_mission_pulse else None

        self._last_poll_mono = now_mono
        try:
            raw_json = self._read_source_text(mode, source, timeout_sec)
            payload, signature = _normalize_mission_container(raw_json)
            mission_id = _extract_mission_id(payload)
            has_mission = bool(mission_id or payload)

            self._last_raw_json = raw_json
            self._last_mission_id = mission_id
            self._last_has_mission = has_mission
            self.output_data[self.out_raw_json] = raw_json
            self.output_data[self.out_mission_id] = mission_id
            self.output_data[self.out_has_mission] = has_mission

            ep01_mission_state.update({
                'status': 'Pending' if has_mission else 'Idle',
                'mission_id': mission_id,
                'source': source,
                'last_error': '',
                'updated_ts': time.time(),
            })

            if has_mission and signature and signature != self._last_signature:
                self._last_signature = signature
                self._new_mission_pulse = True
                ep01_mission_state['mission_type'] = _extract_mission_type(payload)
                write_log(f"[EP01 MISSION RX] pending mission received: {mission_id or 'unknown'}")
            else:
                self._new_mission_pulse = False
        except Exception as e:
            self._new_mission_pulse = False
            ep01_mission_state.update({
                'status': 'Error',
                'last_error': str(e),
                'source': source,
                'updated_ts': time.time(),
            })
            write_log(f"[EP01 MISSION RX] error: {e}")
            self.output_data[self.out_raw_json] = self._last_raw_json
            self.output_data[self.out_mission_id] = self._last_mission_id
            self.output_data[self.out_has_mission] = bool(self._last_has_mission)

        if self._new_mission_pulse:
            self._new_mission_pulse = False
            return self.out_flow
        return None


class EP01MissionDecisionNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "Mission Decision (EP01)", "EP01_MISSION_DECIDE")
        self.in_flow = generate_uuid()
        self.inputs[self.in_flow] = PortType.FLOW
        self.in_raw_json = generate_uuid()
        self.inputs[self.in_raw_json] = PortType.DATA
        self.out_raw_json = generate_uuid()
        self.outputs[self.out_raw_json] = PortType.DATA
        self.out_mission_id = generate_uuid()
        self.outputs[self.out_mission_id] = PortType.DATA
        self.out_decision = generate_uuid()
        self.outputs[self.out_decision] = PortType.DATA
        self.out_reason = generate_uuid()
        self.outputs[self.out_reason] = PortType.DATA
        self.out_accepted = generate_uuid()
        self.outputs[self.out_accepted] = PortType.DATA
        self.out_flow = generate_uuid()
        self.outputs[self.out_flow] = PortType.FLOW

        self.state['decision_mode'] = EP01_MISSION_DECISION_MODE
        self.state['allowed_mission_types'] = ', '.join(EP01_MISSION_ALLOWED_TYPES)
        self.state['decision_url'] = EP01_MISSION_DECISION_URL
        self.state['request_timeout_sec'] = EP01_MISSION_TIMEOUT_SEC
        self._last_post_signature = ''
        self._last_run_generation = -1

    def execute(self):
        cur_gen = engine_module.run_generation
        if cur_gen != self._last_run_generation:
            self._last_run_generation = cur_gen
            self._last_post_signature = ''

        raw_json = self.fetch_input_data(self.in_raw_json)
        payload, signature = _normalize_mission_container(raw_json)
        if not payload:
            write_log("[EP01 MISSION DECIDE] no input payload - skipping")
            return None

        mission_id   = _extract_mission_id(payload)
        mission_type = _extract_mission_type(payload)
        mode = str(self.state.get('decision_mode', EP01_MISSION_DECISION_MODE)).strip().lower()
        allowed_types = [t.strip().lower() for t in str(self.state.get('allowed_mission_types', ', '.join(EP01_MISSION_ALLOWED_TYPES))).split(',') if t.strip()]

        conditions = payload.get('conditions')
        cond_fail = ''
        if isinstance(conditions, dict) and conditions:
            cond_ok, cond_fail = _evaluate_ep01_mission_conditions(conditions)
            if not cond_ok:
                cond_fail = f'condition failed: {cond_fail}'

        if cond_fail:
            decision, reason = 'reject', cond_fail
        elif not mission_id:
            decision, reason = 'reject', 'missing mission_id'
        elif mode == 'accept_all':
            decision, reason = 'accept', 'accept_all'
        elif mode == 'accept_if_allowed_type' and allowed_types:
            decision = 'accept' if mission_type.lower() in allowed_types else 'reject'
            reason = 'allowed_type' if decision == 'accept' else 'type mismatch'
        else:
            decision = 'accept' if (not allowed_types or not mission_type or mission_type.lower() in allowed_types) else 'reject'
            reason = 'type ok' if decision == 'accept' else 'type blocked'

        accepted = decision == 'accept'
        decision_url = str(self.state.get('decision_url', EP01_MISSION_DECISION_URL)).strip() or EP01_MISSION_DECISION_URL
        timeout_sec  = max(0.2, _coerce_float(self.state.get('request_timeout_sec', EP01_MISSION_TIMEOUT_SEC), EP01_MISSION_TIMEOUT_SEC))

        if mission_id and signature and signature != self._last_post_signature:
            self._last_post_signature = signature
            write_log(f"[EP01 MISSION DECIDE] mission={mission_id} type={mission_type} -> {decision} ({reason})")
            try:
                status_code, body = _post_json_payload(decision_url, {'mission_id': mission_id, 'decision': decision}, timeout_sec)
                write_log(f"[EP01 MISSION DECIDE] POST {decision_url} -> rc={status_code}")
                if body:
                    write_log(f"[EP01 MISSION DECIDE] response: {body[:200]}")
                ep01_mission_state.update({
                    'status': 'Accepted' if accepted else 'Rejected',
                    'mission_id': mission_id, 'mission_type': mission_type,
                    'decision': decision, 'decision_reason': reason,
                    'source': decision_url, 'last_error': '', 'updated_ts': time.time(),
                })
            except Exception as e:
                ep01_mission_state.update({
                    'status': 'DecisionError',
                    'mission_id': mission_id, 'mission_type': mission_type,
                    'decision': decision, 'decision_reason': reason,
                    'source': decision_url, 'last_error': str(e), 'updated_ts': time.time(),
                })
                write_log(f"[EP01 MISSION DECIDE] POST failed: {e} -> continuing with decision={decision}")
        elif mission_id and signature == self._last_post_signature:
            write_log(f"[EP01 MISSION DECIDE] duplicate mission skipped: {mission_id}")
        elif not mission_id:
            ep01_mission_state.update({
                'status': 'Rejected', 'mission_id': '', 'mission_type': mission_type,
                'decision': 'reject', 'decision_reason': reason,
                'source': decision_url, 'last_error': '', 'updated_ts': time.time(),
            })

        self.output_data[self.out_raw_json] = raw_json
        self.output_data[self.out_mission_id] = mission_id
        self.output_data[self.out_decision] = decision
        self.output_data[self.out_reason] = reason
        self.output_data[self.out_accepted] = accepted
        return self.out_flow


class EP01MissionDispatchNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "Mission Dispatch (EP01)", "EP01_MISSION_DISPATCH")
        self.in_flow = generate_uuid()
        self.inputs[self.in_flow] = PortType.FLOW
        self.in_raw_json = generate_uuid()
        self.inputs[self.in_raw_json] = PortType.DATA
        self.in_decision = generate_uuid()
        self.inputs[self.in_decision] = PortType.DATA
        self.out_action_json = generate_uuid()
        self.outputs[self.out_action_json] = PortType.DATA
        self.out_mission_id = generate_uuid()
        self.outputs[self.out_mission_id] = PortType.DATA
        self.out_accepted = generate_uuid()
        self.outputs[self.out_accepted] = PortType.DATA
        self.out_flow = generate_uuid()
        self.outputs[self.out_flow] = PortType.FLOW
        self._last_dispatch_signature = ''
        self._last_run_generation = -1

    def execute(self):
        cur_gen = engine_module.run_generation
        if cur_gen != self._last_run_generation:
            self._last_run_generation = cur_gen
            self._last_dispatch_signature = ''

        raw_json = self.fetch_input_data(self.in_raw_json)
        decision = self.fetch_input_data(self.in_decision)
        payload, signature = _normalize_mission_container(raw_json)
        if not payload:
            write_log("[EP01 MISSION DISPATCH] no input payload - skipping")
            return None

        mission_id = _extract_mission_id(payload)
        if isinstance(decision, bool):
            accepted = decision
        else:
            accepted = str(decision).strip().lower() in ('accept', 'true', '1', 'yes') if decision is not None else False

        if accepted:
            action_json = _build_ep01_post_action_json(payload)
            ep01_mission_state.update({
                'status': 'Dispatched', 'mission_id': mission_id,
                'mission_type': _extract_mission_type(payload),
                'decision': 'accept', 'decision_reason': 'mission accepted',
                'action_json': action_json, 'last_error': '', 'updated_ts': time.time(),
            })
            if signature and signature != self._last_dispatch_signature:
                self._last_dispatch_signature = signature
                preview = (action_json[:120] + '...') if action_json and len(action_json) > 120 else action_json
                write_log(f"[EP01 MISSION DISPATCH] mission dispatched: {mission_id or 'unknown'}")
                write_log(f"[EP01 MISSION DISPATCH] action_json: {preview}")
            elif signature and signature == self._last_dispatch_signature:
                write_log(f"[EP01 MISSION DISPATCH] duplicate mission skipped: {mission_id}")
        else:
            action_json = ''
            write_log(f"[EP01 MISSION DISPATCH] mission rejected: {mission_id or 'unknown'} (decision={decision})")
            ep01_mission_state.update({
                'status': 'Rejected', 'mission_id': mission_id,
                'mission_type': _extract_mission_type(payload),
                'decision': 'reject', 'decision_reason': 'mission rejected',
                'action_json': '', 'last_error': '', 'updated_ts': time.time(),
            })

        self.output_data[self.out_action_json] = action_json
        self.output_data[self.out_mission_id] = mission_id
        self.output_data[self.out_accepted] = accepted
        return self.out_flow


class EP01MissionActionNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "Mission Action (EP01)", "EP01_MISSION_ACTION")
        self.in_flow = generate_uuid()
        self.inputs[self.in_flow] = PortType.FLOW
        self.in_mission_action = generate_uuid()
        self.inputs[self.in_mission_action] = PortType.DATA
        self.out_flow = generate_uuid()
        self.outputs[self.out_flow] = PortType.FLOW
        self._last_mission_action_signature = ''
        self._seq_running = False
        self._seq_thread = None
        self._last_run_id = -1
        self._seq_gen = 0

    def _run_sequence(self, steps, seq_gen):
        write_log(f"[EP01 ACTION SEQ] sequence started: {len(steps)} steps")
        for i, step in enumerate(steps):
            if not self._seq_running or self._seq_gen != seq_gen:
                write_log("[EP01 ACTION SEQ] sequence aborted (restart detected)")
                break
            channel  = str(step.get('channel', 'stop')).strip().lower()
            duration = _coerce_float(step.get('duration_sec', step.get('duration', 0.5)), 0.5)
            write_log(f"[EP01 ACTION SEQ] step {i+1}/{len(steps)}: channel={channel}")

            if channel == 'drive':
                vx = _coerce_float(step.get('vx', 0.0), 0.0)
                vy = _coerce_float(step.get('vy', 0.0), 0.0)
                wz = _coerce_float(step.get('wz', 0.0), 0.0)
                ep_node_intent['stop'] = False
                ep_node_intent['vx'] = vx
                ep_node_intent['vy'] = vy
                ep_node_intent['wz'] = wz
                ep_node_intent['trigger_time'] = time.monotonic()
                deadline = time.monotonic() + duration
                while time.monotonic() < deadline and self._seq_running and self._seq_gen == seq_gen:
                    ep_node_intent['trigger_time'] = time.monotonic()
                    if callable(ep_drive_wheels_sender):
                        try:
                            ep_drive_wheels_sender(vx, vy, wz)
                        except Exception:
                            pass
                    time.sleep(0.02)
            elif channel == 'grip':
                open_val = _coerce_bool(step.get('open', True), True)
                grip_wait = _coerce_float(step.get('duration_sec', step.get('duration', 3.0)), 3.0)
                cmd = "grip_open" if open_val else "grip_close"
                send_ep_command(cmd)
                deadline = time.monotonic() + grip_wait
                while time.monotonic() < deadline and self._seq_running and self._seq_gen == seq_gen:
                    time.sleep(0.05)
            elif channel == 'led':
                _LED_COLORS = {
                    'red':    'led_red',
                    'blue':   'led_blue',
                    'yellow': 'led_yellow',
                    'green':  'led_green',
                }
                color = str(step.get('color', 'red')).strip().lower()
                cmd = _LED_COLORS.get(color, 'led_red')
                send_ep_command(cmd)
                if duration > 0:
                    time.sleep(duration)
            else:  # stop or unknown
                ep_node_intent['stop'] = True
                ep_node_intent['vx'] = 0.0
                ep_node_intent['vy'] = 0.0
                ep_node_intent['wz'] = 0.0
                if duration > 0:
                    time.sleep(duration)

            if i < len(steps) - 1:
                ep_node_intent['vx'] = 0.0
                ep_node_intent['vy'] = 0.0
                ep_node_intent['wz'] = 0.0
                ep_node_intent['stop'] = True
                time.sleep(0.15)

            write_log(f"[EP01 ACTION SEQ] step {i+1}/{len(steps)} done")

        if self._seq_gen == seq_gen:
            self._seq_running = False
        write_log("[EP01 ACTION SEQ] sequence complete")

    def execute(self):
        if _ep01_mission_run_id != self._last_run_id:
            self._last_run_id = _ep01_mission_run_id
            self._last_mission_action_signature = ''
            self._seq_gen += 1
            self._seq_running = False

        mission_action_raw = self.fetch_input_data(self.in_mission_action)
        if mission_action_raw is None:
            return self.out_flow

        sig = _mission_signature(mission_action_raw)
        if not sig or sig == self._last_mission_action_signature:
            return self.out_flow

        self._last_mission_action_signature = sig
        payload, _ = _normalize_mission_container(mission_action_raw)
        if not isinstance(payload, dict):
            return self.out_flow

        action_type   = str(payload.get('type', '')).strip().lower()
        mission_id_log = payload.get('mission_id', 'unknown')

        if action_type == 'sequence':
            steps = payload.get('steps', [])
            write_log(f"[EP01 ACTION] sequence mission received: id={mission_id_log} steps={len(steps)}")
            if not self._seq_running:
                self._seq_running = True
                gen = self._seq_gen
                self._seq_thread = threading.Thread(
                    target=self._run_sequence, args=(steps, gen), daemon=True)
                self._seq_thread.start()
            else:
                write_log("[EP01 ACTION] previous sequence running - skipping new sequence")
        else:
            channel = str(payload.get('channel', 'stop')).strip().lower()
            write_log(f"[EP01 ACTION] single action received: id={mission_id_log} channel={channel}")
            if channel == 'drive':
                vx = _coerce_float(payload.get('vx', 0.0), 0.0)
                vy = _coerce_float(payload.get('vy', 0.0), 0.0)
                wz = _coerce_float(payload.get('wz', 0.0), 0.0)
                ep_node_intent['stop'] = False
                ep_node_intent['vx'] = vx
                ep_node_intent['vy'] = vy
                ep_node_intent['wz'] = wz
                ep_node_intent['trigger_time'] = time.monotonic()
            elif channel == 'grip':
                _ep_set_gripper(_coerce_bool(payload.get('open', True), True))
            else:
                ep_node_intent['stop'] = True
                ep_node_intent['vx'] = 0.0
                ep_node_intent['vy'] = 0.0
                ep_node_intent['wz'] = 0.0

        return self.out_flow