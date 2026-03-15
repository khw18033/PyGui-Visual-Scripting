import dearpygui.dearpygui as dpg

class InputManager:
    def __init__(self):
        self.key_state = {
            'W': False, 'A': False, 'S': False, 'D': False,
            'UP': False, 'DOWN': False, 'LEFT': False, 'RIGHT': False,
            'Q': False, 'E': False, 
            'U': False, 'J': False,  
            'Z': False, 'X': False,  
            'SPACE': False, 'R': False
        }

    def poll_inputs(self):
        if not dpg.is_dearpygui_running():
            return

        # ★ 타이핑 방지 로직: 텍스트나 숫자 입력창에 커서가 있으면 모든 키보드 입력을 무시합니다.
        focused_item = dpg.get_focused_item()
        if focused_item:
            item_type = dpg.get_item_info(focused_item).get('type', '')
            if "InputText" in item_type or "InputInt" in item_type or "InputFloat" in item_type:
                for k in self.key_state.keys():
                    self.key_state[k] = False
                return

        self.key_state['W'] = dpg.is_key_down(dpg.mvKey_W)
        self.key_state['A'] = dpg.is_key_down(dpg.mvKey_A)
        self.key_state['S'] = dpg.is_key_down(dpg.mvKey_S)
        self.key_state['D'] = dpg.is_key_down(dpg.mvKey_D)
        
        self.key_state['UP'] = dpg.is_key_down(dpg.mvKey_Up)
        self.key_state['DOWN'] = dpg.is_key_down(dpg.mvKey_Down)
        self.key_state['LEFT'] = dpg.is_key_down(dpg.mvKey_Left)
        self.key_state['RIGHT'] = dpg.is_key_down(dpg.mvKey_Right)
        
        self.key_state['Q'] = dpg.is_key_down(dpg.mvKey_Q)
        self.key_state['E'] = dpg.is_key_down(dpg.mvKey_E)
        
        self.key_state['U'] = dpg.is_key_down(dpg.mvKey_U)
        self.key_state['J'] = dpg.is_key_down(dpg.mvKey_J)
        
        self.key_state['Z'] = dpg.is_key_down(dpg.mvKey_Z)
        self.key_state['X'] = dpg.is_key_down(dpg.mvKey_X)
        
        self.key_state['SPACE'] = dpg.is_key_down(dpg.mvKey_Spacebar)
        self.key_state['R'] = dpg.is_key_down(dpg.mvKey_R)

    def get_key(self, key_name: str) -> bool:
        return self.key_state.get(key_name.upper(), False)

# 전역 인스턴스
global_input_manager = InputManager()