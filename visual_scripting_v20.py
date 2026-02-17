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

dashboard_state = {"status": "Idle", "hw_link": "Offline", "latency": 0.0, "last_pkt_time": 0.0}
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

def get_save_files(): return [f for f in os.listdir(SAVE_DIR) if f.endswith(".json")] if os.path.exists(SAVE_DIR) else []

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

def move_to_coord_callback():
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
    
    # ★ [변경] execute는 이제 '다음에 실행할 Flow Output ID'를 반환해야 함
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

# ================= [Logic Nodes (New)] =================

# 1. Action Node (통합 명령 노드)
class CommandActionNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "Robot Action", "CMD_ACTION")
        self.combo_id = None; self.in_val1 = None; self.in_val2 = None; self.in_val3 = None
        self.out_flow = None
        self.field_v1 = None; self.field_v2 = None; self.field_v3 = None

    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label="Command Action"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as flow: 
                dpg.add_text("Flow In"); self.inputs[flow] = "Flow"
            
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                self.combo_id = dpg.add_combo(
                    items=["Move Relative (XYZ)", "Move Absolute (XYZ)", "Set Gripper", "Homing"], 
                    default_value="Move Relative (XYZ)", width=150
                )
            
            # Inputs (Context sensitive roughly)
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as v1:
                dpg.add_text("X / Grip"); self.field_v1 = dpg.add_input_float(width=60, default_value=0)
                self.inputs[v1] = "Data"; self.in_val1 = v1
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as v2:
                dpg.add_text("Y"); self.field_v2 = dpg.add_input_float(width=60, default_value=0)
                self.inputs[v2] = "Data"; self.in_val2 = v2
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as v3:
                dpg.add_text("Z"); self.field_v3 = dpg.add_input_float(width=60, default_value=0)
                self.inputs[v3] = "Data"; self.in_val3 = v3
            
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out:
                dpg.add_text("Flow Out"); self.outputs[out] = "Flow"; self.out_flow = out

    def execute(self):
        global manual_override_until
        manual_override_until = time.time() + 1.0 # 명령 실행 시 자동 제어권 확보

        mode = dpg.get_value(self.combo_id)
        
        # 입력값 가져오기 (연결된 게 있으면 우선, 없으면 UI 값)
        v1 = self.fetch_input_data(self.in_val1); v1 = float(v1) if v1 is not None else dpg.get_value(self.field_v1)
        v2 = self.fetch_input_data(self.in_val2); v2 = float(v2) if v2 is not None else dpg.get_value(self.field_v2)
        v3 = self.fetch_input_data(self.in_val3); v3 = float(v3) if v3 is not None else dpg.get_value(self.field_v3)

        if mode == "Move Relative (XYZ)":
            target_goal['x'] += v1; target_goal['y'] += v2; target_goal['z'] += v3
            apply_limits_and_move()
            write_log(f"CMD: Rel Move {v1}, {v2}, {v3}")

        elif mode == "Move Absolute (XYZ)":
            target_goal['x'] = v1; target_goal['y'] = v2; target_goal['z'] = v3
            apply_limits_and_move()
            write_log(f"CMD: Abs Move {v1}, {v2}, {v3}")

        elif mode == "Set Gripper":
            target_goal['gripper'] = v1
            apply_limits_and_move()
            write_log(f"CMD: Gripper {v1}")

        elif mode == "Homing":
            threading.Thread(target=homing_thread_func, daemon=True).start()

        return self.out_flow # 다음 노드로 이동

    def get_settings(self): return {"mode": dpg.get_value(self.combo_id), "v1": dpg.get_value(self.field_v1), "v2": dpg.get_value(self.field_v2), "v3": dpg.get_value(self.field_v3)}
    def load_settings(self, data):
        dpg.set_value(self.combo_id, data.get("mode", "Move Relative (XYZ)"))
        dpg.set_value(self.field_v1, data.get("v1", 0)); dpg.set_value(self.field_v2, data.get("v2", 0)); dpg.set_value(self.field_v3, data.get("v3", 0))

