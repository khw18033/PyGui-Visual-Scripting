import time
from nodes.base import BaseNode

class StartNode(BaseNode):
    def __init__(self, node_id: str): super().__init__(node_id, "START", "START")
    def get_ui_schema(self): return [("OUT_FLOW", "Flow Out", None)]
    def get_settings_schema(self): return []
    def execute(self): return "Flow Out"

class ConstantNode(BaseNode):
    def __init__(self, node_id: str): super().__init__(node_id, "Constant", "CONSTANT")
    def get_ui_schema(self): return [("OUT_DATA", "Data", None)]
    def get_settings_schema(self): return [("val", 1.0)]
    def execute(self):
        self.outputs["Data"] = float(self.settings.get("val", 1.0))
        return None

class ConditionKeyNode(BaseNode):
    def __init__(self, node_id: str): super().__init__(node_id, "Check: Key", "COND_KEY")
    def get_ui_schema(self): return [("OUT_DATA", "Is Pressed?", None)]
    def get_settings_schema(self): return [("key", "SPACE")]
    def execute(self):
        from core.input_manager import global_input_manager
        target_key = str(self.settings.get("key", "SPACE")).upper()
        self.outputs["Is Pressed?"] = global_input_manager.get_key(target_key)
        return None

class LogicIfNode(BaseNode):
    def __init__(self, node_id: str): super().__init__(node_id, "Logic: IF", "LOGIC_IF")
    def get_ui_schema(self): return [
        ("IN_FLOW", "Flow In", None), 
        ("IN_DATA", "Condition", False), 
        ("OUT_FLOW", "True", None), ("OUT_FLOW", "False", None)
    ]
    def get_settings_schema(self): return []
    def execute(self):
        cond = self.inputs.get("Condition", False)
        return "True" if cond else "False"

class LogicLoopNode(BaseNode):
    def __init__(self, node_id: str): 
        super().__init__(node_id, "Logic: LOOP", "LOGIC_LOOP")
        self.current_iter = 0; self.is_active = False
    def get_ui_schema(self): return [
        ("IN_FLOW", "Flow In", None), 
        ("OUT_FLOW", "Loop Body", None), ("OUT_FLOW", "Finished", None)
    ]
    def get_settings_schema(self): return [("count", 3.0)]
    def execute(self):
        if not self.is_active: 
            self.current_iter = 0; self.is_active = True
        
        target = int(self.settings.get("count", 3.0))
        if self.current_iter < target: 
            self.current_iter += 1
            return "Loop Body"
        else: 
            self.is_active = False
            return "Finished"

class PrintNode(BaseNode):
    def __init__(self, node_id: str): super().__init__(node_id, "Print Log", "PRINT")
    def get_ui_schema(self): return [
        ("IN_FLOW", "Flow In", None), ("IN_DATA", "Data", None), ("OUT_FLOW", "Flow Out", None)
    ]
    def get_settings_schema(self): return []
    def execute(self):
        val = self.inputs.get("Data")
        if val is not None: print(f"[PRINT NODE] {val}")
        return "Flow Out"

class LoggerNode(BaseNode):
    def __init__(self, node_id: str): super().__init__(node_id, "System Log", "LOGGER")
    def get_ui_schema(self): return []
    def get_settings_schema(self): return [("Notice", "Flowless UI Node")]
    def execute(self): return None