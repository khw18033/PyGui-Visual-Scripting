import dearpygui.dearpygui as dpg
import time
import os
import socket
import json
import serial 
import threading
import subprocess 
from abc import ABC, abstractmethod

# ================= [Global Settings] =================
node_registry = {}
link_registry = {}
ser = None 
is_running = False 

# Robot State
current_pos = {'x': 200.0, 'y': 0.0, 'z': 120.0, 'gripper': 40.0}
target_goal = {'x': 200.0, 'y': 0.0, 'z': 120.0, 'gripper': 40.0} 

# Dashboard State
dashboard_state = {
    "status": "Idle",
    "hw_link": "Offline",
    "latency": 0.0,
    "last_pkt_time": 0.0
}

# Config
UNITY_IP = "192.168.50.63" 
FEEDBACK_PORT = 5005
SMOOTHING_FACTOR = 0.2  

GRIPPER_SPEED = 2.0 
GRIPPER_MIN = 30.0
GRIPPER_MAX = 70.0

LIMITS = {'min_x': 100, 'max_x': 280, 'min_y': -150, 'max_y': 150, 'min_z': 0, 'max_z': 180}

# ================= [Helper Functions] =================
def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip

def get_wifi_ssid():
    try:
        ssid = subprocess.check_output(['iwgetid', '-r']).decode('utf-8').strip()
        if not ssid: return "Unknown"
        return ssid
    except:
        return "Unknown"

# ================= [0. Robot Init] =================
def init_serial():
    global ser
    try:
        ser = serial.Serial('/dev/ttyUSB0', 115200, timeout=0.05)
        dashboard_state["hw_link"] = "Online"
        print("[System] MT4 Robot Connected")
        
        time.sleep(2); ser.write(b"$H\r\n"); time.sleep(15) 
        ser.write(b"M20\r\n"); ser.write(b"G90\r\n"); ser.write(b"G1 F2000\r\n"); time.sleep(1)
        
        # 초기 위치 강제 동기화 (오차 방지)
        current_pos.update({'x':200.0, 'y':0.0, 'z':120.0, 'gripper':40.0})
        target_goal.update({'x':200.0, 'y':0.0, 'z':120.0, 'gripper':40.0})
        
        cmd = f"G0 X200 Y0 Z120 F2000\r\n"
        ser.write(cmd.encode()); ser.write(b"M3 S40\r\n") 
        print("[System] Ready")
    except Exception as e:
        dashboard_state["hw_link"] = "Simulation"
        print(f"[System] Connection Failed: {e}")
        ser = None

# 로봇에게 직접 명령 보내기 (수동 제어용)
def send_robot_command_direct(x, y, z, g):
    global ser
    if ser and ser.is_open:
        cmd_move = f"G0 X{x:.1f} Y{y:.1f} Z{z:.1f}\n"
        cmd_grip = f"M3 S{int(g)}\n"
        ser.write(cmd_move.encode())
        ser.write(cmd_grip.encode())

# ================= [1. Dashboard Callbacks] =================
def manual_control_callback(sender, app_data, user_data):
    axis, step = user_data
    
    # 1. 목표값 변경
    if axis == 'gripper':
        val = 60.0 if target_goal['gripper'] < 50 else 40.0
        target_goal['gripper'] = val
    else:
        target_goal[axis] = current_pos[axis] + step
        # Limit Check
        if axis == 'x': target_goal[axis] = max(LIMITS['min_x'], min(target_goal[axis], LIMITS['max_x']))
        if axis == 'y': target_goal[axis] = max(LIMITS['min_y'], min(target_goal[axis], LIMITS['max_y']))
        if axis == 'z': target_goal[axis] = max(LIMITS['min_z'], min(target_goal[axis], LIMITS['max_z']))

    # 2. 수동 제어는 즉시 반응 (스무딩 건너뛰기 & 바로 전송)
    current_pos.update(target_goal) # 현재 위치도 바로 갱신 (오차 누적 방지)
    
    send_robot_command_direct(target_goal['x'], target_goal['y'], target_goal['z'], target_goal['gripper'])
    
    # UI 값도 갱신
    if dpg.does_item_exist("input_x"):
        dpg.set_value("input_x", int(target_goal['x']))
        dpg.set_value("input_y", int(target_goal['y']))
        dpg.set_value("input_z", int(target_goal['z']))
        dpg.set_value("input_g", int(target_goal['gripper']))

