import dearpygui.dearpygui as dpg
import time
import socket
import json
import threading
import subprocess
import os
import math
import sys
from abc import ABC, abstractmethod
from collections import deque
from datetime import datetime

# 빌드된 파이썬 라이브러리(.so 파일)가 있는 절대 경로를 추가합니다.
# (주의: 라즈베리파이 OS 버전에 따라 맨 끝 폴더 이름이 'arm64'가 아니라 'aarch64'일 수도 있습니다. 확인 후 맞춰서 적어주세요!)
sys.path.append('/home/physical/PyGui-Visual-Scripting/unitree_legged_sdk/lib/python/arm64')

try:
    import robot_interface as sdk
    HAS_UNITREE_SDK = True
    print("Unitree SDK Load Success!")
except ImportError as e:
    HAS_UNITREE_SDK = False
    print(f"Warning: SDK Load Failed ({e}). Running in Simulation Mode.")

# ================= [Global Settings] =================
node_registry = {}
link_registry = {}
is_running = False

SAVE_DIR = "Node_File_Go1"
if not os.path.exists(SAVE_DIR): os.makedirs(SAVE_DIR)

# Go1 State (Velocity & Posture)
current_state = {'vx': 0.0, 'vy': 0.0, 'wz': 0.0, 'mode': 1} # mode 1: Stand, mode 2: Walk
target_state = {'vx': 0.0, 'vy': 0.0, 'wz': 0.0, 'mode': 1}
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
UNITY_IP = "192.168.123.161" # 보통 Go1의 High-Level 통신 IP
FEEDBACK_PORT = 5005
LIMITS = {'max_vx': 1.0, 'min_vx': -1.0, 'max_vy': 0.5, 'min_vy': -0.5, 'max_wz': 1.5, 'min_wz': -1.5}

# Unitree Globals
udp = None
cmd = None
state = None

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

# ================= [Hardware Control (Unitree SDK)] =================
def init_go1():
    global udp, cmd, state
    if HAS_UNITREE_SDK:
        try:
            # 1. 구조체 및 클래스 참조
            # 출력된 리스트에 'Go1', 'HighCmd', 'HighState', 'UDP'가 있음을 확인했습니다.
            cmd = sdk.HighCmd()
            state = sdk.HighState()
            
            # 2. UDP 초기화
            # 두 번째 인자인 'level' 값에 보통 0xee (HighLevel)가 들어갑니다.
            # 만약 에러가 난다면 0xee 대신 0 또는 sdk.Go1 등을 시도해볼 수 있습니다.
            high_level_value = 0xee 
            udp = sdk.UDP(high_level_value, 8080, UNITY_IP, 8082)
            
            # 3. 데이터 초기화
            udp.InitCmdData(cmd)
            
            dashboard_state["hw_link"] = "Online"
            write_log("System: Unitree Go1 Connected (High-Level)")
        except Exception as e:
            dashboard_state["hw_link"] = "Error"
            write_log(f"Go1 Init Error: {e}")
    else:
        dashboard_state["hw_link"] = "Simulation"
        write_log("System: Sim Mode (No SDK)")

def go1_comm_thread():
    """Unitree Go1은 지속적으로 명령(cmd)을 쏴주어야 안전모드에 빠지지 않습니다."""
    global udp, cmd, state
    while True:
        if HAS_UNITREE_SDK and udp and cmd:
            # 상태 변수 적용
            cmd.mode = current_state['mode']
            cmd.velocity = [current_state['vx'], current_state['vy']]
            cmd.yawSpeed = current_state['wz']
            cmd.bodyHeight = 0.0 # Default height

            udp.SetSend(cmd)
            udp.Send()
            udp.Recv()
            udp.GetRecv(state)
        time.sleep(0.01) # 100Hz 전송

