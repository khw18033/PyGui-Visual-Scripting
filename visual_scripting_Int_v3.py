import sys
import time
import math
import socket
import select
import threading
import json
import os
import subprocess
import glob
import asyncio
import aiohttp
import serial 
import dearpygui.dearpygui as dpg
from collections import deque
from abc import ABC, abstractmethod
from datetime import datetime

# ================= [Unitree SDK Import (Go1)] =================
sys.path.append('/home/physical/PyGui-Visual-Scripting/unitree_legged_sdk/lib/python/arm64')
try:
    import robot_interface as sdk
    HAS_UNITREE_SDK = True
except ImportError as e:
    HAS_UNITREE_SDK = False
    print(f"Warning: 'robot_interface' module not found. ({e})")

# ================= [Global Core Settings] =================
node_registry = {}
link_registry = {}
is_running = False
SAVE_DIR = "Node_File_Integrated"
if not os.path.exists(SAVE_DIR): os.makedirs(SAVE_DIR)
system_log_buffer = deque(maxlen=50)

def write_log(msg):
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {msg}"); system_log_buffer.append(f"[{timestamp}] {msg}")

def get_local_ip():
    try: s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(('8.8.8.8', 80)); ip = s.getsockname()[0]; s.close(); return ip
    except: return "127.0.0.1"

def get_wifi_ssid():
    try: return subprocess.check_output(['iwgetid','-r']).decode('utf-8').strip() or "Unknown"
    except: return "Unknown"

def get_save_files():
    if not os.path.exists(SAVE_DIR): return []
    return [f for f in os.listdir(SAVE_DIR) if f.endswith(".json")]

# ================= [MT4 State & Config] =================
ser = None 
mt4_current_pos = {'x': 200.0, 'y': 0.0, 'z': 120.0, 'gripper': 40.0}
mt4_target_goal = {'x': 200.0, 'y': 0.0, 'z': 120.0, 'gripper': 40.0} 
mt4_manual_override_until = 0.0 
mt4_dashboard = {"status": "Idle", "hw_link": "Offline", "latency": 0.0, "last_pkt_time": 0.0}
MT4_UNITY_IP = "192.168.50.63"; MT4_FEEDBACK_PORT = 5005
MT4_LIMITS = {'min_x': 100, 'max_x': 280, 'min_y': -150, 'max_y': 150, 'min_z': 0, 'max_z': 280}
MT4_GRIPPER_MIN = 30.0; MT4_GRIPPER_MAX = 60.0
MT4_Z_OFFSET = 90.0

# ================= [Go1 State & Config] =================
HIGHLEVEL = 0xee; LOCAL_PORT = 8090; ROBOT_IP = "192.168.50.159"; ROBOT_PORT = 8082
GO1_UNITY_IP = "192.168.50.246"; UNITY_STATE_PORT = 15101; UNITY_CMD_PORT = 15102; UNITY_RX_PORT = 15100
dt = 0.002; V_MAX, S_MAX, W_MAX = 0.4, 0.4, 2.0; VX_CMD, VY_CMD, WZ_CMD = 0.20, 0.20, 1.00
hold_timeout_sec = 0.1; repeat_grace_sec = 0.4; min_move_sec = 0.4; stop_brake_sec = 0.0

go1_node_intent = {'vx': 0.0, 'vy': 0.0, 'wz': 0.0, 'yaw_align': False, 'reset_yaw': False, 'stop': False, 'use_unity_cmd': True, 'trigger_time': time.monotonic()}
# ★ [v3 추가] 배터리(battery) 상태 변수 추가
go1_state = {'world_x': 0.0, 'world_z': 0.0, 'yaw_unity': 0.0, 'vx_cmd': 0.0, 'vy_cmd': 0.0, 'wz_cmd': 0.0, 'mode': 1, 'reason': "NONE", 'battery': -1}
go1_unity_data = {'vx': 0.0, 'vy': 0.0, 'wz': 0.0, 'estop': 0, 'active': False} 
go1_dashboard = {"status": "Idle", "hw_link": "Offline", "unity_link": "Waiting"}

camera_state = {'status': 'Stopped', 'target_ip': ''}; camera_command_queue = deque()
sender_state = {'status': 'Stopped'}; sender_command_queue = deque(); multi_sender_active = False
TARGET_FPS = 30; INTERVAL = 1.0 / TARGET_FPS; KEEP_COUNT = 300
CAMERA_CONFIG = [
    {"folder": "/dev/shm/go1_front", "id": "go1_front"},
    {"folder": "/dev/shm/go1_underfront", "id": "go1_underfront"},
    {"folder": "/dev/shm/go1_nano14_left", "id": "go1_left"},
    {"folder": "/dev/shm/go1_nano14_right", "id": "go1_right"},
    {"folder": "/dev/shm/go1_nano15_bottom", "id": "go1_bottom"}
]

def clamp(x, lo, hi): return lo if x < lo else hi if x > hi else x
def wrap_pi(a):
    while a > math.pi: a -= 2.0 * math.pi
    while a < -math.pi: a += 2.0 * math.pi
    return a

# ★ [v3 추가] 긴급 정지(E-STOP) 버튼 콜백
def go1_estop_callback():
    global go1_node_intent
    go1_node_intent['stop'] = True
    go1_node_intent['vx'] = 0.0
    go1_node_intent['vy'] = 0.0
    go1_node_intent['wz'] = 0.0
    write_log("Go1 EMERGENCY STOP Activated!")

# ================= [Architecture: Base & Universal Node] =================
class BaseRobotDriver(ABC):
    @abstractmethod
    def get_ui_schema(self): pass
    @abstractmethod
    def get_settings_schema(self): pass
    @abstractmethod
    def execute_command(self, inputs, settings): pass

class MT4RobotDriver(BaseRobotDriver):
    def __init__(self):
        self.last_cmd = ""; self.last_write_time = 0; self.write_interval = 0.0
    def get_ui_schema(self):
        return {'x': ("X", 200.0), 'y': ("Y", 0.0), 'z': ("Z", 120.0), 'gripper': ("G", 40.0)}
    def get_settings_schema(self):
        return {'smooth': ("Smth", 1.0), 'speed': ("Spd", 2.0)}
    def execute_command(self, inputs, settings):
        global mt4_current_pos, mt4_target_goal, mt4_manual_override_until, ser
        if time.time() > mt4_manual_override_until:
            for k in self.get_ui_schema().keys():
                if inputs.get(k) is not None: mt4_target_goal[k] = float(inputs[k])
        smooth = 1.0 if time.time() < mt4_manual_override_until else max(0.01, min(settings.get('smooth', 1.0), 1.0))
        dx = mt4_target_goal['x'] - mt4_current_pos['x']; dy = mt4_target_goal['y'] - mt4_current_pos['y']; dz = mt4_target_goal['z'] - mt4_current_pos['z']
        nx = mt4_current_pos['x'] + dx * smooth if not (abs(dx)<0.5 and abs(dy)<0.5 and abs(dz)<0.5) else mt4_target_goal['x']
        ny = mt4_current_pos['y'] + dy * smooth if not (abs(dx)<0.5 and abs(dy)<0.5 and abs(dz)<0.5) else mt4_target_goal['y']
        nz = mt4_current_pos['z'] + dz * smooth if not (abs(dx)<0.5 and abs(dy)<0.5 and abs(dz)<0.5) else mt4_target_goal['z']
        ng = max(MT4_GRIPPER_MIN, min(mt4_target_goal['gripper'], MT4_GRIPPER_MAX))
        nx = max(MT4_LIMITS['min_x'], min(nx, MT4_LIMITS['max_x'])); ny = max(MT4_LIMITS['min_y'], min(ny, MT4_LIMITS['max_y'])); nz = max(MT4_LIMITS['min_z'], min(nz, MT4_LIMITS['max_z']))
        new_state = {'x': nx, 'y': ny, 'z': nz, 'gripper': ng}
        if time.time() - self.last_write_time >= self.write_interval:
            cmd = f"G0 X{nx:.1f} Y{ny:.1f} Z{nz:.1f}\nM3 S{int(ng)}\n"
            if cmd != self.last_cmd:
                try: 
                    if ser and ser.is_open: ser.write(cmd.encode()); self.last_write_time = time.time()
                except: mt4_dashboard["hw_link"] = "Offline"
                self.last_cmd = cmd
        mt4_current_pos.update(new_state)
        return new_state

class Go1RobotDriver(BaseRobotDriver):
    def get_ui_schema(self):
        return {'vx': ("Vx In", 0.0), 'vy': ("Vy In", 0.0), 'wz': ("Wz In", 0.0)}
    def get_settings_schema(self):
        return {}
    def execute_command(self, inputs, settings):
        global go1_node_intent
        if inputs.get('vx') is not None or inputs.get('vy') is not None or inputs.get('wz') is not None:
            go1_node_intent['vx'] = float(inputs.get('vx') or 0); go1_node_intent['vy'] = float(inputs.get('vy') or 0); go1_node_intent['wz'] = float(inputs.get('wz') or 0)
            go1_node_intent['trigger_time'] = time.monotonic()
        return None 

class BaseNode(ABC):
    def __init__(self, node_id, label, type_str):
        self.node_id = node_id; self.label = label; self.type_str = type_str; self.inputs = {}; self.outputs = {}; self.output_data = {} 
    @abstractmethod
    def build_ui(self): pass
    @abstractmethod
    def execute(self): return None 
    def fetch_input_data(self, input_attr_id):
        target_link = None
        for link in link_registry.values():
            if link['target'] == input_attr_id: target_link = link; break
        if not target_link: return None 
        source_node = node_registry.get(dpg.get_item_parent(target_link['source']))
        if source_node: return source_node.output_data.get(target_link['source'])
        return None
    def get_settings(self): return {}
    def load_settings(self, data): pass

