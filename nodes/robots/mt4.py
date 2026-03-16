import time
import math
import json
import serial
import os
import csv
import socket
from collections import deque
from nodes.base import BaseNode
from core.input_manager import global_input_manager

ser = None 
mt4_current_pos = {'x': 200.0, 'y': 0.0, 'z': 120.0, 'roll': 0.0, 'gripper': 40.0}
mt4_target_goal = {'x': 200.0, 'y': 0.0, 'z': 120.0, 'roll': 0.0, 'gripper': 40.0}
mt4_manual_override_until = 0.0 
mt4_collision_lock_until = 0.0

mt4_dashboard = {"status": "Idle", "hw_link": "Offline", "latency": 0.0, "last_pkt_time": 0.0}

MT4_LIMITS = {'min_x': 200, 'max_x': 280, 'min_y': -200, 'max_y': 200, 'min_z': 0, 'max_z': 280, 'min_r': -180.0, 'max_r': 180.0}
MT4_GRIPPER_MIN = 30.0
MT4_GRIPPER_MAX = 60.0
MT4_Z_OFFSET = 90.0
MT4_UNITY_IP = "192.168.50.63"
MT4_FEEDBACK_PORT = 5005

mt4_mode = {"recording": False, "playing": False}
mt4_log_event_queue = deque()
mt4_record_f = None
mt4_record_writer = None
mt4_record_temp_name = ""

def get_mt4_paths():
    if not os.path.exists("path_record"): return []
    return [f for f in os.listdir("path_record") if f.endswith(".csv")]

def send_unity_ui(msg_type, extra_data):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(f"type:{msg_type},extra:{extra_data}".encode("utf-8"), (MT4_UNITY_IP, 5007))
        sock.close()
    except: pass

def parse_unity_packet(raw_data):
    try: return json.loads(raw_data)
    except: pass
    parsed = {}
    for part in raw_data.split(','):
        if ':' in part:
            k, v = part.split(':', 1)
            parsed[k.strip()] = v.strip()
    return parsed

class MT4DriverNode(BaseNode):
    def __init__(self, node_id: str):
        super().__init__(node_id, "MT4 Core Driver", "MT4_DRIVER")
        self.last_cmd = ""
        self.last_write_time = 0
        self.write_interval = 0.0
        self.outputs["Flow Out"] = True 

    def get_ui_schema(self):
        return [
            ("IN_FLOW", "Flow In", None),
            ("IN_DATA", "X", 200.0), ("IN_DATA", "Y", 0.0), ("IN_DATA", "Z", 120.0),
            ("IN_DATA", "Roll", 0.0), ("IN_DATA", "Gripper", 40.0),
            ("OUT_FLOW", "Flow Out", None)
        ]

    def get_settings_schema(self): return [("smooth", 1.0), ("grip_spd", 50.0), ("roll_spd", 50.0)]

    def execute(self):
        global mt4_current_pos, mt4_target_goal, mt4_manual_override_until, ser
        if time.time() < mt4_collision_lock_until: return "Flow Out"

        inputs = {
            'x': self.inputs.get("X"), 'y': self.inputs.get("Y"), 'z': self.inputs.get("Z"),
            'roll': self.inputs.get("Roll"), 'gripper': self.inputs.get("Gripper")
        }

        inputs_changed = False
        for key in ['x', 'y', 'z', 'roll', 'gripper']:
            val = inputs.get(key)
            if val is not None:
                if abs(float(val) - mt4_target_goal[key]) > 0.5: inputs_changed = True

        if time.time() > mt4_manual_override_until and inputs_changed:
            for key in ['x', 'y', 'z', 'roll', 'gripper']:
                if inputs.get(key) is not None: mt4_target_goal[key] = float(inputs[key])

        smooth = 1.0 if time.time() < mt4_manual_override_until else max(0.01, min(self.settings.get('smooth', 1.0), 1.0))
        dx = mt4_target_goal['x'] - mt4_current_pos['x']; dy = mt4_target_goal['y'] - mt4_current_pos['y']; dz = mt4_target_goal['z'] - mt4_current_pos['z']
        
        nx = mt4_current_pos['x'] + dx * smooth if not (abs(dx)<0.5 and abs(dy)<0.5 and abs(dz)<0.5) else mt4_target_goal['x']
        ny = mt4_current_pos['y'] + dy * smooth if not (abs(dx)<0.5 and abs(dy)<0.5 and abs(dz)<0.5) else mt4_target_goal['y']
        nz = mt4_current_pos['z'] + dz * smooth if not (abs(dx)<0.5 and abs(dy)<0.5 and abs(dz)<0.5) else mt4_target_goal['z']

        g_spd = float(self.settings.get('grip_spd', 50.0)) * 0.1; r_spd = float(self.settings.get('roll_spd', 50.0)) * 0.1

        dr_err = mt4_target_goal['roll'] - mt4_current_pos['roll']
        nr = mt4_current_pos['roll'] + math.copysign(r_spd, dr_err) if abs(dr_err) > r_spd else mt4_target_goal['roll']
        
        dg_err = mt4_target_goal['gripper'] - mt4_current_pos['gripper']
        ng = mt4_current_pos['gripper'] + math.copysign(g_spd, dg_err) if abs(dg_err) > g_spd else mt4_target_goal['gripper']

        nx = max(MT4_LIMITS['min_x'], min(nx, MT4_LIMITS['max_x'])); ny = max(MT4_LIMITS['min_y'], min(ny, MT4_LIMITS['max_y']))
        nz = max(MT4_LIMITS['min_z'], min(nz, MT4_LIMITS['max_z'])); nr = max(MT4_LIMITS['min_r'], min(nr, MT4_LIMITS['max_r']))
        ng = max(MT4_GRIPPER_MIN, min(ng, MT4_GRIPPER_MAX))

        mt4_current_pos.update({'x': nx, 'y': ny, 'z': nz, 'roll': nr, 'gripper': ng})

        if time.time() - self.last_write_time >= self.write_interval:
            cmd = f"G0 X{nx:.1f} Y{ny:.1f} Z{nz:.1f} A{nr:.1f}\nM3 S{int(ng)}\n"
            if cmd != self.last_cmd:
                if ser and ser.is_open:
                    try: ser.write(cmd.encode()); self.last_write_time = time.time()
                    except: pass
                self.last_cmd = cmd

        self.outputs["Flow Out"] = True
        return "Flow Out"

