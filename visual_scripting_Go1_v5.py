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
sys.path.append('/home/physical/PyGui-Visual-Scripting/unitree_legged_sdk/lib/python/arm64')

try:
    import robot_interface as sdk
    HAS_UNITREE_SDK = True
except ImportError as e:
    HAS_UNITREE_SDK = False
    print(f"Warning: 'robot_interface' module not found. ({e})")

# ================= [Global Settings] =================
node_registry = {}
link_registry = {}
is_running = False

SAVE_DIR = "Node_File_Go1"
if not os.path.exists(SAVE_DIR): os.makedirs(SAVE_DIR)

dashboard_state = {"status": "Idle", "hw_link": "Offline", "unity_link": "Waiting"}
system_log_buffer = deque(maxlen=50)

# V5 Hardware & Network Config
HIGHLEVEL = 0xee; LOCAL_PORT = 8090; ROBOT_IP = "192.168.50.159"; ROBOT_PORT = 8082
UNITY_IP = "192.168.50.246"; UNITY_STATE_PORT = 15101; UNITY_CMD_PORT = 15102; UNITY_RX_PORT = 15100

# V5 Tuning & States
dt = 0.002
V_MAX, S_MAX, W_MAX = 0.4, 0.4, 2.0
VX_CMD, VY_CMD, WZ_CMD = 0.20, 0.20, 1.00
hold_timeout_sec = 0.1; repeat_grace_sec = 0.4; min_move_sec = 0.4; stop_brake_sec = 0.0

node_intent = {'vx': 0.0, 'vy': 0.0, 'wz': 0.0, 'yaw_align': False, 'reset_yaw': False, 'stop': False, 'use_unity_cmd': True, 'trigger_time': time.monotonic()}
go1_state = {'world_x': 0.0, 'world_z': 0.0, 'yaw_unity': 0.0, 'vx_cmd': 0.0, 'vy_cmd': 0.0, 'wz_cmd': 0.0, 'mode': 1, 'reason': "NONE"}
unity_teleop_data = {'vx': 0.0, 'vy': 0.0, 'wz': 0.0, 'estop': 0, 'active': False} 

# V5 Camera Variables
camera_state = {'status': 'Stopped', 'target_ip': ''}
camera_command_queue = deque()

# ================= [Helper Functions] =================
def write_log(msg):
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {msg}"); system_log_buffer.append(f"[{timestamp}] {msg}")

def clamp(x, lo, hi): return lo if x < lo else hi if x > hi else x
def wrap_pi(a):
    while a > math.pi: a -= 2.0 * math.pi
    while a < -math.pi: a += 2.0 * math.pi
    return a

def get_save_files():
    if not os.path.exists(SAVE_DIR): return []
    return [f for f in os.listdir(SAVE_DIR) if f.endswith(".json")]

# ================= [Background Thread (Camera SSH V5)] =================
def camera_worker_thread():
    global camera_state
    nanos = ["unitree@192.168.123.13", "unitree@192.168.123.14", "unitree@192.168.123.15"]
    
    while True:
        if camera_command_queue:
            cmd, pc_ip = camera_command_queue.popleft()
            
            if cmd == 'START':
                camera_state['status'] = 'Starting...'
                camera_state['target_ip'] = pc_ip
                write_log(f"Cam: Start stream to {pc_ip}")
                
                for nano in nanos:
                    script = f"cd /home/unitree && ./kill_camera.sh || true; "
                    if '15' in nano:
                        script += "sudo fuser -k /dev/video0 2>/dev/null || true; sudo pkill -f point_cloud 2>/dev/null || true; "
                    script += f"nohup ./go1_send_both.sh {pc_ip} > /home/unitree/go1_master.log 2>&1 &"
                    
                    try: subprocess.Popen(["ssh", "-o", "StrictHostKeyChecking=accept-new", nano, f"bash -lc '{script}'"])
                    except Exception as e: write_log(f"SSH Error ({nano}): {e}")
                        
                time.sleep(2) 
                camera_state['status'] = 'Running'
                write_log("Cam: Streaming Active")
                
            elif cmd == 'STOP':
                camera_state['status'] = 'Stopping...'
                write_log("Cam: Stopping stream...")
                for nano in nanos:
                    script = "cd /home/unitree && pkill -f go1_send_cam || true; pkill -f gst-launch-1.0 || true; ./kill_camera.sh || true"
                    try: subprocess.Popen(["ssh", "-o", "StrictHostKeyChecking=accept-new", nano, f"bash -lc '{script}'"])
                    except: pass
                time.sleep(1)
                camera_state['status'] = 'Stopped'
                write_log("Cam: Stream Stopped")
        
        time.sleep(0.1)

