import dearpygui.dearpygui as dpg
import time
import socket
import json
import serial 
import threading
import subprocess 
import os
from abc import ABC, abstractmethod
from collections import deque
from datetime import datetime

# ================= [Global Settings] =================
node_registry = {}
link_registry = {}
ser = None 
is_running = False 

SAVE_DIR = "Node_File"  # 저장 경로
if not os.path.exists(SAVE_DIR):
    os.makedirs(SAVE_DIR)

# Robot State
current_pos = {'x': 200.0, 'y': 0.0, 'z': 120.0, 'gripper': 40.0}
target_goal = {'x': 200.0, 'y': 0.0, 'z': 120.0, 'gripper': 40.0} 

manual_override_until = 0.0 

dashboard_state = {
    "status": "Idle",
    "hw_link": "Offline",
    "latency": 0.0,
    "last_pkt_time": 0.0
}

system_log_buffer = deque(maxlen=50)

# Config
UNITY_IP = "192.168.50.63" 
FEEDBACK_PORT = 5005
LIMITS = {'min_x': 100, 'max_x': 280, 'min_y': -150, 'max_y': 150, 'min_z': 0, 'max_z': 180}
GRIPPER_MIN = 30.0; GRIPPER_MAX = 60.0

# ================= [Helper Functions] =================
def write_log(msg):
    timestamp = datetime.now().strftime("%H:%M:%S")
    formatted = f"[{timestamp}] {msg}"
    print(formatted)
    system_log_buffer.append(formatted)

def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try: s.connect(('8.8.8.8', 80)); ip = s.getsockname()[0]
    except: ip = "127.0.0.1"
    finally: s.close()
    return ip

def get_wifi_ssid():
    try: return subprocess.check_output(['iwgetid', '-r']).decode('utf-8').strip() or "Unknown"
    except: return "Unknown"

# ================= [0. Robot Init] =================
def init_serial():
    global ser
    try:
        ser = serial.Serial('/dev/ttyUSB0', 115200, timeout=0.05)
        dashboard_state["hw_link"] = "Online"
        write_log("System: MT4 Robot Connected")
        time.sleep(2); ser.write(b"$H\r\n"); time.sleep(15) 
        ser.write(b"M20\r\n"); ser.write(b"G90\r\n"); ser.write(b"G1 F2000\r\n"); time.sleep(1)
        current_pos.update({'x':200.0, 'y':0.0, 'z':120.0, 'gripper':40.0})
        target_goal.update({'x':200.0, 'y':0.0, 'z':120.0, 'gripper':40.0})
        cmd = f"G0 X200 Y0 Z120 F2000\r\n"; ser.write(cmd.encode()); ser.write(b"M3 S40\r\n") 
        write_log("System: Startup Sequence Complete")
    except Exception as e:
        dashboard_state["hw_link"] = "Simulation"
        write_log(f"System: Connection Failed ({e}). Simulation Mode.")
        ser = None

# ================= [1. Dashboard Callbacks] =================
# ... (기존 콜백 함수들은 동일하므로 생략 없이 포함) ...
def manual_control_callback(sender, app_data, user_data):
    global manual_override_until
    manual_override_until = time.time() + 1.5
    target_goal['x'] = current_pos['x']; target_goal['y'] = current_pos['y']
    target_goal['z'] = current_pos['z']; target_goal['gripper'] = current_pos['gripper']
    axis, step = user_data
    target_goal[axis] = current_pos[axis] + step
    if axis == 'x': target_goal[axis] = max(LIMITS['min_x'], min(target_goal[axis], LIMITS['max_x']))
    elif axis == 'y': target_goal[axis] = max(LIMITS['min_y'], min(target_goal[axis], LIMITS['max_y']))
    elif axis == 'z': target_goal[axis] = max(LIMITS['min_z'], min(target_goal[axis], LIMITS['max_z']))
    elif axis == 'gripper': target_goal[axis] = max(GRIPPER_MIN, min(target_goal[axis], GRIPPER_MAX))
    write_log(f"Manual: {axis.upper()} Moved to {target_goal[axis]:.1f}")
    current_pos.update(target_goal) 
    send_robot_command_direct(target_goal['x'], target_goal['y'], target_goal['z'], target_goal['gripper'])

def send_robot_command_direct(x, y, z, g):
    global ser
    if ser and ser.is_open:
        cmd_move = f"G0 X{x:.1f} Y{y:.1f} Z{z:.1f}\n"; cmd_grip = f"M3 S{int(g)}\n"
        ser.write(cmd_move.encode()); ser.write(cmd_grip.encode())