class UniversalRobotNode(BaseNode):
    def __init__(self, node_id, driver_instance):
        super().__init__(node_id, "Universal Robot Driver", "ROBOT_CONTROL")
        self.driver = driver_instance
        self.schema = self.driver.get_ui_schema()
        self.settings_schema = self.driver.get_settings_schema()
        self.in_pins = {}; self.ui_fields = {}; self.setting_pins = {}; self.setting_fields = {}
        self.cache_ui = {k: 0.0 for k in self.schema.keys()}
        if isinstance(self.driver, MT4RobotDriver): self.label = "MT4 Driver"
        elif isinstance(self.driver, Go1RobotDriver): self.label = "Go1 Driver"

    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label=self.label):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as flow: dpg.add_text("Flow In"); self.inputs[flow]="Flow"
            for key, (label, default_val) in self.schema.items():
                with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as aid:
                    with dpg.group(horizontal=True): 
                        dpg.add_text(label, color=(255,255,0)); self.ui_fields[key] = dpg.add_input_float(width=80, default_value=default_val, step=0)
                    self.inputs[aid] = "Data"; self.in_pins[key] = aid
            
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static): dpg.add_spacer(height=5) 
            for key, (label, default_val) in self.settings_schema.items():
                with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as aid:
                    with dpg.group(horizontal=True): 
                        dpg.add_text(label); self.setting_fields[key] = dpg.add_input_float(width=60, default_value=default_val, step=0)
                    self.inputs[aid] = "Data"; self.setting_pins[key] = aid
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as fout: dpg.add_text("Flow Out"); self.outputs[fout]="Flow"

    def execute(self):
        fetched_inputs = {key: self.fetch_input_data(aid) for key, aid in self.in_pins.items()}
        fetched_settings = {}
        for key, aid in self.setting_pins.items():
            val = self.fetch_input_data(aid)
            if val is not None: dpg.set_value(self.setting_fields[key], float(val))
            fetched_settings[key] = dpg.get_value(self.setting_fields[key])
            
        new_state = self.driver.execute_command(fetched_inputs, fetched_settings)
        if new_state:
            for key in self.schema.keys():
                if key in new_state and abs(self.cache_ui[key] - new_state[key]) > 0.1:
                    dpg.set_value(self.ui_fields[key], new_state[key]); self.cache_ui[key] = new_state[key]
        for k, v in self.outputs.items():
            if v == "Flow": return k
        return None
    def get_settings(self): return {k: dpg.get_value(v) for k, v in self.setting_fields.items()}
    def load_settings(self, data): 
        for k, v in self.setting_fields.items():
            if k in data: dpg.set_value(v, data[k])

# ================= [MT4 Dashboard Callbacks] =================
def mt4_manual_control_callback(sender, app_data, user_data):
    global mt4_manual_override_until, mt4_target_goal, mt4_current_pos
    mt4_manual_override_until = time.time() + 1.5
    axis, step = user_data
    mt4_target_goal[axis] = mt4_current_pos[axis] + step
    mt4_apply_limits_and_move()

def mt4_move_to_coord_callback(sender, app_data, user_data):
    global mt4_manual_override_until, mt4_target_goal
    mt4_manual_override_until = time.time() + 2.0
    mt4_target_goal['x'] = float(dpg.get_value("input_x"))
    mt4_target_goal['y'] = float(dpg.get_value("input_y"))
    mt4_target_goal['z'] = float(dpg.get_value("input_z"))
    mt4_target_goal['gripper'] = float(dpg.get_value("input_g"))
    mt4_apply_limits_and_move()

def mt4_apply_limits_and_move():
    global mt4_target_goal, mt4_current_pos, ser
    mt4_target_goal['x'] = max(MT4_LIMITS['min_x'], min(mt4_target_goal['x'], MT4_LIMITS['max_x']))
    mt4_target_goal['y'] = max(MT4_LIMITS['min_y'], min(mt4_target_goal['y'], MT4_LIMITS['max_y']))
    mt4_target_goal['z'] = max(MT4_LIMITS['min_z'], min(mt4_target_goal['z'], MT4_LIMITS['max_z']))
    mt4_target_goal['gripper'] = max(MT4_GRIPPER_MIN, min(mt4_target_goal['gripper'], MT4_GRIPPER_MAX))
    mt4_current_pos.update(mt4_target_goal)
    if ser and ser.is_open:
        ser.write(f"G0 X{mt4_target_goal['x']:.1f} Y{mt4_target_goal['y']:.1f} Z{mt4_target_goal['z']:.1f}\nM3 S{int(mt4_target_goal['gripper'])}\n".encode())

def mt4_homing_callback(sender, app_data, user_data):
    threading.Thread(target=mt4_homing_thread_func, daemon=True).start()

def mt4_homing_thread_func():
    global ser, mt4_manual_override_until, mt4_target_goal, mt4_current_pos
    if ser:
        mt4_manual_override_until = time.time() + 20.0
        mt4_dashboard["status"] = "HOMING..."; write_log("Homing...")
        ser.write(b"$H\r\n"); time.sleep(15); ser.write(b"M20\r\n"); ser.write(b"G90\r\n"); ser.write(b"G1 F2000\r\n")
        mt4_target_goal.update({'x':200.0, 'y':0.0, 'z':120.0, 'gripper':40.0})
        mt4_current_pos.update(mt4_target_goal)
        ser.write(b"G0 X200 Y0 Z120 F2000\r\n"); ser.write(b"M3 S40\r\n")
        mt4_dashboard["status"] = "Idle"; write_log("Homing Done")

# ================= [MT4 Background Threads] =================
def init_mt4_serial():
    global ser
    try:
        ser = serial.Serial('/dev/ttyUSB0', 115200, timeout=0.05)
        mt4_dashboard["hw_link"] = "Online"; write_log("System: MT4 Connected")
        time.sleep(2); ser.write(b"$H\r\n"); time.sleep(15) 
        ser.write(b"M20\r\n"); ser.write(b"G90\r\n"); ser.write(b"G1 F2000\r\n"); time.sleep(1)
        ser.write(b"G0 X200 Y0 Z120 F2000\r\n"); ser.write(b"M3 S40\r\n") 
    except Exception as e:
        mt4_dashboard["hw_link"] = "Simulation"; write_log(f"MT4 Sim Mode ({e})"); ser = None

def auto_reconnect_mt4_thread():
    global ser
    while True:
        if ser is None and os.path.exists('/dev/ttyUSB0'):
            try: init_mt4_serial() 
            except: pass
        time.sleep(3) 

# ================= [Go1 Background Threads] =================
def camera_worker_thread():
    global camera_state
    nanos = ["unitree@192.168.123.13", "unitree@192.168.123.14", "unitree@192.168.123.15"]
    while True:
        if camera_command_queue:
            cmd, pc_ip = camera_command_queue.popleft()
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            if cmd == 'START':
                camera_state['status'] = 'Starting...'; write_log(f"Cam: Start Stream to {pc_ip}")
                for nano in nanos:
                    remote_cmd = f"sudo fuser -k /dev/video0 /dev/video1 2>/dev/null; cd /home/unitree; ./kill_camera.sh || true; nohup ./go1_send_both.sh {pc_ip} > send_both_{ts}.log 2>&1 & echo $! > send_both_{ts}.pid"
                    try: subprocess.Popen(["ssh", "-o", "StrictHostKeyChecking=accept-new", "-tt", nano, f"bash -lc '{remote_cmd}'"])
                    except Exception as e: write_log(f"SSH Error ({nano}): {e}")
                subprocess.call("pkill -f 'gst-launch-1.0.*multifilesink'", shell=True); time.sleep(0.5)
                recv_configs = [("9400", "/dev/shm/go1_front", "front"), ("9401", "/dev/shm/go1_underfront", "underfront"), ("9410", "/dev/shm/go1_nano14_left", "left"), ("9411", "/dev/shm/go1_nano14_right", "right"), ("9420", "/dev/shm/go1_nano15_bottom", "bottom")]
                for port, outdir, prefix in recv_configs:
                    os.makedirs(outdir, exist_ok=True)
                    gst_cmd = f"gst-launch-1.0 -q udpsrc port={port} caps=\"application/x-rtp,media=video,encoding-name=JPEG,payload=26\" ! rtpjpegdepay ! multifilesink location=\"{outdir}/{prefix}_%06d.jpg\" sync=false"
                    subprocess.Popen(gst_cmd, shell=True)
                time.sleep(2); camera_state['status'] = 'Running'
            elif cmd == 'STOP':
                camera_state['status'] = 'Stopping...'; write_log("Cam: Stopping stream...")
                for nano in nanos:
                    script = "cd /home/unitree && pkill -f go1_send_cam || true; pkill -f gst-launch-1.0 || true; ./kill_camera.sh || true"
                    try: subprocess.Popen(["ssh", "-o", "StrictHostKeyChecking=accept-new", "-tt", nano, f"bash -lc '{script}'"])
                    except: pass
                subprocess.call("pkill -f 'gst-launch-1.0.*multifilesink'", shell=True); time.sleep(1)
                camera_state['status'] = 'Stopped'; write_log("Cam: Stream Stopped")
        time.sleep(0.1)

