from nodes.base import BaseNode
from core.engine import generate_uuid, PortType, write_log
import time

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
        self.last_emitted = False
        self.next_emit_time = None
        # default interval (seconds) between iterations when tick-driven
        self.state.setdefault("interval", 0.1)
    def execute(self):
        now = time.monotonic()
        target = self.state.get("count", 3)
        interval = self.state.get("interval", 0.1)

        # If not active, this execute() call came from a flow activation
        if not self.is_active:
            self.is_active = True
            self.current_iter = 1
            self.last_emitted = True
            self.next_emit_time = None
            return self.out_loop

        # If active and we previously emitted an out_loop and now were called
        # again (this is the loop-back arrival), schedule the next emit
        if self.last_emitted:
            self.next_emit_time = now + interval
            self.last_emitted = False
            return None

        # If active and it's time to emit the next iteration (called from pre-exec)
        if self.next_emit_time is not None and now >= self.next_emit_time:
            if self.current_iter < target:
                self.current_iter += 1
                self.last_emitted = True
                self.next_emit_time = None
                return self.out_loop
            else:
                # finished all iterations
                self.is_active = False
                self.next_emit_time = None
                self.last_emitted = False
                return self.out_finish

        # Otherwise, do nothing this tick
        return None

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


class Go1StateChangeLoggerNode(BaseNode):
    def __init__(self, node_id):
        super().__init__(node_id, "State Change Log", "GO1_SC_LOGGER")
        self.llen = 0
    def execute(self):
        return None