def move_to_coord_callback(sender, app_data, user_data):
    global manual_override_until
    manual_override_until = time.time() + 2.0
    target_goal['x'] = float(dpg.get_value("input_x"))
    target_goal['y'] = float(dpg.get_value("input_y"))
    target_goal['z'] = float(dpg.get_value("input_z"))
    target_goal['gripper'] = float(dpg.get_value("input_g"))
    current_pos.update(target_goal)
    send_robot_command_direct(target_goal['x'], target_goal['y'], target_goal['z'], target_goal['gripper'])
    write_log(f"Direct Move: {target_goal['x']}, {target_goal['y']}, {target_goal['z']}")

def homing_thread_func():
    global ser, manual_override_until
    if ser:
        manual_override_until = time.time() + 20.0
        dashboard_state["status"] = "HOMING..."
        write_log("System: Homing Started...")
        ser.write(b"$H\r\n"); time.sleep(15)
        ser.write(b"M20\r\n"); ser.write(b"G90\r\n"); ser.write(b"G1 F2000\r\n")
        target_goal['x'] = 200.0; target_goal['y'] = 0.0; target_goal['z'] = 120.0; target_goal['gripper'] = 40.0
        current_pos.update(target_goal)
        if dpg.does_item_exist("input_x"): dpg.set_value("input_x", 200); dpg.set_value("input_y", 0); dpg.set_value("input_z", 120)
        cmd = f"G0 X200 Y0 Z120 F2000\r\n"; ser.write(cmd.encode()); ser.write(b"M3 S40\r\n")
        dashboard_state["status"] = "Idle"; write_log("System: Homing Complete")

def homing_callback(sender, app_data, user_data):
    threading.Thread(target=homing_thread_func, daemon=True).start()

# ================= [2. Base Class & Serialization] =================
class BaseNode(ABC):
    def __init__(self, node_id, label, type_str):
        self.node_id = node_id; self.label = label
        self.type_str = type_str # ★ 저장용 타입 태그
        self.inputs = {}; self.outputs = {}; self.output_data = {} 

    @abstractmethod
    def build_ui(self): pass
    @abstractmethod
    def execute(self): pass

    def fetch_input_data(self, input_attr_id):
        target_link = None
        for link in link_registry.values():
            if link['target'] == input_attr_id: target_link = link; break
        if not target_link: return None 
        source_attr_id = target_link['source']
        source_node_id = dpg.get_item_parent(source_attr_id)
        if source_node_id in node_registry:
            return node_registry[source_node_id].output_data.get(source_attr_id)
        return None

    # ★ 저장/불러오기용 가상 메서드 (오버라이드 가능)
    def get_settings(self): return {}
    def load_settings(self, data): pass

# ================= [3. Nodes Implementation] =================
class StartNode(BaseNode):
    def __init__(self, node_id): super().__init__(node_id, "START", "START")
    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label="START"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out_id: dpg.add_text("Flow Out"); self.outputs[out_id] = "Flow"
    def execute(self): return self.outputs

class ConstantNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "Constant", "CONSTANT")
        self.out_val = None; self.field_val = None
    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label=self.label):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                self.field_val = dpg.add_input_float(width=80, default_value=1.0, step=0.1) 
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out:
                dpg.add_text("Output"); self.outputs[out] = "Data"; self.out_val = out
    def execute(self):
        val = dpg.get_value(self.field_val)
        self.output_data[self.out_val] = val
        return self.outputs
    # ★ 값 저장 복구
    def get_settings(self): return {"value": dpg.get_value(self.field_val)}
    def load_settings(self, data): dpg.set_value(self.field_val, data.get("value", 1.0))

class UDPReceiverNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "UDP Receiver", "UDP_RECV")
        self.port_input = None; self.target_ip_input = None
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); self.sock.setblocking(False) 
        self.sock_feedback = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.is_bound = False; self.data_out_id = None; self.last_data_str = ""
    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label=self.label):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as in_id: dpg.add_text("Flow In"); self.inputs[in_id] = "Flow"
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static): 
                dpg.add_input_int(label="Port", width=80, default_value=6000, tag=f"port_{self.node_id}")
                self.port_input = f"port_{self.node_id}"
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static): 
                dpg.add_input_text(label="IP", width=100, default_value="192.168.50.63", tag=f"ip_{self.node_id}")
                self.target_ip_input = f"ip_{self.node_id}"
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as d_out: dpg.add_text("JSON Out"); self.outputs[d_out] = "Data"; self.data_out_id = d_out 
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as f_out: dpg.add_text("Flow Out"); self.outputs[f_out] = "Flow"
    def execute(self):
        global UNITY_IP
        port = dpg.get_value(self.port_input); UNITY_IP = dpg.get_value(self.target_ip_input)
        if not self.is_bound:
            try: self.sock.bind(('0.0.0.0', port)); self.is_bound = True; write_log(f"UDP: Bound to {port}")
            except: self.is_bound = True
        try:
            while True: data, _ = self.sock.recvfrom(4096); decoded = data.decode()
            if decoded != self.last_data_str:
                self.output_data[self.data_out_id] = decoded; self.last_data_str = decoded
                dashboard_state["last_pkt_time"] = time.time(); dashboard_state["status"] = "Connected"
        except: pass
        try:
            fb = {"x": -current_pos['y']/1000.0, "y": current_pos['z']/1000.0, "z": current_pos['x']/1000.0, "gripper": current_pos['gripper'], "status": "Running"}
            self.sock_feedback.sendto(json.dumps(fb).encode(), (UNITY_IP, FEEDBACK_PORT))
        except: pass
        return self.outputs
    # ★ IP/Port 저장 복구
    def get_settings(self): return {"port": dpg.get_value(self.port_input), "ip": dpg.get_value(self.target_ip_input)}
    def load_settings(self, data):
        dpg.set_value(self.port_input, data.get("port", 6000))
        dpg.set_value(self.target_ip_input, data.get("ip", "192.168.50.63"))

class UnityControlNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "Unity Logic", "UNITY_CONTROL")
        self.data_in_id = None; self.out_x = None; self.out_y = None; self.out_z = None; self.out_g = None
    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label=self.label):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as in_flow: dpg.add_text("Flow In"); self.inputs[in_flow] = "Flow"
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as d_in: dpg.add_text("JSON"); self.inputs[d_in] = "Data"; self.data_in_id = d_in
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out_x: dpg.add_text("Target X"); self.outputs[out_x] = "Data"; self.out_x = out_x
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out_y: dpg.add_text("Target Y"); self.outputs[out_y] = "Data"; self.out_y = out_y
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out_z: dpg.add_text("Target Z"); self.outputs[out_z] = "Data"; self.out_z = out_z
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out_g: dpg.add_text("Target Grip"); self.outputs[out_g] = "Data"; self.out_g = out_g
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as f_out: dpg.add_text("Flow Out"); self.outputs[f_out] = "Flow"
    def execute(self):
        raw_json = self.fetch_input_data(self.data_in_id)
        if raw_json:
            try:
                parsed = json.loads(raw_json)
                if parsed.get("type", "MOVE") == "MOVE":
                    self.output_data[self.out_x] = parsed.get('z', 0) * 1000.0
                    self.output_data[self.out_y] = -parsed.get('x', 0) * 1000.0
                    self.output_data[self.out_z] = parsed.get('y', 0) * 1000.0
                    self.output_data[self.out_g] = parsed.get('gripper') 
            except: pass 
        return self.outputs

class KeyboardControlNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "Keyboard (Pi)", "KEYBOARD")
        self.out_x = None; self.out_y = None; self.out_z = None; self.out_g = None
        self.step_size = 10.0; self.grip_step = 5.0; self.cooldown = 0.2; self.last_input_time = 0.0
    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label="Keyboard Input (Step)"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as in_flow: dpg.add_text("Flow In"); self.inputs[in_flow] = "Flow"
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static): dpg.add_text("WASD: XY\nQE: Z\nUJ: Grip", color=(255, 150, 150))
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out_x: dpg.add_text("Target X"); self.outputs[out_x] = "Data"; self.out_x = out_x
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out_y: dpg.add_text("Target Y"); self.outputs[out_y] = "Data"; self.out_y = out_y
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out_z: dpg.add_text("Target Z"); self.outputs[out_z] = "Data"; self.out_z = out_z
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out_g: dpg.add_text("Target Grip"); self.outputs[out_g] = "Data"; self.out_g = out_g
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as f_out: dpg.add_text("Flow Out"); self.outputs[f_out] = "Flow"
    def execute(self):
        global manual_override_until
        if time.time() - self.last_input_time > self.cooldown:
            dx, dy, dz, dg = 0, 0, 0, 0
            if dpg.is_key_down(dpg.mvKey_W): dx = 1
            if dpg.is_key_down(dpg.mvKey_S): dx = -1
            if dpg.is_key_down(dpg.mvKey_A): dy = 1
            if dpg.is_key_down(dpg.mvKey_D): dy = -1
            if dpg.is_key_down(dpg.mvKey_Q): dz = 1
            if dpg.is_key_down(dpg.mvKey_E): dz = -1
            if dpg.is_key_down(dpg.mvKey_J): dg = 1  
            if dpg.is_key_down(dpg.mvKey_U): dg = -1 
            if dx or dy or dz or dg:
                manual_override_until = time.time() + 0.5
                self.last_input_time = time.time()
                target_goal['x'] += dx * self.step_size; target_goal['y'] += dy * self.step_size; target_goal['z'] += dz * self.step_size
                target_goal['gripper'] += dg * self.grip_step
                target_goal['x'] = max(LIMITS['min_x'], min(target_goal['x'], LIMITS['max_x']))
                target_goal['y'] = max(LIMITS['min_y'], min(target_goal['y'], LIMITS['max_y']))
                target_goal['z'] = max(LIMITS['min_z'], min(target_goal['z'], LIMITS['max_z']))
                target_goal['gripper'] = max(GRIPPER_MIN, min(target_goal['gripper'], GRIPPER_MAX))
        self.output_data[self.out_x] = target_goal['x']; self.output_data[self.out_y] = target_goal['y']
        self.output_data[self.out_z] = target_goal['z']; self.output_data[self.out_g] = target_goal['gripper']
        return self.outputs

class RobotControlNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "Robot Driver", "ROBOT_CONTROL")
        self.in_x = None; self.in_y = None; self.in_z = None; self.in_g = None
        self.in_smooth = None; self.in_g_speed = None
        self.field_x = None; self.field_y = None; self.field_z = None; self.field_g = None
        self.field_smooth = None; self.field_g_speed = None 
        self.last_cmd = ""; self.cache_ui = {'x':0, 'y':0, 'z':0, 'g':0}
        self.last_write_time = 0; self.write_interval = 0.033 

    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label=self.label):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as in_flow: dpg.add_text("Flow In"); self.inputs[in_flow] = "Flow"
            for axis, label, default_val, field_attr in [('x',"X",200.0,'field_x'), ('y',"Y",0.0,'field_y'), ('z',"Z",120.0,'field_z'), ('g',"G",40.0,'field_g')]:
                with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as attr_id:
                    with dpg.group(horizontal=True):
                        dpg.add_text(label, color=(255, 255, 0)); setattr(self, field_attr, dpg.add_input_float(width=80, default_value=default_val, step=0))
                    self.inputs[attr_id] = "Data"
                    if axis == 'x': self.in_x = attr_id
                    elif axis == 'y': self.in_y = attr_id
                    elif axis == 'z': self.in_z = attr_id
                    elif axis == 'g': self.in_g = attr_id
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static): dpg.add_spacer(height=5) 
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as s_in:
                with dpg.group(horizontal=True): dpg.add_text("Smth"); self.field_smooth = dpg.add_input_float(width=60, default_value=0.2, step=0)
                self.inputs[s_in] = "Data"; self.in_smooth = s_in
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as gs_in:
                with dpg.group(horizontal=True): dpg.add_text("Spd "); self.field_g_speed = dpg.add_input_float(width=60, default_value=2.0, step=0)
                self.inputs[gs_in] = "Data"; self.in_g_speed = gs_in
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as f_out: dpg.add_text("Flow Out"); self.outputs[f_out] = "Flow"

    def execute(self):
        global current_pos, target_goal, manual_override_until
        tx, ty, tz, tg = self.fetch_input_data(self.in_x), self.fetch_input_data(self.in_y), self.fetch_input_data(self.in_z), self.fetch_input_data(self.in_g)
        link_smooth = self.fetch_input_data(self.in_smooth)
        if link_smooth is not None: dpg.set_value(self.field_smooth, float(link_smooth))
        smooth_factor = 1.0 if time.time() < manual_override_until else max(0.01, min(dpg.get_value(self.field_smooth), 1.0))

        if time.time() > manual_override_until:
            if tx is not None: target_goal['x'] = float(tx)
            if ty is not None: target_goal['y'] = float(ty)
            if tz is not None: target_goal['z'] = float(tz)
            if tg is not None: target_goal['gripper'] = float(tg)
        
        dx, dy, dz = target_goal['x'] - current_pos['x'], target_goal['y'] - current_pos['y'], target_goal['z'] - current_pos['z']
        if abs(dx)<0.5 and abs(dy)<0.5 and abs(dz)<0.5: next_x, next_y, next_z = target_goal['x'], target_goal['y'], target_goal['z']
        else: next_x = current_pos['x'] + dx * smooth_factor; next_y = current_pos['y'] + dy * smooth_factor; next_z = current_pos['z'] + dz * smooth_factor
        
        next_x = max(LIMITS['min_x'], min(next_x, LIMITS['max_x'])); next_y = max(LIMITS['min_y'], min(next_y, LIMITS['max_y']))
        next_z = max(LIMITS['min_z'], min(next_z, LIMITS['max_z'])); next_g = max(GRIPPER_MIN, min(target_goal['gripper'] if target_goal['gripper'] else current_pos['gripper'], GRIPPER_MAX))
        current_pos.update({'x': next_x, 'y': next_y, 'z': next_z, 'gripper': next_g})

        if abs(self.cache_ui['x'] - next_x) > 0.1: dpg.set_value(self.field_x, next_x); self.cache_ui['x'] = next_x
        if abs(self.cache_ui['y'] - next_y) > 0.1: dpg.set_value(self.field_y, next_y); self.cache_ui['y'] = next_y
        if abs(self.cache_ui['z'] - next_z) > 0.1: dpg.set_value(self.field_z, next_z); self.cache_ui['z'] = next_z
        if abs(self.cache_ui['g'] - next_g) > 0.1: dpg.set_value(self.field_g, next_g); self.cache_ui['g'] = next_g

        if time.time() - self.last_write_time > self.write_interval:
            cmd_move = f"G0 X{next_x:.1f} Y{next_y:.1f} Z{next_z:.1f}\n"; cmd_grip = f"M3 S{int(next_g)}\n"
            full_cmd = cmd_move + cmd_grip
            if full_cmd != self.last_cmd:
                global ser
                try: 
                    if ser and ser.is_open: ser.write(cmd_move.encode()); ser.write(cmd_grip.encode()); self.last_write_time = time.time()
                except Exception as e: 
                    write_log(f"Err: {e}"); dashboard_state["hw_link"] = "Offline"; ser = None
                self.last_cmd = full_cmd
        return self.outputs

    def get_settings(self): return {"smooth": dpg.get_value(self.field_smooth), "speed": dpg.get_value(self.field_g_speed)}
    def load_settings(self, data):
        dpg.set_value(self.field_smooth, data.get("smooth", 0.2))
        dpg.set_value(self.field_g_speed, data.get("speed", 2.0))
    
