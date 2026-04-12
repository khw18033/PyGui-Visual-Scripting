// go1_all_mode_tester_full.cpp
// Go1 High-level mode tester — mode 0~13 전체 실행 버전
//
// 입력 방식
//   모드 번호 0~13  : 해당 번호 그대로 입력 → 즉시 실행
//   보행 방향       : wf / wb / wl / wr / yl / yr
//   gaitType 시험   : g0 ~ g4
//   시퀀스          : s1 ~ s4
//   상태 출력       : st
//   종료            : q
//
// 빌드 예시
//   mkdir build && cd build
//   cmake ..
//   make
//
// 실행 예시
//   sudo ./go1_all_mode_tester_full

#include <unistd.h>
#include <cmath>
#include <cstring>
#include <iostream>
#include <thread>
#include <chrono>
#include <string>

#include "unitree_legged_sdk/unitree_legged_sdk.h"

using namespace UNITREE_LEGGED_SDK;
using namespace std;

// =========================================================
// Go1AllModeTester 클래스
// =========================================================
class Go1AllModeTester {
public:
    Go1AllModeTester(uint8_t /*level*/, int localPort, const char* robotIp, int robotPort)
        : udp(localPort, robotIp, robotPort, sizeof(HighCmd), sizeof(HighState)) {
        udp.InitCmdData(cmd);
        memset(&state, 0, sizeof(state));
        resetCmd();
    }

    void recvOnce() {
        udp.Recv();
        udp.GetRecv(state);
    }

    // ms 동안 100Hz 로 cmd 송신
    void sendLoopMs(int ms) {
        const int dt = 10;
        int n = ms / dt;
        for (int i = 0; i < n; ++i) {
            udp.SetSend(cmd);
            udp.Send();
            recvOnce();
            this_thread::sleep_for(chrono::milliseconds(dt));
        }
    }

    void resetCmd() {
        memset(&cmd, 0, sizeof(cmd));
        udp.InitCmdData(cmd);
        cmd.mode            = 0;
        cmd.gaitType        = 0;
        cmd.speedLevel      = 0;
        cmd.footRaiseHeight = 0.0f;
        cmd.bodyHeight      = 0.0f;
        cmd.euler[0]        = 0.0f;
        cmd.euler[1]        = 0.0f;
        cmd.euler[2]        = 0.0f;
        cmd.velocity[0]     = 0.0f;
        cmd.velocity[1]     = 0.0f;
        cmd.yawSpeed        = 0.0f;
        cmd.reserve         = 0;
    }

    void printState() {
        recvOnce();
        cout << "\n[STATE]\n";
        cout << " mode        : " << (int)state.mode        << "\n";
        cout << " gaitType    : " << (int)state.gaitType    << "\n";
        cout << " bodyHeight  : " << state.bodyHeight       << "\n";
        cout << " footRaise   : " << state.footRaiseHeight  << "\n";
        cout << " position    : "
             << state.position[0] << ", "
             << state.position[1] << ", "
             << state.position[2] << "\n";
        cout << " velocity    : "
             << state.velocity[0] << ", "
             << state.velocity[1] << ", "
             << state.velocity[2] << "\n";
        cout << " yawSpeed    : " << state.yawSpeed << "\n\n";
    }

    // =========================================================
    // mode 0~8 : 일반 모드
    // =========================================================

    // mode 0 : Idle — 유휴/정지
    void mode0_idle(int ms = 1000) {
        resetCmd();
        cmd.mode = 0;
        cout << "[RUN] mode 0 : Idle (" << ms << " ms)\n";
        sendLoopMs(ms);
    }

    // mode 1 : Stand — 서기/자세 유지
    // euler[roll, pitch, yaw], bodyHeight 로 자세 지정
    void mode1_stand(float roll = 0.0f, float pitch = 0.0f, float yaw = 0.0f,
                     float bodyHeight = 0.0f, int ms = 1500) {
        resetCmd();
        cmd.mode       = 1;
        cmd.euler[0]   = roll;
        cmd.euler[1]   = pitch;
        cmd.euler[2]   = yaw;
        cmd.bodyHeight = bodyHeight;
        cout << "[RUN] mode 1 : Stand (" << ms << " ms)\n";
        sendLoopMs(ms);
    }

