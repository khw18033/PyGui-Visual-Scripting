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
        stale_nodes = []
        for nid, node in list(engine.nodes.items()):
            if not dpg.does_item_exist(nid):
                stale_nodes.append(nid)
                continue
            pos = dpg.get_item_pos(nid) or [0,0]
            data["nodes"].append({
                "type": node.type_str, "id": nid, "pos": pos, 
                "settings": getattr(node, 'settings', {})
            })

        for stale_nid in stale_nodes:
            engine.remove_node(stale_nid)
        
        stale_links = []
        for link in list(engine.links):
            lid = link.get('id')
            src_id = link.get('src_id')
            dst_id = link.get('dst_id')
            if (lid is not None and not dpg.does_item_exist(lid)) or src_id not in engine.nodes or dst_id not in engine.nodes:
                stale_links.append(lid)
                continue
            data["links"].append({
                "src_node": link['src_id'], "src_pin": link['src_pin'],
                "dst_node": link['dst_id'], "dst_pin": link['dst_pin']
            })

        for stale_lid in stale_links:
            engine.remove_link(stale_lid)
        
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
        for link in list(engine.links):
            if dpg.does_item_exist(link['id']):
                dpg.delete_item(link['id'])
        for nid in list(engine.nodes.keys()):
            if dpg.does_item_exist(nid):
                dpg.delete_item(nid)
        engine.nodes.clear(); engine.links.clear()
        ui_manager.pin_label_map.clear()
        
        try:
            with open(filepath, 'r') as f: data = json.load(f)
            
            id_map = {}
            for n_data in data.get("nodes", []):
                node = NodeFactory.create_node(n_data["type"], n_data["id"])
                if node:
                    id_map[n_data["id"]] = node.node_id
                    if hasattr(node, 'settings'):
                        node.settings.update(n_data.get("settings", {}))
                    ui_manager.draw_node(node)
                    dpg.set_item_pos(node.node_id, n_data["pos"])
                    engine.add_node(node)
                    
            for l_data in data.get("links", []):
                if l_data["src_node"] in id_map and l_data["dst_node"] in id_map:
                    src_node_id = id_map[l_data['src_node']]
                    dst_node_id = id_map[l_data['dst_node']]
                    src_pin = l_data['src_pin']
                    dst_pin = l_data['dst_pin']
                    src_tag = f"{src_node_id}_{src_pin}"
                    dst_tag = f"{dst_node_id}_{dst_pin}"

                    if dpg.does_item_exist(src_tag) and dpg.does_item_exist(dst_tag):
                        lid = dpg.add_node_link(src_tag, dst_tag, parent=ui_manager.editor_tag)
                        engine.add_link(lid, src_node_id, src_pin, dst_node_id, dst_pin)
            print(f"[Serializer] Loaded: {filename}")
        except Exception as e: print(f"[Serializer] Load Error: {e}")

    @classmethod
    def get_save_files(cls):
        if not os.path.exists(cls.SAVE_DIR): return []
        return [f for f in os.listdir(cls.SAVE_DIR) if f.endswith(".json")]