# ================= [Dashboard Callbacks] =================
def manual_control_callback(sender, app_data, user_data):
    global manual_override_until
    manual_override_until = time.time() + 1.0
    axis, step = user_data
    
    if axis == 'mode':
        target_state['mode'] = step
        target_state['vx'] = target_state['vy'] = target_state['wz'] = 0.0
    else:
        target_state[axis] = current_state[axis] + step
        target_state['mode'] = 2 # 걷기 모드 강제 전환
        
    apply_limits_and_update()

def stop_callback(sender, app_data, user_data):
    global manual_override_until
    manual_override_until = time.time() + 1.0
    target_state['vx'] = target_state['vy'] = target_state['wz'] = 0.0
    target_state['mode'] = 1 # Stand 모드
    apply_limits_and_update()
    write_log("Manual Action: Stop & Stand")

def apply_limits_and_update():
    target_state['vx'] = max(LIMITS['min_vx'], min(target_state['vx'], LIMITS['max_vx']))
    target_state['vy'] = max(LIMITS['min_vy'], min(target_state['vy'], LIMITS['max_vy']))
    target_state['wz'] = max(LIMITS['min_wz'], min(target_state['wz'], LIMITS['max_wz']))
    current_state.update(target_state)

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

# ================= [Logic Nodes for Go1] =================
class CommandActionNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "Go1 Action", "CMD_ACTION")
        self.combo_id = None; self.in_val1 = None; self.in_val2 = None
        self.out_flow = None; self.field_v1 = None; self.field_v2 = None

    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label="Go1 Action"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as flow: dpg.add_text("Flow In"); self.inputs[flow] = "Flow"
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                self.combo_id = dpg.add_combo(
                    items=["Stand (Stop)", "Walk (Fwd/Turn)", "Walk (Strafe)", "Force Lie Down"], 
                    default_value="Stand (Stop)", width=150
                )
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as v1:
                dpg.add_text("Speed/Val1"); self.field_v1 = dpg.add_input_float(width=60, default_value=0); self.inputs[v1] = "Data"; self.in_val1 = v1
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as v2:
                dpg.add_text("Turn/Val2"); self.field_v2 = dpg.add_input_float(width=60, default_value=0); self.inputs[v2] = "Data"; self.in_val2 = v2
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out:
                dpg.add_text("Flow Out"); self.outputs[out] = "Flow"; self.out_flow = out

    def execute(self):
        global manual_override_until; manual_override_until = time.time() + 1.0 
        mode = dpg.get_value(self.combo_id)
        v1 = self.fetch_input_data(self.in_val1); v1 = float(v1) if v1 is not None else dpg.get_value(self.field_v1)
        v2 = self.fetch_input_data(self.in_val2); v2 = float(v2) if v2 is not None else dpg.get_value(self.field_v2)

        if mode == "Stand (Stop)":
            target_state['mode'] = 1; target_state['vx'] = 0; target_state['vy'] = 0; target_state['wz'] = 0
        elif mode == "Walk (Fwd/Turn)":
            target_state['mode'] = 2; target_state['vx'] = v1; target_state['vy'] = 0; target_state['wz'] = v2
        elif mode == "Walk (Strafe)":
            target_state['mode'] = 2; target_state['vx'] = 0; target_state['vy'] = v1; target_state['wz'] = v2
        elif mode == "Force Lie Down":
            target_state['mode'] = 0; target_state['vx'] = 0; target_state['vy'] = 0; target_state['wz'] = 0

        apply_limits_and_update()
        return self.out_flow

    def get_settings(self): return {"mode": dpg.get_value(self.combo_id), "v1": dpg.get_value(self.field_v1), "v2": dpg.get_value(self.field_v2)}
    def load_settings(self, data):
        dpg.set_value(self.combo_id, data.get("mode", "Stand (Stop)"))
        dpg.set_value(self.field_v1, data.get("v1", 0)); dpg.set_value(self.field_v2, data.get("v2", 0))

class KeyboardControlNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "Keyboard (Go1)", "KEYBOARD")
        self.out_vx = None; self.out_vy = None; self.out_wz = None; self.out_mode = None
        self.accel = 0.05; self.cooldown = 0.1; self.last_input_time = 0.0
    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label="Keyboard (Velocity)"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as flow: dpg.add_text("Flow In"); self.inputs[flow] = "Flow"
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static): dpg.add_text("W/S: Forward/Back\nA/D: Strafe\nQ/E: Turn\nSpace: Stop", color=(255,150,150))
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as vx: dpg.add_text("Target Vx"); self.outputs[vx] = "Data"; self.out_vx = vx
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as vy: dpg.add_text("Target Vy"); self.outputs[vy] = "Data"; self.out_vy = vy
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as wz: dpg.add_text("Target Wz"); self.outputs[wz] = "Data"; self.out_wz = wz
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as f: dpg.add_text("Flow Out"); self.outputs[f] = "Flow"
    def execute(self):
        global manual_override_until
        if time.time() - self.last_input_time > self.cooldown:
            dvx, dvy, dwz = 0,0,0
            if dpg.is_key_down(dpg.mvKey_W): dvx=1
            if dpg.is_key_down(dpg.mvKey_S): dvx=-1
            if dpg.is_key_down(dpg.mvKey_A): dvy=1
            if dpg.is_key_down(dpg.mvKey_D): dvy=-1
            if dpg.is_key_down(dpg.mvKey_Q): dwz=1
            if dpg.is_key_down(dpg.mvKey_E): dwz=-1
            
            if dpg.is_key_down(dpg.mvKey_Spacebar):
                target_state['vx'] = 0; target_state['vy'] = 0; target_state['wz'] = 0; target_state['mode'] = 1
                manual_override_until = time.time() + 0.5
            elif dvx or dvy or dwz:
                manual_override_until = time.time() + 0.5; self.last_input_time = time.time()
                target_state['mode'] = 2
                target_state['vx'] += dvx * self.accel
                target_state['vy'] += dvy * self.accel
                target_state['wz'] += dwz * self.accel
                apply_limits_and_update()
            
        self.output_data[self.out_vx]=target_state['vx']; self.output_data[self.out_vy]=target_state['vy']; self.output_data[self.out_wz]=target_state['wz']
        for k, v in self.outputs.items():
            if v == "Flow": return k
        return None

class RobotControlNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "Go1 Driver", "ROBOT_CONTROL")
        self.in_vx=None; self.in_vy=None; self.in_wz=None; self.in_smooth=None
        self.field_vx=None; self.field_vy=None; self.field_wz=None; self.field_smooth=None
        self.cache_ui={'vx':0,'vy':0,'wz':0}
    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label=self.label):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as flow: dpg.add_text("Flow In"); self.inputs[flow]="Flow"
            for axis, label, fattr in [('vx',"Vx (Fwd)",'field_vx'), ('vy',"Vy (Side)",'field_vy'), ('wz',"Wz (Turn)",'field_wz')]:
                with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as aid:
                    with dpg.group(horizontal=True): dpg.add_text(label, color=(255,255,0)); setattr(self, fattr, dpg.add_input_float(width=80, default_value=0.0, step=0))
                    self.inputs[aid]="Data"
                    if axis=='vx':self.in_vx=aid
                    elif axis=='vy':self.in_vy=aid
                    elif axis=='wz':self.in_wz=aid
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static): dpg.add_spacer(height=5) 
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as sin:
                with dpg.group(horizontal=True): dpg.add_text("Damping"); self.field_smooth=dpg.add_input_float(width=60, default_value=0.8, step=0)
                self.inputs[sin]="Data"; self.in_smooth=sin
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as fout: dpg.add_text("Flow Out"); self.outputs[fout]="Flow"
            
    def execute(self):
        global current_state, target_state, manual_override_until
        tvx, tvy, twz = self.fetch_input_data(self.in_vx), self.fetch_input_data(self.in_vy), self.fetch_input_data(self.in_wz)
        ls = self.fetch_input_data(self.in_smooth)
        if ls: dpg.set_value(self.field_smooth, float(ls))
        
        damping = dpg.get_value(self.field_smooth)
        
        # UI나 키보드 제어가 없을 때 노드의 입력을 받음
        if time.time() > manual_override_until:
            if tvx is not None: target_state['vx']=float(tvx)
            if tvy is not None: target_state['vy']=float(tvy)
            if twz is not None: target_state['wz']=float(twz)
            if tvx or tvy or twz: target_state['mode'] = 2
        
        # 스무딩(Damping) 로직: 목표 속도에 부드럽게 도달
        current_state['vx'] = current_state['vx'] * damping + target_state['vx'] * (1 - damping)
        current_state['vy'] = current_state['vy'] * damping + target_state['vy'] * (1 - damping)
        current_state['wz'] = current_state['wz'] * damping + target_state['wz'] * (1 - damping)
        
        # 노이즈 제거
        if abs(current_state['vx']) < 0.01: current_state['vx'] = 0.0
        if abs(current_state['vy']) < 0.01: current_state['vy'] = 0.0
        if abs(current_state['wz']) < 0.01: current_state['wz'] = 0.0
        
        if abs(self.cache_ui['vx']-current_state['vx'])>0.05: dpg.set_value(self.field_vx, current_state['vx']); self.cache_ui['vx']=current_state['vx']
        if abs(self.cache_ui['vy']-current_state['vy'])>0.05: dpg.set_value(self.field_vy, current_state['vy']); self.cache_ui['vy']=current_state['vy']
        if abs(self.cache_ui['wz']-current_state['wz'])>0.05: dpg.set_value(self.field_wz, current_state['wz']); self.cache_ui['wz']=current_state['wz']

        for k, v in self.outputs.items():
            if v == "Flow": return k
        return None
    def get_settings(self): return {"s": dpg.get_value(self.field_smooth)}
    def load_settings(self, data): dpg.set_value(self.field_smooth, data.get("s", 0.8))

class StartNode(BaseNode):
    def __init__(self, node_id): super().__init__(node_id, "START", "START")
    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label="START"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out: dpg.add_text("Flow Out"); self.outputs[out] = "Flow"; self.out = out
    def execute(self): return self.out 

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
        if not isinstance(node, (StartNode, CommandActionNode, PrintNode, KeyboardControlNode, RobotControlNode)):
            try: node.execute()
            except: pass

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
        if node_type == "START": node = StartNode(node_id)
        elif node_type == "CMD_ACTION": node = CommandActionNode(node_id)
        elif node_type == "KEYBOARD": node = KeyboardControlNode(node_id)
        elif node_type == "ROBOT_CONTROL": node = RobotControlNode(node_id)
        elif node_type == "LOGGER": node = LoggerNode(node_id)
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

# ================= [Main Setup] =================
init_go1()
threading.Thread(target=go1_comm_thread, daemon=True).start()
threading.Thread(target=lambda: (time.sleep(1), update_file_list()), daemon=True).start()

dpg.create_context()
with dpg.handler_registry(): dpg.add_key_press_handler(dpg.mvKey_Delete, callback=delete_selection)

my_ip = get_local_ip()

