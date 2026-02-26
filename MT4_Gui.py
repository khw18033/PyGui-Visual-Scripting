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
mt4_dashboard = {"status": "Idle", "hw_link": HwStatus.OFFLINE, "latency": 0.0, "last_pkt_time": 0.0}

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
        
    def get_ui_schema(self): 
        return [('x', "X", 200.0), ('y', "Y", 0.0), ('z', "Z", 120.0), ('gripper', "G", 40.0)]
        
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

# ================= [MT4 Specific & Logic Nodes] =================
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

class MT4CommandActionNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "MT4 Action", "MT4_ACTION")
        self.combo_id = None; self.in_val1 = None; self.in_val2 = None; self.in_val3 = None; self.out_flow = None; self.field_v1 = None; self.field_v2 = None; self.field_v3 = None
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
        elif mode == "Homing": mt4_homing_callback(None, None, None) 
        
        mt4_apply_limits()
        mt4_current_pos.update(mt4_target_goal)
        if ser and ser.is_open: ser.write(f"G0 X{mt4_target_goal['x']:.1f} Y{mt4_target_goal['y']:.1f} Z{mt4_target_goal['z']:.1f}\nM3 S{int(mt4_target_goal['gripper'])}\n".encode())
        return self.out_flow
    def get_settings(self): return {"mode": dpg.get_value(self.combo_id), "v1": dpg.get_value(self.field_v1), "v2": dpg.get_value(self.field_v2), "v3": dpg.get_value(self.field_v3)}
    def load_settings(self, data): dpg.set_value(self.combo_id, data.get("mode", "Move Relative (XYZ)")); dpg.set_value(self.field_v1, data.get("v1", 0)); dpg.set_value(self.field_v2, data.get("v2", 0)); dpg.set_value(self.field_v3, data.get("v3", 0))

class MT4UnityNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "Unity Logic (MT4)", "MT4_UNITY")
        self.data_in_id = None; self.out_x = None; self.out_y = None; self.out_z = None; self.out_g = None
        self.last_processed_json = ""
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
                
                if msg_type == "CMD":
                    val = parsed.get("val", "")
                    if val == "COLLISION":
                        mt4_collision_lock_until = time.time() + 2.0 
                        if ser and ser.is_open: ser.write(b"!") 
                        write_log("Collision Detected! Robot Locked.")
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

                elif msg_type == "MOVE" and not mt4_mode["playing"] and time.time() > mt4_collision_lock_until:
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

class LogicIfNode(BaseNode):
    def __init__(self, node_id): super().__init__(node_id, "Logic: IF", "LOGIC_IF"); self.in_cond = None; self.out_true = None; self.out_false = None
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
    def execute(self):
        if not self.is_active: self.target_iter = dpg.get_value(self.field_count); self.current_iter = 0; self.is_active = True
        if self.current_iter < self.target_iter: self.current_iter += 1; return self.out_loop 
        else: self.is_active = False; return self.out_finish
    def get_settings(self): return {"count": dpg.get_value(self.field_count)}
    def load_settings(self, data): dpg.set_value(self.field_count, data.get("count", 3))

class ConditionKeyNode(BaseNode):
    def __init__(self, node_id): super().__init__(node_id, "Check: Key", "COND_KEY"); self.field_key = None; self.out_res = None; self.key_map = {"A": dpg.mvKey_A, "B": dpg.mvKey_B, "C": dpg.mvKey_C, "S": dpg.mvKey_S, "W": dpg.mvKey_W, "SPACE": dpg.mvKey_Spacebar} 
    def execute(self): k = dpg.get_value(self.field_key).upper(); self.output_data[self.out_res] = dpg.is_key_down(self.key_map.get(k, 0)); return None
    def get_settings(self): return {"k": dpg.get_value(self.field_key)}
    def load_settings(self, data): dpg.set_value(self.field_key, data.get("k", "A"))

class ConstantNode(BaseNode):
    def __init__(self, node_id): super().__init__(node_id, "Constant", "CONSTANT"); self.out_val = None; self.field_val = None
    def execute(self): self.output_data[self.out_val] = dpg.get_value(self.field_val); return None
    def get_settings(self): return {"val": dpg.get_value(self.field_val)}
    def load_settings(self, data): dpg.set_value(self.field_val, data.get("val", 1.0))

class PrintNode(BaseNode):
    def __init__(self, node_id): super().__init__(node_id, "Print Log", "PRINT"); self.out_flow = None; self.inp_data = None
    def execute(self):
        val = self.fetch_input_data(self.inp_data)
        if val is not None: write_log(f"PRINT: {val}")
        return self.out_flow