def cleanup_worker(config):
    global multi_sender_active
    folder = config["folder"]
    while multi_sender_active:
        try:
            files = glob.glob(os.path.join(folder, "*.jpg")); files.sort(key=os.path.getmtime)
            if len(files) > KEEP_COUNT:
                for f in files[:len(files) - KEEP_COUNT]:
                    try: os.remove(f)
                    except OSError: pass
        except Exception: pass
        time.sleep(1)

async def send_image_async(session, filepath, camera_id, server_url):
    try:
        if not os.path.exists(filepath): return
        with open(filepath, 'rb') as f: file_data = f.read()
        form = aiohttp.FormData()
        form.add_field('camera_id', camera_id); form.add_field('file', file_data, filename=os.path.basename(filepath), content_type='image/jpeg')
        async with session.post(server_url, data=form, timeout=2.0) as response: pass
    except: pass

async def camera_async_worker(config, server_url):
    global multi_sender_active
    folder = config["folder"]; camera_id = config["id"]; last_processed_file = None
    os.makedirs(folder, exist_ok=True)
    async with aiohttp.ClientSession() as session:
        while multi_sender_active:
            cycle_start = time.time(); files = glob.glob(os.path.join(folder, "*.jpg"))
            if files:
                valid_files = []
                for f in files:
                    try: valid_files.append((os.path.getctime(f), f))
                    except OSError: pass
                if valid_files:
                    _, latest_file = max(valid_files)
                    if latest_file != last_processed_file:
                        last_processed_file = latest_file
                        await send_image_async(session, latest_file, camera_id, server_url)
            await asyncio.sleep(max(0, INTERVAL - (time.time() - cycle_start)))

def start_async_loop(config, server_url):
    loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop); loop.run_until_complete(camera_async_worker(config, server_url))

def sender_manager_thread():
    global multi_sender_active, sender_state
    sender_threads = []
    while True:
        if sender_command_queue:
            cmd, url = sender_command_queue.popleft()
            if cmd == 'START' and not multi_sender_active:
                multi_sender_active = True; sender_state['status'] = 'Running'; write_log(f"Sender: Connect to {url}")
                for config in CAMERA_CONFIG:
                    c_thread = threading.Thread(target=cleanup_worker, args=(config,)); c_thread.daemon = True; c_thread.start()
                    s_thread = threading.Thread(target=start_async_loop, args=(config, url)); s_thread.daemon = True; s_thread.start()
                    sender_threads.extend([c_thread, s_thread])
            elif cmd == 'STOP' and multi_sender_active:
                multi_sender_active = False; sender_state['status'] = 'Stopped'; write_log("Sender: Disconnected"); sender_threads.clear()
        time.sleep(0.1)

def go1_v4_comm_thread():
    global go1_state, GO1_UNITY_IP, go1_unity_data
    if HAS_UNITREE_SDK:
        udp = sdk.UDP(HIGHLEVEL, LOCAL_PORT, ROBOT_IP, ROBOT_PORT)
        cmd = sdk.HighCmd(); state = sdk.HighState()
        udp.InitCmdData(cmd); go1_dashboard["hw_link"] = "Online"
    else: 
        udp = cmd = state = None; go1_dashboard["hw_link"] = "Simulation"
        
    sock_tx_state = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); sock_tx_cmd = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock_rx_unity = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); sock_rx_unity.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try: sock_rx_unity.bind(("0.0.0.0", UNITY_RX_PORT)); sock_rx_unity.setblocking(False)
    except: pass
    
    stand_only = True; now = time.monotonic(); last_key_time = last_move_cmd_time = grace_deadline = now
    use_grace = True; last_unity_cmd_time = now; unity_timeout_sec = 0.15
    yaw0_initialized = False; yaw0 = 0.0; UNITY_YAW_OFFSET_RAD = math.pi / 2.0
    world_x = world_z = 0.0; last_dr_time = now; seq = 0
    yaw_align_active = False; yaw_align_target_rel = 0.0; yaw_align_kp = 2.0; yaw_align_tol_rad = 2.0 * math.pi / 180.0

    def reset_cmd_base():
        if not cmd: return
        cmd.mode = 0; cmd.gaitType = 0; cmd.speedLevel = 0; cmd.footRaiseHeight = 0.08; cmd.bodyHeight = 0.0
        cmd.euler = [0.0, 0.0, 0.0]; cmd.velocity = [0.0, 0.0]; cmd.yawSpeed = 0.0; cmd.reserve = 0

    next_t = time.monotonic()
    while True:
        tnow = time.monotonic()
        if tnow < next_t: time.sleep(max(0.0, next_t - tnow))
        next_t += dt

        raw_yaw = 0.0
        if udp: 
            udp.Recv()
            udp.GetRecv(state)
            raw_yaw = float(state.imu.rpy[2])
            # ★ [v3 추가] SDK 통신 중 배터리(BMS SoC) 값 추출
            try: go1_state['battery'] = int(state.bms.soc)
            except: pass
        
        if not yaw0_initialized: yaw0 = raw_yaw; yaw0_initialized = True; last_dr_time = time.monotonic()
        if go1_node_intent['reset_yaw']: yaw0 = raw_yaw; last_dr_time = time.monotonic(); go1_node_intent['reset_yaw'] = False; write_log("YAW0 Reset")

        yaw_rel = wrap_pi(raw_yaw - yaw0); yaw_unity = wrap_pi(yaw_rel + UNITY_YAW_OFFSET_RAD); go1_state['yaw_unity'] = yaw_unity
        is_node_active = (tnow - go1_node_intent['trigger_time']) < 0.1
        if go1_node_intent['yaw_align']: yaw_align_active = True; stand_only = False; last_key_time = last_move_cmd_time = grace_deadline = tnow; use_grace = True; go1_node_intent['yaw_align'] = False
        if go1_node_intent['stop']: yaw_align_active = False; stand_only = True; last_key_time = last_move_cmd_time = grace_deadline = tnow; use_grace = True; go1_node_intent['stop'] = False
        elif is_node_active:
            yaw_align_active = False; stand_only = False; last_key_time = tnow; grace_deadline = tnow + repeat_grace_sec
            if abs(go1_node_intent['vx']) > 0 or abs(go1_node_intent['vy']) > 0 or abs(go1_node_intent['wz']) > 0: last_move_cmd_time = tnow

        got = None
        try:
            data, _ = sock_rx_unity.recvfrom(256)
            s = data.decode("utf-8", errors="ignore").strip().split()
            if len(s) >= 4: got = (float(s[0]), float(s[1]), float(s[2]), int(s[3]))
        except: pass
        
        uvx = uvy = uwz = uestop = 0
        if got: 
            uvx, uvy, uwz, uestop = got; last_unity_cmd_time = tnow; go1_dashboard['unity_link'] = "Active"
            go1_unity_data['vx'] = uvx; go1_unity_data['vy'] = uvy; go1_unity_data['wz'] = uwz; go1_unity_data['estop'] = uestop
            
        unity_active = go1_node_intent['use_unity_cmd'] and ((tnow - last_unity_cmd_time) <= unity_timeout_sec)
        go1_unity_data['active'] = unity_active
        if not unity_active: go1_dashboard['unity_link'] = "Waiting"

        since_key = tnow - last_key_time; since_move = tnow - last_move_cmd_time
        active_walk = ((not stand_only) and (since_key <= hold_timeout_sec)) or ((not stand_only) and use_grace and (tnow <= grace_deadline)) or ((not stand_only) and (since_move <= min_move_sec))

        reset_cmd_base(); target_mode = 1; out_vx = 0.0; out_vy = 0.0; out_wz = 0.0

        if yaw_align_active:
            err = wrap_pi(yaw_rel - yaw_align_target_rel)
            if abs(err) <= yaw_align_tol_rad: yaw_align_active = False; target_mode = 1
            else: target_mode = 2; out_wz = clamp(-yaw_align_kp * err, -W_MAX, W_MAX)
            if target_mode == 2 and cmd: cmd.gaitType = 1
        elif unity_active:
            target_mode = 2 if not uestop else 1
            if cmd: cmd.gaitType = 1
            out_vx = clamp(uvx, -V_MAX, V_MAX); out_vy = clamp(uvy, -S_MAX, S_MAX); out_wz = clamp(uwz, -W_MAX, W_MAX)
            go1_state['reason'] = "UNITY"
        elif active_walk:
            target_mode = 2
            if cmd: cmd.gaitType = 1
            out_vx = clamp(go1_node_intent['vx'], -V_MAX, V_MAX); out_vy = clamp(go1_node_intent['vy'], -S_MAX, S_MAX); out_wz = clamp(go1_node_intent['wz'], -W_MAX, W_MAX)
            go1_state['reason'] = "NODE_WALK"
        else:
            if since_move <= (min_move_sec + stop_brake_sec): 
                target_mode = 2; go1_state['reason'] = "BRAKE"
                if cmd: cmd.gaitType = 1
            else: target_mode = 1; use_grace = True; go1_state['reason'] = "STAND"

        if cmd: cmd.mode = target_mode; cmd.velocity = [out_vx, out_vy]; cmd.yawSpeed = out_wz; udp.SetSend(cmd); udp.Send()
            
        go1_state['vx_cmd'] = out_vx; go1_state['vy_cmd'] = out_vy; go1_state['wz_cmd'] = out_wz; go1_state['mode'] = target_mode
        dts = tnow - last_dr_time; last_dr_time = tnow
        cy = math.cos(yaw_unity); sy = math.sin(yaw_unity)
        world_x += (out_vx * cy - out_vy * sy) * dts; world_z += (out_vx * sy + out_vy * cy) * dts
        go1_state['world_x'] = world_x; go1_state['world_z'] = world_z

        estop = 1 if target_mode == 1 else 0; seq += 1
        msg_state = f"{seq} {time.time()*1000.0:.1f} {world_x:.6f} {world_z:.6f} {yaw_unity:.6f} {out_vx:.3f} {out_vy:.3f} {out_wz:.3f} {estop} {target_mode}"
        msg_cmd = f"{out_vx:.3f} {out_vy:.3f} {out_wz:.3f} {estop}"
        try: sock_tx_state.sendto(msg_state.encode("utf-8"), (GO1_UNITY_IP, UNITY_STATE_PORT)); sock_tx_cmd.sendto(msg_cmd.encode("utf-8"), (GO1_UNITY_IP, UNITY_CMD_PORT))
        except: pass

