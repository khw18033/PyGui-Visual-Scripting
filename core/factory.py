import importlib
from core.engine import generate_uuid, node_registry
from core.config import GO1_MODULE_NAME
from nodes.common import (
    StartNode, ConditionKeyNode, LogicIfNode, LogicLoopNode, 
    ConstantNode, PrintNode, LoggerNode
)
from nodes.robots.mt4 import (
    MT4CommandActionNode, UniversalRobotNode, MT4RobotDriver, 
    MT4KeyboardNode, MT4UnityNode, UDPReceiverNode, 
    MT4GravitySagNode, MT4CalibrationNode, MT4TooltipNode, MT4BacklashNode
)

# Dynamic import for Go1 module
try:
    go1_module = importlib.import_module(f'nodes.robots.{GO1_MODULE_NAME}')
    Go1RobotDriver = getattr(go1_module, 'Go1RobotDriver')
    Go1ActionNode = getattr(go1_module, 'Go1ActionNode')
    VideoSourceNode = getattr(go1_module, 'VideoSourceNode')
    Go1KeyboardNode = getattr(go1_module, 'Go1KeyboardNode')
    Go1UnityNode = getattr(go1_module, 'Go1UnityNode')
    Go1UnityKeyboardNode = getattr(go1_module, 'Go1UnityKeyboardNode')
    Go1UnityAutonomyNode = getattr(go1_module, 'Go1UnityAutonomyNode')
    FisheyeUndistortNode = getattr(go1_module, 'FisheyeUndistortNode')
    DepthAnythingV2Node = getattr(go1_module, 'DepthAnythingV2Node')
    ArUcoDetectNode = getattr(go1_module, 'ArUcoDetectNode')
    FlaskStreamNode = getattr(go1_module, 'FlaskStreamNode')
    VideoFrameSaveNode = getattr(go1_module, 'VideoFrameSaveNode')
    ServerSenderNode = getattr(go1_module, 'ServerSenderNode')
    Go1ServerJsonRecvNode = getattr(go1_module, 'Go1ServerJsonRecvNode')
    Go1AutoAvoidanceNode = getattr(go1_module, 'Go1AutoAvoidanceNode')
    HAS_GO1 = True
except (ImportError, AttributeError) as e:
    print(f"⚠️  Failed to load Go1 nodes: {e}")
    HAS_GO1 = False
    Go1RobotDriver = None
    Go1ActionNode = None
    VideoSourceNode = None
    Go1KeyboardNode = None
    Go1UnityNode = None
    Go1UnityKeyboardNode = None
    Go1UnityAutonomyNode = None
    FisheyeUndistortNode = None
    DepthAnythingV2Node = None
    ArUcoDetectNode = None
    FlaskStreamNode = None
    VideoFrameSaveNode = None
    ServerSenderNode = None
    Go1ServerJsonRecvNode = None
    Go1AutoAvoidanceNode = None

from nodes.robots.ep01 import (
    EPRobotDriver,
    EPActionNode,
    EPKeyboardNode,
    EPCameraSourceNode,
    EPCameraStreamNode,
    EPVideoFrameSaveNode,
    EPServerSenderNode,
    EPServerJsonRecvNode,
)

class NodeFactory:
    @staticmethod
    def create_node(node_type, node_id=None):
        if node_id is None: 
            node_id = generate_uuid()
        else:
            node_id = str(node_id)
            if node_id.isdigit():
                node_id = f"uid_{node_id}"
        
        node = None
        if node_type == "START": node = StartNode(node_id)
        elif node_type == "COND_KEY": node = ConditionKeyNode(node_id)
        elif node_type == "LOGIC_IF": node = LogicIfNode(node_id)
        elif node_type == "LOGIC_LOOP": node = LogicLoopNode(node_id)
        elif node_type == "MT4_ACTION": node = MT4CommandActionNode(node_id)
        elif node_type == "CONSTANT": node = ConstantNode(node_id)
        elif node_type == "PRINT": node = PrintNode(node_id)
        elif node_type == "LOGGER": node = LoggerNode(node_id)
        elif node_type == "MT4_DRIVER": node = UniversalRobotNode(node_id, MT4RobotDriver(), "MT4 Driver", "MT4_DRIVER")
        elif node_type == "MT4_KEYBOARD": node = MT4KeyboardNode(node_id)
        elif node_type == "MT4_UNITY": node = MT4UnityNode(node_id)
        elif node_type == "UDP_RECV": node = UDPReceiverNode(node_id)
        elif node_type == "MT4_SAG": node = MT4GravitySagNode(node_id)
        elif node_type == "MT4_CALIB": node = MT4CalibrationNode(node_id)
        elif node_type == "MT4_TOOLTIP": node = MT4TooltipNode(node_id)
        elif node_type == "MT4_BACKLASH": node = MT4BacklashNode(node_id)
        elif node_type == "GO1_KEYBOARD" and HAS_GO1: node = Go1KeyboardNode(node_id)
        elif node_type == "GO1_UNITY_KEYBOARD" and HAS_GO1: node = Go1UnityKeyboardNode(node_id)
        elif node_type == "GO1_UNITY" and HAS_GO1: node = Go1UnityNode(node_id)
        elif node_type == "GO1_UNITY_AUTO" and HAS_GO1: node = Go1UnityAutonomyNode(node_id)
        elif node_type == "GO1_DRIVER" and HAS_GO1: node = UniversalRobotNode(node_id, Go1RobotDriver(), "Go1 Driver", "GO1_DRIVER")
        elif node_type == "GO1_ACTION" and HAS_GO1: node = Go1ActionNode(node_id)
        elif node_type == "VIDEO_SRC" and HAS_GO1: node = VideoSourceNode(node_id)
        elif node_type == "VIS_FISHEYE" and HAS_GO1: node = FisheyeUndistortNode(node_id)
        elif node_type == "VIS_DEPTH_DA2" and HAS_GO1: node = DepthAnythingV2Node(node_id)
        elif node_type == "VIS_ARUCO" and HAS_GO1: node = ArUcoDetectNode(node_id)
        elif node_type == "VIS_FLASK" and HAS_GO1: node = FlaskStreamNode(node_id)
        elif node_type == "VIS_SAVE" and HAS_GO1: node = VideoFrameSaveNode(node_id)
        elif node_type == "GO1_SERVER_SENDER" and HAS_GO1: node = ServerSenderNode(node_id)
        elif node_type == "GO1_SERVER_JSON_RECV" and HAS_GO1: node = Go1ServerJsonRecvNode(node_id)
        elif node_type == "GO1_AUTO_AVOIDANCE" and HAS_GO1: node = Go1AutoAvoidanceNode(node_id)
        elif node_type == "EP_DRIVER": node = UniversalRobotNode(node_id, EPRobotDriver(), "EP Driver", "EP_DRIVER")
        elif node_type == "EP_KEYBOARD": node = EPKeyboardNode(node_id)
        elif node_type == "EP_ACTION": node = EPActionNode(node_id)
        elif node_type == "EP_CAM_SRC": node = EPCameraSourceNode(node_id)
        elif node_type == "EP_CAM_STREAM": node = EPCameraStreamNode(node_id)
        elif node_type == "EP_VIS_SAVE": node = EPVideoFrameSaveNode(node_id)
        elif node_type == "EP_SERVER_SENDER": node = EPServerSenderNode(node_id)
        elif node_type == "EP_SERVER_JSON_RECV": node = EPServerJsonRecvNode(node_id)
        
        if node: 
            node_registry[node_id] = node
        return node
