"""매매 판단. **이 파일 하나만** 성향에 맞게 바꾸면 됩니다.

현재 기본 전략은 quant.py의 고정 상대 강도 계산입니다. AI는 기업 발굴·사실 확인만 합니다.
managed.py가 검증한 퀀트 결정만 decide(m)의 quant_decision으로 전달합니다.
아래 Codex 판단 설명과 m["ai"] 경로는 QUANT_ENABLED=False인 이전 예제의 호환 설명입니다.
이 파일은 고정 원칙과 최종 행동 검사를 담습니다.

  INSTRUCTIONS  Codex에게 주는 투자 원칙. 성향을 바꾸려면 여기를 고칩니다.
  decide(m)     Codex의 답을 받아 최종 결정. 여기서 넘지 말아야 할 선을 지킵니다.

Codex가 없거나(설치 안 됨·시간 초과·이상한 답) 판단을 못 받으면 **아무것도 사지
않습니다.** 다만 손절·익절은 Codex에게 묻지 않고 규칙이 먼저 자릅니다. Codex가
조용한 날에도 손절은 동작해야 하기 때문입니다.

고친 뒤에는 `python check.py` 로 검사하세요. 통과해야 씁니다.

━━ 지켜야 하는 약속 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

아래 다섯 개를 반드시 정의할 것:
  SYMBOLS        국내 6자리 종목코드 문자열 리스트. 안 하면 []
                 (삼성전자 005930, SK하이닉스 000660, 카카오 035720,
                  NAVER 035420, 현대차 005380, LG에너지솔루션 373220)
  US_SYMBOLS     미국 티커 대문자 리스트. 안 하면 []
                 (애플 AAPL, 마이크로소프트 MSFT, 엔비디아 NVDA,
                  테슬라 TSLA, 구글 GOOGL, 아마존 AMZN)
  BUY_AMOUNT     국내 한 종목에 넣을 금액(원, 정수)
  US_BUY_AMOUNT  미국 한 종목에 넣을 금액(달러, 정수)
  MAX_HOLDINGS   동시에 들고 갈 최대 종목 수(정수, 1 이상)

그리고 decide(m) 함수가 ("buy"|"sell"|"hold", 이유문자열) 튜플을 돌려줄 것.

없어도 되지만 있으면 쓰이는 세 가지:
  US_SESSIONS   미국장을 볼 시간대. "pre"(프리마켓) "regular"(정규장)
                "after"(애프터마켓) 중에서 고른 리스트. 없으면 정규장만 봅니다.
                프리·애프터에도 주문은 들어가지만(지정가만) 사려는 사람이 적어
                원하는 값에 안 걸리고, 지표는 일봉이라 아직 어제 것입니다.
  INSTRUCTIONS  Codex에게 그대로 가는 투자 원칙. 없으면 원칙 없이 판단합니다.
  facts(m)      Codex에게 더 보여 줄 사실을 문자열 리스트로 돌려줍니다.
                기본으로는 최근 20일 종가·5일/20일 평균·52주 고저·보유 상태·
                공시가 갑니다. **여기에 없는 값을 원칙에 쓰려면 facts()로
                직접 계산해서 넘겨야 합니다.** 예를 들어 "RSI 70 넘으면 사지 마"
                라고 쓰려면:

                    def facts(m):
                        value = rsi(m["closes"])          # 직접 계산
                        return [f"- RSI(14): {value}"] if value else []

                넘기지 않은 값을 원칙에서 부르면 Codex가 종가로 암산하다 틀립니다.

이것만 지키면 나머지는 자유입니다. 다만:
  - 계산용 표준 라이브러리는 자유롭게 (math, statistics, random, re, json, datetime,
    zoneinfo, itertools, functools, collections, decimal …). 파일·네트워크·프로세스에
    손대는 모듈(os, sys, io, pathlib, socket, urllib, subprocess …)과 eval/exec/open은 금지
  - **여기서 Codex를 직접 부르지 않습니다.** 부를 수도 없습니다(네트워크 금지).
    묻는 일은 Codex 예약이 하고, 이 파일은 그 답을 받아 씁니다
  - closes 가 비어 있거나 짧아도 터지지 않게 할 것 (첫날에는 시세가 없습니다)
  - 이유 문자열은 초보자가 읽어서 이해할 수 있는 한국어로
  - 금액을 이유에 쓸 때는 통화를 맞출 것 (원 / $). m["currency"] 로 판단

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

decide()에 들어오는 m의 내용:
  m["code"]      종목코드        "005930"
  m["name"]      종목이름        "삼성전자"
  m["price"]     현재가          71_000
  m["closes"]    최근 종가들      [69800, 70100, ...]  (오래된 것부터, 오늘 포함)
  m["held"]      보유 중인가      True / False
  m["qty"]       보유 수량        0 이면 미보유
  m["avg"]       평균 매입가      미보유면 0
  m["pnl_pct"]   수익률(%)        미보유면 0.0
  m["cash"]      주문가능 현금     1_000_000
                 미국 종목이어도 **항상 원화**입니다. US_BUY_AMOUNT(달러)와 직접
                 비교하지 마세요. 살 수 있는 수량은 앱이 따로 확인합니다.

  ── Codex의 판단 (예약이 `--do` 로 넘겨 준 것) ──
  m["ai"]  {"decision": "buy"|"sell"|"hold", "reason": "왜 그렇게 봤는지"}
           None 이면 판단을 받지 못한 것입니다. 사면 안 됩니다.

  ── 시세에 딸려 오는 값 (추가 조회 없음) ──
  m["change_pct"]  전일 대비(%)      2.43
  m["turnover"]    거래대금          국내 원, 미국 달러
  m["high_52w"]    52주 최고가       미보유 자료면 None
  m["low_52w"]     52주 최저가
  m["per"]         PER              미국은 None일 수 있음
  m["pbr"]         PBR              미국은 None
  m["market_cap"]  시가총액(원)      미국은 None
  m["industry"]    업종             "코스피 전기·전자"
  m["market"]      어느 장인가       "kr" 또는 "us"
  m["currency"]    통화             "KRW" 또는 "USD"

값이 None이면 "모른다"는 뜻입니다. 0으로 읽지 마세요.

국내와 미국이 같은 모양으로 들어오므로 decide()는 둘을 구분하지 않아도 됩니다.
장을 나눠서 다르게 굴리고 싶으면 m["market"]을 보면 됩니다.
"""

