import dearpygui.dearpygui as dpg
import time
import os
import socket
import json
from abc import ABC, abstractmethod

# ================= [전역 설정] =================
# 노드 객체들을 저장할 저장소 {node_id (int): NodeInstance}
node_registry = {}
link_registry = {}

# ================= [1. 기반 클래스 설계 (Architecture)] =================
class BaseNode(ABC):
    """모든 노드의 부모 클래스"""
    def __init__(self, node_id, label):
        self.node_id = node_id
        self.label = label
        self.inputs = {}  # {attribute_id: "name"}
        self.outputs = {} # {attribute_id: "name"}
        self.input_values = {} # 실행 시 들어온 데이터 저장

    @abstractmethod
    def build_ui(self):
        """GUI에 노드를 그리는 함수"""
        pass

    @abstractmethod
    def execute(self):
        """노드의 고유 기능을 실행하는 함수"""
        pass

# ================= [2. 구체적 노드 클래스 (Concrete Nodes)] =================

class StartNode(BaseNode):
    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label="시작 (START)"):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out_id:
                dpg.add_text("출력 흐름")
            self.outputs[out_id] = "Flow"

    def execute(self):
        print("[시스템] 시작 노드 실행")
        return self.outputs  # 연결된 다음 노드를 찾기 위해 출력 정보 반환

class PrintNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "출력 (PRINT)")
        self.input_field = None

    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label=self.label):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as in_id:
                dpg.add_text("입력 흐름")
            self.inputs[in_id] = "Flow"

            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                self.input_field = dpg.add_input_text(label="메시지", width=120, default_value="Test")

            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out_id:
                dpg.add_text("출력 흐름")
            self.outputs[out_id] = "Flow"

    def execute(self):
        text = dpg.get_value(self.input_field)
        print(f"[출력] {text}")
        return self.outputs

class UDPReceiverNode(BaseNode):
    """유니티에서 데이터를 받아오는 노드"""
    def __init__(self, node_id):
        super().__init__(node_id, "UDP 수신 (RECV)")
        self.port_input = None
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setblocking(False) # 비동기 설정 (멈춤 방지)
        self.is_bound = False

    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label=self.label):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as in_id:
                dpg.add_text("입력 흐름")
            self.inputs[in_id] = "Flow"

            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                self.port_input = dpg.add_input_int(label="Port", width=100, default_value=6000)

            # 데이터 출력 소켓 (문자열)
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as data_out_id:
                dpg.add_text("데이터 출력 (String)")
            self.outputs[data_out_id] = "Data_String"
            
            # 흐름 출력 소켓
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as flow_out_id:
                dpg.add_text("출력 흐름")
            self.outputs[flow_out_id] = "Flow"

    def execute(self):
        port = dpg.get_value(self.port_input)
        
        # 소켓 바인딩 (최초 1회)
        if not self.is_bound:
            try:
                self.sock.bind(('0.0.0.0', port))
                self.is_bound = True
                print(f"[UDP] 포트 {port} 바인딩 성공")
            except Exception as e:
                print(f"[오류] UDP 바인딩 실패: {e}")
                return self.outputs

        # 데이터 수신 시도
        try:
            data, addr = self.sock.recvfrom(1024)
            decoded_data = data.decode()
            print(f"[UDP] 수신됨: {decoded_data}")
            
            # TODO: 여기서 데이터를 다음 노드로 넘겨주는 로직이 필요함 (Data Flow)
            # 현재는 프로토타입이므로 출력만 수행
        except BlockingIOError:
            print("[UDP] 수신된 데이터 없음 (건너뜀)")
        except Exception as e:
            print(f"[오류] 수신 중 에러: {e}")

        return self.outputs

