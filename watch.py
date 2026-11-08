"""삿포로 숙소 빈방 감시 (1회 실행 = 1회 확인).

GitHub Actions가 20분마다 실행한다. 대상과 기준은 hotels.json에 있다.
가격 비교는 네이버호텔의 세금 포함 가격(여러 플랫폼)에 현지 숙박세와 입욕세를 더한 총액으로 한다.
기준가 미만인 객실이 새로 나오거나 더 싸지면 텔레그램으로 알린다.
"""
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from notify import send

BASE = Path(__file__).parent
CONFIG = json.loads((BASE / "hotels.json").read_text(encoding="utf-8"))
STATE_PATH = BASE / "state.json"
KST = timezone(timedelta(hours=9))

OTA_NAMES = {
    "NBDC": "부킹닷컴", "NAGD": "아고다", "KYK": "호텔스컴바인", "NTTP": "시크릿몰",
    "NBOK": "네이버예약", "PKG": "호텔패키지", "TBZ": "트립비토즈", "CTE": "트립닷컴",
    "NCTE": "트립닷컴", "NGDC": "여기어때", "ABB": "에어비앤비",
}

NAVER_GQL = "https://hermes-hotel-svc-api.naver.com/graphql"
NAVER_QUERY = """query overseasHotelRates($adultCnt:Int!,$checkIn:String!,$checkOut:String!,$id:String!,$includeTax:Boolean!,$impArea:SingleImpArea!){
 overseasHotelRates(adultCnt:$adultCnt,checkIn:$checkIn,checkOut:$checkOut,id:$id,includeTax:$includeTax,impArea:$impArea){
  totalCount rates{ roomName ota{code isOfficialSite} productFeatures{isFreeCancel freeBreakfast freeDinner}
   rate{userRate localRate{currencyCode rate}} } } }"""

FALLBACK_KRW_PER_JPY = 9.5  # 네이버 응답에 엔화 금액이 없을 때만 쓴다


def lodging_tax_jpy(area, per_person_night_jpy):
    """1인 1박 숙박세(엔). 2026-04-01 시행. 홋카이도 도세 + 삿포로 시세."""
    if per_person_night_jpy < 20000:
        do_tax, city_tax = 100, 200
    elif per_person_night_jpy < 50000:
        do_tax, city_tax = 200, 200
    else:
        do_tax, city_tax = 500, 500
    return do_tax + (city_tax if area == "sapporo" else 0)


def naver_rates(hotel_id):
    body = json.dumps({
        "operationName": "overseasHotelRates",
        "query": NAVER_QUERY,
        "variables": {
            "adultCnt": CONFIG["adults"], "checkIn": CONFIG["check_in"], "checkOut": CONFIG["check_out"],
            "id": hotel_id, "includeTax": True, "impArea": "renewalSingle",
        },
    }).encode()
    req = urllib.request.Request(NAVER_GQL, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=40) as r:
        data = json.loads(r.read())
    return ((data.get("data") or {}).get("overseasHotelRates") or {}).get("rates") or []


def naver_link(hotel_id):
    return (f"https://hotels.naver.com/accommodation/search/detail/overseas/{hotel_id}/rates"
            f"?dCheckIn={CONFIG['check_in']}&dCheckOut={CONFIG['check_out']}&dAdultCnt={CONFIG['adults']}")


def offers_for(hotel):
    """객실 후보 목록을 세금 포함 총액 오름차순으로 돌려준다."""
    if not hotel.get("naver_id"):
        return []
    nights = (date.fromisoformat(CONFIG["check_out"]) - date.fromisoformat(CONFIG["check_in"])).days
    people = CONFIG["adults"]
    out = []
    for r in naver_rates(hotel["naver_id"]):
        krw = (r.get("rate") or {}).get("userRate")
        if not krw:
            continue
        krw = float(krw)  # API가 문자열로 줄 때가 있다
        local = (r["rate"].get("localRate") or {})
        jpy = float(local["rate"]) if local.get("currencyCode") == "JPY" and local.get("rate") else None
        krw_per_jpy = (krw / jpy) if jpy else FALLBACK_KRW_PER_JPY
        room_jpy = jpy or krw / FALLBACK_KRW_PER_JPY
        ppn = room_jpy / nights / people
        local_tax_jpy = (lodging_tax_jpy(hotel["tax_area"], ppn) + hotel["onsen_tax_jpy"]) * nights * people
        total = round(krw + local_tax_jpy * krw_per_jpy)
        pf = r.get("productFeatures") or {}
        meals = "조석식" if pf.get("freeBreakfast") and pf.get("freeDinner") else \
                "조식" if pf.get("freeBreakfast") else "석식" if pf.get("freeDinner") else "식사 없음"
        out.append({
            "ota": OTA_NAMES.get(r["ota"]["code"], r["ota"]["code"]),
            "room": (r.get("roomName") or "").strip(),
            "base_krw": round(krw), "local_tax_krw": round(local_tax_jpy * krw_per_jpy), "total_krw": total,
            "meals": meals, "free_cancel": bool(pf.get("isFreeCancel")),
        })
    return sorted(out, key=lambda o: o["total_krw"])