# ── 무엇을 얼마나 살지 ───────────────────────────────────────────────
SYMBOLS = []  # research.py가 관리하는 동적 후보를 사용합니다.
US_SYMBOLS = []  # 국내 주식만 운용합니다.
BUY_AMOUNT = 200_000  # 종목당 상한. 실제 배분은 판단별 budget_krw로 조절합니다.
US_BUY_AMOUNT = 300  # 미국 한 종목에 넣을 금액(달러)
MAX_HOLDINGS = 5
MANAGED_PORTFOLIO = True
DISCOVERY_ENABLED = True
TOTAL_BUDGET = 1_000_000
MAX_LOSS_KRW = 100_000
CASH_RESERVE_KRW = 250_000
REVIEW_DAYS = 21
RESEARCH_DAYS = 7
QUANT_ENABLED = True
QUANT_VERSION = "relative-strength-60-three-votes-v1"
QUANT_DAYS = 60
BENCHMARK_CODE = "069500"  # KODEX 200: 지수 자체가 아닌 가격 비교용 ETF. 매수하지 않습니다.
DEFAULT_BUY_KRW = 150_000
US_SESSIONS = ["regular"]  # 미국을 볼 시간대: "pre" 프리마켓 · "regular" 정규장 · "after" 애프터마켓

