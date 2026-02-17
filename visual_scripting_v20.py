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

SAVE_DIR = "Node_File"
if not os.path.exists(SAVE_DIR): os.makedirs(SAVE_DIR)

# Robot State
current_pos = {'x': 200.0, 'y': 0.0, 'z': 120.0, 'gripper': 40.0}
target_goal = {'x': 200.0, 'y': 0.0, 'z': 120.0, 'gripper': 40.0} 
manual_override_until = 0.0 

# Dashboard State
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
    print(f"[{timestamp}] {msg}")
    system_log_buffer.append(f"[{timestamp}] {msg}")

def get_local_ip():
    try: s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.connect(('8.8.8.8',80)); ip=s.getsockname()[0]; s.close(); return ip
    except: return "127.0.0.1"

def get_wifi_ssid():
    try: return subprocess.check_output(['iwgetid','-r']).decode('utf-8').strip() or "Unknown"
    except: return "Unknown"

def get_save_files():
    if not os.path.exists(SAVE_DIR): return []
    return [f for f in os.listdir(SAVE_DIR) if f.endswith(".json")]

# ================= [Hardware Control] =================
def init_serial():
    global ser
    try:
        ser = serial.Serial('/dev/ttyUSB0', 115200, timeout=0.05)
        dashboard_state["hw_link"] = "Online"; write_log("System: MT4 Robot Connected")
        time.sleep(2); ser.write(b"$H\r\n"); time.sleep(15) 
        ser.write(b"M20\r\n"); ser.write(b"G90\r\n"); ser.write(b"G1 F2000\r\n"); time.sleep(1)
        update_robot_pos(200,0,120,40)
        ser.write(b"G0 X200 Y0 Z120 F2000\r\n"); ser.write(b"M3 S40\r\n") 
    except Exception as e:
        dashboard_state["hw_link"] = "Simulation"; write_log(f"Sim Mode ({e})"); ser = None

def send_robot_command_direct(x, y, z, g):
    global ser
    if ser and ser.is_open:
        ser.write(f"G0 X{x:.1f} Y{y:.1f} Z{z:.1f}\n".encode())
        ser.write(f"M3 S{int(g)}\n".encode())

def update_robot_pos(x,y,z,g):
    target_goal.update({'x':x, 'y':y, 'z':z, 'gripper':g})
    current_pos.update(target_goal)

# ================= [Dashboard Callbacks] =================
def manual_control_callback(sender, app_data, user_data):
    global manual_override_until; manual_override_until = time.time() + 1.5
    axis, step = user_data
    target_goal[axis] = current_pos[axis] + step
    apply_limits_and_move()

def move_to_coord_callback(sender, app_data, user_data):
    global manual_override_until; manual_override_until = time.time() + 2.0
    target_goal['x'] = float(dpg.get_value("input_x"))
    target_goal['y'] = float(dpg.get_value("input_y"))
    target_goal['z'] = float(dpg.get_value("input_z"))
    target_goal['gripper'] = float(dpg.get_value("input_g"))
    apply_limits_and_move()

def apply_limits_and_move():
    target_goal['x'] = max(LIMITS['min_x'], min(target_goal['x'], LIMITS['max_x']))
    target_goal['y'] = max(LIMITS['min_y'], min(target_goal['y'], LIMITS['max_y']))
    target_goal['z'] = max(LIMITS['min_z'], min(target_goal['z'], LIMITS['max_z']))
    target_goal['gripper'] = max(GRIPPER_MIN, min(target_goal['gripper'], GRIPPER_MAX))
    current_pos.update(target_goal)
    send_robot_command_direct(target_goal['x'], target_goal['y'], target_goal['z'], target_goal['gripper'])

def homing_callback(sender, app_data, user_data):
    threading.Thread(target=homing_thread_func, daemon=True).start()

def homing_thread_func():
    global ser, manual_override_until
    if ser:
        manual_override_until = time.time() + 20.0
        dashboard_state["status"] = "HOMING..."; write_log("Homing...")
        ser.write(b"$H\r\n"); time.sleep(15); ser.write(b"M20\r\n"); ser.write(b"G90\r\n"); ser.write(b"G1 F2000\r\n")
        update_robot_pos(200,0,120,40)
        ser.write(b"G0 X200 Y0 Z120 F2000\r\n"); ser.write(b"M3 S40\r\n")
        dashboard_state["status"] = "Idle"; write_log("Homing Done")