# ================= [3. 팩토리 (Factory Pattern)] =================
class NodeFactory:
    @staticmethod
    def create_node(node_type):
        # DPG에서 고유 ID 생성
        node_id = dpg.generate_uuid()
        
        node = None
        if node_type == "START":
            node = StartNode(node_id, "START")
        elif node_type == "PRINT":
            node = PrintNode(node_id)
        elif node_type == "UDP_RECV":
            node = UDPReceiverNode(node_id)
            
        if node:
            node.build_ui()
            node_registry[node_id] = node # 레지스트리에 등록
            return node
        return None

# ================= [4. 실행 엔진 (Execution Engine)] =================
def execute_graph():
    print("\n--- [실행 시작] ---")
    
    # 1. Start 노드 찾기
    start_node = None
    for node in node_registry.values():
        if isinstance(node, StartNode):
            start_node = node
            break
            
    if not start_node:
        print("[오류] START 노드가 없습니다.")
        return

    # 2. 순차 실행 루프
    current_node = start_node
    while current_node:
        # (1) 노드 실행
        outputs = current_node.execute()
        
        # (2) 다음 노드 찾기
        next_node = None
        
        # 현재 노드의 모든 Output 소켓에 대해
        for out_attr_id in outputs:
            # 해당 소켓에서 시작하는 링크 찾기
            for link_id, link_data in link_registry.items():
                if link_data['source'] == out_attr_id:
                    # 링크 끝에 연결된 노드 ID 찾기
                    target_attr_id = link_data['target']
                    target_node_id = dpg.get_item_parent(target_attr_id)
                    
                    # 레지스트리에서 노드 객체 가져오기
                    if target_node_id in node_registry:
                        next_node = node_registry[target_node_id]
                    break
            if next_node: break # 하나 찾으면 루프 탈출 (단일 흐름)
            
        current_node = next_node
        time.sleep(0.1) # 실행 확인용 딜레이

    print("--- [실행 완료] ---")

# ================= [GUI 콜백 및 설정] =================
def link_cb(sender, app_data):
    # 호환성 처리 (2개 or 3개)
    if len(app_data) == 3: src, dst = app_data[1], app_data[2]
    else: src, dst = app_data[0], app_data[1]
    
    link_id = dpg.add_node_link(src, dst, parent=sender)
    link_registry[link_id] = {'source': src, 'target': dst}

def del_link_cb(sender, app_data):
    dpg.delete_item(app_data)
    if app_data in link_registry:
        del link_registry[app_data]

def add_node_cb(sender, app_data, user_data):
    NodeFactory.create_node(user_data)

# ================= [메인 루프 (수정됨)] =================
dpg.create_context()

# 한글 폰트 설정
font_path = "/usr/share/fonts/truetype/nanum/NanumGothic.ttf"
with dpg.font_registry():
    if os.path.exists(font_path):
        with dpg.font(font_path, 18) as kr_font:
            dpg.add_font_range_hint(dpg.mvFontRangeHint_Default)
            dpg.add_font_range_hint(dpg.mvFontRangeHint_Korean)
        dpg.bind_font(kr_font)

with dpg.window(label="Visual Scripting Tool V2", width=900, height=700):
    with dpg.group(horizontal=True):
        dpg.add_button(label="START 추가", callback=add_node_cb, user_data="START")
        dpg.add_button(label="PRINT 추가", callback=add_node_cb, user_data="PRINT")
        dpg.add_button(label="UDP 수신 추가", callback=add_node_cb, user_data="UDP_RECV")
        dpg.add_spacer(width=50)
        dpg.add_button(label="▶ 스크립트 실행", callback=execute_graph, width=150)

    dpg.add_separator()
    
    # [수정 포인트] tag="node_editor" 가 반드시 있어야 합니다!
    with dpg.node_editor(tag="node_editor", callback=link_cb, delink_callback=del_link_cb):
        pass

dpg.create_viewport(title='PyGui Editor V2', width=900, height=700)
dpg.setup_dearpygui()
dpg.show_viewport()
dpg.start_dearpygui()
dpg.destroy_context()