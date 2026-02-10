import dearpygui.dearpygui as dpg
import time
import os
import socket
import json
import serial 
import threading 
from abc import ABC, abstractmethod

# ================= [Settings] =================
node_registry = {}
link_registry = {}
ser = None 
is_running = False 
run_thread = None  

# Robot State
current_pos = {'x': 200.0, 'y': 0.0, 'z': 120.0, 'gripper': 40.0}
target_goal = {'x': 200.0, 'y': 0.0, 'z': 120.0, 'gripper': None} # 초기값 None

# Config
UNITY_IP = "192.168.50.63" 
FEEDBACK_PORT = 5005
SMOOTHING_FACTOR = 0.2  

# ★ [핵심] 그리퍼 속도 및 범위 설정
# 속도: 0.2 (값을 키우면 빨라짐)
# 범위: 30(최대 열림) ~ 65(최대 닫힘) -> 하드웨어 보호용
GRIPPER_SPEED = 0.2 
GRIPPER_MIN = 30.0
GRIPPER_MAX = 70.0

LIMITS = {'min_x': 100, 'max_x': 280, 'min_y': -150, 'max_y': 150, 'min_z': 0, 'max_z': 180}

# ================= [0. Robot Init] =================
def init_serial():
    global ser
    try:
        ser = serial.Serial('/dev/ttyUSB0', 115200, timeout=0.1)
        print("[System] MT4 Robot Connected")
        
        time.sleep(2) 
        ser.write(b"$H\r\n") 
        time.sleep(15) 
        
        ser.write(b"M20\r\n") 
        ser.write(b"G90\r\n") 
        ser.write(b"G1 F2000\r\n") 
        time.sleep(1)

        cmd = f"G0 X200 Y0 Z120 F2000\r\n"
        ser.write(cmd.encode())
        ser.write(b"M3 S40\r\n") 
        print("[System] Ready")
        
    except Exception as e:
        print(f"[System] Connection Failed: {e}")
        ser = None

# ================= [1. Base Class] =================
class BaseNode(ABC):
    def __init__(self, node_id, label):
        self.node_id = node_id
        self.label = label
        self.inputs = {}      
        self.outputs = {}     
        self.output_data = {} 

    @abstractmethod
    def build_ui(self):
        pass

    @abstractmethod
    def execute(self):
        pass

    def fetch_input_data(self, input_attr_id):
        target_link = None
        for link in link_registry.values():
            if link['target'] == input_attr_id:
                target_link = link
                break
        if not target_link: return None 

        source_attr_id = target_link['source']
        source_node_id = dpg.get_item_parent(source_attr_id)
        
        if source_node_id in node_registry:
            source_node = node_registry[source_node_id]
            return source_node.output_data.get(source_attr_id)
        return None

# ================= [2. Nodes] =================

class StartNode(BaseNode):
    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label="START"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out_id:
                dpg.add_text("Flow Out")
            self.outputs[out_id] = "Flow"
    def execute(self): return self.outputs

class UDPReceiverNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "UDP Receiver")
        self.port_input = None
        self.target_ip_input = None
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setblocking(False) 
        self.sock_feedback = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.is_bound = False
        self.data_out_id = None 

    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label=self.label):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as in_id:
                dpg.add_text("Flow In")
            self.inputs[in_id] = "Flow"
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                self.port_input = dpg.add_input_int(label="Port", width=100, default_value=6000)
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                self.target_ip_input = dpg.add_input_text(label="Target IP", width=120, default_value="192.168.50.63")
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as d_out:
                dpg.add_text("JSON Out")
            self.outputs[d_out] = "Data"
            self.data_out_id = d_out 
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as f_out:
                dpg.add_text("Flow Out")
            self.outputs[f_out] = "Flow"

    def execute(self):
        global UNITY_IP
        port = dpg.get_value(self.port_input)
        target_ip = dpg.get_value(self.target_ip_input)
        UNITY_IP = target_ip

        if not self.is_bound:
            try:
                self.sock.bind(('0.0.0.0', port))
                self.is_bound = True
            except: self.is_bound = True

        latest_data = None
        try:
            while True:
                data, addr = self.sock.recvfrom(4096)
                latest_data = data
        except: pass

        if latest_data:
            self.output_data[self.data_out_id] = latest_data.decode()

        try:
            u_x = -current_pos['y'] / 1000.0
            u_y = current_pos['z'] / 1000.0
            u_z = current_pos['x'] / 1000.0
            feedback_data = {
                "x": u_x, "y": u_y, "z": u_z, 
                "gripper": current_pos['gripper'], "status": "Running"
            }
            self.sock_feedback.sendto(json.dumps(feedback_data).encode(), (UNITY_IP, FEEDBACK_PORT))
        except: pass
        return self.outputs

class UnityControlNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "Unity Logic")
        self.data_in_id = None
        self.out_x = None; self.out_y = None; self.out_z = None; self.out_g = None

    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label=self.label):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as in_flow: dpg.add_text("Flow In")
            self.inputs[in_flow] = "Flow"
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as d_in: dpg.add_text("JSON Packet")
            self.inputs[d_in] = "Data"; self.data_in_id = d_in
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out_x: dpg.add_text("Target X")
            self.outputs[out_x] = "Data"; self.out_x = out_x
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out_y: dpg.add_text("Target Y")
            self.outputs[out_y] = "Data"; self.out_y = out_y
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out_z: dpg.add_text("Target Z")
            self.outputs[out_z] = "Data"; self.out_z = out_z
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out_g: dpg.add_text("Target Grip")
            self.outputs[out_g] = "Data"; self.out_g = out_g
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as f_out: dpg.add_text("Flow Out")
            self.outputs[f_out] = "Flow"

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
        self.field_x = None; self.field_y = None; self.field_z = None; self.field_g = None
        self.last_cmd = ""

    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label=self.label):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as in_flow: dpg.add_text("Flow In")
            self.inputs[in_flow] = "Flow"
            
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as x_in:
                self.field_x = dpg.add_input_float(label="X", width=80, default_value=200.0)
            self.inputs[x_in] = "Data"; self.in_x = x_in
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as y_in:
                self.field_y = dpg.add_input_float(label="Y", width=80, default_value=0.0)
            self.inputs[y_in] = "Data"; self.in_y = y_in
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as z_in:
                self.field_z = dpg.add_input_float(label="Z", width=80, default_value=120.0)
            self.inputs[z_in] = "Data"; self.in_z = z_in
            
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as g_in:
                 self.field_g = dpg.add_input_float(label="Grip", width=80, default_value=40.0)
            self.inputs[g_in] = "Data"; self.in_g = g_in

            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as f_out: dpg.add_text("Flow Out")
            self.outputs[f_out] = "Flow"

    def execute(self):
        global current_pos, target_goal
        
        tx = self.fetch_input_data(self.in_x)
        ty = self.fetch_input_data(self.in_y)
        tz = self.fetch_input_data(self.in_z)
        tg = self.fetch_input_data(self.in_g)

        if tx is not None: target_goal['x'] = float(tx)
        if ty is not None: target_goal['y'] = float(ty)
        if tz is not None: target_goal['z'] = float(tz)
        
        # ★ [핵심 로직 변경] 들어온 값이 40이냐 60이냐에 따라 방향 결정
        if tg is not None: target_goal['gripper'] = float(tg)
        
        # 1. XYZ Smoothing (기존 유지)
        dx = target_goal['x'] - current_pos['x']
        dy = target_goal['y'] - current_pos['y']
        dz = target_goal['z'] - current_pos['z']
        
        if abs(dx)<0.5 and abs(dy)<0.5 and abs(dz)<0.5:
            next_x, next_y, next_z = target_goal['x'], target_goal['y'], target_goal['z']
        else:
            next_x = current_pos['x'] + dx * SMOOTHING_FACTOR
            next_y = current_pos['y'] + dy * SMOOTHING_FACTOR
            next_z = current_pos['z'] + dz * SMOOTHING_FACTOR
        
        # Limit Check
        if not (LIMITS['min_x'] <= next_x <= LIMITS['max_x']): next_x = current_pos['x']
        if not (LIMITS['min_y'] <= next_y <= LIMITS['max_y']): next_y = current_pos['y']
        if not (LIMITS['min_z'] <= next_z <= LIMITS['max_z']): next_z = current_pos['z']

        # 2. ★ Gripper Directional Control (방향 제어)
        # Unity가 60(J)을 보내면 -> 닫는 방향(+)으로 조금 이동
        # Unity가 40(U)을 보내면 -> 여는 방향(-)으로 조금 이동
        # Unity가 None을 보내면 -> 현상 유지
        
        received_g = target_goal['gripper']
        
        if received_g is None:
            # 신호 없으면 멈춤
            next_g = current_pos['gripper']
        elif received_g > 50: 
            # 50보다 큰 값(예: 60)이 오면 "닫아라" 명령으로 해석
            next_g = current_pos['gripper'] + GRIPPER_SPEED
        elif received_g < 50:
            # 50보다 작은 값(예: 40)이 오면 "열어라" 명령으로 해석
            next_g = current_pos['gripper'] - GRIPPER_SPEED
        else:
            # 애매한 값은 멈춤
            next_g = current_pos['gripper']

        # 하드웨어 안전 범위 제한 (30 ~ 70)
        if next_g < GRIPPER_MIN: next_g = GRIPPER_MIN
        if next_g > GRIPPER_MAX: next_g = GRIPPER_MAX

        # 상태 업데이트
        current_pos['x'] = next_x
        current_pos['y'] = next_y
        current_pos['z'] = next_z
        current_pos['gripper'] = next_g 

        dpg.set_value(self.field_x, float(next_x))
        dpg.set_value(self.field_y, float(next_y))
        dpg.set_value(self.field_z, float(next_z))
        dpg.set_value(self.field_g, float(next_g))

        # 3. 명령 전송
        cmd_move = f"G0 X{next_x:.1f} Y{next_y:.1f} Z{next_z:.1f}\n"
        cmd_grip = f"M3 S{int(next_g)}\n"

        full_cmd = cmd_move + cmd_grip
        if full_cmd != self.last_cmd:
            if ser and ser.is_open:
                ser.write(cmd_move.encode())
                ser.write(cmd_grip.encode())
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
        self.input_field = None; self.data_in_id = None 

    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label=self.label):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as in_id: dpg.add_text("Flow In"); self.inputs[in_id] = "Flow"
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as d_in: dpg.add_text("Data In"); self.inputs[d_in] = "Data"; self.data_in_id = d_in
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static): self.input_field = dpg.add_input_text(label="Msg", width=120)
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out_id: dpg.add_text("Flow Out"); self.outputs[out_id] = "Flow"

    def execute(self):
        log_msg = f"Grip: {current_pos['gripper']:.1f}"
        print(f"[Status] {log_msg}") 
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
        if node: node.build_ui(); node_registry[node_id] = node; return node
        return None