def move_to_coord_callback(sender, app_data, user_data):
    # 입력창 값 가져오기
    target_goal['x'] = float(dpg.get_value("input_x"))
    target_goal['y'] = float(dpg.get_value("input_y"))
    target_goal['z'] = float(dpg.get_value("input_z"))
    target_goal['gripper'] = float(dpg.get_value("input_g"))
    
    # 즉시 이동
    current_pos.update(target_goal)
    send_robot_command_direct(target_goal['x'], target_goal['y'], target_goal['z'], target_goal['gripper'])

def homing_thread_func():
    global ser
    if ser:
        dashboard_state["status"] = "HOMING..."
        ser.write(b"$H\r\n")
        time.sleep(15)
        ser.write(b"M20\r\n"); ser.write(b"G90\r\n"); ser.write(b"G1 F2000\r\n")
        
        # Homing 오차 제거 (강제 동기화)
        target_goal['x'] = 200.0; target_goal['y'] = 0.0; target_goal['z'] = 120.0
        target_goal['gripper'] = 40.0
        current_pos.update(target_goal)
        
        # UI 동기화
        if dpg.does_item_exist("input_x"):
            dpg.set_value("input_x", 200); dpg.set_value("input_y", 0); dpg.set_value("input_z", 120)
        
        cmd = f"G0 X200 Y0 Z120 F2000\r\n"
        ser.write(cmd.encode()); ser.write(b"M3 S40\r\n")
        dashboard_state["status"] = "Idle"

def homing_callback(sender, app_data, user_data):
    threading.Thread(target=homing_thread_func, daemon=True).start()

# ================= [2. Base Class] =================
class BaseNode(ABC):
    def __init__(self, node_id, label):
        self.node_id = node_id; self.label = label
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

# ================= [3. Nodes] =================
class StartNode(BaseNode):
    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label="START"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out_id: dpg.add_text("Flow Out"); self.outputs[out_id] = "Flow"
    def execute(self): return self.outputs

class ConstantNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "Constant")
        self.out_val = None; self.field_val = None
    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label=self.label):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                self.field_val = dpg.add_input_float(width=80, default_value=1.0, step=0.1) # Label removed for compact
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out:
                dpg.add_text("Output"); self.outputs[out] = "Data"; self.out_val = out
    def execute(self):
        val = dpg.get_value(self.field_val)
        self.output_data[self.out_val] = val
        return self.outputs

class UDPReceiverNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "UDP Receiver")
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
        port = dpg.get_value(self.port_input)
        UNITY_IP = dpg.get_value(self.target_ip_input)
        if not self.is_bound:
            try: self.sock.bind(('0.0.0.0', port)); self.is_bound = True
            except: self.is_bound = True

        latest_data = None
        try:
            while True: data, _ = self.sock.recvfrom(4096); latest_data = data
        except: pass

        if latest_data:
            decoded = latest_data.decode()
            current_time = time.time()
            if dashboard_state["last_pkt_time"] > 0:
                dashboard_state["latency"] = (current_time - dashboard_state["last_pkt_time"]) * 1000.0
                dashboard_state["status"] = "Connected"
            dashboard_state["last_pkt_time"] = current_time

            if decoded != self.last_data_str:
                self.output_data[self.data_out_id] = decoded
                self.last_data_str = decoded
        
        try:
            feedback_data = {"x": -current_pos['y']/1000.0, "y": current_pos['z']/1000.0, "z": current_pos['x']/1000.0, 
                             "gripper": current_pos['gripper'], "status": "Running"}
            self.sock_feedback.sendto(json.dumps(feedback_data).encode(), (UNITY_IP, FEEDBACK_PORT))
        except: pass
        return self.outputs

class UnityControlNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "Unity Logic")
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

class RobotControlNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "Robot Driver")
        self.in_x = None; self.in_y = None; self.in_z = None; self.in_g = None
        self.in_smooth = None; self.in_g_speed = None
        self.field_x = None; self.field_y = None; self.field_z = None; self.field_g = None
        self.field_smooth = None; self.field_g_speed = None 
        self.last_cmd = ""; self.cache_ui = {'x':0, 'y':0, 'z':0, 'g':0}

    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label=self.label):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as in_flow: dpg.add_text("Flow In"); self.inputs[in_flow] = "Flow"
            
            # ★ [수정] 컴팩트 UI 적용 (Label 분리 & Horizontal Group)
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as x_in:
                with dpg.group(horizontal=True):
                    dpg.add_text("X")
                    self.field_x = dpg.add_input_float(width=50, default_value=200.0, step=0)
                self.inputs[x_in] = "Data"; self.in_x = x_in
                
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as y_in:
                with dpg.group(horizontal=True):
                    dpg.add_text("Y")
                    self.field_y = dpg.add_input_float(width=50, default_value=0.0, step=0)
                self.inputs[y_in] = "Data"; self.in_y = y_in
                
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as z_in:
                with dpg.group(horizontal=True):
                    dpg.add_text("Z")
                    self.field_z = dpg.add_input_float(width=50, default_value=120.0, step=0)
                self.inputs[z_in] = "Data"; self.in_z = z_in
                
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as g_in:
                with dpg.group(horizontal=True):
                    dpg.add_text("G")
                    self.field_g = dpg.add_input_float(width=50, default_value=40.0, step=0)
                self.inputs[g_in] = "Data"; self.in_g = g_in

            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                dpg.add_separator()

            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as s_in:
                with dpg.group(horizontal=True):
                    dpg.add_text("Smth")
                    self.field_smooth = dpg.add_input_float(width=40, default_value=0.2, step=0.05, min_value=0.01, max_value=1.0)
                self.inputs[s_in] = "Data"; self.in_smooth = s_in
            
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as gs_in:
                with dpg.group(horizontal=True):
                    dpg.add_text("Spd ")
                    self.field_g_speed = dpg.add_input_float(width=40, default_value=2.0, step=0.1, min_value=0.1, max_value=10.0)
                self.inputs[gs_in] = "Data"; self.in_g_speed = gs_in

            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as f_out: dpg.add_text("Flow Out"); self.outputs[f_out] = "Flow"

    def execute(self):
        global current_pos, target_goal
        
        tx, ty, tz, tg = self.fetch_input_data(self.in_x), self.fetch_input_data(self.in_y), self.fetch_input_data(self.in_z), self.fetch_input_data(self.in_g)

        link_smooth = self.fetch_input_data(self.in_smooth)
        if link_smooth is not None:
            smooth_factor = float(link_smooth); dpg.set_value(self.field_smooth, smooth_factor)
        else:
            smooth_factor = dpg.get_value(self.field_smooth)

        link_gs = self.fetch_input_data(self.in_g_speed)
        if link_gs is not None:
            gripper_speed = float(link_gs); dpg.set_value(self.field_g_speed, gripper_speed)
        else:
            gripper_speed = dpg.get_value(self.field_g_speed)

        smooth_factor = max(0.01, min(smooth_factor, 1.0))
        gripper_speed = max(0.1, min(gripper_speed, 10.0))

        if tx is not None: target_goal['x'] = float(tx)
        if ty is not None: target_goal['y'] = float(ty)
        if tz is not None: target_goal['z'] = float(tz)
        if tg is not None: target_goal['gripper'] = float(tg)

        dx, dy, dz = target_goal['x'] - current_pos['x'], target_goal['y'] - current_pos['y'], target_goal['z'] - current_pos['z']
        
        # Smooth Move
        if abs(dx)<0.5 and abs(dy)<0.5 and abs(dz)<0.5:
             next_x, next_y, next_z = target_goal['x'], target_goal['y'], target_goal['z']
        else:
            next_x = current_pos['x'] + dx * smooth_factor
            next_y = current_pos['y'] + dy * smooth_factor
            next_z = current_pos['z'] + dz * smooth_factor
        
        next_x = max(LIMITS['min_x'], min(next_x, LIMITS['max_x']))
        next_y = max(LIMITS['min_y'], min(next_y, LIMITS['max_y']))
        next_z = max(LIMITS['min_z'], min(next_z, LIMITS['max_z']))

        # Gripper Move
        received_g = target_goal['gripper']
        if received_g is None: next_g = current_pos['gripper']
        elif received_g > 50: next_g = current_pos['gripper'] + gripper_speed
        elif received_g < 50: next_g = current_pos['gripper'] - gripper_speed
        else: next_g = current_pos['gripper']
        next_g = max(GRIPPER_MIN, min(next_g, GRIPPER_MAX))

        current_pos.update({'x': next_x, 'y': next_y, 'z': next_z, 'gripper': next_g})

        # Update UI
        if abs(self.cache_ui['x'] - next_x) > 0.1: dpg.set_value(self.field_x, next_x); self.cache_ui['x'] = next_x
        if abs(self.cache_ui['y'] - next_y) > 0.1: dpg.set_value(self.field_y, next_y); self.cache_ui['y'] = next_y
        if abs(self.cache_ui['z'] - next_z) > 0.1: dpg.set_value(self.field_z, next_z); self.cache_ui['z'] = next_z
        if abs(self.cache_ui['g'] - next_g) > 0.1: dpg.set_value(self.field_g, next_g); self.cache_ui['g'] = next_g

        cmd_move = f"G0 X{next_x:.1f} Y{next_y:.1f} Z{next_z:.1f}\n"
        cmd_grip = f"M3 S{int(next_g)}\n"
        full_cmd = cmd_move + cmd_grip
        if full_cmd != self.last_cmd:
            if ser and ser.is_open: ser.write(cmd_move.encode()); ser.write(cmd_grip.encode())
            self.last_cmd = full_cmd
        return self.outputs

class JsonParseNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "Simple Parser")
        self.data_in_id = None; self.out_x = None
    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label=self.label):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as in_flow: dpg.add_text("Flow In"); self.inputs[in_flow] = "Flow"
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as d_in: dpg.add_text("JSON In"); self.inputs[d_in] = "Data"; self.data_in_id = d_in
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out_x: dpg.add_text("Data Out"); self.outputs[out_x] = "Data"; self.out_x = out_x
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as f_out: dpg.add_text("Flow Out"); self.outputs[f_out] = "Flow"
    def execute(self): return self.outputs

class PrintNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "Status Monitor")
        self.input_field = None; self.data_in_id = None; self.last_msg = ""
    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label=self.label):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as in_id: dpg.add_text("Flow In"); self.inputs[in_id] = "Flow"
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as d_in: dpg.add_text("Data In"); self.inputs[d_in] = "Data"; self.data_in_id = d_in
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static): self.input_field = dpg.add_input_text(label="Msg", width=120)
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out_id: dpg.add_text("Flow Out"); self.outputs[out_id] = "Flow"
    def execute(self):
        msg = f"{current_pos['gripper']:.1f}"
        if msg != self.last_msg: dpg.set_value(self.input_field, msg); self.last_msg = msg
        return self.outputs

class NodeFactory:
    @staticmethod
    def create_node(node_type):
        node_id = dpg.generate_uuid()
        node = None
        if node_type == "START": node = StartNode(node_id, "START")
        elif node_type == "PRINT": node = PrintNode(node_id)
        elif node_type == "UDP_RECV": node = UDPReceiverNode(node_id)
        elif node_type == "UNITY_CONTROL": node = UnityControlNode(node_id)
        elif node_type == "ROBOT_CONTROL": node = RobotControlNode(node_id)
        elif node_type == "JSON_PARSE": node = JsonParseNode(node_id)
        elif node_type == "CONSTANT": node = ConstantNode(node_id)
        if node: node.build_ui(); node_registry[node_id] = node; return node
        return None

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

def toggle_execution(sender, app_data):
    global is_running
    is_running = not is_running
    label = "STOP" if is_running else "RUN"
    dpg.set_item_label("btn_run", label)

def delete_selection(sender, app_data):
    for link_id in dpg.get_selected_links("node_editor"):
        dpg.delete_item(link_id)
        if link_id in link_registry: del link_registry[link_id]
    for node_id in dpg.get_selected_nodes("node_editor"):
        dpg.delete_item(node_id)
        if node_id in node_registry: del node_registry[node_id]

def link_cb(sender, app_data):
    src, dst = app_data[0], app_data[1] if len(app_data) == 2 else (app_data[1], app_data[2])
    link_id = dpg.add_node_link(src, dst, parent=sender)
    link_registry[link_id] = {'source': src, 'target': dst}

def del_link_cb(sender, app_data):
    dpg.delete_item(app_data)
    if app_data in link_registry: del link_registry[app_data]

def add_node_cb(sender, app_data, user_data):
    NodeFactory.create_node(user_data)

# ================= [Main Setup] =================
init_serial()
dpg.create_context()
with dpg.handler_registry(): dpg.add_key_press_handler(dpg.mvKey_Delete, callback=delete_selection)

