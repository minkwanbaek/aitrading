# aitrading

기존 업비트 자동매매 프로젝트를 기반으로 NH투자증권 Namuh PLUG 주식 자동매매 기능을 개발합니다.

## 현재 구성

- `autotrade.py`: 기존 업비트 비트코인 자동매매. 실행하면 실제 주문 경로에 진입할 수 있습니다.
- `streamlit_app.py`: 기존 비트코인 거래 기록 조회 화면.
- `strategy.txt`: 기존 비트코인 전략 설명.
- `.env.example`: NH 설정용 빈 양식.
- `CREDENTIALS.md`: 로컬 키 입력 및 개발 중 설정 참조 방법.

NH 키 입력 파일은 루트 `.env`이며 Git에서 제외됩니다. 기본값은 모의투자(`NH_MOCK=1`)입니다.
NH 예제 코드와 검사 코드를 가져왔습니다. 예약은 아직 등록하지 않았습니다.

## NH 자동매매 구성

`setup.py`는 설정 화면, `broker.py`는 NH API 연결, `strategy.py`는 투자 전략,
`trade.py`는 조회·검증·주문, `telegram.py`는 실전 주문 승인,
`board.py`는 현황 화면입니다. 예약 지침은 `schedule.txt`를 사용합니다.

NH 전용 의존성은 `requirements-nh.txt`에 분리했습니다. 기존 업비트용
`requirements.txt` 및 프로그램은 유지합니다. 로컬 `.venv`는 NH 개발용입니다.

현재 종목·금액·손절·익절은 원본 예제 값이며 사용자 맞춤 전략은 아직 정하지 않았습니다.
실전 계좌에서는 손절·익절 매도도 Telegram 승인을 거칩니다.
조회 미리보기는 주문하지 않지만, 일반 스캔은 손절·익절 주문을 실행할 수 있습니다.

## 개발 순서

1. 로컬 설정과 키 보관 기반 준비.
2. NH SDK 연동 및 모의 계좌 연결·잔고·시세 조회.
3. 투자 전략과 주문 전 검증, 주문하지 않는 미리보기.
4. 모의 주문 및 거래 기록 화면.
5. 투자 전략을 확정한 뒤 실전 승인 흐름과 정기 실행 구성.

## 참고 자료

- [영상에서 사용한 예제 저장소](https://github.com/youtube-jocoding/nhplug-auto-trader)
- [NH PLUG](https://www.nhplug.com/)
- [NH PLUG 공식 SDK](https://github.com/PLUG-OpenAPI/nhplug-sdk)

예제의 전략과 주문 흐름을 참고하되 현재 코드와 검증한 API 동작을 기준으로 구현합니다.
도입한 원본 커밋: `17f1a9e18aa895f9511ea7297988d512d158be9e`.
원본 MIT 저작권 고지는 `LICENSE`, 원본 사용 설명은 `UPSTREAM_README.md`에 보존했습니다.
원본 설명과 실제 코드가 다를 경우 코드와 검증 결과를 우선합니다.

## 초기 구성 검증 (2026-10-17)

- 프로젝트 단위 테스트 64개 통과, 전략 검사 통과.
- NH 모의 계좌 연결, 주문가능 현금 및 국내 보유 조회 성공.
- 주문 없는 미리보기 정상: 휴장 상태에서 주문하지 않음.
- Telegram 테스트 메시지 전송 성공.
- 실제 주문 및 정기 실행 예약은 수행하지 않음.