# 2. Logic IF Node (조건 분기)
class LogicIfNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "Logic: IF", "LOGIC_IF")
        self.in_cond = None; self.out_true = None; self.out_false = None

    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label="IF Condition"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as flow: dpg.add_text("Flow In"); self.inputs[flow] = "Flow"
            
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as cond:
                dpg.add_text("Condition (Bool)", color=(255, 100, 100))
                self.inputs[cond] = "Data"; self.in_cond = cond
            
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as true_out:
                dpg.add_text("True Flow", color=(100, 255, 100))
                self.outputs[true_out] = "Flow"; self.out_true = true_out
            
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as false_out:
                dpg.add_text("False Flow", color=(255, 100, 100))
                self.outputs[false_out] = "Flow"; self.out_false = false_out

    def execute(self):
        cond = self.fetch_input_data(self.in_cond)
        if cond: return self.out_true
        else: return self.out_false

# 3. Logic LOOP Node (반복문)
class LogicLoopNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "Logic: LOOP", "LOGIC_LOOP")
        self.field_count = None; self.out_loop = None; self.out_finish = None
        self.current_iter = 0; self.target_iter = 0
        self.is_active = False

    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label="For Loop"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as flow: dpg.add_text("Flow In"); self.inputs[flow] = "Flow"
            
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                dpg.add_text("Count:")
                self.field_count = dpg.add_input_int(width=80, default_value=3, min_value=1)

            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as loop_out:
                dpg.add_text("Loop Body", color=(100, 200, 255))
                self.outputs[loop_out] = "Flow"; self.out_loop = loop_out
            
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as finish_out:
                dpg.add_text("Finished", color=(200, 200, 200))
                self.outputs[finish_out] = "Flow"; self.out_finish = finish_out

    def execute(self):
        # 처음 진입 시 초기화
        if not self.is_active:
            self.target_iter = dpg.get_value(self.field_count)
            self.current_iter = 0
            self.is_active = True
        
        if self.current_iter < self.target_iter:
            self.current_iter += 1
            # write_log(f"Loop {self.current_iter}/{self.target_iter}")
            return self.out_loop # 반복 흐름으로 보냄
        else:
            self.is_active = False # 루프 종료
            return self.out_finish # 종료 흐름으로 보냄

    def get_settings(self): return {"count": dpg.get_value(self.field_count)}
    def load_settings(self, data): dpg.set_value(self.field_count, data.get("count", 3))

# 4. Condition Compare Node (상태 확인)
class ConditionCompareNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "Check: State", "COND_COMPARE")
        self.combo_target = None; self.combo_op = None; self.in_val = None; self.out_res = None
        self.field_val = None

    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label="Check State"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                self.combo_target = dpg.add_combo(["Robot X", "Robot Y", "Robot Z", "Gripper"], default_value="Robot X", width=100)
                self.combo_op = dpg.add_combo([">", "<", "=="], default_value=">", width=50)
            
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as val:
                dpg.add_text("Value"); self.field_val = dpg.add_input_float(width=60, default_value=0)
                self.inputs[val] = "Data"; self.in_val = val
            
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as res:
                dpg.add_text("Result (Bool)"); self.outputs[res] = "Data"; self.out_res = res

    def execute(self):
        target = dpg.get_value(self.combo_target)
        op = dpg.get_value(self.combo_op)
        
        # 비교할 기준값
        link_val = self.fetch_input_data(self.in_val)
        ref_val = float(link_val) if link_val is not None else dpg.get_value(self.field_val)
        
        # 현재 로봇 값
        curr_val = 0.0
        if target == "Robot X": curr_val = current_pos['x']
        elif target == "Robot Y": curr_val = current_pos['y']
        elif target == "Robot Z": curr_val = current_pos['z']
        elif target == "Gripper": curr_val = current_pos['gripper']

        res = False
        if op == ">": res = curr_val > ref_val
        elif op == "<": res = curr_val < ref_val
        elif op == "==": res = abs(curr_val - ref_val) < 0.1
        
        self.output_data[self.out_res] = res
        return None

    def get_settings(self): return {"target": dpg.get_value(self.combo_target), "op": dpg.get_value(self.combo_op), "val": dpg.get_value(self.field_val)}
    def load_settings(self, data):
        dpg.set_value(self.combo_target, data.get("target", "Robot X"))
        dpg.set_value(self.combo_op, data.get("op", ">"))
        dpg.set_value(self.field_val, data.get("val", 0))

