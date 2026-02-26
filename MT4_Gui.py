import sys
import time
import math
import socket
import select
import threading
import json
import os
import subprocess
import serial 
import platform 
import dearpygui.dearpygui as dpg
import csv
from collections import deque
from abc import ABC, abstractmethod
from datetime import datetime
from enum import Enum, auto

# ================= [Refactoring: Enum 적용 (문자열 비교 제거)] =================
class HwStatus(Enum):
    OFFLINE = auto()
    ONLINE = auto()
    SIMULATION = auto()

class PortType(Enum):
    FLOW = auto()
    DATA = auto()

# ================= [Global Core Settings] =================
node_registry = {}
link_registry = {}
is_running = False
SAVE_DIR = "Node_File_MT4"
if not os.path.exists(SAVE_DIR): os.makedirs(SAVE_DIR)
system_log_buffer = deque(maxlen=50)

def write_log(msg):
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {msg}")
    system_log_buffer.append(f"[{timestamp}] {msg}")

def get_local_ip():
    try: s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(('8.8.8.8', 80)); ip = s.getsockname()[0]; s.close(); return ip
    except: return "127.0.0.1"

def get_save_files():
    if not os.path.exists(SAVE_DIR): return []
    return [f for f in os.listdir(SAVE_DIR) if f.endswith(".json")]

sys_net_str = "Loading Network..."
def network_monitor_thread():
    global sys_net_str
    while True:
        try:
            out = subprocess.check_output("ip -o -4 addr show", shell=True).decode('utf-8')
            info = []
            for line in out.strip().split('\n'):
                if ' lo ' in line: continue
                p = line.split()
                if len(p) >= 4:
                    dev, ip = p[1], p[3].split('/')[0]
                    ssid = ""
                    if dev.startswith('wl'):
                        try: ssid = subprocess.check_output(['iwgetid', dev, '-r']).decode('utf-8').strip()
                        except: pass
                    info.append(f"[{dev}] {ip} ({ssid})" if ssid else f"[{dev}] {ip}")
            sys_net_str = "\n".join(info) if info else "Offline"
        except: pass
        time.sleep(2)

# ================= [MT4 State & Config] =================
ser = None 
mt4_current_pos = {'x': 200.0, 'y': 0.0, 'z': 120.0, 'gripper': 40.0}
mt4_target_goal = {'x': 200.0, 'y': 0.0, 'z': 120.0, 'gripper': 40.0} 
mt4_manual_override_until = 0.0 
mt4_dashboard = {"status": "Idle", "hw_link": HwStatus.OFFLINE, "last_pkt_time": 0.0}

