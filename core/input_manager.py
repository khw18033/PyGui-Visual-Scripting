class InputManager:
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(InputManager, cls).__new__(cls)
            cls._instance.keys_down = {}
            cls._instance.is_focused = False
        return cls._instance
    
    def set_key_down(self, key_code, is_down):
        self.keys_down[key_code] = is_down
        
    def is_key_down(self, key_code):
        return self.keys_down.get(key_code, False)
        
    def set_focused(self, focused):
        self.is_focused = focused
        
    def get_focused(self):
        return self.is_focused

input_manager = InputManager()