# ================= [Node System Base] =================
class BaseNode(ABC):
    def __init__(self, node_id, label, type_str):
        self.node_id = node_id; self.label = label; self.type_str = type_str
        self.inputs = {}; self.outputs = {}; self.output_data = {} 

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

# ================= [New Logic Nodes] =================
class CommandActionNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "Robot Action", "CMD_ACTION")
        self.combo_id = None; self.in_val1 = None; self.in_val2 = None; self.in_val3 = None
        self.out_flow = None; self.field_v1 = None; self.field_v2 = None; self.field_v3 = None

    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label="Command Action"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as flow: dpg.add_text("Flow In"); self.inputs[flow] = "Flow"
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                self.combo_id = dpg.add_combo(items=["Move Relative (XYZ)", "Move Absolute (XYZ)", "Set Gripper", "Homing"], default_value="Move Relative (XYZ)", width=150)
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as v1:
                dpg.add_text("X / Grip"); self.field_v1 = dpg.add_input_float(width=60, default_value=0); self.inputs[v1] = "Data"; self.in_val1 = v1
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as v2:
                dpg.add_text("Y"); self.field_v2 = dpg.add_input_float(width=60, default_value=0); self.inputs[v2] = "Data"; self.in_val2 = v2
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as v3:
                dpg.add_text("Z"); self.field_v3 = dpg.add_input_float(width=60, default_value=0); self.inputs[v3] = "Data"; self.in_val3 = v3
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out:
                dpg.add_text("Flow Out"); self.outputs[out] = "Flow"; self.out_flow = out

    def execute(self):
        global manual_override_until; manual_override_until = time.time() + 1.0 
        mode = dpg.get_value(self.combo_id)
        v1 = self.fetch_input_data(self.in_val1); v1 = float(v1) if v1 is not None else dpg.get_value(self.field_v1)
        v2 = self.fetch_input_data(self.in_val2); v2 = float(v2) if v2 is not None else dpg.get_value(self.field_v2)
        v3 = self.fetch_input_data(self.in_val3); v3 = float(v3) if v3 is not None else dpg.get_value(self.field_v3)

        if mode.startswith("Move Rel"):
            target_goal['x'] += v1; target_goal['y'] += v2; target_goal['z'] += v3; apply_limits_and_move()
        elif mode.startswith("Move Abs"):
            target_goal['x'] = v1; target_goal['y'] = v2; target_goal['z'] = v3; apply_limits_and_move()
        elif mode.startswith("Set Grip"):
            target_goal['gripper'] = v1; apply_limits_and_move()
        elif mode == "Homing":
            threading.Thread(target=homing_thread_func, daemon=True).start()
        return self.out_flow

    def get_settings(self): return {"mode": dpg.get_value(self.combo_id), "v1": dpg.get_value(self.field_v1), "v2": dpg.get_value(self.field_v2), "v3": dpg.get_value(self.field_v3)}
    def load_settings(self, data):
        dpg.set_value(self.combo_id, data.get("mode", "Move Relative (XYZ)"))
        dpg.set_value(self.field_v1, data.get("v1", 0)); dpg.set_value(self.field_v2, data.get("v2", 0)); dpg.set_value(self.field_v3, data.get("v3", 0))

class LogicIfNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "Logic: IF", "LOGIC_IF")
        self.in_cond = None; self.out_true = None; self.out_false = None
    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label="IF Condition"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as flow: dpg.add_text("Flow In"); self.inputs[flow] = "Flow"
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as cond: dpg.add_text("Condition (Bool)", color=(255,100,100)); self.inputs[cond] = "Data"; self.in_cond = cond
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as t: dpg.add_text("True", color=(100,255,100)); self.outputs[t] = "Flow"; self.out_true = t
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as f: dpg.add_text("False", color=(255,100,100)); self.outputs[f] = "Flow"; self.out_false = f
    def execute(self):
        return self.out_true if self.fetch_input_data(self.in_cond) else self.out_false

class LogicLoopNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "Logic: LOOP", "LOGIC_LOOP")
        self.field_count = None; self.out_loop = None; self.out_finish = None
        self.current_iter = 0; self.target_iter = 0; self.is_active = False
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

class ConditionCompareNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "Check: State", "COND_COMPARE")
        self.combo_target = None; self.combo_op = None; self.in_val = None; self.out_res = None; self.field_val = None
    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label="Check State"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                self.combo_target = dpg.add_combo(["Robot X", "Robot Y", "Robot Z", "Gripper"], default_value="Robot X", width=100)
                self.combo_op = dpg.add_combo([">", "<", "=="], default_value=">", width=50)
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as val: dpg.add_text("Value"); self.field_val = dpg.add_input_float(width=60, default_value=0); self.inputs[val] = "Data"; self.in_val = val
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as res: dpg.add_text("Result (Bool)"); self.outputs[res] = "Data"; self.out_res = res
    def execute(self):
        tgt, op = dpg.get_value(self.combo_target), dpg.get_value(self.combo_op)
        l_val = self.fetch_input_data(self.in_val); ref = float(l_val) if l_val is not None else dpg.get_value(self.field_val)
        curr = current_pos.get(tgt.split()[-1].lower() if "Robot" in tgt else "gripper", 0.0)
        res = (curr > ref) if op == ">" else (curr < ref) if op == "<" else (abs(curr - ref) < 0.1)
        self.output_data[self.out_res] = res
        return None
    def get_settings(self): return {"t": dpg.get_value(self.combo_target), "o": dpg.get_value(self.combo_op), "v": dpg.get_value(self.field_val)}
    def load_settings(self, data): dpg.set_value(self.combo_target, data.get("t", "Robot X")); dpg.set_value(self.combo_op, data.get("o", ">")); dpg.set_value(self.field_val, data.get("v", 0))

class ConditionKeyNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "Check: Key", "COND_KEY")
        self.field_key = None; self.out_res = None
        self.key_map = {"A": dpg.mvKey_A, "B": dpg.mvKey_B, "C": dpg.mvKey_C, "S": dpg.mvKey_S, "W": dpg.mvKey_W} 
    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label="Key Check"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static): dpg.add_text("Key (A-Z):"); self.field_key = dpg.add_input_text(width=50, default_value="A")
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as res: dpg.add_text("Is Pressed?"); self.outputs[res] = "Data"; self.out_res = res
    def execute(self):
        k = dpg.get_value(self.field_key).upper()
        self.output_data[self.out_res] = dpg.is_key_down(self.key_map.get(k, 0))
        return None
    def get_settings(self): return {"k": dpg.get_value(self.field_key)}
    def load_settings(self, data): dpg.set_value(self.field_key, data.get("k", "A"))

# ================= [Existing Nodes] =================
class StartNode(BaseNode):
    def __init__(self, node_id): super().__init__(node_id, "START", "START")
    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label="START"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out: dpg.add_text("Flow Out"); self.outputs[out] = "Flow"; self.out = out
    def execute(self): return self.out 

class KeyboardControlNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "Keyboard (Pi)", "KEYBOARD")
        self.out_x = None; self.out_y = None; self.out_z = None; self.out_g = None
        self.step_size = 10.0; self.grip_step = 5.0; self.cooldown = 0.2; self.last_input_time = 0.0
    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label="Keyboard Input (Step)"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as flow: dpg.add_text("Flow In"); self.inputs[flow] = "Flow"
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static): dpg.add_text("WASD: XY\nQE: Z\nUJ: Grip", color=(255,150,150))
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as x: dpg.add_text("Target X"); self.outputs[x] = "Data"; self.out_x = x
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as y: dpg.add_text("Target Y"); self.outputs[y] = "Data"; self.out_y = y
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as z: dpg.add_text("Target Z"); self.outputs[z] = "Data"; self.out_z = z
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as g: dpg.add_text("Target Grip"); self.outputs[g] = "Data"; self.out_g = g
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as f: dpg.add_text("Flow Out"); self.outputs[f] = "Flow"
    def execute(self):
        global manual_override_until
        if time.time() - self.last_input_time > self.cooldown:
            dx, dy, dz, dg = 0,0,0,0
            if dpg.is_key_down(dpg.mvKey_W): dx=1
            if dpg.is_key_down(dpg.mvKey_S): dx=-1
            if dpg.is_key_down(dpg.mvKey_A): dy=1
            if dpg.is_key_down(dpg.mvKey_D): dy=-1
            if dpg.is_key_down(dpg.mvKey_Q): dz=1
            if dpg.is_key_down(dpg.mvKey_E): dz=-1
            if dpg.is_key_down(dpg.mvKey_J): dg=1
            if dpg.is_key_down(dpg.mvKey_U): dg=-1
            if dx or dy or dz or dg:
                manual_override_until = time.time() + 0.5; self.last_input_time = time.time()
                target_goal['x']+=dx*self.step_size; target_goal['y']+=dy*self.step_size; target_goal['z']+=dz*self.step_size; target_goal['gripper']+=dg*self.grip_step
                target_goal['x'] = max(LIMITS['min_x'], min(target_goal['x'], LIMITS['max_x']))
                target_goal['y'] = max(LIMITS['min_y'], min(target_goal['y'], LIMITS['max_y']))
                target_goal['z'] = max(LIMITS['min_z'], min(target_goal['z'], LIMITS['max_z']))
                target_goal['gripper'] = max(GRIPPER_MIN, min(target_goal['gripper'], GRIPPER_MAX))
        self.output_data[self.out_x]=target_goal['x']; self.output_data[self.out_y]=target_goal['y']; self.output_data[self.out_z]=target_goal['z']; self.output_data[self.out_g]=target_goal['gripper']
        for k, v in self.outputs.items():
            if v == "Flow": return k
        return None

class RobotControlNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "Robot Driver", "ROBOT_CONTROL")
        self.in_x=None; self.in_y=None; self.in_z=None; self.in_g=None; self.in_smooth=None; self.in_g_speed=None
        self.field_x=None; self.field_y=None; self.field_z=None; self.field_g=None; self.field_smooth=None; self.field_g_speed=None 
        self.last_cmd=""; self.cache_ui={'x':0,'y':0,'z':0,'g':0}; self.last_write_time=0; self.write_interval=0.033
    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label=self.label):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as flow: dpg.add_text("Flow In"); self.inputs[flow]="Flow"
            for axis, label, dval, fattr in [('x',"X",200.0,'field_x'), ('y',"Y",0.0,'field_y'), ('z',"Z",120.0,'field_z'), ('g',"G",40.0,'field_g')]:
                with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as aid:
                    with dpg.group(horizontal=True): dpg.add_text(label, color=(255,255,0)); setattr(self, fattr, dpg.add_input_float(width=80, default_value=dval, step=0))
                    self.inputs[aid]="Data"; 
                    if axis=='x':self.in_x=aid
                    elif axis=='y':self.in_y=aid
                    elif axis=='z':self.in_z=aid
                    elif axis=='g':self.in_g=aid
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static): dpg.add_spacer(height=5) 
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as sin:
                with dpg.group(horizontal=True): dpg.add_text("Smth"); self.field_smooth=dpg.add_input_float(width=60, default_value=0.2, step=0)
                self.inputs[sin]="Data"; self.in_smooth=sin
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as gin:
                with dpg.group(horizontal=True): dpg.add_text("Spd "); self.field_g_speed=dpg.add_input_float(width=60, default_value=2.0, step=0)
                self.inputs[gin]="Data"; self.in_g_speed=gin
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as fout: dpg.add_text("Flow Out"); self.outputs[fout]="Flow"
    def execute(self):
        global current_pos, target_goal, manual_override_until
        tx, ty, tz, tg = self.fetch_input_data(self.in_x), self.fetch_input_data(self.in_y), self.fetch_input_data(self.in_z), self.fetch_input_data(self.in_g)
        ls = self.fetch_input_data(self.in_smooth); lg = self.fetch_input_data(self.in_g_speed)
        if ls: dpg.set_value(self.field_smooth, float(ls))
        if lg: dpg.set_value(self.field_g_speed, float(lg))
        
        smooth = 1.0 if time.time()<manual_override_until else max(0.01, min(dpg.get_value(self.field_smooth), 1.0))
        if time.time() > manual_override_until:
            if tx is not None: target_goal['x']=float(tx)
            if ty is not None: target_goal['y']=float(ty)
            if tz is not None: target_goal['z']=float(tz)
            if tg is not None: target_goal['gripper']=float(tg)
        
        dx, dy, dz = target_goal['x']-current_pos['x'], target_goal['y']-current_pos['y'], target_goal['z']-current_pos['z']
        nx = current_pos['x']+dx*smooth if not(abs(dx)<0.5 and abs(dy)<0.5 and abs(dz)<0.5) else target_goal['x']
        ny = current_pos['y']+dy*smooth if not(abs(dx)<0.5 and abs(dy)<0.5 and abs(dz)<0.5) else target_goal['y']
        nz = current_pos['z']+dz*smooth if not(abs(dx)<0.5 and abs(dy)<0.5 and abs(dz)<0.5) else target_goal['z']
        ng = max(GRIPPER_MIN, min(target_goal['gripper'], GRIPPER_MAX))
        
        nx = max(LIMITS['min_x'], min(nx, LIMITS['max_x'])); ny = max(LIMITS['min_y'], min(ny, LIMITS['max_y'])); nz = max(LIMITS['min_z'], min(nz, LIMITS['max_z']))
        current_pos.update({'x':nx, 'y':ny, 'z':nz, 'gripper':ng})
        
        if abs(self.cache_ui['x']-nx)>0.1: dpg.set_value(self.field_x, nx); self.cache_ui['x']=nx
        if abs(self.cache_ui['y']-ny)>0.1: dpg.set_value(self.field_y, ny); self.cache_ui['y']=ny
        if abs(self.cache_ui['z']-nz)>0.1: dpg.set_value(self.field_z, nz); self.cache_ui['z']=nz
        if abs(self.cache_ui['g']-ng)>0.1: dpg.set_value(self.field_g, ng); self.cache_ui['g']=ng

        if time.time()-self.last_write_time > self.write_interval:
            cmd = f"G0 X{nx:.1f} Y{ny:.1f} Z{nz:.1f}\nM3 S{int(ng)}\n"
            if cmd != self.last_cmd:
                global ser
                try: 
                    if ser and ser.is_open: ser.write(cmd.encode()); self.last_write_time=time.time()
                except: dashboard_state["hw_link"]="Offline"; ser=None
                self.last_cmd = cmd
        
        for k, v in self.outputs.items():
            if v == "Flow": return k
        return None
    def get_settings(self): return {"s": dpg.get_value(self.field_smooth), "sp": dpg.get_value(self.field_g_speed)}
    def load_settings(self, data): dpg.set_value(self.field_smooth, data.get("s", 0.2)); dpg.set_value(self.field_g_speed, data.get("sp", 2.0))

