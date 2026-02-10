import serial
import time
import socket
import json
import threading
import os
import csv
import glob
from datetime import datetime
import sys
from collections import deque
import dearpygui.dearpygui as dpg
from abc import ABC, abstractmethod
import subprocess 

# ================= [설정] =================
UNITY_IP = "192.168.50.63"  # GUI에서 수정 가능
LISTEN_PORT = 6000          # GUI에서 수정 가능
FEEDBACK_PORT = 5005        
UI_PORT = 5007              

SERIAL_PORT = '/dev/ttyUSB0' 
BAUD_RATE = 115200           

LIMITS = {'min_x': 100, 'max_x': 280, 'min_y': -150, 'max_y': 150, 'min_z': 0, 'max_z': 180}

PATH_DIR = "path_record"
LOG_DIR = "result_log"
os.makedirs(PATH_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

# ================= [전역 변수] =================
target_pos  = {'x': 200.0, 'y': 0.0, 'z': 120.0, 'gripper': 40} 
current_pos = {'x': 200.0, 'y': 0.0, 'z': 120.0, 'gripper': 40} 
SMOOTHING_FACTOR = 0.2 
last_move_dir = None
waypoints = []  
is_recording = False
new_data_arrived = False 
is_running = True
playback_mode = False 
is_recovering = False 
last_packet_time = 0.0

ignore_udp_until = 0.0 

# ★ [스마트 로깅용 변수]
last_remote_pkt_time = 0.0  # 마지막 패킷 수신 시간
remote_active_flag = False  # 조종 중 여부 플래그

lock = threading.Lock()
ser_instance = None 

sock_feedback = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock_ui = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

# ================= [GUI 상태 저장소] =================
gui_state = {
    "raw_data": "Waiting...",
    "parsed_json": "{}",
    "gcode": "Standby",
    "status": "Idle",
    "hw_status": "Offline",
    "latency_msg": "Latency: -- ms",
    "logs": deque(maxlen=50),
    "plot_x": deque(maxlen=300),
    "plot_y": deque(maxlen=300),
    "plot_z": deque(maxlen=300),
    "plot_t": deque(maxlen=300)
}
frame_count = 0

# ================= [네트워크 헬퍼 함수] =================
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
        if not ssid: return "Wired / Unknown"
        return ssid
    except:
        return "Unknown"

# ================= [헬퍼 함수] =================
def log_to_gui(msg):
    timestamp = datetime.now().strftime("%H:%M:%S")
    formatted_msg = f"[{timestamp}] {msg}"
    print(formatted_msg) 
    gui_state["logs"].append(formatted_msg)

def send_ui_msg(msg_type, extra=""):
    try:
        data = {"type": msg_type, "extra": extra}
        sock_ui.sendto(json.dumps(data, ensure_ascii=False).encode('utf-8'), (UNITY_IP, UI_PORT))
    except: pass

def send_feedback():
    try:
        u_x = -target_pos['y'] / 1000.0
        u_y = target_pos['z'] / 1000.0
        u_z = target_pos['x'] / 1000.0
        status_code = 1 if is_recovering else 0
        data = {"x": u_x, "y": u_y, "z": u_z, "gripper": target_pos['gripper'], "status": status_code}
        sock_feedback.sendto(json.dumps(data).encode(), (UNITY_IP, FEEDBACK_PORT))
    except: pass

class DataLogger:
    def __init__(self):
        self.time_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.filename = os.path.join(LOG_DIR, f"pi_log_{self.time_str}.csv")
        with open(self.filename, mode='w', newline='', encoding='utf-8') as file:
            writer = csv.writer(file)
            writer.writerow(["Timestamp", "X", "Y", "Z", "Gripper", "Note"])
    def log(self, x, y, z, gripper, note="Run"):
        try:
            timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            with open(self.filename, mode='a', newline='', encoding='utf-8') as file:
                writer = csv.writer(file)
                writer.writerow([timestamp, x, y, z, gripper, note])
        except: pass
logger = None

# ================= [설정 변경 콜백] =================
def on_config_change(sender, app_data, user_data):
    global UNITY_IP, LISTEN_PORT
    if user_data == "unity_ip":
        UNITY_IP = app_data
        log_to_gui(f"⚙️ Target IP Changed -> {UNITY_IP}")
        send_ui_msg("STATUS", f"IP Updated to {UNITY_IP}")
    elif user_data == "listen_port":
        LISTEN_PORT = int(app_data)
        log_to_gui(f"⚙️ Port Changed -> {LISTEN_PORT} (Restart Required)")

# ================= [로직 함수들] =================
def perform_collision_recovery(ser):
    global gui_state, is_recovering, waypoints, target_pos
    
    log_to_gui("[COLLISION] 충돌 감지! 조작 잠금 시작")
    gui_state["status"] = "LOCKED (Collision)"
    
    send_ui_msg("STATUS", "충돌! 1.5초간 조작 차단됨")
    send_ui_msg("LOG", "이벤트: 충돌 발생 (Input Locked)")

    if logger: 
        logger.log(target_pos['x'], target_pos['y'], target_pos['z'], target_pos['gripper'], "COLLISION_LOCK")

    if is_recording:
        waypoints.append({
            "x": target_pos['x'], 
            "y": target_pos['y'], 
            "z": target_pos['z'], 
            "gripper": target_pos['gripper'], 
            "event": "COLLISION"
        })

    time.sleep(1.5) 
    
    gui_state["status"] = "Idle"
    send_ui_msg("STATUS", "잠금 해제. 제어 가능.")
    log_to_gui("[UNLOCK] 조작 잠금 해제")

def check_safety_and_move(tx, ty, tz, tg, ser):
    is_collision = False
    if not (LIMITS['min_x'] <= tx <= LIMITS['max_x']): is_collision = True
    if not (LIMITS['min_y'] <= ty <= LIMITS['max_y']): is_collision = True
    if not (LIMITS['min_z'] <= tz <= LIMITS['max_z']): is_collision = True

    if is_collision:
        send_ui_msg("STATUS", "충돌 감지! (범위 이탈)")
        log_to_gui(f"LIMIT HIT: X{tx:.1f} Y{ty:.1f} Z{tz:.1f}")
        gui_state["status"] = "LIMIT HIT"
        return False
    else:
        cmd_move = f"G0 X{tx:.1f} Y{ty:.1f} Z{tz:.1f} F2000\r\n"
        cmd_grip = f"M3 S{tg}\r\n"
        gui_state["gcode"] = f"{cmd_move.strip()} | G:{tg}"

        if ser:
            ser.write(cmd_move.encode())
            ser.write(cmd_grip.encode())
        return True

def playback_sequence(ser, filename):
    global playback_mode, target_pos, current_pos, is_running
    full_path = os.path.join(PATH_DIR, filename)
    if not os.path.exists(full_path): 
        log_to_gui(f"파일 없음: {filename}")
        return
    
    playback_mode = True
    gui_state["status"] = f"Playing: {filename}"
    send_ui_msg("STATUS", f"파일 로드: {filename}")
    
    try:
        with open(full_path, "r", encoding='utf-8') as f: path_data = json.load(f)
        log_to_gui(f"재생 시작: {filename} (Total {len(path_data)} pts)")
        
        if logger: logger.log(target_pos['x'], target_pos['y'], target_pos['z'], target_pos['gripper'], f"PLAY START: {filename}")
        
        if ser and len(path_data) > 0:
            log_to_gui("Homing Sequence Start...")
            ser.write(b"$H\r\n"); time.sleep(15) 
            ser.write(b"M20\r\n"); ser.write(b"G90\r\n"); ser.write(b"G1 F2000\r\n"); time.sleep(1)

            start_pt = path_data[0]
            log_to_gui(f"Moving to Start Point: X{start_pt['x']:.1f} Y{start_pt['y']:.1f} Z{start_pt['z']:.1f}")
            cmd = f"G0 X{start_pt['x']:.1f} Y{start_pt['y']:.1f} Z{start_pt['z']:.1f} F2000\r\n"
            ser.write(cmd.encode())
            ser.write(f"M3 S{start_pt['gripper']}\r\n".encode())
            
            with lock:
                target_pos.update(start_pt)
                current_pos.update(start_pt)
            time.sleep(3.0)

        prev_pt = current_pos.copy()

        for i, pt in enumerate(path_data):
            if not is_running: break
            
            if "event" in pt and pt["event"] == "COLLISION":
                send_ui_msg("LOG", "재연: 충돌 사고 발생 구간")
                log_to_gui(f"[REPLAY] {i}번 스텝: 충돌 재연 (1초 대기)")
                gui_state["status"] = "REPLAY COLLISION"
                
                if logger: logger.log(pt['x'], pt['y'], pt['z'], pt['gripper'], "REPLAY COLLISION")
                
                time.sleep(1.0)
                prev_pt = pt.copy()
                continue

            check_safety_and_move(pt['x'], pt['y'], pt['z'], pt['gripper'], ser)
            
            if logger: logger.log(pt['x'], pt['y'], pt['z'], pt['gripper'], "Playback Move")

            with lock: 
                target_pos.update(pt)
                current_pos.update(pt)
            
            dist = abs(pt['x'] - prev_pt['x']) + abs(pt['y'] - prev_pt['y']) + abs(pt['z'] - prev_pt['z'])
            diff_grip = abs(pt['gripper'] - prev_pt['gripper'])
            
            if diff_grip > 1.0: time.sleep(0.8)
            elif dist < 2.0: time.sleep(0.03) 
            else: time.sleep(0.5)
            
            prev_pt = pt.copy()

        send_ui_msg("STATUS", "재생 완료")
        log_to_gui("재생 완료")
        if logger: logger.log(target_pos['x'], target_pos['y'], target_pos['z'], target_pos['gripper'], "PLAY FINISHED")

    except Exception as e:
        log_to_gui(f"재생 에러: {e}")
    
    playback_mode = False
    gui_state["status"] = "Idle"

def homing_callback(sender, app_data, user_data):
    global ignore_udp_until
    ser = user_data
    if not ser:
        log_to_gui("시리얼 연결 없음!")
        return
    ignore_udp_until = time.time() + 30.0

    def homing_task():
        global ignore_udp_until
        log_to_gui("Homing Start... ($H)")
        gui_state["status"] = "HOMING"
        
        ser.write(b"$H\r\n")
        time.sleep(15) 
        
        ser.write(b"M20\r\n"); ser.write(b"G90\r\n"); ser.write(b"G1 F2000\r\n")
        
        init_x, init_y, init_z = 200.0, 0.0, 120.0
        init_g = 40.0
        
        log_to_gui(f"Init Move: {init_x}, {init_y}, {init_z}")
        
        cmd = f"G0 X{init_x} Y{init_y} Z{init_z} F2000\r\n"
        ser.write(cmd.encode())
        ser.write(f"M3 S{init_g}\r\n".encode())
        
        with lock:
            target_pos['x'] = init_x; target_pos['y'] = init_y; target_pos['z'] = init_z
            target_pos['gripper'] = init_g
            current_pos['x'] = init_x; current_pos['y'] = init_y; current_pos['z'] = init_z
            current_pos['gripper'] = init_g
        time.sleep(2.0)
        log_to_gui("Homing & Reset Complete.")
        gui_state["status"] = "Idle"
        ignore_udp_until = 0.0
    threading.Thread(target=homing_task, daemon=True).start()

def move_to_coord_callback(sender, app_data, user_data):
    global ignore_udp_until
    x = dpg.get_value("input_x")
    y = dpg.get_value("input_y")
    z = dpg.get_value("input_z")
    g = dpg.get_value("input_g")

    ignore_udp_until = time.time() + 2.0
    with lock:
        target_pos['x'] = x; target_pos['y'] = y; target_pos['z'] = z; target_pos['gripper'] = g
        current_pos['x'] = x; current_pos['y'] = y; current_pos['z'] = z; current_pos['gripper'] = g
    
    log_to_gui(f"Direct Move: {x},{y},{z} G:{g}")
    if logger: logger.log(x, y, z, g, "GUI Direct Move")

def manual_control_callback(sender, app_data, user_data):
    global new_data_arrived, target_pos, current_pos, ignore_udp_until
    axis, step = user_data 
    ignore_udp_until = time.time() + 1.0

    with lock:
        if axis == 'gripper':
            val = 60 if target_pos['gripper'] < 50 else 40
            target_pos['gripper'] = val
            current_pos['gripper'] = val 
            log_to_gui(f"[Manual] Gripper {'Open' if val==40 else 'Close'}")
            if logger: logger.log(target_pos['x'], target_pos['y'], target_pos['z'], val, "GUI Gripper Action")
        else:
            target_pos[axis] += step
            current_pos[axis] += step 
            log_to_gui(f"[Manual] {axis.upper()} Move {step:+d}mm")
            if logger: logger.log(target_pos['x'], target_pos['y'], target_pos['z'], target_pos['gripper'], "GUI Manual Move")
        
        check_safety_and_move(target_pos['x'], target_pos['y'], target_pos['z'], target_pos['gripper'], None)
        new_data_arrived = True 

class Command(ABC):
    @abstractmethod
    def execute(self, ser, parsed):
        pass

class MoveCommand(Command):
    def execute(self, ser, parsed):
        global target_pos, new_data_arrived, last_move_dir, waypoints, is_recording, gui_state
        # 스마트 로깅 변수
        global last_remote_pkt_time, remote_active_flag

        rx = parsed['z'] * 1000.0   
        ry = -parsed['x'] * 1000.0  
        rz = parsed['y'] * 1000.0   
        grip = parsed['gripper']
        
        # [스마트 로깅 1] 그리퍼 변화 기록
        if logger and abs(grip - target_pos['gripper']) > 2.0:
            logger.log(rx, ry, rz, grip, "Gripper Action")

        # [스마트 로깅 2] 활성 시간 갱신
        last_remote_pkt_time = time.time()
        remote_active_flag = True

        with lock:
            dx, dy, dz = rx - target_pos['x'], ry - target_pos['y'], rz - target_pos['z']
            max_diff = max(abs(dx), abs(dy), abs(dz))
            if max_diff > 0.001: 
                if abs(dx) == max_diff: last_move_dir = 'x+' if dx > 0 else 'x-'
                elif abs(dy) == max_diff: last_move_dir = 'y+' if dy > 0 else 'y-'
                elif abs(dz) == max_diff: last_move_dir = 'z+' if dz > 0 else 'z-'
                gui_state["status"] = "Remote Control"

            target_pos['x'] = rx
            target_pos['y'] = ry
            target_pos['z'] = rz
            target_pos['gripper'] = grip
            new_data_arrived = True 
        
        if is_recording: waypoints.append(target_pos.copy())

class SystemCommand(Command):
    def execute(self, ser, parsed):
        global is_recording, waypoints, logger, gui_state, ignore_udp_until
        cmd_raw = parsed['val']

        if (cmd_raw == "COLLISION_DETECTED" or cmd_raw == "COLLISION"):
            ignore_udp_until = time.time() + 1.5
            print("충돌 감지! -> 1.5초간 입력 차단")
            log_to_gui(f"[CMD] {cmd_raw}")
        
        elif cmd_raw == "START_REC": 
            is_recording = True; waypoints = []
            send_ui_msg("STATUS", "녹화 시작")
            gui_state["status"] = "RECORDING"
            if logger: logger.log(target_pos['x'], target_pos['y'], target_pos['z'], target_pos['gripper'], "RECORD START")

        elif cmd_raw.startswith("STOP_REC"): 
            is_recording = False
            parts = cmd_raw.split(':'); fname = parts[1] if len(parts)>1 else "data.json"
            if not fname.endswith(".json"): fname+=".json"
            gui_state["status"] = "Idle"
            if logger: logger.log(target_pos['x'], target_pos['y'], target_pos['z'], target_pos['gripper'], f"RECORD STOP: {fname}")                    
            if waypoints:
                log_to_gui(f"Saving {len(waypoints)} pts to {fname}")
                with open(os.path.join(PATH_DIR, fname), "w") as f: json.dump(waypoints, f, indent=2)
                send_ui_msg("STATUS", f"저장 완료: {fname}"); 

        elif cmd_raw == "REQ_FILES":
            files = glob.glob(os.path.join(PATH_DIR, "*.json"))
            names = "|".join([os.path.basename(f) for f in files])
            send_ui_msg("FILE_LIST", names)

        elif cmd_raw.startswith("PLAY"):
            parts = cmd_raw.split(':'); fname = parts[1]
            if not playback_mode: t = threading.Thread(target=playback_sequence, args=(ser, fname)); t.start()

        elif cmd_raw == "LOG_SUCCESS": 
            send_ui_msg("LOG", "판정: 성공 (Saved)")
            log_to_gui("[JUDGE] Success Logged")
            if logger: logger.log(target_pos['x'], target_pos['y'], target_pos['z'], target_pos['gripper'], "SUCCESS")
        
        elif cmd_raw == "LOG_FAIL": 
            send_ui_msg("LOG", "판정: 실패 (Saved)")
            log_to_gui("[JUDGE] Fail Logged")
            if logger: logger.log(target_pos['x'], target_pos['y'], target_pos['z'], target_pos['gripper'], "FAIL")

class CommandFactory:
    @staticmethod
    def create_command(msg_type):
        if msg_type == "MOVE":
            return MoveCommand()
        elif msg_type == "CMD":
            return SystemCommand()
        else:
            return None

def udp_listener(ser):
    global target_pos, is_running, new_data_arrived, is_recording, waypoints, last_move_dir, last_packet_time, gui_state, ignore_udp_until
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(('0.0.0.0', LISTEN_PORT))
    log_to_gui(f"UDP Listener Started on {LISTEN_PORT}")
    factory = CommandFactory()

    while is_running:
        try:
            data, _ = sock.recvfrom(1024)
            if time.time() < ignore_udp_until: continue
            if gui_state["status"] == "HOMING": continue

            current_time = time.time()
            if last_packet_time > 0:
                interval_ms = (current_time - last_packet_time) * 1000.0
                gui_state["latency_msg"] = f"Interval: {interval_ms:.1f} ms"
            
            last_packet_time = current_time
            gui_state["raw_data"] = f"Bytes: {len(data)}"
            
            parsed = json.loads(data.decode('utf-8'))
            msg_type = parsed.get("type", "MOVE")
            gui_state["parsed_json"] = f"Type: {msg_type}"
            
            if playback_mode: continue

            command = factory.create_command(msg_type)
            if command:
                command.execute(ser, parsed)
        except Exception as e:
            print(f"⚠️ UDP Error: {e}") 
    sock.close()

def serial_controller(ser):
    global current_pos, target_pos, is_running
    # 스마트 로깅 변수
    global last_remote_pkt_time, remote_active_flag
    
    last_sent_pos = current_pos.copy()
    LOOP_DELAY = 0.05 

    while is_running:
        send_feedback() 
        
        # [스마트 로깅 3] 유니티 키 뗌 감지 (0.5초 Timeout)
        if remote_active_flag and (time.time() - last_remote_pkt_time > 0.5):
            if logger: 
                logger.log(target_pos['x'], target_pos['y'], target_pos['z'], target_pos['gripper'], "Manual Move")
            # log_to_gui("[LOG] Manual Move Recorded (Key Released)") // 로그가 너무 많아서 일단 각주
            remote_active_flag = False

        if not playback_mode and not is_recovering:
            with lock:
                dx = target_pos['x'] - current_pos['x']
                dy = target_pos['y'] - current_pos['y']
                dz = target_pos['z'] - current_pos['z']
                
                if abs(dx) < 0.5 and abs(dy) < 0.5 and abs(dz) < 0.5:
                    current_pos['x'] = target_pos['x']
                    current_pos['y'] = target_pos['y']
                    current_pos['z'] = target_pos['z']
                else:
                    current_pos['x'] += dx * SMOOTHING_FACTOR
                    current_pos['y'] += dy * SMOOTHING_FACTOR
                    current_pos['z'] += dz * SMOOTHING_FACTOR
                current_pos['gripper'] = target_pos['gripper']

            dx = abs(current_pos['x'] - last_sent_pos['x'])
            dy = abs(current_pos['y'] - last_sent_pos['y'])
            dz = abs(current_pos['z'] - last_sent_pos['z'])
            dg = abs(current_pos['gripper'] - last_sent_pos['gripper'])
            
            is_target_reached = (current_pos['x'] == target_pos['x'] and 
                                 current_pos['y'] == target_pos['y'] and 
                                 current_pos['z'] == target_pos['z'])
            
            if (dx > 0.1 or dy > 0.1 or dz > 0.1 or dg > 0.1) or \
               (is_target_reached and (dx > 0 or dy > 0 or dz > 0)):
                check_safety_and_move(current_pos['x'], current_pos['y'], current_pos['z'], current_pos['gripper'], ser)
                last_sent_pos = current_pos.copy()

        time.sleep(LOOP_DELAY)

def setup_gui(ser):
    dpg.create_context()
    dpg.configure_app(load_init_file=False)
    font_path = "/usr/share/fonts/truetype/nanum/NanumGothic.ttf"
    
    with dpg.font_registry():
        if os.path.exists(font_path):
            with dpg.font(font_path, 18) as kr_font:
                dpg.add_font_range_hint(dpg.mvFontRangeHint_Default)
                dpg.add_font_range_hint(dpg.mvFontRangeHint_Korean) 
            dpg.bind_font(kr_font)
        else:
            print(f"[GUI] 폰트 파일을 찾을 수 없습니다: {font_path}")
    
    my_ip = get_local_ip()
    my_ssid = get_wifi_ssid()

    with dpg.window(tag="Primary Window", label="MT4 Robot Dashboard"):
        # ================= [상단: 상태창 및 제어 패널] =================
        with dpg.group(horizontal=True):
            # 1. 시스템 상태 (좌측)
            with dpg.group(width=300):
                dpg.add_text("System Status", color=(150, 150, 150))
                dpg.add_text("Ready", tag="txt_status", color=(0, 255, 0))
                dpg.add_spacer(height=5)
                dpg.add_text("Hardware Link", color=(150, 150, 150))
                dpg.add_text("Offline", tag="txt_link", color=(255, 0, 0))
                dpg.add_spacer(height=5)
                dpg.add_text("Network Interval", color=(150, 150, 150))
                dpg.add_text("Wait...", tag="txt_latency", color=(255, 255, 0))

            # 2. 수동 조작 (중앙)
            with dpg.child_window(width=400, height=120, border=True):
                dpg.add_text("Manual Control (Step: 10mm)", color=(255, 200, 0))
                with dpg.group(horizontal=True):
                    dpg.add_button(label="X +", width=60, callback=manual_control_callback, user_data=('x', 10))
                    dpg.add_button(label="X -", width=60, callback=manual_control_callback, user_data=('x', -10))
                    dpg.add_text(" | ", color=(100,100,100))
                    dpg.add_button(label="Y +", width=60, callback=manual_control_callback, user_data=('y', 10))
                    dpg.add_button(label="Y -", width=60, callback=manual_control_callback, user_data=('y', -10))
                with dpg.group(horizontal=True):
                    dpg.add_button(label="Z +", width=60, callback=manual_control_callback, user_data=('z', 10))
                    dpg.add_button(label="Z -", width=60, callback=manual_control_callback, user_data=('z', -10))
                    dpg.add_text(" | ", color=(100,100,100))
                    dpg.add_button(label="Gripper Toggle", width=130, callback=manual_control_callback, user_data=('gripper', 0))
                
            # 3. 좌표 이동 및 호밍 (우측)
            with dpg.child_window(width=300, height=120, border=True):
                dpg.add_text("Direct Coord & Homing", color=(0, 255, 255))
                
                with dpg.group(horizontal=True):
                    dpg.add_text("X")
                    dpg.add_input_int(tag="input_x", width=50, default_value=200, step=0)
                    dpg.add_text("Y")
                    dpg.add_input_int(tag="input_y", width=50, default_value=0, step=0)
                    dpg.add_text("Z")
                    dpg.add_input_int(tag="input_z", width=50, default_value=120, step=0)
                
                with dpg.group(horizontal=True):
                    dpg.add_text("G")
                    dpg.add_input_int(tag="input_g", width=50, default_value=40, step=0)
                    dpg.add_spacer(width=10)
                    dpg.add_button(label="Move", callback=move_to_coord_callback)
                
                dpg.add_separator()
                dpg.add_button(label="Homing", width=-1, callback=homing_callback, user_data=ser)

        dpg.add_separator()
        
        # ================= [중단: System Configuration 패널 (가로 배치)] =================
        # ★ Node Editor 밖으로 빼서 상단에 배치
        with dpg.group():
            dpg.add_text("System Configuration", color=(0, 191, 255))
            with dpg.child_window(height=60, border=True): # 패널 형태로 감싸기
                with dpg.group(horizontal=True):
                    # 정보 표시 (IP, SSID)
                    dpg.add_text(f"  My IP: {my_ip}", color=(200, 200, 200))
                    dpg.add_text(" | ", color=(100,100,100))
                    dpg.add_text(f"SSID: {my_ssid}", color=(200, 200, 200))
                    
                    dpg.add_spacer(width=50) # 간격 띄우기
                    
                    # 설정 입력 (Target IP, Port)
                    dpg.add_text("Target IP:")
                    dpg.add_input_text(default_value=UNITY_IP, width=120, callback=on_config_change, user_data="unity_ip")
                    
                    dpg.add_spacer(width=20)
                    
                    dpg.add_text("Listen Port:")
                    dpg.add_input_int(default_value=LISTEN_PORT, width=100, callback=on_config_change, user_data="listen_port")

        dpg.add_spacer(height=5)
        dpg.add_text("Data Pipeline Monitor")

        # ================= [하단: Node Editor (데이터 흐름)] =================
        # ★ 이제 설정값은 위에서 처리하므로, 여기서는 수신부부터 보여줍니다.
        with dpg.node_editor(tag="node_editor", height=200):
            
            # Node 1: UDP Receiver (시작점)
            with dpg.node(label="UDP Receiver", pos=[20, 20], tag="node_1"):
                # 입력핀은 제거하거나 더미로 둠 (외부 설정값 사용 암시)
                with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                     dpg.add_text("Listening...")
                with dpg.node_attribute(tag="attr_out_1", attribute_type=dpg.mvNode_Attr_Output):
                    dpg.add_text("Wait...", tag="node_1_text")

            # Node 2: JSON Parser
            with dpg.node(label="JSON Parser", pos=[250, 20], tag="node_2"):
                with dpg.node_attribute(tag="attr_in_2", attribute_type=dpg.mvNode_Attr_Input):
                    dpg.add_text("Bytes")
                with dpg.node_attribute(tag="attr_out_2", attribute_type=dpg.mvNode_Attr_Output):
                    dpg.add_text("Wait...", tag="node_2_text", color=(255, 255, 0))

            # Node 3: Robot Controller
            with dpg.node(label="Robot Controller", pos=[500, 20], tag="node_3"):
                with dpg.node_attribute(tag="attr_in_3", attribute_type=dpg.mvNode_Attr_Input):
                    dpg.add_text("Dict")
                with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output):
                    dpg.add_text("Wait...", tag="node_3_text", color=(0, 255, 255))
            
            # 링크 연결 (설정 노드가 빠졌으므로 1->2, 2->3만 연결)
            dpg.add_node_link("attr_out_1", "attr_in_2")
            dpg.add_node_link("attr_out_2", "attr_in_3")

        # ================= [최하단: 그래프 및 로그] =================
        with dpg.group(horizontal=True):
            with dpg.plot(label="Live Trajectory", height=200, width=600): # 높이 약간 조정
                dpg.add_plot_legend()
                dpg.add_plot_axis(dpg.mvXAxis, label="Time", tag="x_axis")
                with dpg.plot_axis(dpg.mvYAxis, label="Position", tag="y_axis"):
                    dpg.add_line_series([], [], label="X", tag="series_x")
                    dpg.add_line_series([], [], label="Y", tag="series_y")
                    dpg.add_line_series([], [], label="Z", tag="series_z")
            with dpg.child_window(label="System Logs", width=-1, height=200, border=True):
                dpg.add_text("--- System Log Start ---")
                dpg.add_text("", tag="log_view", color=(200, 200, 200))

    dpg.create_viewport(title='MT4 Dashboard', width=1024, height=800) # 높이 약간 여유있게
    dpg.setup_dearpygui()
    dpg.set_primary_window("Primary Window", True)
    dpg.show_viewport()