# ================= [Background Comm Thread (Go1)] =================
def go1_v4_comm_thread():
    global go1_state, UNITY_IP, unity_teleop_data
    if HAS_UNITREE_SDK:
        udp = sdk.UDP(HIGHLEVEL, LOCAL_PORT, ROBOT_IP, ROBOT_PORT)
        cmd = sdk.HighCmd(); state = sdk.HighState()
        udp.InitCmdData(cmd); dashboard_state["hw_link"] = "Online"
    else: 
        udp = cmd = state = None; dashboard_state["hw_link"] = "Simulation"
        
    sock_tx_state = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock_tx_cmd   = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock_rx_unity = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock_rx_unity.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try: 
        sock_rx_unity.bind(("0.0.0.0", UNITY_RX_PORT))
        sock_rx_unity.setblocking(False)
    except: pass
    
    stand_only = True; now = time.monotonic()
    last_key_time = last_move_cmd_time = grace_deadline = now
    use_grace = True; last_unity_cmd_time = now; unity_timeout_sec = 0.15
    yaw0_initialized = False; yaw0 = 0.0; UNITY_YAW_OFFSET_RAD = math.pi / 2.0
    world_x = world_z = 0.0; last_dr_time = now; seq = 0
    yaw_align_active = False; yaw_align_target_rel = 0.0; yaw_align_kp = 2.0; yaw_align_tol_rad = 2.0 * math.pi / 180.0

    def reset_cmd_base():
        if not cmd: return
        cmd.mode = 0; cmd.gaitType = 0; cmd.speedLevel = 0
        cmd.footRaiseHeight = 0.08; cmd.bodyHeight = 0.0
        cmd.euler = [0.0, 0.0, 0.0]; cmd.velocity = [0.0, 0.0]
        cmd.yawSpeed = 0.0; cmd.reserve = 0

    next_t = time.monotonic()
    while True:
        tnow = time.monotonic()
        if tnow < next_t: time.sleep(max(0.0, next_t - tnow))
        next_t += dt

        raw_yaw = 0.0
        if udp: 
            udp.Recv(); udp.GetRecv(state); raw_yaw = float(state.imu.rpy[2])

        if not yaw0_initialized: 
            yaw0 = raw_yaw; yaw0_initialized = True; last_dr_time = time.monotonic()
            
        if node_intent['reset_yaw']: 
            yaw0 = raw_yaw; last_dr_time = time.monotonic(); node_intent['reset_yaw'] = False; write_log("YAW0 Reset")

        yaw_rel = wrap_pi(raw_yaw - yaw0)
        yaw_unity = wrap_pi(yaw_rel + UNITY_YAW_OFFSET_RAD)
        go1_state['yaw_unity'] = yaw_unity

        is_node_active = (tnow - node_intent['trigger_time']) < 0.1
        if node_intent['yaw_align']: 
            yaw_align_active = True; stand_only = False; last_key_time = last_move_cmd_time = grace_deadline = tnow; use_grace = True; node_intent['yaw_align'] = False
        if node_intent['stop']: 
            yaw_align_active = False; stand_only = True; last_key_time = last_move_cmd_time = grace_deadline = tnow; use_grace = True; node_intent['stop'] = False
        elif is_node_active:
            yaw_align_active = False; stand_only = False; last_key_time = tnow; grace_deadline = tnow + repeat_grace_sec
            if abs(node_intent['vx']) > 0 or abs(node_intent['vy']) > 0 or abs(node_intent['wz']) > 0: 
                last_move_cmd_time = tnow

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
            unity_teleop_data['vx'] = uvx
            unity_teleop_data['vy'] = uvy
            unity_teleop_data['wz'] = uwz
            unity_teleop_data['estop'] = uestop
            
        unity_active = node_intent['use_unity_cmd'] and ((tnow - last_unity_cmd_time) <= unity_timeout_sec)
        unity_teleop_data['active'] = unity_active
        
        if not unity_active: dashboard_state['unity_link'] = "Waiting"

        since_key = tnow - last_key_time; since_move = tnow - last_move_cmd_time
        active_walk = ((not stand_only) and (since_key <= hold_timeout_sec)) or ((not stand_only) and use_grace and (tnow <= grace_deadline)) or ((not stand_only) and (since_move <= min_move_sec))

        reset_cmd_base()
        target_mode = 1; out_vx = 0.0; out_vy = 0.0; out_wz = 0.0

        if yaw_align_active:
            err = wrap_pi(yaw_rel - yaw_align_target_rel)
            if abs(err) <= yaw_align_tol_rad: 
                yaw_align_active = False; target_mode = 1
            else: 
                target_mode = 2
                if cmd: cmd.gaitType = 1
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
            cmd.mode = target_mode; cmd.velocity = [out_vx, out_vy]; cmd.yawSpeed = out_wz
            udp.SetSend(cmd); udp.Send()
            
        go1_state['vx_cmd'] = out_vx; go1_state['vy_cmd'] = out_vy; go1_state['wz_cmd'] = out_wz; go1_state['mode'] = target_mode

        dts = tnow - last_dr_time; last_dr_time = tnow
        cy = math.cos(yaw_unity); sy = math.sin(yaw_unity)
        world_x += (out_vx * cy - out_vy * sy) * dts; world_z += (out_vx * sy + out_vy * cy) * dts
        go1_state['world_x'] = world_x; go1_state['world_z'] = world_z

        estop = 1 if target_mode == 1 else 0; seq += 1
        msg_state = f"{seq} {time.time()*1000.0:.1f} {world_x:.6f} {world_z:.6f} {yaw_unity:.6f} {out_vx:.3f} {out_vy:.3f} {out_wz:.3f} {estop} {target_mode}"
        msg_cmd = f"{out_vx:.3f} {out_vy:.3f} {out_wz:.3f} {estop}"
        try: 
            sock_tx_state.sendto(msg_state.encode("utf-8"), (UNITY_IP, UNITY_STATE_PORT))
            sock_tx_cmd.sendto(msg_cmd.encode("utf-8"), (UNITY_IP, UNITY_CMD_PORT))
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

