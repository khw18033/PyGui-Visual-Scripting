from core.engine import generate_uuid, node_registry
from nodes.common import (
    StartNode, ConditionKeyNode, LogicIfNode, LogicLoopNode, 
    ConstantNode, PrintNode, LoggerNode
)
from nodes.robots.mt4 import (
    MT4CommandActionNode, UniversalRobotNode, MT4RobotDriver, 
    MT4KeyboardNode, MT4UnityNode, UDPReceiverNode, 
    MT4GravitySagNode, MT4CalibrationNode, MT4TooltipNode, MT4BacklashNode
)

from nodes.robots.go1 import (Go1RobotDriver, Go1ActionNode, VideoSourceNode, Go1KeyboardNode, Go1UnityNode,
                              FisheyeUndistortNode, ArUcoDetectNode, FlaskStreamNode, VideoFrameSaveNode)

from nodes.robots.ep01 import EPRobotDriver, EPActionNode

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
        elif node_type == "GO1_KEYBOARD": node = Go1KeyboardNode(node_id)
        elif node_type == "GO1_UNITY": node = Go1UnityNode(node_id)
        elif node_type == "GO1_DRIVER": node = UniversalRobotNode(node_id, Go1RobotDriver(), "Go1 Driver", "GO1_DRIVER")
        elif node_type == "GO1_ACTION": node = Go1ActionNode(node_id)
        elif node_type == "VIDEO_SRC": node = VideoSourceNode(node_id)
        elif node_type == "VIS_FISHEYE": node = FisheyeUndistortNode(node_id)
        elif node_type == "VIS_ARUCO": node = ArUcoDetectNode(node_id)
        elif node_type == "VIS_FLASK": node = FlaskStreamNode(node_id)
        elif node_type == "VIS_SAVE": node = VideoFrameSaveNode(node_id)
        elif node_type == "EP_DRIVER": node = UniversalRobotNode(node_id, EPRobotDriver(), "EP Driver", "EP_DRIVER")
        elif node_type == "EP_ACTION": node = EPActionNode(node_id)
        
        if node: 
            node_registry[node_id] = node
        return node
