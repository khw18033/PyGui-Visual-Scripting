import dearpygui.dearpygui as dpg
import time
import os
import socket
import json
import serial 
import threading 
from abc import ABC, abstractmethod

# ================= [전역 설정 및 변수] =================
node_registry = {}
link_registry = {}
ser = None 
is_running = False 
run_thread = None  

# 로봇 상태 공유 변수
current_pos = {'x': 200.0, 'y': 0.0, 'z': 120.0, 'gripper': 40}

# 통신 설정
UNITY_IP = "192.168.50.63"
FEEDBACK_PORT = 5005
SMOOTHING_FACTOR = 0.3      # 반응성을 위해 0.2 -> 0.3 상향 조정
LIMITS = {'min_x': 100, 'max_x': 280, 'min_y': -150, 'max_y': 150, 'min_z': 0, 'max_z': 180}

# ================= [0. MT4 로봇 연결] =================
def init_serial():
    global ser
    try:
        ser = serial.Serial('/dev/ttyUSB0', 115200, timeout=0.01) # 타임아웃 단축
        print("[System] MT4 Robot Connected")
        
        time.sleep(2) 
        ser.write(b"$H\r\n") 
        time.sleep(15) 
        
        ser.write(b"M20\r\n") 
        ser.write(b"G90\r\n") 
        ser.write(b"G1 F2000\r\n") 
        
        cmd = f"G0 X200 Y0 Z120 F2000\r\n"
        ser.write(cmd.encode())
        ser.write(b"M3 S40\r\n") 
        
    except Exception as e:
        print(f"[System] Robot Connection Failed: {e}")
        ser = None

# ================= [1. 기반 클래스] =================
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

# ================= [2. 노드 클래스 구현] =================

class StartNode(BaseNode):
    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label="START"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out_id:
                dpg.add_text("Flow Out")
            self.outputs[out_id] = "Flow"

    def execute(self):
        return self.outputs

class UDPReceiverNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "UDP Receiver")
        self.port_input = None
        self.target_ip_input = None
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setblocking(False) # 비동기 소켓
        self.sock_feedback = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.is_bound = False
        self.data_out_id = None 

    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label=self.label):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as in_id:
                dpg.add_text("Flow In")
            self.inputs[in_id] = "Flow"

            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                self.port_input = dpg.add_input_int(label="Port", width=120, default_value=6000)
            
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                self.target_ip_input = dpg.add_input_text(label="Target IP", width=120, default_value="192.168.50.63")

            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as d_out:
                dpg.add_text("JSON Output")
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
                print(f"[UDP] Bind Success: {port}")
            except Exception as e:
                if "Address already in use" in str(e): self.is_bound = True
                else: return self.outputs

        # ★ 핵심 최적화 1: 버퍼 비우기 (Packet Flushing)
        # 쌓여있는 패킷을 모두 읽어버리고, 가장 마지막(최신) 패킷만 사용합니다.
        # 이렇게 해야 "밀림 현상(Lag)"이 사라집니다.
        last_data = None
        try:
            while True:
                data, addr = self.sock.recvfrom(4096)
                last_data = data # 계속 덮어씀
        except BlockingIOError:
            pass # 데이터가 더 이상 없으면 루프 종료
        except Exception:
            pass

        # 최신 데이터가 있으면 출력 데이터 갱신
        if last_data:
            self.output_data[self.data_out_id] = last_data.decode()

        # 피드백 전송 (상태 보고)
        try:
            u_x = -current_pos['y'] / 1000.0
            u_y = current_pos['z'] / 1000.0
            u_z = current_pos['x'] / 1000.0
            
            feedback_data = {
                "x": u_x, "y": u_y, "z": u_z, 
                "gripper": current_pos['gripper'], "status": 0
            }
            self.sock_feedback.sendto(json.dumps(feedback_data).encode(), (UNITY_IP, FEEDBACK_PORT))
        except Exception:
            pass

        return self.outputs

class UnityControlNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "Unity Control")
        self.data_in_id = None
        self.out_x = None; self.out_y = None; self.out_z = None; self.out_g = None

    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label=self.label):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as in_flow:
                dpg.add_text("Flow In")
            self.inputs[in_flow] = "Flow"

            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as d_in:
                dpg.add_text("JSON Packet")
            self.inputs[d_in] = "Data"
            self.data_in_id = d_in
            
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out_x:
                dpg.add_text("Target X")
            self.outputs[out_x] = "Data"; self.out_x = out_x

            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out_y:
                dpg.add_text("Target Y")
            self.outputs[out_y] = "Data"; self.out_y = out_y

            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out_z:
                dpg.add_text("Target Z")
            self.outputs[out_z] = "Data"; self.out_z = out_z
            
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out_g:
                dpg.add_text("Target Grip")
            self.outputs[out_g] = "Data"; self.out_g = out_g
            
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as f_out:
                dpg.add_text("Flow Out")
            self.outputs[f_out] = "Flow"

    def execute(self):
        raw_json = self.fetch_input_data(self.data_in_id)
        if raw_json:
            try:
                parsed = json.loads(raw_json)
                msg_type = parsed.get("type", "MOVE")
                
                if msg_type == "MOVE":
                    rx = parsed.get('z', 0) * 1000.0
                    ry = -parsed.get('x', 0) * 1000.0
                    rz = parsed.get('y', 0) * 1000.0
                    rg = parsed.get('gripper', 40)

                    self.output_data[self.out_x] = rx
                    self.output_data[self.out_y] = ry
                    self.output_data[self.out_z] = rz
                    self.output_data[self.out_g] = rg
            except Exception:
                pass 
        return self.outputs

class RobotControlNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "Robot Driver (Optimized)")
        self.in_x = None; self.in_y = None; self.in_z = None; self.in_g = None
        self.field_x = None; self.field_y = None; self.field_z = None
        self.last_sent_cmd = "" # 마지막으로 보낸 명령 기억 (중복 전송 방지)

    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label=self.label):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as in_flow:
                dpg.add_text("Flow In")
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
                 dpg.add_text("Gripper In")
            self.inputs[g_in] = "Data"; self.in_g = g_in

            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as f_out:
                dpg.add_text("Flow Out")
            self.outputs[f_out] = "Flow"

    def execute(self):
        global current_pos
        
        tgt_x = self.fetch_input_data(self.in_x)
        tgt_y = self.fetch_input_data(self.in_y)
        tgt_z = self.fetch_input_data(self.in_z)
        tgt_g = self.fetch_input_data(self.in_g)

        if tgt_x is None: tgt_x = current_pos['x']
        if tgt_y is None: tgt_y = current_pos['y']
        if tgt_z is None: tgt_z = current_pos['z']
        if tgt_g is None: tgt_g = current_pos['gripper']

        dpg.set_value(self.field_x, float(tgt_x))
        dpg.set_value(self.field_y, float(tgt_y))
        dpg.set_value(self.field_z, float(tgt_z))
        
        # 스무딩 계산
        dx = tgt_x - current_pos['x']
        dy = tgt_y - current_pos['y']
        dz = tgt_z - current_pos['z']
        
        # 목표가 매우 가까우면 스무딩 없이 바로 도달 (미세 떨림 방지)
        if abs(dx) < 1.0 and abs(dy) < 1.0 and abs(dz) < 1.0:
            next_x, next_y, next_z = tgt_x, tgt_y, tgt_z
        else:
            next_x = current_pos['x'] + dx * SMOOTHING_FACTOR
            next_y = current_pos['y'] + dy * SMOOTHING_FACTOR
            next_z = current_pos['z'] + dz * SMOOTHING_FACTOR
        
        # 안전 범위 체크
        if not (LIMITS['min_x'] <= next_x <= LIMITS['max_x']): next_x = current_pos['x']
        if not (LIMITS['min_y'] <= next_y <= LIMITS['max_y']): next_y = current_pos['y']
        if not (LIMITS['min_z'] <= next_z <= LIMITS['max_z']): next_z = current_pos['z']

        current_pos['x'] = next_x
        current_pos['y'] = next_y
        current_pos['z'] = next_z
        current_pos['gripper'] = tgt_g

        # ★ 핵심 최적화 2: 중복 명령 전송 방지 (툭툭 끊김 해결)
        # 소수점 1자리까지 비교해서 변화가 없으면 시리얼 전송을 건너뜁니다.
        cmd_move = f"G0 X{next_x:.1f} Y{next_y:.1f} Z{next_z:.1f}\n"
        
        if cmd_move != self.last_sent_cmd:
            if ser and ser.is_open:
                ser.write(cmd_move.encode())
                ser.write(f"M3 S{tgt_g}\n".encode())
            self.last_sent_cmd = cmd_move # 보낸 명령 기억
        
        return self.outputs

class JsonParseNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "Simple Parser")
        self.data_in_id = None
        self.out_x = None; self.out_y = None; self.out_z = None

    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label=self.label):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as in_flow:
                dpg.add_text("Flow In")
            self.inputs[in_flow] = "Flow"

            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as d_in:
                dpg.add_text("JSON In")
            self.inputs[d_in] = "Data"
            self.data_in_id = d_in
            
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out_x:
                dpg.add_text("Raw X")
            self.outputs[out_x] = "Data"; self.out_x = out_x

            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out_y:
                dpg.add_text("Raw Y")
            self.outputs[out_y] = "Data"; self.out_y = out_y
            
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as f_out:
                dpg.add_text("Flow Out")
            self.outputs[f_out] = "Flow"

    def execute(self):
        raw_json = self.fetch_input_data(self.data_in_id)
        if raw_json:
            try:
                parsed = json.loads(raw_json)
                self.output_data[self.out_x] = parsed.get("x", 0)
                self.output_data[self.out_y] = parsed.get("y", 0)
            except: pass
        return self.outputs

class PrintNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "Print Log")
        self.input_field = None; self.data_in_id = None 

    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label=self.label):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as in_id:
                dpg.add_text("Flow In")
            self.inputs[in_id] = "Flow"

            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as d_in:
                dpg.add_text("Data In")
            self.inputs[d_in] = "Data"; self.data_in_id = d_in

            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                self.input_field = dpg.add_input_text(label="Msg", width=120, default_value="Test")

            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out_id:
                dpg.add_text("Flow Out")
            self.outputs[out_id] = "Flow"

    def execute(self):
        received_data = self.fetch_input_data(self.data_in_id)
        if received_data is not None:
            print(f"[Log] Val: {received_data}")
        return self.outputs

# ================= [3. 팩토리 & 실행 엔진] =================
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
            
        if node:
            node.build_ui()
            node_registry[node_id] = node
            return node
        return None

def run_loop():
    global is_running
    print("--- [Loop Start] ---")
    
    start_node = None
    for node in node_registry.values():
        if isinstance(node, StartNode):
            start_node = node
            break
            
    if not start_node:
        print("[Error] No START Node")
        is_running = False
        return

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
                                next_node = node_registry[target_node_id]
                            break
                if next_node: break 
            current_node = next_node
        
        # ★ 핵심 최적화 3: 루프 주기 단축 (반응성 향상)
        # 0.05 (20fps) -> 0.02 (50fps)
        time.sleep(0.02)

    print("--- [Loop End] ---")

def toggle_execution(sender, app_data):
    global is_running, run_thread
    if not is_running:
        is_running = True
        dpg.set_item_label("btn_run", "STOP (Running)")
        run_thread = threading.Thread(target=run_loop, daemon=True)
        run_thread.start()
    else:
        is_running = False
        dpg.set_item_label("btn_run", "RUN (Start)")

def delete_selection(sender, app_data):
    selected_links = dpg.get_selected_links("node_editor")
    for link_id in selected_links:
        dpg.delete_item(link_id)
        if link_id in link_registry: del link_registry[link_id]
    selected_nodes = dpg.get_selected_nodes("node_editor")
    for node_id in selected_nodes:
        dpg.delete_item(node_id)
        if node_id in node_registry: del node_registry[node_id]

def link_cb(sender, app_data):
    if len(app_data) == 3: src, dst = app_data[1], app_data[2]
    else: src, dst = app_data[0], app_data[1]
    link_id = dpg.add_node_link(src, dst, parent=sender)
    link_registry[link_id] = {'source': src, 'target': dst}

def del_link_cb(sender, app_data):
    dpg.delete_item(app_data)
    if app_data in link_registry: del link_registry[app_data]

def add_node_cb(sender, app_data, user_data):
    NodeFactory.create_node(user_data)

init_serial()

dpg.create_context()
font_path = "/usr/share/fonts/truetype/nanum/NanumGothic.ttf"
with dpg.font_registry():
    if os.path.exists(font_path):
        with dpg.font(font_path, 18) as kr_font:
            dpg.add_font_range_hint(dpg.mvFontRangeHint_Default)
            dpg.add_font_range_hint(dpg.mvFontRangeHint_Korean)
        dpg.bind_font(kr_font)

with dpg.handler_registry():
    dpg.add_key_press_handler(dpg.mvKey_Delete, callback=delete_selection)

with dpg.window(label="Visual Scripting V6 (Optimized)", width=1000, height=700):
    with dpg.group(horizontal=True):
        dpg.add_button(label="START", callback=add_node_cb, user_data="START")
        dpg.add_button(label="UDP Recv", callback=add_node_cb, user_data="UDP_RECV")
        dpg.add_button(label="Unity Ctrl", callback=add_node_cb, user_data="UNITY_CONTROL")
        dpg.add_button(label="Robot Driver", callback=add_node_cb, user_data="ROBOT_CONTROL")
        dpg.add_button(label="Print", callback=add_node_cb, user_data="PRINT")
        dpg.add_spacer(width=20)
        dpg.add_button(label="Simple JSON", callback=add_node_cb, user_data="JSON_PARSE")
        dpg.add_spacer(width=50)
        dpg.add_button(label="RUN (Start)", tag="btn_run", callback=toggle_execution, width=150)

    dpg.add_separator()
    with dpg.node_editor(tag="node_editor", callback=link_cb, delink_callback=del_link_cb):
        pass

dpg.create_viewport(title='PyGui Editor V6 (Fast)', width=1000, height=700)
dpg.setup_dearpygui()
dpg.show_viewport()
dpg.start_dearpygui()
dpg.destroy_context()