class MT4GravitySagNode(BaseNode):
    def __init__(self, node_id: str): super().__init__(node_id, "Gravity Sag Comp (StR)", "MT4_SAG")
    def get_ui_schema(self): return [("IN_DATA", "X In", 0.0), ("IN_DATA", "Z In", 0.0), ("OUT_DATA", "Z Out (Comp)", None)]
    def get_settings_schema(self): return [("sag_factor", 0.05)]
    def execute(self):
        x_val = self.inputs.get("X In"); z_val = self.inputs.get("Z In")
        if x_val is not None and z_val is not None: self.outputs["Z Out (Comp)"] = float(z_val) + float(x_val) * float(self.settings.get('sag_factor', 0.0))
        elif z_val is not None: self.outputs["Z Out (Comp)"] = float(z_val)
        return None

class MT4CalibrationNode(BaseNode):
    def __init__(self, node_id: str): super().__init__(node_id, "3D Calibration (StR)", "MT4_CALIB")
    def get_ui_schema(self): return [("IN_DATA", "X In", 0.0), ("IN_DATA", "Y In", 0.0), ("IN_DATA", "Z In", 0.0), ("OUT_DATA", "X Out", None), ("OUT_DATA", "Y Out", None), ("OUT_DATA", "Z Out", None)]
    def get_settings_schema(self): return [("x_offset", 0.0), ("y_offset", 0.0), ("z_offset", 0.0), ("scale", 1.0)]
    def execute(self):
        s = float(self.settings.get('scale', 1.0))
        if self.inputs.get("X In") is not None: self.outputs["X Out"] = float(self.inputs["X In"]) * s + float(self.settings.get('x_offset', 0.0))
        if self.inputs.get("Y In") is not None: self.outputs["Y Out"] = float(self.inputs["Y In"]) * s + float(self.settings.get('y_offset', 0.0))
        if self.inputs.get("Z In") is not None: self.outputs["Z Out"] = float(self.inputs["Z In"]) * s + float(self.settings.get('z_offset', 0.0))
        return None

