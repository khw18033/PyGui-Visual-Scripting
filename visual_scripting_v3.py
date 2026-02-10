import dearpygui.dearpygui as dpg
import time
import os
import socket
import json
from abc import ABC, abstractmethod

# ================= [전역 설정] =================
node_registry = {}  # {node_id: NodeInstance}
link_registry = {}  # {link_id: {source: id, target: id}}

# ================= [1. 기반 클래스 (Architecture)] =================
class BaseNode(ABC):
    def __init__(self, node_id, label):
        self.node_id = node_id
        self.label = label
        self.inputs = {}      # {attr_id: type}
        self.outputs = {}     # {attr_id: type}
        self.output_data = {} # {attr_id: 실제_데이터_값} ★ 택배 상자 보관소

    @abstractmethod
    def build_ui(self):
        pass

    @abstractmethod
    def execute(self):
        pass

    # ★ [핵심 기능] 내 입력 구멍에 연결된 노드를 찾아가서 데이터를 뺏어오는 함수
    def fetch_input_data(self, input_attr_id):
        # 1. 내 구멍(input_attr_id)에 연결된 링크를 찾는다.
        target_link = None
        for link in link_registry.values():
            if link['target'] == input_attr_id:
                target_link = link
                break
        
        if not target_link: return None # 연결된 게 없음

        # 2. 링크 반대편(source)의 속성 ID와 노드 ID를 찾는다.
        source_attr_id = target_link['source']
        source_node_id = dpg.get_item_parent(source_attr_id)
        
        # 3. 그 노드의 'output_data' 창고에서 값을 꺼내온다.
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
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setblocking(False)
        self.is_bound = False
        self.data_out_id = None # 데이터를 내보낼 구멍 ID

    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label=self.label):
            # (1) 흐름 입력
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as in_id:
                dpg.add_text("입력 흐름")
            self.inputs[in_id] = "Flow"

            # (2) 포트 설정
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                self.port_input = dpg.add_input_int(label="Port", width=100, default_value=6000)

            # (3) ★ 데이터 출력 (택배 보내는 곳)
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as d_out:
                dpg.add_text("데이터 출력 (String)")
            self.outputs[d_out] = "Data"
            self.data_out_id = d_out # ID 기억해두기
            
            # (4) 흐름 출력
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as f_out:
                dpg.add_text("출력 흐름")
            self.outputs[f_out] = "Flow"

    def execute(self):
        port = dpg.get_value(self.port_input)
        
        # 소켓 바인딩 (1회만)
        if not self.is_bound:
            try:
                self.sock.bind(('0.0.0.0', port))
                self.is_bound = True
                print(f"[UDP] 포트 {port} 열림")
            except Exception as e:
                print(f"[오류] 포트 바인딩 실패: {e}")
                return self.outputs

        try:
            # 데이터 수신
            data, addr = self.sock.recvfrom(4096)
            decoded_data = data.decode()
            print(f"[UDP] 데이터 수신됨 ({len(data)} bytes)")
            
            # ★ 핵심: 내 창고(output_data)에 데이터를 넣어둠. (다음 노드가 가져가라고)
            self.output_data[self.data_out_id] = decoded_data
            
        except BlockingIOError:
            print("[UDP] 들어온 데이터 없음 (Skip)")
            self.output_data[self.data_out_id] = None # 없으면 빈 값
        except Exception as e:
            print(f"[오류] {e}")

        return self.outputs

class PrintNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "출력 (PRINT)")
        self.input_field = None
        self.data_in_id = None # 데이터를 받을 구멍 ID

    def build_ui(self):
        with dpg.node(tag=self.node_id, parent="node_editor", label=self.label):
            # (1) 흐름 입력
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as in_id:
                dpg.add_text("입력 흐름")
            self.inputs[in_id] = "Flow"

            # (2) ★ 데이터 입력 (택배 받는 곳)
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input) as d_in:
                dpg.add_text("데이터 입력 (Any)")
            self.inputs[d_in] = "Data"
            self.data_in_id = d_in

            # (3) 기본 텍스트 입력창
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                self.input_field = dpg.add_input_text(label="기본 메시지", width=120, default_value="Test")

            # (4) 흐름 출력
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out_id:
                dpg.add_text("출력 흐름")
            self.outputs[out_id] = "Flow"

    def execute(self):
        # ★ 1. 데이터 구멍에 연결된 게 있는지 확인하고 가져옴
        received_data = self.fetch_input_data(self.data_in_id)
        
        if received_data is not None:
            print(f"[출력] (Data 수신): {received_data}")
        else:
            # 연결된 데이터가 없으면 입력창 내용 출력
            text = dpg.get_value(self.input_field)
            print(f"[출력] (기본 Text): {text}")
            
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
            
        if node:
            node.build_ui()
            node_registry[node_id] = node
            return node
        return None

def execute_graph():
    print("\n--- [스크립트 실행] ---")
    
    # Start 노드 찾기
    start_node = None
    for node in node_registry.values():
        if isinstance(node, StartNode):
            start_node = node
            break
            
    if not start_node:
        print("[오류] START 노드가 없습니다.")
        return

    # 실행 루프
    current_node = start_node
    while current_node:
        # (1) 노드 기능 실행
        outputs = current_node.execute()
        
        # (2) 다음 노드 찾기 (Flow 타입만 추적)
        next_node = None
        
        # 현재 노드의 Output 중 'Flow' 타입인 것만 확인
        for out_attr_id, out_type in outputs.items():
            if out_type == "Flow":
                # 링크 레지스트리 뒤져서 연결된 놈 찾기
                for link in link_registry.values():
                    if link['source'] == out_attr_id:
                        target_node_id = dpg.get_item_parent(link['target'])
                        if target_node_id in node_registry:
                            next_node = node_registry[target_node_id]
                        break
            if next_node: break # 하나 찾으면 이동
            
        current_node = next_node
        time.sleep(0.05) # 너무 빠르면 안 보이니까 약간 딜레이

    print("--- [실행 종료] ---")

# ================= [GUI 이벤트 핸들러] =================
def link_cb(sender, app_data):
    # DPG 버전에 따른 호환성 처리
    if len(app_data) == 3: src, dst = app_data[1], app_data[2]
    else: src, dst = app_data[0], app_data[1]
    
    link_id = dpg.add_node_link(src, dst, parent=sender)
    link_registry[link_id] = {'source': src, 'target': dst}

def del_link_cb(sender, app_data):
    dpg.delete_item(app_data)
    if app_data in link_registry: del link_registry[app_data]

def add_node_cb(sender, app_data, user_data):
    NodeFactory.create_node(user_data)

# ================= [메인 윈도우 구성] =================
dpg.create_context()

# 한글 폰트
font_path = "/usr/share/fonts/truetype/nanum/NanumGothic.ttf"
with dpg.font_registry():
    if os.path.exists(font_path):
        with dpg.font(font_path, 18) as kr_font:
            dpg.add_font_range_hint(dpg.mvFontRangeHint_Default)
            dpg.add_font_range_hint(dpg.mvFontRangeHint_Korean)
        dpg.bind_font(kr_font)

with dpg.window(label="Visual Scripting Tool V3 (Data Flow)", width=900, height=700):
    
    with dpg.group(horizontal=True):
        dpg.add_button(label="START 추가", callback=add_node_cb, user_data="START")
        dpg.add_button(label="PRINT 추가", callback=add_node_cb, user_data="PRINT")
        dpg.add_button(label="UDP 수신 추가", callback=add_node_cb, user_data="UDP_RECV")
        dpg.add_spacer(width=50)
        dpg.add_button(label="스크립트 실행", callback=execute_graph, width=150)

    dpg.add_separator()
    dpg.add_text("Tip: 흰색 선은 '실행 순서', 색깔 선은 '데이터 이동'입니다.")

    # tag="node_editor" 필수!
    with dpg.node_editor(tag="node_editor", callback=link_cb, delink_callback=del_link_cb):
        pass

dpg.create_viewport(title='PyGui Editor V3', width=900, height=700)
dpg.setup_dearpygui()
dpg.show_viewport()
dpg.start_dearpygui()
dpg.destroy_context()