# ================= [MT4 Specific Nodes] =================
class MT4CommandActionNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "MT4 Action", "MT4_ACTION")
        self.combo_id = None; self.in_val1 = None; self.in_val2 = None; self.in_val3 = None; self.out_flow = None; self.field_v1 = None; self.field_v2 = None; self.field_v3 = None
    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label="MT4 Action"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as flow: dpg.add_text("Flow In"); self.inputs[flow] = "Flow"
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                self.combo_id = dpg.add_combo(["Move Relative (XYZ)", "Move Absolute (XYZ)", "Set Gripper (Abs)", "Grip Relative (Add)", "Homing"], default_value="Move Relative (XYZ)", width=150)
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as v1: dpg.add_text("X / Grip"); self.field_v1 = dpg.add_input_float(width=60, default_value=0); self.inputs[v1] = "Data"; self.in_val1 = v1
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as v2: dpg.add_text("Y"); self.field_v2 = dpg.add_input_float(width=60, default_value=0); self.inputs[v2] = "Data"; self.in_val2 = v2
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as v3: dpg.add_text("Z"); self.field_v3 = dpg.add_input_float(width=60, default_value=0); self.inputs[v3] = "Data"; self.in_val3 = v3
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out: dpg.add_text("Flow Out"); self.outputs[out] = "Flow"; self.out_flow = out
    def execute(self):
        global mt4_manual_override_until, mt4_target_goal, mt4_current_pos
        mt4_manual_override_until = time.time() + 1.0 
        mode = dpg.get_value(self.combo_id)
        v1 = self.fetch_input_data(self.in_val1); v1 = float(v1) if v1 is not None else dpg.get_value(self.field_v1)
        v2 = self.fetch_input_data(self.in_val2); v2 = float(v2) if v2 is not None else dpg.get_value(self.field_v2)
        v3 = self.fetch_input_data(self.in_val3); v3 = float(v3) if v3 is not None else dpg.get_value(self.field_v3)

        if mode.startswith("Move Rel"): mt4_target_goal['x'] += v1; mt4_target_goal['y'] += v2; mt4_target_goal['z'] += v3
        elif mode.startswith("Move Abs"): mt4_target_goal['x'] = v1; mt4_target_goal['y'] = v2; mt4_target_goal['z'] = v3
        elif mode.startswith("Set Grip"): mt4_target_goal['gripper'] = v1
        elif mode.startswith("Grip Rel"): mt4_target_goal['gripper'] += v1
        elif mode == "Homing": pass
        
        mt4_target_goal['x'] = max(MT4_LIMITS['min_x'], min(mt4_target_goal['x'], MT4_LIMITS['max_x'])); mt4_target_goal['y'] = max(MT4_LIMITS['min_y'], min(mt4_target_goal['y'], MT4_LIMITS['max_y']))
        mt4_target_goal['z'] = max(MT4_LIMITS['min_z'], min(mt4_target_goal['z'], MT4_LIMITS['max_z'])); mt4_target_goal['gripper'] = max(MT4_GRIPPER_MIN, min(mt4_target_goal['gripper'], MT4_GRIPPER_MAX))
        mt4_current_pos.update(mt4_target_goal)
        if ser and ser.is_open: ser.write(f"G0 X{mt4_target_goal['x']:.1f} Y{mt4_target_goal['y']:.1f} Z{mt4_target_goal['z']:.1f}\nM3 S{int(mt4_target_goal['gripper'])}\n".encode())
        return self.out_flow
    def get_settings(self): return {"mode": dpg.get_value(self.combo_id), "v1": dpg.get_value(self.field_v1), "v2": dpg.get_value(self.field_v2), "v3": dpg.get_value(self.field_v3)}
    def load_settings(self, data): dpg.set_value(self.combo_id, data.get("mode", "Move Relative (XYZ)")); dpg.set_value(self.field_v1, data.get("v1", 0)); dpg.set_value(self.field_v2, data.get("v2", 0)); dpg.set_value(self.field_v3, data.get("v3", 0))

class MT4KeyboardNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "Keyboard (MT4)", "MT4_KEYBOARD")
        self.out_x = None; self.out_y = None; self.out_z = None; self.out_g = None
        self.step_size = 10.0; self.grip_step = 5.0; self.cooldown = 0.2; self.last_input_time = 0.0
        self.combo_keys = None # ★ [v3 추가] 키 선택 콤보박스
    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label="MT4 Keyboard"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as flow: dpg.add_text("Flow In"); self.inputs[flow] = "Flow"
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                # ★ [v3 추가] 조작 모드 선택 UI
                self.combo_keys = dpg.add_combo(["WASD", "Arrow Keys"], default_value="WASD", width=120)
                dpg.add_text("XY Move / QE: Z / UJ: Grip", color=(255,150,150))
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as x: dpg.add_text("Target X"); self.outputs[x] = "Data"; self.out_x = x
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as y: dpg.add_text("Target Y"); self.outputs[y] = "Data"; self.out_y = y
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as z: dpg.add_text("Target Z"); self.outputs[z] = "Data"; self.out_z = z
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as g: dpg.add_text("Target Grip"); self.outputs[g] = "Data"; self.out_g = g
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as f: dpg.add_text("Flow Out"); self.outputs[f] = "Flow"
    def execute(self):
        global mt4_manual_override_until, mt4_target_goal
        if time.time() - self.last_input_time > self.cooldown:
            dx=0; dy=0; dz=0; dg=0
            # ★ [v3 수정] WASD 또는 방향키 선택 분기
            key_mode = dpg.get_value(self.combo_keys)
            if key_mode == "WASD":
                if dpg.is_key_down(dpg.mvKey_W): dx=1
                if dpg.is_key_down(dpg.mvKey_S): dx=-1
                if dpg.is_key_down(dpg.mvKey_A): dy=1
                if dpg.is_key_down(dpg.mvKey_D): dy=-1
            else:
                if dpg.is_key_down(dpg.mvKey_Up): dx=1
                if dpg.is_key_down(dpg.mvKey_Down): dx=-1
                if dpg.is_key_down(dpg.mvKey_Left): dy=1
                if dpg.is_key_down(dpg.mvKey_Right): dy=-1

            if dpg.is_key_down(dpg.mvKey_Q): dz=1
            if dpg.is_key_down(dpg.mvKey_E): dz=-1
            if dpg.is_key_down(dpg.mvKey_J): dg=1
            if dpg.is_key_down(dpg.mvKey_U): dg=-1
            if dx or dy or dz or dg:
                mt4_manual_override_until = time.time() + 0.5; self.last_input_time = time.time()
                mt4_target_goal['x']+=dx*self.step_size; mt4_target_goal['y']+=dy*self.step_size; mt4_target_goal['z']+=dz*self.step_size; mt4_target_goal['gripper']+=dg*self.grip_step
        self.output_data[self.out_x]=mt4_target_goal['x']; self.output_data[self.out_y]=mt4_target_goal['y']; self.output_data[self.out_z]=mt4_target_goal['z']; self.output_data[self.out_g]=mt4_target_goal['gripper']
        for k, v in self.outputs.items():
            if v == "Flow": return k
        return None
    def get_settings(self): return {"keys": dpg.get_value(self.combo_keys)}
    def load_settings(self, data): dpg.set_value(self.combo_keys, data.get("keys", "WASD"))

class MT4UnityNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "Unity Logic (MT4)", "MT4_UNITY_CONTROL")
        self.data_in_id = None; self.out_x = None; self.out_y = None; self.out_z = None; self.out_g = None
    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label="Unity Logic (MT4)"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as in_flow: dpg.add_text("Flow In"); self.inputs[in_flow] = "Flow"
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as d_in: dpg.add_text("JSON"); self.inputs[d_in] = "Data"; self.data_in_id = d_in
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out_x: dpg.add_text("Target X"); self.outputs[out_x] = "Data"; self.out_x = out_x
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out_y: dpg.add_text("Target Y"); self.outputs[out_y] = "Data"; self.out_y = out_y
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out_z: dpg.add_text("Target Z"); self.outputs[out_z] = "Data"; self.out_z = out_z
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out_g: dpg.add_text("Target Grip"); self.outputs[out_g] = "Data"; self.out_g = out_g
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as f_out: dpg.add_text("Flow Out"); self.outputs[f_out] = "Flow"
    def execute(self):
        if time.time() - mt4_dashboard.get("last_pkt_time", 0) > 0.5:
            self.output_data[self.out_x] = None; self.output_data[self.out_y] = None; self.output_data[self.out_z] = None; self.output_data[self.out_g] = None
            return self.outputs
        raw_json = self.fetch_input_data(self.data_in_id)
        if raw_json:
            try:
                parsed = json.loads(raw_json)
                if parsed.get("type", "MOVE") == "MOVE":
                    self.output_data[self.out_x] = parsed.get('z', 0) * 1000.0
                    self.output_data[self.out_y] = -parsed.get('x', 0) * 1000.0
                    self.output_data[self.out_z] = (parsed.get('y', 0) * 1000.0) + MT4_Z_OFFSET
                    self.output_data[self.out_g] = parsed.get('gripper') 
            except: pass 
        return self.outputs

class UDPReceiverNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "UDP Receiver", "UDP_RECV")
        self.out_flow = None; self.port = None; self.ip = None; self.out_json = None
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); self.sock.setblocking(False)
        self.is_bound = False; self.last_data_str = ""
    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label="UDP Receiver (MT4 JSON)"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as f: dpg.add_text("Flow In"); self.inputs[f]="Flow"
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static): dpg.add_input_int(label="Port", width=80, default_value=6000, tag=f"p_{self.node_id}"); self.port=f"p_{self.node_id}"
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static): dpg.add_input_text(label="IP", width=100, default_value="192.168.50.63", tag=f"i_{self.node_id}"); self.ip=f"i_{self.node_id}"
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as d: dpg.add_text("JSON Out"); self.outputs[d]="Data"; self.out_json=d
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as o: dpg.add_text("Flow Out"); self.outputs[o]="Flow"; self.out_flow=o
    def execute(self):
        global MT4_UNITY_IP
        port = dpg.get_value(self.port); MT4_UNITY_IP = dpg.get_value(self.ip)
        if not self.is_bound:
            try: self.sock.bind(('0.0.0.0', port)); self.is_bound = True; write_log(f"UDP: Bound to port {port}")
            except: self.is_bound = True
        try:
            while True: 
                data, _ = self.sock.recvfrom(4096); decoded = data.decode()
                if decoded != self.last_data_str:
                    self.output_data[self.out_json] = decoded; self.last_data_str = decoded
                    mt4_dashboard["last_pkt_time"] = time.time(); mt4_dashboard["status"] = "Connected"
        except: pass
        try:
            fb = {"x": -mt4_current_pos['y']/1000.0, "y": (mt4_current_pos['z'] - MT4_Z_OFFSET) / 1000.0, "z": mt4_current_pos['x']/1000.0, "gripper": mt4_current_pos['gripper'], "status": "Running"}
            sock_send = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock_send.sendto(json.dumps(fb).encode(), (MT4_UNITY_IP, MT4_FEEDBACK_PORT))
        except: pass
        return self.out_flow
    def get_settings(self): return {"port": dpg.get_value(self.port), "ip": dpg.get_value(self.ip)}
    def load_settings(self, data): dpg.set_value(self.port, data.get("port", 6000)); dpg.set_value(self.ip, data.get("ip", "192.168.50.63"))

# ================= [Go1 Specific Nodes] =================
class TargetIpNode(BaseNode):
    def __init__(self, node_id): super().__init__(node_id, "Target IP Config", "TARGET_IP"); self.field_ip = None; self.out_ip = None
    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label="Target IP (Receiver)"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static): dpg.add_text("Save Data To IP:"); self.field_ip = dpg.add_input_text(width=120, default_value=get_local_ip())
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out: dpg.add_text("IP String"); self.outputs[out] = "Data"; self.out_ip = out
    def execute(self): self.output_data[self.out_ip] = dpg.get_value(self.field_ip); return None
    def get_settings(self): return {"ip": dpg.get_value(self.field_ip)}
    def load_settings(self, data): dpg.set_value(self.field_ip, data.get("ip", get_local_ip()))

class CameraControlNode(BaseNode):
    def __init__(self, node_id): super().__init__(node_id, "Camera Control", "CAM_CTRL"); self.combo_action = None; self.in_ip = None; self.out_flow = None
    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label="Camera Control (Go1)"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as flow: dpg.add_text("Flow In"); self.inputs[flow] = "Flow"
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static): self.combo_action = dpg.add_combo(["Start Stream", "Stop Stream"], default_value="Start Stream", width=120); dpg.add_spacer(height=3)
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as ip_in: dpg.add_text("Target IP In", color=(255,150,200)); self.inputs[ip_in] = "Data"; self.in_ip = ip_in
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out: dpg.add_text("Flow Out"); self.outputs[out] = "Flow"; self.out_flow = out
    def execute(self):
        action = dpg.get_value(self.combo_action); ext_ip = self.fetch_input_data(self.in_ip); target_ip = ext_ip if ext_ip else get_local_ip()
        if action == "Start Stream" and camera_state['status'] in ['Stopped', 'Stopping...']: camera_command_queue.append(('START', target_ip))
        elif action == "Stop Stream" and camera_state['status'] in ['Running', 'Starting...']: camera_command_queue.append(('STOP', target_ip))
        return self.out_flow
    def get_settings(self): return {"act": dpg.get_value(self.combo_action)}
    def load_settings(self, data): dpg.set_value(self.combo_action, data.get("act", "Start Stream"))

class MultiSenderNode(BaseNode):
    def __init__(self, node_id): super().__init__(node_id, "AI Server Sender", "MULTI_SENDER"); self.combo_action = None; self.field_url = None; self.out_flow = None
    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label="AI Server Sender (Go1)"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as flow: dpg.add_text("Flow In"); self.inputs[flow] = "Flow"
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static): self.combo_action = dpg.add_combo(["Start Sender", "Stop Sender"], default_value="Start Sender", width=140); dpg.add_spacer(height=3); dpg.add_text("AI Server URL:"); self.field_url = dpg.add_input_text(width=160, default_value="http://210.110.250.33:5001/upload")
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out: dpg.add_text("Flow Out"); self.outputs[out] = "Flow"; self.out_flow = out
    def execute(self):
        action = dpg.get_value(self.combo_action); url = dpg.get_value(self.field_url)
        if action == "Start Sender" and sender_state['status'] == 'Stopped': sender_command_queue.append(('START', url))
        elif action == "Stop Sender" and sender_state['status'] == 'Running': sender_command_queue.append(('STOP', url))
        return self.out_flow
    def get_settings(self): return {"act": dpg.get_value(self.combo_action), "url": dpg.get_value(self.field_url)}
    def load_settings(self, data): dpg.set_value(self.combo_action, data.get("act", "Start Sender")); dpg.set_value(self.field_url, data.get("url", "http://210.110.250.33:5001/upload"))

class Go1UnityNode(BaseNode):
    def __init__(self, node_id): super().__init__(node_id, "Unity Link", "GO1_UNITY_CONTROL"); self.field_ip = None; self.chk_enable = None; self.out_vx = None; self.out_vy = None; self.out_wz = None; self.out_active = None
    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label="Unity Link (Go1)"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static): dpg.add_text("Unity PC IP:", color=(100,255,100)); self.field_ip = dpg.add_input_text(width=120, default_value=GO1_UNITY_IP); dpg.add_spacer(height=3); self.chk_enable = dpg.add_checkbox(label="Enable Teleop Rx", default_value=True)
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as vx: dpg.add_text("Teleop Vx"); self.outputs[vx] = "Data"; self.out_vx = vx
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as vy: dpg.add_text("Teleop Vy"); self.outputs[vy] = "Data"; self.out_vy = vy
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as wz: dpg.add_text("Teleop Wz"); self.outputs[wz] = "Data"; self.out_wz = wz
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as act: dpg.add_text("Is Active?"); self.outputs[act] = "Data"; self.out_active = act
    def execute(self):
        global GO1_UNITY_IP, go1_node_intent
        GO1_UNITY_IP = dpg.get_value(self.field_ip); go1_node_intent['use_unity_cmd'] = dpg.get_value(self.chk_enable)
        self.output_data[self.out_vx] = go1_unity_data['vx']; self.output_data[self.out_vy] = go1_unity_data['vy']; self.output_data[self.out_wz] = go1_unity_data['wz']; self.output_data[self.out_active] = go1_unity_data['active']
        return None
    def get_settings(self): return {"ip": dpg.get_value(self.field_ip), "en": dpg.get_value(self.chk_enable)}
    def load_settings(self, data): dpg.set_value(self.field_ip, data.get("ip", "192.168.50.246")); dpg.set_value(self.chk_enable, data.get("en", True))

class Go1CommandActionNode(BaseNode):
    def __init__(self, node_id): super().__init__(node_id, "Go1 Action", "GO1_ACTION"); self.combo_id = None; self.in_val1 = None; self.out_flow = None; self.field_v1 = None
    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label="Go1 Action"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as flow: dpg.add_text("Flow In"); self.inputs[flow] = "Flow"
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static): self.combo_id = dpg.add_combo(items=["Stand", "Reset Yaw0", "Walk Fwd/Back", "Walk Strafe", "Turn"], default_value="Stand", width=130)
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as v1: dpg.add_text("Speed/Val"); self.field_v1 = dpg.add_input_float(width=60, default_value=0.2); self.inputs[v1] = "Data"; self.in_val1 = v1
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out: dpg.add_text("Flow Out"); self.outputs[out] = "Flow"; self.out_flow = out
    def execute(self):
        global go1_node_intent; mode = dpg.get_value(self.combo_id); v1 = self.fetch_input_data(self.in_val1); v1 = float(v1) if v1 is not None else dpg.get_value(self.field_v1)
        if mode == "Stand": go1_node_intent['stop'] = True
        elif mode == "Reset Yaw0": go1_node_intent['reset_yaw'] = True
        else: go1_node_intent['vx'] = v1 if mode == "Walk Fwd/Back" else 0.0; go1_node_intent['vy'] = v1 if mode == "Walk Strafe" else 0.0; go1_node_intent['wz'] = v1 if mode == "Turn" else 0.0; go1_node_intent['trigger_time'] = time.monotonic()
        return self.out_flow
    def get_settings(self): return {"mode": dpg.get_value(self.combo_id), "v1": dpg.get_value(self.field_v1)}
    def load_settings(self, data): dpg.set_value(self.combo_id, data.get("mode", "Stand")); dpg.set_value(self.field_v1, data.get("v1", 0.2))

