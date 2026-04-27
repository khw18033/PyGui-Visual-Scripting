import os
import sys
import time
import json
import math
import socket
import threading
import platform
import subprocess
import glob
import asyncio
import urllib.request
import urllib.error
from datetime import datetime
from collections import deque

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

try:
    import aiohttp
    HAS_AIOHTTP = True
except ImportError:
    aiohttp = None
    HAS_AIOHTTP = False

try:
    import torch
    HAS_TORCH = True
except ImportError:
    torch = None
    HAS_TORCH = False

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    Image = None
    HAS_PIL = False

try:
    from depth_anything_v2.dpt import DepthAnythingV2
    HAS_DA2_OFFICIAL = True
except ImportError:
    DepthAnythingV2 = None
    HAS_DA2_OFFICIAL = False

try:
    from transformers import pipeline as hf_pipeline
    HAS_TRANSFORMERS = True
except ImportError:
    hf_pipeline = None
    HAS_TRANSFORMERS = False

# ================= [Unitree SDK Import (Optional)] =================
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, '..', '..'))
arch = platform.machine().lower()
if arch in ['aarch64', 'arm64']:
    sdk_arch = 'arm64'
elif arch in ['x86_64', 'amd64']:
    sdk_arch = 'amd64'
else:
    sdk_arch = 'amd64'

sdk_path = os.path.join(project_root, 'unitree_legged_sdk', 'lib', 'python', sdk_arch)
if os.path.isdir(sdk_path) and sdk_path not in sys.path:
    sys.path.append(sdk_path)

try:
    import robot_interface as sdk
    HAS_UNITREE_SDK = True
    SDK_IMPORT_ERROR = ""
except Exception:
    sdk = None
    HAS_UNITREE_SDK = False
    SDK_IMPORT_ERROR = "robot_interface import failed"


# ================= [Go1 Globals] =================
go1_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
go1_sock.setblocking(False)

HIGHLEVEL = 0xEE
LOCAL_PORT = 8090
GO1_IP = "192.168.50.41"
GO1_PORT = 8082

GO1_UNITY_IP = "192.168.50.246"
UNITY_STATE_PORT = 15101
UNITY_CMD_PORT = 15102
UNITY_RX_PORT = 15100

DT = 0.02
V_MAX = 0.4
S_MAX = 0.4
W_MAX = 2.0
VX_CMD = 0.20
VY_CMD = 0.20
WZ_CMD = 1.00

BODY_HEIGHT_MIN = -0.12
BODY_HEIGHT_MAX = 0.12
BODY_HEIGHT_KEY_STEP = 0.005

hold_timeout_sec = 0.1
repeat_grace_sec = 0.4
min_move_sec = 0.4
stop_brake_sec = 0.0
unity_timeout_sec = 0.15

_GO1_IP_INITIALIZED = False

go1_target_vel = {
    'vx': 0.0,
    'vy': 0.0,
    'vyaw': 0.0,
    'body_height': 0.0,
}

go1_node_intent = {
    'vx': 0.0,
    'vy': 0.0,
    'wz': 0.0,
    'body_height': 0.0,
    'yaw_align': False,
    'reset_yaw': False,
    'stop': False,
    'use_unity_cmd': True,
    'send_aruco': False,
    'trigger_time': time.monotonic(),
}

go1_state = {
    'world_x': 0.0,
    'world_z': 0.0,
    'yaw_unity': 0.0,
    'vx_cmd': 0.0,
    'vy_cmd': 0.0,
    'wz_cmd': 0.0,
    'body_height_cmd': 0.0,
    'mode': 1,
    'reason': "NONE",
    'battery': -1,
    'control_latency_ms': 0.0,
}

go1_unity_data = {
    'vx': 0.0,
    'vy': 0.0,
    'wz': 0.0,
    'estop': 0,
    'active': False,
}

go1_server_json_data = {
    'raw_json': '',
    'seq': 0,
    'ts': 0.0,
    'vx': 0.0,
    'vy': 0.0,
    'wz': 0.0,
    'stop': True,
    'confidence': 0.0,
    'connected': False,
    'fresh': False,
    'status': 'Idle',
    'source': '',
}

go1_dashboard = {
    "status": "Idle",
    "hw_link": "Offline",
    "unity_link": "Waiting",
    "special": "Idle",
}

GO1_SPECIAL_ACTIONS = {
    'backflip': {'mode': 9, 'trigger_sec': 0.4, 'wait_timeout': 5.0, 'recovery': 'stand'},
    'jumpyaw': {'mode': 10, 'trigger_sec': 0.2, 'wait_timeout': 4.0, 'recovery': 'stand'},
    'straighthand': {'mode': 11, 'trigger_sec': 0.2, 'wait_timeout': 5.0, 'recovery': 'stand'},
    'dance1': {'mode': 12, 'trigger_sec': 0.2, 'wait_timeout': 8.0, 'recovery': 'idle'},
    'dance2': {'mode': 13, 'trigger_sec': 0.2, 'wait_timeout': 8.0, 'recovery': 'idle'},
}

go1_special_queue = deque()
go1_special_state = {
    'active': False,
    'name': '',
    'mode': 0,
    'phase': 'idle',
    'queue_size': 0,
}

aruco_settings = {
    'enabled': False,
    'marker_size': 0.03,
}

zero_dist_coeffs = np.zeros((4, 1), dtype=np.float32) if HAS_CV2 and np is not None else None

camera_state = {
    'status': 'Stopped',
    'target_ip': '',
    'duration': 0.0,
    'start_time': 0.0,
    'timer_started_logged': False,
    'last_interval_count': 0,
}

camera_command_queue = deque()
GO1_CAMERA_NANOS = ["unitree@192.168.123.13"]
CAMERA_CONFIG = [
    {"folder": "Captured_Images/go1_front", "id": "go1_front"}
]
_CAMERA_WORKER_STARTED = False
_CAMERA_RECEIVER_PROC = None

camera_save_state = {
    'status': 'Stopped',
    'folder': 'Captured_Images/go1_saved',
    'duration': 0.0,
    'start_time': None,
    'frame_count': 0,
}
camera_save_queue = deque()

# ================= [Go1 Server Sender (HTTP Upload)] =================
sender_state = {'status': 'Stopped'}
sender_command_queue = deque()
multi_sender_active = False
# Server sender uses a higher polling/send target for smoother updates.
TARGET_FPS = 30
INTERVAL = 1.0 / TARGET_FPS
_SENDER_MANAGER_STARTED = False

_DA2_MODEL_LOCK = threading.Lock()
_DA2_MODEL_CACHE = {}

_DA2_MODEL_CONFIGS = {
    'vits': {'encoder': 'vits', 'features': 64, 'out_channels': [48, 96, 192, 384]},
    'vitb': {'encoder': 'vitb', 'features': 128, 'out_channels': [96, 192, 384, 768]},
    'vitl': {'encoder': 'vitl', 'features': 256, 'out_channels': [256, 512, 1024, 1024]},
    'vitg': {'encoder': 'vitg', 'features': 384, 'out_channels': [1536, 1536, 1536, 1536]},
}


def _clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def _extract_front_frame_index(path):
    name = os.path.basename(path)
    if not (name.startswith("front_") and name.endswith(".jpg")):
        return -1
    number_part = name[6:-4]
    return int(number_part) if number_part.isdigit() else -1


def _compute_roi_pixels(height, width, x0, y0, x1, y1):
    x0 = _clamp(_coerce_float(x0, 0.3), 0.0, 1.0)
    y0 = _clamp(_coerce_float(y0, 0.5), 0.0, 1.0)
    x1 = _clamp(_coerce_float(x1, 0.7), 0.0, 1.0)
    y1 = _clamp(_coerce_float(y1, 0.95), 0.0, 1.0)
    if x1 <= x0:
        x0, x1 = 0.3, 0.7
    if y1 <= y0:
        y0, y1 = 0.5, 0.95

    px0 = int(x0 * width)
    py0 = int(y0 * height)
    px1 = int(x1 * width)
    py1 = int(y1 * height)
    px0 = max(0, min(px0, width - 1))
    py0 = max(0, min(py0, height - 1))
    px1 = max(px0 + 1, min(px1, width))
    py1 = max(py0 + 1, min(py1, height))
    return px0, py0, px1, py1


def _normalize_depth_for_visual(depth_map):
    if depth_map is None or np is None:
        return None
    d = np.asarray(depth_map, dtype=np.float32)
    finite = np.isfinite(d)
    if not np.any(finite):
        return np.zeros_like(d, dtype=np.float32)
    valid = d[finite]
    lo = float(np.percentile(valid, 2.0))
    hi = float(np.percentile(valid, 98.0))
    if hi <= lo:
        hi = lo + 1e-6
    norm = (d - lo) / (hi - lo)
    norm = np.clip(norm, 0.0, 1.0)
    norm[~finite] = 0.0
    return norm


def _get_da2_device_name(prefer_cuda=True):
    if not HAS_TORCH or torch is None:
        return 'cpu'
    if prefer_cuda and torch.cuda.is_available():
        return 'cuda'
    mps_ok = bool(getattr(torch.backends, 'mps', None)) and torch.backends.mps.is_available()
    if mps_ok:
        return 'mps'
    return 'cpu'


def _load_da2_official_model(encoder, checkpoint_path, prefer_cuda=True):
    if not (HAS_DA2_OFFICIAL and HAS_TORCH):
        return None, "official model dependencies missing"

    encoder = str(encoder or 'vits').strip().lower()
    if encoder not in _DA2_MODEL_CONFIGS:
        encoder = 'vits'

    checkpoint = str(checkpoint_path or '').strip()
    if not checkpoint:
        checkpoint = os.path.join('checkpoints', f'depth_anything_v2_{encoder}.pth')

    if not os.path.isfile(checkpoint):
        return None, f"checkpoint not found: {checkpoint}"

    device = _get_da2_device_name(prefer_cuda=prefer_cuda)
    cache_key = ('official', encoder, checkpoint, device)
    with _DA2_MODEL_LOCK:
        cached = _DA2_MODEL_CACHE.get(cache_key)
        if cached is not None:
            return cached, ""

        try:
            model = DepthAnythingV2(**_DA2_MODEL_CONFIGS[encoder])
            state_dict = torch.load(checkpoint, map_location='cpu')
            model.load_state_dict(state_dict)
            model = model.to(device).eval()
            _DA2_MODEL_CACHE[cache_key] = model
            return model, ""
        except Exception as e:
            return None, str(e)


