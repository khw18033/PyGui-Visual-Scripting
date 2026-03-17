import os
import time
import socket
import json
import csv
import math
import threading
import serial
from datetime import datetime
from collections import deque
from nodes.base import BaseNode, BaseRobotDriver
from core.engine import generate_uuid, PortType, write_log, HwStatus, node_registry

# --- MT4 Globals ---
ser = None 
mt4_current_pos = {'x': 200.0, 'y': 0.0, 'z': 120.0, 'roll': 0.0, 'gripper': 40.0}
mt4_target_goal = {'x': 200.0, 'y': 0.0, 'z': 120.0, 'roll': 0.0, 'gripper': 40.0}
mt4_manual_override_until = 0.0 
mt4_dashboard = {"status": "Idle", "hw_link": HwStatus.OFFLINE, "latency": 0.0, "last_pkt_time": 0.0}

PATH_DIR = "path_record"
LOG_DIR = "result_log"
os.makedirs(PATH_DIR, exist_ok=True); os.makedirs(LOG_DIR, exist_ok=True)

mt4_mode = {"recording": False, "playing": False}
mt4_collision_lock_until = 0.0
mt4_record_f = None; mt4_record_writer = None; mt4_record_temp_name = ""
mt4_log_event_queue = deque()

MT4_UNITY_IP = "192.168.50.63"
MT4_FEEDBACK_PORT = 5005
MT4_LIMITS = {'min_x': 200, 'max_x': 280, 'min_y': -200, 'max_y': 200, 'min_z': 0, 'max_z': 280, 'min_r': -180.0, 'max_r': 180.0}
MT4_GRIPPER_MIN = 30.0; MT4_GRIPPER_MAX = 60.0
MT4_Z_OFFSET = 90.0

def get_mt4_paths(): 
    return [f for f in os.listdir(PATH_DIR) if f.endswith(".csv")]

def send_unity_ui(msg_type, extra_data):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(f"type:{msg_type},extra:{extra_data}".encode('utf-8'), (MT4_UNITY_IP, 5007))
    except: pass

def sync_manual_to_node_state():
    for node in node_registry.values():
        if node.type_str == "MT4_DRIVER":
            node.state['x'] = mt4_target_goal['x']
            node.state['y'] = mt4_target_goal['y']
            node.state['z'] = mt4_target_goal['z']
            node.state['gripper'] = mt4_target_goal['gripper'] 
            node.state['roll'] = mt4_target_goal['roll']

def init_mt4_serial():
    global ser
    try:
        ser = serial.Serial('/dev/ttyUSB0', 115200, timeout=0.05)
        mt4_dashboard["hw_link"] = HwStatus.ONLINE
        write_log("System: MT4 Connected")
        time.sleep(2)
        ser.write(b"$H\r\n")
        time.sleep(15)
        ser.write(b"M20\r\n")
        ser.write(b"G90\r\n")
        ser.write(b"G1 F2000\r\n")
        time.sleep(1)
        ser.write(b"G0 X200 Y0 Z120 F2000\r\n")
        ser.write(b"M3 S40\r\n") 
    except Exception as e: 
        mt4_dashboard["hw_link"] = HwStatus.SIMULATION
        write_log(f"MT4 Sim Mode ({e})")
        ser = None

def auto_reconnect_mt4_thread():
    global ser
    while True:
        if ser is None and os.path.exists('/dev/ttyUSB0'):
            try: init_mt4_serial() 
            except: pass
        time.sleep(3) 

