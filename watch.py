"""삿포로 숙소 빈방 감시 (1회 실행 = 1회 확인).

GitHub Actions가 20분마다 실행한다. 대상과 기준은 hotels.json에 있다.
가격 비교는 네이버호텔의 세금 포함 가격(여러 플랫폼)·공식 예약 요금에 현지 숙박세와 입욕세를 더한 총액으로 한다.
기준가 미만인 객실이 새로 나오거나 더 싸지면 텔레그램으로 알린다.
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from notify import send

BASE = Path(__file__).parent
CONFIG = json.loads((BASE / "hotels.json").read_text(encoding="utf-8"))
# 점검용: PROBE_DATES="2026-12-08,2026-12-10" 이면 그 날짜로 조회만 한다(--dry와 함께 사용)
if os.environ.get("PROBE_DATES"):
    CONFIG["check_in"], CONFIG["check_out"] = os.environ["PROBE_DATES"].split(",")
STATE_PATH = BASE / os.environ.get("STATE_FILE", "state.json")
RUNNER = os.environ.get("RUNNER_LABEL", "클라우드" if os.environ.get("GITHUB_ACTIONS") else "Mac")
FAIL_ALERT_AFTER = 3  # 연속 실패 3회(약 1시간)면 알림
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

FALLBACK_KRW_PER_JPY = 9.5  # 환율 조회도 실패했을 때만 쓴다
# 조회 경로. 클라우드는 네이버가 막혀(해외 IP면 500) SOURCES=official 로 돌린다.
SOURCES = set(os.environ.get("SOURCES", "naver,official").split(","))
DORMY_API = "https://dormy-hotels.com/reserve/api/plans"
_fx = None


def krw_per_jpy():
    """엔→원 환율. 실패하면 고정값."""
    global _fx
    if _fx is None:
        try:
            with urllib.request.urlopen("https://open.er-api.com/v6/latest/JPY", timeout=15) as r:
                _fx = float(json.loads(r.read())["rates"]["KRW"])
        except Exception:
            _fx = FALLBACK_KRW_PER_JPY
    return _fx


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


def dormy_params(keyword, page=None):
    q = {"keyword": keyword, "checkin": CONFIG["check_in"], "number_of_nights": nights_count(),
         "number_of_rooms": 1, "number_of_adults[]": CONFIG["adults"],
         "number_of_children_need_futons[]": 0, "number_of_children_no_need_futons[]": 0,
         "search_by_tag": "plan", "tags": "", "brands": "", "order_by": ""}
    if page:
        q.update(page=page, search_all="true")
    return urllib.parse.urlencode(q)


def nights_count():
    return (date.fromisoformat(CONFIG["check_out"]) - date.fromisoformat(CONFIG["check_in"])).days


def dormy_offers(hotel):
    """교리츠(Dormy) 공식 예약. 표시 요금은 1인 1박(소비세 포함), 숙박세·입욕세는 별도."""
    nights, people, fx = nights_count(), CONFIG["adults"], krw_per_jpy()
    link = "https://dormy-hotels.com/reserve/search-plan?" + dormy_params(hotel["dormy_keyword"])
    out, page = [], 1
    while page and page <= 5:
        with urllib.request.urlopen(f"{DORMY_API}?{dormy_params(hotel['dormy_keyword'], page)}", timeout=40) as r:
            data = json.loads(r.read())["data"]
        for item in data["data"]:
            for p in item["plan_stages"]:
                name = p.get("name") or ""
                if not p.get("price") or not p.get("bookable") or "市民限定" in name:
                    continue
                ppn = float(p["price"])
                tax = (lodging_tax_jpy(hotel["tax_area"], ppn) + hotel["onsen_tax_jpy"]) * nights * people
                base = ppn * nights * people
                meals = "조석식" if "2食" in name else "조식" if "朝食" in name else "식사 없음" if "素泊" in name else "-"
                out.append({
                    "ota": "공식(교리츠)", "room": name, "meals": meals, "free_cancel": False,
                    "base_krw": round(base * fx), "local_tax_krw": round(tax * fx),
                    "total_krw": round((base + tax) * fx), "link": link,
                    "note": f"1인 1박 {ppn:,.0f}엔 기준 추정",
                })
        page = data.get("next_page")
    return out


def offers_for(hotel):
    """조회 경로별로 객실 후보를 모아 세금 포함 총액 오름차순으로 돌려준다.
    한 경로가 실패해도 다른 경로 결과는 쓴다. 전부 실패하면 마지막 오류를 올린다."""
    out, errs, tried = [], [], 0
    for src, fn, need in (("naver", naver_offers, "naver_id"), ("official", dormy_offers, "dormy_keyword")):
        if src not in SOURCES or not hotel.get(need):
            continue
        tried += 1
        try:
            out += fn(hotel)
        except (urllib.error.URLError, TimeoutError, ValueError, KeyError) as e:
            errs.append(f"{src}: {e}")
    if tried and len(errs) == tried:
        raise ValueError("; ".join(errs))
    for e in errs:
        print(f"  {hotel['key']} 일부 경로 실패 - {e}")
    return sorted(out, key=lambda o: o["total_krw"]), tried


def naver_offers(hotel):
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
            "link": naver_link(hotel["naver_id"]),
        })
    return out


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
        *( [f"  └ {best['note']}"] if best.get("note") else [] ),
        f"  └ 기준가 미만 객실 {n_under}개 · {CONFIG['check_in']}~{CONFIG['check_out']} 성인 {CONFIG['adults']}명",
        f"[링크] {best['link']}",
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
            offers, tried = offers_for(hotel)
            if not tried:
                continue  # 이 실행 위치에서 볼 경로가 없는 숙소
        except (urllib.error.URLError, TimeoutError, ValueError, KeyError) as e:
            errors.append(f"{hotel['name']}: {e}")
            prev["fail_count"] = prev.get("fail_count", 0) + 1
            if prev["fail_count"] == FAIL_ALERT_AFTER and not dry:
                send(f"⚠️ 조회 실패 ({RUNNER})\n[숙소명] {hotel['name']}\n"
                     f"{FAIL_ALERT_AFTER}회 연속 조회 실패: {e}\n복구되면 다시 알려드릴게요.", disable_preview=True)
            state[key] = prev
            continue
        if prev.get("fail_count", 0) >= FAIL_ALERT_AFTER and not dry:
            send(f"✅ 조회 복구 ({RUNNER})\n[숙소명] {hotel['name']}", disable_preview=True)
        prev["fail_count"] = 0
        under = [o for o in offers if o["total_krw"] < threshold]
        cheapest = offers[0]["total_krw"] if offers else None
        print(f"{now:%m-%d %H:%M} {key}: 객실 {len(offers)}개, 최저 {cheapest}, 기준 미만 {len(under)}개")

        if under:
            best = under[0]
            last = prev.get("alerted_total")
            # 새로 풀렸거나, 지난 알림보다 3% 이상 싸졌을 때만 알린다
            if last is None or best["total_krw"] < last * 0.97:
                msg = (f"🟢 빈방 발견! ({RUNNER})\n" if last is None else f"🔻 더 싸졌어요 ({RUNNER})\n") + build_message(hotel, best, len(under))
                print(msg)
                if not dry:
                    send(msg, disable_preview=True)
                prev["alerted_total"] = best["total_krw"]
                prev["first_seen"] = prev.get("first_seen") or now.isoformat(timespec="minutes")
        elif prev.get("alerted_total") is not None:
            msg = f"⚪ 다시 마감 ({RUNNER})\n[숙소명] {hotel['name']}\n기준가 미만 객실이 사라졌어요."
            print(msg)
            if not dry:
                send(msg, disable_preview=True)
            prev = {"fail_count": 0}
        prev["last_check"] = now.isoformat(timespec="minutes")
        prev["cheapest"] = cheapest
        state[key] = prev

    # 수동 실행(workflow_dispatch) 때는 현재 상태 요약을 보내 동작을 확인시킨다
    if os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch" and not dry:
        lines = [f"📋 감시 상태 ({now:%m-%d %H:%M} KST)",
                 f"{CONFIG['check_in']}~{CONFIG['check_out']} 성인 {CONFIG['adults']}명 · 기준 {fmt_krw(threshold)} 미만 · 20분 간격"]
        for h in CONFIG["hotels"]:
            c = state.get(h["key"], {}).get("cheapest")
            here = ("naver" in SOURCES and h.get("naver_id")) or ("official" in SOURCES and h.get("dormy_keyword"))
            status = (f"최저 {fmt_krw(c)}" if c else "매진") if here else "이 위치에서는 조회 안 함(Mac 담당)"
            lines.append(f"- {h['name'].split(' (')[0]}: {status}")
        lines += [f"- 조회 실패: {e}" for e in errors]
        send("\n".join(lines), disable_preview=True)

    if not dry:
        STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    for e in errors:
        print("조회 실패:", e)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