class UDPReceiverNode(BaseNode):
    def __init__(self, node_id): super().__init__(node_id, "UDP Receiver", "UDP_RECV"); self.out_flow=None; self.port=None; self.ip=None; self.out_json=None
    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label="UDP Receiver"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as f: dpg.add_text("Flow In"); self.inputs[f]="Flow"
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static): dpg.add_input_int(label="Port", width=80, default_value=6000, tag=f"p_{self.node_id}"); self.port=f"p_{self.node_id}"
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static): dpg.add_input_text(label="IP", width=100, default_value="192.168.50.63", tag=f"i_{self.node_id}"); self.ip=f"i_{self.node_id}"
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as d: dpg.add_text("JSON Out"); self.outputs[d]="Data"; self.out_json=d
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as o: dpg.add_text("Flow Out"); self.outputs[o]="Flow"; self.out_flow=o
    def execute(self):
        try:
            fb = {"x": -current_pos['y']/1000.0, "y": current_pos['z']/1000.0, "z": current_pos['x']/1000.0, "gripper": current_pos['gripper'], "status": "Running"}
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.sendto(json.dumps(fb).encode(), (dpg.get_value(self.ip), FEEDBACK_PORT))
        except: pass
        return self.out_flow
    def get_settings(self): return {"port": dpg.get_value(self.port), "ip": dpg.get_value(self.ip)}
    def load_settings(self, data): dpg.set_value(self.port, data.get("port", 6000)); dpg.set_value(self.ip, data.get("ip", "192.168.50.63"))

class UnityControlNode(BaseNode):
    def __init__(self, node_id): super().__init__(node_id, "Unity Logic", "UNITY_CONTROL"); self.d_in=None; self.ox=None; self.oy=None; self.oz=None; self.og=None
    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label="Unity Logic"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as f: dpg.add_text("Flow In"); self.inputs[f]="Flow"
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as d: dpg.add_text("JSON"); self.inputs[d]="Data"; self.d_in=d
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as ox: dpg.add_text("Target X"); self.outputs[ox]="Data"; self.ox=ox
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as oy: dpg.add_text("Target Y"); self.outputs[oy]="Data"; self.oy=oy
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as oz: dpg.add_text("Target Z"); self.outputs[oz]="Data"; self.oz=oz
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as og: dpg.add_text("Target Grip"); self.outputs[og]="Data"; self.og=og
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as fo: dpg.add_text("Flow Out"); self.outputs[fo]="Flow"
    def execute(self):
        raw = self.fetch_input_data(self.d_in)
        if raw:
            try:
                p = json.loads(raw)
                if p.get("type")=="MOVE":
                    self.output_data[self.ox]=p.get('z',0)*1000; self.output_data[self.oy]=-p.get('x',0)*1000; self.output_data[self.oz]=p.get('y',0)*1000; self.output_data[self.og]=p.get('gripper')
            except: pass
        for k, v in self.outputs.items():
            if v == "Flow": return k
        return None