def mt4_background_logger_thread():
    global mt4_record_writer
    log_filename = os.path.join(LOG_DIR, f"mt4_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
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

def mt4_homing_callback(sender, app_data, user_data): 
    threading.Thread(target=mt4_homing_thread_func, daemon=True).start()

def mt4_homing_thread_func():
    global ser, mt4_manual_override_until, mt4_target_goal, mt4_current_pos
    if ser:
        mt4_manual_override_until = time.time() + 20.0
        mt4_dashboard["status"] = "HOMING..."
        write_log("Homing...")
        ser.write(b"$H\r\n")
        time.sleep(15)
        ser.write(b"M20\r\n")
        ser.write(b"G90\r\n")
        ser.write(b"G1 F2000\r\n")
        mt4_target_goal.update({'x':200.0, 'y':0.0, 'z':120.0, 'roll':0.0, 'gripper':40.0})
        mt4_current_pos.update(mt4_target_goal)
        ser.write(b"G0 X200 Y0 Z120 A0 F2000\r\n")
        ser.write(b"M3 S40\r\n")
        mt4_dashboard["status"] = "Idle"
        write_log("Homing Done")
        sync_manual_to_node_state()

def toggle_mt4_record(custom_name=None):
    from ui.dpg_manager import get_ui_value, set_ui_value, update_mt4_path_combo
    global mt4_record_f, mt4_record_writer, mt4_record_temp_name
    if mt4_mode["recording"]:
        mt4_mode["recording"] = False
        if mt4_record_f: mt4_record_f.close()
        if not custom_name: custom_name = get_ui_value("path_name_input")
        if custom_name and mt4_record_temp_name:
            if not custom_name.endswith(".csv"): custom_name += ".csv"
            final_path = os.path.join(PATH_DIR, custom_name)
            try: os.rename(mt4_record_temp_name, final_path)
            except: pass
        set_ui_value("btn_mt4_record_label", "Start Recording")
        update_mt4_path_combo(get_mt4_paths())
        write_log(f"Path Saved: {custom_name}")
        send_unity_ui("STATUS", f"저장 완료: {custom_name}")
        send_unity_ui("FILE_LIST", f"[{'|'.join(get_mt4_paths())}]")
    else:
        mt4_mode["recording"] = True
        fname = os.path.join(PATH_DIR, f"path_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
        mt4_record_temp_name = fname
        mt4_record_f = open(fname, 'w', newline='')
        mt4_record_writer = csv.writer(mt4_record_f)
        mt4_record_writer.writerow(['x', 'y', 'z', 'roll','gripper'])
        set_ui_value("btn_mt4_record_label", "Stop Recording")
        write_log("Path Recording Started.")
        send_unity_ui("STATUS", "경로 녹화 시작...")

def play_mt4_path(sender=None, app_data=None, user_data=None, filename=None):
    from ui.dpg_manager import get_ui_value
    if not filename: filename = get_ui_value("combo_mt4_path")
    if not filename or mt4_mode["playing"] or time.time() < mt4_collision_lock_until: return
    filepath = os.path.join(PATH_DIR, filename)
    if os.path.exists(filepath): 
        threading.Thread(target=play_mt4_path_thread, args=(filepath,), daemon=True).start()

def mt4_apply_limits():
    global mt4_target_goal
    if time.time() < mt4_collision_lock_until: return
    mt4_target_goal['x'] = max(MT4_LIMITS['min_x'], min(mt4_target_goal['x'], MT4_LIMITS['max_x']))
    mt4_target_goal['y'] = max(MT4_LIMITS['min_y'], min(mt4_target_goal['y'], MT4_LIMITS['max_y']))
    mt4_target_goal['z'] = max(MT4_LIMITS['min_z'], min(mt4_target_goal['z'], MT4_LIMITS['max_z']))
    mt4_target_goal['gripper'] = max(MT4_GRIPPER_MIN, min(mt4_target_goal['gripper'], MT4_GRIPPER_MAX))
    mt4_target_goal['roll'] = max(MT4_LIMITS['min_r'], min(mt4_target_goal['roll'], MT4_LIMITS['max_r']))

    if ser and ser.is_open:
        cmd = f"G0 X{mt4_target_goal['x']:.1f} Y{mt4_target_goal['y']:.1f} Z{mt4_target_goal['z']:.1f} A{mt4_target_goal['roll']:.1f}\nM3 S{int(mt4_target_goal['gripper'])} \n"
        ser.write(cmd.encode())
        mt4_current_pos.update(mt4_target_goal)
    sync_manual_to_node_state()

def play_mt4_path_thread(filepath):
    global mt4_mode, mt4_target_goal, mt4_manual_override_until
    mt4_mode["playing"] = True
    mt4_manual_override_until = time.time() + 86400 
    write_log(f"Playing path: {os.path.basename(filepath)}")
    send_unity_ui("STATUS", f"재생 중: {os.path.basename(filepath)}")
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
    except Exception as e: 
        write_log(f"Play Error: {e}")
    mt4_mode["playing"] = False
    mt4_manual_override_until = time.time()
    send_unity_ui("STATUS", "경로 재생 완료")


# --- Nodes Implementation ---

class MT4RobotDriver(BaseRobotDriver):
    def __init__(self): 
        self.last_cmd = ""
        self.last_write_time = 0
        self.write_interval = 0.0
        self.last_inputs = {}

    def get_ui_schema(self): 
        return [('x', "X", 200.0), ('y', "Y", 0.0), ('z', "Z", 120.0), ('roll', "R", 0.0), ('gripper', "G", 40.0)]
        
    def get_settings_schema(self): 
        return [('smooth', "Smth", 1.0), ('grip_spd', "G_Spd", 50.0), ('roll_spd', "R_Spd", 50.0)]
    
    def execute_command(self, inputs, settings):
        global mt4_current_pos, mt4_target_goal, mt4_manual_override_until, ser
        if time.time() < mt4_collision_lock_until: return 
        
        inputs_changed = False
        for key, _, _ in self.get_ui_schema():
            val = inputs.get(key)
            if val is not None:
                if abs(float(val) - mt4_target_goal.get(key, 0.0)) > 0.001:
                    inputs_changed = True
                    self.last_inputs[key] = float(val)

        if time.time() > mt4_manual_override_until and inputs_changed:
            for key, _, _ in self.get_ui_schema():
                if inputs.get(key) is not None: mt4_target_goal[key] = float(inputs[key])
                
        # If manual override is active, keep updating last_inputs to avoid snaps after expiration
        if time.time() < mt4_manual_override_until:
            for key, _, _ in self.get_ui_schema():
                self.last_inputs[key] = mt4_target_goal.get(key, self.last_inputs.get(key, 0.0))
                
        smooth = 1.0 if time.time() < mt4_manual_override_until else max(0.01, min(settings.get('smooth', 1.0), 1.0))
        dx = mt4_target_goal['x'] - mt4_current_pos['x']
        dy = mt4_target_goal['y'] - mt4_current_pos['y']
        dz = mt4_target_goal['z'] - mt4_current_pos['z']
        nx = mt4_current_pos['x'] + dx * smooth if not (abs(dx)<0.5 and abs(dy)<0.5 and abs(dz)<0.5) else mt4_target_goal['x']
        ny = mt4_current_pos['y'] + dy * smooth if not (abs(dx)<0.5 and abs(dy)<0.5 and abs(dz)<0.5) else mt4_target_goal['y']
        nz = mt4_current_pos['z'] + dz * smooth if not (abs(dx)<0.5 and abs(dy)<0.5 and abs(dz)<0.5) else mt4_target_goal['z']
        
        g_spd = float(settings.get('grip_spd', 5.0)) * 0.1
        r_spd = float(settings.get('roll_spd', 5.0)) * 0.1

        dg_err = mt4_target_goal['gripper'] - mt4_current_pos['gripper']
        ng = mt4_current_pos['gripper'] + math.copysign(g_spd, dg_err) if abs(dg_err) > g_spd else mt4_target_goal['gripper']
        ng = max(MT4_GRIPPER_MIN, min(ng, MT4_GRIPPER_MAX))
        
        mt4_target_goal['roll'] = max(MT4_LIMITS['min_r'], min(mt4_target_goal['roll'], MT4_LIMITS['max_r']))
        dr_err = mt4_target_goal['roll'] - mt4_current_pos['roll']
        nr = mt4_current_pos['roll'] + math.copysign(r_spd, dr_err) if abs(dr_err) > r_spd else mt4_target_goal['roll']
        
        nx = max(MT4_LIMITS['min_x'], min(nx, MT4_LIMITS['max_x']))
        ny = max(MT4_LIMITS['min_y'], min(ny, MT4_LIMITS['max_y']))
        nz = max(MT4_LIMITS['min_z'], min(nz, MT4_LIMITS['max_z']))
        new_state = {'x': nx, 'y': ny, 'z': nz, 'gripper': ng, 'roll': nr}
        
        if time.time() - self.last_write_time >= self.write_interval:
            cmd = f"G0 X{nx:.1f} Y{ny:.1f} Z{nz:.1f} A{nr:.1f}\nM3 S{int(ng)}\n"
            if cmd != self.last_cmd:
                try: 
                    if ser and ser.is_open: 
                        ser.write(cmd.encode())
                        self.last_write_time = time.time()
                except: 
                    mt4_dashboard["hw_link"] = HwStatus.OFFLINE
                self.last_cmd = cmd
        mt4_current_pos.update(new_state)
        return new_state


class UniversalRobotNode(BaseNode):
    def __init__(self, node_id, driver): 
        super().__init__(node_id, "MT4 Driver", "MT4_DRIVER")
        self.driver = driver
        self.in_pins = {}; self.setting_pins = {}

        for k, lbl, def_v in self.driver.get_ui_schema():
            aid = generate_uuid()
            self.inputs[aid] = PortType.DATA
            self.in_pins[k] = aid
            self.state[k] = def_v
        for k, lbl, def_v in self.driver.get_settings_schema():
            aid = generate_uuid()
            self.inputs[aid] = PortType.DATA
            self.setting_pins[k] = aid
            self.state[k] = def_v
            
        self.out_flow = generate_uuid()
        self.outputs[self.out_flow] = PortType.FLOW
        
    def execute(self):
        inputs = {k: self.fetch_input_data(aid) for k, aid in self.in_pins.items()}
        settings = {k: self.fetch_input_data(aid) for k, aid in self.setting_pins.items()}
        
        for k in inputs:
            if inputs[k] is None: 
                inputs[k] = self.state.get(k, 0.0)
                
        for k in settings:
            if settings[k] is None: 
                settings[k] = self.state.get(k, 1.0)
            
        new_state = self.driver.execute_command(inputs, settings)

        if new_state:
            for k, v in new_state.items(): self.state[k] = v

        return self.out_flow


class MT4CommandActionNode(BaseNode):
    def __init__(self, node_id): 
        super().__init__(node_id, "MT4 Action", "MT4_ACTION")
        self.in_val1 = generate_uuid(); self.inputs[self.in_val1] = PortType.DATA
        self.in_val2 = generate_uuid(); self.inputs[self.in_val2] = PortType.DATA
        self.in_val3 = generate_uuid(); self.inputs[self.in_val3] = PortType.DATA
        self.out_flow = generate_uuid(); self.outputs[self.out_flow] = PortType.FLOW
        
    def execute(self):
        global mt4_manual_override_until, mt4_target_goal
        mt4_manual_override_until = time.time() + 1.0 
        mode = self.state.get("mode", "Move Relative (XYZ)")
        v1 = self.fetch_input_data(self.in_val1); v1 = float(v1) if v1 is not None else self.state.get("v1", 0)
        v2 = self.fetch_input_data(self.in_val2); v2 = float(v2) if v2 is not None else self.state.get("v2", 0)
        v3 = self.fetch_input_data(self.in_val3); v3 = float(v3) if v3 is not None else self.state.get("v3", 0)

        if mode.startswith("Move Rel"): mt4_target_goal['x'] += v1; mt4_target_goal['y'] += v2; mt4_target_goal['z'] += v3
        elif mode.startswith("Move Abs"): mt4_target_goal['x'] = v1; mt4_target_goal['y'] = v2; mt4_target_goal['z'] = v3
        elif mode.startswith("Set Grip"): mt4_target_goal['gripper'] = v1
        elif mode.startswith("Grip Rel"): mt4_target_goal['gripper'] += v1
        elif mode == "Homing": mt4_homing_callback(None, None, None)
        
        mt4_apply_limits()
        return self.out_flow


class MT4KeyboardNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "Keyboard (MT4)", "MT4_KEYBOARD")
        self.out_x = generate_uuid(); self.outputs[self.out_x] = PortType.DATA
        self.out_y = generate_uuid(); self.outputs[self.out_y] = PortType.DATA
        self.out_z = generate_uuid(); self.outputs[self.out_z] = PortType.DATA
        self.out_r = generate_uuid(); self.outputs[self.out_r] = PortType.DATA
        self.out_g = generate_uuid(); self.outputs[self.out_g] = PortType.DATA
        self.out_flow = generate_uuid(); self.outputs[self.out_flow] = PortType.FLOW
        
        self.step_size = 10.0; self.grip_step = 5.0; self.cooldown = 0.2; self.last_input_time = 0.0
        self.roll_step = 5.0

    def execute(self):
        if self.state.get("is_focused", False): return self.out_flow
        
        global mt4_manual_override_until, mt4_target_goal
        if time.time() - self.last_input_time > self.cooldown:
            dx=0; dy=0; dz=0; dg=0
            key_mode = self.state.get("keys", "WASD")
            if key_mode == "WASD":
                if self.state.get("W"): dx=1
                if self.state.get("S"): dx=-1
                if self.state.get("A"): dy=1
                if self.state.get("D"): dy=-1
            else:
                if self.state.get("UP"): dx=1
                if self.state.get("DOWN"): dx=-1
                if self.state.get("LEFT"): dy=1
                if self.state.get("RIGHT"): dy=-1

            if self.state.get("Q"): dz=1
            if self.state.get("E"): dz=-1
            if self.state.get("J"): dg=1
            if self.state.get("U"): dg=-1

            dr = 0
            if self.state.get("Z"): dr = 1
            if self.state.get("X"): dr = -1
            if dx or dy or dz or dg or dr:
                mt4_manual_override_until = time.time() + 0.5; self.last_input_time = time.time()
                mt4_target_goal['x']+=dx*self.step_size; mt4_target_goal['y']+=dy*self.step_size; mt4_target_goal['z']+=dz*self.step_size; mt4_target_goal['gripper']+=dg*self.grip_step
                mt4_target_goal['roll']+=dr*self.roll_step

        self.output_data[self.out_x]=mt4_target_goal['x']
        self.output_data[self.out_y]=mt4_target_goal['y']
        self.output_data[self.out_z]=mt4_target_goal['z']
        self.output_data[self.out_r]=mt4_target_goal['roll']
        self.output_data[self.out_g]=mt4_target_goal['gripper']
        return self.out_flow


class MT4UnityNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "Unity Logic (MT4)", "MT4_UNITY")
        self.data_in_id = generate_uuid(); self.inputs[self.data_in_id] = PortType.DATA
        self.out_x = generate_uuid(); self.outputs[self.out_x] = PortType.DATA
        self.out_y = generate_uuid(); self.outputs[self.out_y] = PortType.DATA
        self.out_z = generate_uuid(); self.outputs[self.out_z] = PortType.DATA
        self.out_g = generate_uuid(); self.outputs[self.out_g] = PortType.DATA
        self.out_r = generate_uuid(); self.outputs[self.out_r] = PortType.DATA
        self.out_flow = generate_uuid(); self.outputs[self.out_flow] = PortType.FLOW
        self.last_processed_json = ""
        
    def execute(self):
        global mt4_collision_lock_until
        raw_json = self.fetch_input_data(self.data_in_id)
        
        is_new_msg = False
        if raw_json and raw_json != self.last_processed_json:
            is_new_msg = True
            self.last_processed_json = raw_json

        is_overridden = (time.time() < mt4_manual_override_until) or mt4_mode.get("playing", False)

        if is_overridden:
            self.output_data[self.out_x] = mt4_target_goal['x']
            self.output_data[self.out_y] = mt4_target_goal['y']
            self.output_data[self.out_z] = mt4_target_goal['z']
            self.output_data[self.out_g] = mt4_target_goal['gripper']
            self.output_data[self.out_r] = mt4_target_goal['roll']
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
                            write_log("Collision Detected! Robot Locked.")
                            send_unity_ui("STATUS", "충돌 감지! 로봇 긴급 정지")
                        elif val == "START_REC":
                            if not mt4_mode["recording"]: toggle_mt4_record()
                        elif val.startswith("STOP_REC:"):
                            fname = val.split(":")[1]
                            if mt4_mode["recording"]: toggle_mt4_record(custom_name=fname)
                        elif val == "REQ_FILES":
                            send_unity_ui("FILE_LIST", f"[{'|'.join(get_mt4_paths())}]")
                        elif val.startswith("PLAY:"):
                            fname = val.split(":")[1]
                            play_mt4_path(filename=fname)
                        elif val == "LOG_SUCCESS":
                            mt4_log_event_queue.append("SUCCESS")
                            send_unity_ui("LOG", "<color=green>SUCCESS 기록 완료</color>")
                        elif val == "LOG_FAIL":
                            mt4_log_event_queue.append("FAIL")
                            send_unity_ui("LOG", "<color=red>FAIL 기록 완료</color>")
                    elif msg_type == "MOVE":
                        if time.time() > mt4_collision_lock_until:
                            if 'z' in parsed: self.output_data[self.out_x] = float(parsed['z']) * 1000.0
                            if 'x' in parsed: self.output_data[self.out_y] = -float(parsed['x']) * 1000.0
                            if 'y' in parsed: self.output_data[self.out_z] = (float(parsed['y']) * 1000.0) + MT4_Z_OFFSET
                            if 'gripper' in parsed: self.output_data[self.out_g] = float(parsed['gripper']) 
                            if 'roll' in parsed: self.output_data[self.out_r] = float(parsed['roll'])
                except Exception as e:
                    write_log(f"JSON Parse Error: {e}") 
                
        return self.out_flow


class UDPReceiverNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "UDP Receiver", "UDP_RECV")
        self.out_flow = generate_uuid(); self.outputs[self.out_flow] = PortType.FLOW
        self.out_json = generate_uuid(); self.outputs[self.out_json] = PortType.DATA
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setblocking(False)
        self.is_bound = False
        self.last_data_str = ""
        self.current_port = 0
        
    def execute(self):
        global MT4_UNITY_IP
        port = self.state.get("port", 6000)
        MT4_UNITY_IP = self.state.get("ip", "192.168.50.63")
        if not self.is_bound or self.current_port != port:
            try:
                self.sock.close()
                self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); self.sock.setblocking(False)
                self.sock.bind(('0.0.0.0', port)); self.is_bound = True; self.current_port = port
                write_log(f"UDP: Bound to port {port}")
            except: self.is_bound = True
        try:
            while True: 
                data, _ = self.sock.recvfrom(4096); decoded = data.decode('utf-8', errors='ignore').strip('\x00').strip()
                now = time.time()
                mt4_dashboard["latency"] = (now - mt4_dashboard.get("last_pkt_time", now)) * 1000.0 
                mt4_dashboard["last_pkt_time"] = now
                mt4_dashboard["status"] = "Connected"
                if decoded != self.last_data_str:
                    write_log(f"Unity Command: {decoded[:60]}...")
                    self.output_data[self.out_json] = decoded; self.last_data_str = decoded
        except: pass
        return self.out_flow


