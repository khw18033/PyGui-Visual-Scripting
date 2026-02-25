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
import platform 
import dearpygui.dearpygui as dpg
import csv
from collections import deque
from abc import ABC, abstractmethod
from datetime import datetime

latest_processed_frames = {} # ★ 각 카메라의 완성된 이미지를 메모리에 임시 보관할 딕셔너리

# ================= [OpenCV & Flask Import (V4/V5 ArUco)] =================
try:
    import cv2
    import numpy as np
    from flask import Flask, Response
    import logging
    
    HAS_CV2_FLASK = True
    
    # ArUco 최신 문법 세팅
    aruco_dict = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    parameters = cv2.aruco.DetectorParameters()
    aruco_detector = cv2.aruco.ArucoDetector(aruco_dict, parameters)
    
    # ★ [수정됨] 'calib_data' 폴더 안에 있는 캘리브레이션 파일 불러오기
    calib_dir = "Calib_data"
    try:
        orig_camera_matrix = np.load(os.path.join(calib_dir, "K1.npy"))
        orig_dist_coeffs = np.load(os.path.join(calib_dir, "D1.npy"))
        HAS_CALIB_FILES = True
        print(f"[System] Fisheye Calibration files loaded successfully from '{calib_dir}' folder!")
    except Exception as e:
        orig_camera_matrix = np.array([[640.0, 0, 320.0], [0, 640.0, 240.0], [0, 0, 1]], dtype=np.float32)
        orig_dist_coeffs = np.zeros((4, 1)) # Fisheye는 4개
        HAS_CALIB_FILES = False
        print(f"[System] Calibration files not found in '{calib_dir}'. Using defaults. ({e})")
    
    # 이미지를 반듯하게 편 이후에 사용할 '왜곡 0' 매트릭스
    zero_dist_coeffs = np.zeros((4, 1))
    
    app = Flask(__name__)
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
except ImportError as e:
    HAS_CV2_FLASK = False
    print(f"Warning: OpenCV or Flask not found. ArUco features will be disabled. ({e})")

# ================= [Unitree SDK Import (Go1)] =================
current_dir = os.path.dirname(os.path.abspath(__file__))
arch = platform.machine().lower()
if arch in ['aarch64', 'arm64']: sdk_arch = 'arm64'
elif arch in ['x86_64', 'amd64']: sdk_arch = 'amd64'
else: sdk_arch = 'amd64'
sdk_path = os.path.join(current_dir, 'unitree_legged_sdk', 'lib', 'python', sdk_arch)
sys.path.append(sdk_path)

try:
    import robot_interface as sdk
    HAS_UNITREE_SDK = True
except ImportError as e:
    HAS_UNITREE_SDK = False
    print(f"Warning: 'robot_interface' module not found. ({e})")

# ================= [Robomaster SDK Import (EP)] =================
try:
    from robomaster import robot
    HAS_ROBOMASTER_SDK = True
except ImportError as e:
    HAS_ROBOMASTER_SDK = False
    print(f"Warning: 'robomaster' module not found. ({e})")

# ================= [EP State & Config] =================
ep_robot_inst = None
ep_state = {'battery': -1, 'pos_x': 0.0, 'pos_y': 0.0, 'speed': 0.0, 'accel_x': 0.0, 'accel_y': 0.0, 'accel_z': 0.0}
ep_node_intent = {'vx': 0.0, 'vy': 0.0, 'wz': 0.0, 'stop': False, 'trigger_time': time.monotonic()}
ep_dashboard = {"hw_link": "Offline", "sn": "Unknown", "conn_type": "None"}

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

# 백그라운드에서 모든 네트워크를 실시간 감지
sys_net_str = "Loading Network..."
def network_monitor_thread():
    global sys_net_str
    while True:
        try:
            out = subprocess.check_output("ip -o -4 addr show", shell=True).decode('utf-8')
            info = []
            for line in out.strip().split('\n'):
                if ' lo ' in line: continue # 로컬 루프백 제외
                p = line.split()
                if len(p) >= 4:
                    dev, ip = p[1], p[3].split('/')[0]
                    ssid = ""
                    if dev.startswith('wl'): # Wi-Fi인 경우 SSID 탐색
                        try: ssid = subprocess.check_output(['iwgetid', dev, '-r']).decode('utf-8').strip()
                        except: pass
                    info.append(f"[{dev}] {ip} ({ssid})" if ssid else f"[{dev}] {ip}")
            sys_net_str = "\n".join(info) if info else "Offline"
        except: pass
        time.sleep(2) # 2초마다 갱신

def get_save_files():
    if not os.path.exists(SAVE_DIR): return []
    return [f for f in os.listdir(SAVE_DIR) if f.endswith(".json")]

# ================= [MT4 State & Config] =================
ser = None 
mt4_current_pos = {'x': 200.0, 'y': 0.0, 'z': 120.0, 'gripper': 40.0}
mt4_target_goal = {'x': 200.0, 'y': 0.0, 'z': 120.0, 'gripper': 40.0} 
mt4_manual_override_until = 0.0 
mt4_dashboard = {"status": "Idle", "hw_link": "Offline", "latency": 0.0, "last_pkt_time": 0.0}

# ★ [V9 추가] 로깅, 경로 제어, 유니티 통신용 상태 변수
PATH_DIR = "path_record"
LOG_DIR = "result_log"
os.makedirs(PATH_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

mt4_mode = {"recording": False, "playing": False}
mt4_collision_lock_until = 0.0 # 충돌 잠금 타이머
mt4_record_f = None
mt4_record_writer = None
mt4_record_temp_name = ""
mt4_log_event_queue = deque() # 유니티의 성공/실패 로그 이벤트를 담을 큐

# 유니티 5007포트(SystemManager UI)로 메시지 전송
def send_unity_ui(msg_type, extra_data):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        msg = f"type:{msg_type},extra:{extra_data}"
        sock.sendto(msg.encode('utf-8'), (MT4_UNITY_IP, 5007))
    except: pass

MT4_UNITY_IP = "192.168.50.63"; MT4_FEEDBACK_PORT = 5005
MT4_LIMITS = {'min_x': 100, 'max_x': 280, 'min_y': -200, 'max_y': 200, 'min_z': 0, 'max_z': 280}
MT4_GRIPPER_MIN = 30.0; MT4_GRIPPER_MAX = 60.0
MT4_Z_OFFSET = 90.0

# ================= [Go1 State & Config] =================
HIGHLEVEL = 0xee; LOCAL_PORT = 8090; ROBOT_IP = "192.168.50.159"; ROBOT_PORT = 8082
GO1_UNITY_IP = "192.168.50.246"; UNITY_STATE_PORT = 15101; UNITY_CMD_PORT = 15102; UNITY_RX_PORT = 15100
dt = 0.002; V_MAX, S_MAX, W_MAX = 0.4, 0.4, 2.0; VX_CMD, VY_CMD, WZ_CMD = 0.20, 0.20, 1.00
hold_timeout_sec = 0.1; repeat_grace_sec = 0.4; min_move_sec = 0.4; stop_brake_sec = 0.0

go1_node_intent = {'vx': 0.0, 'vy': 0.0, 'wz': 0.0, 'yaw_align': False, 'reset_yaw': False, 'stop': False, 'use_unity_cmd': True, 'trigger_time': time.monotonic()}
go1_state = {'world_x': 0.0, 'world_z': 0.0, 'yaw_unity': 0.0, 'vx_cmd': 0.0, 'vy_cmd': 0.0, 'wz_cmd': 0.0, 'mode': 1, 'reason': "NONE", 'battery': -1}
go1_unity_data = {'vx': 0.0, 'vy': 0.0, 'wz': 0.0, 'estop': 0, 'active': False} 
go1_dashboard = {"status": "Idle", "hw_link": "Offline", "unity_link": "Waiting"}

aruco_settings = {'enabled': False, 'marker_size': 0.03}
calib_settings = {'enabled': True} # ★ 추가된 부분 (보정 활성화 설정)
latest_display_frame = None

# V4 File-based Camera
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

def go1_estop_callback():
    global go1_node_intent
    go1_node_intent['stop'] = True; go1_node_intent['vx'] = 0.0; go1_node_intent['vy'] = 0.0; go1_node_intent['wz'] = 0.0
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
    def get_ui_schema(self): return {'x': ("X", 200.0), 'y': ("Y", 0.0), 'z': ("Z", 120.0), 'gripper': ("G", 40.0)}
    def get_settings_schema(self): return {'smooth': ("Smth", 1.0), 'speed': ("Spd", 2.0)}
    def execute_command(self, inputs, settings):
        global mt4_current_pos, mt4_target_goal, mt4_manual_override_until, ser
        if time.time() < mt4_collision_lock_until: return # 충돌 락
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
    def get_ui_schema(self): return {'vx': ("Vx In", 0.0), 'vy': ("Vy In", 0.0), 'wz': ("Wz In", 0.0)}
    
    # ★ 설정 스키마를 다시 비워줍니다. (UI에서 Speed 입력창이 사라짐)
    def get_settings_schema(self): return {}
    
    def execute_command(self, inputs, settings):
        global go1_node_intent
        if inputs.get('vx') is not None or inputs.get('vy') is not None or inputs.get('wz') is not None:
            # ★ 배율 계산을 없애고 앞선 노드에서 들어온 값을 100% 그대로 로봇에 전달합니다.
            go1_node_intent['vx'] = float(inputs.get('vx') or 0)
            go1_node_intent['vy'] = float(inputs.get('vy') or 0)
            go1_node_intent['wz'] = float(inputs.get('wz') or 0)
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
        if isinstance(self.driver, MT4RobotDriver): self.label = "MT4 Driver"; self.type_str = "MT4_DRIVER"  
        elif isinstance(self.driver, Go1RobotDriver): self.label = "Go1 Driver"; self.type_str = "GO1_DRIVER"  
        elif isinstance(self.driver, EPRobotDriver): self.label = "EP Driver"; self.type_str = "EP_DRIVER"

    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label=self.label):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as flow: dpg.add_text("Flow In"); self.inputs[flow]="Flow"
            for key, (label, default_val) in self.schema.items():
                with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as aid:
                    with dpg.group(horizontal=True): dpg.add_text(label, color=(255,255,0)); self.ui_fields[key] = dpg.add_input_float(width=80, default_value=default_val, step=0)
                    self.inputs[aid] = "Data"; self.in_pins[key] = aid
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static): dpg.add_spacer(height=5) 
            for key, (label, default_val) in self.settings_schema.items():
                with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as aid:
                    with dpg.group(horizontal=True): dpg.add_text(label); self.setting_fields[key] = dpg.add_input_float(width=60, default_value=default_val, step=0)
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

