import time
from nodes.base import BaseNode
from core.engine import generate_uuid, PortType, write_log

class StartNode(BaseNode):
    def __init__(self, node_id): 
        super().__init__(node_id, "START", "START")
        self.out = generate_uuid()
        self.outputs[self.out] = PortType.FLOW
    def execute(self): 
        return self.out 

class ConditionKeyNode(BaseNode):
    def __init__(self, node_id): 
        super().__init__(node_id, "Check: Key", "COND_KEY")
        self.out_res = generate_uuid()
        self.outputs[self.out_res] = PortType.DATA
        self.prev_state = False 
    def execute(self):
        current = self.state.get("is_down", False)
        if current and not self.prev_state: 
            self.output_data[self.out_res] = True
        else: 
            self.output_data[self.out_res] = False
        self.prev_state = current
        return None

class LogicIfNode(BaseNode):
    def __init__(self, node_id): 
        super().__init__(node_id, "Logic: IF", "LOGIC_IF")
        self.in_cond = generate_uuid()
        self.inputs[self.in_cond] = PortType.DATA
        self.out_true = generate_uuid()
        self.outputs[self.out_true] = PortType.FLOW
        self.out_false = generate_uuid()
        self.outputs[self.out_false] = PortType.FLOW
    def execute(self):
        cond_val = self.fetch_input_data(self.in_cond)
        return self.out_true if cond_val else self.out_false

class LogicLoopNode(BaseNode):
    def __init__(self, node_id): 
        super().__init__(node_id, "Logic: LOOP", "LOGIC_LOOP")
        self.out_loop = generate_uuid()
        self.outputs[self.out_loop] = PortType.FLOW
        self.out_finish = generate_uuid()
        self.outputs[self.out_finish] = PortType.FLOW
        self.current_iter = 0
        self.is_active = False
    def execute(self):
        if not self.is_active: 
            self.current_iter = 0
            self.is_active = True
        target = self.state.get("count", 3)
        if self.current_iter < target: 
            self.current_iter += 1
            return self.out_loop 
        else: 
            self.is_active = False
            return self.out_finish

class ConstantNode(BaseNode):
    def __init__(self, node_id): 
        super().__init__(node_id, "Constant", "CONSTANT")
        self.out_val = generate_uuid()
        self.outputs[self.out_val] = PortType.DATA
    def execute(self): 
        self.output_data[self.out_val] = self.state.get("val", 1.0)
        return None

class PrintNode(BaseNode):
    def __init__(self, node_id): 
        super().__init__(node_id, "Print Log", "PRINT")
        self.out_flow = generate_uuid()
        self.outputs[self.out_flow] = PortType.FLOW
        self.inp_data = generate_uuid()
        self.inputs[self.inp_data] = PortType.DATA
    def execute(self):
        val = self.fetch_input_data(self.inp_data)
        if val is not None: 
            write_log(f"PRINT: {val}")
        return self.out_flow

class LoggerNode(BaseNode):
    def __init__(self, node_id): 
        super().__init__(node_id, "System Log", "LOGGER")
        self.llen = 0
    def execute(self): 
        return None 


class ServerSenderNode(BaseNode):
    """원격 서버로 이미지 업로드하는 통합 노드 (Core Nodes)
    - Go1과 EP01 로봇 모두 지원
    - HTTP multipart/form-data로 비동기 업로드
    - 시작/중지 제어
    - 로봇별 글로벌 상태를 동적으로 참조
    """
    def __init__(self, node_id, robot_type='GO1', node_name='Server Sender', node_type='SERVER_SENDER'):
        """
        Args:
            node_id: 노드 ID
            robot_type: 'GO1' 또는 'EP01'
            node_name: 표시될 노드 이름
            node_type: 노드 타입 (TYPE_STR)
        """
        super().__init__(node_id, node_name, node_type)
        self.robot_type = robot_type
        self.in_flow = generate_uuid()
        self.inputs[self.in_flow] = PortType.FLOW
        self.out_flow = generate_uuid()
        self.outputs[self.out_flow] = PortType.FLOW
        
        self.state['action'] = 'Start Sender'
        if robot_type == 'GO1':
            self.state['server_url'] = "http://192.168.1.100:5001/upload"
        elif robot_type == 'EP01':
            self.state['server_url'] = "http://210.110.250.33:5002/upload"
        else:
            self.state['server_url'] = "http://127.0.0.1:5001/upload"
        
        self._last_action = None
        self._last_request_ts = 0.0

    def _get_globals(self):
        """로봇별 글로벌 변수 참조 (동적 로딩)"""
        if self.robot_type == 'GO1':
            import nodes.robots.go1 as robot_module
            return {
                'state': robot_module.sender_state,
                'active': robot_module.multi_sender_active,
                'queue': robot_module.sender_command_queue,
            }
        elif self.robot_type == 'EP01':
            import nodes.robots.ep01 as robot_module
            return {
                'state': robot_module.ep_sender_state,
                'active': robot_module.ep_sender_active,
                'queue': robot_module.ep_sender_command_queue,
            }
        else:
            return None

    def execute(self):
        globals_dict = self._get_globals()
        if not globals_dict:
            return self.out_flow
        
        state = globals_dict['state']
        queue = globals_dict['queue']
        active = globals_dict['active']
        
        action = self.state.get('action', 'Start Sender')
        url = self.state.get('server_url', "http://192.168.1.100:5001/upload")
        now = time.monotonic()
        cooldown_ok = (now - self._last_request_ts) > 0.5
        
        if action != self._last_action:
            self._last_action = action

        if action == "Start Sender":
            if (not active) and state['status'] in ['Stopped', 'Stopping...'] and cooldown_ok:
                state['status'] = 'Starting...'
                queue.append(('START', url))
                self._last_request_ts = now

        elif action == "Stop Sender":
            if active and state['status'] in ['Running', 'Starting...'] and cooldown_ok:
                state['status'] = 'Stopping...'
                queue.append(('STOP', url))
                self._last_request_ts = now
        
        return self.out_flow