class GetGo1StateNode(BaseNode):
    def __init__(self, node_id): super().__init__(node_id, "Get Go1 State", "GET_GO1_STATE"); self.out_x = None; self.out_z = None; self.out_yaw = None
    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label="Get Go1 State"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as x: dpg.add_text("World X"); self.outputs[x] = "Data"; self.out_x = x
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as z: dpg.add_text("World Z"); self.outputs[z] = "Data"; self.out_z = z
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as y: dpg.add_text("Yaw (rad)"); self.outputs[y] = "Data"; self.out_yaw = y
    def execute(self): self.output_data[self.out_x] = go1_state['world_x']; self.output_data[self.out_z] = go1_state['world_z']; self.output_data[self.out_yaw] = go1_state['yaw_unity']; return None

class Go1KeyboardNode(BaseNode):
    def __init__(self, node_id): super().__init__(node_id, "Keyboard (Go1)", "GO1_KEYBOARD"); self.out_vx = None; self.out_vy = None; self.out_wz = None; self.combo_keys = None
    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label="Keyboard (Go1 Intent)"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as flow: dpg.add_text("Flow In"); self.inputs[flow] = "Flow"
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                # ★ [v3 추가] 조작 모드 선택 UI
                self.combo_keys = dpg.add_combo(["WASD", "Arrow Keys"], default_value="WASD", width=120)
                dpg.add_text("Move / QE: Turn\nSpace: Stop / R: Yaw Align", color=(255,150,150))
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as vx: dpg.add_text("Target Vx"); self.outputs[vx] = "Data"; self.out_vx = vx
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as vy: dpg.add_text("Target Vy"); self.outputs[vy] = "Data"; self.out_vy = vy
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as wz: dpg.add_text("Target Wz"); self.outputs[wz] = "Data"; self.out_wz = wz
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as f: dpg.add_text("Flow Out"); self.outputs[f] = "Flow"
    def execute(self):
        global go1_node_intent; vx = 0.0; vy = 0.0; wz = 0.0
        
        # ★ [v3 수정] WASD 또는 방향키 선택 분기
        key_mode = dpg.get_value(self.combo_keys)
        if key_mode == "WASD":
            if dpg.is_key_down(dpg.mvKey_W): vx = VX_CMD
            if dpg.is_key_down(dpg.mvKey_S): vx = -VX_CMD
            if dpg.is_key_down(dpg.mvKey_A): vy = VY_CMD
            if dpg.is_key_down(dpg.mvKey_D): vy = -VY_CMD
        else:
            if dpg.is_key_down(dpg.mvKey_Up): vx = VX_CMD
            if dpg.is_key_down(dpg.mvKey_Down): vx = -VX_CMD
            if dpg.is_key_down(dpg.mvKey_Left): vy = VY_CMD
            if dpg.is_key_down(dpg.mvKey_Right): vy = -VY_CMD

        if dpg.is_key_down(dpg.mvKey_Q): wz = WZ_CMD
        if dpg.is_key_down(dpg.mvKey_E): wz = -WZ_CMD
        if dpg.is_key_down(dpg.mvKey_Spacebar): go1_node_intent['stop'] = True
        if dpg.is_key_pressed(dpg.mvKey_R): go1_node_intent['yaw_align'] = True
        if dpg.is_key_pressed(dpg.mvKey_Z): go1_node_intent['reset_yaw'] = True
        
        if vx or vy or wz: go1_node_intent['vx'] = vx; go1_node_intent['vy'] = vy; go1_node_intent['wz'] = wz; go1_node_intent['trigger_time'] = time.monotonic()
        self.output_data[self.out_vx]=vx; self.output_data[self.out_vy]=vy; self.output_data[self.out_wz]=wz
        for k, v in self.outputs.items():
            if v == "Flow": return k
        return None
    def get_settings(self): return {"keys": dpg.get_value(self.combo_keys)}
    def load_settings(self, data): dpg.set_value(self.combo_keys, data.get("keys", "WASD"))

# ================= [Universal / Logic Nodes] =================
class StartNode(BaseNode):
    def __init__(self, node_id): super().__init__(node_id, "START", "START")
    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label="START"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out: dpg.add_text("Flow Out"); self.outputs[out] = "Flow"; self.out = out
    def execute(self): return self.out 

class LogicIfNode(BaseNode):
    def __init__(self, node_id): super().__init__(node_id, "Logic: IF", "LOGIC_IF"); self.in_cond = None; self.out_true = None; self.out_false = None
    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label="IF Condition"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as flow: dpg.add_text("Flow In"); self.inputs[flow] = "Flow"
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as cond: dpg.add_text("Condition", color=(255,100,100)); self.inputs[cond] = "Data"; self.in_cond = cond
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as t: dpg.add_text("True", color=(100,255,100)); self.outputs[t] = "Flow"; self.out_true = t
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as f: dpg.add_text("False", color=(255,100,100)); self.outputs[f] = "Flow"; self.out_false = f
    def execute(self):
        target_link = None
        for link in link_registry.values():
            if link['target'] == self.in_cond: target_link = link; break
        if target_link:
            src_node_id = dpg.get_item_parent(target_link['source'])
            if src_node_id in node_registry and node_registry[src_node_id].type_str.startswith("COND_"): node_registry[src_node_id].execute()
        return self.out_true if self.fetch_input_data(self.in_cond) else self.out_false

class LogicLoopNode(BaseNode):
    def __init__(self, node_id): super().__init__(node_id, "Logic: LOOP", "LOGIC_LOOP"); self.field_count = None; self.out_loop = None; self.out_finish = None; self.current_iter = 0; self.target_iter = 0; self.is_active = False
    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label="For Loop"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as flow: dpg.add_text("Flow In"); self.inputs[flow] = "Flow"
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static): dpg.add_text("Count:"); self.field_count = dpg.add_input_int(width=80, default_value=3, min_value=1)
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as l: dpg.add_text("Loop Body", color=(100,200,255)); self.outputs[l] = "Flow"; self.out_loop = l
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as f: dpg.add_text("Finished", color=(200,200,200)); self.outputs[f] = "Flow"; self.out_finish = f
    def execute(self):
        if not self.is_active: self.target_iter = dpg.get_value(self.field_count); self.current_iter = 0; self.is_active = True
        if self.current_iter < self.target_iter: self.current_iter += 1; return self.out_loop 
        else: self.is_active = False; return self.out_finish
    def get_settings(self): return {"count": dpg.get_value(self.field_count)}
    def load_settings(self, data): dpg.set_value(self.field_count, data.get("count", 3))

class ConditionKeyNode(BaseNode):
    def __init__(self, node_id): super().__init__(node_id, "Check: Key", "COND_KEY"); self.field_key = None; self.out_res = None; self.key_map = {"A": dpg.mvKey_A, "B": dpg.mvKey_B, "C": dpg.mvKey_C, "S": dpg.mvKey_S, "W": dpg.mvKey_W, "SPACE": dpg.mvKey_Spacebar} 
    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label="Key Check"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static): dpg.add_text("Key (A-Z):"); self.field_key = dpg.add_input_text(width=60, default_value="A")
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as res: dpg.add_text("Is Pressed?"); self.outputs[res] = "Data"; self.out_res = res
    def execute(self): k = dpg.get_value(self.field_key).upper(); self.output_data[self.out_res] = dpg.is_key_down(self.key_map.get(k, 0)); return None
    def get_settings(self): return {"k": dpg.get_value(self.field_key)}
    def load_settings(self, data): dpg.set_value(self.field_key, data.get("k", "A"))

class ConstantNode(BaseNode):
    def __init__(self, node_id): super().__init__(node_id, "Constant", "CONSTANT"); self.out_val = None; self.field_val = None
    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label="Constant"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static): self.field_val = dpg.add_input_float(width=80, default_value=1.0)
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out: dpg.add_text("Data"); self.outputs[out] = "Data"; self.out_val = out
    def execute(self): self.output_data[self.out_val] = dpg.get_value(self.field_val); return None
    def get_settings(self): return {"val": dpg.get_value(self.field_val)}
    def load_settings(self, data): dpg.set_value(self.field_val, data.get("val", 1.0))

class PrintNode(BaseNode):
    def __init__(self, node_id): super().__init__(node_id, "Print Log", "PRINT"); self.out_flow = None; self.inp_data = None
    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label="Print Log"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as flow: dpg.add_text("Flow In"); self.inputs[flow] = "Flow"
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as data: dpg.add_text("Data"); self.inputs[data] = "Data"; self.inp_data = data
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out: dpg.add_text("Flow Out"); self.outputs[out] = "Flow"; self.out_flow = out
    def execute(self):
        val = self.fetch_input_data(self.inp_data)
        if val is not None: write_log(f"PRINT: {val}")
        return self.out_flow

class LoggerNode(BaseNode):
    def __init__(self, node_id): super().__init__(node_id, "System Log", "LOGGER"); self.txt=None; self.llen=0
    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label="System Log (Flowless)"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                with dpg.child_window(width=200, height=100): self.txt=dpg.add_text("", wrap=190)
    def execute(self):
        if len(system_log_buffer)!=self.llen: dpg.set_value(self.txt, "\n".join(list(system_log_buffer)[-8:])); self.llen=len(system_log_buffer)
        return None