class MT4TooltipNode(BaseNode):
    def __init__(self, node_id: str): super().__init__(node_id, "Tool-tip Offset (StR)", "MT4_TOOLTIP")
    def get_ui_schema(self): return [("IN_DATA", "X In", 0.0), ("IN_DATA", "Z In", 0.0), ("OUT_DATA", "X Out (Comp)", None), ("OUT_DATA", "Z Out (Comp)", None)]
    def get_settings_schema(self): return [("tool_length", 0.0), ("tool_angle", 0.0)]
    def execute(self):
        x, z = self.inputs.get("X In"), self.inputs.get("Z In")
        l, a = float(self.settings.get('tool_length', 0.0)), float(self.settings.get('tool_angle', 0.0))
        if x is not None and z is not None:
            self.outputs["X Out (Comp)"] = float(x) + l * math.cos(math.radians(a))
            self.outputs["Z Out (Comp)"] = float(z) + l * math.sin(math.radians(a))
        return None

class MT4BacklashNode(BaseNode):
    def __init__(self, node_id: str): super().__init__(node_id, "Backlash & Inertia (StR)", "MT4_BACKLASH"); self.internal_pos = None
    def get_ui_schema(self): return [("IN_DATA", "X In", 0.0), ("IN_DATA", "Y In", 0.0), ("IN_DATA", "Z In", 0.0), ("OUT_DATA", "X Out", None), ("OUT_DATA", "Y Out", None), ("OUT_DATA", "Z Out", None)]
    def get_settings_schema(self): return [("decel_dist", 15.0), ("stop_delay", 100.0)]
    def execute(self):
        tx, ty, tz = self.inputs.get("X In"), self.inputs.get("Y In"), self.inputs.get("Z In")
        if None in [tx, ty, tz]: return None
        if self.internal_pos is None: self.internal_pos = [float(tx), float(ty), float(tz)]
        dd, df = max(1.0, float(self.settings.get('decel_dist', 15.0))), max(1.0, float(self.settings.get('stop_delay', 100.0)))
        dx, dy, dz = float(tx) - self.internal_pos[0], float(ty) - self.internal_pos[1], float(tz) - self.internal_pos[2]
        dist = math.sqrt(dx**2 + dy**2 + dz**2)
        speed = 1.0 if dist > dd else max(0.01, (dist / dd) * (50.0 / df))
        self.internal_pos[0] += dx * speed; self.internal_pos[1] += dy * speed; self.internal_pos[2] += dz * speed
        self.outputs["X Out"] = self.internal_pos[0]; self.outputs["Y Out"] = self.internal_pos[1]; self.outputs["Z Out"] = self.internal_pos[2]
        return None