def _load_da2_hf_pipeline(model_id, prefer_cuda=True):
    if not (HAS_TRANSFORMERS and HAS_PIL):
        return None, "transformers or PIL dependency missing"

    model_name = str(model_id or '').strip() or "depth-anything/Depth-Anything-V2-Small-hf"
    device_index = 0 if (prefer_cuda and HAS_TORCH and torch.cuda.is_available()) else -1
    cache_key = ('hf', model_name, device_index)
    with _DA2_MODEL_LOCK:
        cached = _DA2_MODEL_CACHE.get(cache_key)
        if cached is not None:
            return cached, ""

        try:
            pipe = hf_pipeline(task="depth-estimation", model=model_name, device=device_index)
            _DA2_MODEL_CACHE[cache_key] = pipe
            return pipe, ""
        except Exception as e:
            return None, str(e)


def _is_file_stable(path, wait_sec=0.02):
    """Check whether a file write has settled before upload."""
    try:
        size1 = os.path.getsize(path)
        time.sleep(wait_sec)
        size2 = os.path.getsize(path)
        return size1 > 0 and size1 == size2
    except OSError:
        return False


def _wrap_pi(a):
    while a > math.pi:
        a -= 2.0 * math.pi
    while a < -math.pi:
        a += 2.0 * math.pi
    return a


def _coerce_bool(value, default=False):
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in ['1', 'true', 'yes', 'on']
    return bool(value)


def _coerce_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def _coerce_int(value, default=0):
    try:
        return int(float(value))
    except Exception:
        return default


def _marker_size_cm_to_m(marker_size_cm):
    return max(0.0, _coerce_float(marker_size_cm, 0.0)) / 100.0


def _normalize_marker_image_points(corners):
    points = np.asarray(corners, dtype=np.float32)
    if points.ndim == 3:
        points = points[0]
    return points.reshape(-1, 2)


def _build_marker_object_points(marker_size_m):
    half = marker_size_m * 0.5
    return np.array([
        [-half, half, 0.0],
        [half, half, 0.0],
        [half, -half, 0.0],
        [-half, -half, 0.0],
    ], dtype=np.float32)