# ================= [Execution Engine (Hybrid)] =================
def execute_graph_once():
    start_node = None
    for node in node_registry.values():
        if isinstance(node, StartNode): start_node = node; break
    
    for node in node_registry.values():
        if not isinstance(node, (StartNode, MT4CommandActionNode, Go1CommandActionNode, LogicIfNode, LogicLoopNode, ConditionKeyNode)):
            try: node.execute()
            except: pass

    if not start_node: return

    current_node = start_node
    steps = 0; MAX_STEPS = 100 
    while current_node and steps < MAX_STEPS:
        result = current_node.execute()
        next_out_id = None
        if result is not None:
            if isinstance(result, (int, str)): next_out_id = result
            elif isinstance(result, dict):
                for k, v in result.items():
                    if v == "Flow": next_out_id = k; break
        next_node = None
        if next_out_id:
            for link in link_registry.values():
                if link['source'] == next_out_id:
                    target_node_id = dpg.get_item_parent(link['target'])
                    if target_node_id in node_registry:
                        next_node = node_registry[target_node_id]; break
        current_node = next_node; steps += 1

# ================= [Factory & Serialization] =================
class NodeFactory:
    @staticmethod
    def create_node(node_type, node_id=None):
        if node_id is None: node_id = dpg.generate_uuid()
        node = None
        if node_type == "START": node = StartNode(node_id)
        elif node_type == "LOGIC_IF": node = LogicIfNode(node_id)
        elif node_type == "LOGIC_LOOP": node = LogicLoopNode(node_id)
        elif node_type == "COND_KEY": node = ConditionKeyNode(node_id)
        elif node_type == "CONSTANT": node = ConstantNode(node_id)
        elif node_type == "PRINT": node = PrintNode(node_id)
        elif node_type == "LOGGER": node = LoggerNode(node_id)
        elif node_type == "MT4_DRIVER": node = UniversalRobotNode(node_id, MT4RobotDriver())
        elif node_type == "MT4_ACTION": node = MT4CommandActionNode(node_id)
        elif node_type == "MT4_KEYBOARD": node = MT4KeyboardNode(node_id)
        elif node_type == "MT4_UNITY": node = MT4UnityNode(node_id)
        elif node_type == "UDP_RECV": node = UDPReceiverNode(node_id)
        elif node_type == "GO1_DRIVER": node = UniversalRobotNode(node_id, Go1RobotDriver())
        elif node_type == "GO1_ACTION": node = Go1CommandActionNode(node_id)
        elif node_type == "GO1_KEYBOARD": node = Go1KeyboardNode(node_id)
        elif node_type == "GO1_UNITY": node = Go1UnityNode(node_id)
        elif node_type == "CAM_CTRL": node = CameraControlNode(node_id)
        elif node_type == "TARGET_IP": node = TargetIpNode(node_id)
        elif node_type == "MULTI_SENDER": node = MultiSenderNode(node_id)
        elif node_type == "GET_GO1_STATE": node = GetGo1StateNode(node_id)
        
        if node: node.build_ui(); node_registry[node_id] = node; return node
        return None

def toggle_exec(s, a): global is_running; is_running = not is_running; dpg.set_item_label("btn_run", "STOP" if is_running else "RUN SCRIPT")
def link_cb(s, a): src, dst = a[0], a[1] if len(a)==2 else a[1]; lid = dpg.add_node_link(src, dst, parent=s); link_registry[lid] = {'source': src, 'target': dst}
def del_link_cb(s, a): dpg.delete_item(a); link_registry.pop(a, None)
def add_node_cb(s, a, u): NodeFactory.create_node(u)
def save_cb(s, a): save_graph(dpg.get_value("file_name_input"))
def load_cb(s, a): load_graph(dpg.get_value("file_list_combo"))

def save_graph(filename):
    if not filename.endswith(".json"): filename += ".json"
    filepath = os.path.join(SAVE_DIR, filename)
    data = {"nodes": [], "links": []}
    for nid, node in node_registry.items():
        pos = dpg.get_item_pos(nid) or [0,0]
        data["nodes"].append({"type": node.type_str, "id": nid, "pos": pos, "settings": node.get_settings()})
    for lid, link in link_registry.items():
        src_node_id, dst_node_id = dpg.get_item_parent(link['source']), dpg.get_item_parent(link['target'])
        if src_node_id in node_registry and dst_node_id in node_registry:
            src_idx = list(node_registry[src_node_id].outputs.keys()).index(link['source'])
            dst_idx = list(node_registry[dst_node_id].inputs.keys()).index(link['target'])
            data["links"].append({"src_node": src_node_id, "src_idx": src_idx, "dst_node": dst_node_id, "dst_idx": dst_idx})
    try:
        with open(filepath, 'w') as f: json.dump(data, f, indent=4)
        write_log(f"Saved: {filename}"); update_file_list()
    except Exception as e: write_log(f"Save Err: {e}")

def load_graph(filename):
    if not filename.endswith(".json"): filename += ".json"
    filepath = os.path.join(SAVE_DIR, filename)
    if not os.path.exists(filepath): return
    for lid in list(link_registry.keys()): dpg.delete_item(lid)
    for nid in list(node_registry.keys()): dpg.delete_item(nid)
    link_registry.clear(); node_registry.clear()
    try:
        with open(filepath, 'r') as f: data = json.load(f)
        id_map = {}
        for n_data in data["nodes"]:
            node = NodeFactory.create_node(n_data["type"], None) 
            if node:
                id_map[n_data["id"]] = node.node_id
                dpg.set_item_pos(node.node_id, n_data["pos"] if n_data["pos"] else [0,0])
                node.load_settings(n_data.get("settings", {}))
        for l_data in data["links"]:
            if l_data["src_node"] in id_map and l_data["dst_node"] in id_map:
                src_node = node_registry[id_map[l_data["src_node"]]]
                dst_node = node_registry[id_map[l_data["dst_node"]]]
                src_attr = list(src_node.outputs.keys())[l_data["src_idx"]]
                dst_attr = list(dst_node.inputs.keys())[l_data["dst_idx"]]
                lid = dpg.add_node_link(src_attr, dst_attr, parent="node_editor")
                link_registry[lid] = {'source': src_attr, 'target': dst_attr}
        write_log(f"Loaded: {filename}")
    except Exception as e: write_log(f"Load Err: {e}")

def update_file_list(): dpg.configure_item("file_list_combo", items=get_save_files())
def delete_selection(sender, app_data):
    selected_links = dpg.get_selected_links("node_editor"); selected_nodes = dpg.get_selected_nodes("node_editor")
    for lid in selected_links:
        if lid in link_registry: del link_registry[lid]
        if dpg.does_item_exist(lid): dpg.delete_item(lid)
    for nid in selected_nodes:
        if nid not in node_registry: continue
        node = node_registry[nid]; my_ports = set(node.inputs.keys()) | set(node.outputs.keys()); links_to_remove = []
        for lid, ldata in link_registry.items():
            if ldata['source'] in my_ports or ldata['target'] in my_ports: links_to_remove.append(lid)
        for lid in links_to_remove:
            if lid in link_registry: del link_registry[lid]
            if dpg.does_item_exist(lid): dpg.delete_item(lid)
        del node_registry[nid]
        if dpg.does_item_exist(nid): dpg.delete_item(nid)

# ================= [Main Setup] =================
init_mt4_serial()
threading.Thread(target=auto_reconnect_mt4_thread, daemon=True).start()
threading.Thread(target=go1_v4_comm_thread, daemon=True).start()
threading.Thread(target=camera_worker_thread, daemon=True).start()
threading.Thread(target=sender_manager_thread, daemon=True).start()
threading.Thread(target=lambda: (time.sleep(1), update_file_list()), daemon=True).start()

dpg.create_context()
with dpg.handler_registry(): dpg.add_key_press_handler(dpg.mvKey_Delete, callback=delete_selection)

# ★ [v3 추가] E-STOP 버튼용 테마 설정 (빨간색)
with dpg.theme() as estop_theme:
    with dpg.theme_component(dpg.mvButton):
        dpg.add_theme_color(dpg.mvThemeCol_Button, (200, 50, 50))
        dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (255, 100, 100))
        dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (150, 30, 30))