class JsonParseNode(BaseNode):
    def __init__(self, node_id): super().__init__(node_id, "Simple Parser", "JSON_PARSE")
    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label=self.label):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as in_flow: dpg.add_text("Flow In"); self.inputs[in_flow] = "Flow"
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as d_in: dpg.add_text("JSON In"); self.inputs[d_in] = "Data"
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out_x: dpg.add_text("Data Out"); self.outputs[out_x] = "Data"
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as f_out: dpg.add_text("Flow Out"); self.outputs[f_out] = "Flow"
    def execute(self): return self.outputs

class PrintNode(BaseNode):
    def __init__(self, node_id): super().__init__(node_id, "Status Monitor", "PRINT")
    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label=self.label):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as in_id: dpg.add_text("Flow In"); self.inputs[in_id] = "Flow"
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as d_in: dpg.add_text("Data In"); self.inputs[d_in] = "Data"
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static): self.input_field = dpg.add_input_text(label="Msg", width=120)
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out_id: dpg.add_text("Flow Out"); self.outputs[out_id] = "Flow"
    def execute(self): return self.outputs

class GraphNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "Live Trajectory", "GRAPH")
        self.in_x = None; self.in_y = None; self.in_z = None
        self.buf_x = deque(maxlen=200); self.t_x = deque(maxlen=200)
        self.buf_y = deque(maxlen=200); self.t_y = deque(maxlen=200)
        self.buf_z = deque(maxlen=200); self.t_z = deque(maxlen=200)
        self.counter = 0
    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label="Live Graph"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as flow: dpg.add_text("Flow In"); self.inputs[flow] = "Flow"
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as ix: dpg.add_text("Input X", color=(100,200,255)); self.inputs[ix] = "Data"; self.in_x = ix
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as iy: dpg.add_text("Input Y", color=(255,200,100)); self.inputs[iy] = "Data"; self.in_y = iy
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as iz: dpg.add_text("Input Z", color=(100,255,100)); self.inputs[iz] = "Data"; self.in_z = iz
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                with dpg.plot(label="Trajectory", height=150, width=250):
                    dpg.add_plot_legend(); dpg.add_plot_axis(dpg.mvXAxis, label="T", tag=f"xaxis_{self.node_id}")
                    with dpg.plot_axis(dpg.mvYAxis, label="Pos", tag=f"yaxis_{self.node_id}"):
                        dpg.add_line_series([], [], label="X", tag=f"series_x_{self.node_id}")
                        dpg.add_line_series([], [], label="Y", tag=f"series_y_{self.node_id}")
                        dpg.add_line_series([], [], label="Z", tag=f"series_z_{self.node_id}")
    def execute(self):
        self.counter += 1
        vx, vy, vz = self.fetch_input_data(self.in_x), self.fetch_input_data(self.in_y), self.fetch_input_data(self.in_z)
        if vx: self.buf_x.append(float(vx)); self.t_x.append(self.counter); dpg.set_value(f"series_x_{self.node_id}", [list(self.t_x), list(self.buf_x)])
        else: self.buf_x.clear(); self.t_x.clear(); dpg.set_value(f"series_x_{self.node_id}", [[], []])
        if vy: self.buf_y.append(float(vy)); self.t_y.append(self.counter); dpg.set_value(f"series_y_{self.node_id}", [list(self.t_y), list(self.buf_y)])
        else: self.buf_y.clear(); self.t_y.clear(); dpg.set_value(f"series_y_{self.node_id}", [[], []])
        if vz: self.buf_z.append(float(vz)); self.t_z.append(self.counter); dpg.set_value(f"series_z_{self.node_id}", [list(self.t_z), list(self.buf_z)])
        else: self.buf_z.clear(); self.t_z.clear(); dpg.set_value(f"series_z_{self.node_id}", [[], []])
        
        all_vals = list(self.buf_x) + list(self.buf_y) + list(self.buf_z)
        if all_vals:
            mn, mx = min(all_vals), max(all_vals); pad = (mx-mn)*0.1 if mx!=mn else 10
            dpg.set_axis_limits(f"yaxis_{self.node_id}", mn-pad, mx+pad)
            dpg.set_axis_limits(f"xaxis_{self.node_id}", self.counter-200, self.counter)
        return self.outputs

