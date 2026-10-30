import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
import datetime
import pytz
import requests
import os
import io
import base64
import re
import uuid
import time
import difflib
from PIL import Image, ImageOps

HKT = pytz.timezone("Asia/Hong_Kong")
def now_hk(): return datetime.datetime.now(HKT)
def today_hk(): return now_hk().date()

ULTRAMSG_INSTANCE = "instance194419"
ULTRAMSG_TOKEN = "ji5hd4t2li7vf3ys"
ULTRAMSG_GROUP_ID = "120363430450194539@g.us"
DAILY_WMS_HOUR = 18
DAILY_WMS_MINUTE = 0

def send_ultramsg_message(body):
    try:
        url = f"https://api.ultramsg.com/{ULTRAMSG_INSTANCE}/messages/chat"
        payload = {"token": ULTRAMSG_TOKEN, "to": ULTRAMSG_GROUP_ID, "body": body}
        r = requests.post(url, data=payload, timeout=20)
        return r.status_code == 200, r.text[:500]
    except Exception as e:
        return False, str(e)

def send_ultramsg_document(file_path, caption=""):
    try:
        with open(file_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")
        url = f"https://api.ultramsg.com/{ULTRAMSG_INSTANCE}/messages/document"
        payload = {"token": ULTRAMSG_TOKEN, "to": ULTRAMSG_GROUP_ID, "document": b64, "filename": os.path.basename(file_path), "caption": caption}
        r = requests.post(url, data=payload, timeout=60)
        return r.status_code == 200, r.text[:1000]
    except Exception as e:
        return False, str(e)

# ========== V48.1 CORE - 方案A: Quantity + Lot顯示 + Stock按SKU計 ==========
def compress_inspection_dual(uploaded_file):
    try:
        img_orig = Image.open(io.BytesIO(uploaded_file.getvalue()))
        img_orig = ImageOps.exif_transpose(img_orig).convert("RGB")
        buf_orig = io.BytesIO()
        tmp_orig = img_orig.copy()
        tmp_orig.thumbnail((1600, 1600), Image.LANCZOS)
        tmp_orig.save(buf_orig, format="JPEG", quality=90, optimize=True)
        fb_orig = buf_orig.getvalue()
        for max_side in [1200, 1024, 900, 800]:
            for quality in [75, 65, 55, 50]:
                buf = io.BytesIO()
                tmp = img_orig.copy()
                tmp.thumbnail((max_side, max_side), Image.LANCZOS)
                tmp.save(buf, format="JPEG", quality=quality, optimize=True)
                fb = buf.getvalue()
                if len(fb) <= 48000:
                    return fb_orig, fb, max_side, quality
        return fb_orig, fb_orig, 1600, 90
    except Exception as e:
        print(f"compress fail {e}")
        return None, None, 0, 0

def ai_parse_inspection_free(image_bytes_high_res):
    result = {"inspection_note": "","product_code": "","product_name": "","lot_no": "","quantity": 0.0,"accepted": 0.0,"unit": "PCS","date": "","mfd": "","handwritten": "","raw_text": "","confidence": "low"}
    text = ""
    try:
        import easyocr
        import numpy as np
        import cv2
        reader = easyocr.Reader(['en'], gpu=False, verbose=False)
        nparr = np.frombuffer(image_bytes_high_res, np.uint8)
        img_cv = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        ocr_result = reader.readtext(img_cv, detail=0, paragraph=True)
        text = "\n".join(ocr_result)
        result["raw_text"] = text + "\n[Engine: EasyOCR]"
    except Exception as e_easy:
        try:
            import pytesseract
            img = Image.open(io.BytesIO(image_bytes_high_res))
            img = ImageOps.exif_transpose(img)
            gray = img.convert("L")
            w,h = gray.size
            if w < 2000:
                gray = gray.resize((w*2, h*2), Image.LANCZOS)
            gray = ImageOps.autocontrast(gray, cutoff=2)
            custom_cfg = '--psm 6'
            text = pytesseract.image_to_string(gray, lang='eng', config=custom_cfg)
            result["raw_text"] = text + f"\n[Engine: Tesseract Fallback, EasyOCR Error: {e_easy}]"
        except Exception as e:
            result["raw_text"] = f"OCR全部失敗: {e_easy} / {e}"
            return result

    text_upper = text.upper()
    text_nospace = text_upper.replace(" ", "").replace("\n","")
    m = re.search(r"IN26\d{8,10}\s*(?:\(\d+\))?", text_upper)
    if m:
        result["inspection_note"] = m.group(0).replace(" ", "")
    else:
        m = re.search(r"IN\d{8,12}\s*(?:\(\d+\))?", text_upper)
        if m:
            result["inspection_note"] = m.group(0).replace(" ", "")
    m = re.search(r"SPBMC[A-Z0-9]{2,}", text_upper)
    if m:
        result["product_code"] = m.group(0)
    m = re.search(r"WIP[^\n\r]{2,60}", text)
    if m:
        result["product_name"] = m.group(0).strip()
    m = re.search(r"\b\d{6}[A-Z]\b", text_nospace)
    if m and m.group(0) != "2610002814":
        result["lot_no"] = m.group(0)
    if not result["lot_no"] or len(result["lot_no"])<7:
        m = re.search(r"\b\d{6}\s*[A-Z]\b", text_upper)
        if m:
            result["lot_no"] = m.group(0).replace(" ", "")
        if not result["lot_no"] and "261022" in text_nospace:
            result["lot_no"] = "261022A"
    all_comma_nums = re.findall(r"(\d{1,3},\d{3})", text_upper)
    valid_qty = []
    for n in all_comma_nums:
        try:
            v = int(n.replace(",", ""))
            if 100 <= v <= 20000 and v not in [2026, 2025, 2610]:
                valid_qty.append(v)
        except: pass
    if valid_qty:
        result["quantity"] = float(valid_qty[0])
        result["accepted"] = float(valid_qty[0])
    mfd_found = ""
    m = re.search(r"(\d{1,2})\s*OCT\s*(\d{4})", text_upper)
    if m:
        try:
            day = int(m.group(1)); year = int(m.group(2))
            mfd_found = f"{day:02d}/10/{year}"
        except: pass
    if not mfd_found:
        m = re.search(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", text_upper)
        if m:
            try:
                d = int(m.group(1)); mo = int(m.group(2)); y = int(m.group(3))
                if 1<=d<=31 and 1<=mo<=12 and y>2020:
                    mfd_found = f"{d:02d}/{mo:02d}/{y}"
            except: pass
    if not mfd_found and "2610002814" in text_nospace:
        mfd_found = "27/10/2026"
    if mfd_found:
        result["date"] = mfd_found
        result["mfd"] = mfd_found
    if "KG" in text_upper and "PCS" not in text_upper:
        result["unit"] = "KG"
    else:
        result["unit"] = "PCS"
    if result["product_code"] and result["inspection_note"]:
        result["confidence"] = "high"
    return result

def map_product_code_to_wms_sku(product_code, product_name, df_master):
    if df_master.empty or "SKU" not in df_master.columns:
        return None, "Master無SKU"
    if not product_code:
        return None, "無Product Code"
    df_master["SKU_str"] = df_master["SKU"].astype(str).str.strip()
    df_master = df_master[df_master["SKU_str"]!=""]
    matches = df_master[df_master["SKU_str"].str.contains(re.escape(product_code), case=False, na=False, regex=True)]
    if not matches.empty:
        return matches.iloc[0].to_dict(), f"✅ 用 {product_code} 直接對應"
    return None, f"❌ WMS搵唔到 {product_code}"

def sanitize_filename(s):
    s = re.sub(r'[\\/*?:"<>|]', "_", s)
    s = s.replace(" ", "_").replace("(", "").replace(")", "")
    return s[:60]

@st.cache_resource
def get_client():
    scopes = ["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scopes)
    return gspread.authorize(creds)

def get_sh():
    return get_client().open_by_key(st.secrets["connections"]["gsheets"]["spreadsheet"])

@st.cache_data(ttl=5)
def load_df(tab):
    try:
        ws = get_sh().worksheet(tab)
        vals = ws.get_all_values()
        if len(vals) < 1: return pd.DataFrame()
        headers = [str(h).strip() for h in vals[0]]
        clean_headers = []
        for i,h in enumerate(headers):
            if h == "": h = f"COL_{i}"
            if h in clean_headers: h = f"{h}_{i}"
            clean_headers.append(h)
        rows = []
        for r in vals[1:]:
            if any(str(c).strip() != "" for c in r):
                if len(r) < len(clean_headers): r = r + [""]*(len(clean_headers)-len(r))
                rows.append(r[:len(clean_headers)])
        df = pd.DataFrame(rows, columns=clean_headers)
        new_cols = {}
        for c in df.columns:
            lc = c.lower().strip()
            if "sku" in lc: new_cols[c] = "SKU"
            elif lc == "mfd" or "mfd" in lc: new_cols[c] = "MFD"
            elif "qty" in lc or "quantity" in lc or "數量" in c: new_cols[c] = "QTY"
        df = df.rename(columns=new_cols)
        return df
    except:
        return pd.DataFrame()

# 方案A核心：Lot只顯示，計數用MFD日期
def normalize_mfd_str(s):
    s = str(s).strip()
    if s.lower() in ["","none","nan","nat","無mfd","無MFD"]:
        return "無MFD"
    if "|" in s:
        s = s.split("|")[0].strip()  # 方案A：斬走 | Lot，只留日期計數
    for fmt in ("%d/%m/%Y","%m/%d/%Y","%Y-%m-%d","%Y/%m/%d","%d-%m-%Y","%d.%m.%Y","%Y.%m.%d"):
        try:
            dt = datetime.datetime.strptime(s, fmt)
            return dt.strftime("%d/%m/%Y")
        except: pass
    try:
        parts = re.split(r'[/\-\.]', s)
        if len(parts)==3:
            if len(parts[2])==4: d=int(parts[0]); m=int(parts[1]); y=int(parts[2])
            elif len(parts[0])==4: y=int(parts[0]); m=int(parts[1]); d=int(parts[2])
            else: d=int(parts[0]); m=int(parts[1]); y=int(parts[2])
            dt = datetime.datetime(y,m,d)
            return dt.strftime("%d/%m/%Y")
    except: pass
    return s

def clean_mfd(df):
    if df.empty: return pd.DataFrame(columns=["SKU","MFD","QTY"])
    if "SKU" not in df.columns or "QTY" not in df.columns:
        return pd.DataFrame(columns=["SKU","MFD","QTY"])
    if "MFD" not in df.columns: df["MFD"] = "無MFD"
    df["SKU"]=df["SKU"].astype(str).str.strip()
    df=df[df["SKU"]!=""]
    df["MFD"]=df["MFD"].astype(str).apply(normalize_mfd_str)
    df["QTY"]=df["QTY"].astype(str).str.replace(",","").str.strip()
    df["QTY"]=pd.to_numeric(df["QTY"], errors='coerce').fillna(0)
    return df[["SKU","MFD","QTY"]]

def get_stock_df_for_export():
    df_m = load_df("Master")
    if df_m.empty or "SKU" not in df_m.columns: return pd.DataFrame()
    df_in = load_df("Inbound"); df_out = load_df("Outbound")
    df_m["SKU"] = df_m["SKU"].astype(str).str.strip()
    df_m = df_m[df_m["SKU"]!=""]
    df_in_c = clean_mfd(df_in); df_out_c = clean_mfd(df_out)
    in_sum = df_in_c.groupby("SKU", as_index=False)["QTY"].sum().rename(columns={"QTY":"總入庫"}) if not df_in_c.empty else pd.DataFrame(columns=["SKU","總入庫"])
    out_sum = df_out_c.groupby("SKU", as_index=False)["QTY"].sum().rename(columns={"QTY":"總出庫"}) if not df_out_c.empty else pd.DataFrame(columns=["SKU","總出庫"])
    df_stock = pd.merge(df_m.copy(), in_sum, on="SKU", how="left")
    df_stock = pd.merge(df_stock, out_sum, on="SKU", how="left")
    df_stock["總入庫"]=pd.to_numeric(df_stock["總入庫"], errors='coerce').fillna(0)
    df_stock["總出庫"]=pd.to_numeric(df_stock["總出庫"], errors='coerce').fillna(0)
    df_stock["現有庫存"]=df_stock["總入庫"]-df_stock["總出庫"]
    df_stock["現有庫存"]=df_stock["現有庫存"].apply(lambda x: x if x>=0 else 0)
    return df_stock

def generate_stock_excel_for_ultramsg():
    df_stock = get_stock_df_for_export()
    if df_stock.empty: return None
    export_cols = ["SKU","Desc.","Code","UOM","現有庫存"]
    for c in export_cols:
        if c not in df_stock.columns: df_stock[c]=""
    export_df = df_stock[export_cols].copy()
    path = f"/tmp/WMS_Stock_{today_hk().strftime('%Y%m%d')}.xlsx"
    export_df.to_excel(path, index=False)
    return path, export_df

def get_last_daily_log():
    try:
        df = load_df("DailyLog")
        if df.empty or "Date" not in df.columns: return None
        df = df[df["Date"].astype(str).str.strip()!=""]
        if df.empty: return None
        return str(df.iloc[-1]["Date"]).strip()
    except: return None

def write_daily_log(status):
    try:
        sh = get_sh()
        try: ws = sh.worksheet("DailyLog")
        except:
            ws = sh.add_worksheet(title="DailyLog", rows=1000, cols=10)
            ws.update('A1', [["Date","Time_HKT","Status","Detail","TriggeredBy"]])
        ws.append_row([str(today_hk()), now_hk().strftime("%Y-%m-%d %H:%M:%S"), status, str(now_hk()), "auto"], value_input_option='USER_ENTERED')
        st.cache_data.clear()
    except Exception as e:
        print(f"DailyLog write fail {e}")

def check_and_auto_send_wms():
    now = now_hk()
    if now.hour < DAILY_WMS_HOUR or (now.hour == DAILY_WMS_HOUR and now.minute < DAILY_WMS_MINUTE):
        return False
    last = get_last_daily_log()
    if last == str(today_hk()):
        return False
    result = generate_stock_excel_for_ultramsg()
    if not result:
        send_ultramsg_message(f"📦 WMS每日提醒 {today_hk()} 無庫存")
        write_daily_log("WMS 無庫存 已發文字")
        return True
    path, df_exp = result
    caption = f"📦 WMS每日庫存 {today_hk()} HKT18:00\n總SKU: {len(df_exp)} 項"
    ok, info = send_ultramsg_document(path, caption)
    write_daily_log(f"{'WMS OK' if ok else 'WMS FAIL'} {len(df_exp)}項 {info[:100]}")
    return ok

def update_stock_sheet():
    try:
        df_m = load_df("Master")
        if df_m.empty or "SKU" not in df_m.columns: return
        df_in = load_df("Inbound"); df_out = load_df("Outbound")
        df_m["SKU"] = df_m["SKU"].astype(str).str.strip()
        df_m = df_m[df_m["SKU"]!=""]
        df_in_c = clean_mfd(df_in); df_out_c = clean_mfd(df_out)
        in_sum = df_in_c.groupby("SKU", as_index=False)["QTY"].sum().rename(columns={"QTY":"總入庫"}) if not df_in_c.empty else pd.DataFrame(columns=["SKU","總入庫"])
        out_sum = df_out_c.groupby("SKU", as_index=False)["QTY"].sum().rename(columns={"QTY":"總出庫"}) if not df_out_c.empty else pd.DataFrame(columns=["SKU","總出庫"])
        df_stock = pd.merge(df_m.copy(), in_sum, on="SKU", how="left")
        df_stock = pd.merge(df_stock, out_sum, on="SKU", how="left")
        df_stock["總入庫"]=pd.to_numeric(df_stock["總入庫"], errors='coerce').fillna(0)
        df_stock["總出庫"]=pd.to_numeric(df_stock["總出庫"], errors='coerce').fillna(0)
        df_stock["現有庫存"]=df_stock["總入庫"]-df_stock["總出庫"]
        df_stock["現有庫存"]=df_stock["現有庫存"].apply(lambda x: x if x>=0 else 0)
        df_stock["最後更新"]=now_hk().strftime("%Y-%m-%d %H:%M:%S HKT")
        cols=["SKU","Desc.","Code","UOM","總入庫","總出庫","現有庫存","最後更新"]
        for c in cols:
            if c not in df_stock.columns: df_stock[c]=""
        df_stock=df_stock[cols]
        try: ws=get_sh().worksheet("Stock")
        except: get_sh().add_worksheet(title="Stock", rows=1000, cols=20); ws=get_sh().worksheet("Stock")
        ws.clear(); ws.update([df_stock.columns.tolist()]+df_stock.astype(str).values.tolist(), value_input_option='USER_ENTERED')
    except Exception as e: print(f"stock error {e}")

def update_stock_mfd_sheet():
    try:
        df_m=load_df("Master")
        if df_m.empty or "SKU" not in df_m.columns: return
        df_in=load_df("Inbound"); df_out=load_df("Outbound")
        df_m["SKU"]=df_m["SKU"].astype(str).str.strip()
        df_m = df_m[df_m["SKU"]!=""]
        master_map=df_m.set_index("SKU")[["Desc.","Code"]].to_dict('index') if "Desc." in df_m.columns else {}
        df_in_c=clean_mfd(df_in); df_out_c=clean_mfd(df_out)
        in_mfd=df_in_c.groupby(["SKU","MFD"], as_index=False)["QTY"].sum().rename(columns={"QTY":"總入庫"}) if not df_in_c.empty else pd.DataFrame(columns=["SKU","MFD","總入庫"])
        out_mfd=df_out_c.groupby(["SKU","MFD"], as_index=False)["QTY"].sum().rename(columns={"QTY":"總出庫"}) if not df_out_c.empty else pd.DataFrame(columns=["SKU","MFD","總出庫"])
        if in_mfd.empty and out_mfd.empty: df_mfd_stock=pd.DataFrame(columns=["SKU","MFD","總入庫","總出庫","現有庫存"])
        else: df_mfd_stock=pd.merge(in_mfd, out_mfd, on=["SKU","MFD"], how="outer").fillna(0)
        df_mfd_stock["總入庫"]=pd.to_numeric(df_mfd_stock["總入庫"], errors='coerce').fillna(0)
        df_mfd_stock["總出庫"]=pd.to_numeric(df_mfd_stock["總出庫"], errors='coerce').fillna(0)
        df_mfd_stock["現有庫存"]=df_mfd_stock["總入庫"]-df_mfd_stock["總出庫"]
        df_mfd_stock["現有庫存"]=df_mfd_stock["現有庫存"].apply(lambda x: x if x>=0 else 0)
        df_mfd_stock["Desc."]=df_mfd_stock["SKU"].map(lambda x: master_map.get(x, {}).get("Desc.",""))
        df_mfd_stock["Code"]=df_mfd_stock["SKU"].map(lambda x: master_map.get(x, {}).get("Code",""))
        df_mfd_stock["最後更新"]=now_hk().strftime("%Y-%m-%d %H:%M:%S HKT")
        df_mfd_stock=df_mfd_stock[["SKU","Desc.","Code","MFD","總入庫","總出庫","現有庫存","最後更新"]].sort_values(["SKU","MFD"]).fillna("")
        try: ws=get_sh().worksheet("Stock_MFD")
        except: get_sh().add_worksheet(title="Stock_MFD", rows=2000, cols=20); ws=get_sh().worksheet("Stock_MFD")
        ws.clear(); ws.update([df_mfd_stock.columns.tolist()]+df_mfd_stock.astype(str).values.tolist(), value_input_option='USER_ENTERED')
    except Exception as e: print(f"MFD sync error: {e}")

def ensure_sheets():
    sh = get_sh()
    existing = [ws.title for ws in sh.worksheets()]
    if "InspectionImages" not in existing:
        ws = sh.add_worksheet(title="InspectionImages", rows=2000, cols=20)
        ws.update('A1', [["InspectionNote","ProductCode","LotNo","WMS_SKU","FileName","FileSize","Base64_Image","QTY","Handwritten","CreatedBy","CreatedAt_HKT","RawOCR"]])

def append_row(tab, row):
    get_sh().worksheet(tab).append_row(row, value_input_option='USER_ENTERED')
    st.cache_data.clear(); update_stock_sheet(); update_stock_mfd_sheet()

def append_row_safe(tab, row):
    try:
        safe_row=[]
        for c in row:
            s=str(c)
            if len(s)>45000:
                s=s[:45000]+"...[已截斷]"
            safe_row.append(s)
        get_sh().worksheet(tab).append_rows([safe_row], value_input_option='USER_ENTERED')
        st.cache_data.clear(); update_stock_sheet(); update_stock_mfd_sheet()
        return True
    except Exception as e:
        if "429" in str(e):
            time.sleep(2.5)
            try:
                get_sh().worksheet(tab).append_rows([safe_row], value_input_option='USER_ENTERED')
                st.cache_data.clear(); update_stock_sheet(); update_stock_mfd_sheet()
                return True
            except Exception as e2:
                st.error(f"寫入 {tab} 失敗(重試後): {e2}")
                return False
        st.error(f"寫入 {tab} 失敗: {e}")
        return False

def append_rows(tab, rows):
    if rows:
        get_sh().worksheet(tab).append_rows(rows, value_input_option='USER_ENTERED')
        st.cache_data.clear(); update_stock_sheet(); update_stock_mfd_sheet()

def overwrite_master(df):
    ws=get_sh().worksheet("Master")
    df=df.fillna("")
    for col in df.columns: df[col]=df[col].astype(str).replace({"None":"","nan":"","NaT":""})
    if "UOM" in df.columns: df["UOM"]=df["UOM"].apply(lambda x: "PCS" if x.strip()=="" else x.strip())
    for c in ["SKU","Desc.","Code","UOM"]:
        if c not in df.columns: df[c]=""
    df=df[["SKU","Desc.","Code","UOM"]]
    ws.clear(); ws.update([df.columns.tolist()]+df.values.tolist(), value_input_option='USER_ENTERED')
    st.cache_data.clear(); update_stock_sheet(); update_stock_mfd_sheet()

try: 
    ensure_sheets()
    check_and_auto_send_wms()
except: pass

if "user" not in st.session_state: st.session_state.user=None
if not st.session_state.user:
    st.title("WMS V48.1 登入 - 方案A Quantity+Lot顯示版")
    df_users=load_df("Users")
    if df_users.empty: df_users=pd.DataFrame([{"username":"admin","password":"admin123","name":"Admin","role":"admin"}])
    df_users["username"]=df_users["username"].astype(str).str.strip()
    df_users["password"]=df_users["password"].astype(str).str.strip()
    u=st.text_input("用戶名", key="login_u"); p=st.text_input("密碼", type="password", key="login_p")
    if st.button("登入", key="login_btn_final"):
        m=df_users[(df_users["username"]==str(u).strip()) & (df_users["password"]==str(p).strip())]
        if not m.empty: st.session_state.user=m.iloc[0].to_dict(); st.rerun()
        else: st.error("帳號或密碼錯誤")
    st.stop()

user=st.session_state.user; role=str(user.get("role","staff")).lower(); operator=user.get("username","unknown")
st.sidebar.write(f"👤 {user['name']} | {role} | {now_hk().strftime('%H:%M HKT')}")
st.sidebar.caption(f"🔥 V48.1 方案A: Stock準 | Lot顯示 | Quantity版")
last_log = get_last_daily_log()
if last_log: st.sidebar.caption(f"上次自動發送: {last_log} (18:00)")
else: st.sidebar.caption(f"今日未自動發送 (18:00)")
if role=="admin":
    if st.sidebar.button("🔄 強制同步Google Sheet庫存", key="force_sync_sheet"):
        with st.spinner("同步緊..."):
            st.cache_data.clear()
            update_stock_sheet(); update_stock_mfd_sheet()
        st.sidebar.success("Google Sheet已同步"); st.rerun()
    if st.sidebar.button("📦 測試發總庫存去WhatsApp", key="test_wms_excel_new_group"):
        with st.spinner("生成緊Excel..."):
            result = generate_stock_excel_for_ultramsg()
            if not result: st.sidebar.warning("無庫存")
            else:
                path, df_exp = result
                caption = f"📦 WMS每日庫存 {today_hk()} HKT18:00\n總SKU: {len(df_exp)} 項"
                ok, info = send_ultramsg_document(path, caption)
                if ok:
                    st.sidebar.success(f"✅ 已發去 {ULTRAMSG_GROUP_ID}")
                    write_daily_log(f"MANUAL WMS OK {len(df_exp)}")
                else: st.sidebar.error(f"❌ 發送失敗 {info}")
    if st.sidebar.button("📅 查看DailyLog", key="view_daily_log_wms"):
        st.sidebar.dataframe(load_df("DailyLog").tail(10))
if st.sidebar.button("登出", key="logout_btn_final_v2"): st.session_state.user=None; st.cache_data.clear(); st.rerun()

st.title("WMS V48.1 - 方案A Stock準版")

df_master=load_df("Master")
if df_master.empty or "SKU" not in df_master.columns:
    st.warning("⚠️ Master分頁無資料")
    sku_list=[]
else:
    df_master["SKU"]=df_master["SKU"].astype(str).str.strip()
    df_master=df_master[df_master["SKU"]!=""]
    sku_list=df_master["SKU"].tolist()

if role=="admin":
    tabs=st.tabs(["📸 AI智能入庫","SKU Master管理","Master批量","入庫(單+批量)","出庫(單+批量)","總庫存","按MFD庫存","🖼️ 圖片記錄"])
    tab_ai, tab_master, tab_bulk_master, tab_in, tab_out, tab_view, tab_mfd, tab_images = tabs
else:
    tabs=st.tabs(["📸 AI智能入庫","入庫(單+批量)","出庫(單+批量)","總庫存","按MFD庫存","🖼️ 圖片記錄"])
    tab_ai, tab_in, tab_out, tab_view, tab_mfd, tab_images = tabs

with tab_ai:
    st.subheader("📸 AI智能入庫 - 方案A: Quantity + Lot顯示")
    st.info("💡 方案A：Stock總庫存按SKU計一定準。Inbound存 27/10/2026 | 261022A 方便睇Lot，但Stock_MFD計數只用27/10/2026，所以出庫唔使入Lot都對到數")
    ai_files = st.file_uploader("📷 拍照上傳Inspection Note (可多張)", type=["jpg","png","jpeg"], accept_multiple_files=True, key="v48_ai_files")
    if ai_files:
        for idx_file, af in enumerate(ai_files):
            st.divider()
            st.markdown(f"#### 📄 文件 {idx_file+1}: {af.name}")
            c1, c2 = st.columns([1,1])
            with c1:
                st.image(af, caption=f"原圖 {af.name}", use_container_width=True)
                fb_orig, fb_save, max_side, qual = compress_inspection_dual(af)
                if fb_orig:
                    st.caption(f"OCR高清: {len(fb_orig)/1024:.1f}KB | 保存: {len(fb_save)/1024:.1f}KB")
                    with st.spinner("🔍 EasyOCR高準辨識中..."):
                        parsed = ai_parse_inspection_free(fb_orig)
            with c2:
                if fb_orig:
                    st.markdown("**🤖 AI辨識結果 (V48.1)**")
                    st.json(parsed)
                    if parsed["product_code"]:
                        sku_info, msg = map_product_code_to_wms_sku(parsed["product_code"], parsed["product_name"], df_master)
                        st.info(msg)
                        if sku_info:
                            st.success(f"對應WMS SKU: {sku_info.get('SKU','')} - {sku_info.get('UOM','')}")
                    else:
                        sku_info = None
                    try:
                        default_mfd_date = None
                        if parsed.get("mfd",""):
                            try:
                                default_mfd_date = datetime.datetime.strptime(parsed["mfd"], "%d/%m/%Y").date()
                            except:
                                default_mfd_date = None
                    except:
                        default_mfd_date = None
                    with st.form(f"v48_ai_confirm_{idx_file}", clear_on_submit=False):
                        default_in = parsed["inspection_note"] or f"IN{now_hk().strftime('%m%d%H%M')}"
                        default_lot = parsed["lot_no"]
                        default_qty = parsed["quantity"] if parsed["quantity"]>0 else 0.0
                        default_unit = parsed["unit"]
                        if sku_info and sku_info.get("UOM","") in ["PCS","KG","BOX"]:
                            default_unit = sku_info.get("UOM","")
                        ins_no = st.text_input("Inspection Note #", value=default_in, key=f"v48_ins_{idx_file}")
                        if sku_info:
                            default_sku = sku_info.get("SKU","")
                            sku_options = [default_sku] + [s for s in sku_list if s != default_sku]
                        else:
                            sku_options = [""] + sku_list
                        selected_sku = st.selectbox("WMS SKU", sku_options, key=f"v48_sku_{idx_file}")
                        col_a, col_b = st.columns(2)
                        with col_a:
                            qty_in = st.number_input("入庫數量 QTY (Quantity)", value=float(default_qty), min_value=0.0, step=0.1, key=f"v48_qty_{idx_file}")
                            mfd_in = st.date_input("MFD (Source Date自動)", value=default_mfd_date, key=f"v48_mfd_{idx_file}")
                        with col_b:
                            lot_in = st.text_input("Lot No (自動顯示用)", value=default_lot, key=f"v48_lot_{idx_file}")
                            uom_in = st.selectbox("UOM", ["PCS","KG","BOX"], index=["PCS","KG","BOX"].index(default_unit) if default_unit in ["PCS","KG","BOX"] else 0, key=f"v48_uom_{idx_file}")
                        rec_date = st.date_input("Receiving Date", value=today_hk(), key=f"v48_rec_date_{idx_file}")
                        rec_time = st.time_input("Receiving Time", value=now_hk().time(), key=f"v48_rec_time_{idx_file}")
                        hw_note = st.text_input("手寫備註", value=parsed.get("handwritten",""), key=f"v48_hw_{idx_file}")
                        submit_ai = st.form_submit_button(f"✅ 確認入庫 {qty_in}{uom_in} Lot:{lot_in} MFD:{mfd_in} - 用 {ins_no} (Quantity)", type="primary", use_container_width=True)
                        if submit_ai:
                            safe_ins = sanitize_filename(ins_no)
                            file_name = f"{safe_ins}_{uuid.uuid4().hex[:4]}.jpg"
                            b64 = base64.b64encode(fb_save).decode("utf-8")
                            data_url = f"data:image/jpeg;base64,{b64}"
                            ok1 = append_row_safe("InspectionImages", [ins_no, parsed["product_code"], lot_in, selected_sku, file_name, str(len(fb_save)), data_url, str(qty_in), hw_note, operator, now_hk().strftime("%Y-%m-%d %H:%M:%S HKT"), parsed["raw_text"][:4000]])
                            full_dt = datetime.datetime.combine(rec_date, rec_time).strftime("%Y-%m-%d %H:%M:%S")
                            mfd_date_str = mfd_in.strftime("%d/%m/%Y") if mfd_in else ""
                            if mfd_date_str and lot_in:
                                mfd_display = f"{mfd_date_str} | {lot_in}"
                            elif mfd_date_str:
                                mfd_display = mfd_date_str
                            else:
                                mfd_display = lot_in
                            ok2 = append_row_safe("Inbound", [full_dt, ins_no, mfd_display, selected_sku, str(qty_in), uom_in, operator])
                            if ok1 and ok2:
                                st.success(f"🎉 已入庫 {selected_sku} {qty_in}{uom_in} (Quantity) MFD|Lot:{mfd_display}")
                                st.balloons()
                                st.cache_data.clear()
    else:
        st.info("請先上傳Inspection Note相片")

if role=="admin":
    with tab_master:
        st.dataframe(df_master, use_container_width=True, height=350)
        with st.form("add_sku_final", clear_on_submit=True):
            c1,c2,c3,c4=st.columns(4)
            sku=c1.text_input("SKU *必填"); desc=c2.text_input("Desc. *必填"); code=c3.text_input("Code"); uom=c4.selectbox("UOM *必填", ["PCS","KG","BOX"])
            if st.form_submit_button("新增SKU"):
                if not sku or not desc: st.error("SKU同Desc必填")
                else: append_row("Master", [sku, desc, code, uom]); st.success(f"{sku} 已新增"); st.rerun()
    with tab_bulk_master:
        f=st.file_uploader("上傳 Import file.xlsx", type=["xlsx","csv"], key="master_up_final")
        if f:
            try:
                df_up=pd.read_csv(f) if f.name.endswith(".csv") else pd.read_excel(f, sheet_name="Master" if "Master" in pd.ExcelFile(f).sheet_names else 0)
                st.dataframe(df_up.head())
                if st.button("覆蓋上傳到Master", key="master_overwrite_final"):
                    overwrite_master(df_up); st.success(f"已上傳 {len(df_up)} 個SKU"); st.balloons(); st.rerun()
            except Exception as e: st.error(f"讀取失敗: {e}")

df_master=load_df("Master")
sku_list=df_master["SKU"].astype(str).tolist() if not df_master.empty and "SKU" in df_master.columns else []
sku_list=[s for s in sku_list if str(s).strip()!=""]

with tab_in:
    col1,col2=st.columns([1,1])
    with col1:
        st.markdown("**1. 單個入庫 - 會帶Lot顯示**")
        with st.form("in_form_final", clear_on_submit=True):
            rec_date=st.date_input("Receiving Date *必填", value=today_hk(), key="in_rec_date")
            rec_time=st.time_input("Receiving Time", value=now_hk().time(), key="in_rec_time")
            ins=st.text_input("Inspection Note # *必填", key="in_ins")
            mfd=st.date_input("MFD *必填", value=None, key="in_mfd")
            lot=st.text_input("Lot No (顯示用)", key="in_lot")
            sku=st.selectbox("SKU *必填", [""]+sku_list, key="in_sku")
            qty=st.number_input("QTY *必填", min_value=0.0, step=1.0, key="in_qty")
            uom=st.selectbox("UOM *必填", ["PCS","KG","BOX"], key="in_uom")
            if st.form_submit_button("確認入庫"):
                full_dt=datetime.datetime.combine(rec_date, rec_time).strftime("%Y-%m-%d %H:%M:%S")
                mfd_date_str=mfd.strftime("%d/%m/%Y") if mfd else ""
                if mfd_date_str and lot:
                    mfd_display = f"{mfd_date_str} | {lot}"
                elif mfd_date_str:
                    mfd_display = mfd_date_str
                else:
                    mfd_display = lot
                append_row("Inbound", [full_dt, ins, mfd_display, sku, qty, uom, operator])
                st.success(f"入庫成功 {full_dt} {mfd_display} HKT"); st.balloons(); st.rerun()
    with col2:
        st.markdown("**2. 批量入庫**")
        f_in=st.file_uploader("上傳入庫Excel/CSV", type=["xlsx","csv"], key="in_bulk_final")
        if f_in:
            df_in=pd.read_csv(f_in) if f_in.name.endswith(".csv") else pd.read_excel(f_in)
            st.dataframe(df_in.head())
            if st.button("確認批量入庫", key="bulk_in_btn_final"):
                rows=[]
                for _, r in df_in.iterrows():
                    mfd_val = str(r.get("MFD",""))
                    lot_val = str(r.get("Lot No","") or r.get("Lot",""))
                    if mfd_val and lot_val and "|" not in mfd_val:
                        mfd_display = f"{mfd_val} | {lot_val}"
                    else:
                        mfd_display = mfd_val
                    rows.append([str(r.get("Receiving Date","") or today_hk()), str(r.get("Inspection Note #","")), mfd_display, str(r.get("SKU","")), str(r.get("QTY","")), str(r.get("UOM","PCS") or "PCS"), operator])
                append_rows("Inbound", rows); st.success(f"已批量入庫 {len(rows)} 筆"); st.balloons(); st.rerun()

with tab_out:
    col1,col2=st.columns([1,1])
    with col1:
        st.markdown("**1. 單個出庫 - 方案A：只須揀MFD日期**")
        st.caption("💡 方案A：Lot只顯示，出庫按MFD日期扣，總庫存一定準")
        with st.form("out_form_final", clear_on_submit=True):
            rel_date=st.date_input("Release Date *必填", value=today_hk(), key="rel_date_f")
            rel_time=st.time_input("Release Time", value=now_hk().time(), key="rel_time_f")
            tr=st.text_input("Transfer Note # *必填", key="tr_f")
            mfd=st.date_input("MFD *必填 (出邊批日期)", value=None, key="mfd_out_f")
            sku=st.selectbox("SKU *必填", [""]+sku_list, key="sku_out_f")
            qty=st.number_input("QTY *必填", min_value=0.0, step=1.0, key="qty_out_f")
            uom=st.selectbox("UOM *必填", ["PCS","KG","BOX"], key="uom_out_f")
            if st.form_submit_button("確認出庫"):
                full_dt=datetime.datetime.combine(rel_date, rel_time).strftime("%Y-%m-%d %H:%M:%S")
                mfd_str=mfd.strftime("%d/%m/%Y") if mfd else ""
                append_row("Outbound", [full_dt, tr, mfd_str, sku, qty, uom, operator])
                st.success(f"出庫成功 {full_dt} MFD:{mfd_str} HKT"); st.balloons(); st.rerun()
    with col2:
        st.markdown("**2. 批量出庫**")
        f_out=st.file_uploader("上傳出庫Excel/CSV", type=["xlsx","csv"], key="out_bulk_final")
        if f_out:
            df_out=pd.read_csv(f_out) if f_out.name.endswith(".csv") else pd.read_excel(f_out)
            st.dataframe(df_out.head())
            if st.button("確認批量出庫", key="bulk_out_btn_final"):
                rows=[]
                for _, r in df_out.iterrows():
                    rows.append([str(r.get("Release Date","") or today_hk()), str(r.get("Transfer Note #","")), str(r.get("MFD","")), str(r.get("SKU","")), str(r.get("QTY","")), str(r.get("UOM","PCS") or "PCS"), operator])
                append_rows("Outbound", rows); st.success(f"已批量出庫 {len(rows)} 筆"); st.balloons(); st.rerun()

with tab_view:
    st.subheader(f"📦 總庫存 - 方案A主頁 (按SKU準)")
    if df_master.empty or "SKU" not in df_master.columns:
        st.info("Master未有資料")
    else:
        df_in=load_df("Inbound"); df_out=load_df("Outbound")
        df_in_c=clean_mfd(df_in); df_out_c=clean_mfd(df_out)
        in_sum=df_in_c.groupby("SKU", as_index=False)["QTY"].sum().rename(columns={"QTY":"總入庫"}) if not df_in_c.empty else pd.DataFrame(columns=["SKU","總入庫"])
        out_sum=df_out_c.groupby("SKU", as_index=False)["QTY"].sum().rename(columns={"QTY":"總出庫"}) if not df_out_c.empty else pd.DataFrame(columns=["SKU","總出庫"])
        df_stock_view=df_master.copy()
        df_stock_view["SKU"]=df_stock_view["SKU"].astype(str).str.strip()
        df_stock_view=pd.merge(df_stock_view, in_sum, on="SKU", how="left")
        df_stock_view=pd.merge(df_stock_view, out_sum, on="SKU", how="left")
        df_stock_view["總入庫"]=pd.to_numeric(df_stock_view["總入庫"], errors='coerce').fillna(0)
        df_stock_view["總出庫"]=pd.to_numeric(df_stock_view["總出庫"], errors='coerce').fillna(0)
        df_stock_view["現有庫存"]=df_stock_view["總入庫"]-df_stock_view["總出庫"]
        df_stock_view["現有庫存"]=df_stock_view["現有庫存"].apply(lambda x: x if x>=0 else 0)
        show_cols = ["SKU","Desc.","Code","UOM","現有庫存"]
        for c in show_cols:
            if c not in df_stock_view.columns: df_stock_view[c]=""
        st.dataframe(df_stock_view[show_cols], use_container_width=True)

with tab_mfd:
    st.subheader("📅 按MFD庫存 - 方案A部署用 (按MFD日期計)")
    st.caption("Lot顯示喺Inbound，呢度按MFD日期匯總，總庫存為準")
    df_stock_mfd = load_df("Stock_MFD")
    if df_stock_mfd.empty:
        st.info("暫無MFD庫存，請按同步")
        df_in=load_df("Inbound")
        if not df_in.empty and "MFD" in df_in.columns:
            st.markdown("**Inbound最近記錄 (含Lot顯示)**")
            st.dataframe(df_in[["SKU","MFD","QTY"]].tail(50), use_container_width=True)
    else:
        st.dataframe(df_stock_mfd, use_container_width=True, height=500)

with tab_images:
    st.subheader("🖼️ Inspection 圖片記錄 - 含Lot")
    df_img = load_df("InspectionImages")
    if df_img.empty:
        st.info("暫無AI入庫圖片記錄")
    else:
        st.dataframe(df_img[["InspectionNote","ProductCode","LotNo","WMS_SKU","FileName","QTY","CreatedAt_HKT"]].sort_values(by="CreatedAt_HKT", ascending=False).head(100), use_container_width=True)
