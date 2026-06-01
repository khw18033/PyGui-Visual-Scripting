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
    Go1MissionReceiverNode = getattr(go1_module, 'Go1MissionReceiverNode')
    Go1MissionDecisionNode = getattr(go1_module, 'Go1MissionDecisionNode')
    Go1MissionDispatchNode = getattr(go1_module, 'Go1MissionDispatchNode')
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
    Go1MissionReceiverNode = None
    Go1MissionDecisionNode = None
    Go1MissionDispatchNode = None
    FisheyeUndistortNode = None
    DepthAnythingV2Node = None
    ArUcoDetectNode = None
    FlaskStreamNode = None
    VideoFrameSaveNode = None
    ServerSenderNode = None
    Go1ServerJsonRecvNode = None
    Go1AutoAvoidanceNode = None

try:
    tello_module = importlib.import_module('nodes.robots.tello')
    TelloRobotDriver = getattr(tello_module, 'TelloRobotDriver')
    TelloKeyboardNode = getattr(tello_module, 'TelloKeyboardNode')
    TelloActionNode = getattr(tello_module, 'TelloActionNode')
    HAS_TELLO = True
except (ImportError, AttributeError) as e:
    print(f"⚠️  Failed to load Tello nodes: {e}")
    tello_module = None
    TelloRobotDriver = None
    TelloKeyboardNode = None
    TelloActionNode = None
    HAS_TELLO = False

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
        elif node_type == "GO1_MISSION_RECV" and HAS_GO1: node = Go1MissionReceiverNode(node_id)
        elif node_type == "GO1_MISSION_DECIDE" and HAS_GO1: node = Go1MissionDecisionNode(node_id)
        elif node_type == "GO1_MISSION_DISPATCH" and HAS_GO1: node = Go1MissionDispatchNode(node_id)
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
        elif node_type == "TELLO_DRIVER" and HAS_TELLO: node = UniversalRobotNode(node_id, TelloRobotDriver(), "Tello Driver", "TELLO_DRIVER")
        elif node_type == "TELLO_KEYBOARD" and HAS_TELLO: node = TelloKeyboardNode(node_id)
        elif node_type == "TELLO_ACTION" and HAS_TELLO: node = TelloActionNode(node_id)
        elif node_type.startswith("EP_") or node_type.startswith("EP01_"):
            try:
                ep_module = importlib.import_module('nodes.robots.ep01')
                EPRobotDriver = getattr(ep_module, 'EPRobotDriver')
                EPActionNode = getattr(ep_module, 'EPActionNode')
                EPKeyboardNode = getattr(ep_module, 'EPKeyboardNode')
                EPCameraSourceNode = getattr(ep_module, 'EPCameraSourceNode')
                EPCameraStreamNode = getattr(ep_module, 'EPCameraStreamNode')
                EPVideoFrameSaveNode = getattr(ep_module, 'EPVideoFrameSaveNode')
                EPServerSenderNode = getattr(ep_module, 'EPServerSenderNode')
                EPServerJsonRecvNode = getattr(ep_module, 'EPServerJsonRecvNode')
                EP01MissionReceiverNode  = getattr(ep_module, 'EP01MissionReceiverNode')
                EP01MissionDecisionNode  = getattr(ep_module, 'EP01MissionDecisionNode')
                EP01MissionDispatchNode  = getattr(ep_module, 'EP01MissionDispatchNode')
                EP01MissionActionNode    = getattr(ep_module, 'EP01MissionActionNode')
            except Exception as e:
                print(f"⚠️  Failed to load EP nodes: {e}")
                EPRobotDriver = None
                EPActionNode = None
                EPKeyboardNode = None
                EPCameraSourceNode = None
                EPCameraStreamNode = None
                EPVideoFrameSaveNode = None
                EPServerSenderNode = None
                EPServerJsonRecvNode = None
                EP01MissionReceiverNode = None
                EP01MissionDecisionNode = None
                EP01MissionDispatchNode = None
                EP01MissionActionNode   = None

            if node_type == "EP_DRIVER" and EPRobotDriver is not None:
                node = UniversalRobotNode(node_id, EPRobotDriver(), "EP Driver", "EP_DRIVER")
            elif node_type == "EP_KEYBOARD" and EPKeyboardNode is not None:
                node = EPKeyboardNode(node_id)
            elif node_type == "EP_ACTION" and EPActionNode is not None:
                node = EPActionNode(node_id)
            elif node_type == "EP_CAM_SRC" and EPCameraSourceNode is not None:
                node = EPCameraSourceNode(node_id)
            elif node_type == "EP_CAM_STREAM" and EPCameraStreamNode is not None:
                node = EPCameraStreamNode(node_id)
            elif node_type == "EP_VIS_SAVE" and EPVideoFrameSaveNode is not None:
                node = EPVideoFrameSaveNode(node_id)
            elif node_type == "EP_SERVER_SENDER" and EPServerSenderNode is not None:
                node = EPServerSenderNode(node_id)
            elif node_type == "EP_SERVER_JSON_RECV" and EPServerJsonRecvNode is not None:
                node = EPServerJsonRecvNode(node_id)
            elif node_type == "EP01_MISSION_RECV" and EP01MissionReceiverNode is not None:
                node = EP01MissionReceiverNode(node_id)
            elif node_type == "EP01_MISSION_DECIDE" and EP01MissionDecisionNode is not None:
                node = EP01MissionDecisionNode(node_id)
            elif node_type == "EP01_MISSION_DISPATCH" and EP01MissionDispatchNode is not None:
                node = EP01MissionDispatchNode(node_id)
            elif node_type == "EP01_MISSION_ACTION" and EP01MissionActionNode is not None:
                node = EP01MissionActionNode(node_id)
        
        if node: 
            node_registry[node_id] = node
        return node
