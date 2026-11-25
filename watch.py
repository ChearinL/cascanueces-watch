#!/usr/bin/env python3
"""
Entradas BA - El Cascanueces 12/26 20:00, 12/27 17:00 품절 해제 감시기

- 페이지가 Blazor(JS 렌더링)라서 Playwright(headless Chromium)로 열어서 날짜 카드를 읽는다.
- 12/26, 12/27 카드가 새로 예매 가능해지면 알림을 보낸다(계속 가능한 상태면 다시 알리지 않음, state.json).
- 알림: macOS 알림 + 소리, (선택) ntfy.sh 로 폰 푸시.
- 읽기만 한다. 로그인/결제는 하지 않는다.

설치(한 번만):
    pip3 install playwright
    python3 -m playwright install chromium

실행:
    python3 cascanueces_watch.py              # 계속 돌면서 감시 (기본 3분 간격)
    python3 cascanueces_watch.py --once       # 한 번만 확인(테스트용)
    python3 cascanueces_watch.py --test-notify  # 알림이 오는지만 테스트
"""
import argparse
import json
import os
import random
import subprocess
import sys
import time
import urllib.request
from datetime import datetime

URL = "https://entradasba.buenosaires.gob.ar/evento/335449d8-79db-4f40-8580-28e0aab4311d"
TARGET_DAYS = ["26", "27"]  # 감시할 날짜(일)
STATE_FILE = "state.json"   # 직전 상태 저장(새로 풀렸을 때만 알림)
TARGET_MONTH = "DICIEMBRE" # 감시할 달(스페인어, 페이지 표기 그대로)
INTERVAL_SEC = 180         # 확인 간격(초). 너무 짧게 하지 마세요(서버 부담/차단 방지).
JITTER_SEC = 30            # 간격에 랜덤으로 더할 최대 초

# 폰 푸시를 원하면: ntfy 앱 설치 -> 아래에 추측하기 어려운 토픽명 입력 -> 앱에서 같은 토픽 구독
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")  # GitHub Secret 또는 환경변수로 설정


def log(msg):
    print(f"[{datetime.now():%m-%d %H:%M:%S}] {msg}", flush=True)


def notify(title, message):
    log(f"ALERT: {title} - {message}")
    # macOS 알림 (맥에서만)
    try:
        if sys.platform != "darwin":
            raise RuntimeError("not macOS, skipped")
        script = f'display notification "{message}" with title "{title}" sound name "Glass"'
        subprocess.run(["osascript", "-e", script], check=False)
        subprocess.run(["say", "Cascanueces tickets available"], check=False)
    except Exception as e:  # noqa: BLE001
        log(f"macOS notify failed: {e}")
    # ntfy 푸시(선택)
    if NTFY_TOPIC:
        try:
            req = urllib.request.Request(
                f"https://ntfy.sh/{NTFY_TOPIC}",
                data=message.encode("utf-8"),
                headers={"Title": title.encode("utf-8").decode("latin-1", "ignore"),
                         "Priority": "urgent", "Click": URL, "Tags": "ticket"},
            )
            urllib.request.urlopen(req, timeout=10)
        except Exception as e:  # noqa: BLE001
            log(f"ntfy failed: {e}")


def read_cards(page):
    return page.evaluate(
        """() => [...document.querySelectorAll('.fecha-card')].map(c => ({
              day: (c.querySelector('.dia-numero')||{}).textContent?.trim(),
              month: (c.querySelector('.dia-nombre')||{}).textContent?.trim(),
              time: (c.querySelector('.horario-unico')||{}).textContent?.trim(),
              soldOut: c.classList.contains('no-disponible') || !!c.querySelector('.agotado-label'),
            }))"""
    )


