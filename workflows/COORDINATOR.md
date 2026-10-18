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
   조회·수집·계산의 일시 실패는 원인을 확인하고 같은 JOB으로 한 번만 다시 실행할 수 있다.
   주문을 보낼 수 있는 risk/execute의 실패·응답 불명은 재실행하지 않고 기존 체결 대조에 맡긴다.
   세 판단이 실패하면 원문을 보존하고 실패를 hold로 바꾸지 않는다.
   Work가 --run 전에 끝나거나 10분 초과·도구 실패·필수 결과 누락이면
   alerts.py --failure FOLDER --stage STAGE --category timeout/missing/tool/blocked 중 실제 사유로 알린다.
   --run/--inspect/--aggregate 도구 안의 오류는 자동 알림이며 같은 회차·단계는 중복 발송하지 않는다.
   --start 이전의 충돌 등 유효 회차가 없는 실패는 Work에 남긴다. 기기/앱 자체가 꺼지면 알림을 보낼 수 없다.
6. 세 응답이 다 유효할 때 flow.py --aggregate FOLDER로 집계한다.
   verification은 이 회차 안에만 결정서를 저장하고 운영 결정서를 게시하지 않는다.
   mock도 gate=request와 동일 공통 입력이 필요하다. 실패하면 과거 결정서로 대신 주문하지 않는다.
7. evaluation 작업 후에는 evaluation-report.md가 실제 작성됐는지도 확인한다.
   flow.py --finish FOLDER로 회차를 종료한다. 모든 실패 이유와 누락을 보고한다.
   오류가 나도 --finish로 회차를 정리해 다음 예약을 막지 않는다. 이 정리는 만료 후에도 가능하다.
   discovery 완료 시 후보 변경이 있을 때만 아침 알림이 자동 발송된다.
   evaluation 완료·보고서 작성 후 --finish가 장 마감 종합 보고를 하루 한 번 발송한다.
   --finish의 notification에서 delivered 또는 duplicate+prior_status=delivered일 때만 발송 성공으로 보고한다.
   unconfirmed·local_error는 성공이 아니다. 원문/키를 출력하거나 불명 응답을 자동 재전송하지 않는다.
   정기 알림을 수동으로 복제하거나 매시간 정상/보유 알림을 추가하지 않는다.

예약은 세 개만 둔다. 평일 07:30 discovery-coordinator.md, 장중 매시간 schedule.txt,
평일 16:00 evaluation-coordinator.md. 여덟 워크를 여덟 개 타이머로 각각 돌리지 않는다.
시장 휴장·새 의견 불필요 상태에서는 주문과 판단 호출을 억지로 만들지 않는다.