# 5. Condition Key Node (키보드 확인)
class ConditionKeyNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "Check: Key", "COND_KEY")
        self.field_key = None; self.out_res = None
        # DPG Key Mapping (Simple A-Z)
        self.key_map = {"A": dpg.mvKey_A, "B": dpg.mvKey_B, "C": dpg.mvKey_C, "D": dpg.mvKey_D, "S": dpg.mvKey_S, "W": dpg.mvKey_W}

    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label="Key Check"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                dpg.add_text("Key (A-Z):")
                self.field_key = dpg.add_input_text(width=50, default_value="A")
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as res:
                dpg.add_text("Is Pressed?"); self.outputs[res] = "Data"; self.out_res = res

    def execute(self):
        key_char = dpg.get_value(self.field_key).upper()
        res = False
        if key_char in self.key_map:
            if dpg.is_key_down(self.key_map[key_char]): res = True
        elif len(key_char) == 1:
            # Fallback check (might not work for all keys in DPG without mapping)
            pass 
        
        self.output_data[self.out_res] = res
        return None
    
    def get_settings(self): return {"key": dpg.get_value(self.field_key)}
    def load_settings(self, data): dpg.set_value(self.field_key, data.get("key", "A"))

# ================= [Standard Nodes (Updated Execute)] =================
class StartNode(BaseNode):
    def __init__(self, node_id): super().__init__(node_id, "START", "START")
    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label="START"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out: dpg.add_text("Flow Out"); self.outputs[out] = "Flow"; self.out = out
    def execute(self): return self.out # Start는 항상 다음으로

class UDPReceiverNode(BaseNode):
    def __init__(self, node_id): super().__init__(node_id, "UDP Receiver", "UDP_RECV"); self.out_flow = None
    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label="UDP Receiver"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as flow: dpg.add_text("Flow In"); self.inputs[flow] = "Flow"
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out: dpg.add_text("Flow Out"); self.outputs[out] = "Flow"; self.out_flow = out
    def execute(self):
        # UDP Logic (축소됨)
        try: 
            fb = {"x": -current_pos['y']/1000.0, "y": current_pos['z']/1000.0, "z": current_pos['x']/1000.0, "gripper": current_pos['gripper'], "status": "Running"}
            # Send logic here...
        except: pass
        return self.out_flow

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

# ================= [Execution Engine (Advanced)] =================
def execute_graph_once():
    # 1. 시작 노드 찾기
    start_node = None
    for node in node_registry.values():
        if isinstance(node, StartNode): start_node = node; break
    if not start_node: return

    # 2. 데이터 노드 먼저 계산 (상수, 센서 등 Flow가 없는 노드들)
    for node in node_registry.values():
        if not isinstance(node, (StartNode, CommandActionNode, LogicIfNode, LogicLoopNode, UDPReceiverNode, PrintNode)):
            node.execute()

    # 3. Flow 실행 (순차적 + 분기 처리)
    current_node = start_node
    steps = 0
    MAX_STEPS = 100 # 무한 루프 방지용 (한 프레임에 최대 100번 이동)

    while current_node and steps < MAX_STEPS:
        # 노드 실행 후 '다음에 활성화될 Output Flow ID'를 받음
        active_out_id = current_node.execute()
        
        next_node = None
        if active_out_id:
            # 해당 Output에 연결된 링크 찾기
            for link in link_registry.values():
                if link['source'] == active_out_id:
                    # 링크의 목적지 노드 ID 찾기
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
        if node_type == "START": node = StartNode(node_id)
        elif node_type == "CMD_ACTION": node = CommandActionNode(node_id)
        elif node_type == "LOGIC_IF": node = LogicIfNode(node_id)
        elif node_type == "LOGIC_LOOP": node = LogicLoopNode(node_id)
        elif node_type == "COND_COMPARE": node = ConditionCompareNode(node_id)
        elif node_type == "COND_KEY": node = ConditionKeyNode(node_id)
        elif node_type == "UDP_RECV": node = UDPReceiverNode(node_id)
        elif node_type == "CONSTANT": node = ConstantNode(node_id)
        elif node_type == "PRINT": node = PrintNode(node_id)
        
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
    
    # Clear All
    for lid in list(link_registry.keys()): dpg.delete_item(lid)
    for nid in list(node_registry.keys()): dpg.delete_item(nid)
    link_registry.clear(); node_registry.clear()

    try:
        with open(filepath, 'r') as f: data = json.load(f)
        id_map = {}
        for n_data in data["nodes"]:
            node = NodeFactory.create_node(n_data["type"], None) # New ID
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

# ================= [Main Setup] =================
def add_node_cb(s, a, u): NodeFactory.create_node(u)
def toggle_exec(s, a): global is_running; is_running = not is_running; dpg.set_item_label("btn_run", "STOP" if is_running else "RUN")
def save_cb(s, a): save_graph(dpg.get_value("file_name_input"))
def load_cb(s, a): load_graph(dpg.get_value("file_list_combo"))