class LoggerNode(BaseNode):
    def __init__(self, node_id): super().__init__(node_id, "System Log", "LOGGER"); self.text_id = None; self.last_len = 0
    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label="System Log"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as flow: dpg.add_text("Update Signal"); self.inputs[flow] = "Flow"
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                with dpg.child_window(width=250, height=150): self.text_id = dpg.add_text("", color=(200,200,200), wrap=240)
    def execute(self):
        if len(system_log_buffer) != self.last_len:
            dpg.set_value(self.text_id, "\n".join(list(system_log_buffer)[-10:])); self.last_len = len(system_log_buffer)
        return self.outputs

# ================= [Factory & Serialization Logic] =================
class NodeFactory:
    @staticmethod
    def create_node(node_type, node_id=None):
        if node_id is None: node_id = dpg.generate_uuid()
        node = None
        if node_type == "START": node = StartNode(node_id)
        elif node_type == "PRINT": node = PrintNode(node_id)
        elif node_type == "UDP_RECV": node = UDPReceiverNode(node_id)
        elif node_type == "UNITY_CONTROL": node = UnityControlNode(node_id)
        elif node_type == "ROBOT_CONTROL": node = RobotControlNode(node_id)
        elif node_type == "JSON_PARSE": node = JsonParseNode(node_id)
        elif node_type == "CONSTANT": node = ConstantNode(node_id)
        elif node_type == "GRAPH": node = GraphNode(node_id) 
        elif node_type == "LOGGER": node = LoggerNode(node_id)
        elif node_type == "KEYBOARD": node = KeyboardControlNode(node_id)
        
        if node: node.build_ui(); node_registry[node_id] = node; return node
        return None