class JsonParseNode(BaseNode):
    def __init__(self, node_id): super().__init__(node_id, "Simple Parser", "JSON_PARSE"); self.d_in=None; self.d_out=None
    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label="JSON Parser"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as f: dpg.add_text("Flow In"); self.inputs[f]="Flow"
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as d: dpg.add_text("JSON In"); self.inputs[d]="Data"; self.d_in=d
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as o: dpg.add_text("Data Out"); self.outputs[o]="Data"; self.d_out=o
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as fo: dpg.add_text("Flow Out"); self.outputs[fo]="Flow"
    def execute(self):
        for k, v in self.outputs.items():
            if v == "Flow": return k
        return None

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

class GraphNode(BaseNode):
    def __init__(self, node_id): super().__init__(node_id, "Live Graph", "GRAPH"); self.in_x=None; self.in_y=None; self.in_z=None; self.buf_x=deque(maxlen=200); self.t=deque(maxlen=200); self.c=0
    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label="Live Graph"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as f: dpg.add_text("Flow In"); self.inputs[f]="Flow"
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as x: dpg.add_text("X", color=(255,0,0)); self.inputs[x]="Data"; self.in_x=x
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as y: dpg.add_text("Y", color=(0,255,0)); self.inputs[y]="Data"; self.in_y=y
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as z: dpg.add_text("Z", color=(0,0,255)); self.inputs[z]="Data"; self.in_z=z
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                with dpg.plot(height=150, width=250):
                    dpg.add_plot_legend()
                    dpg.add_plot_axis(dpg.mvXAxis, label="Time", tag=f"ax_{self.node_id}")
                    with dpg.plot_axis(dpg.mvYAxis, label="Val", tag=f"ay_{self.node_id}"):
                        dpg.add_line_series([],[], label="X", tag=f"sx_{self.node_id}")
                        dpg.add_line_series([],[], label="Y", tag=f"sy_{self.node_id}")
                        dpg.add_line_series([],[], label="Z", tag=f"sz_{self.node_id}")
    def execute(self):
        self.c+=1; vx=self.fetch_input_data(self.in_x); vy=self.fetch_input_data(self.in_y); vz=self.fetch_input_data(self.in_z)
        if vx is not None:
            self.buf_x.append(vx); self.t.append(self.c)
            dpg.set_value(f"sx_{self.node_id}", [list(self.t), list(self.buf_x)])
            dpg.set_axis_limits(f"ax_{self.node_id}", self.c-200, self.c)
            dpg.set_axis_limits(f"ay_{self.node_id}", min(self.buf_x)-10, max(self.buf_x)+10)
        return None

class LoggerNode(BaseNode):
    def __init__(self, node_id): super().__init__(node_id, "System Log", "LOGGER"); self.txt=None; self.llen=0
    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label="Logger"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as f: dpg.add_text("Flow In"); self.inputs[f]="Flow"
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                with dpg.child_window(width=200, height=100): self.txt=dpg.add_text("", wrap=190)
    def execute(self):
        if len(system_log_buffer)!=self.llen:
            dpg.set_value(self.txt, "\n".join(list(system_log_buffer)[-8:])); self.llen=len(system_log_buffer)
        return None