# ================= [V5 Camera Link Node] =================
class CameraStreamNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "Camera Stream", "CAM_STREAM")
        self.combo_action = None; self.field_ip = None; self.out_flow = None
        
    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label="Camera Link (Go1)"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as flow: 
                dpg.add_text("Flow In"); self.inputs[flow] = "Flow"
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                self.combo_action = dpg.add_combo(["Start Stream", "Stop Stream"], default_value="Start Stream", width=130)
                dpg.add_spacer(height=3)
                dpg.add_text("Server(PC) IP:", color=(255,150,200))
                self.field_ip = dpg.add_input_text(width=130, default_value="192.168.50.81")
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out: 
                dpg.add_text("Flow Out"); self.outputs[out] = "Flow"; self.out_flow = out
                
    def execute(self):
        action = dpg.get_value(self.combo_action)
        pc_ip = dpg.get_value(self.field_ip)
        
        # Debounce filter: only queue command if state is different to prevent spamming
        if action == "Start Stream" and camera_state['status'] in ['Stopped', 'Stopping...']:
            camera_command_queue.append(('START', pc_ip))
        elif action == "Stop Stream" and camera_state['status'] in ['Running', 'Starting...']:
            camera_command_queue.append(('STOP', pc_ip))
            
        return self.out_flow
        
    def get_settings(self): return {"act": dpg.get_value(self.combo_action), "ip": dpg.get_value(self.field_ip)}
    def load_settings(self, data): 
        dpg.set_value(self.combo_action, data.get("act", "Start Stream"))
        dpg.set_value(self.field_ip, data.get("ip", "192.168.50.81"))

