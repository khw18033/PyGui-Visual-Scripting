import sys
import time
import math
import socket
import select
import threading
import json
import os
import subprocess
import dearpygui.dearpygui as dpg
from collections import deque
from abc import ABC, abstractmethod
from datetime import datetime

# ================= [Unitree SDK Import] =================
# 앞서 성공했던 arm64 경로를 사용합니다. (필요 시 수정)
sys.path.append('/home/physical/PyGui-Visual-Scripting/unitree_legged_sdk/lib/python/arm64')

try:
    import robot_interface as sdk
    HAS_UNITREE_SDK = True
except ImportError as e:
    HAS_UNITREE_SDK = False
    print(f"Warning: 'robot_interface' module not found. Running in Simulation Mode. ({e})")

# ================= [Global Settings & V2 Config] =================
node_registry = {}
link_registry = {}
is_running = False

SAVE_DIR = "Node_File_Go1"
if not os.path.exists(SAVE_DIR): os.makedirs(SAVE_DIR)

# Dashboard State
dashboard_state = {
    "status": "Idle",
    "hw_link": "Offline",
    "unity_link": "Waiting"
}

system_log_buffer = deque(maxlen=50)

# ----------------- [V2 하드웨어 및 Unity 설정] -----------------
# 기존 Go1 코드.txt의 네트워크 설정을 그대로 가져옵니다.
HIGHLEVEL = 0xee
LOCAL_PORT = 8090
ROBOT_IP   = "192.168.50.159"
ROBOT_PORT = 8082

UNITY_IP = "192.168.50.246"
UNITY_STATE_PORT = 15101
UNITY_CMD_PORT   = 15102
UNITY_RX_PORT    = 15100

# ----------------- [V2 튜닝 및 상태 변수] -----------------
dt = 0.002 # 500Hz

V_MAX, S_MAX, W_MAX = 0.4, 0.4, 2.0
VX_CMD, VY_CMD, WZ_CMD = 0.20, 0.20, 1.00

hold_timeout_sec = 0.1
repeat_grace_sec = 0.4
min_move_sec     = 0.4
stop_brake_sec   = 0.0

# GUI 노드가 백그라운드 스레드에 전달할 의도(Intent)
node_intent = {
    'vx': 0.0, 'vy': 0.0, 'wz': 0.0,
    'yaw_align': False, 'reset_yaw': False, 'stop': False,
    'use_unity_cmd': True, 'trigger_time': time.monotonic()
}

# 로봇 추측 항법(Dead-reckoning) 및 상태 변수
go1_state = {
    'world_x': 0.0, 'world_z': 0.0, 'yaw_unity': 0.0,
    'vx_cmd': 0.0, 'vy_cmd': 0.0, 'wz_cmd': 0.0,
    'mode': 1, 'reason': "NONE"
}

# ================= [Helper Functions] =================
def write_log(msg):
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {msg}")
    system_log_buffer.append(f"[{timestamp}] {msg}")

def clamp(x, lo, hi):
    return lo if x < lo else hi if x > hi else x

def wrap_pi(a):
    while a > math.pi: a -= 2.0 * math.pi
    while a < -math.pi: a += 2.0 * math.pi
    return a

def get_save_files():
    if not os.path.exists(SAVE_DIR): return []
    return [f for f in os.listdir(SAVE_DIR) if f.endswith(".json")]