def update_gui_render():
    global frame_count
    frame_count += 1
    dpg.set_value("txt_status", gui_state["status"])
    dpg.set_value("txt_link", gui_state["hw_status"])
    dpg.set_value("txt_latency", gui_state["latency_msg"])

    if gui_state["hw_status"] == "Online": dpg.configure_item("txt_link", color=(0, 255, 0))
    else: dpg.configure_item("txt_link", color=(255, 0, 0))

    if "RECOVERING" in gui_state["status"]: dpg.configure_item("txt_status", color=(255, 0, 0)) 
    elif "RECORDING" in gui_state["status"]: dpg.configure_item("txt_status", color=(255, 165, 0))
    else: dpg.configure_item("txt_status", color=(0, 255, 0))

    dpg.set_value("node_1_text", gui_state["raw_data"])
    dpg.set_value("node_2_text", gui_state["parsed_json"])
    dpg.set_value("node_3_text", gui_state["gcode"])

    log_str = "\n".join(gui_state["logs"])
    dpg.set_value("log_view", log_str)

    if frame_count % 5 == 0:
        gui_state["plot_t"].append(frame_count)
        gui_state["plot_x"].append(current_pos['x'])
        gui_state["plot_y"].append(current_pos['y'])
        gui_state["plot_z"].append(current_pos['z'])
        dpg.set_value("series_x", [list(gui_state["plot_t"]), list(gui_state["plot_x"])])
        dpg.set_value("series_y", [list(gui_state["plot_t"]), list(gui_state["plot_y"])])
        dpg.set_value("series_z", [list(gui_state["plot_t"]), list(gui_state["plot_z"])])
        if len(gui_state["plot_t"]) > 0:
            dpg.set_axis_limits("x_axis", gui_state["plot_t"][0], gui_state["plot_t"][-1])
            dpg.set_axis_limits("y_axis", -200, 300)

