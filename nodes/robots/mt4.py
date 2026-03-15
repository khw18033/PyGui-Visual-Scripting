import time
import math
import json
import serial
import os
import csv
import socket
from collections import deque
from nodes.base import BaseNode

# ==========================================
# ⚙️ MT4 전역 하드웨어 상태 관리 (Global State)
# ==========================================
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

# (Unity 기록용)
mt4_mode = {"recording": False, "playing": False}
mt4_log_event_queue = deque()


# ==========================================
# 🤖 1. MT4 메인 드라이버 노드 (하드웨어 통신 전담)
# ==========================================
class MT4DriverNode(BaseNode):
    def __init__(self, node_id: str):
        super().__init__(node_id, "MT4 Core Driver", "MT4_DRIVER")
        self.last_cmd = ""
        self.last_write_time = 0
        self.write_interval = 0.0

    def get_ui_schema(self):
        return [
            ("IN_FLOW", "Flow In", None),
            ("IN_DATA", "X", 200.0),
            ("IN_DATA", "Y", 0.0),
            ("IN_DATA", "Z", 120.0),
            ("IN_DATA", "Roll", 0.0),
            ("IN_DATA", "Gripper", 40.0),
            ("OUT_FLOW", "Flow Out", None)
        ]

    def get_settings_schema(self):
        return [
            ("smooth", 1.0),
            ("grip_spd", 50.0),
            ("roll_spd", 50.0)
        ]

    def execute(self):
        global mt4_current_pos, mt4_target_goal, mt4_manual_override_until, ser
        
        if time.time() < mt4_collision_lock_until:
            return "Flow Out"

        # 1. 입력 핀에서 데이터 읽어오기 (없으면 현재 타겟 유지)
        inputs = {
            'x': self.inputs.get("X", mt4_target_goal['x']),
            'y': self.inputs.get("Y", mt4_target_goal['y']),
            'z': self.inputs.get("Z", mt4_target_goal['z']),
            'roll': self.inputs.get("Roll", mt4_target_goal['roll']),
            'gripper': self.inputs.get("Gripper", mt4_target_goal['gripper'])
        }

        # 2. 강제 조작(수동) 중이 아니라면 목표값 갱신
        if time.time() > mt4_manual_override_until:
            for k in ['x', 'y', 'z', 'roll', 'gripper']:
                if inputs[k] is not None:
                    mt4_target_goal[k] = float(inputs[k])

        # 3. 보간 연산 (Smooth & Speed)
        smooth = 1.0 if time.time() < mt4_manual_override_until else max(0.01, min(self.settings.get('smooth', 1.0), 1.0))
        
        dx = mt4_target_goal['x'] - mt4_current_pos['x']
        dy = mt4_target_goal['y'] - mt4_current_pos['y']
        dz = mt4_target_goal['z'] - mt4_current_pos['z']
        
        nx = mt4_current_pos['x'] + dx * smooth if not (abs(dx)<0.5 and abs(dy)<0.5 and abs(dz)<0.5) else mt4_target_goal['x']
        ny = mt4_current_pos['y'] + dy * smooth if not (abs(dx)<0.5 and abs(dy)<0.5 and abs(dz)<0.5) else mt4_target_goal['y']
        nz = mt4_current_pos['z'] + dz * smooth if not (abs(dx)<0.5 and abs(dy)<0.5 and abs(dz)<0.5) else mt4_target_goal['z']

        g_spd = float(self.settings.get('grip_spd', 50.0)) * 0.1
        r_spd = float(self.settings.get('roll_spd', 50.0)) * 0.1

        # Roll 및 Gripper 속도 제어
        dr_err = mt4_target_goal['roll'] - mt4_current_pos['roll']
        nr = mt4_current_pos['roll'] + math.copysign(r_spd, dr_err) if abs(dr_err) > r_spd else mt4_target_goal['roll']
        
        dg_err = mt4_target_goal['gripper'] - mt4_current_pos['gripper']
        ng = mt4_current_pos['gripper'] + math.copysign(g_spd, dg_err) if abs(dg_err) > g_spd else mt4_target_goal['gripper']

        # 4. Limit 안전장치 적용
        nx = max(MT4_LIMITS['min_x'], min(nx, MT4_LIMITS['max_x']))
        ny = max(MT4_LIMITS['min_y'], min(ny, MT4_LIMITS['max_y']))
        nz = max(MT4_LIMITS['min_z'], min(nz, MT4_LIMITS['max_z']))
        nr = max(MT4_LIMITS['min_r'], min(nr, MT4_LIMITS['max_r']))
        ng = max(MT4_GRIPPER_MIN, min(ng, MT4_GRIPPER_MAX))

        mt4_current_pos.update({'x': nx, 'y': ny, 'z': nz, 'roll': nr, 'gripper': ng})

        # 5. 시리얼 통신 전송
        if time.time() - self.last_write_time >= self.write_interval:
            cmd = f"G0 X{nx:.1f} Y{ny:.1f} Z{nz:.1f} A{nr:.1f}\nM3 S{int(ng)}\n"
            if cmd != self.last_cmd:
                if ser and ser.is_open:
                    try:
                        ser.write(cmd.encode())
                        self.last_write_time = time.time()
                    except:
                        pass
                self.last_cmd = cmd

        return "Flow Out"