# ================= [Execution Engine (Hybrid)] =================
def execute_graph_once():
    start_node = None
    for node in node_registry.values():
        if isinstance(node, StartNode): start_node = node; break
    if not start_node: return

    for node in node_registry.values():
        if not isinstance(node, (StartNode, CommandActionNode, LogicIfNode, LogicLoopNode, UDPReceiverNode, PrintNode, KeyboardControlNode, RobotControlNode)):
            node.execute()

    current_node = start_node
    steps = 0
    MAX_STEPS = 100 

    while current_node and steps < MAX_STEPS:
        result = current_node.execute()
        next_out_id = None

        if result is not None:
            if isinstance(result, (int, str)):
                next_out_id = result
            elif isinstance(result, dict):
                for k, v in result.items():
                    if v == "Flow": next_out_id = k; break
        
        next_node = None
        if next_out_id:
            for link in link_registry.values():
                if link['source'] == next_out_id:
                    target_node_id = dpg.get_item_parent(link['target'])
                    if target_node_id in node_registry:
                        next_node = node_registry[target_node_id]
                        break
        current_node = next_node
        steps += 1

# ================= [Factory & Serialization] =================
class NodeFactory:
    @staticmethod
    def create_node(node_type, node_id=None):
        if node_id is None: node_id = dpg.generate_uuid()
        node = None
        if node_type == "START": node = StartNode(node_id, "START")
        elif node_type == "CMD_ACTION": node = CommandActionNode(node_id)
        elif node_type == "LOGIC_IF": node = LogicIfNode(node_id)
        elif node_type == "LOGIC_LOOP": node = LogicLoopNode(node_id)
        elif node_type == "COND_COMPARE": node = ConditionCompareNode(node_id)
        elif node_type == "COND_KEY": node = ConditionKeyNode(node_id)
        elif node_type == "UDP_RECV": node = UDPReceiverNode(node_id)
        elif node_type == "CONSTANT": node = ConstantNode(node_id)
        elif node_type == "PRINT": node = PrintNode(node_id)
        elif node_type == "KEYBOARD": node = KeyboardControlNode(node_id)
        elif node_type == "ROBOT_CONTROL": node = RobotControlNode(node_id)
        elif node_type == "UNITY_CONTROL": node = UnityControlNode(node_id)
        elif node_type == "JSON_PARSE": node = JsonParseNode(node_id)
        elif node_type == "GRAPH": node = GraphNode(node_id)
        elif node_type == "LOGGER": node = LoggerNode(node_id)
        
        if node: node.build_ui(); node_registry[node_id] = node; return node
        return None

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

def update_file_list():
    dpg.configure_item("file_list_combo", items=get_save_files())

def delete_selection(sender, app_data):
    selected_links = dpg.get_selected_links("node_editor")
    selected_nodes = dpg.get_selected_nodes("node_editor")
    
    for lid in selected_links:
        if lid in link_registry: del link_registry[lid]
        if dpg.does_item_exist(lid): dpg.delete_item(lid)

    for nid in selected_nodes:
        if nid not in node_registry: continue
        node = node_registry[nid]
        my_ports = set(node.inputs.keys()) | set(node.outputs.keys())
        links_to_remove = []
        for lid, ldata in link_registry.items():
            if ldata['source'] in my_ports or ldata['target'] in my_ports: links_to_remove.append(lid)
        for lid in links_to_remove:
            if lid in link_registry: del link_registry[lid]
            if dpg.does_item_exist(lid): dpg.delete_item(lid)
        del node_registry[nid]
        if dpg.does_item_exist(nid): dpg.delete_item(nid)

def link_cb(sender, app_data):
    src, dst = app_data[0], app_data[1] if len(app_data) == 2 else (app_data[1], app_data[2])
    link_id = dpg.add_node_link(src, dst, parent=sender)
    link_registry[link_id] = {'source': src, 'target': dst}

def del_link_cb(sender, app_data):
    dpg.delete_item(app_data)
    if app_data in link_registry: del link_registry[app_data]

def add_node_cb(s, a, u): NodeFactory.create_node(u)
def toggle_exec(s, a): global is_running; is_running = not is_running; dpg.set_item_label("btn_run", "STOP" if is_running else "RUN")
def save_cb(s, a): save_graph(dpg.get_value("file_name_input"))
def load_cb(s, a): load_graph(dpg.get_value("file_list_combo"))

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
threading.Thread(target=lambda: (time.sleep(1), update_file_list()), daemon=True).start()

dpg.create_context()
with dpg.handler_registry(): dpg.add_key_press_handler(dpg.mvKey_Delete, callback=delete_selection)

