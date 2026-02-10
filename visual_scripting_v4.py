import dearpygui.dearpygui as dpg
import time
import os
import socket
import json
import serial 
from abc import ABC, abstractmethod

# ================= [전역 설정] =================
node_registry = {}
link_registry = {}
ser = None 

# ================= [0. MT4 로봇 연결 및 초기화] =================
def init_serial():
    global ser
    try:
        # 라즈베리파이: '/dev/ttyUSB0' 또는 '/dev/ttyACM0'
        # MT4 로봇은 보통 115200 보드레이트를 사용합니다.
        ser = serial.Serial('/dev/ttyUSB0', 115200, timeout=1)
        print("[시스템] MT4 로봇 연결 성공 (/dev/ttyUSB0)")
        
        # [기능 4] 연결 후 호밍 및 기본 위치 이동
        time.sleep(2) # 보드 리셋 대기
        
        print("[MT4] 호밍(Homing) 시작... (G28)")
        ser.write(b"G28\n") 
        time.sleep(3) # 호밍 완료될 때까지 안전 대기
        
        print("[MT4] 기본 위치로 이동 (200, 0, 120)")
        ser.write(b"G0 X200 Y0 Z120\n")
        
    except Exception as e:
        print(f"[시스템] ⚠️ 로봇 연결 실패 (시뮬레이션 모드로 동작): {e}")
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
        """연결된 노드로부터 데이터를 가져옵니다."""
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
        with dpg.node(tag=self.node_id, parent="node_editor", label="시작 (START)"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out_id:
                dpg.add_text("출력 흐름")
            self.outputs[out_id] = "Flow"

    def execute(self):
        print("[시스템] 시작 노드 실행")
        return self.outputs

class UDPReceiverNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "UDP 수신 (RECV)")
        self.port_input = None
        self.target_ip_input = None # [기능 3] Target IP 변수
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setblocking(False)
        self.is_bound = False
        self.data_out_id = None 

    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label=self.label):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as in_id:
                dpg.add_text("입력 흐름")
            self.inputs[in_id] = "Flow"

            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                self.port_input = dpg.add_input_int(label="Port", width=120, default_value=6000)
            
            # [기능 3] Target IP 입력 필드 추가 (기본값 192.168.50.63)
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                self.target_ip_input = dpg.add_input_text(label="Target IP", width=120, default_value="192.168.50.63")

            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as d_out:
                dpg.add_text("데이터 출력 (JSON)")
            self.outputs[d_out] = "Data"
            self.data_out_id = d_out 
            
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as f_out:
                dpg.add_text("출력 흐름")
            self.outputs[f_out] = "Flow"

    def execute(self):
        port = dpg.get_value(self.port_input)
        
        if not self.is_bound:
            try:
                self.sock.bind(('0.0.0.0', port))
                self.is_bound = True
                print(f"[UDP] 포트 {port} 바인딩 성공")
                print(f"[UDP] Target IP 설정: {dpg.get_value(self.target_ip_input)}")
            except Exception as e:
                print(f"[오류] 포트 바인딩 실패: {e}")
                return self.outputs

        try:
            # 4096 바이트만큼 수신 시도
            data, addr = self.sock.recvfrom(4096)
            decoded_data = data.decode()
            
            # (선택 사항) 만약 특정 IP(Target IP)에서 온 것만 받고 싶다면:
            # target_ip = dpg.get_value(self.target_ip_input)
            # if addr[0] != target_ip: return self.outputs 
            
            print(f"[UDP] 수신: {decoded_data} (from {addr[0]})")
            self.output_data[self.data_out_id] = decoded_data
        except BlockingIOError:
            # 데이터가 아직 안 옴
            self.output_data[self.data_out_id] = None 
        except Exception as e:
            print(f"[오류] UDP: {e}")
        return self.outputs

class JsonParseNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "JSON 파서")
        self.data_in_id = None
        self.out_x = None; self.out_y = None; self.out_z = None; self.out_g = None

    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label=self.label):
            # 1. 흐름 입력
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as in_flow:
                dpg.add_text("입력 흐름")
            self.inputs[in_flow] = "Flow"

            # 2. JSON 데이터 입력
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as d_in:
                dpg.add_text("JSON 입력")
            self.inputs[d_in] = "Data"
            self.data_in_id = d_in
            
            dpg.add_separator()
            
            # [기능 2] 좌표 출력 연결점 (Data Out)
            # X 좌표
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out_x:
                dpg.add_text("X 좌표")
            self.outputs[out_x] = "Data"; self.out_x = out_x

            # Y 좌표
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out_y:
                dpg.add_text("Y 좌표")
            self.outputs[out_y] = "Data"; self.out_y = out_y

            # Z 좌표
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out_z:
                dpg.add_text("Z 좌표")
            self.outputs[out_z] = "Data"; self.out_z = out_z
            
            # Gripper 출력
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out_g:
                dpg.add_text("Gripper")
            self.outputs[out_g] = "Data"; self.out_g = out_g
            
            dpg.add_separator()
            
            # 흐름 출력
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as f_out:
                dpg.add_text("출력 흐름")
            self.outputs[f_out] = "Flow"

    def execute(self):
        raw_json = self.fetch_input_data(self.data_in_id)
        if raw_json:
            try:
                parsed = json.loads(raw_json)
                self.output_data[self.out_x] = parsed.get("x", 0)
                self.output_data[self.out_y] = parsed.get("y", 0)
                self.output_data[self.out_z] = parsed.get("z", 0)
                self.output_data[self.out_g] = parsed.get("gripper", 0)
                print(f"[파서] 분해 완료: X={parsed.get('x')} Y={parsed.get('y')} Z={parsed.get('z')}")
            except Exception:
                print("[파서] JSON 데이터 형식 오류")
        else:
            # 데이터 없음
            pass
        return self.outputs

class RobotMoveNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "로봇 이동")
        self.in_x = None; self.in_y = None; self.in_z = None
        self.field_x = None; self.field_y = None; self.field_z = None

    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label=self.label):
            # 1. 흐름 입력
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as in_flow:
                dpg.add_text("입력 흐름")
            self.inputs[in_flow] = "Flow"

            # [기능 1] 입력 필드 + 연결점
            # 유니티에서 값이 오면 이 필드 숫자가 자동으로 바뀝니다 (모니터링 용도)
            
            # X 좌표
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as x_in:
                self.field_x = dpg.add_input_float(label="X", width=80, default_value=200.0)
            self.inputs[x_in] = "Data"; self.in_x = x_in
            
            # Y 좌표
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as y_in:
                self.field_y = dpg.add_input_float(label="Y", width=80, default_value=0.0)
            self.inputs[y_in] = "Data"; self.in_y = y_in

            # Z 좌표
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as z_in:
                self.field_z = dpg.add_input_float(label="Z", width=80, default_value=120.0)
            self.inputs[z_in] = "Data"; self.in_z = z_in

            # 흐름 출력
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as f_out:
                dpg.add_text("출력 흐름")
            self.outputs[f_out] = "Flow"

    def execute(self):
        # 1. 파서(Unity)에서 데이터 가져오기
        x_val = self.fetch_input_data(self.in_x)
        y_val = self.fetch_input_data(self.in_y)
        z_val = self.fetch_input_data(self.in_z)

        # 2. 데이터가 있으면 필드 업데이트 (모니터링) 후 사용
        if x_val is not None:
            dpg.set_value(self.field_x, float(x_val))
            x = float(x_val)
        else:
            x = dpg.get_value(self.field_x)

        if y_val is not None:
            dpg.set_value(self.field_y, float(y_val))
            y = float(y_val)
        else:
            y = dpg.get_value(self.field_y)

        if z_val is not None:
            dpg.set_value(self.field_z, float(z_val))
            z = float(z_val)
        else:
            z = dpg.get_value(self.field_z)

        command = f"G0 X{x} Y{y} Z{z}\n"
        print(f"[MT4] 전송: {command.strip()}")

        if ser and ser.is_open:
            ser.write(command.encode())
        
        return self.outputs

class PrintNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "출력 (PRINT)")
        self.input_field = None; self.data_in_id = None 

    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label=self.label):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as in_id:
                dpg.add_text("입력 흐름")
            self.inputs[in_id] = "Flow"

            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as d_in:
                dpg.add_text("데이터 입력")
            self.inputs[d_in] = "Data"; self.data_in_id = d_in

            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                self.input_field = dpg.add_input_text(label="메시지", width=120, default_value="Test")

            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out_id:
                dpg.add_text("출력 흐름")
            self.outputs[out_id] = "Flow"

    def execute(self):
        received_data = self.fetch_input_data(self.data_in_id)
        if received_data is not None:
            print(f"[출력] 값: {received_data}")
        else:
            text = dpg.get_value(self.input_field)
            print(f"[출력] 텍스트: {text}")
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
        elif node_type == "JSON_PARSE": node = JsonParseNode(node_id)
        elif node_type == "ROBOT_MOVE": node = RobotMoveNode(node_id)
            
        if node:
            node.build_ui()
            node_registry[node_id] = node
            return node
        return None

def execute_graph():
    print("\n--- [실행 시작] ---")
    start_node = None
    for node in node_registry.values():
        if isinstance(node, StartNode):
            start_node = node
            break
            
    if not start_node:
        print("[오류] START 노드 없음")
        return

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
        time.sleep(0.05)
    print("--- [실행 종료] ---")

def delete_selection(sender, app_data):
    selected_links = dpg.get_selected_links("node_editor")
    for link_id in selected_links:
        dpg.delete_item(link_id)
        if link_id in link_registry: del link_registry[link_id]
    selected_nodes = dpg.get_selected_nodes("node_editor")
    for node_id in selected_nodes:
        dpg.delete_item(node_id)
        if node_id in node_registry: del node_registry[node_id]

# ================= [GUI 구성] =================
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

# 시리얼 초기화 실행
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

with dpg.window(label="Visual Scripting V4", width=1000, height=700):
    with dpg.group(horizontal=True):
        dpg.add_button(label="START", callback=add_node_cb, user_data="START")
        dpg.add_button(label="UDP 수신", callback=add_node_cb, user_data="UDP_RECV")
        dpg.add_button(label="JSON 파서", callback=add_node_cb, user_data="JSON_PARSE")
        dpg.add_button(label="로봇 이동", callback=add_node_cb, user_data="ROBOT_MOVE")
        dpg.add_button(label="PRINT", callback=add_node_cb, user_data="PRINT")
        dpg.add_spacer(width=50)
        dpg.add_button(label="RUN", callback=execute_graph, width=150)

    dpg.add_separator()
    with dpg.node_editor(tag="node_editor", callback=link_cb, delink_callback=del_link_cb):
        pass

dpg.create_viewport(title='PyGui Editor V4', width=1000, height=700)
dpg.setup_dearpygui()
dpg.show_viewport()
dpg.start_dearpygui()
dpg.destroy_context()