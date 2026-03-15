import json
import os
import dearpygui.dearpygui as dpg
from core.factory import NodeFactory

class GraphSerializer:
    SAVE_DIR = "Node_File_MT4"

    @classmethod
    def save_graph(cls, filename, engine):
        os.makedirs(cls.SAVE_DIR, exist_ok=True)
        if not filename.endswith(".json"): filename += ".json"
        filepath = os.path.join(cls.SAVE_DIR, filename)
        
        data = {"nodes": [], "links": []}
        for nid, node in engine.nodes.items():
            pos = dpg.get_item_pos(nid) or [0,0]
            data["nodes"].append({
                "type": node.type_str, "id": nid, "pos": pos, 
                "settings": getattr(node, 'state', {})
            })
        
        for link in engine.links:
            data["links"].append({
                "src_node": link['src_id'], "src_pin": link['src_pin'],
                "dst_node": link['dst_id'], "dst_pin": link['dst_pin']
            })
        
        try:
            with open(filepath, 'w') as f: json.dump(data, f, indent=4)
            print(f"[Serializer] Saved: {filename}")
        except Exception as e: print(f"[Serializer] Save Error: {e}")

    @classmethod
    def load_graph(cls, filename, engine, ui_manager):
        if not filename.endswith(".json"): filename += ".json"
        filepath = os.path.join(cls.SAVE_DIR, filename)
        if not os.path.exists(filepath): return
        
        # 기존 화면과 엔진 데이터 초기화
        for link in engine.links: dpg.delete_item(link['id'])
        for nid in engine.nodes: dpg.delete_item(nid)
        engine.nodes.clear(); engine.links.clear()
        
        try:
            with open(filepath, 'r') as f: data = json.load(f)
            
            id_map = {}
            for n_data in data.get("nodes", []):
                node = NodeFactory.create_node(n_data["type"], n_data["id"])
                if node:
                    id_map[n_data["id"]] = node.node_id
                    if hasattr(node, 'state'): node.state.update(n_data.get("settings", {}))
                    ui_manager.draw_node(node)
                    dpg.set_item_pos(node.node_id, n_data["pos"])
                    engine.add_node(node)
                    
            for l_data in data.get("links", []):
                if l_data["src_node"] in id_map and l_data["dst_node"] in id_map:
                    # 저장된 순정 라벨(Target X 등)을 DPG 태그명으로 복원하여 선을 그립니다.
                    src_tag = f"{id_map[l_data['src_node']]}_{l_data['src_pin']}"
                    dst_tag = f"{id_map[l_data['dst_node']]}_{l_data['dst_pin']}"
                    ui_manager.link_callback(ui_manager.editor_tag, [src_tag, dst_tag])
            print(f"[Serializer] Loaded: {filename}")
        except Exception as e: print(f"[Serializer] Load Error: {e}")

    @classmethod
    def get_save_files(cls):
        if not os.path.exists(cls.SAVE_DIR): return []
        return [f for f in os.listdir(cls.SAVE_DIR) if f.endswith(".json")]