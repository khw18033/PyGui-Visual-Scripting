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
SAVE_DIR = "Node_File_MT4"
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
    
    # Allow certain nodes to run every tick and optionally return a flow start
    start_flow_out = None
    for node in node_registry.values():
        if node.type_str in ["COND_KEY", "MT4_DRIVER", "GO1_DRIVER", "EP_DRIVER", "VIDEO_SRC", "VIS_FISHEYE", "VIS_DEPTH_DA2", "VIS_ARUCO", "VIS_FLASK", "MT4_UNITY", "GO1_UNITY", "GO1_UNITY_KEYBOARD", "GO1_UNITY_AUTO", "GO1_SERVER_JSON_RECV", "EP_SERVER_JSON_RECV", "UDP_RECV", "LOGGER", "CONSTANT", "MT4_SAG", "MT4_CALIB", "MT4_TOOLTIP", "MT4_BACKLASH", "MT4_KEYBOARD", "GO1_KEYBOARD", "EP_KEYBOARD", "EP_CAM_SRC", "EP_CAM_STREAM", "LOGIC_LOOP"]:
            try:
                res = node.execute()
                # capture the first returned flow port from pre-exec nodes
                if res is not None and start_flow_out is None:
                    if isinstance(res, (int, str)):
                        start_flow_out = res
                    elif isinstance(res, dict):
                        start_flow_out = next((k for k, v in res.items() if v == PortType.FLOW), None)
            except Exception as e:
                print(f"[{node.label}] Error: {e}")

    # If a pre-exec node returned a flow, use that as the starting point for flow traversal
    if not start_node and not start_flow_out:
        return

    current_node = None
    if start_flow_out:
        target_link = next((l for l in link_registry.values() if l['source'] == start_flow_out), None)
        if target_link:
            current_node = node_registry.get(target_link['dst_node_id'])
    if current_node is None:
        current_node = start_node
    steps = 0; MAX_STEPS = 100 
    while current_node and steps < MAX_STEPS:
        result = current_node.execute()
        next_out_id = None
        if result is not None:
            if isinstance(result, (int, str)): next_out_id = result
            elif isinstance(result, dict):
                next_out_id = next((k for k, v in result.items() if v == PortType.FLOW), None)
        next_node = None
        if next_out_id:
            target_link = next((l for l in link_registry.values() if l['source'] == next_out_id), None)
            if target_link: next_node = node_registry.get(target_link['dst_node_id'])
        current_node = next_node; steps += 1