# ================= [V2 Background Comm Thread] =================
def go1_v2_comm_thread():
    global go1_state
    
    # Unitree UDP 세팅
    if HAS_UNITREE_SDK:
        udp = sdk.UDP(HIGHLEVEL, LOCAL_PORT, ROBOT_IP, ROBOT_PORT)
        cmd = sdk.HighCmd()
        state = sdk.HighState()
        udp.InitCmdData(cmd)
        dashboard_state["hw_link"] = "Online"
    else:
        udp = cmd = state = None
        dashboard_state["hw_link"] = "Simulation"
        
    # Unity UDP 세팅
    sock_tx_state = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock_tx_cmd   = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock_rx_unity = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock_rx_unity.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock_rx_unity.bind(("0.0.0.0", UNITY_RX_PORT))
        sock_rx_unity.setblocking(False)
    except: pass
    
    unity_state_addr = (UNITY_IP, UNITY_STATE_PORT)
    unity_cmd_addr   = (UNITY_IP, UNITY_CMD_PORT)

    # 런타임 변수
    stand_only = True
    now = time.monotonic()
    last_key_time = last_move_cmd_time = grace_deadline = last_event_time = now
    use_grace = True
    recent_event_count = 0
    last_unity_cmd_time = now
    unity_timeout_sec = 0.15
    
    yaw0_initialized = False; yaw0 = 0.0
    UNITY_YAW_OFFSET_RAD = math.pi / 2.0
    world_x = world_z = 0.0
    last_dr_time = now; seq = 0
    
    yaw_align_active = False
    yaw_align_target_rel = 0.0
    yaw_align_kp = 2.0
    yaw_align_tol_rad = 2.0 * math.pi / 180.0

    def reset_cmd_base():
        if not cmd: return
        cmd.mode = 0; cmd.gaitType = 0; cmd.speedLevel = 0
        cmd.footRaiseHeight = 0.08; cmd.bodyHeight = 0.0
        cmd.euler = [0.0, 0.0, 0.0]; cmd.velocity = [0.0, 0.0]
        cmd.yawSpeed = 0.0; cmd.reserve = 0

    next_t = time.monotonic()
    
    while True: # 500Hz Loop
        tnow = time.monotonic()
        if tnow < next_t:
            time.sleep(max(0.0, next_t - tnow))
        next_t += dt

        # ---- recv robot ----
        raw_yaw = 0.0
        if udp:
            udp.Recv()
            udp.GetRecv(state)
            raw_yaw = float(state.imu.rpy[2])

        if not yaw0_initialized:
            yaw0 = raw_yaw; yaw0_initialized = True
            last_dr_time = time.monotonic()
            
        if node_intent['reset_yaw']:
            yaw0 = raw_yaw; last_dr_time = time.monotonic()
            node_intent['reset_yaw'] = False
            write_log(f"YAW0 Reset: {yaw0:.3f}")

        yaw_rel = wrap_pi(raw_yaw - yaw0)
        yaw_unity = wrap_pi(yaw_rel + UNITY_YAW_OFFSET_RAD)
        go1_state['yaw_unity'] = yaw_unity

        # ---- GUI Node Intent 처리 (가상 키보드) ----
        is_node_active = (tnow - node_intent['trigger_time']) < 0.1
        
        if node_intent['yaw_align']:
            yaw_align_active = True
            stand_only = False
            last_key_time = last_move_cmd_time = grace_deadline = tnow
            use_grace = True
            node_intent['yaw_align'] = False
            
        if node_intent['stop']:
            yaw_align_active = False
            stand_only = True
            last_key_time = last_move_cmd_time = grace_deadline = tnow
            use_grace = True
            node_intent['stop'] = False
            
        elif is_node_active:
            yaw_align_active = False
            stand_only = False
            last_key_time = tnow
            grace_deadline = tnow + repeat_grace_sec
            if abs(node_intent['vx']) > 0 or abs(node_intent['vy']) > 0 or abs(node_intent['wz']) > 0:
                last_move_cmd_time = tnow

        # ---- Unity teleop ----
        got = None
        try:
            data, _ = sock_rx_unity.recvfrom(256)
            s = data.decode("utf-8", errors="ignore").strip().split()
            if len(s) >= 4: got = (float(s[0]), float(s[1]), float(s[2]), int(s[3]))
        except: pass
        
        uvx = uvy = uwz = uestop = 0
        if got:
            uvx, uvy, uwz, uestop = got
            last_unity_cmd_time = tnow
            dashboard_state['unity_link'] = "Active"
            
        unity_active = node_intent['use_unity_cmd'] and ((tnow - last_unity_cmd_time) <= unity_timeout_sec)
        if not unity_active: dashboard_state['unity_link'] = "Waiting"

        # ---- hold logic ----
        since_key = tnow - last_key_time
        since_move = tnow - last_move_cmd_time
        hold_by_key_repeat = (not stand_only) and (since_key <= hold_timeout_sec)
        hold_by_grace = (not stand_only) and use_grace and (tnow <= grace_deadline)
        hold_by_min_move = (not stand_only) and (since_move <= min_move_sec)
        active_walk = hold_by_key_repeat or hold_by_grace or hold_by_min_move

        reset_cmd_base()
        target_mode = 1; out_vx = out_vy = out_wz = 0.0

        if yaw_align_active:
            err = wrap_pi(yaw_rel - yaw_align_target_rel)
            if abs(err) <= yaw_align_tol_rad:
                yaw_align_active = False
                target_mode = 1
            else:
                target_mode = 2; cmd.gaitType = 1 if cmd else 1
                out_wz = clamp(-yaw_align_kp * err, -W_MAX, W_MAX)
        elif unity_active:
            target_mode = 1 if uestop else 2
            if cmd: cmd.gaitType = 1
            out_vx = clamp(uvx, -V_MAX, V_MAX)
            out_vy = clamp(uvy, -S_MAX, S_MAX)
            out_wz = clamp(uwz, -W_MAX, W_MAX)
            go1_state['reason'] = "UNITY"
        elif active_walk:
            target_mode = 2
            if cmd: cmd.gaitType = 1
            out_vx = clamp(node_intent['vx'], -V_MAX, V_MAX)
            out_vy = clamp(node_intent['vy'], -S_MAX, S_MAX)
            out_wz = clamp(node_intent['wz'], -W_MAX, W_MAX)
            go1_state['reason'] = "NODE_WALK"
        else:
            if since_move <= (min_move_sec + stop_brake_sec):
                target_mode = 2
                if cmd: cmd.gaitType = 1
                go1_state['reason'] = "BRAKE"
            else:
                target_mode = 1
                use_grace = True
                go1_state['reason'] = "STAND"

        if cmd:
            cmd.mode = target_mode
            cmd.velocity = [out_vx, out_vy]
            cmd.yawSpeed = out_wz
            udp.SetSend(cmd)
            udp.Send()
            
        # 상태 업데이트 (UI용)
        go1_state['vx_cmd'], go1_state['vy_cmd'], go1_state['wz_cmd'] = out_vx, out_vy, out_wz
        go1_state['mode'] = target_mode

        # ---- send unity ----
        dts = tnow - last_dr_time
        last_dr_time = tnow
        cy = math.cos(yaw_unity); sy = math.sin(yaw_unity)
        world_x += (out_vx * cy - out_vy * sy) * dts
        world_z += (out_vx * sy + out_vy * cy) * dts
        go1_state['world_x'] = world_x; go1_state['world_z'] = world_z

        estop = 1 if target_mode == 1 else 0
        seq += 1
        msg_state = f"{seq} {time.time()*1000.0:.1f} {world_x:.6f} {world_z:.6f} {yaw_unity:.6f} {out_vx:.3f} {out_vy:.3f} {out_wz:.3f} {estop} {target_mode}"
        msg_cmd = f"{out_vx:.3f} {out_vy:.3f} {out_wz:.3f} {estop}"
        try:
            sock_tx_state.sendto(msg_state.encode("utf-8"), unity_state_addr)
            sock_tx_cmd.sendto(msg_cmd.encode("utf-8"), unity_cmd_addr)
        except: pass

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