# ================= [V4 Unity Link Node] =================
class UnityControlNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "Unity Link", "UNITY_CONTROL")
        self.field_ip = None; self.chk_enable = None
        self.out_vx = None; self.out_vy = None; self.out_wz = None; self.out_active = None
    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label="Unity Link (Flowless)"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                dpg.add_text("Unity PC IP:", color=(100,255,100))
                self.field_ip = dpg.add_input_text(width=120, default_value=UNITY_IP)
                dpg.add_spacer(height=3)
                self.chk_enable = dpg.add_checkbox(label="Enable Teleop Rx", default_value=True)
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as vx: dpg.add_text("Teleop Vx"); self.outputs[vx] = "Data"; self.out_vx = vx
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as vy: dpg.add_text("Teleop Vy"); self.outputs[vy] = "Data"; self.out_vy = vy
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as wz: dpg.add_text("Teleop Wz"); self.outputs[wz] = "Data"; self.out_wz = wz
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as act: dpg.add_text("Is Active?"); self.outputs[act] = "Data"; self.out_active = act
    def execute(self):
        global UNITY_IP, node_intent
        UNITY_IP = dpg.get_value(self.field_ip)
        node_intent['use_unity_cmd'] = dpg.get_value(self.chk_enable)
        self.output_data[self.out_vx] = unity_teleop_data['vx']; self.output_data[self.out_vy] = unity_teleop_data['vy']
        self.output_data[self.out_wz] = unity_teleop_data['wz']; self.output_data[self.out_active] = unity_teleop_data['active']
        return None
    def get_settings(self): return {"ip": dpg.get_value(self.field_ip), "en": dpg.get_value(self.chk_enable)}
    def load_settings(self, data): dpg.set_value(self.field_ip, data.get("ip", "192.168.50.246")); dpg.set_value(self.chk_enable, data.get("en", True))

# ================= [V3 Logic Nodes] =================
class CommandActionNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "Go1 Action", "CMD_ACTION")
        self.combo_id = None; self.in_val1 = None; self.in_val2 = None
        self.out_flow = None; self.field_v1 = None; self.field_v2 = None
    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label="Go1 Action"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as flow: dpg.add_text("Flow In"); self.inputs[flow] = "Flow"
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                self.combo_id = dpg.add_combo(items=["Stand", "Reset Yaw0", "Walk Fwd/Back", "Walk Strafe", "Turn"], default_value="Stand", width=130)
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as v1: dpg.add_text("Speed/Val"); self.field_v1 = dpg.add_input_float(width=60, default_value=0.2); self.inputs[v1] = "Data"; self.in_val1 = v1
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out: dpg.add_text("Flow Out"); self.outputs[out] = "Flow"; self.out_flow = out
    def execute(self):
        global node_intent; mode = dpg.get_value(self.combo_id); v1 = self.fetch_input_data(self.in_val1); v1 = float(v1) if v1 is not None else dpg.get_value(self.field_v1)
        if mode == "Stand": node_intent['stop'] = True
        elif mode == "Reset Yaw0": node_intent['reset_yaw'] = True
        else: node_intent['vx'] = v1 if mode == "Walk Fwd/Back" else 0.0; node_intent['vy'] = v1 if mode == "Walk Strafe" else 0.0; node_intent['wz'] = v1 if mode == "Turn" else 0.0; node_intent['trigger_time'] = time.monotonic()
        return self.out_flow
    def get_settings(self): return {"mode": dpg.get_value(self.combo_id), "v1": dpg.get_value(self.field_v1)}
    def load_settings(self, data): dpg.set_value(self.combo_id, data.get("mode", "Stand")); dpg.set_value(self.field_v1, data.get("v1", 0.2))

class ConditionCompareNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "Check: Go1 State", "COND_COMPARE")
        self.combo_target = None; self.combo_op = None; self.in_val = None; self.out_res = None; self.field_val = None
    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label="Check Go1 State"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                self.combo_target = dpg.add_combo(["World X", "World Z", "Yaw (rad)", "Vx", "Vy", "Wz"], default_value="World X", width=100)
                self.combo_op = dpg.add_combo([">", "<", "=="], default_value=">", width=50)
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as val: dpg.add_text("Value"); self.field_val = dpg.add_input_float(width=60, default_value=0); self.inputs[val] = "Data"; self.in_val = val
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as res: dpg.add_text("Result (Bool)"); self.outputs[res] = "Data"; self.out_res = res
    def execute(self):
        tgt, op = dpg.get_value(self.combo_target), dpg.get_value(self.combo_op); l_val = self.fetch_input_data(self.in_val); ref = float(l_val) if l_val is not None else dpg.get_value(self.field_val)
        curr = 0.0
        if tgt == "World X": curr = go1_state['world_x']
        elif tgt == "World Z": curr = go1_state['world_z']
        elif tgt == "Yaw (rad)": curr = go1_state['yaw_unity']
        elif tgt == "Vx": curr = go1_state['vx_cmd']
        elif tgt == "Vy": curr = go1_state['vy_cmd']
        elif tgt == "Wz": curr = go1_state['wz_cmd']
        res = (curr > ref) if op == ">" else (curr < ref) if op == "<" else (abs(curr - ref) < 0.1); self.output_data[self.out_res] = res; return None
    def get_settings(self): return {"t": dpg.get_value(self.combo_target), "o": dpg.get_value(self.combo_op), "v": dpg.get_value(self.field_val)}
    def load_settings(self, data): dpg.set_value(self.combo_target, data.get("t", "World X")); dpg.set_value(self.combo_op, data.get("o", ">")); dpg.set_value(self.field_val, data.get("v", 0))

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

class GraphNode(BaseNode):
    def __init__(self, node_id): super().__init__(node_id, "Live Graph", "GRAPH"); self.in_x=None; self.in_y=None; self.in_z=None; self.buf_x=deque(maxlen=200); self.buf_y=deque(maxlen=200); self.buf_z=deque(maxlen=200); self.t=deque(maxlen=200); self.c=0
    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label="Live Graph (Flowless)"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as x: dpg.add_text("Val 1", color=(255,100,100)); self.inputs[x]="Data"; self.in_x=x
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as y: dpg.add_text("Val 2", color=(100,255,100)); self.inputs[y]="Data"; self.in_y=y
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as z: dpg.add_text("Val 3", color=(100,100,255)); self.inputs[z]="Data"; self.in_z=z
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                with dpg.plot(height=150, width=250):
                    dpg.add_plot_legend(); dpg.add_plot_axis(dpg.mvXAxis, label="Time", tag=f"ax_{self.node_id}")
                    with dpg.plot_axis(dpg.mvYAxis, label="Val", tag=f"ay_{self.node_id}"):
                        dpg.add_line_series([],[], label="1", tag=f"sx_{self.node_id}"); dpg.add_line_series([],[], label="2", tag=f"sy_{self.node_id}"); dpg.add_line_series([],[], label="3", tag=f"sz_{self.node_id}")
    def execute(self):
        self.c+=1; vx=self.fetch_input_data(self.in_x); vy=self.fetch_input_data(self.in_y); vz=self.fetch_input_data(self.in_z)
        if vx is not None or vy is not None or vz is not None:
            self.buf_x.append(vx or 0); self.buf_y.append(vy or 0); self.buf_z.append(vz or 0); self.t.append(self.c)
            dpg.set_value(f"sx_{self.node_id}", [list(self.t), list(self.buf_x)]); dpg.set_value(f"sy_{self.node_id}", [list(self.t), list(self.buf_y)]); dpg.set_value(f"sz_{self.node_id}", [list(self.t), list(self.buf_z)])
            dpg.set_axis_limits(f"ax_{self.node_id}", self.c-200, self.c)
            all_vals = list(self.buf_x) + list(self.buf_y) + list(self.buf_z)
            dpg.set_axis_limits(f"ay_{self.node_id}", min(all_vals)-0.5, max(all_vals)+0.5)
        return None

class LoggerNode(BaseNode):
    def __init__(self, node_id): super().__init__(node_id, "System Log", "LOGGER"); self.txt=None; self.llen=0
    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label="Logger (Flowless)"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                with dpg.child_window(width=200, height=100): self.txt=dpg.add_text("", wrap=190)
    def execute(self):
        if len(system_log_buffer)!=self.llen: dpg.set_value(self.txt, "\n".join(list(system_log_buffer)[-8:])); self.llen=len(system_log_buffer)
        return None