class MT4GravitySagNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "Gravity Sag Comp (StR)", "MT4_SAG")
        self.in_x = generate_uuid(); self.inputs[self.in_x] = PortType.DATA
        self.in_z = generate_uuid(); self.inputs[self.in_z] = PortType.DATA
        self.out_z = generate_uuid(); self.outputs[self.out_z] = PortType.DATA
        self.state['sag_factor'] = 0.05 
        
    def execute(self):
        x_val = self.fetch_input_data(self.in_x)
        z_val = self.fetch_input_data(self.in_z)
        if x_val is not None and z_val is not None:
            sag_comp = float(x_val) * float(self.state.get('sag_factor', 0.0))
            self.output_data[self.out_z] = float(z_val) + sag_comp
        elif z_val is not None:
            self.output_data[self.out_z] = float(z_val)
        return None

class MT4CalibrationNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "3D Calibration (StR)", "MT4_CALIB")
        self.in_x = generate_uuid(); self.inputs[self.in_x] = PortType.DATA
        self.in_y = generate_uuid(); self.inputs[self.in_y] = PortType.DATA
        self.in_z = generate_uuid(); self.inputs[self.in_z] = PortType.DATA
        self.out_x = generate_uuid(); self.outputs[self.out_x] = PortType.DATA
        self.out_y = generate_uuid(); self.outputs[self.out_y] = PortType.DATA
        self.out_z = generate_uuid(); self.outputs[self.out_z] = PortType.DATA
        self.state.update({'x_offset': 0.0, 'y_offset': 0.0, 'z_offset': 0.0, 'scale': 1.0})
        
    def execute(self):
        x_val = self.fetch_input_data(self.in_x)
        y_val = self.fetch_input_data(self.in_y)
        z_val = self.fetch_input_data(self.in_z)
        
        scale = float(self.state.get('scale', 1.0))
        if x_val is not None: self.output_data[self.out_x] = (float(x_val) * scale) + float(self.state.get('x_offset', 0.0))
        if y_val is not None: self.output_data[self.out_y] = (float(y_val) * scale) + float(self.state.get('y_offset', 0.0))
        if z_val is not None: self.output_data[self.out_z] = (float(z_val) * scale) + float(self.state.get('z_offset', 0.0))
        return None