# ================= [V2 Logic Nodes] =================
class KeyboardControlNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "Keyboard (V2)", "KEYBOARD")
        self.out_vx = None; self.out_vy = None; self.out_wz = None
    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label="Keyboard (Intent)"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as flow: dpg.add_text("Flow In"); self.inputs[flow] = "Flow"
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static): dpg.add_text("W/S: Fwd/Back\nA/D: Strafe\nQ/E: Turn\nSpace: Stop\nR: Yaw Align", color=(255,150,150))
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as vx: dpg.add_text("Target Vx"); self.outputs[vx] = "Data"; self.out_vx = vx
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as vy: dpg.add_text("Target Vy"); self.outputs[vy] = "Data"; self.out_vy = vy
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as wz: dpg.add_text("Target Wz"); self.outputs[wz] = "Data"; self.out_wz = wz
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as f: dpg.add_text("Flow Out"); self.outputs[f] = "Flow"
    def execute(self):
        global node_intent
        vx, vy, wz = 0.0, 0.0, 0.0
        if dpg.is_key_down(dpg.mvKey_W): vx = VX_CMD
        if dpg.is_key_down(dpg.mvKey_S): vx = -VX_CMD
        if dpg.is_key_down(dpg.mvKey_A): vy = VY_CMD
        if dpg.is_key_down(dpg.mvKey_D): vy = -VY_CMD
        if dpg.is_key_down(dpg.mvKey_Q): wz = WZ_CMD
        if dpg.is_key_down(dpg.mvKey_E): wz = -WZ_CMD
        
        if dpg.is_key_down(dpg.mvKey_Spacebar): node_intent['stop'] = True
        if dpg.is_key_pressed(dpg.mvKey_R): node_intent['yaw_align'] = True
        if dpg.is_key_pressed(dpg.mvKey_Z): node_intent['reset_yaw'] = True

        if vx or vy or wz:
            node_intent['vx'] = vx; node_intent['vy'] = vy; node_intent['wz'] = wz
            node_intent['trigger_time'] = time.monotonic()
            
        self.output_data[self.out_vx]=vx; self.output_data[self.out_vy]=vy; self.output_data[self.out_wz]=wz
        for k, v in self.outputs.items():
            if v == "Flow": return k
        return None

class RobotControlNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "Go1 Driver (V2)", "ROBOT_CONTROL")
        self.in_vx=None; self.in_vy=None; self.in_wz=None
    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label=self.label):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as flow: dpg.add_text("Flow In"); self.inputs[flow]="Flow"
            for axis, label in [('vx',"Vx In"), ('vy',"Vy In"), ('wz',"Wz In")]:
                with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as aid:
                    dpg.add_text(label, color=(255,255,0)); self.inputs[aid]="Data"
                    if axis=='vx':self.in_vx=aid
                    elif axis=='vy':self.in_vy=aid
                    elif axis=='wz':self.in_wz=aid
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static): dpg.add_text("Internal Damping Active", color=(100,255,100))
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as fout: dpg.add_text("Flow Out"); self.outputs[fout]="Flow"
            
    def execute(self):
        global node_intent
        tvx, tvy, twz = self.fetch_input_data(self.in_vx), self.fetch_input_data(self.in_vy), self.fetch_input_data(self.in_wz)
        if tvx is not None or tvy is not None or twz is not None:
            node_intent['vx'] = float(tvx or 0)
            node_intent['vy'] = float(tvy or 0)
            node_intent['wz'] = float(twz or 0)
            node_intent['trigger_time'] = time.monotonic()
            
        for k, v in self.outputs.items():
            if v == "Flow": return k
        return None

class StartNode(BaseNode):
    def __init__(self, node_id): super().__init__(node_id, "START", "START")
    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label="START"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out: dpg.add_text("Flow Out"); self.outputs[out] = "Flow"; self.out = out
    def execute(self): return self.out 

# ================= [Execution & Factory] =================
def execute_graph_once():
    start_node = None
    for node in node_registry.values():
        if isinstance(node, StartNode): start_node = node; break
    if not start_node: return

    for node in node_registry.values():
        if not isinstance(node, (StartNode, KeyboardControlNode, RobotControlNode)):
            try: node.execute()
            except: pass

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

class NodeFactory:
    @staticmethod
    def create_node(node_type, node_id=None):
        if node_id is None: node_id = dpg.generate_uuid()
        node = None
        if node_type == "START": node = StartNode(node_id)
        elif node_type == "KEYBOARD": node = KeyboardControlNode(node_id)
        elif node_type == "ROBOT_CONTROL": node = RobotControlNode(node_id)
        if node: node.build_ui(); node_registry[node_id] = node; return node
        return None