def _safe_json_dump(path, payload):
    if not path:
        return False

    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)

    with open(path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return True


def _serialize_marker_pose(rvec, tvec):
    return {
        'rvec': [round(float(v), 6) for v in np.asarray(rvec, dtype=np.float32).reshape(-1)],
        'tvec': [round(float(v), 6) for v in np.asarray(tvec, dtype=np.float32).reshape(-1)],
    }


def _has_go1_nodes():
    return any(n.type_str.startswith("GO1_") for n in node_registry.values())


def request_go1_special_action(action_name):
    key = str(action_name or '').strip().lower().replace('_', '')
    if key not in GO1_SPECIAL_ACTIONS:
        return False, f"Unknown action: {action_name}"

    go1_special_queue.append(key)
    go1_special_state['queue_size'] = len(go1_special_queue)
    go1_dashboard['special'] = f"Queued: {key}"
    write_log(f"[Go1 Special] queued: {key} (queue={len(go1_special_queue)})")
    return True, key


def get_go1_rtsp_url():
    return f"rtsp://{GO1_IP}:8554/live"


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def go1_estop_callback():
    go1_node_intent['stop'] = True
    go1_node_intent['vx'] = 0.0
    go1_node_intent['vy'] = 0.0
    go1_node_intent['wz'] = 0.0
    go1_target_vel['vx'] = 0.0
    go1_target_vel['vy'] = 0.0
    go1_target_vel['vyaw'] = 0.0
    write_log("Go1 EMERGENCY STOP Activated")


def _prompt_go1_ip(default_ip):
    print("\n" + "=" * 56)
    print("[System] Go1 IP 확인 (엔터 입력 시 기본값 사용)")
    print("=" * 56)
    current = default_ip
    while True:
        try:
            entered = input(f"Go1 IP 입력 [{current}]: ").strip()
        except EOFError:
            return current
        candidate = entered if entered else current
        try:
            socket.inet_aton(candidate)
        except OSError:
            print("[System] 잘못된 IP 형식입니다. 예: 192.168.50.42")
            continue
        try:
            confirm = input(f"현재 Go1 IP가 {candidate} 맞습니까? (y/n): ").strip().lower()
        except EOFError:
            return candidate
        if confirm in ["y", "yes", ""]:
            return candidate
        current = candidate


def init_go1_connection():
    global GO1_IP, _GO1_IP_INITIALIZED, _CAMERA_WORKER_STARTED, _SENDER_MANAGER_STARTED
    if _GO1_IP_INITIALIZED:
        return
    _GO1_IP_INITIALIZED = True

    try:
        use_ap_mode = input("Go1 AP 모드로 접속합니까? (y/n): ").strip().lower()
    except EOFError:
        use_ap_mode = "n"

    if use_ap_mode in ["y", "yes"]:
        GO1_IP = "192.168.123.161"
    else:
        GO1_IP = _prompt_go1_ip(GO1_IP)

    write_log(f"Go1 Target IP: {GO1_IP}")
    if HAS_UNITREE_SDK:
        write_log(f"Go1 SDK Ready: {sdk_path}")
    else:
        write_log(f"Go1 SDK Missing: {sdk_path} ({SDK_IMPORT_ERROR})")

    if not _CAMERA_WORKER_STARTED:
        _CAMERA_WORKER_STARTED = True
        threading.Thread(target=camera_worker_thread, daemon=True).start()

    if not _SENDER_MANAGER_STARTED and HAS_AIOHTTP:
        _SENDER_MANAGER_STARTED = True
        threading.Thread(target=sender_manager_thread, daemon=True).start()



def camera_worker_thread():
    global camera_state, CAMERA_CONFIG, _CAMERA_RECEIVER_PROC
    nanos = GO1_CAMERA_NANOS

    while True:
        if camera_command_queue:
            cmd_data = camera_command_queue.popleft()
            cmd = cmd_data[0]

            if cmd == 'START_CMD':
                _, pc_ip, target_folder, duration = cmd_data
                target_folder = str(target_folder).strip() or "Captured_Images/go1_front"
                camera_state['status'] = 'Starting...'
                camera_state['target_ip'] = pc_ip
                camera_state['duration'] = float(duration)

                CAMERA_CONFIG.clear()
                CAMERA_CONFIG.append({"folder": target_folder, "id": "go1_front"})

                write_log(f"[Cam START] Target PC: {pc_ip}, Folder: {target_folder}, Dur: {duration}s")

                for nano in nanos:
                    key_path = os.path.expanduser("~/.ssh/id_rsa")

                    kill_cmd = (
                        "bash -lc '"
                        "echo 123 | sudo -S fuser -k /dev/video0 /dev/video1 2>/dev/null ; "
                        "cd /home/unitree ; "
                        "./kill_camera.sh || true ; "
                        "pkill -f go1_send_both || true ; "
                        "pkill -f gst-launch-1.0 || true'"
                    )

                    start_cmd = (
                        f"bash -lc '"
                        f"cd /home/unitree ; "
                        f"nohup ./go1_send_both.sh {pc_ip} > send_both_py.log 2>&1 < /dev/null & "
                        f"sleep 1'"
                    )

                    base_ssh = [
                        "ssh", "-i", key_path,
                        "-o", "StrictHostKeyChecking=accept-new",
                        "-o", "ConnectTimeout=5",
                        "-J", f"pi@{GO1_IP}", nano
                    ]

                    try:
                        subprocess.run(base_ssh + [kill_cmd], capture_output=True, text=True, timeout=30)
                        subprocess.run(base_ssh + [start_cmd], capture_output=True, text=True, timeout=30)
                        write_log(f"[Cam START] SSH commands sent to {nano}")
                    except Exception as e:
                        write_log(f"[Cam START ERROR] SSH execution failed: {e}")

                # Stop previous local receiver process if still alive.
                try:
                    if _CAMERA_RECEIVER_PROC is not None and _CAMERA_RECEIVER_PROC.poll() is None:
                        _CAMERA_RECEIVER_PROC.terminate()
                        _CAMERA_RECEIVER_PROC.wait(timeout=2)
                except Exception:
                    try:
                        if _CAMERA_RECEIVER_PROC is not None and _CAMERA_RECEIVER_PROC.poll() is None:
                            _CAMERA_RECEIVER_PROC.kill()
                    except Exception:
                        pass
                finally:
                    _CAMERA_RECEIVER_PROC = None

                try:
                    subprocess.call("pkill -f 'gst-launch-1.0.*multifilesink'", shell=True)
                    subprocess.call("pkill -f 'gst-launch-1.0.*port=9400'", shell=True)
                except Exception:
                    pass
                time.sleep(0.5)

                try:
                    os.makedirs(target_folder, exist_ok=True)
                    gst_cmd = (
                        f"gst-launch-1.0 -q udpsrc port=9400 "
                        f"caps=\"application/x-rtp,media=video,encoding-name=JPEG,payload=26\" "
                        f"! rtpjpegdepay ! multifilesink location=\"{target_folder}/front_%06d.jpg\" sync=false"
                    )
                    _CAMERA_RECEIVER_PROC = subprocess.Popen(gst_cmd, shell=True)
                    write_log(f"[Cam START] Receiver listening on port 9400 -> {target_folder}")
                except Exception as e:
                    write_log(f"[Cam START ERROR] Failed to start receiver: {e}")

                time.sleep(1.0)
                camera_state['status'] = 'Running'
                camera_state['start_time'] = time.time()
                camera_state['timer_started_logged'] = False
                camera_state['last_interval_count'] = 0

            elif cmd == 'STOP':
                if camera_state['status'] == 'Running' and float(camera_state.get('duration', 0.0)) > 0.0:
                    write_log("[Cam Timer] 카메라 타이머 종료")
                camera_state['status'] = 'Stopping...'
                camera_state['duration'] = 0.0
                try:
                    if _CAMERA_RECEIVER_PROC is not None and _CAMERA_RECEIVER_PROC.poll() is None:
                        _CAMERA_RECEIVER_PROC.terminate()
                        _CAMERA_RECEIVER_PROC.wait(timeout=2)
                except Exception:
                    try:
                        if _CAMERA_RECEIVER_PROC is not None and _CAMERA_RECEIVER_PROC.poll() is None:
                            _CAMERA_RECEIVER_PROC.kill()
                    except Exception:
                        pass
                finally:
                    _CAMERA_RECEIVER_PROC = None
                try:
                    subprocess.call("pkill -f 'gst-launch-1.0.*multifilesink'", shell=True)
                    subprocess.call("pkill -f 'gst-launch-1.0.*port=9400'", shell=True)
                except Exception:
                    pass
                time.sleep(0.5)
                camera_state['status'] = 'Stopped'

        if camera_state['status'] == 'Running' and float(camera_state.get('duration', 0.0)) > 0.0:
            elapsed = time.time() - float(camera_state.get('start_time', 0.0))
            if not camera_state.get('timer_started_logged', False):
                write_log("[Cam Timer] 카메라 타이머 시작")
                camera_state['timer_started_logged'] = True

            interval_count = int(elapsed // 10)
            if interval_count > camera_state.get('last_interval_count', 0) and interval_count > 0:
                write_log(f"[Cam Timer] {interval_count * 10}초 경과")
                camera_state['last_interval_count'] = interval_count

            if elapsed >= float(camera_state.get('duration', 0.0)):
                write_log("[Cam Timer] 카메라 타이머 종료")
                camera_state['status'] = 'Stopping...'
                camera_state['duration'] = 0.0
                for node in node_registry.values():
                    if node.type_str == 'VIDEO_SRC' and hasattr(node, '_auto_stopped_by_timer'):
                        node._auto_stopped_by_timer = True
                    if node.type_str == 'VIS_SAVE':
                        if hasattr(node, '_save_start_time'):
                            node._save_start_time = None
                        if hasattr(node, '_timer_completed_this_run'):
                            node._timer_completed_this_run = True
                camera_command_queue.append(('STOP', camera_state.get('target_ip', '')))

        if camera_state['status'] == 'Running':
            elapsed = time.time() - float(camera_state.get('start_time', 0.0))
            interval_count = int(elapsed // 10)
            if interval_count > camera_state.get('last_interval_count', 0) and interval_count > 0:
                write_log(f"[Cam Running] {interval_count * 10}초 경과")
                camera_state['last_interval_count'] = interval_count

        time.sleep(0.1)


# ================= [Server Sender Functions] =================
async def send_image_async(session, filepath, camera_id, server_url):
    """HTTP multipart/form-data로 이미지 비동기 업로드"""
    try:
        if not os.path.exists(filepath):
            return
        with open(filepath, 'rb') as f:
            file_data = f.read()
        
        form = aiohttp.FormData()
        form.add_field('camera_id', camera_id)
        form.add_field('file', file_data, filename=f"{camera_id}_calib.jpg", content_type='image/jpeg')
        
        async with session.post(server_url, data=form, timeout=aiohttp.ClientTimeout(total=3.5)) as response:
            pass
    except Exception as e:
        write_log(f"[Server Sender] upload error: {e}")


async def camera_async_worker(config, server_url):
    """카메라 폴더 모니터링 및 이미지 송신"""
    global multi_sender_active
    
    folder = config["folder"]
    camera_id = config["id"]
    last_processed_file = None
    last_processed_idx = -1
    
    os.makedirs(folder, exist_ok=True)
    
    try:
        async with aiohttp.ClientSession() as session:
            while multi_sender_active:
                cycle_start = time.time()
                files = glob.glob(os.path.join(folder, "*.jpg"))
                
                if files:
                    best_file = None
                    best_idx = -1
                    for f in files:
                        idx = _extract_front_frame_index(f)
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
                            if latest_file != last_processed_file and _is_file_stable(latest_file):
                                last_processed_file = latest_file
                                await send_image_async(session, latest_file, camera_id, server_url)
                    else:
                        if best_idx > last_processed_idx and _is_file_stable(best_file):
                                await send_image_async(session, best_file, camera_id, server_url)
                                last_processed_idx = best_idx
                                last_processed_file = best_file
                
                await asyncio.sleep(max(0, INTERVAL - (time.time() - cycle_start)))
    except Exception as e:
        write_log(f"[Server Sender] worker error ({camera_id}): {e}")


def start_async_loop(config, server_url):
    """asyncio 이벤트루프 생성 및 실행"""
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(camera_async_worker(config, server_url))
    except Exception:
        pass


def sender_manager_thread():
    """송신 명령 처리 및 워커 스레드 관리"""
    global multi_sender_active, sender_state, CAMERA_CONFIG
    sender_threads = []
    
    while True:
        if sender_command_queue:
            cmd, url = sender_command_queue.popleft()
            
            if cmd == 'START' and not multi_sender_active:
                # 송신 원본 폴더는 VIS_SAVE 설정을 우선 사용 (보정/오버레이 결과 업로드)
                upload_folder = None
                try:
                    for node in node_registry.values():
                        if getattr(node, 'type_str', '') == 'VIS_SAVE':
                            upload_folder = str(node.state.get('folder', '')).strip()
                            if upload_folder:
                                break
                except Exception:
                    upload_folder = None

                if not upload_folder:
                    upload_folder = str(camera_save_state.get('folder', '')).strip() or 'Captured_Images/go1_saved'

                try:
                    CAMERA_CONFIG.clear()
                    CAMERA_CONFIG.append({"folder": upload_folder, "id": "go1_front"})
                except Exception:
                    pass

                multi_sender_active = True
                sender_state['status'] = 'Running'
                write_log(f"[Server Sender] 연결: {url} | folder={upload_folder}")
                
                for config in CAMERA_CONFIG:
                    s_thread = threading.Thread(
                        target=start_async_loop,
                        args=(config, url),
                        daemon=True
                    )
                    s_thread.start()
                    sender_threads.append(s_thread)
            
            elif cmd == 'STOP' and multi_sender_active:
                multi_sender_active = False
                sender_state['status'] = 'Stopped'
                write_log("[Server Sender] 연결 해제")
                sender_threads.clear()
        
        time.sleep(0.1)


def go1_keepalive_thread():
    global GO1_UNITY_IP

    if HAS_UNITREE_SDK:
        try:
            udp = sdk.UDP(HIGHLEVEL, LOCAL_PORT, GO1_IP, GO1_PORT)
            cmd = sdk.HighCmd()
            state = sdk.HighState()
            udp.InitCmdData(cmd)
            go1_dashboard["hw_link"] = "Connecting..."
        except Exception:
            udp = None
            cmd = None
            state = None
            go1_dashboard["hw_link"] = "Simulation"
    else:
        udp = None
        cmd = None
        state = None
        go1_dashboard["hw_link"] = "Simulation"

    sock_tx_state = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock_tx_cmd = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    sock_rx_unity = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock_rx_unity.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock_rx_unity.bind(("0.0.0.0", UNITY_RX_PORT))
        sock_rx_unity.setblocking(False)
    except Exception:
        pass

    stand_only = True
    now = time.monotonic()
    last_key_time = now
    last_move_cmd_time = now
    grace_deadline = now
    use_grace = True
    last_unity_cmd_time = now

    yaw0_initialized = False
    yaw0 = 0.0
    unity_yaw_offset_rad = math.pi / 2.0

    world_x = 0.0
    world_z = 0.0
    last_dr_time = now
    seq = 0

    yaw_align_active = False
    yaw_align_target_rel = 0.0
    yaw_align_kp = 2.0
    yaw_align_tol_rad = 2.0 * math.pi / 180.0

    last_go1_recv_time = now

    special_runtime = {
        'active': False,
        'name': '',
        'mode': 0,
        'phase': 'idle',
        'phase_until': 0.0,
        'wait_timeout': 0.0,
        'recovery': 'stand',
        'wait_started_at': 0.0,
        'wait_mode_seen': False,
    }

    def reset_cmd_base():
        if not cmd:
            return
        cmd.mode = 0
        cmd.gaitType = 0
        cmd.speedLevel = 0
        cmd.footRaiseHeight = 0.08
        cmd.bodyHeight = _clamp(go1_node_intent.get('body_height', 0.0), BODY_HEIGHT_MIN, BODY_HEIGHT_MAX)
        cmd.euler = [0.0, 0.0, 0.0]
        cmd.velocity = [0.0, 0.0]
        cmd.yawSpeed = 0.0
        cmd.reserve = 0

    next_t = time.monotonic()

    while True:
        tnow = time.monotonic()
        if tnow < next_t:
            time.sleep(max(0.0, next_t - tnow))
        next_t += DT

        raw_yaw = 0.0
        if udp:
            try:
                udp.Recv()
                udp.GetRecv(state)
                raw_yaw = float(state.imu.rpy[2])

                # AP/STA 환경 차이로 상태값 변화가 작더라도 수신 성공 자체를 연결 유지로 본다.
                last_go1_recv_time = tnow

                if (tnow - last_go1_recv_time) < 1.0:
                    go1_in_use = bool(engine_module.is_running) and (_has_go1_nodes() or special_runtime['active'])
                    go1_dashboard["hw_link"] = "Online (Active)" if go1_in_use else "Online (Listen)"
                    try:
                        if hasattr(state.bms, 'SOC'):
                            go1_state['battery'] = int(state.bms.SOC)
                        elif hasattr(state.bms, 'soc'):
                            go1_state['battery'] = int(state.bms.soc)
                    except Exception:
                        pass
                else:
                    go1_dashboard["hw_link"] = "Offline"
                    go1_state['battery'] = -1
            except Exception:
                go1_dashboard["hw_link"] = "Offline"
                go1_state['battery'] = -1

        if not yaw0_initialized:
            yaw0 = raw_yaw
            yaw0_initialized = True
            last_dr_time = time.monotonic()

        if go1_node_intent['reset_yaw']:
            yaw0 = raw_yaw
            last_dr_time = time.monotonic()
            go1_node_intent['reset_yaw'] = False
            write_log("Go1 YAW0 Reset")

        yaw_rel = _wrap_pi(raw_yaw - yaw0)
        yaw_unity = _wrap_pi(yaw_rel + unity_yaw_offset_rad)
        go1_state['yaw_unity'] = yaw_unity

        is_node_active = (tnow - go1_node_intent['trigger_time']) < 0.1

        if go1_node_intent['yaw_align']:
            yaw_align_active = True
            stand_only = False
            last_key_time = tnow
            last_move_cmd_time = tnow
            grace_deadline = tnow
            use_grace = True
            go1_node_intent['yaw_align'] = False

        if go1_node_intent['stop']:
            yaw_align_active = False
            stand_only = True
            last_key_time = tnow
            last_move_cmd_time = tnow
            grace_deadline = tnow
            use_grace = True
            go1_node_intent['stop'] = False
        elif is_node_active:
            yaw_align_active = False
            stand_only = False
            last_key_time = tnow
            grace_deadline = tnow + repeat_grace_sec
            if abs(go1_node_intent['vx']) > 0 or abs(go1_node_intent['vy']) > 0 or abs(go1_node_intent['wz']) > 0:
                last_move_cmd_time = tnow

        got = None
        while True:
            try:
                data, _ = sock_rx_unity.recvfrom(256)
                parts = data.decode("utf-8", errors="ignore").strip().split()
                if len(parts) >= 4:
                    got = (float(parts[0]), float(parts[1]), float(parts[2]), int(parts[3]))
            except Exception:
                break

        if got:
            last_unity_cmd_time = tnow
            go1_dashboard['unity_link'] = "Active"
            go1_unity_data['vx'], go1_unity_data['vy'], go1_unity_data['wz'], go1_unity_data['estop'] = got

        unity_active = go1_node_intent['use_unity_cmd'] and ((tnow - last_unity_cmd_time) <= unity_timeout_sec)
        go1_unity_data['active'] = unity_active
        if not unity_active:
            go1_dashboard['unity_link'] = "Waiting"

        since_key = tnow - last_key_time
        since_move = tnow - last_move_cmd_time
        active_walk = (
            ((not stand_only) and (since_key <= hold_timeout_sec))
            or ((not stand_only) and use_grace and (tnow <= grace_deadline))
            or ((not stand_only) and (since_move <= min_move_sec))
        )

        # Dashboard/노드에서 요청한 특수동작 큐 시작.
        if (not special_runtime['active']) and go1_special_queue:
            next_name = go1_special_queue.popleft()
            cfg = GO1_SPECIAL_ACTIONS.get(next_name)
            if cfg and cmd:
                special_runtime['active'] = True
                special_runtime['name'] = next_name
                special_runtime['mode'] = int(cfg['mode'])
                special_runtime['phase'] = 'prep_stand'
                special_runtime['phase_until'] = tnow + 1.5
                special_runtime['trigger_sec'] = float(cfg.get('trigger_sec', 0.2))
                special_runtime['wait_timeout'] = float(cfg['wait_timeout'])
                special_runtime['recovery'] = str(cfg['recovery'])
                special_runtime['wait_started_at'] = 0.0
                special_runtime['wait_mode_seen'] = False
                go1_node_intent['stop'] = True
                go1_dashboard['special'] = f"Running: {next_name}"
                write_log(f"[Go1 Special] start: {next_name}")
            elif cfg and not cmd:
                go1_dashboard['special'] = "Skipped: SDK unavailable"
                write_log(f"[Go1 Special] skipped(no SDK): {next_name}")

        go1_special_state['active'] = bool(special_runtime['active'])
        go1_special_state['name'] = special_runtime['name']
        go1_special_state['mode'] = special_runtime['mode']
        go1_special_state['phase'] = special_runtime['phase']
        go1_special_state['queue_size'] = len(go1_special_queue)

        reset_cmd_base()
        target_mode = 1
        out_vx = 0.0
        out_vy = 0.0
        out_wz = 0.0

        if yaw_align_active:
            err = _wrap_pi(yaw_rel - yaw_align_target_rel)
            if abs(err) <= yaw_align_tol_rad:
                yaw_align_active = False
                target_mode = 1
            else:
                target_mode = 2
                out_wz = _clamp(-yaw_align_kp * err, -W_MAX, W_MAX)
            if target_mode == 2 and cmd:
                cmd.gaitType = 1
        elif unity_active:
            target_mode = 2 if not go1_unity_data['estop'] else 1
            if cmd:
                cmd.gaitType = 1
            out_vx = _clamp(go1_unity_data['vx'], -V_MAX, V_MAX)
            out_vy = _clamp(go1_unity_data['vy'], -S_MAX, S_MAX)
            out_wz = _clamp(go1_unity_data['wz'], -W_MAX, W_MAX)
            go1_state['reason'] = "UNITY"
        elif active_walk:
            target_mode = 2
            if cmd:
                cmd.gaitType = 1
            out_vx = _clamp(go1_node_intent['vx'], -V_MAX, V_MAX)
            out_vy = _clamp(go1_node_intent['vy'], -S_MAX, S_MAX)
            out_wz = _clamp(go1_node_intent['wz'], -W_MAX, W_MAX)
            go1_state['reason'] = "NODE_WALK"
        else:
            if since_move <= (min_move_sec + stop_brake_sec):
                target_mode = 2
                go1_state['reason'] = "BRAKE"
                if cmd:
                    cmd.gaitType = 1
            else:
                target_mode = 1
                use_grace = True
                go1_state['reason'] = "STAND"

        if special_runtime['active']:
            phase = special_runtime['phase']

            if phase == 'prep_stand':
                target_mode = 1
                go1_state['reason'] = "SPECIAL_PREP"
                if tnow >= special_runtime['phase_until']:
                    special_runtime['phase'] = 'trigger'
                    special_runtime['phase_until'] = tnow + special_runtime.get('trigger_sec', 0.2)

            elif phase == 'trigger':
                target_mode = special_runtime['mode']
                go1_state['reason'] = f"SPECIAL_TRIG_{special_runtime['mode']}"
                if tnow >= special_runtime['phase_until']:
                    special_runtime['phase'] = 'wait_done'
                    special_runtime['wait_started_at'] = tnow

            elif phase == 'wait_done':
                # C++ 테스트 코드와 동일하게 트리거 후에는 mode를 계속 밀지 않고 완료를 대기한다.
                target_mode = 1
                go1_state['reason'] = f"SPECIAL_WAIT_{special_runtime['mode']}"
                hw_mode = int(getattr(state, 'mode', special_runtime['mode'])) if state is not None else int(go1_state.get('mode', special_runtime['mode']))
                if hw_mode == special_runtime['mode']:
                    special_runtime['wait_mode_seen'] = True

                elapsed = tnow - special_runtime['wait_started_at']
                done = special_runtime['wait_mode_seen'] and hw_mode != special_runtime['mode']
                timeout = elapsed >= special_runtime['wait_timeout']
                if done or timeout:
                    special_runtime['phase'] = 'post_wait'
                    special_runtime['phase_until'] = tnow + 0.3
                    if timeout:
                        write_log(f"[Go1 Special] timeout: {special_runtime['name']}")

            elif phase == 'post_wait':
                target_mode = 1
                go1_state['reason'] = "SPECIAL_POST_WAIT"
                if tnow >= special_runtime['phase_until']:
                    if special_runtime['recovery'] == 'stand':
                        special_runtime['phase'] = 'recover8'
                        special_runtime['phase_until'] = tnow + 1.5
                    else:
                        special_runtime['phase'] = 'recover0'
                        special_runtime['phase_until'] = tnow + 0.5

            elif phase == 'recover8':
                target_mode = 8
                go1_state['reason'] = "SPECIAL_RECOVER8"
                if tnow >= special_runtime['phase_until']:
                    special_runtime['phase'] = 'recover1'
                    special_runtime['phase_until'] = tnow + 1.5

            elif phase == 'recover1':
                target_mode = 1
                go1_state['reason'] = "SPECIAL_RECOVER1"
                if tnow >= special_runtime['phase_until']:
                    finished = special_runtime['name']
                    special_runtime['active'] = False
                    special_runtime['name'] = ''
                    special_runtime['mode'] = 0
                    special_runtime['phase'] = 'idle'
                    go1_dashboard['special'] = "Idle"
                    write_log(f"[Go1 Special] done: {finished}")

            elif phase == 'recover0':
                target_mode = 0
                go1_state['reason'] = "SPECIAL_RECOVER0"
                if tnow >= special_runtime['phase_until']:
                    finished = special_runtime['name']
                    special_runtime['active'] = False
                    special_runtime['name'] = ''
                    special_runtime['mode'] = 0
                    special_runtime['phase'] = 'idle'
                    go1_dashboard['special'] = "Idle"
                    write_log(f"[Go1 Special] done: {finished}")

            out_vx = 0.0
            out_vy = 0.0
            out_wz = 0.0
            if cmd:
                cmd.gaitType = 0
                cmd.speedLevel = 0
                cmd.footRaiseHeight = 0.0
                cmd.bodyHeight = 0.0
                cmd.euler = [0.0, 0.0, 0.0]
                cmd.velocity = [0.0, 0.0]
                cmd.yawSpeed = 0.0
                cmd.reserve = 0

        go1_in_use = bool(engine_module.is_running) and (_has_go1_nodes() or special_runtime['active'])

        suppress_send = bool(special_runtime['active'] and special_runtime['phase'] == 'wait_done')

        if cmd:
            cmd.mode = target_mode
            cmd.velocity = [out_vx, out_vy]
            cmd.yawSpeed = out_wz
            if go1_in_use and not suppress_send:
                try:
                    udp.SetSend(cmd)
                    udp.Send()
                except Exception:
                    pass

        if not cmd and go1_in_use:
            msg = f"cmd_vel {out_vx:.3f} {out_vy:.3f} {out_wz:.3f} {_clamp(go1_node_intent.get('body_height', 0.0), BODY_HEIGHT_MIN, BODY_HEIGHT_MAX):.3f}"
            try:
                go1_sock.sendto(msg.encode('utf-8'), (GO1_IP, GO1_PORT))
            except Exception:
                pass

        if unity_active and (abs(out_vx) > 1e-4 or abs(out_vy) > 1e-4 or abs(out_wz) > 1e-4):
            go1_state['control_latency_ms'] = max(0.0, (tnow - last_unity_cmd_time) * 1000.0)
        elif target_mode == 2 and (abs(out_vx) > 1e-4 or abs(out_vy) > 1e-4 or abs(out_wz) > 1e-4):
            go1_state['control_latency_ms'] = max(0.0, (tnow - go1_node_intent.get('trigger_time', tnow)) * 1000.0)
        else:
            go1_state['control_latency_ms'] = 0.0

        go1_state['vx_cmd'] = out_vx
        go1_state['vy_cmd'] = out_vy
        go1_state['wz_cmd'] = out_wz
        go1_state['mode'] = target_mode
        go1_state['body_height_cmd'] = _clamp(go1_node_intent.get('body_height', 0.0), BODY_HEIGHT_MIN, BODY_HEIGHT_MAX)

        dts = tnow - last_dr_time
        last_dr_time = tnow
        cy = math.cos(yaw_unity)
        sy = math.sin(yaw_unity)
        world_x += (out_vx * cy - out_vy * sy) * dts
        world_z += (out_vx * sy + out_vy * cy) * dts

        go1_state['world_x'] = world_x
        go1_state['world_z'] = world_z

        go1_target_vel['vx'] = out_vx
        go1_target_vel['vy'] = out_vy
        go1_target_vel['vyaw'] = out_wz
        go1_target_vel['body_height'] = go1_state['body_height_cmd']

        if special_runtime['active']:
            go1_dashboard['status'] = f"Special ({special_runtime['name']})"
        else:
            go1_dashboard['status'] = "Running" if (go1_in_use and target_mode == 2) else "Idle"

        estop = 1 if target_mode == 1 else 0
        seq += 1
        msg_state = (
            f"{seq} {time.time() * 1000.0:.1f} {world_x:.6f} {world_z:.6f} {yaw_unity:.6f} "
            f"{out_vx:.3f} {out_vy:.3f} {out_wz:.3f} {estop} {target_mode}"
        )
        msg_cmd = f"{out_vx:.3f} {out_vy:.3f} {out_wz:.3f} {estop}"

        try:
            sock_tx_state.sendto(msg_state.encode("utf-8"), (GO1_UNITY_IP, UNITY_STATE_PORT))
            sock_tx_cmd.sendto(msg_cmd.encode("utf-8"), (GO1_UNITY_IP, UNITY_CMD_PORT))
        except Exception:
            pass


# ================= [Go1 Driver/Control Nodes] =================
class Go1RobotDriver(BaseRobotDriver):
    def get_ui_schema(self):
        return [
            ('vx', "Vx In", 0.0),
            ('vy', "Vy In", 0.0),
            ('vyaw', "Wz In", 0.0),
            ('body_height', "Body H", 0.0),
        ]

    def get_settings_schema(self):
        return []

    def execute_command(self, inputs, settings):
        if inputs.get('vx') is not None:
            go1_node_intent['vx'] = float(inputs['vx'])
        if inputs.get('vy') is not None:
            go1_node_intent['vy'] = float(inputs['vy'])
        if inputs.get('vyaw') is not None:
            go1_node_intent['wz'] = float(inputs['vyaw'])
        if inputs.get('body_height') is not None:
            go1_node_intent['body_height'] = _clamp(float(inputs['body_height']), BODY_HEIGHT_MIN, BODY_HEIGHT_MAX)

        if any(inputs.get(k) is not None for k in ['vx', 'vy', 'vyaw']):
            go1_node_intent['trigger_time'] = time.monotonic()

        return {
            'vx': go1_state['vx_cmd'],
            'vy': go1_state['vy_cmd'],
            'vyaw': go1_state['wz_cmd'],
            'body_height': go1_state.get('body_height_cmd', 0.0),
        }


class Go1ActionNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "Go1 Action", "GO1_ACTION")
        self.in_flow = generate_uuid()
        self.inputs[self.in_flow] = PortType.FLOW
        self.in_val1 = generate_uuid()
        self.inputs[self.in_val1] = PortType.DATA
        self.out_flow = generate_uuid()
        self.outputs[self.out_flow] = PortType.FLOW
        self.state['mode'] = "Stand"
        self.state['v1'] = 0.2

    def execute(self):
        mode = self.state.get('mode', 'Stand')
        v1 = self.fetch_input_data(self.in_val1)
        v1 = float(v1) if v1 is not None else float(self.state.get('v1', 0.2))

        if mode == "Stand":
            go1_node_intent['stop'] = True
        elif mode == "Reset Yaw0":
            go1_node_intent['reset_yaw'] = True
        elif mode == "Sit Down":
            go1_node_intent['body_height'] = BODY_HEIGHT_MIN
        elif mode == "Stand Tall":
            go1_node_intent['body_height'] = BODY_HEIGHT_MAX
        elif mode == "Set Body Height":
            go1_node_intent['body_height'] = _clamp(v1, BODY_HEIGHT_MIN, BODY_HEIGHT_MAX)
        elif mode == "Backflip":
            request_go1_special_action('backflip')
        elif mode == "Jump Yaw":
            request_go1_special_action('jumpyaw')
        elif mode == "Straight Hand":
            request_go1_special_action('straighthand')
        elif mode == "Dance 1":
            request_go1_special_action('dance1')
        elif mode == "Dance 2":
            request_go1_special_action('dance2')
        else:
            go1_node_intent['vx'] = v1 if mode == "Walk Fwd/Back" else 0.0
            go1_node_intent['vy'] = v1 if mode == "Walk Strafe" else 0.0
            go1_node_intent['wz'] = v1 if mode == "Turn" else 0.0
            go1_node_intent['trigger_time'] = time.monotonic()

        return self.out_flow


class Go1KeyboardNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "Keyboard (Go1)", "GO1_KEYBOARD")
        self.in_flow = generate_uuid()
        self.inputs[self.in_flow] = PortType.FLOW
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

    def execute(self):
        if self.state.get('is_focused', False):
            return self.out_flow

        vx = 0.0
        vy = 0.0
        wz = 0.0

        key_mode = self.state.get('keys', 'WASD')
        if key_mode == 'WASD':
            if self.state.get('W'):
                vx = VX_CMD
            if self.state.get('S'):
                vx = -VX_CMD
            if self.state.get('A'):
                vy = VY_CMD
            if self.state.get('D'):
                vy = -VY_CMD
        else:
            if self.state.get('UP'):
                vx = VX_CMD
            if self.state.get('DOWN'):
                vx = -VX_CMD
            if self.state.get('LEFT'):
                vy = VY_CMD
            if self.state.get('RIGHT'):
                vy = -VY_CMD

        if self.state.get('Q'):
            wz = WZ_CMD
        if self.state.get('E'):
            wz = -WZ_CMD

        if self.state.get('Z'):
            go1_node_intent['body_height'] = _clamp(go1_node_intent.get('body_height', 0.0) + BODY_HEIGHT_KEY_STEP, BODY_HEIGHT_MIN, BODY_HEIGHT_MAX)
        if self.state.get('X'):
            go1_node_intent['body_height'] = _clamp(go1_node_intent.get('body_height', 0.0) - BODY_HEIGHT_KEY_STEP, BODY_HEIGHT_MIN, BODY_HEIGHT_MAX)

        if self.state.get('SPACE'):
            go1_node_intent['stop'] = True
        if self.state.get('R_pressed'):
            go1_node_intent['yaw_align'] = True
        if self.state.get('C_pressed'):
            go1_node_intent['reset_yaw'] = True

        if vx or vy or wz:
            go1_node_intent['vx'] = vx
            go1_node_intent['vy'] = vy
            go1_node_intent['wz'] = wz
            go1_node_intent['trigger_time'] = time.monotonic()

        self.output_data[self.out_vx] = vx
        self.output_data[self.out_vy] = vy
        self.output_data[self.out_vyaw] = wz
        self.output_data[self.out_body_height] = go1_node_intent.get('body_height', 0.0)
        return self.out_flow


class Go1UnityNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "Unity Logic (Go1)", "GO1_UNITY")
        self.in_flow = generate_uuid()
        self.inputs[self.in_flow] = PortType.FLOW
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
        self.out_active = generate_uuid()
        self.outputs[self.out_active] = PortType.DATA
        self.out_flow = generate_uuid()
        self.outputs[self.out_flow] = PortType.FLOW

        self.state['unity_ip'] = GO1_UNITY_IP
        self.state['enable_teleop_rx'] = True
        self.state['send_aruco'] = False
        self.last_processed_json = ""

    def execute(self):
        global GO1_UNITY_IP

        GO1_UNITY_IP = self.state.get('unity_ip', GO1_UNITY_IP)
        go1_node_intent['use_unity_cmd'] = bool(self.state.get('enable_teleop_rx', True))
        go1_node_intent['send_aruco'] = bool(self.state.get('send_aruco', False))
        aruco_settings['enabled'] = bool(self.state.get('send_aruco', False))

        raw_json = self.fetch_input_data(self.data_in_id)
        if raw_json and raw_json != self.last_processed_json:
            self.last_processed_json = raw_json
            try:
                payload = json.loads(raw_json)
                go1_unity_data['vx'] = float(payload.get('vx', go1_unity_data['vx']))
                go1_unity_data['vy'] = float(payload.get('vy', go1_unity_data['vy']))
                go1_unity_data['wz'] = float(payload.get('wz', go1_unity_data['wz']))
                go1_unity_data['estop'] = int(payload.get('estop', go1_unity_data['estop']))
            except Exception as e:
                write_log(f"Go1 Unity JSON Error: {e}")

        self.output_data[self.out_vx] = go1_unity_data['vx']
        self.output_data[self.out_vy] = go1_unity_data['vy']
        self.output_data[self.out_vyaw] = go1_unity_data['wz']
        self.output_data[self.out_body_height] = go1_state.get('body_height_cmd', 0.0)
        self.output_data[self.out_active] = go1_unity_data.get('active', False)
        return self.out_flow


