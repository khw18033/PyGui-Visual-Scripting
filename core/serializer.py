import json
import os
import dearpygui.dearpygui as dpg
from core.factory import NodeFactory

class GraphSerializer:
    SAVE_DIR = "Node_File_MT4"

    @staticmethod
    def _get_pin_labels(node, pin_types):
        labels = []
        for pin_type, label, _ in node.get_ui_schema():
            if pin_type in pin_types:
                labels.append(label)
        return labels

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
                "type": node.type_str, "id": str(nid), "pos": pos,
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
                "src_node": str(link['src_id']), "src_pin": link['src_pin'],
                "dst_node": str(link['dst_id']), "dst_pin": link['dst_pin']
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

        try:
            ui_manager.is_bulk_loading = True

            # 🚨 1. 안전한 삭제 (튕김 완벽 방지): 무조건 선(Link)을 먼저 모두 지웁니다.
            links_to_delete = list(engine.links)
            for link in links_to_delete:
                lid = link.get('id')
                if lid is not None and dpg.does_item_exist(lid):
                    dpg.delete_item(lid)
            engine.links.clear()

            # 🚨 2. 선이 모두 제거된 안전한 상태에서 노드(Node)를 지웁니다.
            nodes_to_delete = list(engine.nodes.keys())
            for nid in nodes_to_delete:
                if dpg.does_item_exist(nid):
                    dpg.delete_item(nid)
            engine.nodes.clear()
            
            ui_manager.pin_label_map.clear()

            with open(filepath, 'r') as f: data = json.load(f)
            
            id_map = {}
            for n_data in data.get("nodes", []):
                src_node_id = str(n_data["id"])
                node = NodeFactory.create_node(n_data["type"], src_node_id)
                if node:
                    id_map[src_node_id] = node.node_id
                    if hasattr(node, 'settings'):
                        node.settings.update(n_data.get("settings", {}))
                    ui_manager.draw_node(node)
                    if dpg.does_item_exist(node.node_id):
                        dpg.set_item_pos(node.node_id, n_data.get("pos", [0, 0]))
                    engine.add_node(node)
                    
            restored_links = 0
            skipped_links = 0

            for l_data in data.get("links", []):
                saved_src_node = str(l_data.get("src_node", ""))
                saved_dst_node = str(l_data.get("dst_node", ""))

                if saved_src_node in id_map and saved_dst_node in id_map:
                    src_node_id = id_map[saved_src_node]
                    dst_node_id = id_map[saved_dst_node]

                    src_pin = l_data.get('src_pin')
                    dst_pin = l_data.get('dst_pin')

                    if src_pin is None or dst_pin is None:
                        continue

                    src_tag = f"{src_node_id}_{src_pin}"
                    dst_tag = f"{dst_node_id}_{dst_pin}"

                    if dpg.does_item_exist(src_tag) and dpg.does_item_exist(dst_tag):
                        lid = dpg.add_node_link(src_tag, dst_tag, parent=ui_manager.editor_tag)
                        engine.add_link(lid, src_node_id, src_pin, dst_node_id, dst_pin)
                        restored_links += 1
                    else:
                        skipped_links += 1
                else:
                    skipped_links += 1
            print(f"[Serializer] Loaded: {filename} (links: restored={restored_links}, skipped={skipped_links})")
        except Exception as e: print(f"[Serializer] Load Error: {e}")
        finally:
            ui_manager.is_bulk_loading = False

    @classmethod
    def get_save_files(cls):
        if not os.path.exists(cls.SAVE_DIR): return []
        return [f for f in os.listdir(cls.SAVE_DIR) if f.endswith(".json")]