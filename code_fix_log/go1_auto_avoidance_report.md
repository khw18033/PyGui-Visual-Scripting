## Go1 Auto Avoidance 변경 보고서

**작성일:** 2026-04-28

**목적**
- JSON 수신 시 근접 인물(person, `risk_level=='near'`)에 대해 즉시 정지 대신 위치 기반 횡회피 명령을 0.5초간 주입하도록 변경.

**변경 파일(요약)**
- `nodes/robots/go1.py`: Auto Avoidance 노드 로직 수정 — bbox 중심 계산, 위치 분류, 회피 명령 주입, 로깅
- `core/engine.py`: `GO1_AUTO_AVOIDANCE`를 주기 실행 목록에서 제거(중복 실행 방지)
- UI/Factory 관련 파일: 노드 등록 및 UI 렌더러는 기존 적용됨

**동작 요약**
- 입력: payload의 `detections` 배열에서 `risk_level=='near'`이며 `name=='person'`인 항목만 처리.
- 바운딩박스 중심 계산: `center_x = (x1 + x2) / 2.0` (이미지 너비 = 464px)
- 위치 분류(최종 구현):
  - `center` : 오직 `center_x == 232.0`일 때만 적용
  - `left` : `center_x < 232.0`
  - `right` : `center_x > 232.0`
- 회피 매핑: `left` 또는 `center` → 오른쪽으로 0.5s 이동, `right` → 왼쪽으로 0.5s 이동
- 명령 주입: 가능하면 `GO1_SERVER_JSON_RECV` 노드의 `_inject_direction_motion(dir, speed, duration, signature)` 호출, 실패 시 안전하게 정지 명령으로 폴백

---

### 샘플 JSON 분석
파일: `jsonbackup/1777363709_8966951_go1_front.json`

관심 대상(사람, `risk_level=='near'`) 항목은 id=5 항목입니다:

```json
{
  "id": 5,
  "name": "person",
  "group": "AGENT",
  "rel_depth": 0.981680154800415,
  "risk_level": "near",
  "bbox_xyxy": [387, 152, 406, 222]
}
```

계산:
- center_x = (387 + 406) / 2 = 396.5
- 이미지 중심 = 464 / 2 = 232.0
- 상대 위치(rel) = 396.5 / 464 ≈ 0.8545 → `right`

결론: 판정은 `right` 이므로 로봇은 왼쪽으로 0.5초간 회피하도록 명령이 주입됩니다.

예상 로그 (샘플):

- [GO1 AUTO AVOID] near person detected | id=5 | rel_depth=0.981680154800415
- [GO1 AUTO AVOID] 왼쪽으로 이동 (id=5 | rel_depth=0.981680154800415)
- [GO1 JSON RX] command=left -> move vx=0.000, vy=0.200, wz=0.000, duration=0.50s

---

**주의/추천**
- 현재 `center` 판정이 매우 엄격합니다(정확히 중앙일 때만). 실환경에서 중앙 정확 일치를 기대하기 어렵다면 임계값(예: 0.4/0.6 또는 ±2px)으로 완화할 것을 권장합니다.
- 추가로 동일 이벤트의 중복 실행을 완벽히 막기 위해 노드 내부 디바운스(예: 최근 명령과 동일 대상/시그니처면 무시)도 병행하면 안전합니다.

**재현 방법(간단)**
1. 엔진과 노드를 실행하고 `GO1_SERVER_JSON_RECV`가 해당 JSON을 읽도록 배치합니다.
2. 로그에서 위의 예상 로그 라인들이 출력되는지 확인합니다.

---

보고서 작성자: 구현팀