class Go1ServerJsonRecvNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "Server JSON Receiver", "GO1_SERVER_JSON_RECV")
        self.in_flow = generate_uuid()
        self.inputs[self.in_flow] = PortType.FLOW
        self.out_flow = generate_uuid()
        self.outputs[self.out_flow] = PortType.FLOW

        self.out_raw_json = generate_uuid()
        self.outputs[self.out_raw_json] = PortType.DATA
        self.out_seq = generate_uuid()
        self.outputs[self.out_seq] = PortType.DATA
        self.out_ts = generate_uuid()
        self.outputs[self.out_ts] = PortType.DATA
        self.out_vx = generate_uuid()
        self.outputs[self.out_vx] = PortType.DATA
        self.out_vy = generate_uuid()
        self.outputs[self.out_vy] = PortType.DATA
        self.out_wz = generate_uuid()
        self.outputs[self.out_wz] = PortType.DATA
        self.out_stop = generate_uuid()
        self.outputs[self.out_stop] = PortType.DATA
        self.out_confidence = generate_uuid()
        self.outputs[self.out_confidence] = PortType.DATA
        self.out_connected = generate_uuid()
        self.outputs[self.out_connected] = PortType.DATA
        self.out_fresh = generate_uuid()
        self.outputs[self.out_fresh] = PortType.DATA
        self.out_status = generate_uuid()
        self.outputs[self.out_status] = PortType.DATA

        self.state['mode'] = 'HTTP'
        self.state['source'] = 'http://127.0.0.1:5001/cmd'
        self.state['poll_interval_sec'] = 0.05
        self.state['request_timeout_sec'] = 2.0
        self.state['fresh_timeout_sec'] = 0.2
        self.state['move_speed'] = 0.2
        self.state['move_duration_sec'] = 0.5

        self._last_poll_mono = 0.0
        self._last_ok_mono = 0.0
        self._last_raw_json = ''
        self._last_payload = {}
        self._last_seq = 0
        self._last_error = ''
        self._motion_active = False
        self._motion_until_mono = 0.0
        self._motion_vx = 0.0
        self._motion_vy = 0.0
        self._motion_wz = 0.0
        self._last_motion_trigger_key = ''
        self._last_logged_raw = ''
        self._last_logged_error = ''

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
            headers={
                'Accept': 'application/json',
                'Cache-Control': 'no-cache',
                'Pragma': 'no-cache',
            },
            method='GET',
        )
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            charset = resp.headers.get_content_charset() or 'utf-8'
            return resp.read().decode(charset, errors='replace')

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

    def _extract_direction_text(self, payload):
        allowed = {'left', 'right', 'front', 'back', 'stop'}

        if isinstance(payload, str):
            text = payload.strip().lower()
            return text if text in allowed else ''

        if isinstance(payload, list) and payload:
            last_item = payload[-1]
            if isinstance(last_item, str):
                text = last_item.strip().lower()
                return text if text in allowed else ''

        if isinstance(payload, dict):
            for key in ('cmd', 'command', 'direction', 'action'):
                value = payload.get(key)
                if isinstance(value, str):
                    text = value.strip().lower()
                    if text in allowed:
                        return text
            for key in ('left', 'right', 'front', 'back', 'stop'):
                if _coerce_bool(payload.get(key, False), False):
                    return key

        return ''

    def _inject_direction_motion(self, direction, move_speed, move_duration_sec, trigger_key):
        if not direction:
            return
        if trigger_key == self._last_motion_trigger_key:
            return

        self._last_motion_trigger_key = trigger_key

        if direction == 'stop':
            go1_node_intent['vx'] = 0.0
            go1_node_intent['vy'] = 0.0
            go1_node_intent['wz'] = 0.0
            go1_node_intent['stop'] = True
            go1_node_intent['trigger_time'] = time.monotonic()
            self._motion_active = False
            self._motion_until_mono = 0.0
            self._motion_vx = 0.0
            self._motion_vy = 0.0
            self._motion_wz = 0.0
            write_log("[GO1 JSON RX] command=stop -> immediate stop")
            return

        vx = 0.0
        vy = 0.0
        wz = 0.0
        if direction == 'front':
            vx = move_speed
        elif direction == 'back':
            vx = -move_speed
        elif direction == 'left':
            vy = move_speed
        elif direction == 'right':
            vy = -move_speed

        go1_node_intent['vx'] = _clamp(vx, -V_MAX, V_MAX)
        go1_node_intent['vy'] = _clamp(vy, -S_MAX, S_MAX)
        go1_node_intent['wz'] = _clamp(wz, -W_MAX, W_MAX)
        go1_node_intent['stop'] = False
        go1_node_intent['trigger_time'] = time.monotonic()

        self._motion_active = True
        self._motion_until_mono = time.monotonic() + max(0.05, move_duration_sec)
        self._motion_vx = go1_node_intent['vx']
        self._motion_vy = go1_node_intent['vy']
        self._motion_wz = go1_node_intent['wz']
        write_log(
            f"[GO1 JSON RX] command={direction} -> move vx={go1_node_intent['vx']:.3f}, "
            f"vy={go1_node_intent['vy']:.3f}, wz={go1_node_intent['wz']:.3f}, duration={move_duration_sec:.2f}s"
        )

    def _publish_state(self, raw_json, payload, connected, fresh, status, source):
        seq = _coerce_int(payload.get('seq', self._last_seq), self._last_seq)
        ts = _coerce_float(payload.get('ts', payload.get('timestamp', time.time())), time.time())
        vx_raw = _coerce_float(payload.get('vx', 0.0), 0.0)
        vy_raw = _coerce_float(payload.get('vy', 0.0), 0.0)
        wz_raw = _coerce_float(payload.get('wz', payload.get('yaw', 0.0)), 0.0)

        stop_raw = payload.get('stop', payload.get('estop', False))
        stop = _coerce_bool(stop_raw, False)
        if 'estop' in payload:
            stop = stop or _coerce_bool(payload.get('estop', False), False)

        confidence = _clamp(_coerce_float(payload.get('confidence', 1.0), 1.0), 0.0, 1.0)
        vx = 0.0 if stop else _clamp(vx_raw, -V_MAX, V_MAX)
        vy = 0.0 if stop else _clamp(vy_raw, -S_MAX, S_MAX)
        wz = 0.0 if stop else _clamp(wz_raw, -W_MAX, W_MAX)

        self._last_seq = seq
        self._last_raw_json = raw_json
        self._last_payload = dict(payload)
        if connected and fresh:
            self._last_ok_mono = time.monotonic()

        go1_server_json_data.update({
            'raw_json': raw_json,
            'seq': seq,
            'ts': ts,
            'vx': vx,
            'vy': vy,
            'wz': wz,
            'stop': stop,
            'confidence': confidence,
            'connected': bool(connected),
            'fresh': bool(fresh),
            'status': status,
            'source': source,
        })

        self.output_data[self.out_raw_json] = raw_json
        self.output_data[self.out_seq] = seq
        self.output_data[self.out_ts] = ts
        self.output_data[self.out_vx] = vx
        self.output_data[self.out_vy] = vy
        self.output_data[self.out_wz] = wz
        self.output_data[self.out_stop] = stop
        self.output_data[self.out_confidence] = confidence
        self.output_data[self.out_connected] = bool(connected)
        self.output_data[self.out_fresh] = bool(fresh)
        self.output_data[self.out_status] = status

    def execute(self):
        mode = str(self.state.get('mode', 'HTTP')).strip().upper()
        source = str(self.state.get('source', '')).strip()
        poll_interval_sec = max(0.0, _coerce_float(self.state.get('poll_interval_sec', 0.05), 0.05))
        request_timeout_sec = max(0.2, _coerce_float(self.state.get('request_timeout_sec', 2.0), 2.0))
        fresh_timeout_sec = max(0.05, _coerce_float(self.state.get('fresh_timeout_sec', 0.2), 0.2))
        move_speed = max(0.01, _coerce_float(self.state.get('move_speed', 0.2), 0.2))
        move_duration_sec = max(0.05, _coerce_float(self.state.get('move_duration_sec', 0.5), 0.5))

        now_mono = time.monotonic()

        if self._motion_active:
            if now_mono < self._motion_until_mono:
                go1_node_intent['vx'] = self._motion_vx
                go1_node_intent['vy'] = self._motion_vy
                go1_node_intent['wz'] = self._motion_wz
                go1_node_intent['stop'] = False
                go1_node_intent['trigger_time'] = now_mono
            else:
                go1_node_intent['vx'] = 0.0
                go1_node_intent['vy'] = 0.0
                go1_node_intent['wz'] = 0.0
                go1_node_intent['stop'] = True
                go1_node_intent['trigger_time'] = now_mono
                self._motion_active = False
                self._motion_until_mono = 0.0
                self._motion_vx = 0.0
                self._motion_vy = 0.0
                self._motion_wz = 0.0
                write_log("[GO1 JSON RX] timed motion finished -> stop")

        should_poll = (now_mono - self._last_poll_mono) >= poll_interval_sec or not self._last_raw_json

        if should_poll:
            self._last_poll_mono = now_mono
            try:
                raw_json = self._read_source_text(mode, source, request_timeout_sec)
                parsed = json.loads(raw_json)
                direction = self._extract_direction_text(parsed)
                payload = self._pick_payload(parsed)
                if not isinstance(payload, dict):
                    payload = {}

                if direction:
                    if direction == 'front':
                        payload['vx'] = move_speed
                        payload['vy'] = 0.0
                        payload['wz'] = 0.0
                        payload['stop'] = False
                    elif direction == 'back':
                        payload['vx'] = -move_speed
                        payload['vy'] = 0.0
                        payload['wz'] = 0.0
                        payload['stop'] = False
                    elif direction == 'left':
                        payload['vx'] = 0.0
                        payload['vy'] = move_speed
                        payload['wz'] = 0.0
                        payload['stop'] = False
                    elif direction == 'right':
                        payload['vx'] = 0.0
                        payload['vy'] = -move_speed
                        payload['wz'] = 0.0
                        payload['stop'] = False
                    elif direction == 'stop':
                        payload['vx'] = 0.0
                        payload['vy'] = 0.0
                        payload['wz'] = 0.0
                        payload['stop'] = True

                if 'ts' not in payload and 'timestamp' not in payload:
                    payload['ts'] = time.time()

                self._last_error = ''
                self._publish_state(raw_json, payload, True, True, 'OK', source)

                raw_for_log = raw_json.strip()
                if raw_for_log != self._last_logged_raw:
                    if direction:
                        write_log(f"[GO1 JSON RX] read ok | source={source} | direction={direction} | raw={raw_for_log}")
                    else:
                        write_log(f"[GO1 JSON RX] read ok but no direction token | source={source} | raw={raw_for_log}")
                    self._last_logged_raw = raw_for_log
                self._last_logged_error = ''

                if direction:
                    trigger_key = raw_json.strip()
                    self._inject_direction_motion(direction, move_speed, move_duration_sec, trigger_key)
            except Exception as e:
                self._last_error = str(e)
                fresh = (now_mono - self._last_ok_mono) <= fresh_timeout_sec if self._last_ok_mono else False
                status = f'ERR: {e.__class__.__name__}'
                self._publish_state(self._last_raw_json, self._last_payload, False, fresh, status, source)
                if self._last_error != self._last_logged_error:
                    write_log(f"[GO1 JSON RX] read error | source={source} | {e.__class__.__name__}: {self._last_error}")
                    self._last_logged_error = self._last_error
        else:
            fresh = (now_mono - self._last_ok_mono) <= fresh_timeout_sec if self._last_ok_mono else False
            status = go1_server_json_data.get('status', 'Idle')
            if not fresh and status == 'OK':
                status = 'STALE'
            self._publish_state(self._last_raw_json, self._last_payload, bool(self._last_raw_json), fresh, status, source)

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
    """라즈베리파이 Go1 카메라와 PC를 연결하는 노드
    - PC IP 설정만 담당
    - 라즈베리파이로 START/STOP 명령 전송
    - 이미지 저장은 VideoSaveNode에서 담당
    """
    def __init__(self, node_id):
        super().__init__(node_id, "Video Source", "VIDEO_SRC")
        self.out_frame = generate_uuid()
        self.outputs[self.out_frame] = PortType.DATA
        self.state['target_ip'] = get_local_ip()
        self.state['receiver_folder'] = 'Captured_Images/go1_front'
        self._started = False
        self._last_frame = None
        self._auto_stopped_by_timer = False

    def execute(self):
        if not HAS_CV2:
            camera_state['status'] = 'Stopped'
            return None

        if not engine_module.is_running:
            self._auto_stopped_by_timer = False

        run_flag = bool(engine_module.is_running and not self._auto_stopped_by_timer)
        target_ip = str(self.state.get('target_ip', get_local_ip())).strip() or get_local_ip()
        
        if run_flag:
            if not self._started and camera_state['status'] in ['Stopped', 'Stopping...']:
                receiver_folder = str(self.state.get('receiver_folder', 'Captured_Images/go1_front')).strip() or 'Captured_Images/go1_front'
                start_duration = 0.0
                for node in node_registry.values():
                    if node.type_str == 'VIS_SAVE':
                        raw_use_timer = node.state.get('use_timer', False)
                        if isinstance(raw_use_timer, str):
                            use_timer = raw_use_timer.strip().lower() in ['1', 'true', 'yes', 'on']
                        else:
                            use_timer = bool(raw_use_timer)
                        if use_timer:
                            try:
                                start_duration = max(0.0, float(node.state.get('duration', 0.0)))
                            except Exception:
                                start_duration = 0.0
                        break
                camera_command_queue.append(('START_CMD', target_ip, receiver_folder, start_duration))
                self._started = True
        else:
            if self._started and camera_state['status'] in ['Running', 'Starting...']:
                camera_command_queue.append(('STOP', target_ip))
            self._started = False
            self._last_frame = None
            self.output_data[self.out_frame] = None
            return None

        # 수신 전용 폴더에서 최신 안정 프레임 읽기 (VIS_SAVE 출력 폴더와 분리)
        frame = self._last_frame
        try:
            source_folder = str(self.state.get('receiver_folder', 'Captured_Images/go1_front')).strip() or 'Captured_Images/go1_front'
            files = glob.glob(os.path.join(source_folder, "front_*.jpg"))
            if len(files) >= 2:
                files.sort(key=os.path.getctime)
                # 최신 파일은 쓰기 중일 수 있으므로 직전 파일부터 역순 탐색
                candidates = files[:-1][-5:]
                for target_file in reversed(candidates):
                    if not _is_file_stable(target_file):
                        continue
                    loaded = cv2.imread(target_file)
                    if loaded is not None and len(loaded.shape) >= 2 and loaded.shape[1] > 1:
                        self._last_frame = loaded
                        frame = loaded
                        break
        except Exception:
            frame = self._last_frame

        self.output_data[self.out_frame] = frame
        return None


class FisheyeUndistortNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "Fisheye Undistort", "VIS_FISHEYE")
        self.in_frame = generate_uuid()
        self.inputs[self.in_frame] = PortType.DATA
        self.out_frame = generate_uuid()
        self.outputs[self.out_frame] = PortType.DATA
        self.state['enabled'] = True
        self.state['crop_enabled'] = True
        self.state['crop_mode'] = 'left_half'
        self.state['crop_ratio'] = 0.5

    def execute(self):
        frame = self.fetch_input_data(self.in_frame)
        if frame is None or not HAS_CV2:
            return None

        try:
            use_calib = _coerce_bool(self.state.get('enabled', True), True)
            if use_calib:
                undistorted = cv2.fisheye.undistortImage(
                    frame,
                    _default_camera_matrix,
                    _default_dist_coeffs,
                    Knew=_default_camera_matrix,
                )
            else:
                undistorted = frame

            out_frame = undistorted
            crop_enabled = _coerce_bool(self.state.get('crop_enabled', True), True)
            if use_calib and crop_enabled and out_frame is not None and len(out_frame.shape) >= 2:
                h, w = out_frame.shape[:2]
                if w > 1:
                    crop_mode = str(self.state.get('crop_mode', 'left_half')).strip().lower()
                    if crop_mode == 'custom_ratio':
                        ratio = _clamp(_coerce_float(self.state.get('crop_ratio', 0.5), 0.5), 0.1, 1.0)
                        crop_w = max(1, int(w * ratio))
                    else:
                        crop_w = max(1, w // 2)
                    out_frame = out_frame[:, :crop_w]

            self.output_data[self.out_frame] = out_frame
        except Exception:
            self.output_data[self.out_frame] = frame
        return None


class DepthAnythingV2Node(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "Depth Anything V2", "VIS_DEPTH_DA2")
        self.in_frame = generate_uuid()
        self.inputs[self.in_frame] = PortType.DATA

        self.out_frame = generate_uuid()
        self.outputs[self.out_frame] = PortType.DATA
        self.out_depth = generate_uuid()
        self.outputs[self.out_depth] = PortType.DATA
        self.out_near_score = generate_uuid()
        self.outputs[self.out_near_score] = PortType.DATA
        self.out_obstacle = generate_uuid()
        self.outputs[self.out_obstacle] = PortType.DATA
        self.out_json = generate_uuid()
        self.outputs[self.out_json] = PortType.DATA

        self.state['enabled'] = True
        self.state['backend'] = 'transformers'
        self.state['encoder'] = 'vits'
        self.state['checkpoint_path'] = 'checkpoints/depth_anything_v2_vits.pth'
        self.state['hf_model_id'] = 'depth-anything/Depth-Anything-V2-Small-hf'
        self.state['prefer_cuda'] = True
        self.state['input_size'] = 518
        self.state['inference_interval_sec'] = 0.2
        self.state['closer_is_brighter'] = True
        self.state['risk_threshold'] = 0.65
        self.state['roi_x0'] = 0.3
        self.state['roi_y0'] = 0.5
        self.state['roi_x1'] = 0.7
        self.state['roi_y1'] = 0.95
        self.state['consecutive_frames_for_stop'] = 2
        self.state['use_stop_signal'] = False
        self.state['save_json'] = False
        self.state['json_path'] = 'depth_da2_data.json'

        self._last_infer_ts = 0.0
        self._last_depth = None
        self._last_vis = None
        self._last_json = ""
        self._last_near_score = 0.0
        self._last_obstacle = False
        self._risk_hit_count = 0
        self._last_error = ""

    def _run_inference(self, frame):
        backend = str(self.state.get('backend', 'transformers')).strip().lower()
        prefer_cuda = _coerce_bool(self.state.get('prefer_cuda', True), True)
        input_size = max(64, _coerce_int(self.state.get('input_size', 518), 518))

        if backend == 'official':
            model, err = _load_da2_official_model(
                self.state.get('encoder', 'vits'),
                self.state.get('checkpoint_path', ''),
                prefer_cuda=prefer_cuda,
            )
            if model is None:
                raise RuntimeError(err or 'failed to load official DA2 model')

            try:
                depth = model.infer_image(frame, input_size=input_size)
            except TypeError:
                depth = model.infer_image(frame)
            return np.asarray(depth, dtype=np.float32)

        pipe, err = _load_da2_hf_pipeline(
            self.state.get('hf_model_id', 'depth-anything/Depth-Anything-V2-Small-hf'),
            prefer_cuda=prefer_cuda,
        )
        if pipe is None:
            raise RuntimeError(err or 'failed to load transformers depth pipeline')

        if not HAS_PIL or Image is None:
            raise RuntimeError('PIL is required for transformers backend')

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(rgb)
        result = pipe(pil_img)
        raw_depth = result.get('depth') if isinstance(result, dict) else None
        if raw_depth is None:
            raise RuntimeError('depth output is missing from transformers pipeline')
        return np.asarray(raw_depth, dtype=np.float32)

    def execute(self):
        frame = self.fetch_input_data(self.in_frame)
        if frame is None or not HAS_CV2 or np is None:
            self.output_data[self.out_frame] = frame
            self.output_data[self.out_depth] = None
            self.output_data[self.out_near_score] = 0.0
            self.output_data[self.out_obstacle] = False
            self.output_data[self.out_json] = ""
            return None

        if not _coerce_bool(self.state.get('enabled', True), True):
            self._risk_hit_count = 0
            self.output_data[self.out_frame] = frame
            self.output_data[self.out_depth] = None
            self.output_data[self.out_near_score] = 0.0
            self.output_data[self.out_obstacle] = False
            self.output_data[self.out_json] = json.dumps({'status': 'disabled'})
            return None

        infer_interval = max(0.02, _coerce_float(self.state.get('inference_interval_sec', 0.2), 0.2))
        now = time.monotonic()
        should_infer = (self._last_depth is None) or ((now - self._last_infer_ts) >= infer_interval)
        backend = str(self.state.get('backend', 'transformers')).strip().lower()

        vis_frame = self._last_vis if self._last_vis is not None else frame
        depth_map = self._last_depth
        near_score = float(self._last_near_score)
        obstacle = bool(self._last_obstacle)
        payload_json = self._last_json

        if should_infer:
            start_t = time.perf_counter()
            try:
                depth_map = self._run_inference(frame)
                norm = _normalize_depth_for_visual(depth_map)

                if norm is None:
                    raise RuntimeError('failed to normalize depth output')

                closer_is_brighter = _coerce_bool(self.state.get('closer_is_brighter', True), True)
                near_map = norm if closer_is_brighter else (1.0 - norm)

                h, w = near_map.shape[:2]
                px0, py0, px1, py1 = _compute_roi_pixels(
                    h,
                    w,
                    self.state.get('roi_x0', 0.3),
                    self.state.get('roi_y0', 0.5),
                    self.state.get('roi_x1', 0.7),
                    self.state.get('roi_y1', 0.95),
                )
                roi = near_map[py0:py1, px0:px1]
                if roi.size == 0:
                    near_score = 0.0
                else:
                    near_score = float(np.percentile(roi, 90.0))

                risk_threshold = _clamp(_coerce_float(self.state.get('risk_threshold', 0.65), 0.65), 0.0, 1.0)
                obstacle = near_score >= risk_threshold

                if obstacle:
                    self._risk_hit_count += 1
                else:
                    self._risk_hit_count = 0

                required_hits = max(1, _coerce_int(self.state.get('consecutive_frames_for_stop', 2), 2))
                stop_recommended = bool(obstacle and self._risk_hit_count >= required_hits)
                if stop_recommended and _coerce_bool(self.state.get('use_stop_signal', False), False):
                    go1_node_intent['stop'] = True
                    go1_node_intent['trigger_time'] = time.monotonic()

                vis_gray = np.clip(norm * 255.0, 0, 255).astype(np.uint8)
                vis_color = cv2.applyColorMap(vis_gray, cv2.COLORMAP_INFERNO)
                cv2.rectangle(vis_color, (px0, py0), (px1 - 1, py1 - 1), (255, 255, 255), 2)
                text = f"NearScore:{near_score:.2f} Thr:{risk_threshold:.2f} {'STOP' if stop_recommended else 'SAFE'}"
                cv2.putText(vis_color, text, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

                infer_latency_ms = (time.perf_counter() - start_t) * 1000.0
                payload = {
                    'status': 'ok',
                    'timestamp': round(time.time(), 3),
                    'backend': backend,
                    'near_score': round(float(near_score), 4),
                    'risk_threshold': round(float(risk_threshold), 4),
                    'obstacle': bool(obstacle),
                    'stop_recommended': bool(stop_recommended),
                    'risk_hit_count': int(self._risk_hit_count),
                    'roi': {'x0': px0, 'y0': py0, 'x1': px1, 'y1': py1, 'width': w, 'height': h},
                    'infer_latency_ms': round(float(infer_latency_ms), 2),
                }
                payload_json = json.dumps(payload)

                if _coerce_bool(self.state.get('save_json', False), False):
                    json_path = str(self.state.get('json_path', 'depth_da2_data.json')).strip() or 'depth_da2_data.json'
                    if not _safe_json_dump(json_path, payload):
                        write_log(f"[VIS_DEPTH_DA2] JSON 저장 실패: path={json_path}")

                self._last_depth = depth_map
                self._last_vis = vis_color
                self._last_json = payload_json
                self._last_near_score = near_score
                self._last_obstacle = obstacle
                self._last_infer_ts = now
                self._last_error = ""
                vis_frame = vis_color
            except Exception as e:
                self._last_error = str(e)
                write_log(f"[VIS_DEPTH_DA2] {self._last_error}")
                payload = {'status': 'error', 'message': self._last_error, 'timestamp': round(time.time(), 3)}
                payload_json = json.dumps(payload)
                if _coerce_bool(self.state.get('save_json', False), False):
                    json_path = str(self.state.get('json_path', 'depth_da2_data.json')).strip() or 'depth_da2_data.json'
                    if not _safe_json_dump(json_path, payload):
                        write_log(f"[VIS_DEPTH_DA2] JSON 저장 실패: path={json_path}")
                self._last_json = payload_json
                self._risk_hit_count = 0
                depth_map = self._last_depth
                vis_frame = frame
                near_score = 0.0
                obstacle = False

        self.output_data[self.out_frame] = vis_frame
        self.output_data[self.out_depth] = depth_map
        self.output_data[self.out_near_score] = float(near_score)
        self.output_data[self.out_obstacle] = bool(obstacle)
        self.output_data[self.out_json] = payload_json
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
        self.out_json = generate_uuid()
        self.outputs[self.out_json] = PortType.DATA

        self.state['camera_id'] = 'go1_front'
        self.state['marker_size_m'] = 0.03
        self.state['input_undistorted'] = False
        self.state['json_path'] = 'aruco_data.json'
        self.state['draw_axes'] = True
        self.state['draw_overlay_text'] = True

    def execute(self):
        frame = self.fetch_input_data(self.in_frame)
        if frame is None or not HAS_CV2 or _aruco_detector is None:
            self.output_data[self.out_frame] = frame
            self.output_data[self.out_data] = []
            self.output_data[self.out_json] = ""
            return None

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        corners, ids, _ = _aruco_detector.detectMarkers(gray)

        detected = []
        draw = frame.copy()
        marker_size_m = max(0.0, _coerce_float(self.state.get('marker_size_m', 0.03), 0.03))
        if marker_size_m <= 0.0:
            marker_size_m = 0.03
        aruco_settings['marker_size'] = marker_size_m

        camera_matrix = _default_camera_matrix if _default_camera_matrix is not None else np.array(
            [[640.0, 0.0, 320.0], [0.0, 640.0, 240.0], [0.0, 0.0, 1.0]],
            dtype=np.float32,
        )
        use_undistorted_input = _coerce_bool(self.state.get('input_undistorted', False), False)
        dist_coeffs = zero_dist_coeffs if use_undistorted_input else _default_dist_coeffs
        if dist_coeffs is None:
            dist_coeffs = np.zeros((4, 1), dtype=np.float32)

        marker_points = _build_marker_object_points(marker_size_m)
        camera_id = str(self.state.get('camera_id', 'go1_front')).strip() or 'go1_front'
        payload_json = ""

        if ids is not None and len(ids) > 0:
            for i, marker_id in enumerate(ids.flatten()):
                try:
                    ret, rvec, tvec = cv2.solvePnP(marker_points, corners[i], camera_matrix, dist_coeffs)
                except Exception:
                    ret = False
                    rvec = None
                    tvec = None

                if not ret or rvec is None or tvec is None:
                    continue

                if _coerce_bool(self.state.get('draw_axes', True), True):
                    try:
                        cv2.drawFrameAxes(draw, camera_matrix, dist_coeffs, rvec, tvec, 0.03)
                    except Exception:
                        pass

                try:
                    cv2.aruco.drawDetectedMarkers(draw, corners)
                except Exception:
                    pass

                tx = float(tvec[0][0])
                ty = float(tvec[1][0])
                tz = float(tvec[2][0])
                marker_data = {
                    'id': int(marker_id),
                    'x': round(tx, 4),
                    'y': round(ty, 4),
                    'z': round(tz, 4),
                    'cam': camera_id,
                }
                detected.append(marker_data)

                if _coerce_bool(self.state.get('draw_overlay_text', True), True):
                    try:
                        text = f"[{camera_id}] ID:{int(marker_id)} X:{tx:.2f} Y:{ty:.2f} Z:{tz:.2f}"
                        cx = int(corners[i][0][0][0])
                        cy = int(corners[i][0][0][1])
                        cv2.putText(draw, text, (cx, cy - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
                    except Exception:
                        pass

        if len(detected) > 0:
            payload = {
                'camera': camera_id,
                'timestamp': round(time.time(), 3),
                'markers': detected,
            }
            payload_json = json.dumps(payload)

            if go1_node_intent.get('send_aruco', False):
                try:
                    go1_sock.sendto(payload_json.encode('utf-8'), (GO1_UNITY_IP, 5008))
                except Exception as e:
                    write_log(f"[VIS_ARUCO] UDP 전송 실패: {e}")

            json_path = str(self.state.get('json_path', 'aruco_data.json')).strip() or 'aruco_data.json'
            try:
                json_dir = os.path.dirname(json_path)
                if json_dir:
                    os.makedirs(json_dir, exist_ok=True)
                with open(json_path, 'w', encoding='utf-8') as f:
                    f.write(payload_json)
            except Exception as e:
                write_log(f"[VIS_ARUCO] JSON 저장 실패: {e} | path={json_path}")

        self.output_data[self.out_frame] = draw
        self.output_data[self.out_data] = detected
        self.output_data[self.out_json] = payload_json
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


# ================= [Video Frame Save Node] =================
class VideoFrameSaveNode(BaseNode):
    """VideoSourceNode에서 전달받은 프레임을 지정된 폴더에 저장
    - 입력 포트에서 프레임 수신
    - 이미지를 JPEG 파일로 저장
    - 타이머 설정 가능 (타이머 종료 후 저장 중단)
    - 타이머 미설정 시 Max Frames 초과 파일 자동 삭제
    """
    def __init__(self, node_id):
        super().__init__(node_id, "Video Save", "VIS_SAVE")
        self.in_flow = generate_uuid()
        self.inputs[self.in_flow] = PortType.FLOW
        self.in_frame = generate_uuid()
        self.inputs[self.in_frame] = PortType.DATA
        self.out_flow = generate_uuid()
        self.outputs[self.out_flow] = PortType.FLOW

        self.state['folder'] = 'Captured_Images/go1_saved'
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
        """Max Frames 초과 파일 삭제"""
        files = glob.glob(os.path.join(folder, "front_*.jpg"))
        if len(files) <= max_frames:
            return

        # 파일명(front_000001.jpg) 인덱스를 우선 기준으로 정렬해 가장 오래된 프레임부터 삭제한다.
        files.sort(key=lambda p: (self._extract_frame_index(p), os.path.getmtime(p)))
        delete_fail_count = 0
        for old_file in files[:len(files) - max_frames]:
            try:
                os.remove(old_file)
            except Exception as e:
                delete_fail_count += 1
                if delete_fail_count == 1:
                    write_log(f"[VIS_SAVE] MaxFrames 삭제 실패(예시): {os.path.basename(old_file)} ({e})")

    def execute(self):
        global camera_save_state
        
        folder = str(self.state.get('folder', 'Captured_Images/go1_front')).strip() or 'Captured_Images/go1_front'
        is_saving = bool(engine_module.is_running)
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

        if not is_saving:
            self._timer_completed_this_run = False

        # 저장 상태 업데이트
        camera_save_state['folder'] = folder
        camera_save_state['duration'] = duration

        if not is_saving:
            if self._save_start_time is not None:
                write_log("[VIS_SAVE] 저장 중단")
                self._save_start_time = None
                camera_save_state['status'] = 'Stopped'
                camera_save_state['start_time'] = None
                camera_save_state['frame_count'] = 0
            return self.out_flow

        # 저장 시작
        if is_saving and not self._save_start_time and not self._timer_completed_this_run:
            self._save_start_time = time.time()
            self._frame_count = 0
            camera_save_state['status'] = 'Running'
            camera_save_state['start_time'] = self._save_start_time
            try:
                os.makedirs(folder, exist_ok=True)
                self._sync_frame_index_from_folder(folder)
                write_log(f"[VIS_SAVE] 저장 시작: {folder}")
            except Exception as e:
                write_log(f"[VIS_SAVE] 폴더 생성 실패: {e}")
                return self.out_flow

        # 타이머 체크
        if self._save_start_time and use_timer and duration > 0:
            elapsed = time.time() - self._save_start_time
            if elapsed > duration:
                write_log(f"[VIS_SAVE] 타이머 종료: {duration:.1f}s 경과")
                self._save_start_time = None
                self._timer_completed_this_run = True
                camera_save_state['status'] = 'Stopped'
                camera_save_state['start_time'] = None
                camera_save_state['frame_count'] = 0
                # 저장 완료 시 스트리밍도 함께 정지
                for node in node_registry.values():
                    if node.type_str == 'VIDEO_SRC':
                        node._auto_stopped_by_timer = True
                camera_command_queue.append(('STOP', ''))
                return self.out_flow

        # 프레임 저장
        frame = self.fetch_input_data(self.in_frame)
        if frame is not None and HAS_CV2 and self._save_start_time is not None:
            try:
                self._frame_index += 1
                filename = os.path.join(folder, f"front_{self._frame_index:06d}.jpg")
                success = cv2.imwrite(filename, frame)
                if success:
                    self._frame_count += 1
                    camera_save_state['frame_count'] = self._frame_count
            except Exception as e:
                write_log(f"[VIS_SAVE] 프레임 저장 실패: {e}")

        # Max Frames 정리 (타이머 OFF 상태에서만)
        if self._save_start_time is not None and not use_timer:
            self._prune_saved_frames(folder, max_frames)
        
        return self.out_flow


# ================= [Server Sender Node] =================
class ServerSenderNode(BaseNode):
    """원격 서버로 이미지 업로드하는 노드
    - VideoFrameSaveNode에서 저장한 이미지 감지
    - HTTP multipart/form-data로 비동기 업로드
    - 시작/중지 제어
    """
    def __init__(self, node_id):
        super().__init__(node_id, "Server Sender", "GO1_SERVER_SENDER")
        self.in_flow = generate_uuid()
        self.inputs[self.in_flow] = PortType.FLOW
        self.out_flow = generate_uuid()
        self.outputs[self.out_flow] = PortType.FLOW
        
        self.state['action'] = 'Start Sender'  # "Start Sender" / "Stop Sender"
        self.state['server_url'] = "http://192.168.1.100:5001/upload"
        
        self._last_action = None
        self._last_request_ts = 0.0

    def execute(self):
        global sender_state, multi_sender_active
        
        action = self.state.get('action', 'Start Sender')
        url = self.state.get('server_url', "http://192.168.1.100:5001/upload")
        now = time.monotonic()
        cooldown_ok = (now - self._last_request_ts) > 0.5
        
        # 액션 변경 기록(디버깅/상태 추적용)
        if action != self._last_action:
            self._last_action = action

        # 토글 변경이 없어도 현재 의도 상태를 유지하도록 재요청 가능하게 처리
        if action == "Start Sender":
            if (not multi_sender_active) and sender_state['status'] in ['Stopped', 'Stopping...'] and cooldown_ok:
                sender_state['status'] = 'Starting...'
                sender_command_queue.append(('START', url))
                self._last_request_ts = now

        elif action == "Stop Sender":
            if multi_sender_active and sender_state['status'] in ['Running', 'Starting...'] and cooldown_ok:
                sender_state['status'] = 'Stopping...'
                sender_command_queue.append(('STOP', url))
                self._last_request_ts = now
        
        return self.out_flow