class MT4TooltipNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "Tool-tip Offset (StR)", "MT4_TOOLTIP")
        self.in_x = generate_uuid(); self.inputs[self.in_x] = PortType.DATA
        self.in_z = generate_uuid(); self.inputs[self.in_z] = PortType.DATA
        self.out_x = generate_uuid(); self.outputs[self.out_x] = PortType.DATA
        self.out_z = generate_uuid(); self.outputs[self.out_z] = PortType.DATA
        self.state.update({'tool_length': 0.0, 'tool_angle': 0.0})
        
    def execute(self):
        x_val = self.fetch_input_data(self.in_x)
        z_val = self.fetch_input_data(self.in_z)
        length = float(self.state.get('tool_length', 0.0))
        angle_deg = float(self.state.get('tool_angle', 0.0))
        
        if x_val is not None and z_val is not None:
            dx = length * math.cos(math.radians(angle_deg))
            dz = length * math.sin(math.radians(angle_deg))
            self.output_data[self.out_x] = float(x_val) + dx
            self.output_data[self.out_z] = float(z_val) + dz
        return None

class MT4BacklashNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "Backlash & Inertia (StR)", "MT4_BACKLASH")
        self.in_x = generate_uuid(); self.inputs[self.in_x] = PortType.DATA
        self.in_y = generate_uuid(); self.inputs[self.in_y] = PortType.DATA
        self.in_z = generate_uuid(); self.inputs[self.in_z] = PortType.DATA
        self.out_x = generate_uuid(); self.outputs[self.out_x] = PortType.DATA
        self.out_y = generate_uuid(); self.outputs[self.out_y] = PortType.DATA
        self.out_z = generate_uuid(); self.outputs[self.out_z] = PortType.DATA
        self.state.update({'decel_dist': 15.0, 'stop_delay': 100.0})
        self.internal_pos = None
        
    def execute(self):
        tx = self.fetch_input_data(self.in_x)
        ty = self.fetch_input_data(self.in_y)
        tz = self.fetch_input_data(self.in_z)
        if tx is None or ty is None or tz is None: return None
        if self.internal_pos is None: self.internal_pos = [float(tx), float(ty), float(tz)]
            
        decel_dist = max(1.0, float(self.state.get('decel_dist', 15.0)))
        delay_factor = max(1.0, float(self.state.get('stop_delay', 100.0)))
        
        dx = float(tx) - self.internal_pos[0]; dy = float(ty) - self.internal_pos[1]; dz = float(tz) - self.internal_pos[2]
        dist = math.sqrt(dx**2 + dy**2 + dz**2)
        
        speed = 1.0 if dist > decel_dist else max(0.01, (dist / decel_dist) * (50.0 / delay_factor))
        self.internal_pos[0] += dx * speed; self.internal_pos[1] += dy * speed; self.internal_pos[2] += dz * speed
        
        self.output_data[self.out_x] = self.internal_pos[0]
        self.output_data[self.out_y] = self.internal_pos[1]
        self.output_data[self.out_z] = self.internal_pos[2]
        return None