def fmt_krw(v):
    return f"{v / 10000:,.1f}만 원"


def build_message(hotel, best, n_under):
    return "\n".join([
        f"[장소] {hotel['place']}",
        f"[위치] {hotel['location']}",
        f"[숙소명] {hotel['name']}",
        f"  └ {best['ota']} · {best['room']} ({best['meals']}{', 무료취소' if best['free_cancel'] else ''})",
        f"  └ 총 {fmt_krw(best['total_krw'])} = 요금 {fmt_krw(best['base_krw'])}"
        f" + 숙박세·입욕세 {fmt_krw(best['local_tax_krw'])}",
        f"  └ 기준가 미만 객실 {n_under}개 · {CONFIG['check_in']}~{CONFIG['check_out']} 성인 {CONFIG['adults']}명",
        f"[링크] {naver_link(hotel['naver_id'])}",
    ])


def main():
    now = datetime.now(KST)
    if now.date() >= date.fromisoformat(CONFIG["stop_after"]):
        print("감시 기간 종료")
        return 0
    state = json.loads(STATE_PATH.read_text(encoding="utf-8")) if STATE_PATH.exists() else {}
    threshold = CONFIG["threshold_krw"]
    dry = "--dry" in sys.argv
    errors = []
    for hotel in CONFIG["hotels"]:
        key = hotel["key"]
        prev = state.get(key, {})
        try:
            offers = offers_for(hotel)
        except (urllib.error.URLError, TimeoutError, ValueError, KeyError) as e:
            errors.append(f"{hotel['name']}: {e}")
            continue
        under = [o for o in offers if o["total_krw"] < threshold]
        cheapest = offers[0]["total_krw"] if offers else None
        print(f"{now:%m-%d %H:%M} {key}: 객실 {len(offers)}개, 최저 {cheapest}, 기준 미만 {len(under)}개")

        if under:
            best = under[0]
            last = prev.get("alerted_total")
            # 새로 풀렸거나, 지난 알림보다 3% 이상 싸졌을 때만 알린다
            if last is None or best["total_krw"] < last * 0.97:
                msg = ("🟢 빈방 발견!\n" if last is None else "🔻 더 싸졌어요\n") + build_message(hotel, best, len(under))
                print(msg)
                if not dry:
                    send(msg, disable_preview=True)
                prev["alerted_total"] = best["total_krw"]
                prev["first_seen"] = prev.get("first_seen") or now.isoformat(timespec="minutes")
        elif prev.get("alerted_total") is not None:
            msg = f"⚪ 다시 마감\n[숙소명] {hotel['name']}\n기준가 미만 객실이 사라졌어요."
            print(msg)
            if not dry:
                send(msg, disable_preview=True)
            prev = {}
        prev["last_check"] = now.isoformat(timespec="minutes")
        prev["cheapest"] = cheapest
        state[key] = prev

    # 수동 실행(workflow_dispatch) 때는 현재 상태 요약을 보내 동작을 확인시킨다
    if os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch" and not dry:
        lines = [f"📋 감시 상태 ({now:%m-%d %H:%M} KST)",
                 f"{CONFIG['check_in']}~{CONFIG['check_out']} 성인 {CONFIG['adults']}명 · 기준 {fmt_krw(threshold)} 미만 · 20분 간격"]
        for h in CONFIG["hotels"]:
            c = state.get(h["key"], {}).get("cheapest")
            lines.append(f"- {h['name'].split(' (')[0]}: " + (f"최저 {fmt_krw(c)}" if c else "매진"))
        lines += [f"- 조회 실패: {e}" for e in errors]
        send("\n".join(lines), disable_preview=True)

    if not dry:
        STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    for e in errors:
        print("조회 실패:", e)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
