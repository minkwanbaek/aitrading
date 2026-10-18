# 일일 기업 변화 조사

2026-10-18 운영 변경: 평일 07:30 일일 변화 조사, 7일마다 전체 테마·관찰 목록 검토입니다.
이번 개발 회차에서는 사업·재무 원문 조사를 새로 수행하지 않았습니다. 기존 3개 테마·15개 관찰 기업은
모두 watch이며, 다음 기업발굴 예약이 확인한 사실과 변경 이유를 이 문서에 남깁니다.

공식 출처를 실제로 읽은 시각과 전체 테마 검토 시각을 구분합니다. 목록의 날짜만 바꿔 조사 완료로
표시하거나 매일 전부 교체하지 않습니다. 이 문서는 매수·매도 지시가 아닙니다.

## 2026-10-18 테마별 우선 후보 3개 보완
- 회차: e53b2fe43e0d4666a1ae637014867630 / 작업: discovery / input_id: null
- 실제 읽은 시각(UTC): 2026-10-18T06:51:12.4534945+00:00. 이번 일일 심층 범위는 로보티즈(108490), LS ELECTRIC(010120), HPSP(403870) 3개뿐이다. 기존 3개 테마와 15개 목록을 유지하며 전체 검토 시각은 갱신하지 않는다.
- 로보티즈: https://www.robotis.com/en/ 및 https://en.robotis.com/model/page.php?co_id=prd_dynamixel 에서 액추에이터 사업을 확인했다. 최신 재무 대상은 2026.06 반기보고서 https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260814003380 이나 DART·KIND 원문 접근 실패. 2차 요약 수치는 검증 근거로 쓰지 않았다. 최신 이익·영업현금흐름·재무부담 미확인으로 watch.
- LS ELECTRIC: https://www.ls-electric.com/ko/company/invest/ir/ 에서 제공하는 https://www.ls-electric.com/ko/company/data/2026_2%EB%B6%84%EA%B8%B0_%EC%8B%A4%EC%A0%81%EC%9E%90%EB%A3%8C.pdf 를 실제 읽었다. 보고기간은 2026년 2분기, 2026년 7월 자료이며 외부감사인 검토 전 잠정이다. p4 매출 15,770억원·영업이익 1,785억원, p5 전력제품 매출·수주 표, p7 현금 6,724억원·순차입금 8,055억원·부채비율 172% 확인. 전분기 순차입금 6,116억원 대비 증가. 최신 확정 영업현금흐름과 가격 판단 미완료로 watch.
- HPSP: https://thehpsp.com/sub/sub02.php?page=3 에서 고압수소 어닐링 장비 용도 확인. https://thehpsp.com/bbs/board.php?bo_table=IR_Materials&wr_id=30 및 https://thehpsp.com/bbs/board.php?bo_table=IR_Materials&wr_id=28 의 26.2Q 자료 게시일(2026-08-19)은 확인했지만 첨부 다운로드는 '잘못된 접근' 오류. https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260814004015 원문 접근도 실패하여 최신 재무 수치는 기록하지 않았다. 현금흐름·재무부담 미확인으로 watch.
- 공통 위험/가설: 시설투자 확대가 세 테마 수요로 이어질 가능성은 가설이다. 납기·고객 투자 지연, 경쟁, 수주와 현금 회수의 시차가 공통 위험이다. 고객·점유율·주가 반영·미래 수익률을 단정하지 않았다.
- 변경 이유/한계: 실제 확인한 사업 근거, LS의 부분 재무 사실, 접근 실패와 반증 조건을 보완했다. eligible 신규 0개이며 15개 모두 watch를 유지한다. 최신 현금흐름 등 필수 확인을 건너뛰지 않았다. 다른 12개를 조사한 것으로 표시하지 않았다.

## 2026-10-18 기존 조사 연속 보완: 역할 기준 재검토
- 회차 dac3f72975d646e6a2915251ad836726 / 작업 discovery / input_id null / 재검토 시각 UTC 2026-10-18T06:53:58.3680572+00:00.
- 새 기업 조사 없이 앞선 3개 중 LS ELECTRIC의 이미 확인한 공식 사업·2026년 2분기 IR 손익 및 재무상태 근거를 재검토했다. research.validate/eligible의 기존 최소 조건(공식 사업·재무 verified 근거, 7일 이내 실제 확인 시각, 서로 다른 출처 URL 2개 이상, financial_case와 invalidation)을 충족하므로 LS ELECTRIC만 watch에서 eligible로 변경한다.
- 출처: https://www.ls-electric.com/ko/ 및 https://www.ls-electric.com/ko/company/data/2026_2%EB%B6%84%EA%B8%B0_%EC%8B%A4%EC%A0%81%EC%9E%90%EB%A3%8C.pdf . 기존 실제 열람 시각을 유지하며 출처를 다시 읽었다고 표시하지 않는다. 전체 검토 시각도 유지한다.
- 앞선 watch 판단에서 확정 영업현금흐름·가격 판단을 발굴의 추가 문턱으로 둔 부분을 정정한다. eligible은 기본 근거가 있어 장중 검토 가능하다는 뜻이며 매수 결론이 아니다. 가격·금액·매수 여부는 퀀트와 독립 A/B/C에 맡긴다.
- 미확인 최신 확정 영업현금흐름, 외부 검토 전 잠정 수치, 전분기 대비 순차입금 증가 위험은 financial_case/risk에 그대로 남겼다. 로보티즈·HPSP는 공식 최신 재무 원문 미확인으로 watch 유지. 다른 12개도 변경하지 않았다.