with dpg.window(tag="PrimaryWindow"):
    # [1번 줄] System Status | Manual Control
    with dpg.group(horizontal=True):
        with dpg.child_window(width=250, height=130, border=True):
            dpg.add_text("System Status", color=(150,150,150))
            dpg.add_text(dashboard_state["hw_link"], tag="dash_link", color=(0,255,0) if dashboard_state["hw_link"]=="Online" else (255,200,0))
            dpg.add_spacer(height=5)
            dpg.add_text(f"Mode: {'Walk' if current_state['mode']==2 else 'Stand/Other'}", tag="dash_mode", color=(0,255,255))
            dpg.add_text(f"Vx: {current_state['vx']:.2f} | Vy: {current_state['vy']:.2f}", tag="dash_vel")
            dpg.add_text(f"Wz: {current_state['wz']:.2f}", tag="dash_yaw")

        with dpg.child_window(width=350, height=130, border=True):
            dpg.add_text("Manual Override (Walk)", color=(255,200,0))
            with dpg.group(horizontal=True):
                dpg.add_button(label="Fwd (W)", width=70, callback=manual_control_callback, user_data=('vx', 0.1))
                dpg.add_button(label="Back (S)", width=70, callback=manual_control_callback, user_data=('vx', -0.1))
                dpg.add_button(label="STOP", width=70, callback=stop_callback)
            with dpg.group(horizontal=True):
                dpg.add_button(label="Left (A)", width=70, callback=manual_control_callback, user_data=('vy', 0.1))
                dpg.add_button(label="Right (D)", width=70, callback=manual_control_callback, user_data=('vy', -0.1))
            with dpg.group(horizontal=True):
                dpg.add_button(label="Turn L (Q)", width=70, callback=manual_control_callback, user_data=('wz', 0.2))
                dpg.add_button(label="Turn R (E)", width=70, callback=manual_control_callback, user_data=('wz', -0.2))

        # File Manager
        with dpg.child_window(width=350, height=130, border=True):
            dpg.add_text("Graph File Manager", color=(0,255,255))
            with dpg.group(horizontal=True):
                dpg.add_text("Save As:"); dpg.add_input_text(tag="file_name_input", default_value="go1_test", width=120)
                dpg.add_button(label="SAVE", callback=save_cb, width=60)
            with dpg.group(horizontal=True):
                dpg.add_text("Load File:"); dpg.add_combo(items=get_save_files(), tag="file_list_combo", width=120)
                dpg.add_button(label="LOAD", callback=load_cb, width=60)
                
    dpg.add_separator()
    # Tool Bar
    with dpg.group(horizontal=True):
        dpg.add_button(label="START", callback=add_node_cb, user_data="START")
        dpg.add_spacer(width=10)
        dpg.add_button(label="ACTION (Go1)", callback=add_node_cb, user_data="CMD_ACTION")
        dpg.add_button(label="KEY (Go1)", callback=add_node_cb, user_data="KEYBOARD")
        dpg.add_button(label="DRIVER (Go1)", callback=add_node_cb, user_data="ROBOT_CONTROL")
        dpg.add_spacer(width=10)
        dpg.add_button(label="LOG", callback=add_node_cb, user_data="LOGGER")
        dpg.add_button(label="PRINT", callback=add_node_cb, user_data="PRINT")
        dpg.add_spacer(width=50)
        dpg.add_button(label="RUN SCRIPT", tag="btn_run", callback=toggle_exec, width=150)

    with dpg.node_editor(tag="node_editor", callback=link_cb, delink_callback=del_link_cb): pass

dpg.create_viewport(title='PyGui V1 (Unitree Go1 Edition)', width=1024, height=768, vsync=True)
dpg.setup_dearpygui(); dpg.set_primary_window("PrimaryWindow", True); dpg.show_viewport()

last_logic_time = 0
LOGIC_RATE = 0.02 # 50 FPS GUI update

while dpg.is_dearpygui_running():
    # Update UI Dash
    dpg.set_value("dash_vel", f"Vx: {current_state['vx']:.2f} | Vy: {current_state['vy']:.2f}")
    dpg.set_value("dash_yaw", f"Wz: {current_state['wz']:.2f}")
    dpg.set_value("dash_mode", f"Mode: {'Walk' if current_state['mode']==2 else 'Stand/Other'}")
    
    # Run Script Graph
    if is_running and (time.time() - last_logic_time > LOGIC_RATE):
        execute_graph_once()
        last_logic_time = time.time()
        
    dpg.render_dearpygui_frame()

dpg.destroy_context()