with dpg.window(tag="PrimaryWindow"):
    # [1번 줄] System Status | Manual Control | Direct Coord (v19 Restore)
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

        with dpg.child_window(width=300, height=130, border=True):
            dpg.add_text("Direct Coord", color=(0,255,255))
            with dpg.group(horizontal=True):
                dpg.add_text("X"); dpg.add_input_int(tag="input_x", width=50, default_value=200, step=0)
                dpg.add_text("Y"); dpg.add_input_int(tag="input_y", width=50, default_value=0, step=0)
            with dpg.group(horizontal=True):
                dpg.add_text("Z"); dpg.add_input_int(tag="input_z", width=50, default_value=120, step=0)
                dpg.add_text("G"); dpg.add_input_int(tag="input_g", width=50, default_value=40, step=0)
            with dpg.group(horizontal=True):
                dpg.add_button(label="Move", width=100, callback=move_to_coord_callback)
                dpg.add_button(label="Homing", width=100, callback=homing_callback)

    # [2번 줄] File Manager & IP Info
    with dpg.group(horizontal=True):
        # 4. File Manager
        with dpg.child_window(width=400, height=100, border=True):
            dpg.add_text("Graph File Manager", color=(0,255,255))
            with dpg.group(horizontal=True):
                dpg.add_text("Save As:"); dpg.add_input_text(tag="file_name_input", default_value="my_graph", width=150); dpg.add_button(label="SAVE", callback=save_cb, width=60)
            with dpg.group(horizontal=True):
                dpg.add_text("Load File:"); dpg.add_combo(items=get_save_files(), tag="file_list_combo", width=150); dpg.add_button(label="LOAD", callback=load_cb, width=60); dpg.add_button(label="Refresh", callback=update_file_list, width=60)
        
        # 5. IP Info
        with dpg.child_window(width=500, height=100, border=False):
            dpg.add_spacer(height=20)
            dpg.add_text(f"My IP: {my_ip} | SSID: {my_ssid}", color=(180,180,180))
            dpg.add_text(f"Target IP: {UNITY_IP}  Port: {FEEDBACK_PORT}", color=(180,180,180))

    dpg.add_separator()
    # Tool Bar (New + Old)
    with dpg.group(horizontal=True):
        dpg.add_button(label="START", callback=add_node_cb, user_data="START")
        dpg.add_button(label="UDP", callback=add_node_cb, user_data="UDP_RECV")
        dpg.add_spacer(width=10)
        # New Logic
        dpg.add_button(label="ACTION", callback=add_node_cb, user_data="CMD_ACTION", width=60)
        dpg.add_button(label="IF", callback=add_node_cb, user_data="LOGIC_IF", width=40)
        dpg.add_button(label="LOOP", callback=add_node_cb, user_data="LOGIC_LOOP", width=50)
        dpg.add_button(label="CHK STATE", callback=add_node_cb, user_data="COND_COMPARE")
        dpg.add_button(label="CHK KEY", callback=add_node_cb, user_data="COND_KEY")
        dpg.add_spacer(width=10)
        # Legacy Nodes
        dpg.add_button(label="KEY", callback=add_node_cb, user_data="KEYBOARD")
        dpg.add_button(label="ROBOT", callback=add_node_cb, user_data="ROBOT_CONTROL")
        dpg.add_button(label="UNITY", callback=add_node_cb, user_data="UNITY_CONTROL")
        dpg.add_button(label="CONST", callback=add_node_cb, user_data="CONSTANT")
        dpg.add_button(label="GRAPH", callback=add_node_cb, user_data="GRAPH")
        dpg.add_button(label="LOG", callback=add_node_cb, user_data="LOGGER")
        dpg.add_spacer(width=50)
        dpg.add_button(label="RUN", tag="btn_run", callback=toggle_exec, width=150)

    with dpg.node_editor(tag="node_editor", callback=link_cb, delink_callback=del_link_cb): pass

dpg.create_viewport(title='PyGui V20 (Perfect Merge)', width=1024, height=768, vsync=True)
dpg.setup_dearpygui(); dpg.set_primary_window("PrimaryWindow", True); dpg.show_viewport()

last_logic_time = 0
LOGIC_RATE = 0.02 # 50 FPS

while dpg.is_dearpygui_running():
    if dashboard_state["last_pkt_time"] > 0:
        dpg.set_value("dash_status", dashboard_state["status"])
        dpg.set_value("dash_latency", f"{dashboard_state['latency']:.1f} ms")
    
    if is_running and (time.time() - last_logic_time > LOGIC_RATE):
        execute_graph_once()
        last_logic_time = time.time()
    dpg.render_dearpygui_frame()
dpg.destroy_context()