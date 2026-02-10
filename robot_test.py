import serial
import time
import sys
import tty
import termios

# ================= [설정 구간] =================
SERIAL_PORT = '/dev/ttyUSB0'  # 본인 포트 이름으로 변경 (/dev/ttyACM0 일 수도 있음)
BAUD_RATE = 115200

# 이동 단위 (mm) - 한 번 누를 때마다 이동할 거리
STEP_SIZE = 10  
# Z축 이동 단위 (mm)
Z_STEP_SIZE = 10 

# [안전 장치] 소프트웨어 좌표 제한 (기구적 한계 보호)
# 이 범위를 넘어가려 하면 코드가 명령을 보내지 않습니다.
LIMITS = {
    'min_x': 100, 'max_x': 280,  # 앞뒤 (너무 가까우면 몸통 충돌)
    'min_y': -150, 'max_y': 150, # 좌우
    'min_z': 0,   'max_z': 180   # 높이
}

# 그리퍼 설정 (Servo Gripper)
GRIPPER_OPEN = 40
GRIPPER_CLOSE = 60
# ==============================================

# 리눅스 터미널에서 키 입력 한 글자씩 받아오는 함수 (Enter 없이 즉시 반응)
def getch():
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(sys.stdin.fileno())
        ch = sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
    return ch

def main():
    try:
        # 1. 시리얼 연결
        ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=0.1)
        print(f"[{SERIAL_PORT}] 연결 성공! 초기화 중...")
        time.sleep(3)

        def send_gcode(cmd):
            ser.write((cmd + '\r\n').encode())
            # 빠르게 연속 입력 받기 위해 응답 대기는 최소화하거나 생략
            time.sleep(0.05) 

        # 2. 초기화 (호밍 & 모드 설정)
        print(">>> 호밍(Homing) 시작... (잠시 대기)")
        send_gcode("$H")
        time.sleep(20) # 호밍 대기

        print(">>> 좌표 모드 설정")
        send_gcode("M20")
        time.sleep(0.5)
        send_gcode("G90") # 절대 좌표 모드
        time.sleep(0.5)

        # 3. 시작 위치로 이동
        # 현재 로봇의 위치를 변수에 저장해두고 관리합니다.
        curr_x, curr_y, curr_z = 200, 0, 100
        curr_gripper = GRIPPER_OPEN
        
        print(f">>> 시작 위치로 이동: X{curr_x} Y{curr_y} Z{curr_z}")
        send_gcode(f"G0 X{curr_x} Y{curr_y} Z{curr_z}")
        send_gcode(f"M3 S{curr_gripper}") # 그리퍼 열기
        time.sleep(2)

        # 4. 키보드 제어 루프
        print("\n" + "="*40)
        print("   [MT4 키보드 제어 모드]")
        print("   W / S : 앞 / 뒤 (X축)")
        print("   A / D : 좌 / 우 (Y축)")
        print("   Q / E : 위 / 아래 (Z축)")
        print("   U / J : 그리퍼 열기 / 닫기")
        print("   ESC   : 종료")
        print("="*40 + "\n")

        while True:
            key = getch() # 키 입력 대기 (블로킹)

            # 변경 전 좌표 기억
            next_x, next_y, next_z = curr_x, curr_y, curr_z
            moved = False
            gripper_action = False

            # 키 매핑 확인
            if key == 'w': # 앞
                next_x += STEP_SIZE
                moved = True
            elif key == 's': # 뒤
                next_x -= STEP_SIZE
                moved = True
            elif key == 'a': # 좌 (Y+)
                next_y += STEP_SIZE
                moved = True
            elif key == 'd': # 우 (Y-)
                next_y -= STEP_SIZE
                moved = True
            elif key == 'q': # 위
                next_z += Z_STEP_SIZE
                moved = True
            elif key == 'e': # 아래
                next_z -= Z_STEP_SIZE
                moved = True
            
            # 그리퍼 제어
            elif key == 'u': # 열기
                curr_gripper = GRIPPER_OPEN
                gripper_action = True
                print("   [Gripper] OPEN")
            elif key == 'j': # 닫기
                curr_gripper = GRIPPER_CLOSE
                gripper_action = True
                print("   [Gripper] CLOSE")

            # 종료
            elif ord(key) == 27: # ESC 키
                print("\n>>> 종료합니다.")
                break

            # 5. 유효성 검사 및 명령 전송
            if moved:
                # 좌표 제한 확인 (Safety Check)
                if (LIMITS['min_x'] <= next_x <= LIMITS['max_x'] and
                    LIMITS['min_y'] <= next_y <= LIMITS['max_y'] and
                    LIMITS['min_z'] <= next_z <= LIMITS['max_z']):
                    
                    # 유효하면 좌표 업데이트 및 전송
                    curr_x, curr_y, curr_z = next_x, next_y, next_z
                    cmd = f"G0 X{curr_x} Y{curr_y} Z{curr_z}"
                    send_gcode(cmd)
                    print(f"MOVED -> X:{curr_x} Y:{curr_y} Z:{curr_z}\r", end='') # \r로 줄바꿈 없이 갱신
                else:
                    print(f"\n[WARNING] 제한 범위 도달! ({next_x}, {next_y}, {next_z})")
            
            if gripper_action:
                send_gcode(f"M3 S{curr_gripper}")

    except Exception as e:
        print(f"\n에러 발생: {e}")
    finally:
        if 'ser' in locals() and ser.is_open:
            ser.close()

if __name__ == "__main__":
    main()
