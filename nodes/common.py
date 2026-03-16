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