# ==========================================
# 🔧 2. Sim-to-Real 보정 노드들 (새로운 아키텍처 적용)
# ==========================================
class MT4GravitySagNode(BaseNode):
    def __init__(self, node_id: str):
        super().__init__(node_id, "Gravity Sag Comp (StR)", "MT4_SAG")

    def get_ui_schema(self):
        return [
            ("IN_DATA", "X In", 0.0),
            ("IN_DATA", "Z In", 0.0),
            ("OUT_DATA", "Z Out (Comp)", None)
        ]

    def get_settings_schema(self):
        return [("sag_factor", 0.05)]

    def execute(self):
        x_val = self.inputs.get("X In")
        z_val = self.inputs.get("Z In")
        
        if x_val is not None and z_val is not None:
            sag_comp = float(x_val) * float(self.settings.get('sag_factor', 0.0))
            self.outputs["Z Out (Comp)"] = float(z_val) + sag_comp
        elif z_val is not None:
            self.outputs["Z Out (Comp)"] = float(z_val)
        return None

class MT4CalibrationNode(BaseNode):
    def __init__(self, node_id: str):
        super().__init__(node_id, "3D Calibration (StR)", "MT4_CALIB")

    def get_ui_schema(self):
        return [
            ("IN_DATA", "X In", 0.0), ("IN_DATA", "Y In", 0.0), ("IN_DATA", "Z In", 0.0),
            ("OUT_DATA", "X Out", None), ("OUT_DATA", "Y Out", None), ("OUT_DATA", "Z Out", None)
        ]

    def get_settings_schema(self):
        return [("x_offset", 0.0), ("y_offset", 0.0), ("z_offset", 0.0), ("scale", 1.0)]

    def execute(self):
        x_val = self.inputs.get("X In")
        y_val = self.inputs.get("Y In")
        z_val = self.inputs.get("Z In")
        scale = float(self.settings.get('scale', 1.0))
        
        if x_val is not None: self.outputs["X Out"] = (float(x_val) * scale) + float(self.settings.get('x_offset', 0.0))
        if y_val is not None: self.outputs["Y Out"] = (float(y_val) * scale) + float(self.settings.get('y_offset', 0.0))
        if z_val is not None: self.outputs["Z Out"] = (float(z_val) * scale) + float(self.settings.get('z_offset', 0.0))
        return None

class MT4TooltipNode(BaseNode):
    def __init__(self, node_id: str):
        super().__init__(node_id, "Tool-tip Offset (StR)", "MT4_TOOLTIP")

    def get_ui_schema(self):
        return [
            ("IN_DATA", "X In", 0.0), ("IN_DATA", "Z In", 0.0),
            ("OUT_DATA", "X Out (Comp)", None), ("OUT_DATA", "Z Out (Comp)", None)
        ]

    def get_settings_schema(self):
        return [("tool_length", 0.0), ("tool_angle", 0.0)]

    def execute(self):
        x_val = self.inputs.get("X In")
        z_val = self.inputs.get("Z In")
        length = float(self.settings.get('tool_length', 0.0))
        angle_deg = float(self.settings.get('tool_angle', 0.0))
        
        if x_val is not None and z_val is not None:
            dx = length * math.cos(math.radians(angle_deg))
            dz = length * math.sin(math.radians(angle_deg))
            self.outputs["X Out (Comp)"] = float(x_val) + dx
            self.outputs["Z Out (Comp)"] = float(z_val) + dz
        return None