class PrintNode(BaseNode):
    def __init__(self, node_id): super().__init__(node_id, "Print Log", "PRINT"); self.out_flow = None; self.inp_data = None
    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label="Print Log"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as flow: dpg.add_text("Flow In"); self.inputs[flow] = "Flow"
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as data: dpg.add_text("Data"); self.inputs[data] = "Data"; self.inp_data = data
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out: dpg.add_text("Flow Out"); self.outputs[out] = "Flow"; self.out_flow = out
    def execute(self): val = self.fetch_input_data(self.inp_data); if val is not None: write_log(f"PRINT: {val}"); return self.out_flow

class ConstantNode(BaseNode):
    def __init__(self, node_id): super().__init__(node_id, "Constant", "CONSTANT"); self.out_val = None; self.field_val = None
    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label="Constant"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static): self.field_val = dpg.add_input_float(width=80, default_value=1.0)
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out: dpg.add_text("Data"); self.outputs[out] = "Data"; self.out_val = out
    def execute(self): self.output_data[self.out_val] = dpg.get_value(self.field_val); return None
    def get_settings(self): return {"val": dpg.get_value(self.field_val)}
    def load_settings(self, data): dpg.set_value(self.field_val, data.get("val", 1.0))

class GetStateNode(BaseNode):
    def __init__(self, node_id): super().__init__(node_id, "Get Go1 State", "GET_STATE"); self.out_x = None; self.out_z = None; self.out_yaw = None
    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label="Get Go1 State"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as x: dpg.add_text("World X"); self.outputs[x] = "Data"; self.out_x = x
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as z: dpg.add_text("World Z"); self.outputs[z] = "Data"; self.out_z = z
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as y: dpg.add_text("Yaw (rad)"); self.outputs[y] = "Data"; self.out_yaw = y
    def execute(self): self.output_data[self.out_x] = go1_state['world_x']; self.output_data[self.out_z] = go1_state['world_z']; self.output_data[self.out_yaw] = go1_state['yaw_unity']; return None

class KeyboardControlNode(BaseNode):
    def __init__(self, node_id): super().__init__(node_id, "Keyboard (V3)", "KEYBOARD"); self.out_vx = None; self.out_vy = None; self.out_wz = None
    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label="Keyboard (Intent)"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as flow: dpg.add_text("Flow In"); self.inputs[flow] = "Flow"
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static): dpg.add_text("W/S: Fwd/Back\nA/D: Strafe\nQ/E: Turn\nSpace: Stop\nR: Yaw Align", color=(255,150,150))
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as vx: dpg.add_text("Target Vx"); self.outputs[vx] = "Data"; self.out_vx = vx
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as vy: dpg.add_text("Target Vy"); self.outputs[vy] = "Data"; self.out_vy = vy
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as wz: dpg.add_text("Target Wz"); self.outputs[wz] = "Data"; self.out_wz = wz
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as f: dpg.add_text("Flow Out"); self.outputs[f] = "Flow"
    def execute(self):
        global node_intent; vx = 0.0; vy = 0.0; wz = 0.0
        if dpg.is_key_down(dpg.mvKey_W): vx = VX_CMD
        if dpg.is_key_down(dpg.mvKey_S): vx = -VX_CMD
        if dpg.is_key_down(dpg.mvKey_A): vy = VY_CMD
        if dpg.is_key_down(dpg.mvKey_D): vy = -VY_CMD
        if dpg.is_key_down(dpg.mvKey_Q): wz = WZ_CMD
        if dpg.is_key_down(dpg.mvKey_E): wz = -WZ_CMD
        if dpg.is_key_down(dpg.mvKey_Spacebar): node_intent['stop'] = True
        if dpg.is_key_pressed(dpg.mvKey_R): node_intent['yaw_align'] = True
        if dpg.is_key_pressed(dpg.mvKey_Z): node_intent['reset_yaw'] = True
        if vx or vy or wz: node_intent['vx'] = vx; node_intent['vy'] = vy; node_intent['wz'] = wz; node_intent['trigger_time'] = time.monotonic()
        self.output_data[self.out_vx]=vx; self.output_data[self.out_vy]=vy; self.output_data[self.out_wz]=wz
        for k, v in self.outputs.items():
            if v == "Flow": return k
        return None