class MT4UnityNode(BaseNode):
    def __init__(self, node_id: str):
        super().__init__(node_id, "Unity Logic (MT4)", "MT4_UNITY")
        self.last_key_time = 0.0
        global mt4_target_goal
        self.outputs["Target X"] = mt4_target_goal['x']; self.outputs["Target Y"] = mt4_target_goal['y']
        self.outputs["Target Z"] = mt4_target_goal['z']; self.outputs["Target Roll"] = mt4_target_goal['roll']
        self.outputs["Target Grip"] = mt4_target_goal['gripper']; self.outputs["Flow Out"] = True

    def get_ui_schema(self): 
        return [
            ("IN_FLOW", "Flow In", None), ("IN_DATA", "JSON", None),
            ("OUT_DATA", "Target X", None), ("OUT_DATA", "Target Y", None), ("OUT_DATA", "Target Z", None),
            ("OUT_DATA", "Target Roll", None), ("OUT_DATA", "Target Grip", None), ("OUT_FLOW", "Flow Out", None)
        ]
        
    def get_settings_schema(self): return []
    
    def execute(self):
        global mt4_collision_lock_until, mt4_target_goal, mt4_mode, mt4_current_pos
        raw_json = self.inputs.get("JSON", "")

        if (time.time() < mt4_manual_override_until) or mt4_mode.get("playing", False):
            self.outputs["Target X"] = mt4_target_goal['x']; self.outputs["Target Y"] = mt4_target_goal['y']
            self.outputs["Target Z"] = mt4_target_goal['z']; self.outputs["Target Roll"] = mt4_target_goal['roll']
            self.outputs["Target Grip"] = mt4_target_goal['gripper']
        else:
            if raw_json:
                # 🚨 유니티 방향키 파서로 드디어 데이터가 정상 유입됩니다!
                parsed = parse_unity_packet(raw_json)
                msg_type = parsed.get("type", "MOVE")
                
                if msg_type == "CMD":
                    val = parsed.get("val", "")
                    if val == "COLLISION":
                        mt4_collision_lock_until = time.time() + 2.0 
                        if ser and ser.is_open: ser.write(b"!")
                        send_unity_ui("STATUS", "충돌 감지! 로봇 긴급 정지")
                    elif val == "START_REC":
                        if not mt4_mode["recording"]: toggle_mt4_record()
                    elif val.startswith("STOP_REC:"):
                        if mt4_mode["recording"]: toggle_mt4_record(custom_name=val.split(":")[1])
                    elif val == "REQ_FILES": send_unity_ui("FILE_LIST", f"[{'|'.join(get_mt4_paths())}]")
                    elif val.startswith("PLAY:"): play_mt4_path(filename=val.split(":")[1])
                    
                elif msg_type == "MOVE" and time.time() > mt4_collision_lock_until:
                    if 'z' in parsed: self.outputs["Target X"] = float(parsed['z']) * 1000.0
                    if 'x' in parsed: self.outputs["Target Y"] = -float(parsed['x']) * 1000.0
                    if 'y' in parsed: self.outputs["Target Z"] = (float(parsed['y']) * 1000.0) + MT4_Z_OFFSET
                    if 'roll' in parsed: self.outputs["Target Roll"] = float(parsed['roll'])
                    if 'gripper' in parsed: self.outputs["Target Grip"] = float(parsed['gripper']) 
                    
                # 🚨 유니티 방향키 꾹 누름(연속 입력)을 끊김 없이 부드럽게 지원합니다.
                elif msg_type in ["KEY", "DIRECTION"] and time.time() > mt4_collision_lock_until:
                    if time.time() - self.last_key_time > 0.05:
                        self.last_key_time = time.time()
                        val = parsed.get("val", "").upper()
                        step = 10.0; r_step = 5.0
                        nx = self.outputs.get("Target X", mt4_current_pos['x'])
                        ny = self.outputs.get("Target Y", mt4_current_pos['y'])
                        nz = self.outputs.get("Target Z", mt4_current_pos['z'])
                        nr = self.outputs.get("Target Roll", mt4_current_pos['roll'])
                        
                        if val in ["UP", "W"]: nx += step
                        elif val in ["DOWN", "S"]: nx -= step
                        elif val in ["LEFT", "A"]: ny += step
                        elif val in ["RIGHT", "D"]: ny -= step
                        elif val in ["Q"]: nz += step
                        elif val in ["E"]: nz -= step
                        elif val in ["ROLL_UP", "Z"]: nr += r_step
                        elif val in ["ROLL_DOWN", "X"]: nr -= r_step
                        
                        self.outputs["Target X"] = nx; self.outputs["Target Y"] = ny
                        self.outputs["Target Z"] = nz; self.outputs["Target Roll"] = nr

        self.outputs["Flow Out"] = True
        return "Flow Out"