class MT4BacklashNode(BaseNode):
    def __init__(self, node_id: str):
        super().__init__(node_id, "Backlash & Inertia (StR)", "MT4_BACKLASH")
        self.internal_pos = None

    def get_ui_schema(self):
        return [
            ("IN_DATA", "X In", 0.0), ("IN_DATA", "Y In", 0.0), ("IN_DATA", "Z In", 0.0),
            ("OUT_DATA", "X Out", None), ("OUT_DATA", "Y Out", None), ("OUT_DATA", "Z Out", None)
        ]

    def get_settings_schema(self):
        return [("decel_dist", 15.0), ("stop_delay", 100.0)]

    def execute(self):
        tx = self.inputs.get("X In")
        ty = self.inputs.get("Y In")
        tz = self.inputs.get("Z In")
        
        if tx is None or ty is None or tz is None: return None
        if self.internal_pos is None: self.internal_pos = [float(tx), float(ty), float(tz)]
            
        decel_dist = max(1.0, float(self.settings.get('decel_dist', 15.0)))
        delay_factor = max(1.0, float(self.settings.get('stop_delay', 100.0)))
        
        dx = float(tx) - self.internal_pos[0]
        dy = float(ty) - self.internal_pos[1]
        dz = float(tz) - self.internal_pos[2]
        dist = math.sqrt(dx**2 + dy**2 + dz**2)
        
        speed = 1.0 if dist > decel_dist else max(0.01, (dist / decel_dist) * (50.0 / delay_factor))
        
        self.internal_pos[0] += dx * speed
        self.internal_pos[1] += dy * speed
        self.internal_pos[2] += dz * speed
        
        self.outputs["X Out"] = self.internal_pos[0]
        self.outputs["Y Out"] = self.internal_pos[1]
        self.outputs["Z Out"] = self.internal_pos[2]
        return None

# ==========================================
# 🎮 3. 외부 통신 및 제어 로직 노드 (Unity & Keyboard)
# ==========================================
class MT4UnityNode(BaseNode):
    def __init__(self, node_id: str):
        super().__init__(node_id, "Unity Logic (MT4)", "MT4_UNITY")
        self.last_processed_json = ""
        
    def get_ui_schema(self): 
        # ★ 해결: 누락되었던 OUT_DATA (Target X~Grip) 핀들을 100% 복구했습니다.
        return [
            ("IN_FLOW", "Flow In", None), ("IN_DATA", "JSON", ""),
            ("OUT_DATA", "Target X", None), ("OUT_DATA", "Target Y", None), ("OUT_DATA", "Target Z", None),
            ("OUT_DATA", "Target Roll", None), ("OUT_DATA", "Target Grip", None), 
            ("OUT_FLOW", "Flow Out", None)
        ]
        
    def get_settings_schema(self): return []
    
    def execute(self):
        global mt4_collision_lock_until, mt4_target_goal, mt4_mode
        raw_json = self.inputs.get("JSON", "")
        
        is_new_msg = (raw_json and raw_json != self.last_processed_json)
        if is_new_msg: self.last_processed_json = raw_json

        is_overridden = (time.time() < mt4_manual_override_until) or mt4_mode.get("playing", False)

        if is_overridden:
            self.outputs["Target X"] = mt4_target_goal['x']
            self.outputs["Target Y"] = mt4_target_goal['y']
            self.outputs["Target Z"] = mt4_target_goal['z']
            self.outputs["Target Roll"] = mt4_target_goal['roll']
            self.outputs["Target Grip"] = mt4_target_goal['gripper']
        else:
            if is_new_msg:
                try:
                    parsed = json.loads(raw_json)
                    msg_type = parsed.get("type", "MOVE")
                    if msg_type == "CMD":
                        val = parsed.get("val", "")
                        if val == "COLLISION":
                            mt4_collision_lock_until = time.time() + 2.0 
                            if ser and ser.is_open: ser.write(b"!") 
                        elif val == "START_REC":
                            if not mt4_mode["recording"]: toggle_mt4_record()
                        elif val.startswith("STOP_REC:"):
                            fname = val.split(":")[1]
                            if mt4_mode["recording"]: toggle_mt4_record(custom_name=fname)
                        elif val.startswith("PLAY:"):
                            fname = val.split(":")[1]
                            play_mt4_path(filename=fname)
                        elif val == "LOG_SUCCESS": mt4_log_event_queue.append("SUCCESS")
                        elif val == "LOG_FAIL": mt4_log_event_queue.append("FAIL")
                    elif msg_type == "MOVE" and time.time() > mt4_collision_lock_until:
                        # 파이프라인으로 안전하게 다음 노드로 타겟값 넘기기
                        if 'z' in parsed: self.outputs["Target X"] = float(parsed['z']) * 1000.0
                        if 'x' in parsed: self.outputs["Target Y"] = -float(parsed['x']) * 1000.0
                        if 'y' in parsed: self.outputs["Target Z"] = (float(parsed['y']) * 1000.0) + MT4_Z_OFFSET
                        if 'roll' in parsed: self.outputs["Target Roll"] = float(parsed['roll'])
                        if 'gripper' in parsed: self.outputs["Target Grip"] = float(parsed['gripper']) 
                except: pass 
        return "Flow Out"
    