# ================= [MT4 Dashboard & Threads] =================
def mt4_manual_control_callback(sender, app_data, user_data):
    global mt4_manual_override_until, mt4_target_goal, mt4_current_pos
    mt4_manual_override_until = time.time() + 1.5; axis, step = user_data; mt4_target_goal[axis] = mt4_current_pos[axis] + step; mt4_apply_limits_and_move()

def mt4_move_to_coord_callback(sender, app_data, user_data):
    global mt4_manual_override_until, mt4_target_goal
    mt4_manual_override_until = time.time() + 2.0; mt4_target_goal['x'] = float(dpg.get_value("input_x")); mt4_target_goal['y'] = float(dpg.get_value("input_y"))
    mt4_target_goal['z'] = float(dpg.get_value("input_z")); mt4_target_goal['gripper'] = float(dpg.get_value("input_g")); mt4_apply_limits_and_move()

def mt4_apply_limits_and_move():
    global mt4_target_goal, mt4_current_pos, ser
    if time.time() < mt4_collision_lock_until: return # 충돌 락
    mt4_target_goal['x'] = max(MT4_LIMITS['min_x'], min(mt4_target_goal['x'], MT4_LIMITS['max_x']))
    mt4_target_goal['y'] = max(MT4_LIMITS['min_y'], min(mt4_target_goal['y'], MT4_LIMITS['max_y']))
    mt4_target_goal['z'] = max(MT4_LIMITS['min_z'], min(mt4_target_goal['z'], MT4_LIMITS['max_z']))
    mt4_target_goal['gripper'] = max(MT4_GRIPPER_MIN, min(mt4_target_goal['gripper'], MT4_GRIPPER_MAX))
    
    # ★ 기존에 있던 mt4_current_pos 덮어쓰기와 ser.write() 코드를 완전히 삭제합니다. 
    # MT4RobotDriver가 백그라운드에서 안전하게 부드러운 이동 및 전송을 대신 처리합니다.

def get_mt4_paths(): return [f for f in os.listdir(PATH_DIR) if f.endswith(".csv")]