    // mode 2 : Walk — 속도 기반 보행
    // gaitType : 0=Idle 1=Trot walking 2=Trot running 3=Stairs 4=Trot obstacle
    void mode2_walk(float vx, float vy, float wz,
                    uint8_t gaitType = 1,
                    float bodyHeight = 0.0f,
                    float footRaiseHeight = 0.0f,
                    int ms = 1500) {
        resetCmd();
        cmd.mode            = 2;
        cmd.gaitType        = gaitType;
        cmd.velocity[0]     = vx;
        cmd.velocity[1]     = vy;
        cmd.yawSpeed        = wz;
        cmd.bodyHeight      = bodyHeight;
        cmd.footRaiseHeight = footRaiseHeight;
        cout << "[RUN] mode 2 : Walk  vx=" << vx << " vy=" << vy
             << " wz=" << wz << " gait=" << (int)gaitType
             << " (" << ms << " ms)\n";
        sendLoopMs(ms);
    }

    // mode 3 : Walk by target position (reserved — 송신 생략)
    void mode3_reserved() {
        cout << "[INFO] mode 3 = reserved for future release. 송신 생략.\n";
    }

    // mode 4 : Walk by path (reserved — 송신 생략)
    void mode4_reserved() {
        cout << "[INFO] mode 4 = reserved for future release. 송신 생략.\n";
    }

    // mode 5 : Stand down — 몸 낮추기 (오래 유지 금지)
    void mode5_standDown(int ms = 800) {
        resetCmd();
        cmd.mode = 5;
        cout << "[RUN] mode 5 : Stand down (" << ms << " ms)\n";
        sendLoopMs(ms);
    }

    // mode 6 : Stand up — 기립
    void mode6_standUp(int ms = 2500) {
        resetCmd();
        cmd.mode = 6;
        cout << "[RUN] mode 6 : Stand up (" << ms << " ms)\n";
        sendLoopMs(ms);
    }

    // mode 7 : Damping — 소프트 비상정지
    void mode7_damping(int ms = 1000) {
        resetCmd();
        cmd.mode = 7;
        cout << "[RUN] mode 7 : Damping (" << ms << " ms)\n";
        sendLoopMs(ms);
    }

    // mode 8 : Recovery — 낙상 후 자세 복구
    void mode8_recovery(int ms = 2000) {
        resetCmd();
        cmd.mode = 8;
        cout << "[RUN] mode 8 : Recovery (" << ms << " ms)\n";
        sendLoopMs(ms);
    }

    // =========================================================
    // mode 9~13 : 특수 퍼포먼스 동작 (즉시 실행)
    //
    // 공통 실행 흐름
    //   mode1(stand) 선행 자세 → 목표 모드 송신
    //   → state.mode 폴링으로 동작 완료 감지
    //   → mode8(Recovery) 자세 안정화 → mode1 복귀
    //
    // 실행 전 반드시 확인
    //   - 로봇 주변 최소 2m × 2m 공간 확보
    //   - backflip / jumpYaw : 평탄하고 미끄럽지 않은 바닥
    //   - straightHand       : 앞쪽 1.5m 이상 여유 공간
    //   - dance1 / dance2    : 케이블·장애물 사전 정리
    // =========================================================

    // 동작 완료 감지 헬퍼
    // targetMode 에서 state.mode 가 벗어날 때까지 폴링
    // maxWaitMs 초과 시 타임아웃으로 강제 진행
    void waitUntilDone(int targetMode, int maxWaitMs = 8000) {
        const int pollDt = 50;
        int waited = 0;
        while (waited < maxWaitMs) {
            recvOnce();
            if ((int)state.mode != targetMode) {
                cout << "  동작 완료 감지 (state.mode=" << (int)state.mode
                     << ", " << waited << "ms 경과)\n";
                return;
            }
            this_thread::sleep_for(chrono::milliseconds(pollDt));
            waited += pollDt;
        }
        cout << "  타임아웃 (" << maxWaitMs << "ms) — 강제 복구 진행\n";
    }