class RobotControlNode(BaseNode):
    def __init__(self, node_id): super().__init__(node_id, "Go1 Driver (V3)", "ROBOT_CONTROL"); self.in_vx=None; self.in_vy=None; self.in_wz=None
    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label=self.label):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as flow: dpg.add_text("Flow In"); self.inputs[flow]="Flow"
            for axis, label in [('vx',"Vx In"), ('vy',"Vy In"), ('wz',"Wz In")]:
                with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as aid:
                    dpg.add_text(label, color=(255,255,0)); self.inputs[aid]="Data"
                    if axis=='vx': self.in_vx=aid
                    elif axis=='vy': self.in_vy=aid
                    elif axis=='wz': self.in_wz=aid
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as fout: dpg.add_text("Flow Out"); self.outputs[fout]="Flow"
    def execute(self):
        global node_intent
        tvx, tvy, twz = self.fetch_input_data(self.in_vx), self.fetch_input_data(self.in_vy), self.fetch_input_data(self.in_wz)
        if tvx is not None or tvy is not None or twz is not None:
            node_intent['vx'] = float(tvx or 0); node_intent['vy'] = float(tvy or 0); node_intent['wz'] = float(twz or 0); node_intent['trigger_time'] = time.monotonic()
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
    
    # V5 Update: Add CameraStreamNode to the conditional flow exclusion
    for node in node_registry.values():
        if not isinstance(node, (StartNode, CommandActionNode, LogicIfNode, LogicLoopNode, ConditionCompareNode, ConditionKeyNode, PrintNode, KeyboardControlNode, RobotControlNode, CameraStreamNode)):
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

class NodeFactory:
    @staticmethod
    def create_node(node_type, node_id=None):
        if node_id is None: node_id = dpg.generate_uuid()
        node = None
        if node_type == "START": node = StartNode(node_id)
        elif node_type == "KEYBOARD": node = KeyboardControlNode(node_id)
        elif node_type == "ROBOT_CONTROL": node = RobotControlNode(node_id)
        elif node_type == "CMD_ACTION": node = CommandActionNode(node_id)
        elif node_type == "LOGIC_IF": node = LogicIfNode(node_id)
        elif node_type == "LOGIC_LOOP": node = LogicLoopNode(node_id)
        elif node_type == "COND_COMPARE": node = ConditionCompareNode(node_id)
        elif node_type == "COND_KEY": node = ConditionKeyNode(node_id)
        elif node_type == "CONSTANT": node = ConstantNode(node_id)
        elif node_type == "PRINT": node = PrintNode(node_id)
        elif node_type == "GRAPH": node = GraphNode(node_id)
        elif node_type == "LOGGER": node = LoggerNode(node_id)
        elif node_type == "GET_STATE": node = GetStateNode(node_id)
        elif node_type == "UNITY_CONTROL": node = UnityControlNode(node_id)
        elif node_type == "CAM_STREAM": node = CameraStreamNode(node_id) # V5 Added
        
        if node: node.build_ui(); node_registry[node_id] = node; return node
        return None

def toggle_exec(s, a): global is_running; is_running = not is_running; dpg.set_item_label("btn_run", "STOP" if is_running else "RUN")
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

# ================= [Main Setup] =================
threading.Thread(target=go1_v4_comm_thread, daemon=True).start()
threading.Thread(target=camera_worker_thread, daemon=True).start() # V5 Camera Thread Added
threading.Thread(target=lambda: (time.sleep(1), update_file_list()), daemon=True).start()

dpg.create_context()
with dpg.handler_registry(): dpg.add_key_press_handler(dpg.mvKey_Delete, callback=delete_selection)

