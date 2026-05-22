import os
from collections import deque
from datetime import datetime
from enum import Enum, auto

class HwStatus(Enum):
    OFFLINE = auto()
    ONLINE = auto()
    SIMULATION = auto()

class PortType(Enum):
    FLOW = auto()
    DATA = auto()

node_registry = {}
link_registry = {}
is_running = False
SAVE_DIR = "Node_Files"
if not os.path.exists(SAVE_DIR): 
    os.makedirs(SAVE_DIR)
system_log_buffer = deque(maxlen=50)

_id_counter = 1000000

def generate_uuid():
    global _id_counter
    while True:
        _id_counter += 1
        uid = f"uid_{_id_counter}"
        if uid not in node_registry and uid not in link_registry:
            return uid

def write_log(msg):
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {msg}")
    system_log_buffer.append(f"[{timestamp}] {msg}")

def execute_graph_once():
    start_node = next((n for n in node_registry.values() if n.type_str == "START"), None)

    def _parse_flow_out(result):
        if result is None:
            return None
        if isinstance(result, (int, str)):
            return result
        if isinstance(result, dict):
            return next((k for k, v in result.items() if v == PortType.FLOW), None)
        return None

    def _enqueue_targets_by_out(out_id, queue):
        if not out_id:
            return
        for link in link_registry.values():
            if link.get('source') == out_id:
                dst_node_id = link.get('dst_node_id')
                if dst_node_id in node_registry:
                    queue.append(node_registry[dst_node_id])

    preexec_flow_outs = []
    for node in node_registry.values():
        # LOGIC_LOOP must not auto-start. Only poll active loops in pre-exec.
        if node.type_str == "LOGIC_LOOP":
            if not getattr(node, 'is_active', False):
                continue
            try:
                res = node.execute()
                out_id = _parse_flow_out(res)
                if out_id:
                    preexec_flow_outs.append(out_id)
            except Exception as e:
                print(f"[{node.label}] Error: {e}")
            continue

        if node.type_str in ["COND_KEY", "MT4_DRIVER", "GO1_DRIVER", "EP_DRIVER", "VIDEO_SRC", "VIS_FISHEYE", "VIS_DEPTH_DA2", "VIS_ARUCO", "VIS_FLASK", "MT4_UNITY", "GO1_UNITY", "GO1_UNITY_KEYBOARD", "GO1_UNITY_AUTO", "GO1_SERVER_JSON_RECV", "GO1_MISSION_RECV", "EP_SERVER_JSON_RECV", "UDP_RECV", "LOGGER", "CONSTANT", "MT4_SAG", "MT4_CALIB", "MT4_TOOLTIP", "MT4_BACKLASH", "MT4_KEYBOARD", "GO1_KEYBOARD", "EP_KEYBOARD", "EP_CAM_SRC", "EP_CAM_STREAM"]:
            try:
                node.execute()
            except Exception as e:
                print(f"[{node.label}] Error: {e}")

    if not start_node and not preexec_flow_outs:
        return

    queue = []
    if start_node:
        _enqueue_targets_by_out(start_node.execute(), queue)
    for out_id in preexec_flow_outs:
        _enqueue_targets_by_out(out_id, queue)

    steps = 0
    MAX_STEPS = 300
    while queue and steps < MAX_STEPS:
        current_node = queue.pop(0)
        try:
            result = current_node.execute()
        except Exception as e:
            print(f"[{current_node.label}] Error: {e}")
            steps += 1
            continue

        next_out_id = _parse_flow_out(result)
        _enqueue_targets_by_out(next_out_id, queue)
        steps += 1
