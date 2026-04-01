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
GO1_IP = "192.168.50.42"
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

go1_dashboard = {
    "status": "Idle",
    "hw_link": "Offline",
    "unity_link": "Waiting",
}

aruco_settings = {
    'enabled': False,
    'marker_size': 0.03,
}

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

camera_save_state = {
    'status': 'Stopped',
    'folder': 'Captured_Images/go1_saved',
    'duration': 0.0,
    'start_time': None,
    'frame_count': 0,
}
camera_save_queue = deque()


def _clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def _wrap_pi(a):
    while a > math.pi:
        a -= 2.0 * math.pi
    while a < -math.pi:
        a += 2.0 * math.pi
    return a


def _has_go1_nodes():
    return any(n.type_str.startswith("GO1_") for n in node_registry.values())


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
    global GO1_IP, _GO1_IP_INITIALIZED, _CAMERA_WORKER_STARTED
    if _GO1_IP_INITIALIZED:
        return
    _GO1_IP_INITIALIZED = True

    GO1_IP = _prompt_go1_ip(GO1_IP)
    write_log(f"Go1 Target IP: {GO1_IP}")
    if HAS_UNITREE_SDK:
        write_log(f"Go1 SDK Ready: {sdk_path}")
    else:
        write_log(f"Go1 SDK Missing: {sdk_path} ({SDK_IMPORT_ERROR})")

    if not _CAMERA_WORKER_STARTED:
        _CAMERA_WORKER_STARTED = True
        threading.Thread(target=camera_worker_thread, daemon=True).start()


def camera_worker_thread():
    global camera_state, CAMERA_CONFIG
    nanos = GO1_CAMERA_NANOS

    while True:
        if camera_command_queue:
            cmd_data = camera_command_queue.popleft()
            cmd = cmd_data[0]

            if cmd == 'START_CMD':
                _, pc_ip, target_folder, duration = cmd_data
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

                try:
                    subprocess.call("pkill -f 'gst-launch-1.0.*multifilesink'", shell=True)
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
                    subprocess.Popen(gst_cmd, shell=True)
                    write_log(f"[Cam START] Receiver listening on port 9400 -> {target_folder}")
                except Exception as e:
                    write_log(f"[Cam START ERROR] Failed to start receiver: {e}")

                time.sleep(1.0)
                camera_state['status'] = 'Running'
                camera_state['start_time'] = time.time()
                camera_state['timer_started_logged'] = False
                camera_state['last_interval_count'] = 0

            elif cmd == 'STOP':
                camera_state['status'] = 'Stopping...'
                camera_state['duration'] = 0.0
                try:
                    subprocess.call("pkill -f 'gst-launch-1.0.*multifilesink'", shell=True)
                except Exception:
                    pass
                time.sleep(0.5)
                camera_state['status'] = 'Stopped'

        if camera_state['status'] == 'Running':
            elapsed = time.time() - float(camera_state.get('start_time', 0.0))
            interval_count = int(elapsed // 10)
            if interval_count > camera_state.get('last_interval_count', 0) and interval_count > 0:
                write_log(f"[Cam Running] {interval_count * 10}초 경과")
                camera_state['last_interval_count'] = interval_count

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
    last_go1_tick = 0
    last_imu_val = 0.0

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

                current_tick = getattr(state, 'tick', 0)
                current_imu = float(state.imu.rpy[0]) + float(state.imu.rpy[1]) + float(state.imu.rpy[2])

                if current_tick != last_go1_tick or current_imu != last_imu_val:
                    last_go1_recv_time = tnow
                    last_go1_tick = current_tick
                    last_imu_val = current_imu

                if (tnow - last_go1_recv_time) < 1.0:
                    go1_in_use = bool(engine_module.is_running) and _has_go1_nodes()
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

        go1_in_use = bool(engine_module.is_running) and _has_go1_nodes()

        if cmd:
            cmd.mode = target_mode
            cmd.velocity = [out_vx, out_vy]
            cmd.yawSpeed = out_wz
            if go1_in_use:
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
                # PC IP만 라즈베리파이로 전송 (저장은 하지 않음)
                camera_command_queue.append(('START_CMD', target_ip, '', 0.0))
                self._started = True
        else:
            if self._started and camera_state['status'] in ['Running', 'Starting...']:
                camera_command_queue.append(('STOP', target_ip))
            self._started = False
            self._last_frame = None
            self.output_data[self.out_frame] = None
            return None

        # VideoSaveNode에서 저장한 사용자 지정 폴더에서 프레임 읽기
        frame = self._last_frame
        try:
            # 모든 노드를 순회하여 VideoSaveNode의 save_folder 찾기
            save_folder = 'Captured_Images/go1_front'  # 기본값
            for node in node_registry.values():
                if node.type_str == 'VIS_SAVE':
                    save_folder = str(node.state.get('save_folder', 'Captured_Images/go1_front'))
                    break
            
            files = glob.glob(os.path.join(save_folder, "front_*.jpg"))

            if len(files) >= 2:
                files.sort(key=os.path.getctime)
                target_file = files[-2]  # 가장 최신 두 번째 파일
                loaded = cv2.imread(target_file)
                if loaded is not None:
                    self._last_frame = loaded
                    frame = loaded
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

        if go1_node_intent.get('send_aruco', False) and detected:
            payload = {
                "camera": "go1_front",
                "timestamp": round(time.time(), 3),
                "markers": detected,
            }
            payload_json = json.dumps(payload)
            try:
                go1_sock.sendto(payload_json.encode('utf-8'), (GO1_UNITY_IP, 5008))
            except Exception:
                pass

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

        self.state['folder'] = 'Captured_Images/go1_front'
        self.state['duration'] = 10.0
        self.state['use_timer'] = False
        self.state['max_frames'] = 100
        
        self._save_start_time = None
        self._frame_count = 0
        self._timer_completed_this_run = False
        self._frame_index = 0

    def _prune_saved_frames(self, folder, max_frames):
        """Max Frames 초과 파일 삭제"""
        files = glob.glob(os.path.join(folder, "front_*.jpg"))
        if len(files) <= max_frames:
            return
        files.sort(key=os.path.getctime)
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
        use_timer = bool(self.state.get('use_timer', False))
        max_frames = max(1, int(self.state.get('max_frames', 100)))

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
            self._frame_index = 0
            camera_save_state['status'] = 'Running'
            camera_save_state['start_time'] = self._save_start_time
            try:
                os.makedirs(folder, exist_ok=True)
                write_log(f"[VIS_SAVE] 저장 시작: {folder}")
            except Exception as e:
                write_log(f"[VIS_SAVE] 폴더 생성 실패: {e}")
                return self.out_flow

        # 타이머 체크
        if self._save_start_time and use_timer and duration > 0:
            elapsed = time.time() - self._save_start_time
            if elapsed > duration:
                write_log("[VIS_SAVE] 타이머 완료")
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