from core.input_manager import global_input_manager  # InputManager 수입

# ==========================================
# ⌨️ MT4 키보드 제어 노드 (InputManager 연동)
# ==========================================
class MT4KeyboardNode(BaseNode):
    def __init__(self, node_id: str):
        super().__init__(node_id, "Keyboard (MT4)", "MT4_KEYBOARD")
        self.last_input_time = 0.0
        self.cooldown = 0.2

    def get_ui_schema(self):
        return [
            ("IN_FLOW", "Flow In", None),
            ("OUT_DATA", "Target X", None),
            ("OUT_DATA", "Target Y", None),
            ("OUT_DATA", "Target Z", None),
            ("OUT_DATA", "Target Roll", None),
            ("OUT_DATA", "Target Grip", None),
            ("OUT_FLOW", "Flow Out", None)
        ]

    def get_settings_schema(self):
        return [
            ("step_size", 10.0),
            ("grip_step", 5.0),
            ("roll_step", 5.0)
        ]

    def execute(self):
        global mt4_manual_override_until, mt4_target_goal
        
        # 쿨다운 체크 (너무 빠른 연속 입력 방지)
        if time.time() - self.last_input_time < self.cooldown:
            return "Flow Out"

        dx = dy = dz = dg = dr = 0
        
        # DPG UI 코드 없이, 외부의 InputManager 장부에서 순수 상태만 읽어옵니다.
        if global_input_manager.get_key('W'): dx = 1
        if global_input_manager.get_key('S'): dx = -1
        if global_input_manager.get_key('A'): dy = 1
        if global_input_manager.get_key('D'): dy = -1
        
        if global_input_manager.get_key('Q'): dz = 1
        if global_input_manager.get_key('E'): dz = -1
        
        if global_input_manager.get_key('J'): dg = 1
        if global_input_manager.get_key('U'): dg = -1
        
        # 첨부하신 코드의 핵심인 Roll 제어 (Z/X 키)
        if global_input_manager.get_key('Z'): dr = 1
        if global_input_manager.get_key('X'): dr = -1

        # 입력이 하나라도 발생했다면
        if any([dx, dy, dz, dg, dr]):
            self.last_input_time = time.time()
            mt4_manual_override_until = time.time() + 0.5
            
            # 노드의 Settings에서 보폭값(Step Size)을 읽어와서 곱해줍니다.
            step = float(self.settings.get("step_size", 10.0))
            g_step = float(self.settings.get("grip_step", 5.0))
            r_step = float(self.settings.get("roll_step", 5.0))

            mt4_target_goal['x'] += dx * step
            mt4_target_goal['y'] += dy * step
            mt4_target_goal['z'] += dz * step
            mt4_target_goal['gripper'] += dg * g_step
            mt4_target_goal['roll'] += dr * r_step

        # 다음 노드로 전달할 출력 데이터 저장
        self.outputs["Target X"] = mt4_target_goal['x']
        self.outputs["Target Y"] = mt4_target_goal['y']
        self.outputs["Target Z"] = mt4_target_goal['z']
        self.outputs["Target Roll"] = mt4_target_goal['roll']
        self.outputs["Target Grip"] = mt4_target_goal['gripper']
        
        return "Flow Out"
    
