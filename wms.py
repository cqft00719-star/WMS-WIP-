import os
import re
import json
import base64
import traceback
import io
import glob
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
import requests
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

EVOLUTION_URL = "https://evolution-api-production-5d91de27.up.railway.app"
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY", "")
INSTANCE = "my WhatsApps"
BOT_URL = "https://cookie-delivery-bot-production.up.railway.app"
GCP_JSON = os.getenv("GCP_JSON", "")
MY_WHATSAPP = os.getenv("MY_WHATSAPP", "")
# ===== 已改：真 VIP Group ID，唔係舊嗰個 120363413020795341 =====
TMS_GROUP_ID = os.getenv("TMS_GROUP_ID", "") or "120363410834827562@g.us"
COOKIE_GROUP_ID = os.getenv("COOKIE_GROUP_ID", "") or "85264352282-1512209129@g.us"
TMS_SHEET_ID = os.getenv("TMS_SHEET_ID", "") or "15X06a1oOFeta-ETz-CiLbB-5TUWS1rWKGi35HmBNNVE"
# OCR 已刪除，曲奇相唔再留、唔再回覆
HKT = timezone(timedelta(hours=8))
def now_hk():
    return datetime.now(HKT)

SHOPS = ["TSTN", "TSTG", "TST", "MK", "MPMK", "MOKO", "CENT", "HSP", "CPE", "CWB", "CYW", "AT1", "AT2", "ATRA", "APM", "KLN", "TM", "SH"]
FEES = {"TSTN":200,"TSTG":200,"TST":200,"MK":200,"MPMK":200,"MOKO":200,"CENT":380,"HSP":380,"CPE":380,"CWB":380,"CYW":380,"AT1":500,"AT2":500,"ATRA":500,"APM":200,"KLN":200,"TM":250,"SH":250}
SCAN_PER_BOX = 5
def scan_cost_for(shop, boxes):
    if shop not in SHOPS:
        return 0
    mc = FEES.get(shop, 0)
    included = mc // SCAN_PER_BOX
    if boxes <= included:
        return mc
    return mc + (boxes - included) * SCAN_PER_BOX

app = FastAPI()
logs = []
cookie_photos = []
pod_photos = []
jobs_snapshot = {}
last_webhook = {}
_seen_msg_ids = set()
COOKIE_DIR = "/tmp/cookie_photos"
os.makedirs(COOKIE_DIR, exist_ok=True)

def log(m):
    print(m, flush=True)
    logs.append(str(m))
    if len(logs) > 200:
        logs.pop(0)

def persist_cookie_photo(img_bytes, mid):
    return ""
def load_persisted_photos():
    return

def creds():
    from google.oauth2.service_account import Credentials
    s = GCP_JSON.strip()
    if not s.startswith("{"):
        s = base64.b64decode(s).decode()
    info = json.loads(s)
    sc = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    return Credentials.from_service_account_info(info, scopes=sc)
def sheet():
    import gspread
    return gspread.authorize(creds())

def send_text(n, t):
    try:
        url = EVOLUTION_URL + "/message/sendText/" + quote(INSTANCE)
        head = {"apikey": EVOLUTION_API_KEY}
        r = requests.post(url, json={"number": n, "text": t}, headers=head, timeout=20)
        log("send_text to " + str(n) + " " + str(r.status_code) + " " + r.text[:200])
        return r.status_code in [200, 201]
    except Exception as e:
        log("send_text fail " + str(e))
        return False
def send_xls(n, b, fn, cap):
    try:
        url = EVOLUTION_URL + "/message/sendMedia/" + quote(INSTANCE)
        head = {"apikey": EVOLUTION_API_KEY}
        b64 = base64.b64encode(b).decode()
        payload = {"number": n, "mediatype": "document", "mimetype": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "caption": cap, "media": b64, "fileName": fn}
        r = requests.post(url, json=payload, headers=head, timeout=60)
        log("send_xls to " + str(n) + " " + str(r.status_code) + " " + r.text[:200])
        return r.status_code in [200, 201]
    except Exception as e:
        log(str(e))
        return False