init_serial()
threading.Thread(target=lambda: (time.sleep(1), update_file_list()), daemon=True).start()

dpg.create_context()
with dpg.handler_registry(): dpg.add_key_press_handler(dpg.mvKey_Delete, callback=delete_selection)

with dpg.window(tag="PrimaryWindow"):
    # Top Panel
    with dpg.group(horizontal=True):
        with dpg.child_window(width=250, height=130, border=True):
            dpg.add_text("System Status", color=(150,150,150)); dpg.add_text("Idle", tag="dash_status", color=(0,255,0))
            dpg.add_spacer(height=5); dpg.add_text(f"My IP: {get_local_ip()}", color=(180,180,180))
            dpg.add_text(f"SSID: {get_wifi_ssid()}", color=(180,180,180))

        with dpg.child_window(width=350, height=130, border=True):
            dpg.add_text("Manual Control", color=(255,200,0))
            with dpg.group(horizontal=True):
                dpg.add_button(label="X+", width=60, callback=manual_control_callback, user_data=('x', 10)); dpg.add_button(label="X-", width=60, callback=manual_control_callback, user_data=('x', -10))
                dpg.add_text("|"); dpg.add_button(label="Y+", width=60, callback=manual_control_callback, user_data=('y', 10)); dpg.add_button(label="Y-", width=60, callback=manual_control_callback, user_data=('y', -10))
            with dpg.group(horizontal=True):
                dpg.add_button(label="Z+", width=60, callback=manual_control_callback, user_data=('z', 10)); dpg.add_button(label="Z-", width=60, callback=manual_control_callback, user_data=('z', -10))
                dpg.add_text("|"); dpg.add_button(label="G+", width=60, callback=manual_control_callback, user_data=('gripper', 5)); dpg.add_button(label="G-", width=60, callback=manual_control_callback, user_data=('gripper', -5))

        with dpg.child_window(width=400, height=130, border=True):
            dpg.add_text("Graph File Manager", color=(0,255,255))
            with dpg.group(horizontal=True):
                dpg.add_text("Save:"); dpg.add_input_text(tag="file_name_input", default_value="logic_test", width=120); dpg.add_button(label="SAVE", callback=save_cb, width=50)
            with dpg.group(horizontal=True):
                dpg.add_text("Load:"); dpg.add_combo(tag="file_list_combo", width=120); dpg.add_button(label="LOAD", callback=load_cb, width=50); dpg.add_button(label="Refresh", callback=update_file_list, width=50)

    dpg.add_separator()
    # Tool Bar
    with dpg.group(horizontal=True):
        dpg.add_button(label="START", callback=add_node_cb, user_data="START")
        dpg.add_spacer(width=10)
        dpg.add_button(label="ACTION", callback=add_node_cb, user_data="CMD_ACTION", width=60) # New
        dpg.add_button(label="IF", callback=add_node_cb, user_data="LOGIC_IF", width=40)       # New
        dpg.add_button(label="LOOP", callback=add_node_cb, user_data="LOGIC_LOOP", width=50)   # New
        dpg.add_spacer(width=10)
        dpg.add_button(label="CHECK STATE", callback=add_node_cb, user_data="COND_COMPARE")    # New
        dpg.add_button(label="CHECK KEY", callback=add_node_cb, user_data="COND_KEY")          # New
        dpg.add_spacer(width=10)
        dpg.add_button(label="CONST", callback=add_node_cb, user_data="CONSTANT")
        dpg.add_button(label="PRINT", callback=add_node_cb, user_data="PRINT")
        dpg.add_spacer(width=50)
        dpg.add_button(label="RUN", tag="btn_run", callback=toggle_exec, width=150)

    with dpg.node_editor(tag="node_editor", callback=link_cb, delink_callback=del_link_cb): pass

dpg.create_viewport(title='PyGui V20 (Logic & Blocks)', width=1024, height=768, vsync=True)
dpg.setup_dearpygui(); dpg.set_primary_window("PrimaryWindow", True); dpg.show_viewport()

last_logic_time = 0
LOGIC_RATE = 0.05 # 20 FPS (로직 처리를 위해 약간 여유를 둠)

while dpg.is_dearpygui_running():
    if is_running and (time.time() - last_logic_time > LOGIC_RATE):
        execute_graph_once()
        last_logic_time = time.time()
    dpg.render_dearpygui_frame()
dpg.destroy_context()