class MT4CommandActionNode(BaseNode):
    def __init__(self, node_id: str): super().__init__(node_id, "MT4 Action", "MT4_ACTION")
    def get_ui_schema(self): return [
        ("IN_FLOW", "Flow In", None),
        ("IN_DATA", "X / Grip", 0.0), ("IN_DATA", "Y", 0.0), ("IN_DATA", "Z", 0.0),
        ("OUT_FLOW", "Flow Out", None)
    ]
    def get_settings_schema(self): return [("mode", 1.0)] 
    
    def execute(self):
        global mt4_target_goal
        mode = int(self.settings.get("mode", 1.0))
        v1 = float(self.inputs.get("X / Grip", 0.0))
        v2 = float(self.inputs.get("Y", 0.0))
        v3 = float(self.inputs.get("Z", 0.0))

        if mode == 1: # Move Relative
            mt4_target_goal['x'] += v1; mt4_target_goal['y'] += v2; mt4_target_goal['z'] += v3
        elif mode == 2: # Move Absolute
            mt4_target_goal['x'] = v1; mt4_target_goal['y'] = v2; mt4_target_goal['z'] = v3
        elif mode == 3: # Set Grip
            mt4_target_goal['gripper'] = v1
        
        return "Flow Out"

class UDPReceiverNode(BaseNode):
    def __init__(self, node_id: str): 
        super().__init__(node_id, "UDP Receiver", "UDP_RECV")
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); self.sock.setblocking(False)
        self.is_bound = False; self.current_port = 0; self.last_data = ""

    def get_ui_schema(self): return [("IN_FLOW", "Flow In", None), ("OUT_DATA", "JSON Out", None), ("OUT_FLOW", "Flow Out", None)]
    
    # ★ 해결: 6000.0(float)이 아니라 6000(int)으로 기본값을 주어 소수점을 없앱니다!
    def get_settings_schema(self): return [("port", 6000), ("ip", "192.168.50.63")]
    
    def execute(self):
        port = int(self.settings.get("port", 6000))
        if not self.is_bound or self.current_port != port:
            try:
                self.sock.close()
                self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); self.sock.setblocking(False)
                self.sock.bind(('0.0.0.0', port))
                self.is_bound = True; self.current_port = port
            except: self.is_bound = True

        try:
            while True:
                data, _ = self.sock.recvfrom(4096)
                decoded = data.decode()
                
                now = time.time()
                mt4_dashboard["latency"] = (now - mt4_dashboard.get("last_pkt_time", now)) * 1000.0 
                mt4_dashboard["last_pkt_time"] = now
                mt4_dashboard["status"] = "Connected"
                
                if decoded != self.last_data:
                    self.outputs["JSON Out"] = decoded; self.last_data = decoded
        except: pass
        return "Flow Out"
    
# ==========================================
# 📊 MT4 Dashboard Callbacks & Threads (Answer_code.py 100% 이식)
# ==========================================
import dearpygui.dearpygui as dpg

def mt4_apply_limits():
    global mt4_target_goal, mt4_current_pos, ser, mt4_collision_lock_until
    if time.time() < mt4_collision_lock_until: return
    
    mt4_target_goal['x'] = max(MT4_LIMITS['min_x'], min(mt4_target_goal['x'], MT4_LIMITS['max_x']))
    mt4_target_goal['y'] = max(MT4_LIMITS['min_y'], min(mt4_target_goal['y'], MT4_LIMITS['max_y']))
    mt4_target_goal['z'] = max(MT4_LIMITS['min_z'], min(mt4_target_goal['z'], MT4_LIMITS['max_z']))
    mt4_target_goal['gripper'] = max(MT4_GRIPPER_MIN, min(mt4_target_goal['gripper'], MT4_GRIPPER_MAX))
    mt4_target_goal['roll'] = max(MT4_LIMITS['min_r'], min(mt4_target_goal['roll'], MT4_LIMITS['max_r']))

    # 노드 엔진(DriverNode)이 멈춰있어도 즉각 시리얼 전송!
    if ser and ser.is_open:
        cmd = f"G0 X{mt4_target_goal['x']:.1f} Y{mt4_target_goal['y']:.1f} Z{mt4_target_goal['z']:.1f} A{mt4_target_goal['roll']:.1f}\nM3 S{int(mt4_target_goal['gripper'])}\n"
        try:
            ser.write(cmd.encode())
        except Exception:
            pass
        mt4_current_pos.update(mt4_target_goal)