with dpg.window(tag="PrimaryWindow"):
    # [1번 줄] System Status | Odom | Commands
    with dpg.group(horizontal=True):
        with dpg.child_window(width=250, height=130, border=True):
            dpg.add_text("System Status (V5)", color=(150,150,150))
            dpg.add_text(f"HW: {dashboard_state['hw_link']}", tag="dash_link", color=(0,255,0))
            dpg.add_text(f"Unity: Waiting", tag="dash_unity", color=(255,255,0))
            dpg.add_text(f"Camera: Stopped", tag="dash_cam", color=(200,200,200)) # V5 추가
            dpg.add_spacer(height=2)
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

    # [2번 줄] File Manager
    with dpg.group(horizontal=True):
        with dpg.child_window(width=450, height=50, border=True):
            with dpg.group(horizontal=True):
                dpg.add_text("Save:"); dpg.add_input_text(tag="file_name_input", default_value="go1_graph", width=100); dpg.add_button(label="SAVE", callback=save_cb, width=50)
                dpg.add_text(" | Load:"); dpg.add_combo(items=get_save_files(), tag="file_list_combo", width=100); dpg.add_button(label="LOAD", callback=load_cb, width=50)

    dpg.add_separator()
    # Tool Bar (V5 Camera Added)
    with dpg.group(horizontal=True):
        dpg.add_button(label="START", callback=add_node_cb, user_data="START")
        dpg.add_button(label="KEY (Go1)", callback=add_node_cb, user_data="KEYBOARD")
        dpg.add_button(label="DRIVER (Go1)", callback=add_node_cb, user_data="ROBOT_CONTROL")
        
        dpg.add_button(label="UNITY", callback=add_node_cb, user_data="UNITY_CONTROL") 
        dpg.add_button(label="CAMERA", callback=add_node_cb, user_data="CAM_STREAM") # V5 추가
        
        dpg.add_spacer(width=10)
        dpg.add_button(label="ACTION", callback=add_node_cb, user_data="CMD_ACTION")
        dpg.add_button(label="IF", callback=add_node_cb, user_data="LOGIC_IF")
        dpg.add_button(label="LOOP", callback=add_node_cb, user_data="LOGIC_LOOP")
        dpg.add_button(label="CHK STATE", callback=add_node_cb, user_data="COND_COMPARE")
        dpg.add_button(label="CHK KEY", callback=add_node_cb, user_data="COND_KEY")
        dpg.add_spacer(width=10)
        dpg.add_button(label="GET STATE", callback=add_node_cb, user_data="GET_STATE")
        dpg.add_button(label="GRAPH", callback=add_node_cb, user_data="GRAPH")
        dpg.add_button(label="LOG", callback=add_node_cb, user_data="LOGGER")
        dpg.add_spacer(width=50)
        dpg.add_button(label="RUN SCRIPT", tag="btn_run", callback=toggle_exec, width=150)

    with dpg.node_editor(tag="node_editor", callback=link_cb, delink_callback=del_link_cb): pass

dpg.create_viewport(title='PyGui Visual Scripting v5 (Camera & Unity Twin)', width=1024, height=768, vsync=True)
dpg.setup_dearpygui(); dpg.set_primary_window("PrimaryWindow", True); dpg.show_viewport()

last_logic_time = 0; LOGIC_RATE = 0.02

while dpg.is_dearpygui_running():
    dpg.set_value("dash_unity", f"Unity: {dashboard_state['unity_link']}")
    dpg.set_value("dash_reason", f"Mode: {go1_state['mode']} | {go1_state['reason']}")
    dpg.set_value("dash_wx", f"World X: {go1_state['world_x']:.3f}")
    dpg.set_value("dash_wz", f"World Z: {go1_state['world_z']:.3f}")
    dpg.set_value("dash_yaw", f"Yaw (Unity): {go1_state['yaw_unity']:.3f} rad")
    dpg.set_value("dash_vx_2", f"Vx Cmd: {go1_state['vx_cmd']:.2f}")
    dpg.set_value("dash_vy_2", f"Vy Cmd: {go1_state['vy_cmd']:.2f}")
    dpg.set_value("dash_wz_2", f"Wz Cmd: {go1_state['wz_cmd']:.2f}")
    
    # V5 UI Camera Status Update
    dpg.set_value("dash_cam", f"Camera: {camera_state['status']}")
    if camera_state['status'] == 'Running': dpg.configure_item("dash_cam", color=(0,255,0))
    elif camera_state['status'] == 'Stopped': dpg.configure_item("dash_cam", color=(200,200,200))
    else: dpg.configure_item("dash_cam", color=(255,200,0))
    
    if is_running and (time.time() - last_logic_time > LOGIC_RATE):
        execute_graph_once()
        last_logic_time = time.time()
        
    dpg.render_dearpygui_frame()

dpg.destroy_context()