    // 공통 복귀 흐름: 착지 후 잠깐 대기 → mode8 안정화 → mode1
    void recoverToStand() {
        this_thread::sleep_for(chrono::milliseconds(300)); // 착지 충격 흡수
        cout << "  mode8(Recovery)으로 자세 안정화\n";
        mode8_recovery(1500);
        cout << "  mode1(stand)으로 복귀\n";
        mode1_stand(0, 0, 0, 0.0f, 1500);
    }

    // mode 9 : backflip — 뒤로 공중제비
    void mode9_backflip() {
        cout << "[RUN] mode 9 : backflip\n";
        cout << "  선행 자세(mode1) 준비 중...\n";
        mode1_stand(0, 0, 0, 0.0f, 1500);

        resetCmd();
        cmd.mode = 9;
        sendLoopMs(200); // 단발성 트리거
        cout << "  동작 완료 대기 중...\n";
        waitUntilDone(9, 5000);
        recoverToStand();
    }

    // mode 10 : jumpYaw — 점프 + yaw 회전
    void mode10_jumpYaw() {
        cout << "[RUN] mode 10 : jumpYaw\n";
        cout << "  선행 자세(mode1) 준비 중...\n";
        mode1_stand(0, 0, 0, 0.0f, 1500);

        resetCmd();
        cmd.mode = 10;
        sendLoopMs(200); // 단발성 트리거
        cout << "  동작 완료 대기 중...\n";
        waitUntilDone(10, 4000);
        recoverToStand();
    }

    // mode 11 : straightHand — 뒷발 직립 퍼포먼스
    void mode11_straightHand() {
        cout << "[RUN] mode 11 : straightHand\n";
        cout << "  선행 자세(mode1) 준비 중...\n";
        mode1_stand(0, 0, 0, 0.0f, 1500);

        resetCmd();
        cmd.mode = 11;
        sendLoopMs(200); // 단발성 트리거
        cout << "  동작 완료 대기 중...\n";
        waitUntilDone(11, 5000);
        recoverToStand();
    }

    // mode 12 : dance1 — 퍼포먼스 댄스 1
    void mode12_dance1() {
        cout << "[RUN] mode 12 : dance1\n";
        cout << "  선행 자세(mode1) 준비 중...\n";
        mode1_stand(0, 0, 0, 0.0f, 1500);

        resetCmd();
        cmd.mode = 12;
        sendLoopMs(200); // 단발성 트리거
        cout << "  동작 완료 대기 중...\n";
        waitUntilDone(12, 8000);

        this_thread::sleep_for(chrono::milliseconds(300));
        cout << "  mode0(idle)으로 복귀\n";
        mode0_idle(500);
    }

    // mode 13 : dance2 — 퍼포먼스 댄스 2
    void mode13_dance2() {
        cout << "[RUN] mode 13 : dance2\n";
        cout << "  선행 자세(mode1) 준비 중...\n";
        mode1_stand(0, 0, 0, 0.0f, 1500);

        resetCmd();
        cmd.mode = 13;
        sendLoopMs(200); // 단발성 트리거
        cout << "  동작 완료 대기 중...\n";
        waitUntilDone(13, 8000);

        this_thread::sleep_for(chrono::milliseconds(300));
        cout << "  mode0(idle)으로 복귀\n";
        mode0_idle(500);
    }

    // =========================================================
    // 추천 전이 시퀀스
    // =========================================================

    // s1 : 댐핑 → 낮추기 → 기립
    void seq_standup() {
        cout << "[SEQ] s1 : mode7 → mode5 → mode6\n";
        mode7_damping(800);
        mode5_standDown(600);
        mode6_standUp(2200);
    }

    // s2 : 기립 → 서기 → 걷기
    void seq_unlockWalk() {
        cout << "[SEQ] s2 : mode6 → mode1 → mode2\n";
        mode6_standUp(2200);
        mode1_stand(0, 0, 0, 0.0f, 1200);
        mode2_walk(0.15f, 0.0f, 0.0f, 1, 0.0f, 0.0f, 1200);
        mode0_idle(300);
    }