class MT4KeyboardNode(BaseNode):
    def __init__(self, node_id: str):
        super().__init__(node_id, "Keyboard (MT4)", "MT4_KEYBOARD")
        self.last_input_time = 0.0; self.cooldown = 0.2
        global mt4_target_goal
        self.outputs["Target X"] = mt4_target_goal['x']; self.outputs["Target Y"] = mt4_target_goal['y']
        self.outputs["Target Z"] = mt4_target_goal['z']; self.outputs["Target Roll"] = mt4_target_goal['roll']
        self.outputs["Target Grip"] = mt4_target_goal['gripper']; self.outputs["Flow Out"] = True

    def get_ui_schema(self):
        return [
            ("IN_FLOW", "Flow In", None),
            ("OUT_DATA", "Target X", None), ("OUT_DATA", "Target Y", None), ("OUT_DATA", "Target Z", None),
            ("OUT_DATA", "Target Roll", None), ("OUT_DATA", "Target Grip", None), ("OUT_FLOW", "Flow Out", None)
        ]

    def get_settings_schema(self): return [("step_size", 10.0), ("grip_step", 5.0), ("roll_step", 5.0)]

    def execute(self):
        global mt4_target_goal, mt4_manual_override_until, mt4_current_pos
        
        # 🚨 핵심 수정: 수동 제어(오버라이드) 중일 때는 예전 값을 고집하지 않고 현재 목표값을 따라가도록 동기화!
        if time.time() < mt4_manual_override_until:
            self.outputs["Target X"] = mt4_target_goal['x']
            self.outputs["Target Y"] = mt4_target_goal['y']
            self.outputs["Target Z"] = mt4_target_goal['z']
            self.outputs["Target Roll"] = mt4_target_goal['roll']
            self.outputs["Target Grip"] = mt4_target_goal['gripper']
        else:
            from core.input_manager import global_input_manager 
            if time.time() - self.last_input_time > self.cooldown:
                dx = dy = dz = dg = dr = 0
                if global_input_manager.get_key('W'): dx = 1
                if global_input_manager.get_key('S'): dx = -1
                if global_input_manager.get_key('A'): dy = 1
                if global_input_manager.get_key('D'): dy = -1
                if global_input_manager.get_key('Q'): dz = 1
                if global_input_manager.get_key('E'): dz = -1
                if global_input_manager.get_key('J'): dg = 1
                if global_input_manager.get_key('U'): dg = -1
                if global_input_manager.get_key('Z'): dr = 1
                if global_input_manager.get_key('X'): dr = -1

                if any([dx, dy, dz, dg, dr]):
                    self.last_input_time = time.time()
                    step = float(self.settings.get("step_size", 10.0))
                    g_step = float(self.settings.get("grip_step", 5.0)); r_step = float(self.settings.get("roll_step", 5.0))

                    self.outputs["Target X"] = self.outputs.get("Target X", mt4_current_pos['x']) + (dx * step)
                    self.outputs["Target Y"] = self.outputs.get("Target Y", mt4_current_pos['y']) + (dy * step)
                    self.outputs["Target Z"] = self.outputs.get("Target Z", mt4_current_pos['z']) + (dz * step)
                    self.outputs["Target Grip"] = self.outputs.get("Target Grip", mt4_current_pos['gripper']) + (dg * g_step)
                    self.outputs["Target Roll"] = self.outputs.get("Target Roll", mt4_current_pos['roll']) + (dr * r_step)
                    
                    # 🚨 키보드 조작도 수동 제어로 인정하여, 조작 후 0.5초 동안 유니티/다른 노드와 충돌하는 것을 방지합니다.
                    mt4_manual_override_until = time.time() + 0.5

        self.outputs["Flow Out"] = True
        return "Flow Out"

class MT4CommandActionNode(BaseNode):
    def __init__(self, node_id: str): super().__init__(node_id, "MT4 Action", "MT4_ACTION")
    def get_ui_schema(self): return [("IN_FLOW", "Flow In", None), ("IN_DATA", "X / Grip", 0.0), ("IN_DATA", "Y", 0.0), ("IN_DATA", "Z", 0.0), ("OUT_FLOW", "Flow Out", None)]
    def get_settings_schema(self): return [("mode", 1.0)] 
    def execute(self):
        global mt4_target_goal, mt4_manual_override_until
        mt4_manual_override_until = time.time() + 1.0
        mode = int(self.settings.get("mode", 1.0))
        v1 = float(self.inputs.get("X / Grip", 0.0))
        v2 = float(self.inputs.get("Y", 0.0))
        v3 = float(self.inputs.get("Z", 0.0))

        if mode == 1:
            mt4_target_goal['x'] += v1; mt4_target_goal['y'] += v2; mt4_target_goal['z'] += v3
        elif mode == 2:
            mt4_target_goal['x'] = v1; mt4_target_goal['y'] = v2; mt4_target_goal['z'] = v3
        elif mode == 3:
            mt4_target_goal['gripper'] = v1
        elif mode == 4:
            mt4_target_goal['gripper'] += v1
            
        return "Flow Out"

