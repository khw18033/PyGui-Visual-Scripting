from abc import ABC, abstractmethod
from core.engine import link_registry, node_registry

class BaseRobotDriver(ABC):
    @abstractmethod
    def get_ui_schema(self): pass
    @abstractmethod
    def get_settings_schema(self): pass
    @abstractmethod
    def execute_command(self, inputs, settings): pass

class BaseNode(ABC):
    def __init__(self, node_id, label, type_str):
        self.node_id = node_id
        self.label = label
        self.type_str = type_str
        self.inputs = {}
        self.outputs = {}
        self.output_data = {} 
        self.state = {} 
    
    @abstractmethod
    def execute(self): 
        return None 
    
    def fetch_input_data(self, input_attr_id):
        target_link = next((l for l in link_registry.values() if l['target'] == input_attr_id), None)
        if not target_link: return None 
        source_node = node_registry.get(target_link['src_node_id'])
        return source_node.output_data.get(target_link['source']) if source_node else None
        
    def get_settings(self): 
        return self.state
        
    def load_settings(self, data): 
        self.state.update(data)