def dl_img(data):
    try:
        url = EVOLUTION_URL + "/chat/getBase64FromMediaMessage/" + quote(INSTANCE)
        key = data.get("key", {})
        msg = data.get("message", {})
        head = {"apikey": EVOLUTION_API_KEY}
        for payload in [
            {"message": {"key": key, "message": msg}},
            {"key": key, "message": msg},
            {"message": msg, "key": key},
        ]:
            try:
                r = requests.post(url, json=payload, headers=head, timeout=60)
                log("dl_img status " + str(r.status_code))
                j = r.json()
                b64 = j.get("base64") or ""
                if not b64:
                    d2 = j.get("data", {})
                    if isinstance(d2, dict):
                        b64 = d2.get("base64", "") or d2.get("media", "")
                if b64:
                    if "," in b64:
                        b64 = b64.split(",", 1)[1]
                    return base64.b64decode(b64)
            except Exception as e:
                log(f"dl_img try fail {e}")
                continue
        log("dl_img no base64")
        return None
    except Exception as e:
        log("dl_img fail " + str(e))
        return None

def _is_cookie_group(jid):
    j = str(jid)
    if j == COOKIE_GROUP_ID: return True
    if "85264352282" in j or "1512209129" in j: return True
    return False

@app.post("/webhook")
async def webhook(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        last_webhook["time"] = now_hk().strftime("%H:%M:%S")
        last_webhook["event"] = str(body.get("event", ""))
        last_webhook["body_keys"] = str(list(body.keys())[:10])
        ev = str(body.get("event", ""))
        log("WEBHOOK hit event=" + ev + " keys=" + str(list(body.keys())[:10]))
        data = body.get("data") or body
        if isinstance(data, dict) and "key" not in data and "message" in body:
            data = body
        key = data.get("key", {}) if isinstance(data, dict) else {}
        jid = key.get("remoteJid") or key.get("remoteJidAlt") or ""
        if not jid and isinstance(data, dict):
            jid = data.get("remoteJid") or ""
        last_webhook["jid"] = jid
        log("WEBHOOK jid=" + str(jid) + " expect_cookie=" + COOKIE_GROUP_ID + " tms=" + TMS_GROUP_ID + " fromMe=" + str(key.get("fromMe", False)))
        if ev and "upsert" not in ev.lower():
            return {"ok": True}
        msg = data.get("message", {}) if isinstance(data, dict) else {}
        inner = msg.get("viewOnceMessage", {}).get("message", {}) if msg.get("viewOnceMessage") else msg
        if msg.get("ephemeralMessage"):
            inner = msg.get("ephemeralMessage", {}).get("message", msg)
        cap = ""
        try:
            if inner.get("imageMessage"): cap = inner.get("imageMessage", {}).get("caption", "") or ""
            if inner.get("extendedTextMessage"): cap += " " + inner.get("extendedTextMessage", {}).get("text", "")
            if inner.get("conversation"): cap += " " + inner.get("conversation", "")
            if msg.get("imageMessage"): cap += " " + msg.get("imageMessage", {}).get("caption", "")
        except: cap = ""
        log("WEBHOOK caption=" + cap[:200])
        has_img = bool(inner.get("imageMessage") or inner.get("extendedTextMessage", {}).get("contextInfo", {}).get("quotedMessage", {}).get("imageMessage") or msg.get("imageMessage"))
        if not has_img:
            log("WEBHOOK no image, skip")
            return {"ok": True}
        mid = key.get("id", "") or ""
        if mid:
            if mid in _seen_msg_ids:
                log("WEBHOOK duplicate msg id, skip " + mid[:20])
                return {"ok": True}
            _seen_msg_ids.add(mid)
            if len(_seen_msg_ids) > 500: _seen_msg_ids.clear()
        is_dm = ("@g.us" not in str(jid))
        if is_dm:
            log("DM photo ignored - NO reply, NO save, NO OCR")
            return {"ok": True}
        if _is_cookie_group(jid):
            log("COOKIE photo ignored - NO save, NO reply, NO OCR")
            return {"ok": True}
        if TMS_GROUP_ID and jid == TMS_GROUP_ID:
            img = dl_img(data)
            if not img:
                log("WEBHOOK dl_img failed pod")
                return {"ok": True}
            mjob = re.search(r"JOB-[A-Z0-9\-]+", cap.upper())
            job = mjob.group(0) if mjob else ""
            pod_photos.append({"img": img, "jid": jid, "job": job, "caption": cap})
            if MY_WHATSAPP:
                if job: send_text(MY_WHATSAPP, "POD 收到 " + job + "\n喺 運輸 (VIP單) Group 收到 POD 相，Caption: " + cap[:100])
                else: send_text(MY_WHATSAPP, "POD 收到 (未寫單號)\n喺 運輸 (VIP單) Group 收到 POD 相，Caption: " + cap[:100] + "\n記得叫司機打埋 JOB 單號。")
            return {"ok": True}
        log("WEBHOOK not cookie / tms group, skip. got=" + str(jid))
        return {"ok": True}
    except Exception as e:
        log("WEBHOOK ERR " + str(e))
        log(traceback.format_exc())
        return JSONResponse({"ok": False}, status_code=200)

def tms_daily_xls():
    import openpyxl
    ws = sheet().open_by_key(TMS_SHEET_ID).worksheet("Jobs")
    vals = ws.get_all_values()
    if len(vals) < 2: return None, "Jobs 冇資料"
    headers = [h.strip() for h in vals[0]]
    date_idx = None
    for c in ["Date", "送貨日期"]:
        if c in headers: date_idx = headers.index(c); break
    if date_idx is None: return None, "Jobs 冇 Date 欄"
    tomorrow = (now_hk() + timedelta(days=1)).date()
    day_after = tomorrow + timedelta(days=1)
    filtered = []
    for r in vals[1:]:
        d = None
        try:
            txt = r[date_idx].split(" ")[0]
            d = datetime.strptime(txt, "%Y-%m-%d").date()
        except:
            try:
                txt = r[date_idx].split(" ")[0]
                d = datetime.strptime(txt, "%d-%m-%Y").date()
            except: continue
        if d in [tomorrow, day_after]: filtered.append(r)
    if not filtered:
        return None, str(tomorrow) + "~" + str(day_after) + " 冇單"
    wb = openpyxl.Workbook(); w2 = wb.active; w2.title = "TMS Orders"
    want = ["InternalNo","JobID","Date","Client","Zone","From","To","CargoDetail","DeliveryTime","Notes","Status"]
    idxs = [i for i, h in enumerate(headers) if h in want]
    w2.append([headers[i] for i in idxs])
    for r in filtered:
        w2.append([r[i] if i < len(r) else "" for i in idxs])
    bio = io.BytesIO(); wb.save(bio); bio.seek(0)
    return bio.getvalue(), str(tomorrow) + " to " + str(day_after) + " 共 " + str(len(filtered)) + " 單"

def parse_created(s):
    try:
        s = str(s).strip()
        if not s: return None
        s = s.replace(" HKT", "")
        for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"]:
            try: return datetime.strptime(s, fmt)
            except: pass
        return None
    except: return None

def check_jobs():
    try:
        if not TMS_GROUP_ID: return
        ws = sheet().open_by_key(TMS_SHEET_ID).worksheet("Jobs")
        vals = ws.get_all_values()
        if len(vals) < 2: return
        headers = [h.strip() for h in vals[0]]
        id_idx = 0
        for c in ["InternalNo","JobID","單號"]:
            if c in headers: id_idx = headers.index(c); break
        st_idx = next((headers.index(c) for c in ["Status","狀態"] if c in headers), None)
        ca_idx = next((headers.index(c) for c in ["CreatedAt_HKT","CreatedAt","建立時間"] if c in headers), None)
        is_first = len(jobs_snapshot) == 0
        for r in vals[1:]:
            if not r or len(r) <= id_idx or not r[id_idx].strip(): continue
            jid = r[id_idx].strip()
            st = r[st_idx] if st_idx is not None and st_idx < len(r) else ""
            line = " | ".join([x for x in r if x.strip()])
            if jid not in jobs_snapshot:
                jobs_snapshot[jid] = st
                if is_first:
                    if st.strip().upper() in ["DELIVERED","COMPLETED","CANCELLED","CANCEL"]: continue
                    ca = parse_created(r[ca_idx]) if ca_idx is not None and ca_idx < len(r) else None
                    if ca is None: continue
                    if (now_hk().replace(tzinfo=None) - ca).total_seconds() > 900: continue
                    send_text(TMS_GROUP_ID, "TMS 新單\n" + line)
                else:
                    send_text(TMS_GROUP_ID, "TMS 新單\n" + line)
            elif jobs_snapshot[jid] != st:
                jobs_snapshot[jid] = st
                if is_first: continue
                send_text(TMS_GROUP_ID, "TMS 狀態變更\n" + line)
    except Exception as e:
        log("check_jobs fail " + str(e))

def write_daily_log(status):
    try:
        sh = sheet().open_by_key(TMS_SHEET_ID)
        try: ws = sh.worksheet("DailyLog")
        except:
            ws = sh.add_worksheet(title="DailyLog", rows=1000, cols=10)
            ws.update('A1', [["Date","Time_HKT","Status","Detail","TriggeredBy"]])
        ws.append_row([now_hk().strftime("%Y-%m-%d"), now_hk().strftime("%H:%M:%S"), status, str(now_hk()), "app.py-auto"], value_input_option='USER_ENTERED')
    except Exception as e:
        log("DailyLog write fail " + str(e))

def already_sent_today():
    try:
        sh = sheet().open_by_key(TMS_SHEET_ID)
        ws = sh.worksheet("DailyLog")
        vals = ws.get_all_values()
        today_s = now_hk().strftime("%Y-%m-%d")
        for r in reversed(vals[1:]):
            if r and len(r) >= 3 and r[0].strip() == today_s and "17:00" in str(r[2]):
                return True
        return False
    except Exception as e:
        log("already_sent_today fail " + str(e))
        return False

def send17():
    log(f"send17 trigger at {now_hk().strftime('%Y-%m-%d %H:%M:%S HKT')}")
    if not TMS_GROUP_ID:
        if MY_WHATSAPP: send_text(MY_WHATSAPP, "17:00 TMS 每日提醒失敗：Railway 入面 TMS_GROUP_ID 未設定")
        return
    if already_sent_today():
        log("send17 already sent today, skip"); return
    b, info = tms_daily_xls()
    if not b:
        send_text(TMS_GROUP_ID, "TMS每日提醒 " + now_hk().strftime("%Y-%m-%d") + " " + info)
    else:
        fn = "TMS_" + now_hk().strftime("%Y-%m-%d") + ".xlsx"
        send_xls(TMS_GROUP_ID, b, fn, "TMS每日提醒 " + now_hk().strftime("%Y-%m-%d %H:%M") + "\n" + info)
    write_daily_log("17:00 OK " + str(info)[:100])

def send22():
    log("send22 disabled - NO OCR, NO save, NO reply")
    return

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
sc = BackgroundScheduler(timezone="Asia/Hong_Kong")
sc.add_job(send17, CronTrigger(hour=17, minute=0, timezone="Asia/Hong_Kong"))
# 已停用 22:00
sc.add_job(check_jobs, CronTrigger(minute="*/5", timezone="Asia/Hong_Kong"))
sc.start()

@app.get("/")
def root():
    return {"status": "running", "ocr": "disabled", "cookie_photo_save": "disabled", "dm_reply": "disabled", "time_hkt": now_hk().strftime("%Y-%m-%d %H:%M:%S HKT"), "cookie_photos": len(cookie_photos), "pod_photos": len(pod_photos), "cookie_group_expected": COOKIE_GROUP_ID, "last_webhook": last_webhook, "tms_group": TMS_GROUP_ID, "my_whatsapp": bool(MY_WHATSAPP), "jobs_seen": len(jobs_snapshot), "logs": logs[-30:]}

@app.get("/fix-webhook")
def fix():
    url = EVOLUTION_URL + "/webhook/set/" + quote(INSTANCE)
    head = {"apikey": EVOLUTION_API_KEY}
    payload = {"webhook": {"enabled": True, "url": BOT_URL + "/webhook", "webhookByEvents": False, "webhookBase64": False, "events": ["MESSAGES_UPSERT"]}}
    r = requests.post(url, json=payload, headers=head, timeout=20)
    log("fix-webhook " + str(r.status_code) + " " + r.text[:500])
    return {"ok": True, "status": r.status_code, "result": r.text[:800]}

@app.get("/check-webhook")
def checkwh():
    url = EVOLUTION_URL + "/webhook/find/" + quote(INSTANCE)
    r = requests.get(url, headers={"apikey": EVOLUTION_API_KEY}, timeout=20)
    return {"status": r.status_code, "result": r.text[:1500]}

@app.get("/test-22")
def test22():
    send22()
    return {"ok": True, "logs": logs[-20:]}
@app.get("/test-17")
def test17():
    if not TMS_GROUP_ID: return {"ok": False, "logs": ["no TMS_GROUP_ID"]}
    b, info = tms_daily_xls()
    if not b: send_text(TMS_GROUP_ID, "TMS每日提醒 " + now_hk().strftime("%Y-%m-%d") + " " + info + " (手動測試)")
    else: send_xls(TMS_GROUP_ID, b, "TMS_" + now_hk().strftime("%Y-%m-%d") + ".xlsx", "TMS每日提醒 " + now_hk().strftime("%Y-%m-%d %H:%M") + "\n" + info + " (手動測試)")
    return {"ok": True, "logs": logs[-10:]}
@app.get("/test-jobs")
def testjobs():
    check_jobs()
    return {"ok": True, "snapshot": jobs_snapshot, "logs": logs[-10:]}