def check_once(pw):
    """반환: {일: 'AVAILABLE' | 'SOLD_OUT' | 'NOT_FOUND'} (예외는 호출자가 처리)"""
    browser = pw.chromium.launch(headless=True)
    try:
        page = browser.new_page(locale="es-AR")
        page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector(".fecha-card", timeout=45000)
        page.wait_for_timeout(1500)

        seen = {}
        for _ in range(12):  # 페이지 넘김 최대 12번
            for c in read_cards(page):
                seen[(c["day"], c["month"])] = c
            nxt = page.locator("button.nav-btn-gris").last
            if nxt.count() == 0 or nxt.is_disabled():
                break
            nxt.click()
            page.wait_for_timeout(700)

        log("dates seen: " + ", ".join(f"{d}/{m[:3]}{'(X)' if c['soldOut'] else '(OK)'}" for (d, m), c in seen.items()))
        result = {}
        for day in TARGET_DAYS:
            card = seen.get((day, TARGET_MONTH))
            if card is None:
                result[day] = "NOT_FOUND"
            else:
                result[day] = "SOLD_OUT" if card["soldOut"] else "AVAILABLE"
        return result
    finally:
        browser.close()


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--test-notify", action="store_true")
    ap.add_argument("--max-minutes", type=float, help="이 시간(분)이 지나면 정상 종료(GitHub Actions용)")
    ap.add_argument("--interval", type=float, help="확인 간격(초), 기본 180")
    ap.add_argument("--url", help="다른 공연 페이지로 테스트할 때")
    ap.add_argument("--days", help="감시할 일, 쉼표로 구분(예: 26,27)")
    ap.add_argument("--month", help="감시할 달, 페이지 표기 그대로(예: DICIEMBRE)")
    args = ap.parse_args()
    global URL, TARGET_DAYS, TARGET_MONTH, INTERVAL_SEC
    if args.interval:
        INTERVAL_SEC = args.interval
    URL = args.url or URL
    TARGET_DAYS = args.days.split(",") if args.days else TARGET_DAYS
    TARGET_MONTH = (args.month or TARGET_MONTH).upper()

    if args.test_notify:
        notify("테스트", "알림이 정상적으로 도착했습니다")
        return

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("playwright 가 없습니다: pip3 install playwright && python3 -m playwright install chromium")

    fails = 0
    deadline = time.time() + args.max_minutes * 60 if args.max_minutes else None
    not_found = 0
    state = load_state()
    with sync_playwright() as pw:
        while True:
            try:
                results = check_once(pw)
                fails = 0
                released = []
                for day, status in results.items():
                    log(f"{day}/{TARGET_MONTH}: {status}")
                    if status == "NOT_FOUND":
                        continue  # 직전 상태 유지
                    if status == "AVAILABLE" and state.get(day) != "AVAILABLE":
                        released.append(day)
                    state[day] = status
                save_state(state)
                if released:
                    names = ", ".join(f"12/{d}" for d in released)
                    notify(f"Cascanueces {names} 티켓 풀림!", f"{names} 지금 확인하세요: {URL}")
                    log("알림을 보냈습니다. 다음 실행이 감시를 이어갑니다.")
                    sys.exit(10)
                if all(s == "NOT_FOUND" for s in results.values()):
                    log("대상 날짜 카드를 찾지 못했습니다(페이지 구조 변경/일정 삭제 가능성).")
                    not_found += 1
                    if args.once or not_found >= 3:
                        sys.exit(2)
                else:
                    not_found = 0
            except Exception as e:  # noqa: BLE001
                fails += 1
                log(f"확인 실패({fails}회): {e}")
                if args.once:
                    sys.exit(3)
                if fails == 5:
                    notify("감시기 확인 실패", "5번 연속 실패. 인터넷/사이트 상태를 확인하세요.")
            if args.once:
                return
            if deadline and time.time() + INTERVAL_SEC > deadline:
                log("정해진 시간이 끝나 정상 종료합니다(다음 실행이 이어받습니다).")
                return
            time.sleep(INTERVAL_SEC + random.uniform(0, JITTER_SEC))


if __name__ == "__main__":
    main()