def run_loop():
    global is_running
    start_node = None
    for node in node_registry.values():
        if isinstance(node, StartNode): start_node = node; break
    if not start_node: is_running = False; return

    while is_running:
        current_node = start_node
        while current_node:
            outputs = current_node.execute()
            next_node = None
            for out_attr_id, out_type in outputs.items():
                if out_type == "Flow":
                    for link in link_registry.values():
                        if link['source'] == out_attr_id:
                            target_node_id = dpg.get_item_parent(link['target'])
                            if target_node_id in node_registry:
                                next_node = node_registry[target_node_id]; break
                if next_node: break 
            current_node = next_node
        time.sleep(0.01)

def toggle_execution(sender, app_data):
    global is_running, run_thread
    if not is_running:
        is_running = True
        dpg.set_item_label("btn_run", "STOP")
        run_thread = threading.Thread(target=run_loop, daemon=True)
        run_thread.start()
    else:
        is_running = False
        dpg.set_item_label("btn_run", "RUN")

def delete_selection(sender, app_data):
    selected_links = dpg.get_selected_links("node_editor")
    for link_id in selected_links: dpg.delete_item(link_id); del link_registry[link_id] if link_id in link_registry else None
    selected_nodes = dpg.get_selected_nodes("node_editor")
    for node_id in selected_nodes: dpg.delete_item(node_id); del node_registry[node_id] if node_id in node_registry else None

def link_cb(sender, app_data):
    src, dst = app_data[0], app_data[1] if len(app_data) == 2 else (app_data[1], app_data[2])
    link_id = dpg.add_node_link(src, dst, parent=sender)
    link_registry[link_id] = {'source': src, 'target': dst}

def del_link_cb(sender, app_data):
    dpg.delete_item(app_data)
    if app_data in link_registry: del link_registry[app_data]

def add_node_cb(sender, app_data, user_data):
    NodeFactory.create_node(user_data)

init_serial()
dpg.create_context()
with dpg.handler_registry(): dpg.add_key_press_handler(dpg.mvKey_Delete, callback=delete_selection)
with dpg.window(label="Visual Scripting V10 (Updated)", width=1000, height=700):
    with dpg.group(horizontal=True):
        dpg.add_button(label="START", callback=add_node_cb, user_data="START")
        dpg.add_button(label="UDP", callback=add_node_cb, user_data="UDP_RECV")
        dpg.add_button(label="UNITY", callback=add_node_cb, user_data="UNITY_CONTROL")
        dpg.add_button(label="ROBOT", callback=add_node_cb, user_data="ROBOT_CONTROL")
        dpg.add_button(label="STATUS", callback=add_node_cb, user_data="PRINT")
        dpg.add_spacer(width=20)
        dpg.add_button(label="JSON", callback=add_node_cb, user_data="JSON_PARSE")
        dpg.add_spacer(width=50)
        dpg.add_button(label="RUN", tag="btn_run", callback=toggle_execution, width=150)
    dpg.add_separator()
    with dpg.node_editor(tag="node_editor", callback=link_cb, delink_callback=del_link_cb): pass

dpg.create_viewport(title='PyGui V10 (Directional)', width=1000, height=700, vsync=False)
dpg.setup_dearpygui()
dpg.show_viewport()
dpg.start_dearpygui()
dpg.destroy_context()