with dpg.window(tag="PrimaryWindow"):
    with dpg.tab_bar():
        with dpg.tab(label="MT4 Dashboard"):
            with dpg.group(horizontal=True):
                with dpg.child_window(width=250, height=130, border=True):
                    dpg.add_text("MT4 Status", color=(150,150,150)); dpg.add_text("Idle", tag="mt4_dash_status", color=(0,255,0))
                    dpg.add_text(mt4_dashboard["hw_link"], tag="mt4_dash_link", color=(0,255,0) if mt4_dashboard["hw_link"]=="Online" else (255,0,0))
                    dpg.add_text("0.0 ms", tag="mt4_dash_latency", color=(255,255,0))
                with dpg.child_window(width=350, height=130, border=True):
                    dpg.add_text("Manual Control", color=(255,200,0))
                    with dpg.group(horizontal=True):
                        dpg.add_button(label="X+", width=60, callback=mt4_manual_control_callback, user_data=('x', 10)); dpg.add_button(label="X-", width=60, callback=mt4_manual_control_callback, user_data=('x', -10))
                        dpg.add_text("|"); dpg.add_button(label="Y+", width=60, callback=mt4_manual_control_callback, user_data=('y', 10)); dpg.add_button(label="Y-", width=60, callback=mt4_manual_control_callback, user_data=('y', -10))
                    with dpg.group(horizontal=True):
                        dpg.add_button(label="Z+", width=60, callback=mt4_manual_control_callback, user_data=('z', 10)); dpg.add_button(label="Z-", width=60, callback=mt4_manual_control_callback, user_data=('z', -10))
                        dpg.add_text("|"); dpg.add_button(label="G+", width=60, callback=mt4_manual_control_callback, user_data=('gripper', 5)); dpg.add_button(label="G-", width=60, callback=mt4_manual_control_callback, user_data=('gripper', -5))
                with dpg.child_window(width=300, height=130, border=True):
                    dpg.add_text("Direct Coord", color=(0,255,255))
                    with dpg.group(horizontal=True):
                        dpg.add_text("X"); dpg.add_input_int(tag="input_x", width=50, default_value=200, step=0)
                        dpg.add_text("Y"); dpg.add_input_int(tag="input_y", width=50, default_value=0, step=0)
                    with dpg.group(horizontal=True):
                        dpg.add_text("Z"); dpg.add_input_int(tag="input_z", width=50, default_value=120, step=0)
                        dpg.add_text("G"); dpg.add_input_int(tag="input_g", width=50, default_value=40, step=0)
                    with dpg.group(horizontal=True):
                        dpg.add_button(label="Move", width=100, callback=mt4_move_to_coord_callback)
                        dpg.add_button(label="Homing", width=100, callback=mt4_homing_callback)
                with dpg.child_window(width=150, height=130, border=True):
                    dpg.add_text("Coords", color=(0,255,255))
                    dpg.add_text("X: 0", tag="mt4_x"); dpg.add_text("Y: 0", tag="mt4_y")
                    dpg.add_text("Z: 0", tag="mt4_z"); dpg.add_text("G: 0", tag="mt4_g")

        with dpg.tab(label="Go1 Dashboard"):
            with dpg.group(horizontal=True):
                with dpg.child_window(width=250, height=140, border=True):
                    dpg.add_text("Go1 Status", color=(150,150,150))
                    dpg.add_text(f"HW: {go1_dashboard['hw_link']}", tag="go1_dash_link", color=(0,255,0))
                    dpg.add_text(f"Unity: Waiting", tag="go1_dash_unity", color=(255,255,0))
                    dpg.add_text(f"Camera: Stopped", tag="go1_dash_cam", color=(200,200,200))
                    # ★ [v3 추가] 배터리 텍스트 표시 및 긴급 정지 버튼
                    dpg.add_text("Battery: -%", tag="go1_dash_battery", color=(100,255,100))
                    btn_estop = dpg.add_button(label="[ EMERGENCY STOP ]", width=-1, height=25, callback=go1_estop_callback)
                    dpg.bind_item_theme(btn_estop, estop_theme)

                with dpg.child_window(width=300, height=140, border=True):
                    dpg.add_text("Odometry", color=(0,255,255))
                    dpg.add_text("World X: 0.000", tag="go1_dash_wx")
                    dpg.add_text("World Z: 0.000", tag="go1_dash_wz")
                    dpg.add_text("Yaw: 0.000 rad", tag="go1_dash_yaw")
                    dpg.add_text("Reason: NONE", tag="go1_dash_reason", color=(200,200,200))
                with dpg.child_window(width=250, height=140, border=True):
                    dpg.add_text("Commands", color=(255,200,0))
                    dpg.add_text("Vx Cmd: 0.00", tag="go1_dash_vx_2")
                    dpg.add_text("Vy Cmd: 0.00", tag="go1_dash_vy_2")
                    dpg.add_text("Wz Cmd: 0.00", tag="go1_dash_wz_2")

        with dpg.tab(label="Files & System"):
            with dpg.group(horizontal=True):
                with dpg.child_window(width=650, height=100, border=True):
                    dpg.add_text("File Manager", color=(0,255,255))
                    with dpg.group(horizontal=True):
                        dpg.add_text("Save:"); dpg.add_input_text(tag="file_name_input", default_value="my_graph", width=120); dpg.add_button(label="SAVE", callback=save_cb, width=60)
                        dpg.add_spacer(width=20)
                        dpg.add_text("Load:"); dpg.add_combo(items=get_save_files(), tag="file_list_combo", width=120); dpg.add_button(label="LOAD", callback=load_cb, width=60); dpg.add_button(label="Refresh", callback=update_file_list, width=60)
                with dpg.child_window(width=400, height=100, border=False):
                    dpg.add_spacer(height=20)
                    dpg.add_text(f"My IP: {get_local_ip()} | SSID: {get_wifi_ssid()}", color=(180,180,180))

    dpg.add_separator()
    with dpg.group(horizontal=True):
        dpg.add_button(label="START", callback=add_node_cb, user_data="START")
        dpg.add_button(label="LOG", callback=add_node_cb, user_data="LOGGER")
        dpg.add_button(label="IF", callback=add_node_cb, user_data="LOGIC_IF")
        dpg.add_button(label="LOOP", callback=add_node_cb, user_data="LOGIC_LOOP")
        dpg.add_spacer(width=20)
        
        dpg.add_text("MT4 Tools:", color=(255,200,0))
        dpg.add_button(label="DRIVER(MT4)", callback=add_node_cb, user_data="MT4_DRIVER")
        dpg.add_button(label="ACTION(MT4)", callback=add_node_cb, user_data="MT4_ACTION")
        dpg.add_button(label="KEY(MT4)", callback=add_node_cb, user_data="MT4_KEYBOARD")
        dpg.add_button(label="UNITY(MT4)", callback=add_node_cb, user_data="MT4_UNITY")
        dpg.add_button(label="UDP(MT4)", callback=add_node_cb, user_data="UDP_RECV")
        dpg.add_spacer(width=20)
        
        dpg.add_text("Go1 Tools:", color=(0,255,255))
        dpg.add_button(label="DRIVER(Go1)", callback=add_node_cb, user_data="GO1_DRIVER")
        dpg.add_button(label="ACTION(Go1)", callback=add_node_cb, user_data="GO1_ACTION")
        dpg.add_button(label="KEY(Go1)", callback=add_node_cb, user_data="GO1_KEYBOARD")
        dpg.add_button(label="UNITY(Go1)", callback=add_node_cb, user_data="GO1_UNITY")
        dpg.add_button(label="CAM_CTRL", callback=add_node_cb, user_data="CAM_CTRL")
        dpg.add_button(label="AI_SENDER", callback=add_node_cb, user_data="MULTI_SENDER")

        dpg.add_spacer(width=50)
        dpg.add_button(label="RUN SCRIPT", tag="btn_run", callback=toggle_exec, width=150)

    with dpg.node_editor(tag="node_editor", callback=link_cb, delink_callback=del_link_cb): pass

dpg.create_viewport(title='PyGui V3 (E-STOP & Advanced Setup)', width=1280, height=800, vsync=True)
dpg.setup_dearpygui(); dpg.set_primary_window("PrimaryWindow", True); dpg.show_viewport()

last_logic_time = 0; LOGIC_RATE = 0.02

while dpg.is_dearpygui_running():
    if mt4_dashboard["last_pkt_time"] > 0:
        dpg.set_value("mt4_dash_status", mt4_dashboard["status"])
    dpg.set_value("mt4_x", f"X: {mt4_current_pos['x']:.1f}")
    dpg.set_value("mt4_y", f"Y: {mt4_current_pos['y']:.1f}")
    dpg.set_value("mt4_z", f"Z: {mt4_current_pos['z']:.1f}")
    dpg.set_value("mt4_g", f"G: {mt4_current_pos['gripper']:.1f}")
    
    dpg.set_value("go1_dash_unity", f"Unity: {go1_dashboard['unity_link']}")
    dpg.set_value("go1_dash_reason", f"Mode: {go1_state['mode']} | {go1_state['reason']}")
    dpg.set_value("go1_dash_wx", f"World X: {go1_state['world_x']:.3f}")
    dpg.set_value("go1_dash_wz", f"World Z: {go1_state['world_z']:.3f}")
    dpg.set_value("go1_dash_yaw", f"Yaw: {go1_state['yaw_unity']:.3f} rad")
    dpg.set_value("go1_dash_vx_2", f"Vx Cmd: {go1_state['vx_cmd']:.2f}")
    dpg.set_value("go1_dash_vy_2", f"Vy Cmd: {go1_state['vy_cmd']:.2f}")
    dpg.set_value("go1_dash_wz_2", f"Wz Cmd: {go1_state['wz_cmd']:.2f}")
    
    # ★ [v3 추가] 배터리 상태 업데이트 UI (시뮬레이션 모드 대응)
    bat_val = go1_state.get('battery', -1)
    if bat_val >= 0:
        dpg.set_value("go1_dash_battery", f"Battery: {bat_val}%")
    else:
        dpg.set_value("go1_dash_battery", "Battery: 100% (Sim)")
    
    dpg.set_value("go1_dash_cam", f"Camera: {camera_state['status']}")
    if camera_state['status'] == 'Running': dpg.configure_item("go1_dash_cam", color=(0,255,0))
    elif camera_state['status'] == 'Stopped': dpg.configure_item("go1_dash_cam", color=(200,200,200))
    else: dpg.configure_item("go1_dash_cam", color=(255,200,0))
    
    if is_running and (time.time() - last_logic_time > LOGIC_RATE):
        execute_graph_once()
        last_logic_time = time.time()
        
    dpg.render_dearpygui_frame()

dpg.destroy_context()