def toggle_mt4_record(custom_name=None):
    global mt4_record_f, mt4_record_writer, mt4_record_temp_name
    if mt4_mode["recording"]:
        mt4_mode["recording"] = False
        if mt4_record_f: mt4_record_f.close()
        
        # ★ 유니티에서 보낸 이름이 없고 GUI에서 누른 경우, GUI 입력창의 이름을 가져옴
        if not custom_name and dpg.does_item_exist("path_name_input"):
            custom_name = dpg.get_value("path_name_input")
            
        # 지정한 이름으로 파일명 변경
        if custom_name and mt4_record_temp_name:
            if not custom_name.endswith(".csv"): custom_name += ".csv"
            final_path = os.path.join(PATH_DIR, custom_name)
            try: os.rename(mt4_record_temp_name, final_path)
            except: pass
                
        dpg.set_item_label("btn_mt4_record", "Start Recording")
        if dpg.does_item_exist("combo_mt4_path"): dpg.configure_item("combo_mt4_path", items=get_mt4_paths())
        
        log_msg = f"MT4 Path Saved: {custom_name}" if custom_name else "MT4 Path Saved"
        write_log(log_msg)
        
        # ★ 유니티 UI로 저장 완료 메시지 띄우기 및 드롭다운 목록 즉시 갱신!
        send_unity_ui("STATUS", f"저장 완료: {custom_name if custom_name else '기본 이름'}")
        send_unity_ui("FILE_LIST", f"[{'|'.join(get_mt4_paths())}]")
    else:
        mt4_mode["recording"] = True
        fname = os.path.join(PATH_DIR, f"path_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
        mt4_record_temp_name = fname
        mt4_record_f = open(fname, 'w', newline='')
        mt4_record_writer = csv.writer(mt4_record_f)
        mt4_record_writer.writerow(['x', 'y', 'z', 'gripper'])
        dpg.set_item_label("btn_mt4_record", "Stop Recording")
        write_log("MT4 Path Recording Started.")
        
        # ★ 유니티 UI로 녹화 시작 메시지 띄우기!
        send_unity_ui("STATUS", "경로 녹화 시작...")

def play_mt4_path(sender=None, app_data=None, user_data=None, filename=None):
    if not filename: filename = dpg.get_value("combo_mt4_path")
    if not filename or mt4_mode["playing"] or time.time() < mt4_collision_lock_until: return
    filepath = os.path.join(PATH_DIR, filename)
    if os.path.exists(filepath): threading.Thread(target=play_mt4_path_thread, args=(filepath,), daemon=True).start()

def play_mt4_path_thread(filepath):
    global mt4_mode, mt4_target_goal, mt4_manual_override_until
    mt4_mode["playing"] = True
    mt4_manual_override_until = time.time() + 86400 # 재생 중 조작 잠금
    write_log(f"MT4 Playing path: {os.path.basename(filepath)}")
    send_unity_ui("STATUS", f"경로 재생 중: {os.path.basename(filepath)}")
    try:
        with open(filepath, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if time.time() < mt4_collision_lock_until or not mt4_mode["playing"]: break
                mt4_target_goal['x'] = float(row['x']); mt4_target_goal['y'] = float(row['y'])
                mt4_target_goal['z'] = float(row['z']); mt4_target_goal['gripper'] = float(row['gripper'])
                mt4_apply_limits_and_move()
                time.sleep(0.05)
    except Exception as e: 
        write_log(f"Play Error: {e}") # pass 대신 에러 내용을 출력하도록 수정
    mt4_mode["playing"] = False; mt4_manual_override_until = time.time()
    send_unity_ui("STATUS", "경로 재생 완료")
    write_log("MT4 Playback finished.")

def mt4_background_logger_thread():
    global mt4_record_writer
    log_filename = os.path.join(LOG_DIR, f"mt4_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
    with open(log_filename, 'w', newline='') as mt4_log_f:
        mt4_log_writer = csv.writer(mt4_log_f)
        mt4_log_writer.writerow(['timestamp', 'event', 'target_x', 'target_y', 'target_z', 'target_g', 'current_x', 'current_y', 'current_z', 'current_g'])
        
        while True:
            time.sleep(0.05) # ★ 0.1(10Hz)에서 0.05(20Hz)로 재생 속도와 완벽히 맞춤
            
            event_str = "TICK"
            if mt4_log_event_queue: event_str = mt4_log_event_queue.popleft()
            
            # 상시 로깅
            mt4_log_writer.writerow([time.time(), event_str, mt4_target_goal['x'], mt4_target_goal['y'], mt4_target_goal['z'], mt4_target_goal['gripper'], mt4_current_pos['x'], mt4_current_pos['y'], mt4_current_pos['z'], mt4_current_pos['gripper']])
            mt4_log_f.flush()
            
            # ★ 경로 녹화 (중복 생략 코드를 제거하여, 정지 시간까지 1:1로 리얼타임 녹화)
            if mt4_mode["recording"] and mt4_record_writer:
                curr_tuple = (mt4_current_pos['x'], mt4_current_pos['y'], mt4_current_pos['z'], mt4_current_pos['gripper'])
                mt4_record_writer.writerow(curr_tuple)
                mt4_record_f.flush()

def mt4_homing_callback(sender, app_data, user_data): threading.Thread(target=mt4_homing_thread_func, daemon=True).start()
def mt4_homing_thread_func():
    global ser, mt4_manual_override_until, mt4_target_goal, mt4_current_pos
    if ser:
        mt4_manual_override_until = time.time() + 20.0
        mt4_dashboard["status"] = "HOMING..."; write_log("Homing...")
        ser.write(b"$H\r\n"); time.sleep(15); ser.write(b"M20\r\n"); ser.write(b"G90\r\n"); ser.write(b"G1 F2000\r\n")
        mt4_target_goal.update({'x':200.0, 'y':0.0, 'z':120.0, 'gripper':40.0}); mt4_current_pos.update(mt4_target_goal)
        ser.write(b"G0 X200 Y0 Z120 F2000\r\n"); ser.write(b"M3 S40\r\n")
        mt4_dashboard["status"] = "Idle"; write_log("Homing Done")

def init_mt4_serial():
    global ser
    try:
        ser = serial.Serial('/dev/ttyUSB0', 115200, timeout=0.05); mt4_dashboard["hw_link"] = "Online"; write_log("System: MT4 Connected")
        time.sleep(2); ser.write(b"$H\r\n"); time.sleep(15); ser.write(b"M20\r\n"); ser.write(b"G90\r\n"); ser.write(b"G1 F2000\r\n"); time.sleep(1)
        ser.write(b"G0 X200 Y0 Z120 F2000\r\n"); ser.write(b"M3 S40\r\n") 
    except Exception as e: mt4_dashboard["hw_link"] = "Simulation"; write_log(f"MT4 Sim Mode ({e})"); ser = None

def auto_reconnect_mt4_thread():
    global ser
    while True:
        if ser is None and os.path.exists('/dev/ttyUSB0'):
            try: init_mt4_serial() 
            except: pass
        time.sleep(3) 

# ================= [V8 File-based Vision (All Cameras Calibration & ArUco)] =================
def go1_vision_worker_thread():
    global latest_display_frame
    sock_aruco = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    
    # 각 카메라별로 마지막 처리한 파일을 기억하기 위한 딕셔너리
    last_processed_files = {cfg["id"]: None for cfg in CAMERA_CONFIG}
    
    while True:
        if camera_state['status'] == 'Running':
            # ★ 5개의 모든 카메라 폴더를 순회하며 처리합니다.
            for config in CAMERA_CONFIG:
                folder = config["folder"]
                camera_id = config["id"]
                
                try:
                    if not os.path.exists(folder): continue
                    
                    files = glob.glob(os.path.join(folder, "*.jpg"))
                    if len(files) >= 2:
                        files.sort(key=os.path.getctime)
                        target_file = files[-2] # 안전하게 완전히 저장된 직전 파일 읽기
                        
                        if target_file != last_processed_files[camera_id]:
                            last_processed_files[camera_id] = target_file
                            frame = cv2.imread(target_file)
                            
                            if frame is not None:
                                # ★ 1단계: 보정 옵션이 켜져 있는지 확인
                                is_calibrated = HAS_CALIB_FILES and calib_settings['enabled']
                                if is_calibrated:
                                    frame = cv2.fisheye.undistortImage(frame, orig_camera_matrix, orig_dist_coeffs, Knew=orig_camera_matrix)
                                
                                # ★ 2단계: ArUco 마커 인식 및 축 그리기
                                if aruco_settings['enabled'] and HAS_CV2_FLASK:
                                    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                                    corners, ids, rejected = aruco_detector.detectMarkers(gray)
                                    if ids is not None:
                                        msize = aruco_settings['marker_size']
                                        marker_points = np.array([
                                            [-msize / 2, msize / 2, 0], [msize / 2, msize / 2, 0],
                                            [msize / 2, -msize / 2, 0], [-msize / 2, -msize / 2, 0]
                                        ], dtype=np.float32)
                                        for i in range(len(ids)):
                                            # ★ 핵심: 화면을 폈으면 왜곡이 없는 행렬(0)을, 화면이 둥글면 원본 왜곡 행렬을 넣어서 거리를 계산합니다.
                                            use_dist = zero_dist_coeffs if is_calibrated else orig_dist_coeffs
                                            ret, rvec, tvec = cv2.solvePnP(marker_points, corners[i], orig_camera_matrix, use_dist)
                                            if ret:
                                                cv2.drawFrameAxes(frame, orig_camera_matrix, use_dist, rvec, tvec, 0.03)
                                                cv2.aruco.drawDetectedMarkers(frame, corners)
                                                
                                                marker_id = int(ids[i][0])
                                                tx, ty, tz = float(tvec[0][0]), float(tvec[1][0]), float(tvec[2][0])
                                                
                                                data = {"id": marker_id, "x": round(tx, 4), "y": round(ty, 4), "z": round(tz, 4), "cam": camera_id}
                                                try: sock_aruco.sendto(json.dumps(data).encode(), (GO1_UNITY_IP, 5008))
                                                except: pass
                                                
                                                text = f"[{camera_id}] ID:{marker_id} X:{tx:.2f} Y:{ty:.2f} Z:{tz:.2f}"
                                                cx, cy = int(corners[i][0][0][0]), int(corners[i][0][0][1])
                                                cv2.putText(frame, text, (cx, cy - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
                                
                                # ★ 3단계: 화면 자르기 (보정 옵션을 켰을 때만 오른쪽 이상한 부분을 반으로 자릅니다)
                                if is_calibrated:
                                    height, width = frame.shape[:2]
                                    cropped_frame = frame[:, :width//2]
                                else:
                                    cropped_frame = frame

                                # 최종 인코딩 및 메모리 저장
                                ret_enc, buffer = cv2.imencode('.jpg', cropped_frame)
                                if ret_enc:
                                    processed_bytes = buffer.tobytes()
                                    global latest_processed_frames
                                    latest_processed_frames[camera_id] = processed_bytes
                                    
                                    # Flask 웹 브라우저에도 잘린 화면을 띄웁니다.
                                    if camera_id == 'go1_front':
                                        latest_display_frame = processed_bytes
                except Exception as e: pass
        time.sleep(0.01)

if HAS_CV2_FLASK:
    @app.route('/')
    def index(): return "<h1>Multi-Robot Integrated Visual Scripting Framework (9)</h1><img src='/video_feed' width='640'>"

    @app.route('/video_feed')
    def video_feed():
        def generate():
            while True:
                if latest_display_frame is not None: yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + latest_display_frame + b'\r\n')
                time.sleep(0.05)
        return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=frame')

def start_flask_app():
    if HAS_CV2_FLASK: app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)

# ================= [Go1 Background Threads] =================
def camera_worker_thread():
    global camera_state
    nanos = ["unitree@192.168.123.13", "unitree@192.168.123.14", "unitree@192.168.123.15"]
    
    while True:
        if camera_command_queue:
            cmd, pc_ip = camera_command_queue.popleft()
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            
            if cmd == 'START':
                camera_state['status'] = 'Starting...'
                write_log(f"[Cam START] Target PC IP: {pc_ip}")
                
                # 1단계: 로봇 내부(나노)로 영상 송출 명령 전송
                write_log("[Cam START] Step 1: Sending SSH commands to Nanos...")
                for nano in nanos:
                    remote_cmd = f"echo 123 | sudo -S bash -c 'fuser -k /dev/video0 /dev/video1 2>/dev/null; cd /home/unitree; ./kill_camera.sh || true; nohup ./go1_send_both.sh {pc_ip} > send_both_{ts}.log 2>&1 &'"
                    try: 
                        subprocess.Popen(["ssh", "-o", "StrictHostKeyChecking=accept-new", nano, remote_cmd])
                        write_log(f"[Cam START] SSH command sent successfully to {nano}")
                    except Exception as e: 
                        write_log(f"[Cam START ERROR] SSH failed for {nano}: {e}")
                
                # 2단계: 로컬(노트북)에 남아있던 기존 GStreamer 찌꺼기 프로세스 정리
                write_log("[Cam START] Step 2: Cleaning up existing local GStreamer processes...")
                try:
                    subprocess.call("pkill -f 'gst-launch-1.0.*multifilesink'", shell=True)
                except Exception as e:
                    write_log(f"[Cam START WARN] Process cleanup issue: {e}")
                time.sleep(0.5)
                
                # 3단계: 5개의 각 포트별로 GStreamer 수신 파이프라인 생성
                write_log("[Cam START] Step 3: Setting up local GStreamer receivers...")
                recv_configs = [
                    ("9400", "/dev/shm/go1_front", "front"), 
                    ("9401", "/dev/shm/go1_underfront", "underfront"), 
                    ("9410", "/dev/shm/go1_nano14_left", "left"), 
                    ("9411", "/dev/shm/go1_nano14_right", "right"), 
                    ("9420", "/dev/shm/go1_nano15_bottom", "bottom")
                ]
                
                for port, outdir, prefix in recv_configs:
                    try:
                        os.makedirs(outdir, exist_ok=True)
                        gst_cmd = f"gst-launch-1.0 -q udpsrc port={port} caps=\"application/x-rtp,media=video,encoding-name=JPEG,payload=26\" ! rtpjpegdepay ! multifilesink location=\"{outdir}/{prefix}_%06d.jpg\" sync=false"
                        subprocess.Popen(gst_cmd, shell=True)
                        write_log(f"[Cam START] Receiver listening on port {port} -> {outdir}")
                    except Exception as e:
                        write_log(f"[Cam START ERROR] Failed to start receiver on port {port}: {e}")
                
                time.sleep(2)
                camera_state['status'] = 'Running'
                write_log("[Cam START] All camera streams are now Running.")
                
            elif cmd == 'STOP':
                camera_state['status'] = 'Stopping...'
                write_log("[Cam STOP] Initiating stream shutdown...")
                
                # 1단계: 로봇 내부(나노)의 송출 스크립트 강제 종료
                write_log("[Cam STOP] Step 1: Sending kill commands to Nanos...")
                for nano in nanos:
                    script = f"echo 123 | sudo -S bash -c 'pkill -f go1_send_cam || true; cd /home/unitree && ./kill_camera.sh || true'"
                    try: 
                        subprocess.Popen(["ssh", "-o", "StrictHostKeyChecking=accept-new", nano, script])
                        write_log(f"[Cam STOP] Kill command sent to {nano}")
                    except Exception as e: 
                        # 기존에는 pass로 덮어뒀던 에러를 표출하도록 수정
                        write_log(f"[Cam STOP ERROR] Failed to send kill command to {nano}: {e}")
                
                # 2단계: 로컬(노트북)의 수신 프로세스 종료
                write_log("[Cam STOP] Step 2: Terminating local GStreamer receivers...")
                try:
                    subprocess.call("pkill -f 'gst-launch-1.0.*multifilesink'", shell=True)
                except Exception as e:
                    write_log(f"[Cam STOP WARN] Local termination issue: {e}")
                    
                time.sleep(1)
                camera_state['status'] = 'Stopped'
                write_log("[Cam STOP] Stream completely stopped.")
                
        time.sleep(0.1)

def global_image_cleanup_thread():
    while True:
        for config in CAMERA_CONFIG:
            folder = config["folder"]
            if os.path.exists(folder):
                try:
                    files = glob.glob(os.path.join(folder, "*.jpg"))
                    if len(files) > KEEP_COUNT:
                        # 이름이 아닌 '파일 생성 시간'을 기준으로 정확하게 정렬하도록 수정
                        files.sort(key=os.path.getctime) 
                        for f in files[:len(files) - KEEP_COUNT]:
                            try: os.remove(f)
                            except OSError: pass
                except Exception: pass
        time.sleep(2)

async def send_image_async(session, filepath, camera_id, server_url):
    try:
        global latest_processed_frames
        # ★ 폴더 뒤지지 않고, 비전 스레드가 완성해둔 메모리 속 이미지를 즉시 가져옴!
        file_data = latest_processed_frames.get(camera_id)
        
        # 만약 아직 비전 처리가 한 번도 안 끝난 초기 상태라면 기존 방식대로 폴더에서 원본 읽기
        if file_data is None:
            if not os.path.exists(filepath): return
            with open(filepath, 'rb') as f: file_data = f.read()

        form = aiohttp.FormData()
        form.add_field('camera_id', camera_id)
        # 서버에서 받을 때 헷갈리지 않게 파일명 고정
        form.add_field('file', file_data, filename=f"{camera_id}_calib.jpg", content_type='image/jpeg')
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
                        s_thread = threading.Thread(target=start_async_loop, args=(config, url)); s_thread.daemon = True; s_thread.start()
                        sender_threads.extend([s_thread])
            elif cmd == 'STOP' and multi_sender_active:
                multi_sender_active = False; sender_state['status'] = 'Stopped'; write_log("Sender: Disconnected"); sender_threads.clear()
        time.sleep(0.1)

def go1_v4_comm_thread():
    global go1_state, GO1_UNITY_IP, go1_unity_data
    if HAS_UNITREE_SDK:
        udp = sdk.UDP(HIGHLEVEL, LOCAL_PORT, ROBOT_IP, ROBOT_PORT)
        cmd = sdk.HighCmd(); state = sdk.HighState()
        udp.InitCmdData(cmd); go1_dashboard["hw_link"] = "Connecting..." # ★ 처음엔 무조건 Online이 아니라 연결 중으로 표시
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

    # ★ [추가된 부분] 로봇 연결 상태 감지용 변수
    last_go1_recv_time = now
    last_go1_tick = 0
    last_imu_val = 0.0

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
            
            # ★ [핵심 추가] 데이터가 진짜로 갱신되고 있는지 (Heartbeat) 검사
            current_tick = getattr(state, 'tick', 0)
            current_imu = float(state.imu.rpy[0]) + float(state.imu.rpy[1]) + float(state.imu.rpy[2])
            
            if current_tick != last_go1_tick or current_imu != last_imu_val:
                last_go1_recv_time = tnow
                last_go1_tick = current_tick
                last_imu_val = current_imu
                
            # 최근 1초 이내에 데이터가 살아서 움직였다면 Online, 아니면 Offline
            if (tnow - last_go1_recv_time) < 1.0:
                # ★ [핵심] 화면에 Go1 노드가 있고 스크립트가 RUN 중인지 확인
                go1_in_use = is_running and any(n.type_str.startswith("GO1_") for n in node_registry.values())
                # 상태 텍스트 분리 (조종 중 / 관전 중)
                go1_dashboard["hw_link"] = "Online (Active)" if go1_in_use else "Online (Listen)"

                try:
                    if hasattr(state.bms, 'SOC'): go1_state['battery'] = int(state.bms.SOC)
                    elif hasattr(state.bms, 'soc'): go1_state['battery'] = int(state.bms.soc)
                    if go1_state['battery'] == 0: go1_state['battery'] = -1 
                except Exception as e: pass
            else:
                go1_dashboard["hw_link"] = "Offline"
                go1_state['battery'] = -1
        
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
        # ★ [핵심 패치 1] 버퍼에 밀린 패킷을 싹 다 읽어서 가장 최신의 명령만 가져옵니다 (C++ 로직 동일)
        while True:
            try:
                data, _ = sock_rx_unity.recvfrom(256)
                s = data.decode("utf-8", errors="ignore").strip().split()
                if len(s) >= 4: got = (float(s[0]), float(s[1]), float(s[2]), int(s[3]))
            except: 
                break # 더 이상 읽을 패킷이 없으면 빠져나옴
        
        # ★ [핵심 패치 2] 명령이 오지 않은 찰나의 순간에도 기존 명령(go1_unity_data)을 0으로 덮어쓰지 않고 유지합니다
        if got: 
            last_unity_cmd_time = tnow; go1_dashboard['unity_link'] = "Active"
            go1_unity_data['vx'], go1_unity_data['vy'], go1_unity_data['wz'], go1_unity_data['estop'] = got
            
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
            # ★ [핵심 패치 3] 허공에 사라지는 임시 변수(uvx) 대신 기억해둔 유니티 값을 모터에 꽂아줍니다
            target_mode = 2 if not go1_unity_data['estop'] else 1
            if cmd: cmd.gaitType = 1
            out_vx = clamp(go1_unity_data['vx'], -V_MAX, V_MAX)
            out_vy = clamp(go1_unity_data['vy'], -S_MAX, S_MAX)
            out_wz = clamp(go1_unity_data['wz'], -W_MAX, W_MAX)
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

        # ★ [핵심 2] 송신 전에도 똑같이 안전하게 확인 후, 제어권이 있을 때만 로봇에게 패킷을 발송합니다.
        try:
            go1_in_use = is_running and any(n.type_str.startswith("GO1_") for n in list(node_registry.values()))
        except:
            go1_in_use = False

        if cmd: 
            cmd.mode = target_mode; cmd.velocity = [out_vx, out_vy]; cmd.yawSpeed = out_wz
            if go1_in_use: 
                udp.SetSend(cmd); udp.Send()
            
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

# --- EP Telemetry Callbacks ---
def ep_sub_pos(info): ep_state['pos_x'], ep_state['pos_y'], _ = info
def ep_sub_vel(info): ep_state['speed'] = math.sqrt(info[0]**2 + info[1]**2)
def ep_sub_bat(info): ep_state['battery'] = int(info[0]) if isinstance(info, (tuple, list)) else int(info)
def ep_sub_imu(info): ep_state['accel_x'], ep_state['accel_y'], ep_state['accel_z'] = info[:3]

# --- EP Connection Engine ---
def connect_ep_thread_func(conn_mode):
    global ep_robot_inst
    if not HAS_ROBOMASTER_SDK:
        ep_dashboard["hw_link"] = "Simulation"; return
        
    ep_dashboard["hw_link"] = f"Connecting ({conn_mode.upper()})..."
    write_log(f"System: Attempting EP Connection via {conn_mode.upper()}...")
    try:
        if ep_robot_inst is not None:
            try: ep_robot_inst.close()
            except: pass
        
        ep_robot_inst = robot.Robot()
        ep_robot_inst.initialize(conn_type=conn_mode)
        ep_dashboard["sn"] = ep_robot_inst.get_sn()
        ep_dashboard["hw_link"] = f"Online ({conn_mode.upper()})"
        ep_dashboard["conn_type"] = conn_mode.upper()
        write_log(f"System: EP Connected! (SN: {ep_dashboard['sn']})")
        
        ep_robot_inst.chassis.sub_position(freq=1, callback=ep_sub_pos)
        ep_robot_inst.chassis.sub_velocity(freq=5, callback=ep_sub_vel)
        ep_robot_inst.battery.sub_battery_info(freq=1, callback=ep_sub_bat)
        ep_robot_inst.chassis.sub_imu(freq=10, callback=ep_sub_imu)
    except Exception as e:
        ep_robot_inst = None; ep_dashboard["hw_link"] = "Offline"
        write_log(f"EP Connect Error: {e}")

def btn_connect_ep_sta(): threading.Thread(target=connect_ep_thread_func, args=("sta",), daemon=True).start()
def btn_connect_ep_ap(): threading.Thread(target=connect_ep_thread_func, args=("ap",), daemon=True).start()

# --- EP Control Loop (with sender2.py Brake Sequence) ---
def ep_comm_thread():
    global ep_node_intent, ep_robot_inst
    is_moving = False
    while True:
        time.sleep(0.05)
        if ep_robot_inst is None or ep_dashboard["hw_link"] == "Offline": continue
        
        tnow = time.monotonic()
        # 노드/키보드에서 0.2초 이상 신호가 없으면(손을 떼면) 정지 판정
        active = (tnow - ep_node_intent['trigger_time']) < 0.2
        
        if ep_node_intent['stop'] or not active:
            if is_moving:
                write_log("EP: 🛑 정지 시퀀스 시작 (Active Brake -> Wheel Lock)")
                try:
                    ep_robot_inst.chassis.drive_speed(x=0, y=0, z=0, timeout=0.1)
                    time.sleep(0.05)
                    # ★ 성공판 로직: 완전히 멈추기 위해 drive_wheels 사용
                    for _ in range(3):
                        ep_robot_inst.chassis.drive_wheels(w1=0, w2=0, w3=0, w4=0)
                        time.sleep(0.1)
                except Exception as e: write_log(f"EP Brake Error: {e}")
                is_moving = False; ep_node_intent['stop'] = False
        else:
            try:
                # x: 전후, y: 좌우, z: 제자리 회전 (deg/s)
                ep_robot_inst.chassis.drive_speed(x=ep_node_intent['vx'], y=ep_node_intent['vy'], z=ep_node_intent['wz'], timeout=0.5)
                is_moving = True
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
        self.combo_keys = None 
    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label="MT4 Keyboard"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as flow: dpg.add_text("Flow In"); self.inputs[flow] = "Flow"
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                self.combo_keys = dpg.add_combo(["WASD", "Arrow Keys"], default_value="WASD", width=120)
                dpg.add_text("XY Move / QE: Z / UJ: Grip", color=(255,150,150))
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as x: dpg.add_text("Target X"); self.outputs[x] = "Data"; self.out_x = x
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as y: dpg.add_text("Target Y"); self.outputs[y] = "Data"; self.out_y = y
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as z: dpg.add_text("Target Z"); self.outputs[z] = "Data"; self.out_z = z
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as g: dpg.add_text("Target Grip"); self.outputs[g] = "Data"; self.out_g = g
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as f: dpg.add_text("Flow Out"); self.outputs[f] = "Flow"
    def execute(self):
        # ★ [추가된 부분] 파일 이름 입력창에 커서가 깜빡일 때는 키 입력을 무시하고 흐름만 통과시킵니다.
        if dpg.is_item_focused("file_name_input") or (dpg.does_item_exist("path_name_input") and dpg.is_item_focused("path_name_input")):
            for k, v in self.outputs.items():
                if v == "Flow": return k
            return None
        
        global mt4_manual_override_until, mt4_target_goal
        if time.time() - self.last_input_time > self.cooldown:
            dx=0; dy=0; dz=0; dg=0
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
        super().__init__(node_id, "Unity Logic (MT4)", "MT4_UNITY")
        self.data_in_id = None; self.out_x = None; self.out_y = None; self.out_z = None; self.out_g = None
        self.last_processed_json = ""  # ★ 이 한 줄을 끝에 추가해 주세요!
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
        global mt4_collision_lock_until
        if time.time() - mt4_dashboard.get("last_pkt_time", 0) > 0.5:
            self.output_data[self.out_x] = None; self.output_data[self.out_y] = None; self.output_data[self.out_z] = None; self.output_data[self.out_g] = None
            return self.outputs
            
        raw_json = self.fetch_input_data(self.data_in_id)
        if raw_json and raw_json != self.last_processed_json:
            self.last_processed_json = raw_json
            try:
                parsed = json.loads(raw_json)
                msg_type = parsed.get("type", "MOVE")
                
                # ★ 유니티가 보낸 Raw Command 처리
                if msg_type == "CMD":
                    val = parsed.get("val", "")
                    
                    if val == "COLLISION":
                        mt4_collision_lock_until = time.time() + 2.0 # 2초간 조작 무시
                        if ser and ser.is_open: ser.write(b"!") # 하드웨어 급정지
                        write_log("MT4 Collision Detected! Robot Locked.")
                        send_unity_ui("STATUS", "충돌 감지! 로봇 긴급 정지")
                        
                    elif val == "START_REC":
                        if not mt4_mode["recording"]: toggle_mt4_record()
                        
                    elif val.startswith("STOP_REC:"):
                        fname = val.split(":")[1]
                        if mt4_mode["recording"]: toggle_mt4_record(custom_name=fname)
                        
                    elif val == "REQ_FILES":
                        send_unity_ui("FILE_LIST", f"[{'|'.join(get_mt4_paths())}]")
                        
                    elif val.startswith("PLAY:"):
                        fname = val.split(":")[1]
                        play_mt4_path(filename=fname)
                        
                    elif val == "LOG_SUCCESS":
                        mt4_log_event_queue.append("SUCCESS")
                        send_unity_ui("LOG", "<color=green>SUCCESS 기록 완료</color>")
                        
                    elif val == "LOG_FAIL":
                        mt4_log_event_queue.append("FAIL")
                        send_unity_ui("LOG", "<color=red>FAIL 기록 완료</color>")

                # ★ XYZ 및 그리퍼 이동 처리 (충돌 시나 재생 중일 때는 유니티 명령 무시)
                elif msg_type == "MOVE" and not mt4_mode["playing"] and time.time() > mt4_collision_lock_until:
                    self.output_data[self.out_x] = parsed.get('z', 0) * 1000.0
                    self.output_data[self.out_y] = -parsed.get('x', 0) * 1000.0
                    self.output_data[self.out_z] = (parsed.get('y', 0) * 1000.0) + MT4_Z_OFFSET
                    self.output_data[self.out_g] = parsed.get('gripper') 
            except: pass 
        return self.outputs
    
# --- EP Nodes ---
class EPRobotDriver(BaseRobotDriver):
    def get_ui_schema(self): return {'vx': ("Vx(m/s)", 0.0), 'vy': ("Vy(m/s)", 0.0), 'wz': ("Wz(deg/s)", 0.0)}
    def get_settings_schema(self): return {}
    def execute_command(self, inputs, settings):
        global ep_node_intent
        if inputs.get('vx') is not None or inputs.get('vy') is not None or inputs.get('wz') is not None:
            ep_node_intent['vx'] = float(inputs.get('vx') or 0)
            ep_node_intent['vy'] = float(inputs.get('vy') or 0)
            ep_node_intent['wz'] = float(inputs.get('wz') or 0)
            ep_node_intent['trigger_time'] = time.monotonic()
        return None

class EPKeyboardNode(BaseNode):
    def __init__(self, node_id): super().__init__(node_id, "Keyboard (EP)", "EP_KEYBOARD"); self.out_vx = None; self.out_vy = None; self.out_wz = None; self.combo_keys = None
    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label="Keyboard (EP Intent)"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as flow: dpg.add_text("Flow In"); self.inputs[flow] = "Flow"
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                self.combo_keys = dpg.add_combo(["WASD", "Arrow Keys"], default_value="WASD", width=120)
                dpg.add_text("Move / QE: Turn\nSpace: Stop", color=(100,255,100))
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as vx: dpg.add_text("Target Vx"); self.outputs[vx] = "Data"; self.out_vx = vx
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as vy: dpg.add_text("Target Vy"); self.outputs[vy] = "Data"; self.out_vy = vy
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as wz: dpg.add_text("Target Wz"); self.outputs[wz] = "Data"; self.out_wz = wz
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as f: dpg.add_text("Flow Out"); self.outputs[f] = "Flow"
    def execute(self):
        if dpg.is_item_focused("file_name_input") or (dpg.does_item_exist("path_name_input") and dpg.is_item_focused("path_name_input")): return None
        global ep_node_intent; vx = 0.0; vy = 0.0; wz = 0.0
        EP_V_MAX = 0.5; EP_W_MAX = 60.0 # 안전 속도 (0.5m/s, 60deg/s)
        
        km = dpg.get_value(self.combo_keys)
        if km == "WASD":
            if dpg.is_key_down(dpg.mvKey_W): vx = EP_V_MAX
            if dpg.is_key_down(dpg.mvKey_S): vx = -EP_V_MAX
            if dpg.is_key_down(dpg.mvKey_A): vy = -EP_V_MAX
            if dpg.is_key_down(dpg.mvKey_D): vy = EP_V_MAX
        else:
            if dpg.is_key_down(dpg.mvKey_Up): vx = EP_V_MAX
            if dpg.is_key_down(dpg.mvKey_Down): vx = -EP_V_MAX
            if dpg.is_key_down(dpg.mvKey_Left): vy = -EP_V_MAX
            if dpg.is_key_down(dpg.mvKey_Right): vy = EP_V_MAX

        if dpg.is_key_down(dpg.mvKey_Q): wz = -EP_W_MAX
        if dpg.is_key_down(dpg.mvKey_E): wz = EP_W_MAX
        if dpg.is_key_down(dpg.mvKey_Spacebar): ep_node_intent['stop'] = True
        
        if vx or vy or wz: ep_node_intent['vx'] = vx; ep_node_intent['vy'] = vy; ep_node_intent['wz'] = wz; ep_node_intent['trigger_time'] = time.monotonic()
        self.output_data[self.out_vx]=vx; self.output_data[self.out_vy]=vy; self.output_data[self.out_wz]=wz
        for k, v in self.outputs.items():
            if v == "Flow": return k
        return None

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
    
# (기존 V4 카메라 노드 유지)
class CameraControlNode(BaseNode):
    def __init__(self, node_id): super().__init__(node_id, "Camera Control", "CAM_CTRL"); self.combo_action = None; self.in_ip = None; self.out_flow = None; self.chk_aruco = None; self.input_size = None; self.chk_calib = None
    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label="File Save Camera (Go1)"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as flow: dpg.add_text("Flow In"); self.inputs[flow] = "Flow"
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static): 
                self.combo_action = dpg.add_combo(["Start Stream", "Stop Stream"], default_value="Start Stream", width=140)
                dpg.add_spacer(height=3)
                self.chk_calib = dpg.add_checkbox(label="Enable Calibration", default_value=True) # ★ 보정 체크박스 추가
                self.chk_aruco = dpg.add_checkbox(label="Enable ArUco Tracking", default_value=False)
                with dpg.group(horizontal=True):
                    dpg.add_text("Size(m):")
                    self.input_size = dpg.add_input_float(width=80, default_value=0.03, step=0.01)
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as ip_in: dpg.add_text("Target IP In", color=(255,150,200)); self.inputs[ip_in] = "Data"; self.in_ip = ip_in
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out: dpg.add_text("Flow Out"); self.outputs[out] = "Flow"; self.out_flow = out
    def execute(self):
        global aruco_settings, calib_settings
        aruco_settings['enabled'] = dpg.get_value(self.chk_aruco)
        aruco_settings['marker_size'] = dpg.get_value(self.input_size)
        calib_settings['enabled'] = dpg.get_value(self.chk_calib) # ★ 상태 저장
        
        action = dpg.get_value(self.combo_action); ext_ip = self.fetch_input_data(self.in_ip); target_ip = ext_ip if ext_ip else get_local_ip()
        if action == "Start Stream" and camera_state['status'] in ['Stopped', 'Stopping...']: camera_command_queue.append(('START', target_ip))
        elif action == "Stop Stream" and camera_state['status'] in ['Running', 'Starting...']: camera_command_queue.append(('STOP', target_ip))
        return self.out_flow
    def get_settings(self): return {"act": dpg.get_value(self.combo_action), "aruco": dpg.get_value(self.chk_aruco), "size": dpg.get_value(self.input_size), "calib": dpg.get_value(self.chk_calib)}
    def load_settings(self, data): dpg.set_value(self.combo_action, data.get("act", "Start Stream")); dpg.set_value(self.chk_aruco, data.get("aruco", False)); dpg.set_value(self.input_size, data.get("size", 0.03)); dpg.set_value(self.chk_calib, data.get("calib", True))

class MultiSenderNode(BaseNode):
    def __init__(self, node_id): super().__init__(node_id, "Server Sender", "MULTI_SENDER"); self.combo_action = None; self.field_url = None; self.out_flow = None
    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label="Server Sender (Go1)"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as flow: dpg.add_text("Flow In"); self.inputs[flow] = "Flow"
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static): self.combo_action = dpg.add_combo(["Start Sender", "Stop Sender"], default_value="Start Sender", width=140); dpg.add_spacer(height=3); dpg.add_text("Server URL:"); self.field_url = dpg.add_input_text(width=160, default_value="http://210.110.250.33:5001/upload")
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out: dpg.add_text("Flow Out"); self.outputs[out] = "Flow"; self.out_flow = out
    def execute(self):
        global sender_state # ★ sender_state 전역 변수 선언 추가
        action = dpg.get_value(self.combo_action); url = dpg.get_value(self.field_url)
        
        # ★ [핵심 패치] 중복 실행 차단!
        if action == "Start Sender" and sender_state['status'] == 'Stopped': 
            sender_state['status'] = 'Starting...'
            sender_command_queue.append(('START', url))
        elif action == "Stop Sender" and sender_state['status'] == 'Running': 
            sender_state['status'] = 'Stopping...'
            sender_command_queue.append(('STOP', url))
            
        return self.out_flow
    def get_settings(self): return {"act": dpg.get_value(self.combo_action), "url": dpg.get_value(self.field_url)}
    def load_settings(self, data): dpg.set_value(self.combo_action, data.get("act", "Start Sender")); dpg.set_value(self.field_url, data.get("url", "http://210.110.250.33:5001/upload"))

class Go1UnityNode(BaseNode):
    def __init__(self, node_id): super().__init__(node_id, "Unity Link", "GO1_UNITY"); self.field_ip = None; self.chk_enable = None; self.out_vx = None; self.out_vy = None; self.out_wz = None; self.out_active = None
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

class Go1TimedActionNode(BaseNode):
    def __init__(self, node_id): 
        super().__init__(node_id, "Go1 Timed Action", "GO1_TIMED")
        self.combo_id = None; self.field_spd = None; self.field_time = None; self.out_flow = None
        self.start_time = 0.0; self.is_running = False

    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label="Go1 Timed Action (Test)"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as flow: dpg.add_text("Flow In"); self.inputs[flow] = "Flow"
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static): 
                self.combo_id = dpg.add_combo(items=["Walk Fwd", "Walk Back", "Turn Left", "Turn Right"], default_value="Walk Fwd", width=130)
                dpg.add_spacer(height=3)
                with dpg.group(horizontal=True): dpg.add_text("Spd:"); self.field_spd = dpg.add_input_float(width=60, default_value=0.4, step=0.1)
                with dpg.group(horizontal=True): dpg.add_text("Sec:"); self.field_time = dpg.add_input_float(width=60, default_value=2.0, step=0.5)
                dpg.add_spacer(height=5)
                # ★ 노드 자체에 독립적인 실행 버튼을 달아줍니다.
                dpg.add_button(label="[ ▶ TEST TRIGGER ]", width=130, callback=self.start_timer)
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out: dpg.add_text("Flow Out"); self.outputs[out] = "Flow"; self.out_flow = out

    def start_timer(self, s, a):
        self.is_running = True; self.start_time = time.time()
        write_log(f"Timed Action: {dpg.get_value(self.field_time)}sec test started")

    def execute(self):
        global go1_node_intent
        # 버튼이 눌렸을 때만 백그라운드에서 N초간 실행됩니다.
        if self.is_running:
            if time.time() - self.start_time <= dpg.get_value(self.field_time):
                mode = dpg.get_value(self.combo_id); spd = dpg.get_value(self.field_spd)
                vx = 0.0; vy = 0.0; wz = 0.0
                if mode == "Walk Fwd": vx = spd
                elif mode == "Walk Back": vx = -spd
                elif mode == "Turn Left": wz = spd
                elif mode == "Turn Right": wz = -spd
                
                go1_node_intent['vx'] = vx; go1_node_intent['vy'] = vy; go1_node_intent['wz'] = wz
                go1_node_intent['trigger_time'] = time.monotonic()
            else: 
                # 시간이 다 되면 자동으로 멈춤(브레이크) 신호를 보냅니다.
                self.is_running = False; go1_node_intent['stop'] = True
                write_log("Timed Action: Finished")
        return self.out_flow

    def get_settings(self): return {"mode": dpg.get_value(self.combo_id), "spd": dpg.get_value(self.field_spd), "time": dpg.get_value(self.field_time)}
    def load_settings(self, data): dpg.set_value(self.combo_id, data.get("mode", "Walk Fwd")); dpg.set_value(self.field_spd, data.get("spd", 0.4)); dpg.set_value(self.field_time, data.get("time", 2.0))

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
                self.combo_keys = dpg.add_combo(["WASD", "Arrow Keys"], default_value="WASD", width=120)
                dpg.add_text("Move / QE: Turn\nSpace: Stop / R: Yaw Align", color=(255,150,150))
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as vx: dpg.add_text("Target Vx"); self.outputs[vx] = "Data"; self.out_vx = vx
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as vy: dpg.add_text("Target Vy"); self.outputs[vy] = "Data"; self.out_vy = vy
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as wz: dpg.add_text("Target Wz"); self.outputs[wz] = "Data"; self.out_wz = wz
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as f: dpg.add_text("Flow Out"); self.outputs[f] = "Flow"
    def execute(self):
        # ★ [추가된 부분] 파일 이름 입력창에 커서가 깜빡일 때는 키 입력을 무시하고 흐름만 통과시킵니다.
        if dpg.is_item_focused("file_name_input") or (dpg.does_item_exist("path_name_input") and dpg.is_item_focused("path_name_input")):
            for k, v in self.outputs.items():
                if v == "Flow": return k
            return None
        
        global go1_node_intent; vx = 0.0; vy = 0.0; wz = 0.0
        
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
        elif node_type == "GO1_TIMED": node = Go1TimedActionNode(node_id)
        elif node_type == "GO1_KEYBOARD": node = Go1KeyboardNode(node_id)
        elif node_type == "GO1_UNITY": node = Go1UnityNode(node_id)
        elif node_type == "CAM_CTRL": node = CameraControlNode(node_id)
        elif node_type == "TARGET_IP": node = TargetIpNode(node_id)
        elif node_type == "MULTI_SENDER": node = MultiSenderNode(node_id)
        elif node_type == "GET_GO1_STATE": node = GetGo1StateNode(node_id)
        elif node_type == "EP_DRIVER": node = UniversalRobotNode(node_id, EPRobotDriver())
        elif node_type == "EP_KEYBOARD": node = EPKeyboardNode(node_id)
        
        if node: node.build_ui(); node_registry[node_id] = node; return node
        return None

def toggle_exec(s, a): 
    global is_running
    is_running = not is_running
    dpg.set_item_label("btn_run", "STOP" if is_running else "RUN SCRIPT")
    
    # ★ [추가된 로직] 스크립트 정지 시 백그라운드 스레드들도 강제 종료
    if not is_running:
        # 1. 카메라 강제 종료
        if camera_state['status'] in ['Running', 'Starting...']:
            camera_command_queue.append(('STOP', ''))
            
        # 2. 센더 강제 종료
        if sender_state['status'] in ['Running']:
            sender_command_queue.append(('STOP', ''))
            
        # 3. (안전장치) 로봇의 움직임도 즉시 정지
        global go1_node_intent
        go1_node_intent['stop'] = True
        go1_node_intent['vx'] = 0.0
        go1_node_intent['vy'] = 0.0
        go1_node_intent['wz'] = 0.0
        write_log("System: Script Stopped. Halting all background tasks.")
        
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

# ================= [Main Setup & Cleanup] =================
import atexit

# ★ [추가된 부분] 프로그램 시작 시 자동 네트워크 길뚫기 (라우팅)
def setup_go1_routing():
    try:
        # 현재 우분투의 라우팅 테이블을 읽어옵니다.
        routes = subprocess.check_output(['ip', 'route']).decode()
        
        # 길이 안 뚫려 있다면 자동으로 뚫어줍니다.
        if "192.168.123.0/24 via 192.168.50.159" not in routes:
            print("\n" + "="*60)
            print("[System] Go1 카메라망(123.x) 접속을 위한 자동 길뚫기를 시작합니다.")
            print("[System] 🚨 아래에 우분투 노트북의 '로그인 비밀번호'를 입력하고 엔터를 치세요!")
            print("="*60 + "\n")
            
            # sudo 권한으로 명령어 실행 (터미널에서 비밀번호를 물어봄)
            subprocess.call(['sudo', 'ip', 'route', 'add', '192.168.123.0/24', 'via', '192.168.50.159'])
            write_log("System: Go1 Network Routing Configured.")
        else:
            write_log("System: Go1 Network Routing Already Exists.")
    except Exception as e:
        write_log(f"System: Routing setup error: {e}")

setup_go1_routing() # 스크립트 실행 시 즉시 호출

def force_cleanup_cameras():
    write_log("System: Cleaning up ghost camera processes...")
    subprocess.call("pkill -f 'gst-launch-1.0'", shell=True)
    time.sleep(0.5)
    for config in CAMERA_CONFIG:
        folder = config["folder"]
        if os.path.exists(folder):
            try:
                for f in glob.glob(os.path.join(folder, "*.jpg")): os.remove(f)
            except: pass

force_cleanup_cameras()
atexit.register(force_cleanup_cameras)

init_mt4_serial()
threading.Thread(target=auto_reconnect_mt4_thread, daemon=True).start()
threading.Thread(target=network_monitor_thread, daemon=True).start()
threading.Thread(target=go1_v4_comm_thread, daemon=True).start()
threading.Thread(target=camera_worker_thread, daemon=True).start()
threading.Thread(target=sender_manager_thread, daemon=True).start()
threading.Thread(target=go1_vision_worker_thread, daemon=True).start()
threading.Thread(target=global_image_cleanup_thread, daemon=True).start()
threading.Thread(target=start_flask_app, daemon=True).start()
threading.Thread(target=lambda: (time.sleep(1), update_file_list()), daemon=True).start()
threading.Thread(target=mt4_background_logger_thread, daemon=True).start() # ★ 추가
threading.Thread(target=ep_comm_thread, daemon=True).start()

dpg.create_context()
with dpg.handler_registry(): dpg.add_key_press_handler(dpg.mvKey_Delete, callback=delete_selection)

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
                    dpg.add_text("MT4 Status", color=(150,150,150)); 
                    dpg.add_text("Status: Idle", tag="mt4_dash_status", color=(0,255,0))
                    dpg.add_text(f"HW: {mt4_dashboard['hw_link']}", tag="mt4_dash_link", color=(0,255,0) if mt4_dashboard["hw_link"]=="Online" else (255,0,0))
                    dpg.add_text("Latency: 0.0 ms", tag="mt4_dash_latency", color=(255,255,0))
                with dpg.child_window(width=350, height=130, border=True):
                    dpg.add_text("Manual Control (10mm, Grip 5mm)", color=(255,200,0))
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
                # 기존 "Coords" 자식 윈도우 아래쪽 또는 옆에 추가
                with dpg.child_window(width=200, height=155, border=True):
                    dpg.add_text("Record & Play", color=(255,100,200))
                    dpg.add_input_text(tag="path_name_input", default_value="my_path", width=130) # ★ 파일 이름 입력창 추가
                    dpg.add_button(label="Start Recording", tag="btn_mt4_record", width=130, callback=lambda s,a,u: toggle_mt4_record())
                    dpg.add_combo(items=get_mt4_paths(), tag="combo_mt4_path", width=130)
                    dpg.add_button(label="Play Selected", width=130, callback=play_mt4_path)

        with dpg.tab(label="Go1 Dashboard"):
            with dpg.group(horizontal=True):
                with dpg.child_window(width=250, height=150, border=True):
                    dpg.add_text("Go1 Status", color=(150,150,150))
                    dpg.add_text(f"HW: {go1_dashboard['hw_link']}", tag="go1_dash_link", color=(0,255,0))
                    dpg.add_text(f"Unity: Waiting", tag="go1_dash_unity", color=(255,255,0))
                    dpg.add_text(f"File Cam: Stopped", tag="go1_dash_cam", color=(200,200,200))
                    dpg.add_text("ArUco: OFF", tag="go1_dash_aruco", color=(200,200,200))
                    dpg.add_text("Battery: -%", tag="go1_dash_battery", color=(100,255,100))
                    btn_estop = dpg.add_button(label="[ EMERGENCY STOP ]", width=-1, height=25, callback=go1_estop_callback)
                    dpg.bind_item_theme(btn_estop, estop_theme)
                with dpg.child_window(width=300, height=150, border=True):
                    dpg.add_text("Odometry", color=(0,255,255))
                    dpg.add_text("World X: 0.000", tag="go1_dash_wx")
                    dpg.add_text("World Z: 0.000", tag="go1_dash_wz")
                    dpg.add_text("Yaw: 0.000 rad", tag="go1_dash_yaw")
                    dpg.add_text("Reason: NONE", tag="go1_dash_reason", color=(200,200,200))
                with dpg.child_window(width=250, height=150, border=True):
                    dpg.add_text("Commands", color=(255,200,0))
                    dpg.add_text("Vx Cmd: 0.00", tag="go1_dash_vx_2")
                    dpg.add_text("Vy Cmd: 0.00", tag="go1_dash_vy_2")
                    dpg.add_text("Wz Cmd: 0.00", tag="go1_dash_wz_2")
                # ★ [새로 추가할 부분: 세 번째 박스 Network Info] ★
                with dpg.child_window(width=260, height=150, border=True):
                    dpg.add_text("Network Info", color=(100,200,255))
                    dpg.add_text(f"Host IP: {get_local_ip()}", color=(200,200,200))
                    dpg.add_text("Relay (Pi): 192.168.50.159", color=(200,200,200))
                    dpg.add_separator()
                    dpg.add_text("Cam 1 (Front): 192.168.123.13")
                    dpg.add_text("Cam 2 (L / R): 192.168.123.14")
                    dpg.add_text("Cam 3 (Bottom): 192.168.123.15")

        with dpg.tab(label="EP Dashboard"):
            with dpg.group(horizontal=True):
                with dpg.child_window(width=250, height=150, border=True):
                    dpg.add_text("EP Status", color=(150,150,150))
                    dpg.add_text(f"HW: {ep_dashboard['hw_link']}", tag="ep_dash_link", color=(0,255,0))
                    dpg.add_text("Battery: -%", tag="ep_dash_battery", color=(100,255,100))
                    dpg.add_text("SN: Unknown", tag="ep_dash_sn", color=(200,200,200))
                    dpg.add_spacer(height=5)
                    with dpg.group(horizontal=True):
                        # ★ 두 개의 네트워크 모드 접속 버튼
                        dpg.add_button(label="Conn STA", callback=btn_connect_ep_sta, width=80)
                        dpg.add_button(label="Conn AP", callback=btn_connect_ep_ap, width=80)
                with dpg.child_window(width=300, height=150, border=True):
                    dpg.add_text("Odometry", color=(0,255,255))
                    dpg.add_text("Pos X: 0.000", tag="ep_dash_px")
                    dpg.add_text("Pos Y: 0.000", tag="ep_dash_py")
                    dpg.add_text("Speed: 0.000", tag="ep_dash_spd")
                    dpg.add_text("Accel Z: 0.000", tag="ep_dash_acc")
                with dpg.child_window(width=250, height=150, border=True):
                    dpg.add_text("Commands", color=(255,200,0))
                    dpg.add_text("Vx Cmd: 0.00", tag="ep_dash_vx")
                    dpg.add_text("Vy Cmd: 0.00", tag="ep_dash_vy")
                    dpg.add_text("Wz Cmd: 0.00", tag="ep_dash_wz")

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
                    dpg.add_text("Loading...", tag="sys_tab_net", color=(180,180,180)) # ★ 태그 달기

    dpg.add_separator()
    
    with dpg.group():
        with dpg.group(horizontal=True):
            dpg.add_text("Common:", color=(200,200,200))
            dpg.add_button(label="START", callback=add_node_cb, user_data="START")
            dpg.add_button(label="IF", callback=add_node_cb, user_data="LOGIC_IF")
            dpg.add_button(label="LOOP", callback=add_node_cb, user_data="LOGIC_LOOP")
            dpg.add_button(label="CHK KEY", callback=add_node_cb, user_data="COND_KEY")
            dpg.add_button(label="CONST", callback=add_node_cb, user_data="CONSTANT")
            dpg.add_button(label="PRINT", callback=add_node_cb, user_data="PRINT")
            dpg.add_button(label="LOG", callback=add_node_cb, user_data="LOGGER")
            
            dpg.add_spacer(width=30)
            
            dpg.add_text("MT4 Tools:", color=(255,200,0))
            dpg.add_button(label="DRIVER", callback=add_node_cb, user_data="MT4_DRIVER")
            dpg.add_button(label="ACTION", callback=add_node_cb, user_data="MT4_ACTION")
            dpg.add_button(label="KEY", callback=add_node_cb, user_data="MT4_KEYBOARD")
            dpg.add_button(label="UNITY", callback=add_node_cb, user_data="MT4_UNITY")
            dpg.add_button(label="UDP", callback=add_node_cb, user_data="UDP_RECV")
            
            dpg.add_spacer(width=50)
            dpg.add_button(label="RUN SCRIPT", tag="btn_run", callback=toggle_exec, width=150)

        with dpg.group(horizontal=True):
            dpg.add_text("Go1 Tools:", color=(0,255,255))
            dpg.add_button(label="DRIVER", callback=add_node_cb, user_data="GO1_DRIVER")
            dpg.add_button(label="ACTION", callback=add_node_cb, user_data="GO1_ACTION")
            dpg.add_button(label="TIMED", callback=add_node_cb, user_data="GO1_TIMED")
            dpg.add_button(label="KEY", callback=add_node_cb, user_data="GO1_KEYBOARD")
            dpg.add_button(label="UNITY", callback=add_node_cb, user_data="GO1_UNITY")
            dpg.add_button(label="FILE_CAM", callback=add_node_cb, user_data="CAM_CTRL")
            dpg.add_button(label="TARGET_IP", callback=add_node_cb, user_data="TARGET_IP")
            dpg.add_button(label="SENDER", callback=add_node_cb, user_data="MULTI_SENDER")
            dpg.add_button(label="GO1_STATE", callback=add_node_cb, user_data="GET_GO1_STATE")

            dpg.add_text("EP Tools:", color=(100,255,100))
            dpg.add_button(label="DRIVER", callback=add_node_cb, user_data="EP_DRIVER")
            dpg.add_button(label="KEY", callback=add_node_cb, user_data="EP_KEYBOARD")

        

    with dpg.node_editor(tag="node_editor", callback=link_cb, delink_callback=del_link_cb): pass

dpg.create_viewport(title='PyGui V9', width=1280, height=800, vsync=True)
dpg.setup_dearpygui(); dpg.set_primary_window("PrimaryWindow", True); dpg.show_viewport()

last_logic_time = 0; LOGIC_RATE = 0.02

while dpg.is_dearpygui_running():
    if mt4_dashboard["last_pkt_time"] > 0: dpg.set_value("mt4_dash_status", f"Status: {mt4_dashboard['status']}")
    dpg.set_value("mt4_x", f"X: {mt4_current_pos['x']:.1f}"); dpg.set_value("mt4_y", f"Y: {mt4_current_pos['y']:.1f}")
    dpg.set_value("mt4_z", f"Z: {mt4_current_pos['z']:.1f}"); dpg.set_value("mt4_g", f"G: {mt4_current_pos['gripper']:.1f}")

    mt4_hw_str = mt4_dashboard.get('hw_link', 'Offline')
    dpg.set_value("mt4_dash_link", f"HW: {mt4_hw_str}")
    if mt4_hw_str == "Online": dpg.configure_item("mt4_dash_link", color=(0,255,0))
    elif mt4_hw_str == "Simulation": dpg.configure_item("mt4_dash_link", color=(255,200,0))
    else: dpg.configure_item("mt4_dash_link", color=(255,0,0))

    hw_link_str = go1_dashboard.get('hw_link', 'Offline')
    dpg.set_value("go1_dash_link", f"HW: {hw_link_str}")
    
    # ★ "Online"이라는 단어가 포함되어 있으면(Active, Listen 둘 다) 모두 초록색으로 렌더링
    if "Online" in hw_link_str: dpg.configure_item("go1_dash_link", color=(0,255,0))
    elif hw_link_str == "Simulation" or hw_link_str == "Connecting...": dpg.configure_item("go1_dash_link", color=(255,200,0))
    else: dpg.configure_item("go1_dash_link", color=(255,0,0))
    
    dpg.set_value("go1_dash_unity", f"Unity: {go1_dashboard['unity_link']}")
    dpg.set_value("go1_dash_reason", f"Mode: {go1_state['mode']} | {go1_state['reason']}")
    dpg.set_value("go1_dash_wx", f"World X: {go1_state['world_x']:.3f}")
    dpg.set_value("go1_dash_wz", f"World Z: {go1_state['world_z']:.3f}")
    dpg.set_value("go1_dash_yaw", f"Yaw: {go1_state['yaw_unity']:.3f} rad")
    dpg.set_value("go1_dash_vx_2", f"Vx Cmd: {go1_state['vx_cmd']:.2f}")
    dpg.set_value("go1_dash_vy_2", f"Vy Cmd: {go1_state['vy_cmd']:.2f}")
    dpg.set_value("go1_dash_wz_2", f"Wz Cmd: {go1_state['wz_cmd']:.2f}")
    
    bat_val = go1_state.get('battery', -1)
    if bat_val >= 0: dpg.set_value("go1_dash_battery", f"Battery: {bat_val}%")
    else: dpg.set_value("go1_dash_battery", "Battery: 100% (Sim)")
    
    # File Cam (V4) Dashboard UI
    dpg.set_value("go1_dash_cam", f"File Cam: {camera_state['status']}")
    if camera_state['status'] == 'Running': dpg.configure_item("go1_dash_cam", color=(0,255,0))
    elif camera_state['status'] == 'Stopped': dpg.configure_item("go1_dash_cam", color=(200,200,200))
    else: dpg.configure_item("go1_dash_cam", color=(255,200,0))
    
    # ArUco State UI
    is_aruco_on = (aruco_settings['enabled'] and camera_state['status'] == 'Running')
    if is_aruco_on and HAS_CV2_FLASK: dpg.configure_item("go1_dash_aruco", default_value="ArUco: ON (Port 5000)", color=(0,255,255))
    else: dpg.configure_item("go1_dash_aruco", default_value="ArUco: OFF", color=(200,200,200))

    if dpg.does_item_exist("dash_host_ip"): dpg.set_value("dash_host_ip", sys_net_str)
    if dpg.does_item_exist("sys_tab_net"): dpg.set_value("sys_tab_net", sys_net_str)

    # [EP Dashboard Update]
    ep_link_str = ep_dashboard.get('hw_link', 'Offline')
    dpg.set_value("ep_dash_link", f"HW: {ep_link_str}")
    if "Online" in ep_link_str: dpg.configure_item("ep_dash_link", color=(0,255,0))
    elif "Connecting" in ep_link_str: dpg.configure_item("ep_dash_link", color=(255,200,0))
    else: dpg.configure_item("ep_dash_link", color=(255,0,0))
    
    dpg.set_value("ep_dash_battery", f"Battery: {ep_state['battery']}%" if ep_state['battery'] >= 0 else "Battery: -%")
    dpg.set_value("ep_dash_sn", f"SN: {ep_dashboard['sn']}")
    dpg.set_value("ep_dash_px", f"Pos X: {ep_state['pos_x']:.3f}"); dpg.set_value("ep_dash_py", f"Pos Y: {ep_state['pos_y']:.3f}")
    dpg.set_value("ep_dash_spd", f"Speed: {ep_state['speed']:.3f}"); dpg.set_value("ep_dash_acc", f"Accel Z: {ep_state['accel_z']:.3f}")
    dpg.set_value("ep_dash_vx", f"Vx Cmd: {ep_node_intent['vx']:.2f}"); dpg.set_value("ep_dash_vy", f"Vy Cmd: {ep_node_intent['vy']:.2f}")
    dpg.set_value("ep_dash_wz", f"Wz Cmd: {ep_node_intent['wz']:.2f}")
    
    if is_running and (time.time() - last_logic_time > LOGIC_RATE):
        execute_graph_once()
        last_logic_time = time.time()
        
    dpg.render_dearpygui_frame()

dpg.destroy_context()