    // s3 : 걷기 종료 후 점차 낮추기
    void seq_crouch() {
        cout << "[SEQ] s3 : mode2 → mode1 → mode6 → mode5 → mode7\n";
        mode2_walk(0.0f, 0.0f, 0.0f, 0, 0.0f, 0.0f, 400);
        mode1_stand(0, 0, 0, 0.0f, 600);
        mode6_standUp(800);
        mode5_standDown(600);
        mode7_damping(800);
    }

    // s4 : 낙상 후 복구
    void seq_fallRecovery() {
        cout << "[SEQ] s4 : mode7 → mode8\n";
        mode7_damping(800);
        mode8_recovery(2000);
    }

    // =========================================================
    // 메뉴
    // =========================================================
    void printMenu() {
        cout << "\n========== Go1 ALL MODE TESTER ==========\n";
        cout << "--- 모드 번호 직접 입력 (0~13) ---\n";
        cout << "  0   Idle\n";
        cout << "  1   Stand\n";
        cout << "  2   Walk  (방향은 wf/wb/wl/wr/yl/yr 사용)\n";
        cout << "  3   Walk by position  (reserved, 송신 생략)\n";
        cout << "  4   Walk by path      (reserved, 송신 생략)\n";
        cout << "  5   Stand down\n";
        cout << "  6   Stand up\n";
        cout << "  7   Damping\n";
        cout << "  8   Recovery\n";
        cout << "  9   backflip\n";
        cout << " 10   jumpYaw\n";
        cout << " 11   straightHand\n";
        cout << " 12   dance1\n";
        cout << " 13   dance2\n";
        cout << "--- 보행 방향 단축 커맨드 ---\n";
        cout << " wf  forward   wb  backward\n";
        cout << " wl  left      wr  right\n";
        cout << " yl  yaw-left  yr  yaw-right\n";
        cout << "--- gaitType 시험 ---\n";
        cout << " g0  Idle   g1  Trot walking   g2  Trot running\n";
        cout << " g3  Stairs climbing            g4  Trot obstacle\n";
        cout << "--- 시퀀스 ---\n";
        cout << " s1  stand-up   s2  unlock-walk   s3  crouch   s4  fall-recovery\n";
        cout << "--- 기타 ---\n";
        cout << " st  print state     q  exit\n";
        cout << "=========================================\n";
        cout << "cmd> ";
    }

private:
    UDP       udp;
    HighCmd   cmd   = {0};
    HighState state = {0};
};