my_ip = get_local_ip()
my_ssid = get_wifi_ssid()

with dpg.window(tag="PrimaryWindow"):
    
    # ★ [대시보드]
    with dpg.group(horizontal=True):
        # 1. 상태창
        with dpg.child_window(width=250, height=130, border=True):
            dpg.add_text("System Status", color=(150,150,150))
            dpg.add_text("Idle", tag="dash_status", color=(0,255,0))
            dpg.add_spacer(height=5)
            dpg.add_text("Hardware Link", color=(150,150,150))
            dpg.add_text(dashboard_state["hw_link"], tag="dash_link", color=(0,255,0) if dashboard_state["hw_link"]=="Online" else (255,0,0))
            dpg.add_spacer(height=5)
            dpg.add_text("Network Interval", color=(150,150,150))
            dpg.add_text("0.0 ms", tag="dash_latency", color=(255,255,0))

        # 2. 수동 제어
        with dpg.child_window(width=350, height=130, border=True):
            dpg.add_text("Manual Control (10mm)", color=(255,200,0))
            with dpg.group(horizontal=True):
                dpg.add_button(label="X+", width=60, callback=manual_control_callback, user_data=('x', 10))
                dpg.add_button(label="X-", width=60, callback=manual_control_callback, user_data=('x', -10))
                dpg.add_text("|", color=(100,100,100))
                dpg.add_button(label="Y+", width=60, callback=manual_control_callback, user_data=('y', 10))
                dpg.add_button(label="Y-", width=60, callback=manual_control_callback, user_data=('y', -10))
            with dpg.group(horizontal=True):
                dpg.add_button(label="Z+", width=60, callback=manual_control_callback, user_data=('z', 10))
                dpg.add_button(label="Z-", width=60, callback=manual_control_callback, user_data=('z', -10))
                dpg.add_text("|", color=(100,100,100))
                dpg.add_button(label="Gripper", width=70, callback=manual_control_callback, user_data=('gripper', 0))

        # 3. 직접 이동 & 호밍
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

    # 4. 시스템 설정
    with dpg.group(horizontal=True):
        dpg.add_text(f"My IP: {my_ip} | SSID: {my_ssid}", color=(180,180,180))
        dpg.add_spacer(width=50)
        dpg.add_text(f"Target IP: {UNITY_IP}  Port: {FEEDBACK_PORT}")

    dpg.add_separator()
    
    # [노드 에디터]
    with dpg.group(horizontal=True):
        dpg.add_button(label="START", callback=add_node_cb, user_data="START")
        dpg.add_button(label="UDP", callback=add_node_cb, user_data="UDP_RECV")
        dpg.add_button(label="UNITY", callback=add_node_cb, user_data="UNITY_CONTROL")
        dpg.add_button(label="ROBOT", callback=add_node_cb, user_data="ROBOT_CONTROL")
        dpg.add_button(label="STATUS", callback=add_node_cb, user_data="PRINT")
        dpg.add_spacer(width=20)
        dpg.add_button(label="JSON", callback=add_node_cb, user_data="JSON_PARSE")
        dpg.add_button(label="CONST", callback=add_node_cb, user_data="CONSTANT")
        dpg.add_spacer(width=50)
        dpg.add_button(label="RUN", tag="btn_run", callback=toggle_execution, width=150)
    
    with dpg.node_editor(tag="node_editor", callback=link_cb, delink_callback=del_link_cb): pass

dpg.create_viewport(title='PyGui V15 (Final Optimized)', width=1024, height=768, vsync=True)
dpg.setup_dearpygui()
dpg.set_primary_window("PrimaryWindow", True)
dpg.show_viewport()

last_logic_time = 0
LOGIC_RATE = 0.03 

# Main Loop
while dpg.is_dearpygui_running():
    current_time = time.time()
    
    # 대시보드 상태 갱신
    if dashboard_state["last_pkt_time"] > 0:
        dpg.set_value("dash_status", dashboard_state["status"])
        dpg.set_value("dash_latency", f"{dashboard_state['latency']:.1f} ms")
    
    # 노드 로직 실행
    if is_running and (current_time - last_logic_time > LOGIC_RATE):
        execute_graph_once()
        last_logic_time = current_time
        
    dpg.render_dearpygui_frame()

dpg.destroy_context()