# ── Codex에게 주는 투자 원칙 ─────────────────────────────────────────
# 성향을 바꾸고 싶으면 여기를 고치세요. 이 글이 그대로 Codex에게 갑니다.
# 지시가 구체적일수록 판단이 일관됩니다. "알아서 잘"이라고 쓰면 그날그날 달라집니다.
INSTRUCTIONS = """기업 발굴 AI와 장중 독립 판단자 A/B/C의 역할을 구분한다.
세 판단자는 동일 자료와 계산된 퀀트 점수를 받고 서로의 답을 보지 않고 매수·매도·보유를 판단한다.
유효한 세 응답이 모두 있어야 2표 이상 일치한 의견을 채택한다. 셋이 갈리면 보유, 누락·실패면 주문 보류다.
매매는 전일까지 완료된 일봉 60거래일 수익률에서 KODEX 200 가격 수익률을 뺀 상대 강도로 정한다.
퀀트는 후보 순위·공통 근거·매수 조건이며 자동 매수 명령이 아니다. 양수 점수여도 세 AI가 보류할 수 있다.
점수와 투표 비율을 미래 수익률·성공 확률로 표시하지 않는다.
총예산 100만 원, 최대 5종목, 종목당 20만 원 상한, 초기 현금 25만 원을 지킨다.
관찰 후보는 3개 테마의 중복 없는 15~25개 기업이며 개수를 채우기 위한 매수는 하지 않는다.
기업 공식 자료와 공시에서 투자 근거·수혜 경로·위험을 확인하고 최신 뉴스로 반증한다.
자료가 불충분하면 신규 매수를 보류한다. 물타기하지 않는다.
양수 점수의 적격 후보 중 점수가 높은 순서로 기본 15만 원, 상한 20만 원 이내 배분한다.
보유 기업은 21일마다 근거를 재평가하고 연장·매도한다.
중대한 변화는 일일 공식 근거 확인과 신규 매수 적격 여부에 반영하며 뉴스 제목만으로 자동 매도하지 않는다.
계좌 손실 10만 원 도달 시 신규 매수 중단. 개별 종목 -4% 손절이 우선한다.
이 고정 제한은 사용자만 변경할 수 있다. AI는 한도 안의 매매 의견·보수적 매수 금액·가격 상한을 제시한다.
AI는 퀀트 공식·점수·순위·손절·한도를 변경하지 않는다. 매수 금액·상한은 찬성자 중 작은 값을 따른다.
고정 익절은 쓰지 않는다. -4% 손절은 매시간 우선 점검한다.
21일 재평가에는 점수와 근거를 다시 검토해 세 AI가 매도·보유를 판단한다. 기간만으로 강제 매도하지 않는다.
보유 연장은 양수 점수·최신 사업/재무 근거·두 판단자의 명시적 연장 의견이 모두 있어야 한다.
보유 기업의 중대한 근거 훼손은 21일 전에도 두 판단자의 매도 의견으로 반영할 수 있다.
재평가 사이에는 순위 하락·RSI·MACD만으로 교체하지 않는다. 자료 실패는 연장 완료로 표시하지 않는다.
같은 기업은 같은 신호일에 신규 매수를 한 번만 시도한다. 취소·손절 후 같은 날 재진입하지 않는다.
뉴스 검색 성공의 빈 결과는 악재도 호재도 아니다. 제목 존재를 매수 조건으로 삼지 않는다.
52주 고점 제외와 RSI/MACD 매매 조건은 이 퀀트 전략에서 사용하지 않는다.
"""

# ── Codex가 뭐라 하든 지키는 선 ──────────────────────────────────────
# 판단은 Codex가 하지만, 이 선은 규칙이 지킵니다. Codex가 죽어도 동작합니다.
STOP_LOSS_PCT = -4.0  # 사용자 확정값. AI가 변경하지 않습니다.
TAKE_PROFIT_PCT = None  # 수익률만으로 자동 익절하지 않습니다.

# 크게 버는 규칙보다 크게 잃지 않는 규칙이 오래갑니다.
MIN_TURNOVER_KRW = 10_000_000_000  # 퀀트는 완료된 20일 평균 거래대금에 적용합니다.
MIN_TURNOVER_USD = 50_000_000  # 미국 하루 거래대금 5천만 달러 미만이면 안 삼
MAX_NEAR_HIGH_PCT = 95.0  # 이전 예제 전용. 활성 퀀트 매수에는 적용하지 않습니다.


def decide(m):
    """한 종목을 보고 무엇을 할지 정합니다. ("buy"|"sell"|"hold", 이유)

    순서가 중요합니다. Codex에게 묻기 **전에** 규칙이 먼저 걸러야, Codex가
    조용한 날에도 손절이 돌고 사면 안 되는 종목을 사지 않습니다.
    """
    if m["held"]:
        # 손절·익절은 Codex 의견을 묻지 않습니다. 넘으면 그냥 팝니다.
        if m["pnl_pct"] <= STOP_LOSS_PCT:
            return "sell", f"손절 기준 {STOP_LOSS_PCT}% 도달 (현재 {m['pnl_pct']:+.2f}%)"
        if TAKE_PROFIT_PCT is not None and m["pnl_pct"] >= TAKE_PROFIT_PCT:
            return "sell", f"익절 기준 +{TAKE_PROFIT_PCT}% 도달 (현재 {m['pnl_pct']:+.2f}%)"
    else:
        skip = None if QUANT_ENABLED and m.get('quant_decision') else why_not_buy(m)
        if skip:
            return "hold", skip

    if QUANT_ENABLED:
        call = m.get('quant_decision') or {}
        if call.get('strategy_version') != QUANT_VERSION or call.get('decision') not in ('buy', 'sell', 'hold'):
            return 'hold', '검증된 퀀트 결정이 없어 보유·관찰합니다'
        decision = call['decision']
        reason = call.get('reason') or '퀀트 규칙에 따른 보유·관찰'
        if m['held']:
            return ('sell', reason) if decision == 'sell' else ('hold', reason)
        return ('buy', reason) if decision == 'buy' else ('hold', reason)

    call = m.get("ai") or {}
    decision = call.get("decision")
    reason = (call.get("reason") or "").strip()
    if decision not in ("buy", "sell", "hold"):
        # 판단을 못 받았습니다. 모르는 상태에서 사지 않습니다.
        return "hold", "Codex 판단을 받지 못해 이번에는 아무것도 하지 않습니다"
    if not reason:
        reason = "Codex가 이유를 적지 않았습니다"

    if m["held"]:
        return ("sell", reason) if decision == "sell" else ("hold", reason)
    return ("buy", reason) if decision == "buy" else ("hold", reason)