// =========================================================
// main
// =========================================================
int main(int argc, char* argv[]) {
    const char*   robotIp   = "192.168.123.161";  // 기본값
    const int     localPort = 8080;
    const int     robotPort = 8082;
    const uint8_t level     = HIGHLEVEL;

    // 커맨드라인 옵션 파싱
    // 사용 예: sudo ./go1_all_mode_tester_full --robot_ip 192.168.123.161
    for (int i = 1; i < argc; ++i) {
        if (string(argv[i]) == "--robot_ip" && i + 1 < argc) {
            robotIp = argv[++i];
        } else {
            cerr << "[WARN] 알 수 없는 옵션: " << argv[i] << "\n";
            cerr << "사용법: " << argv[0] << " [--robot_ip <IP>]\n";
        }
    }

    Go1AllModeTester tester(level, localPort, robotIp, robotPort);

    cout << "[INFO] Go1 high-level tester started\n";
    cout << "[INFO] robotIp=" << robotIp
         << "  localPort=" << localPort
         << "  robotPort=" << robotPort << "\n";

    while (true) {
        tester.printMenu();

        string token;
        if (!getline(cin, token)) break;

        // 앞뒤 공백 제거
        {
            size_t s = token.find_first_not_of(" \t");
            size_t e = token.find_last_not_of(" \t");
            token = (s == string::npos) ? "" : token.substr(s, e - s + 1);
        }
        if (token.empty()) continue;

        // ── 종료 ──────────────────────────────────────────────
        if (token == "q" || token == "Q") {
            tester.mode0_idle(300);
            cout << "[INFO] exit\n";
            break;
        }

        // ── 모드 번호 0~13 ────────────────────────────────────
        bool isNum = !token.empty();
        for (char c : token) if (!isdigit(c)) { isNum = false; break; }

        if (isNum) {
            int m = stoi(token);
            switch (m) {
                case 0:  tester.mode0_idle(1000);      break;
                case 1:  tester.mode1_stand();         break;
                case 2:
                    // mode2 단독: 제자리 trot (속도 0)
                    tester.mode2_walk(0.0f, 0.0f, 0.0f, 1, 0.0f, 0.0f, 1000);
                    tester.mode0_idle(300);
                    break;
                case 3:  tester.mode3_reserved();      break;
                case 4:  tester.mode4_reserved();      break;
                case 5:  tester.mode5_standDown(800);  break;
                case 6:  tester.mode6_standUp(2500);   break;
                case 7:  tester.mode7_damping(1200);   break;
                case 8:  tester.mode8_recovery(2000);  break;
                case 9:  tester.mode9_backflip();      break;
                case 10: tester.mode10_jumpYaw();      break;
                case 11: tester.mode11_straightHand(); break;
                case 12: tester.mode12_dance1();       break;
                case 13: tester.mode13_dance2();       break;
                default:
                    cout << "[WARN] 유효 범위 초과 (0~13): " << m << "\n";
                    break;
            }
            continue;
        }

        // ── 보행 방향 단축 커맨드 ─────────────────────────────
        if (token == "wf") {
            tester.mode2_walk( 0.20f,  0.0f,  0.0f, 1, 0.0f, 0.0f, 1500);
            tester.mode0_idle(300);
        } else if (token == "wb") {
            tester.mode2_walk(-0.10f,  0.0f,  0.0f, 1, 0.0f, 0.0f, 1200);
            tester.mode0_idle(300);
        } else if (token == "wl") {
            tester.mode2_walk( 0.0f,  0.10f,  0.0f, 1, 0.0f, 0.0f, 1200);
            tester.mode0_idle(300);
        } else if (token == "wr") {
            tester.mode2_walk( 0.0f, -0.10f,  0.0f, 1, 0.0f, 0.0f, 1200);
            tester.mode0_idle(300);
        } else if (token == "yl") {
            tester.mode2_walk( 0.0f,  0.0f,  0.40f, 1, 0.0f, 0.0f, 1200);
            tester.mode0_idle(300);
        } else if (token == "yr") {
            tester.mode2_walk( 0.0f,  0.0f, -0.40f, 1, 0.0f, 0.0f, 1200);
            tester.mode0_idle(300);

        // ── gaitType 시험 ──────────────────────────────────────
        } else if (token == "g0") {
            tester.mode2_walk(0.10f, 0.0f, 0.0f, 0, 0.0f, 0.0f, 800);
            tester.mode0_idle(300);
        } else if (token == "g1") {
            tester.mode2_walk(0.18f, 0.0f, 0.0f, 1, 0.0f, 0.0f, 1200);
            tester.mode0_idle(300);
        } else if (token == "g2") {
            tester.mode2_walk(0.28f, 0.0f, 0.0f, 2, 0.0f, 0.0f, 1000);
            tester.mode0_idle(300);
        } else if (token == "g3") {
            tester.mode2_walk(0.08f, 0.0f, 0.0f, 3, 0.0f, 0.03f, 1000);
            tester.mode0_idle(300);
        } else if (token == "g4") {
            tester.mode2_walk(0.12f, 0.0f, 0.0f, 4, 0.0f, 0.02f, 1000);
            tester.mode0_idle(300);

        // ── 시퀀스 ────────────────────────────────────────────
        } else if (token == "s1") {
            tester.seq_standup();
        } else if (token == "s2") {
            tester.seq_unlockWalk();
        } else if (token == "s3") {
            tester.seq_crouch();
        } else if (token == "s4") {
            tester.seq_fallRecovery();

        // ── 상태 출력 ─────────────────────────────────────────
        } else if (token == "st") {
            tester.printState();

        } else {
            cout << "[WARN] 알 수 없는 커맨드: '" << token << "'\n";
        }
    }

    return 0;
}