class LoggerNode(BaseNode):
    def __init__(self, node_id): super().__init__(node_id, "System Log", "LOGGER"); self.txt=None; self.llen=0
    def execute(self):
        if len(system_log_buffer)!=self.llen: dpg.set_value(self.txt, "\n".join(list(system_log_buffer)[-8:])); self.llen=len(system_log_buffer)
        return None

# ================= [Refactoring: OCP 준수를 위한 UI Renderer 완벽 구현] =================
class NodeUIRenderer:
    @staticmethod
    def render(node):
        if isinstance(node, UniversalRobotNode): NodeUIRenderer._render_universal(node)
        elif isinstance(node, MT4KeyboardNode): NodeUIRenderer._render_mt4_keyboard(node)
        elif isinstance(node, MT4CommandActionNode): NodeUIRenderer._render_mt4_action(node)
        elif isinstance(node, MT4UnityNode): NodeUIRenderer._render_mt4_unity(node)
        elif isinstance(node, UDPReceiverNode): NodeUIRenderer._render_udp(node)
        elif isinstance(node, LogicIfNode): NodeUIRenderer._render_logic_if(node)
        elif isinstance(node, LogicLoopNode): NodeUIRenderer._render_logic_loop(node)
        elif isinstance(node, ConditionKeyNode): NodeUIRenderer._render_cond_key(node)
        elif isinstance(node, ConstantNode): NodeUIRenderer._render_constant(node)
        elif isinstance(node, PrintNode): NodeUIRenderer._render_print(node)
        elif isinstance(node, LoggerNode): NodeUIRenderer._render_logger(node)
        elif isinstance(node, StartNode):
            with dpg.node(tag=node.node_id, parent="node_editor", label="START"):
                with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out: 
                    dpg.add_text("Flow Out"); node.outputs[out] = PortType.FLOW; node.out = out

    @staticmethod
    def _render_universal(node):
        with dpg.node(tag=node.node_id, parent="node_editor", label=node.label):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as flow: 
                dpg.add_text("Flow In"); node.inputs[flow] = PortType.FLOW
            for key, label, default_val in node.schema:
                with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as aid:
                    with dpg.group(horizontal=True): 
                        dpg.add_text(label, color=(255,255,0))
                        node.ui_fields[key] = dpg.add_input_float(width=80, default_value=default_val, step=0)
                    node.inputs[aid] = PortType.DATA; node.in_pins[key] = aid
            dpg.add_node_attribute(attribute_type=dpg.mvNode_Attr_Static)
            for key, label, default_val in node.settings_schema:
                with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as aid:
                    with dpg.group(horizontal=True): 
                        dpg.add_text(label)
                        node.setting_fields[key] = dpg.add_input_float(width=60, default_value=default_val, step=0)
                    node.inputs[aid] = PortType.DATA; node.setting_pins[key] = aid
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as fout: 
                dpg.add_text("Flow Out"); node.outputs[fout] = PortType.FLOW

    @staticmethod
    def _render_mt4_keyboard(node):
        with dpg.node(tag=node.node_id, parent="node_editor", label="MT4 Keyboard"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as flow: dpg.add_text("Flow In"); node.inputs[flow] = PortType.FLOW
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                node.combo_keys = dpg.add_combo(["WASD", "Arrow Keys"], default_value="WASD", width=120)
                dpg.add_text("XY Move / QE: Z / UJ: Grip", color=(255,150,150))
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as x: dpg.add_text("Target X"); node.outputs[x] = PortType.DATA; node.out_x = x
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as y: dpg.add_text("Target Y"); node.outputs[y] = PortType.DATA; node.out_y = y
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as z: dpg.add_text("Target Z"); node.outputs[z] = PortType.DATA; node.out_z = z
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as g: dpg.add_text("Target Grip"); node.outputs[g] = PortType.DATA; node.out_g = g
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as f: dpg.add_text("Flow Out"); node.outputs[f] = PortType.FLOW

    @staticmethod
    def _render_mt4_action(node):
        with dpg.node(tag=node.node_id, parent="node_editor", label="MT4 Action"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as flow: dpg.add_text("Flow In"); node.inputs[flow] = PortType.FLOW
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                node.combo_id = dpg.add_combo(["Move Relative (XYZ)", "Move Absolute (XYZ)", "Set Gripper (Abs)", "Grip Relative (Add)", "Homing"], default_value="Move Relative (XYZ)", width=150)
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as v1: dpg.add_text("X / Grip"); node.field_v1 = dpg.add_input_float(width=60, default_value=0); node.inputs[v1] = PortType.DATA; node.in_val1 = v1
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as v2: dpg.add_text("Y"); node.field_v2 = dpg.add_input_float(width=60, default_value=0); node.inputs[v2] = PortType.DATA; node.in_val2 = v2
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as v3: dpg.add_text("Z"); node.field_v3 = dpg.add_input_float(width=60, default_value=0); node.inputs[v3] = PortType.DATA; node.in_val3 = v3
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out: dpg.add_text("Flow Out"); node.outputs[out] = PortType.FLOW; node.out_flow = out

    @staticmethod
    def _render_mt4_unity(node):
        with dpg.node(tag=node.node_id, parent="node_editor", label="Unity Logic (MT4)"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as in_flow: dpg.add_text("Flow In"); node.inputs[in_flow] = PortType.FLOW
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as d_in: dpg.add_text("JSON"); node.inputs[d_in] = PortType.DATA; node.data_in_id = d_in
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out_x: dpg.add_text("Target X"); node.outputs[out_x] = PortType.DATA; node.out_x = out_x
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out_y: dpg.add_text("Target Y"); node.outputs[out_y] = PortType.DATA; node.out_y = out_y
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out_z: dpg.add_text("Target Z"); node.outputs[out_z] = PortType.DATA; node.out_z = out_z
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out_g: dpg.add_text("Target Grip"); node.outputs[out_g] = PortType.DATA; node.out_g = out_g
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as f_out: dpg.add_text("Flow Out"); node.outputs[f_out] = PortType.FLOW

    @staticmethod
    def _render_udp(node):
        with dpg.node(tag=node.node_id, parent="node_editor", label="UDP Receiver (MT4 JSON)"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as f: dpg.add_text("Flow In"); node.inputs[f]=PortType.FLOW
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static): dpg.add_input_int(label="Port", width=80, default_value=6000, tag=f"p_{node.node_id}"); node.port=f"p_{node.node_id}"
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static): dpg.add_input_text(label="IP", width=100, default_value="192.168.50.63", tag=f"i_{node.node_id}"); node.ip=f"i_{node.node_id}"
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as d: dpg.add_text("JSON Out"); node.outputs[d]=PortType.DATA; node.out_json=d
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as o: dpg.add_text("Flow Out"); node.outputs[o]=PortType.FLOW; node.out_flow=o

    @staticmethod
    def _render_logic_if(node):
        with dpg.node(tag=node.node_id, parent="node_editor", label="IF Condition"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as flow: dpg.add_text("Flow In"); node.inputs[flow] = PortType.FLOW
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as cond: dpg.add_text("Condition", color=(255,100,100)); node.inputs[cond] = PortType.DATA; node.in_cond = cond
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as t: dpg.add_text("True", color=(100,255,100)); node.outputs[t] = PortType.FLOW; node.out_true = t
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as f: dpg.add_text("False", color=(255,100,100)); node.outputs[f] = PortType.FLOW; node.out_false = f

    @staticmethod
    def _render_logic_loop(node):
        with dpg.node(tag=node.node_id, parent="node_editor", label="For Loop"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as flow: dpg.add_text("Flow In"); node.inputs[flow] = PortType.FLOW
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static): dpg.add_text("Count:"); node.field_count = dpg.add_input_int(width=80, default_value=3, min_value=1)
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as l: dpg.add_text("Loop Body", color=(100,200,255)); node.outputs[l] = PortType.FLOW; node.out_loop = l
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as f: dpg.add_text("Finished", color=(200,200,200)); node.outputs[f] = PortType.FLOW; node.out_finish = f

    @staticmethod
    def _render_cond_key(node):
        with dpg.node(tag=node.node_id, parent="node_editor", label="Key Check"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static): dpg.add_text("Key (A-Z):"); node.field_key = dpg.add_input_text(width=60, default_value="A")
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as res: dpg.add_text("Is Pressed?"); node.outputs[res] = PortType.DATA; node.out_res = res

    @staticmethod
    def _render_constant(node):
        with dpg.node(tag=node.node_id, parent="node_editor", label="Constant"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static): node.field_val = dpg.add_input_float(width=80, default_value=1.0)
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out: dpg.add_text("Data"); node.outputs[out] = PortType.DATA; node.out_val = out

    @staticmethod
    def _render_print(node):
        with dpg.node(tag=node.node_id, parent="node_editor", label="Print Log"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as flow: dpg.add_text("Flow In"); node.inputs[flow] = PortType.FLOW
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as data: dpg.add_text("Data"); node.inputs[data] = PortType.DATA; node.inp_data = data
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out: dpg.add_text("Flow Out"); node.outputs[out] = PortType.FLOW; node.out_flow = out

    @staticmethod
    def _render_logger(node):
        with dpg.node(tag=node.node_id, parent="node_editor", label="System Log (Flowless)"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                with dpg.child_window(width=200, height=100): node.txt=dpg.add_text("", wrap=190)

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
        if not custom_name and dpg.does_item_exist("path_name_input"): custom_name = dpg.get_value("path_name_input")
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

# ================= [Execution Engine (Hybrid)] =================
def execute_graph_once():
    start_node = None
    for node in node_registry.values():
        if isinstance(node, StartNode): start_node = node; break
    
    for node in node_registry.values():
        if not isinstance(node, (StartNode, MT4CommandActionNode, MT4KeyboardNode, LogicIfNode, LogicLoopNode, ConditionKeyNode)):
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
        
        if node: 
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

# ================= [Main Setup & GUI Initialization] =================
init_mt4_serial()
threading.Thread(target=auto_reconnect_mt4_thread, daemon=True).start()
threading.Thread(target=network_monitor_thread, daemon=True).start()
threading.Thread(target=lambda: (time.sleep(1), update_file_list()), daemon=True).start()
threading.Thread(target=mt4_background_logger_thread, daemon=True).start() 

dpg.create_context()
with dpg.handler_registry(): dpg.add_key_press_handler(dpg.mvKey_Delete, callback=delete_selection)

with dpg.window(tag="PrimaryWindow"):
    with dpg.tab_bar():
        with dpg.tab(label="MT4 Dashboard"):
            with dpg.group(horizontal=True):
                with dpg.child_window(width=250, height=130, border=True):
                    dpg.add_text("MT4 Status", color=(150,150,150)); 
                    dpg.add_text("Status: Idle", tag="mt4_dash_status", color=(0,255,0))
                    dpg.add_text(f"HW: Offline", tag="mt4_dash_link", color=(255,0,0))
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
                with dpg.child_window(width=200, height=130, border=True):
                    dpg.add_text("Record & Play", color=(255,100,200))
                    dpg.add_input_text(tag="path_name_input", default_value="my_path", width=130)
                    dpg.add_button(label="Start Recording", tag="btn_mt4_record", width=130, callback=lambda s,a,u: toggle_mt4_record())
                    dpg.add_combo(items=get_mt4_paths(), tag="combo_mt4_path", width=130)
                    dpg.add_button(label="Play Selected", width=130, callback=play_mt4_path)

        with dpg.tab(label="Files & System"):
            with dpg.group(horizontal=True):
                with dpg.child_window(width=650, height=130, border=True):
                    dpg.add_text("File Manager", color=(0,255,255))
                    with dpg.group(horizontal=True):
                        dpg.add_text("Save:"); dpg.add_input_text(tag="file_name_input", default_value="my_graph", width=120); dpg.add_button(label="SAVE", callback=save_cb, width=60)
                        dpg.add_spacer(width=20)
                        dpg.add_text("Load:"); dpg.add_combo(items=get_save_files(), tag="file_list_combo", width=120); dpg.add_button(label="LOAD", callback=load_cb, width=60); dpg.add_button(label="Refresh", callback=update_file_list, width=60)
                with dpg.child_window(width=400, height=130, border=True):
                    dpg.add_text("Network Info", color=(100,200,255))
                    dpg.add_text("Loading...", tag="sys_tab_net", color=(180,180,180))

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

    with dpg.node_editor(tag="node_editor", callback=link_cb, delink_callback=del_link_cb): pass

dpg.create_viewport(title='MT4 Educational V24 - Fully Restored & Refactored', width=1280, height=800, vsync=True)
dpg.setup_dearpygui(); dpg.set_primary_window("PrimaryWindow", True); dpg.show_viewport()

last_logic_time = 0; LOGIC_RATE = 0.02

while dpg.is_dearpygui_running():
    if mt4_dashboard["last_pkt_time"] > 0: dpg.set_value("mt4_dash_status", f"Status: {mt4_dashboard['status']}")
    
    dpg.set_value("mt4_x", f"X: {mt4_current_pos['x']:.1f}"); dpg.set_value("mt4_y", f"Y: {mt4_current_pos['y']:.1f}")
    dpg.set_value("mt4_z", f"Z: {mt4_current_pos['z']:.1f}"); dpg.set_value("mt4_g", f"G: {mt4_current_pos['gripper']:.1f}")

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