class UDPReceiverNode(BaseNode):
    def __init__(self, node_id: str): 
        super().__init__(node_id, "UDP Receiver", "UDP_RECV")
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); self.sock.setblocking(False)
        self.is_bound = False; self.current_port = 0
        self.outputs["JSON Out"] = ""; self.outputs["Flow Out"] = True

    def get_ui_schema(self): return [("IN_FLOW", "Flow In", None), ("OUT_DATA", "JSON Out", None), ("OUT_FLOW", "Flow Out", None)]
    def get_settings_schema(self): return [("port", 6000), ("ip", "192.168.50.63")]
    
    def execute(self):
        global MT4_UNITY_IP
        port = int(self.settings.get("port", 6000))
        MT4_UNITY_IP = str(self.settings.get("ip", "192.168.50.63"))
        
        if not self.is_bound or self.current_port != port:
            try:
                self.sock.close()
                self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); self.sock.setblocking(False)
                self.sock.bind(('0.0.0.0', port)); self.is_bound = True; self.current_port = port
            except: self.is_bound = False

        received_data = ""
        try:
            while True:
                data, _ = self.sock.recvfrom(4096); decoded = data.decode()
                now = time.time()
                mt4_dashboard["latency"] = (now - mt4_dashboard.get("last_pkt_time", now)) * 1000.0 
                mt4_dashboard["last_pkt_time"] = now; mt4_dashboard["status"] = "Connected"
                received_data = decoded
        except: pass

        # 🚨 데이터가 갱신되지 않으면 핀 데이터를 비워, 유니티 방향키가 무한 반복되어 굳는 현상을 방지합니다.
        if received_data:
            self.outputs["JSON Out"] = received_data
        else:
            self.outputs["JSON Out"] = ""

        try:
            fb = {
                "x": -mt4_current_pos['y'] / 1000.0, "y": (mt4_current_pos['z'] - MT4_Z_OFFSET) / 1000.0,
                "z": mt4_current_pos['x'] / 1000.0, "roll": mt4_current_pos['roll'],
                "gripper": mt4_current_pos['gripper'], "status": "Running"
            }
            sock_send = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock_send.sendto(json.dumps(fb).encode("utf-8"), (MT4_UNITY_IP, MT4_FEEDBACK_PORT))
            sock_send.close()
        except: pass

        self.outputs["Flow Out"] = True
        return "Flow Out"

def mt4_apply_limits():
    global mt4_target_goal, mt4_current_pos, ser, mt4_collision_lock_until
    if time.time() < mt4_collision_lock_until: return
    
    mt4_target_goal['x'] = max(MT4_LIMITS['min_x'], min(mt4_target_goal['x'], MT4_LIMITS['max_x']))
    mt4_target_goal['y'] = max(MT4_LIMITS['min_y'], min(mt4_target_goal['y'], MT4_LIMITS['max_y']))
    mt4_target_goal['z'] = max(MT4_LIMITS['min_z'], min(mt4_target_goal['z'], MT4_LIMITS['max_z']))
    mt4_target_goal['gripper'] = max(MT4_GRIPPER_MIN, min(mt4_target_goal['gripper'], MT4_GRIPPER_MAX))
    mt4_target_goal['roll'] = max(MT4_LIMITS['min_r'], min(mt4_target_goal['roll'], MT4_LIMITS['max_r']))

    if ser and ser.is_open:
        cmd = f"G0 X{mt4_target_goal['x']:.1f} Y{mt4_target_goal['y']:.1f} Z{mt4_target_goal['z']:.1f} A{mt4_target_goal['roll']:.1f}\nM3 S{int(mt4_target_goal['gripper'])}\n"
        try: ser.write(cmd.encode())
        except Exception: pass
        mt4_current_pos.update(mt4_target_goal)

def mt4_manual_control(axis, step):
    global mt4_manual_override_until, mt4_target_goal, mt4_current_pos
    mt4_manual_override_until = time.time() + 1.5
    mt4_target_goal[axis] = mt4_current_pos[axis] + step
    mt4_apply_limits()

def mt4_move_to_coord(x, y, z, roll, gripper):
    global mt4_manual_override_until, mt4_target_goal
    mt4_manual_override_until = time.time() + 2.0
    mt4_target_goal['x'] = float(x)
    mt4_target_goal['y'] = float(y)
    mt4_target_goal['z'] = float(z)
    mt4_target_goal['gripper'] = float(gripper)
    if roll is not None: mt4_target_goal['roll'] = float(roll)
    mt4_apply_limits()

