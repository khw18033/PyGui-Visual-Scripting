import dearpygui.dearpygui as dpg
import time
import os

# ================= [데이터 구조] =================
nodes = {}       
links = {}       

# ================= [실행 엔진] =================
def execute_graph():
    print("\n--- [실행 시작] ---")
    
    # 1. Start 노드 찾기
    current_node_id = None
    for nid, info in nodes.items():
        if info['type'] == "START":
            current_node_id = nid
            break
            
    if current_node_id is None:
        print("[오류] 'START' 노드를 찾을 수 없습니다.")
        return

    # 2. 링크를 타고 다음 노드로 이동하며 실행
    while current_node_id is not None:
        try:
            node_info = nodes[current_node_id]
        except KeyError:
            print(f"[오류] 노드 ID {current_node_id} 정보를 찾을 수 없습니다.")
            break

        node_type = node_info['type']
        
        # --- [노드별 기능 실행] ---
        if node_type == "START":
            print("[시스템] 시작 (Start Node)")
            
        elif node_type == "PRINT":
            text = dpg.get_value(node_info['input_tag'])
            print(f"[출력] {text}")
            
        elif node_type == "DELAY":
            sec = dpg.get_value(node_info['input_tag'])
            print(f"[지연] {sec}초 대기 중...")
            time.sleep(sec) 
            
        # 3. 다음 노드 찾기
        output_attr = node_info['out_attr']
        next_link_id = None
        
        for lid, link_data in links.items():
            if link_data['source'] == output_attr:
                next_link_id = lid
                break
        
        if next_link_id:
            target_attr = links[next_link_id]['target']
            current_node_id = dpg.get_item_parent(target_attr)
        else:
            print("--- [실행 완료] ---")
            current_node_id = None 

# ================= [GUI 이벤트 콜백] =================
def link_callback(sender, app_data):
    # [수정됨] 데이터 길이에 따라 유동적으로 처리
    if len(app_data) == 3:
        src, dst = app_data[1], app_data[2]
    else:
        src, dst = app_data[0], app_data[1]

    link_id = dpg.add_node_link(src, dst, parent=sender)
    links[link_id] = {'source': src, 'target': dst}

def del_link_callback(sender, app_data):
    dpg.delete_item(app_data)
    if app_data in links:
        del links[app_data]

def add_node(sender, app_data, user_data):
    node_type = user_data
    
    node_label = node_type
    if node_type == "START": node_label = "시작 (START)"
    elif node_type == "PRINT": node_label = "출력 (PRINT)"
    elif node_type == "DELAY": node_label = "지연 (DELAY)"

    with dpg.node(parent="node_editor", label=node_label) as new_node:
        
        if node_type == "START":
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out_attr:
                dpg.add_text("출력 흐름")
            nodes[new_node] = {'type': "START", 'out_attr': out_attr}
            
        elif node_type == "PRINT":
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input):
                dpg.add_text("입력 흐름")
            
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                input_field = dpg.add_input_text(label="메시지", width=120, default_value="Hello Robot")
                
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out_attr:
                dpg.add_text("출력 흐름")
            
            nodes[new_node] = {'type': "PRINT", 'out_attr': out_attr, 'input_tag': input_field}
            
        elif node_type == "DELAY":
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Input):
                dpg.add_text("입력 흐름")
                
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                input_field = dpg.add_input_float(label="시간 (초)", width=100, default_value=1.0)
                
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Output) as out_attr:
                dpg.add_text("출력 흐름")
                
            nodes[new_node] = {'type': "DELAY", 'out_attr': out_attr, 'input_tag': input_field}

# ================= [메인 GUI 구성] =================
dpg.create_context()

font_path = "/usr/share/fonts/truetype/nanum/NanumGothic.ttf"
with dpg.font_registry():
    if os.path.exists(font_path):
        with dpg.font(font_path, 18) as kr_font:
            dpg.add_font_range_hint(dpg.mvFontRangeHint_Default)
            dpg.add_font_range_hint(dpg.mvFontRangeHint_Korean)
        dpg.bind_font(kr_font)
        print("[시스템] 한글 폰트 로드 성공")
    else:
        print(f"[시스템] 폰트 파일을 찾을 수 없습니다: {font_path}")

with dpg.window(label="비주얼 스크립팅 도구", width=800, height=600):
    
    with dpg.group(horizontal=True):
        dpg.add_button(label="START 추가", callback=add_node, user_data="START")
        dpg.add_button(label="PRINT 추가", callback=add_node, user_data="PRINT")
        dpg.add_button(label="DELAY 추가", callback=add_node, user_data="DELAY")
        dpg.add_spacer(width=50)
        dpg.add_button(label="스크립트 실행 (RUN)", callback=execute_graph, width=150)

    dpg.add_separator()
    dpg.add_text("노드를 추가하고 점끼리 드래그하여 연결하세요. [Del]키로 연결 삭제 가능.")

    with dpg.node_editor(tag="node_editor", callback=link_callback, delink_callback=del_link_callback):
        pass 

dpg.create_viewport(title='PyGui Visual Scripting', width=800, height=600)
dpg.setup_dearpygui()
dpg.show_viewport()
dpg.start_dearpygui()
dpg.destroy_context()