def why_not_buy(m):
    """Codex에게 묻기도 전에 거를 이유. 없으면 None.

    잘 고르는 것보다 안 사도 될 것을 거르는 쪽이 손실을 줄입니다.
    """
    floor = MIN_TURNOVER_USD if m["currency"] == "USD" else MIN_TURNOVER_KRW
    turnover = m.get("turnover")
    if turnover is None or turnover <= 0:
        return "거래대금을 확인하지 못해 이번에는 사지 않습니다"
    if turnover < floor:
        return f"거래가 너무 적어 팔 때 곤란할 수 있습니다 (거래대금 {money(turnover, m)})"

    high = m.get("high_52w")
    if not QUANT_ENABLED and high and m["price"] >= high * MAX_NEAR_HIGH_PCT / 100:
        return f"52주 최고 {money(high, m)}에 가까워 지금은 사지 않습니다"
    return None


def facts(m):
    """Codex에게 더 보여 줄 사실. 여기서 돌려준 줄이 --scan 출력에 그대로 붙습니다.

    지표를 넘기지 않으면 "흐름이 좋아 보인다" 같은 두루뭉술한 이유만 돌아옵니다.
    숫자를 주면 "5일 평균 71,200원을 넘었고 RSI 58" 처럼 확인할 수 있는 말이 됩니다.
    자료가 모자란 지표는 아예 적지 않습니다. 0으로 적으면 값이 0인 줄 압니다.
    """
    closes = [float(c) for c in (m.get("closes") or [])]
    lines = []
    for days in (5, 20, 60):
        avg = moving_average(closes, days)
        if avg is not None:
            lines.append(f"- {days}일 이동평균: {money(avg, m)}")
    strength = rsi(closes)
    if strength is not None:
        lines.append(f"- RSI(14): {strength} (70 위 과열 · 30 아래 과매도)")
    line, signal = macd(closes)
    if line is not None:
        # 차이의 부호가 곧 신호입니다. 양수면 신호선 위, 음수면 아래.
        lines.append(f"- MACD: {line:,.1f} · 신호선 {signal:,.1f} · 차이 {line - signal:+,.1f}")
    return lines


def moving_average(closes, days):
    """최근 종가 평균. 자료가 모자라면 None."""
    if len(closes) < days:
        return None
    return sum(closes[-days:]) / days


def rsi(closes, days=14):
    """상대강도지수 0~100. 오른 폭과 내린 폭의 비율입니다."""
    if len(closes) < days + 1:
        return None
    changes = [b - a for a, b in zip(closes[-days - 1 : -1], closes[-days:], strict=False)]
    up = sum(c for c in changes if c > 0) / days
    down = -sum(c for c in changes if c < 0) / days
    if not down:
        return 100.0 if up else 50.0
    return round(100 - 100 / (1 + up / down), 1)


def _ema(closes, days):
    """지수이동평균 줄. 처음 days개의 평균에서 출발합니다."""
    if len(closes) < days:
        return []
    weight = 2 / (days + 1)
    out = [sum(closes[:days]) / days]
    for value in closes[days:]:
        out.append(value * weight + out[-1] * (1 - weight))
    return out


def macd(closes, fast=12, slow=26, smooth=9):
    """(MACD, 신호선). 신호선을 위로 뚫으면 상승 전환으로 봅니다."""
    quick, slack = _ema(closes, fast), _ema(closes, slow)
    if not quick or not slack:
        return None, None
    length = min(len(quick), len(slack))
    line = [q - s for q, s in zip(quick[-length:], slack[-length:], strict=False)]
    signal = _ema(line, smooth)
    if not signal:
        return None, None
    return round(line[-1], 2), round(signal[-1], 2)


def money(value, m):
    """국내는 원, 미국은 달러로 적습니다. 이유 문구에 그대로 쓰입니다."""
    if m.get("currency") == "USD":
        return f"${value:,.2f}"
    return f"{value:,.0f}원"