PATH_DIR = "path_record"
LOG_DIR = "result_log"
os.makedirs(PATH_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

mt4_mode = {"recording": False, "playing": False}
mt4_collision_lock_until = 0.0
mt4_record_f = None
mt4_record_writer = None
mt4_record_temp_name = ""
mt4_log_event_queue = deque()

MT4_UNITY_IP = "192.168.50.63"; MT4_FEEDBACK_PORT = 5005
MT4_LIMITS = {'min_x': 100, 'max_x': 280, 'min_y': -200, 'max_y': 200, 'min_z': 0, 'max_z': 280}
MT4_GRIPPER_MIN = 30.0; MT4_GRIPPER_MAX = 60.0
MT4_Z_OFFSET = 90.0

def send_unity_ui(msg_type, extra_data):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        msg = f"type:{msg_type},extra:{extra_data}"
        sock.sendto(msg.encode('utf-8'), (MT4_UNITY_IP, 5007))
    except: pass

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
        
    # [Refactoring: Dict -> List 기반 스키마로 변경 (검색용이 아님)]
    def get_ui_schema(self): 
        return [
            ('x', "X", 200.0), 
            ('y', "Y", 0.0), 
            ('z', "Z", 120.0), 
            ('gripper', "G", 40.0)
        ]
        
    def get_settings_schema(self): 
        return [('smooth', "Smth", 1.0)]
        
    def execute_command(self, inputs, settings):
        global mt4_current_pos, mt4_target_goal, mt4_manual_override_until, ser
        if time.time() < mt4_collision_lock_until: return 
        
        if time.time() > mt4_manual_override_until:
            for key, _, _ in self.get_ui_schema():
                if inputs.get(key) is not None: mt4_target_goal[key] = float(inputs[key])
                
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
                except: mt4_dashboard["hw_link"] = HwStatus.OFFLINE
                self.last_cmd = cmd
        mt4_current_pos.update(new_state)
        return new_state

class BaseNode(ABC):
    def __init__(self, node_id, label, type_str):
        self.node_id = node_id; self.label = label; self.type_str = type_str
        self.inputs = {}; self.outputs = {}; self.output_data = {} 
        
    # [Refactoring: 갓 클래스 탈피 - Node 내부에서 UI 그리는 build_ui() 삭제!]
    # 이제 노드는 오직 데이터와 실행(execute) 로직만 가집니다.

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
        super().__init__(node_id, "MT4 Driver", "MT4_DRIVER")
        self.driver = driver_instance
        self.schema = self.driver.get_ui_schema()
        self.settings_schema = self.driver.get_settings_schema()
        self.in_pins = {}; self.ui_fields = {}; self.setting_pins = {}; self.setting_fields = {}
        self.cache_ui = {key: 0.0 for key, _, _ in self.schema}

    def execute(self):
        fetched_inputs = {key: self.fetch_input_data(aid) for key, aid in self.in_pins.items()}
        fetched_settings = {}
        for key, aid in self.setting_pins.items():
            val = self.fetch_input_data(aid)
            if val is not None: dpg.set_value(self.setting_fields[key], float(val))
            fetched_settings[key] = dpg.get_value(self.setting_fields[key])
            
        new_state = self.driver.execute_command(fetched_inputs, fetched_settings)
        if new_state:
            for key, _, _ in self.schema:
                if key in new_state and abs(self.cache_ui[key] - new_state[key]) > 0.1:
                    dpg.set_value(self.ui_fields[key], new_state[key]); self.cache_ui[key] = new_state[key]
        for k, v in self.outputs.items():
            if v == PortType.FLOW: return k
        return None
        
    def get_settings(self): return {k: dpg.get_value(v) for k, v in self.setting_fields.items()}
    def load_settings(self, data): 
        for k, v in self.setting_fields.items():
            if k in data: dpg.set_value(v, data[k])

# ================= [Refactoring: OCP 준수를 위한 UI Renderer 분리] =================
class NodeUIRenderer:
    """
    노드 객체를 주입받아 GUI(DPG)를 대신 그려주는 클라이언트 클래스.
    이렇게 하면 향후 DPG가 아닌 다른 GUI 툴로 바꿔도 노드 로직은 전혀 수정할 필요가 없습니다.
    """
    @staticmethod
    def render(node):
        if isinstance(node, UniversalRobotNode):
            NodeUIRenderer._render_universal(node)
        elif isinstance(node, MT4KeyboardNode):
            NodeUIRenderer._render_keyboard(node)
        # (나머지 노드들도 동일하게 분리 적용 가능)
        elif isinstance(node, StartNode):
            with dpg.node(tag=node.node_id, parent="node_editor", label="START"):
                with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out: 
                    dpg.add_text("Flow Out"); node.outputs[out] = PortType.FLOW; node.out = out

    @staticmethod
    def _render_universal(node):
        with dpg.node(tag=node.node_id, parent="node_editor", label=node.label):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as flow: 
                dpg.add_text("Flow In"); node.inputs[flow] = PortType.FLOW
            
            # List 형태의 스키마 순회
            for key, label, default_val in node.schema:
                with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as aid:
                    with dpg.group(horizontal=True): 
                        dpg.add_text(label, color=(255,255,0))
                        node.ui_fields[key] = dpg.add_input_float(width=80, default_value=default_val, step=0)
                    node.inputs[aid] = PortType.DATA; node.in_pins[key] = aid
            
            dpg.add_node_attribute(attribute_type=dpg.mvNode_Attr_Static) # Spacer
            
            for key, label, default_val in node.settings_schema:
                with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as aid:
                    with dpg.group(horizontal=True): 
                        dpg.add_text(label)
                        node.setting_fields[key] = dpg.add_input_float(width=60, default_value=default_val, step=0)
                    node.inputs[aid] = PortType.DATA; node.setting_pins[key] = aid
                    
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as fout: 
                dpg.add_text("Flow Out"); node.outputs[fout] = PortType.FLOW

    @staticmethod
    def _render_keyboard(node):
        with dpg.node(tag=node.node_id, parent="node_editor", label="MT4 Keyboard"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as flow: 
                dpg.add_text("Flow In"); node.inputs[flow] = PortType.FLOW
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                node.combo_keys = dpg.add_combo(["WASD", "Arrow Keys"], default_value="WASD", width=120)
                dpg.add_text("XY Move / QE: Z / UJ: Grip", color=(255,150,150))
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as x: 
                dpg.add_text("Target X"); node.outputs[x] = PortType.DATA; node.out_x = x
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as y: 
                dpg.add_text("Target Y"); node.outputs[y] = PortType.DATA; node.out_y = y
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as z: 
                dpg.add_text("Target Z"); node.outputs[z] = PortType.DATA; node.out_z = z
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as g: 
                dpg.add_text("Target Grip"); node.outputs[g] = PortType.DATA; node.out_g = g
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as f: 
                dpg.add_text("Flow Out"); node.outputs[f] = PortType.FLOW

# ================= [MT4 Dashboard & Threads] =================
def mt4_manual_control_callback(sender, app_data, user_data):
    global mt4_manual_override_until, mt4_target_goal, mt4_current_pos
    mt4_manual_override_until = time.time() + 1.5; axis, step = user_data; mt4_target_goal[axis] = mt4_current_pos[axis] + step; mt4_apply_limits()

def mt4_move_to_coord_callback(sender, app_data, user_data):
    global mt4_manual_override_until, mt4_target_goal
    mt4_manual_override_until = time.time() + 2.0; mt4_target_goal['x'] = float(dpg.get_value("input_x")); mt4_target_goal['y'] = float(dpg.get_value("input_y"))
    mt4_target_goal['z'] = float(dpg.get_value("input_z")); mt4_target_goal['gripper'] = float(dpg.get_value("input_g")); mt4_apply_limits()

def mt4_apply_limits():
    global mt4_target_goal
    if time.time() < mt4_collision_lock_until: return
    mt4_target_goal['x'] = max(MT4_LIMITS['min_x'], min(mt4_target_goal['x'], MT4_LIMITS['max_x']))
    mt4_target_goal['y'] = max(MT4_LIMITS['min_y'], min(mt4_target_goal['y'], MT4_LIMITS['max_y']))
    mt4_target_goal['z'] = max(MT4_LIMITS['min_z'], min(mt4_target_goal['z'], MT4_LIMITS['max_z']))
    mt4_target_goal['gripper'] = max(MT4_GRIPPER_MIN, min(mt4_target_goal['gripper'], MT4_GRIPPER_MAX))

def get_mt4_paths(): return [f for f in os.listdir(PATH_DIR) if f.endswith(".csv")]

def toggle_mt4_record(custom_name=None):
    global mt4_record_f, mt4_record_writer, mt4_record_temp_name
    if mt4_mode["recording"]:
        mt4_mode["recording"] = False
        if mt4_record_f: mt4_record_f.close()
        
        if not custom_name and dpg.does_item_exist("path_name_input"):
            custom_name = dpg.get_value("path_name_input")
            
        if custom_name and mt4_record_temp_name:
            if not custom_name.endswith(".csv"): custom_name += ".csv"
            final_path = os.path.join(PATH_DIR, custom_name)
            try: os.rename(mt4_record_temp_name, final_path)
            except: pass
                
        dpg.set_item_label("btn_mt4_record", "Start Recording")
        if dpg.does_item_exist("combo_mt4_path"): dpg.configure_item("combo_mt4_path", items=get_mt4_paths())
        
        write_log(f"Path Saved: {custom_name}")
        send_unity_ui("STATUS", f"저장 완료: {custom_name}")
        send_unity_ui("FILE_LIST", f"[{'|'.join(get_mt4_paths())}]")
    else:
        mt4_mode["recording"] = True
        fname = os.path.join(PATH_DIR, f"path_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
        mt4_record_temp_name = fname
        mt4_record_f = open(fname, 'w', newline='')
        mt4_record_writer = csv.writer(mt4_record_f)
        mt4_record_writer.writerow(['x', 'y', 'z', 'gripper'])
        dpg.set_item_label("btn_mt4_record", "Stop Recording")
        write_log("Path Recording Started.")
        send_unity_ui("STATUS", "경로 녹화 시작...")

def play_mt4_path(sender=None, app_data=None, user_data=None, filename=None):
    if not filename: filename = dpg.get_value("combo_mt4_path")
    if not filename or mt4_mode["playing"] or time.time() < mt4_collision_lock_until: return
    filepath = os.path.join(PATH_DIR, filename)
    if os.path.exists(filepath): threading.Thread(target=play_mt4_path_thread, args=(filepath,), daemon=True).start()

def play_mt4_path_thread(filepath):
    global mt4_mode, mt4_target_goal, mt4_manual_override_until
    mt4_mode["playing"] = True
    mt4_manual_override_until = time.time() + 86400 
    write_log(f"Playing path: {os.path.basename(filepath)}")
    send_unity_ui("STATUS", f"재생 중: {os.path.basename(filepath)}")
    try:
        with open(filepath, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if time.time() < mt4_collision_lock_until or not mt4_mode["playing"]: break
                mt4_target_goal['x'] = float(row['x']); mt4_target_goal['y'] = float(row['y'])
                mt4_target_goal['z'] = float(row['z']); mt4_target_goal['gripper'] = float(row['gripper'])
                mt4_apply_limits()
                time.sleep(0.05)
    except Exception as e: write_log(f"Play Error: {e}")
    mt4_mode["playing"] = False; mt4_manual_override_until = time.time()
    send_unity_ui("STATUS", "경로 재생 완료")

def mt4_background_logger_thread():
    global mt4_record_writer
    log_filename = os.path.join(LOG_DIR, f"mt4_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
    with open(log_filename, 'w', newline='') as mt4_log_f:
        mt4_log_writer = csv.writer(mt4_log_f)
        mt4_log_writer.writerow(['timestamp', 'event', 'target_x', 'target_y', 'target_z', 'target_g', 'current_x', 'current_y', 'current_z', 'current_g'])
        
        while True:
            time.sleep(0.05)
            event_str = "TICK"
            if mt4_log_event_queue: event_str = mt4_log_event_queue.popleft()
            
            mt4_log_writer.writerow([time.time(), event_str, mt4_target_goal['x'], mt4_target_goal['y'], mt4_target_goal['z'], mt4_target_goal['gripper'], mt4_current_pos['x'], mt4_current_pos['y'], mt4_current_pos['z'], mt4_current_pos['gripper']])
            mt4_log_f.flush()
            
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
        ser = serial.Serial('/dev/ttyUSB0', 115200, timeout=0.05); mt4_dashboard["hw_link"] = HwStatus.ONLINE; write_log("System: MT4 Connected")
        time.sleep(2); ser.write(b"$H\r\n"); time.sleep(15); ser.write(b"M20\r\n"); ser.write(b"G90\r\n"); ser.write(b"G1 F2000\r\n"); time.sleep(1)
        ser.write(b"G0 X200 Y0 Z120 F2000\r\n"); ser.write(b"M3 S40\r\n") 
    except Exception as e: mt4_dashboard["hw_link"] = HwStatus.SIMULATION; write_log(f"MT4 Sim Mode ({e})"); ser = None

def auto_reconnect_mt4_thread():
    global ser
    while True:
        if ser is None and os.path.exists('/dev/ttyUSB0'):
            try: init_mt4_serial() 
            except: pass
        time.sleep(3) 

# ================= [Nodes Logic Execution] =================
class StartNode(BaseNode):
    def __init__(self, node_id): super().__init__(node_id, "START", "START")
    def execute(self): return self.out 

class MT4KeyboardNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "Keyboard (MT4)", "MT4_KEYBOARD")
        self.out_x = None; self.out_y = None; self.out_z = None; self.out_g = None
        self.step_size = 10.0; self.grip_step = 5.0; self.cooldown = 0.2; self.last_input_time = 0.0
        self.combo_keys = None 

    def execute(self):
        if dpg.is_item_focused("file_name_input") or (dpg.does_item_exist("path_name_input") and dpg.is_item_focused("path_name_input")):
            for k, v in self.outputs.items():
                if v == PortType.FLOW: return k
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
            if v == PortType.FLOW: return k
        return None
        
    def get_settings(self): return {"keys": dpg.get_value(self.combo_keys)}
    def load_settings(self, data): dpg.set_value(self.combo_keys, data.get("keys", "WASD"))

def execute_graph_once():
    start_node = None
    for node in node_registry.values():
        if isinstance(node, StartNode): start_node = node; break
    
    for node in node_registry.values():
        if not isinstance(node, (StartNode, MT4KeyboardNode)):
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
                    if v == PortType.FLOW: next_out_id = k; break
        next_node = None
        if next_out_id:
            for link in link_registry.values():
                if link['source'] == next_out_id:
                    target_node_id = dpg.get_item_parent(link['target'])
                    if target_node_id in node_registry:
                        next_node = node_registry[target_node_id]; break
        current_node = next_node; steps += 1

# ================= [Factory] =================
class NodeFactory:
    @staticmethod
    def create_node(node_type, node_id=None):
        if node_id is None: node_id = dpg.generate_uuid()
        node = None
        if node_type == "START": node = StartNode(node_id)
        elif node_type == "MT4_DRIVER": node = UniversalRobotNode(node_id, MT4RobotDriver())
        elif node_type == "MT4_KEYBOARD": node = MT4KeyboardNode(node_id)
        
        if node: 
            # 팩토리에서 노드를 생성한 후, 렌더러에게 주입하여 UI를 그리게 함 (의존성 분리)
            NodeUIRenderer.render(node)
            node_registry[node_id] = node
            return node
        return None

def toggle_exec(s, a): 
    global is_running
    is_running = not is_running
    dpg.set_item_label("btn_run", "STOP" if is_running else "RUN SCRIPT")

def link_cb(s, a): src, dst = a[0], a[1] if len(a)==2 else a[1]; lid = dpg.add_node_link(src, dst, parent=s); link_registry[lid] = {'source': src, 'target': dst}
def del_link_cb(s, a): dpg.delete_item(a); link_registry.pop(a, None)
def add_node_cb(s, a, u): NodeFactory.create_node(u)

# ================= [Main Setup & GUI Initialization] =================
init_mt4_serial()
threading.Thread(target=auto_reconnect_mt4_thread, daemon=True).start()
threading.Thread(target=network_monitor_thread, daemon=True).start()
threading.Thread(target=mt4_background_logger_thread, daemon=True).start() 

dpg.create_context()
with dpg.handler_registry(): pass

with dpg.window(tag="PrimaryWindow"):
    with dpg.tab_bar():
        with dpg.tab(label="MT4 Dashboard"):
            with dpg.group(horizontal=True):
                with dpg.child_window(width=250, height=130, border=True):
                    dpg.add_text("MT4 Status", color=(150,150,150)); 
                    dpg.add_text("Status: Idle", tag="mt4_dash_status", color=(0,255,0))
                    dpg.add_text(f"HW: Offline", tag="mt4_dash_link", color=(255,0,0))
                with dpg.child_window(width=350, height=130, border=True):
                    dpg.add_text("Manual Control", color=(255,200,0))
                    with dpg.group(horizontal=True):
                        dpg.add_button(label="X+", width=60, callback=mt4_manual_control_callback, user_data=('x', 10)); dpg.add_button(label="X-", width=60, callback=mt4_manual_control_callback, user_data=('x', -10))
                    with dpg.group(horizontal=True):
                        dpg.add_button(label="Z+", width=60, callback=mt4_manual_control_callback, user_data=('z', 10)); dpg.add_button(label="Z-", width=60, callback=mt4_manual_control_callback, user_data=('z', -10))
                with dpg.child_window(width=300, height=130, border=True):
                    dpg.add_text("Direct Coord", color=(0,255,255))
                    with dpg.group(horizontal=True):
                        dpg.add_text("X"); dpg.add_input_int(tag="input_x", width=50, default_value=200, step=0)
                    with dpg.group(horizontal=True):
                        dpg.add_button(label="Move", width=100, callback=mt4_move_to_coord_callback)
                        dpg.add_button(label="Homing", width=100, callback=mt4_homing_callback)
                with dpg.child_window(width=150, height=130, border=True):
                    dpg.add_text("Coords", color=(0,255,255))
                    dpg.add_text("X: 0", tag="mt4_x"); dpg.add_text("Y: 0", tag="mt4_y")
                    dpg.add_text("Z: 0", tag="mt4_z"); dpg.add_text("G: 0", tag="mt4_g")
                    
        with dpg.tab(label="System"):
            with dpg.group(horizontal=True):
                with dpg.child_window(width=400, height=130, border=True):
                    dpg.add_text("Network Info", color=(100,200,255))
                    dpg.add_text("Loading...", tag="sys_tab_net", color=(180,180,180))

    dpg.add_separator()
    with dpg.group():
        with dpg.group(horizontal=True):
            dpg.add_button(label="START", callback=add_node_cb, user_data="START")
            dpg.add_button(label="DRIVER", callback=add_node_cb, user_data="MT4_DRIVER")
            dpg.add_button(label="KEY", callback=add_node_cb, user_data="MT4_KEYBOARD")
            dpg.add_spacer(width=50)
            dpg.add_button(label="RUN SCRIPT", tag="btn_run", callback=toggle_exec, width=150)

    with dpg.node_editor(tag="node_editor", callback=link_cb, delink_callback=del_link_cb): pass

dpg.create_viewport(title='MT4 Educational V24 - Refactored', width=1280, height=800, vsync=True)
dpg.setup_dearpygui(); dpg.set_primary_window("PrimaryWindow", True); dpg.show_viewport()

last_logic_time = 0; LOGIC_RATE = 0.02

while dpg.is_dearpygui_running():
    if mt4_dashboard["last_pkt_time"] > 0: dpg.set_value("mt4_dash_status", f"Status: {mt4_dashboard['status']}")
    
    dpg.set_value("mt4_x", f"X: {mt4_current_pos['x']:.1f}"); dpg.set_value("mt4_y", f"Y: {mt4_current_pos['y']:.1f}")
    dpg.set_value("mt4_z", f"Z: {mt4_current_pos['z']:.1f}"); dpg.set_value("mt4_g", f"G: {mt4_current_pos['gripper']:.1f}")

    # Enum 객체를 사용한 하드웨어 상태 체크
    hw_status = mt4_dashboard.get('hw_link', HwStatus.OFFLINE)
    if hw_status == HwStatus.ONLINE:
        dpg.set_value("mt4_dash_link", "HW: Online"); dpg.configure_item("mt4_dash_link", color=(0,255,0))
    elif hw_status == HwStatus.SIMULATION:
        dpg.set_value("mt4_dash_link", "HW: Simulation"); dpg.configure_item("mt4_dash_link", color=(255,200,0))
    else:
        dpg.set_value("mt4_dash_link", "HW: Offline"); dpg.configure_item("mt4_dash_link", color=(255,0,0))

    if dpg.does_item_exist("sys_tab_net"): dpg.set_value("sys_tab_net", sys_net_str)
    
    if is_running and (time.time() - last_logic_time > LOGIC_RATE):
        execute_graph_once()
        last_logic_time = time.time()
        
    dpg.render_dearpygui_frame()

dpg.destroy_context()