def play_mt4_path_thread(filepath):
    global mt4_mode, mt4_target_goal, mt4_manual_override_until, mt4_collision_lock_until
    mt4_mode["playing"] = True
    mt4_manual_override_until = time.time() + 86400 
    try:
        with open(filepath, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if time.time() < mt4_collision_lock_until or not mt4_mode["playing"]: break
                mt4_target_goal['x'] = float(row['x'])
                mt4_target_goal['y'] = float(row['y'])
                mt4_target_goal['z'] = float(row['z'])
                mt4_target_goal['gripper'] = float(row['gripper'])
                mt4_target_goal['roll'] = float(row.get('roll', 0.0))
                mt4_apply_limits() 
                time.sleep(0.05)
    except Exception: pass
    mt4_mode["playing"] = False
    mt4_manual_override_until = time.time()

def toggle_mt4_record(custom_name=None):
    global mt4_record_f, mt4_record_writer, mt4_record_temp_name
    
    if mt4_mode["recording"]:
        mt4_mode["recording"] = False
        if mt4_record_f: mt4_record_f.close()
        if custom_name and mt4_record_temp_name:
            if not custom_name.endswith(".csv"): custom_name += ".csv"
            final_path = os.path.join("path_record", custom_name)
            try: os.rename(mt4_record_temp_name, final_path)
            except: pass
    else:
        mt4_mode["recording"] = True
        os.makedirs("path_record", exist_ok=True)
        fname = os.path.join("path_record", f"path_{time.strftime('%Y%m%d_%H%M%S')}.csv")
        mt4_record_temp_name = fname
        mt4_record_f = open(fname, 'w', newline='')
        mt4_record_writer = csv.writer(mt4_record_f)
        mt4_record_writer.writerow(['x', 'y', 'z', 'roll','gripper'])

def play_mt4_path(filename=None):
    if not filename or mt4_mode["playing"] or time.time() < mt4_collision_lock_until: return
    filepath = os.path.join("path_record", filename)
    if os.path.exists(filepath): 
        import threading
        threading.Thread(target=play_mt4_path_thread, args=(filepath,), daemon=True).start()

def mt4_background_logger_thread():
    global mt4_record_writer
    os.makedirs("result_log", exist_ok=True)
    log_filename = os.path.join("result_log", f"mt4_log_{time.strftime('%Y%m%d_%H%M%S')}.csv")
    with open(log_filename, 'w', newline='') as mt4_log_f:
        mt4_log_writer = csv.writer(mt4_log_f)
        mt4_log_writer.writerow(['timestamp', 'event', 'target_x', 'target_y', 'target_z', 'target_r', 'target_g', 'current_x', 'current_y', 'current_z', 'current_r', 'current_g'])
        while True:
            time.sleep(0.05)
            event_str = "TICK"
            if mt4_log_event_queue: event_str = mt4_log_event_queue.popleft()
            
            mt4_log_writer.writerow([
                time.time(), event_str, 
                mt4_target_goal['x'], mt4_target_goal['y'], mt4_target_goal['z'], mt4_target_goal['roll'], mt4_target_goal['gripper'], 
                mt4_current_pos['x'], mt4_current_pos['y'], mt4_current_pos['z'], mt4_current_pos['roll'], mt4_current_pos['gripper']
            ])
            mt4_log_f.flush()
            
            if mt4_mode["recording"] and mt4_record_writer:
                mt4_record_writer.writerow((mt4_current_pos['x'], mt4_current_pos['y'], mt4_current_pos['z'], mt4_current_pos['roll'], mt4_current_pos['gripper']))
                mt4_record_f.flush()

def init_mt4_serial():
    global ser
    try:
        ser = serial.Serial('/dev/ttyUSB0', 115200, timeout=0.05)
        mt4_dashboard["hw_link"] = "Online"
        time.sleep(2); ser.write(b"$H\r\n"); time.sleep(15); ser.write(b"M20\r\n"); ser.write(b"G90\r\n"); ser.write(b"G1 F2000\r\n"); time.sleep(1)
        ser.write(b"G0 X200 Y0 Z120 F2000\r\n"); ser.write(b"M3 S40\r\n") 
    except Exception: 
        mt4_dashboard["hw_link"] = "Simulation"
        ser = None

def mt4_homing_logic(): 
    import threading
    threading.Thread(target=mt4_homing_thread_func, daemon=True).start()

def mt4_homing_thread_func():
    global ser, mt4_manual_override_until, mt4_target_goal, mt4_current_pos, mt4_dashboard
    if ser:
        mt4_manual_override_until = time.time() + 20.0
        mt4_dashboard["status"] = "HOMING..."
        ser.write(b"$H\r\n"); time.sleep(15); ser.write(b"M20\r\n"); ser.write(b"G90\r\n"); ser.write(b"G1 F2000\r\n")
        mt4_target_goal.update({'x':200.0, 'y':0.0, 'z':120.0, 'roll':0.0, 'gripper':40.0})
        mt4_current_pos.update(mt4_target_goal)
        ser.write(b"G0 X200 Y0 Z120 A0 F2000\r\n"); ser.write(b"M3 S40\r\n")
        mt4_dashboard["status"] = "Idle"