def mt4_manual_control_callback(sender, app_data, user_data):
    global mt4_manual_override_until, mt4_target_goal, mt4_current_pos
    mt4_manual_override_until = time.time() + 1.5
    axis, step = user_data
    mt4_target_goal[axis] = mt4_current_pos[axis] + step
    mt4_apply_limits()

def mt4_move_to_coord_callback(sender, app_data, user_data):
    global mt4_manual_override_until, mt4_target_goal
    import dearpygui.dearpygui as dpg
    mt4_manual_override_until = time.time() + 2.0
    mt4_target_goal['x'] = float(dpg.get_value("input_x"))
    mt4_target_goal['y'] = float(dpg.get_value("input_y"))
    mt4_target_goal['z'] = float(dpg.get_value("input_z"))
    mt4_target_goal['gripper'] = float(dpg.get_value("input_g"))
    if dpg.does_item_exist("input_r"): 
        mt4_target_goal['roll'] = float(dpg.get_value("input_r"))
    mt4_apply_limits()

def play_mt4_path_thread(filepath):
    global mt4_mode, mt4_target_goal, mt4_manual_override_until, mt4_collision_lock_until
    import csv
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
                
                mt4_apply_limits()  # 재생 중에도 틱마다 다이렉트 전송!
                time.sleep(0.05)
    except Exception: pass
    mt4_mode["playing"] = False
    mt4_manual_override_until = time.time()

def toggle_mt4_record(custom_name=None):
    global mt4_record_f, mt4_record_writer, mt4_record_temp_name
    
    def get_mt4_paths():
        if not os.path.exists("path_record"): return []
        return [f for f in os.listdir("path_record") if f.endswith(".csv")]

    if mt4_mode["recording"]:
        mt4_mode["recording"] = False
        if mt4_record_f: mt4_record_f.close()
        if not custom_name and dpg.does_item_exist("path_name_input"): custom_name = dpg.get_value("path_name_input")
        if custom_name and mt4_record_temp_name:
            if not custom_name.endswith(".csv"): custom_name += ".csv"
            final_path = os.path.join("path_record", custom_name)
            try: os.rename(mt4_record_temp_name, final_path)
            except: pass
        dpg.set_item_label("btn_mt4_record", "Start Recording")
        if dpg.does_item_exist("combo_mt4_path"): dpg.configure_item("combo_mt4_path", items=get_mt4_paths())
    else:
        mt4_mode["recording"] = True
        os.makedirs("path_record", exist_ok=True)
        fname = os.path.join("path_record", f"path_{time.strftime('%Y%m%d_%H%M%S')}.csv")
        mt4_record_temp_name = fname
        mt4_record_f = open(fname, 'w', newline='')
        mt4_record_writer = csv.writer(mt4_record_f)
        mt4_record_writer.writerow(['x', 'y', 'z', 'roll','gripper'])
        dpg.set_item_label("btn_mt4_record", "Stop Recording")

def play_mt4_path(sender=None, app_data=None, user_data=None, filename=None):
    if not filename: filename = dpg.get_value("combo_mt4_path")
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
        import serial
        ser = serial.Serial('/dev/ttyUSB0', 115200, timeout=0.05)
        mt4_dashboard["hw_link"] = "Online"
        time.sleep(2); ser.write(b"$H\r\n"); time.sleep(15); ser.write(b"M20\r\n"); ser.write(b"G90\r\n"); ser.write(b"G1 F2000\r\n"); time.sleep(1)
        ser.write(b"G0 X200 Y0 Z120 F2000\r\n"); ser.write(b"M3 S40\r\n") 
    except Exception: 
        mt4_dashboard["hw_link"] = "Simulation"
        ser = None

# 👇 파일 맨 끝에 붙여넣기 (호밍 기능)
def mt4_homing_callback(sender, app_data, user_data): 
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