# 실제 역할 Work 연결 지침

사용자는 역할별 Work 생성·예약과 이 설계대로 작업 전달을 승인했다.
이 대화가 연결 담당이다. 역할 Work는 서로 메시지를 보내지 않고 결과 파일을 반환한다.
공통 루트는 이 파일의 상위 aitrading이다. 모든 기존 Python 명령은 공통 루트에서 직접 실행한다.
코드·패키지·규칙·.env·원장은 수정하지 않는다. 키·계좌번호·원장 전체를 출력하지 않는다.

1. .cache/multiflow/workers.json의 실제 threadId/hostId를 읽는다. 이름으로 다른 작업을 추측하지 않는다.
   8개 준비 기록의 root가 공통 루트인지 확인한다. 누락되면 해당 흐름을 보류한다.
2. flow.py --start mock으로 FOLDER를 받는다. verification은 별도 주문 없는 연결 검증 전용이다.
   기존 진행 회차가 있으면 상태를 조사하고 새 자료 수집·판단·결정서 주문을 시작하지 않는다.
   단, 모의 환경 확인 후 기존 trade.py --scan 위험 점검과 --account 조회는 연결 담당이 별도로 시도한다.
   기존 엔진 잠금도 우회하지 않는다. 이 독립 위험 점검을 새 회차·새 결정서 실행으로 위장하지 않는다.
   완료 실패·미완료를 완료라고 바꾸지 않는다.
3. flow.py --issue FOLDER STAGE로 작업 파일을 만든다.
   STAGE: risk/data/quant/A/B/C/execute/discovery/evaluation. risk/execute의 role은 execution이다.
4. send_message_to_thread로 등록된 해당 Work에 작업 파일 절대경로·역할과
   workflows/WORKER.md에 따라 --inspect 후 --run을 하라는 작업을 전달한다.
   판단 A/B/C는 같은 공통 파일·원칙만 주고 부모 의견을 쓰지 않는다. 세 Work에 각각 전달하고 병렬로 기다린다.
5. wait_threads를 사용하되 60초 이하로 나누어 기다린다. 10분 안에 끝나지 않거나 실패하면 다음 주문을 보류한다.
   .done.json의 존재와 flow.py가 검사하는 job_hash/run_id/input_id를 확인한다.
   채팅의 '완료'만으로 통과시키지 않는다. 수집 후 quant, quant 후 A/B/C, 집계 후 실행의 순서를 지킨다.
   작업 실패·타임아웃·응답 불명은 자동 재전송하지 않는다. 다음 위험 점검과 체결 대조에 맡긴다.
6. 세 응답이 다 유효할 때 flow.py --aggregate FOLDER로 집계한다.
   verification은 이 회차 안에만 결정서를 저장하고 운영 결정서를 게시하지 않는다.
   mock도 gate=request와 동일 공통 입력이 필요하다. 실패하면 과거 결정서로 대신 주문하지 않는다.
7. evaluation 작업 후에는 evaluation-report.md가 실제 작성됐는지도 확인한다.
   flow.py --finish FOLDER로 회차를 종료한다. 모든 실패 이유와 누락을 보고한다.

예약은 세 개만 둔다. 평일 07:30 discovery-coordinator.md, 장중 매시간 schedule.txt,
평일 16:00 evaluation-coordinator.md. 여덟 워크를 여덟 개 타이머로 각각 돌리지 않는다.
시장 휴장·새 의견 불필요 상태에서는 주문과 판단 호출을 억지로 만들지 않는다.