# ★ 저장 함수
def save_graph(filename):
    if not filename.endswith(".json"): filename += ".json"
    filepath = os.path.join(SAVE_DIR, filename)
    
    data = {"nodes": [], "links": []}
    
    # 1. 노드 저장
    for node_id, node in node_registry.items():
        node_info = {
            "type": node.type_str,
            "id": node_id,
            "pos": dpg.get_item_pos(node_id),
            "settings": node.get_settings()
        }
        data["nodes"].append(node_info)
        
    # 2. 링크 저장 (Source Node Index -> Target Node Index 방식)
    # DPG ID는 매번 바뀌므로, 링크는 "어떤 노드의 몇 번째 속성인가"로 저장
    for link_id, link in link_registry.items():
        src_attr, dst_attr = link['source'], link['target']
        src_node_id = dpg.get_item_parent(src_attr)
        dst_node_id = dpg.get_item_parent(dst_attr)
        
        if src_node_id in node_registry and dst_node_id in node_registry:
            # 해당 노드의 몇 번째 output 속성인지 찾기
            src_node = node_registry[src_node_id]
            dst_node = node_registry[dst_node_id]
            
            src_idx = list(src_node.outputs.keys()).index(src_attr)
            dst_idx = list(dst_node.inputs.keys()).index(dst_attr)
            
            data["links"].append({
                "src_node": src_node_id, "src_idx": src_idx,
                "dst_node": dst_node_id, "dst_idx": dst_idx
            })
            
    try:
        with open(filepath, 'w') as f: json.dump(data, f, indent=4)
        write_log(f"Graph Saved: {filename}")
    except Exception as e:
        write_log(f"Save Error: {e}")

# ★ 불러오기 함수
def load_graph(filename):
    if not filename.endswith(".json"): filename += ".json"
    filepath = os.path.join(SAVE_DIR, filename)
    
    if not os.path.exists(filepath):
        write_log(f"File not found: {filename}")
        return

    # 1. 기존 삭제
    for link in list(link_registry.keys()): dpg.delete_item(link)
    for node in list(node_registry.keys()): dpg.delete_item(node)
    link_registry.clear(); node_registry.clear()
    
    try:
        with open(filepath, 'r') as f: data = json.load(f)
        
        # 2. 노드 생성
        for n_data in data["nodes"]:
            node = NodeFactory.create_node(n_data["type"], n_data["id"])
            if node:
                dpg.set_item_pos(node.node_id, n_data["pos"])
                node.load_settings(n_data.get("settings", {}))
        
        # 3. 링크 복원
        # 저장된 ID는 이제 node_registry의 키와 일치함
        for l_data in data["links"]:
            src_node = node_registry.get(l_data["src_node"])
            dst_node = node_registry.get(l_data["dst_node"])
            
            if src_node and dst_node:
                # 인덱스로 속성 ID 찾기
                src_attr_id = list(src_node.outputs.keys())[l_data["src_idx"]]
                dst_attr_id = list(dst_node.inputs.keys())[l_data["dst_idx"]]
                
                link_id = dpg.add_node_link(src_attr_id, dst_attr_id, parent="node_editor")
                link_registry[link_id] = {'source': src_attr_id, 'target': dst_attr_id}
                
        write_log(f"Graph Loaded: {filename}")
        
    except Exception as e:
        write_log(f"Load Error: {e}")

# ================= [Execution Logic] =================
def execute_graph_once():
    start_node = None
    for node in node_registry.values():
        if isinstance(node, StartNode): start_node = node; break
    if not start_node: return

    current_node = start_node
    while current_node:
        outputs = current_node.execute()
        next_node = None
        for out_attr_id, out_type in outputs.items():
            if out_type == "Flow":
                for link in link_registry.values():
                    if link['source'] == out_attr_id:
                        target_node_id = dpg.get_item_parent(link['target'])
                        if target_node_id in node_registry: next_node = node_registry[target_node_id]; break
            if next_node: break 
        current_node = next_node
    
    for node in node_registry.values():
        if isinstance(node, (GraphNode, LoggerNode)): node.execute()

# Callbacks
def toggle_execution(sender, app_data):
    global is_running; is_running = not is_running
    dpg.set_item_label("btn_run", "STOP" if is_running else "RUN")

def delete_selection(sender, app_data):
    for link_id in dpg.get_selected_links("node_editor"): dpg.delete_item(link_id); del link_registry[link_id]
    for node_id in dpg.get_selected_nodes("node_editor"): dpg.delete_item(node_id); del node_registry[node_id]

def link_cb(sender, app_data):
    src, dst = app_data[0], app_data[1] if len(app_data)==2 else (app_data[1], app_data[2])
    link_id = dpg.add_node_link(src, dst, parent=sender)
    link_registry[link_id] = {'source': src, 'target': dst}

def del_link_cb(sender, app_data): dpg.delete_item(app_data); del link_registry[app_data]
def add_node_cb(sender, app_data, user_data): NodeFactory.create_node(user_data)
def save_cb(sender, app_data): save_graph(dpg.get_value("file_name_input"))
def load_cb(sender, app_data): load_graph(dpg.get_value("file_name_input"))

def auto_reconnect_thread():
    global ser
    while True:
        if ser is None and os.path.exists('/dev/ttyUSB0'):
            try: init_serial() 
            except: pass
        time.sleep(3) 