def toggle_exec(s, a): global is_running; is_running = not is_running; dpg.set_item_label("btn_run", "STOP" if is_running else "RUN")
def link_cb(s, a): src, dst = a[0], a[1] if len(a)==2 else a[1]; lid = dpg.add_node_link(src, dst, parent=s); link_registry[lid] = {'source': src, 'target': dst}
def del_link_cb(s, a): dpg.delete_item(a); link_registry.pop(a, None)
def add_node_cb(s, a, u): NodeFactory.create_node(u)

# ================= [Main Setup] =================
threading.Thread(target=go1_v2_comm_thread, daemon=True).start()

dpg.create_context()
with dpg.window(tag="PrimaryWindow"):
    with dpg.group(horizontal=True):
        with dpg.child_window(width=250, height=130, border=True):
            dpg.add_text("System Status (V2)", color=(150,150,150))
            dpg.add_text(f"HW: {dashboard_state['hw_link']}", tag="dash_link", color=(0,255,0))
            dpg.add_text(f"Unity: Waiting", tag="dash_unity", color=(255,255,0))
            dpg.add_spacer(height=5)
            dpg.add_text("Reason: NONE", tag="dash_reason")
            
        with dpg.child_window(width=300, height=130, border=True):
            dpg.add_text("Odom / DR", color=(0,255,255))
            dpg.add_text("World X: 0.000", tag="dash_wx")
            dpg.add_text("World Z: 0.000", tag="dash_wz")
            dpg.add_text("Yaw (Unity): 0.000", tag="dash_yaw")

        with dpg.child_window(width=250, height=130, border=True):
            dpg.add_text("Commands", color=(255,200,0))
            dpg.add_text("Vx Cmd: 0.00", tag="dash_vx_2")
            dpg.add_text("Vy Cmd: 0.00", tag="dash_vy_2")
            dpg.add_text("Wz Cmd: 0.00", tag="dash_wz_2")

    dpg.add_separator()
    with dpg.group(horizontal=True):
        dpg.add_button(label="START", callback=add_node_cb, user_data="START")
        dpg.add_button(label="KEY (Go1)", callback=add_node_cb, user_data="KEYBOARD")
        dpg.add_button(label="DRIVER (Go1)", callback=add_node_cb, user_data="ROBOT_CONTROL")
        dpg.add_spacer(width=50)
        dpg.add_button(label="RUN SCRIPT", tag="btn_run", callback=toggle_exec, width=150)

    with dpg.node_editor(tag="node_editor", callback=link_cb, delink_callback=del_link_cb): pass

dpg.create_viewport(title='PyGui Visual Scripting v2 (Go1 Compatible)', width=1024, height=768, vsync=True)
dpg.setup_dearpygui(); dpg.set_primary_window("PrimaryWindow", True); dpg.show_viewport()

last_logic_time = 0; LOGIC_RATE = 0.02

while dpg.is_dearpygui_running():
    # Update UI Dash
    dpg.set_value("dash_unity", f"Unity: {dashboard_state['unity_link']}")
    dpg.set_value("dash_reason", f"Mode: {go1_state['mode']} | {go1_state['reason']}")
    dpg.set_value("dash_wx", f"World X: {go1_state['world_x']:.3f}")
    dpg.set_value("dash_wz", f"World Z: {go1_state['world_z']:.3f}")
    dpg.set_value("dash_yaw", f"Yaw (Unity): {go1_state['yaw_unity']:.3f} rad")
    dpg.set_value("dash_vx", f"Vx Cmd: {go1_state['vx_cmd']:.2f}")
    dpg.set_value("dash_vy", f"Vy Cmd: {go1_state['vy_cmd']:.2f}")
    dpg.set_value("dash_wz", f"Wz Cmd: {go1_state['wz_cmd']:.2f}")
    
    if is_running and (time.time() - last_logic_time > LOGIC_RATE):
        execute_graph_once()
        last_logic_time = time.time()
        
    dpg.render_dearpygui_frame()

dpg.destroy_context()