def main():
    global is_running, logger
    logger = DataLogger()
    ser = None
    try:
        ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=0.1)
        gui_state["hw_status"] = "Online"
        log_to_gui("Robot Connected")
        ser.write(b"$H\r\n"); time.sleep(15) 
        ser.write(b"M20\r\n"); ser.write(b"G90\r\n"); ser.write(b"G1 F2000\r\n"); time.sleep(1)
        
        init_x, init_y, init_z = 200.0, 0.0, 120.0
        init_g = 40.0
        log_to_gui(f"Startup Init Move: {init_x}, {init_y}, {init_z}")
        
        cmd = f"G0 X{init_x} Y{init_y} Z{init_z} F2000\r\n"
        ser.write(cmd.encode())
        ser.write(f"M3 S{init_g}\r\n".encode())
        time.sleep(2.0)
    except:
        gui_state["hw_status"] = "Simulation"
        log_to_gui("Simulation Mode (No Robot)")

    t1 = threading.Thread(target=udp_listener, args=(ser,), daemon=True)
    t2 = threading.Thread(target=serial_controller, args=(ser,), daemon=True)
    
    global ignore_udp_until
    ignore_udp_until = time.time() + 5.0
    
    t1.start()
    t2.start()
    
    setup_gui(ser)
    while dpg.is_dearpygui_running():
        update_gui_render()
        dpg.render_dearpygui_frame()

    is_running = False
    dpg.destroy_context()

if __name__ == "__main__":
    main()