# ================= [Main Setup] =================
init_serial()
threading.Thread(target=auto_reconnect_thread, daemon=True).start()

dpg.create_context()
with dpg.handler_registry(): dpg.add_key_press_handler(dpg.mvKey_Delete, callback=delete_selection)

with dpg.window(tag="PrimaryWindow"):
    with dpg.group(horizontal=True):
        with dpg.child_window(width=250, height=130, border=True):
            dpg.add_text("System Status", color=(150,150,150)); dpg.add_text("Idle", tag="dash_status", color=(0,255,0))
            dpg.add_spacer(height=5); dpg.add_text("Hardware Link", color=(150,150,150))
            dpg.add_text(dashboard_state["hw_link"], tag="dash_link", color=(0,255,0) if dashboard_state["hw_link"]=="Online" else (255,0,0))
            dpg.add_spacer(height=5); dpg.add_text("Latency", color=(150,150,150)); dpg.add_text("0.0 ms", tag="dash_latency", color=(255,255,0))

        with dpg.child_window(width=350, height=130, border=True):
            dpg.add_text("Manual Control", color=(255,200,0))
            with dpg.group(horizontal=True):
                dpg.add_button(label="X+", width=60, callback=manual_control_callback, user_data=('x', 10)); dpg.add_button(label="X-", width=60, callback=manual_control_callback, user_data=('x', -10))
                dpg.add_text("|"); dpg.add_button(label="Y+", width=60, callback=manual_control_callback, user_data=('y', 10)); dpg.add_button(label="Y-", width=60, callback=manual_control_callback, user_data=('y', -10))
            with dpg.group(horizontal=True):
                dpg.add_button(label="Z+", width=60, callback=manual_control_callback, user_data=('z', 10)); dpg.add_button(label="Z-", width=60, callback=manual_control_callback, user_data=('z', -10))
                dpg.add_text("|"); dpg.add_button(label="G+", width=60, callback=manual_control_callback, user_data=('gripper', 5)); dpg.add_button(label="G-", width=60, callback=manual_control_callback, user_data=('gripper', -5))

        # ★ [추가된 부분] 파일 저장/불러오기 패널
        with dpg.child_window(width=300, height=130, border=True):
            dpg.add_text("Graph File Manager", color=(0,255,255))
            dpg.add_text("File Name:")
            dpg.add_input_text(tag="file_name_input", default_value="my_graph", width=200)
            dpg.add_spacer(height=10)
            with dpg.group(horizontal=True):
                dpg.add_button(label="SAVE", callback=save_cb, width=130)
                dpg.add_button(label="LOAD", callback=load_cb, width=130)

    dpg.add_separator()
    with dpg.group(horizontal=True):
        dpg.add_button(label="START", callback=add_node_cb, user_data="START")
        dpg.add_button(label="UDP", callback=add_node_cb, user_data="UDP_RECV")
        dpg.add_button(label="UNITY", callback=add_node_cb, user_data="UNITY_CONTROL")
        dpg.add_button(label="KEY", callback=add_node_cb, user_data="KEYBOARD")
        dpg.add_button(label="ROBOT", callback=add_node_cb, user_data="ROBOT_CONTROL")
        dpg.add_spacer(width=20)
        dpg.add_button(label="CONST", callback=add_node_cb, user_data="CONSTANT")
        dpg.add_spacer(width=20)
        dpg.add_button(label="GRAPH", callback=add_node_cb, user_data="GRAPH", width=60)
        dpg.add_button(label="LOG", callback=add_node_cb, user_data="LOGGER", width=60)
        dpg.add_spacer(width=50)
        dpg.add_button(label="RUN", tag="btn_run", callback=toggle_execution, width=150)
    
    with dpg.node_editor(tag="node_editor", callback=link_cb, delink_callback=del_link_cb): pass

dpg.create_viewport(title='PyGui V19 (Save/Load)', width=1024, height=768, vsync=True)
dpg.setup_dearpygui()
dpg.set_primary_window("PrimaryWindow", True)
dpg.show_viewport()

last_logic_time = 0
LOGIC_RATE = 0.01

while dpg.is_dearpygui_running():
    if dashboard_state["last_pkt_time"] > 0:
        dpg.set_value("dash_status", dashboard_state["status"])
        dpg.set_value("dash_latency", f"{dashboard_state['latency']:.1f} ms")
    if is_running and (time.time() - last_logic_time > LOGIC_RATE):
        execute_graph_once()
        last_logic_time = time.time()
    dpg.render_dearpygui_frame()
dpg.destroy_context()