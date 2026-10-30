import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
import datetime
import pytz
import requests
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
    except Exception:
        return None, None, 0, 0

def clean_product_code_candidate(raw):
    if not raw:
        return ""
    s = raw.upper().strip().replace(" ","").replace("\n","").replace("\r","")
    s = s.replace("//","M").replace("/","").replace("\\","").replace("|","I")
    s = re.sub(r"SPB[A-Z]?C006", "SPBMC006", s)
    s = re.sub(r"SPB[A-Z]?C00(\d)", r"SPBMC00\1", s)
    s = re.sub(r"SPCQM0{2,}(\d)", r"SPCQM00\1", s)
    s = re.sub(r"[^A-Z0-9]", "", s)
    return s

def extract_product_code_robust(text_upper):
    # 清理 0CT -> OCT
    fixed = text_upper.replace("0CT","OCT")
    m = re.search(r"PRODUCT\s*CODE\s*[:：]?\s*([A-Z0-9/\\|]{5,15})", fixed)
    if m:
        cand = clean_product_code_candidate(m.group(1))
        if cand.startswith("SP") and len(cand)>=7:
            return cand
    candidates = re.findall(r"SP[A-Z0-9/\\|]{4,10}", fixed)
    for c in candidates:
        cleaned = clean_product_code_candidate(c)
        if cleaned.startswith("SPBMC") or cleaned.startswith("SPCQM"):
            if len(cleaned)>=7:
                return cleaned
    # 最後兜底
    if "SPB" in fixed and "006" in fixed:
        return "SPBMC006"
    if "SPCQM" in fixed and "002" in fixed:
        return "SPCQM002"
    return ""

def extract_inspection_note_robust(text_upper):
    fixed = text_upper.replace("0CT","OCT").replace(" ","")
    m = re.search(r"IN26\d{8,10}\(?\d*\)?", fixed)
    if m:
        return m.group(0).replace(" ","")
    m = re.search(r"42\s*61\d{7,10}\(?\d*\)?", text_upper)
    if m:
        raw = m.group(0).replace(" ","").replace("42","IN",1)
        raw = raw.replace("61","26",1) if "IN61" in raw else raw
        return raw
    m = re.search(r"26\d{8,10}\(?\d*\)?", fixed)
    if m:
        return "IN" + m.group(0)
    return ""

def ai_parse_inspection_free(image_bytes_high_res):
    result = {"inspection_note": "","product_code": "","product_name": "","lot_no": "","quantity": 0.0,"accepted": 0.0,"unit": "PCS","date": "","mfd": "","handwritten": "","raw_text": "","confidence": "low"}
    text = ""
    try:
        import easyocr
        reader = easyocr.Reader(['ch_tra','en'], gpu=False, verbose=False)
        img = Image.open(io.BytesIO(image_bytes_high_res))
        img = ImageOps.exif_transpose(img).convert("RGB")
        # 放大2倍易認
        w,h = img.size
        img = img.resize((w*2, h*2), Image.LANCZOS)
        # EasyOCR直接讀PIL會自己轉
        ocr_result = reader.readtext(img, detail=0, paragraph=True)
        text = "\n".join(ocr_result)
        result["raw_text"] = text
    except Exception as e_easy:
        try:
            import pytesseract
            img = Image.open(io.BytesIO(image_bytes_high_res))
            img = ImageOps.exif_transpose(img)
            gray = img.convert("L")
            w,h = gray.size
            gray = gray.resize((w*2, h*2), Image.LANCZOS)
            gray = ImageOps.autocontrast(gray, cutoff=2)
            text = pytesseract.image_to_string(gray, lang='chi_tra+eng', config='--psm 6')
            result["raw_text"] = text + f"\n[Fallback {e_easy}]"
        except Exception as e:
            result["raw_text"] = f"OCR失敗: {e_easy}/{e}"
            return result

    text_upper = text.upper()
    text_upper_fixed = text_upper.replace("0CT","OCT")

    result["inspection_note"] = extract_inspection_note_robust(text_upper)
    result["product_code"] = extract_product_code_robust(text_upper_fixed)

    m = re.search(r"WIP[^\n\r]{2,80}", text)
    if m:
        result["product_name"] = m.group(0).strip()

    # Lot
    m = re.search(r"LOT\w{0,5}\s*(\d{6}[A-Z/\\|]?)", text_upper_fixed)
    if m:
        result["lot_no"] = m.group(1).replace("/","").replace("|","I").upper()
    if not result["lot_no"]:
        m = re.search(r"\b(26\d{4}[A-Z])\b", text_upper_fixed)
        if m:
            result["lot_no"] = m.group(1).upper()

    for n in re.findall(r"(\d{1,3},\d{3})", text_upper_fixed):
        try:
            v = int(n.replace(",",""))
            if 100 <= v <= 20000 and v not in [2026,2025,2610]:
                result["quantity"] = float(v)
                result["accepted"] = float(v)
                break
        except:
            pass

    mfd_found = ""
    oct_dates = re.findall(r"(\d{1,2})\s*OCT\s*(\d{4})", text_upper_fixed)
    if len(oct_dates) >= 2:
        mfd_found = f"{int(oct_dates[1][0]):02d}/10/{int(oct_dates[1][1])}"
    elif len(oct_dates)==1:
        mfd_found = f"{int(oct_dates[0][0]):02d}/10/{int(oct_dates[0][1])}"
    if mfd_found:
        result["date"] = mfd_found
        result["mfd"] = mfd_found

    if result["product_code"] or result["quantity"]>0:
        result["confidence"] = "high" if result["product_code"] and result["inspection_note"] else "medium"
    return result

def map_product_code_to_wms_sku(product_code, product_name, df_master):
    if df_master.empty or "SKU" not in df_master.columns:
        return None, "Master無SKU"
    if not product_code:
        return None, "無Product Code"
    product_code = str(product_code).strip().upper()
    df_master["SKU_str"] = df_master["SKU"].astype(str).str.strip()
    df_master["SKU_base"] = df_master["SKU_str"].apply(lambda x: x.split("---")[0].strip().upper() if "---" in x else x.strip().upper())
    exact = df_master[df_master["SKU_base"] == product_code]
    if not exact.empty:
        return exact.iloc[0].to_dict(), f"✅ 用 {product_code} 對應 {exact.iloc[0]['SKU_str']}"
    contains = df_master[df_master["SKU_str"].str.upper().str.contains(product_code, na=False)]
    if not contains.empty:
        return contains.iloc[0].to_dict(), f"✅ 用 {product_code} 包含對應 {contains.iloc[0]['SKU_str']}"
    close = difflib.get_close_matches(product_code, df_master["SKU_base"].tolist(), n=1, cutoff=0.8)
    if close:
        row = df_master[df_master["SKU_base"]==close[0]].iloc[0].to_dict()
        return row, f"✅ 用 {product_code} 模糊對應 {row['SKU_str']}"
    return None, f"❌ Master無 {product_code}"

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
            elif "qty" in lc or "quantity" in lc: new_cols[c] = "QTY"
        df = df.rename(columns=new_cols)
        return df
    except:
        return pd.DataFrame()

def normalize_mfd_str(s):
    s = str(s).strip()
    if s.lower() in ["","none","nan","nat","無mfd"]:
        return "無MFD"
    if "|" in s: s = s.split("|")[0].strip()
    for fmt in ("%d/%m/%Y","%m/%d/%Y","%Y-%m-%d","%Y/%m/%d"):
        try:
            dt = datetime.datetime.strptime(s, fmt)
            return dt.strftime("%d/%m/%Y")
        except: pass
    return s

def clean_mfd(df):
    if df.empty: return pd.DataFrame(columns=["SKU","MFD","QTY"])
    if "SKU" not in df.columns or "QTY" not in df.columns:
        return pd.DataFrame(columns=["SKU","MFD","QTY"])
    if "MFD" not in df.columns: df["MFD"] = "無MFD"
    df["SKU"]=df["SKU"].astype(str).str.strip()
    df["SKU"]=df["SKU"].apply(lambda x: x.split("---")[0].strip().upper() if "---" in x else x.strip().upper())
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
    df_m["SKU_base"] = df_m["SKU"].apply(lambda x: x.split("---")[0].strip().upper() if "---" in x else x.strip().upper())
    df_in_c = clean_mfd(df_in); df_out_c = clean_mfd(df_out)
    in_sum = df_in_c.groupby("SKU", as_index=False)["QTY"].sum().rename(columns={"QTY":"總入庫"}) if not df_in_c.empty else pd.DataFrame(columns=["SKU","總入庫"])
    out_sum = df_out_c.groupby("SKU", as_index=False)["QTY"].sum().rename(columns={"QTY":"總出庫"}) if not df_out_c.empty else pd.DataFrame(columns=["SKU","總出庫"])
    df_stock = pd.merge(df_m.copy(), in_sum, left_on="SKU_base", right_on="SKU", how="left")
    df_stock = pd.merge(df_stock, out_sum, left_on="SKU_base", right_on="SKU", how="left", suffixes=("","_out"))
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
    except: pass

def check_and_auto_send_wms():
    now = now_hk()
    if now.hour < 18: return False
    last = get_last_daily_log()
    if last == str(today_hk()): return False
    result = generate_stock_excel_for_ultramsg()
    if not result: return True
    path, df_exp = result
    ok, info = send_ultramsg_document(path, f"📦 WMS {today_hk()} {len(df_exp)}項")
    write_daily_log(f"{'WMS OK' if ok else 'WMS FAIL'} {len(df_exp)}項")
    return ok

def update_stock_sheet():
    try:
        df_m = load_df("Master")
        if df_m.empty or "SKU" not in df_m.columns: return
        df_in = load_df("Inbound"); df_out = load_df("Outbound")
        df_m["SKU"] = df_m["SKU"].astype(str).str.strip()
        df_m = df_m[df_m["SKU"]!=""]
        df_m["SKU_base"] = df_m["SKU"].apply(lambda x: x.split("---")[0].strip().upper() if "---" in x else x.strip().upper())
        df_in_c = clean_mfd(df_in); df_out_c = clean_mfd(df_out)
        in_sum = df_in_c.groupby("SKU", as_index=False)["QTY"].sum().rename(columns={"QTY":"總入庫"}) if not df_in_c.empty else pd.DataFrame(columns=["SKU","總入庫"])
        out_sum = df_out_c.groupby("SKU", as_index=False)["QTY"].sum().rename(columns={"QTY":"總出庫"}) if not df_out_c.empty else pd.DataFrame(columns=["SKU","總出庫"])
        df_stock = pd.merge(df_m.copy(), in_sum, left_on="SKU_base", right_on="SKU", how="left")
        df_stock = pd.merge(df_stock, out_sum, left_on="SKU_base", right_on="SKU", how="left", suffixes=("","_y"))
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
    except: pass

def update_stock_mfd_sheet():
    try:
        df_m=load_df("Master")
        if df_m.empty or "SKU" not in df_m.columns: return
        df_in=load_df("Inbound"); df_out=load_df("Outbound")
        df_m["SKU"]=df_m["SKU"].astype(str).str.strip()
        df_m = df_m[df_m["SKU"]!=""]
        df_m["SKU_base"]=df_m["SKU"].apply(lambda x: x.split("---")[0].strip().upper() if "---" in x else x.strip().upper())
        master_map=df_m.set_index("SKU_base")[["Desc.","Code"]].to_dict('index') if "Desc." in df_m.columns else {}
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
    except: pass

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
        safe_row=[str(c)[:45000] for c in row]
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
            except: return False
        return False

def append_rows(tab, rows):
    if rows:
        get_sh().worksheet(tab).append_rows(rows, value_input_option='USER_ENTERED')
        st.cache_data.clear(); update_stock_sheet(); update_stock_mfd_sheet()

def overwrite_master(df):
    ws=get_sh().worksheet("Master")
    df=df.fillna("")
    for col in df.columns: df[col]=df[col].astype(str).replace({"None":"","nan":"","NaT":""})
    for c in ["SKU","Desc.","Code","UOM"]:
        if c not in df.columns: df[c]=""
    df=df[["SKU","Desc.","Code","UOM"]]
    ws.clear(); ws.update([df.columns.tolist()]+df.values.tolist(), value_input_option='USER_ENTERED')
    st.cache_data.clear(); update_stock_sheet(); update_stock_mfd_sheet()

try: 
    ensure_sheets()
    check_and_auto_send_wms()
except Exception as e:
    print(f"init error {e}")

if "user" not in st.session_state: st.session_state.user=None
if not st.session_state.user:
    st.title("WMS V49.2 登入 - 穩定版")
    df_users=load_df("Users")
    if df_users.empty: df_users=pd.DataFrame([{"username":"admin","password":"admin123","name":"Admin","role":"admin"}])
    df_users["username"]=df_users["username"].astype(str).str.strip()
    df_users["password"]=df_users["password"].astype(str).str.strip()
    u=st.text_input("用戶名"); p=st.text_input("密碼", type="password")
    if st.button("登入"):
        m=df_users[(df_users["username"]==str(u).strip()) & (df_users["password"]==str(p).strip())]
        if not m.empty: st.session_state.user=m.iloc[0].to_dict(); st.rerun()
        else: st.error("帳號錯")
    st.stop()

user=st.session_state.user; role=str(user.get("role","staff")).lower(); operator=user.get("username","unknown")
st.sidebar.write(f"👤 {user['name']} | {role} | {now_hk().strftime('%H:%M HKT')}")
st.sidebar.caption(f"🔥 V49.2 穩定修復版 無cv2")
if role=="admin":
    if st.sidebar.button("🔄 強制同步"):
        st.cache_data.clear()
        update_stock_sheet(); update_stock_mfd_sheet()
        st.sidebar.success("已同步"); st.rerun()
    if st.sidebar.button("📦 測試發總庫存去WhatsApp"):
        result = generate_stock_excel_for_ultramsg()
        if not result: st.sidebar.warning("無庫存")
        else:
            path, df_exp = result
            ok, info = send_ultramsg_document(path, f"📦 WMS {today_hk()} {len(df_exp)}項")
            st.sidebar.success("已發" if ok else f"失敗 {info}")
if st.sidebar.button("登出"): st.session_state.user=None; st.cache_data.clear(); st.rerun()

st.title("WMS V49.2 - 穩定修復版")

df_master=load_df("Master")
sku_list=df_master["SKU"].astype(str).tolist() if not df_master.empty and "SKU" in df_master.columns else []

if role=="admin":
    tabs=st.tabs(["📸 AI智能入庫","SKU Master管理","Master批量","入庫(單+批量)","出庫(單+批量)","總庫存","按MFD庫存","🖼️ 圖片記錄"])
    tab_ai, tab_master, tab_bulk_master, tab_in, tab_out, tab_view, tab_mfd, tab_images = tabs
else:
    tabs=st.tabs(["📸 AI智能入庫","入庫(單+批量)","出庫(單+批量)","總庫存","按MFD庫存","🖼️ 圖片記錄"])
    tab_ai, tab_in, tab_out, tab_view, tab_mfd, tab_images = tabs

with tab_ai:
    st.subheader("📸 AI智能入庫 - V49.2")
    st.info("💡 V49.2：已修復Oh no錯誤 + SPB//C006→SPBMC006 + 42→IN + 支援---WIP中文")
    ai_files = st.file_uploader("📷 拍照上傳Inspection Note", type=["jpg","png","jpeg"], accept_multiple_files=True)
    if ai_files:
        for idx_file, af in enumerate(ai_files):
            st.divider()
            st.markdown(f"#### 📄 文件 {idx_file+1}: {af.name}")
            c1, c2 = st.columns([1,1])
            with c1:
                st.image(af, caption=f"原圖 {af.name}", use_container_width=True)
                fb_orig, fb_save, _, _ = compress_inspection_dual(af)
                if fb_orig:
                    with st.spinner("🔍 辨識中..."):
                        parsed = ai_parse_inspection_free(fb_orig)
            with c2:
                if fb_orig:
                    st.json(parsed)
                    if parsed["product_code"]:
                        sku_info, msg = map_product_code_to_wms_sku(parsed["product_code"], parsed["product_name"], df_master)
                        if "✅" in msg: st.success(msg)
                        else: st.error(msg)
                        if sku_info: st.success(f"WMS SKU: {sku_info.get('SKU','')}")
                    else:
                        sku_info = None
                        st.warning("未認到Product Code，試下影清啲或手動揀")
                    try:
                        default_mfd_date = datetime.datetime.strptime(parsed["mfd"], "%d/%m/%Y").date() if parsed.get("mfd","") else None
                    except:
                        default_mfd_date = None
                    with st.form(f"ai_confirm_{idx_file}"):
                        default_in = parsed["inspection_note"] or f"IN{now_hk().strftime('%m%d%H%M')}"
                        default_lot = parsed["lot_no"]
                        default_qty = parsed["quantity"] if parsed["quantity"]>0 else 0.0
                        ins_no = st.text_input("Inspection Note #", value=default_in, key=f"ins_{idx_file}")
                        if sku_info:
                            sku_options = [sku_info.get("SKU","")] + [s for s in sku_list if s != sku_info.get("SKU","")]
                        else:
                            sku_options = [""] + sku_list
                        selected_sku = st.selectbox("WMS SKU", sku_options, key=f"sku_{idx_file}")
                        qty_in = st.number_input("入庫數量", value=float(default_qty), min_value=0.0, step=0.1, key=f"qty_{idx_file}")
                        mfd_in = st.date_input("MFD", value=default_mfd_date, key=f"mfd_{idx_file}")
                        lot_in = st.text_input("Lot No", value=default_lot, key=f"lot_{idx_file}")
                        uom_in = st.selectbox("UOM", ["PCS","KG","BOX"], key=f"uom_{idx_file}")
                        rec_date = st.date_input("Receiving Date", value=today_hk(), key=f"rec_{idx_file}")
                        rec_time = st.time_input("Receiving Time", value=now_hk().time(), key=f"rec_t_{idx_file}")
                        if st.form_submit_button(f"✅ 確認入庫 {qty_in}{uom_in}", type="primary", use_container_width=True):
                            b64 = base64.b64encode(fb_save).decode("utf-8")
                            data_url = f"data:image/jpeg;base64,{b64}"
                            file_name = f"{ins_no}_{uuid.uuid4().hex[:4]}.jpg"
                            append_row_safe("InspectionImages", [ins_no, parsed["product_code"], lot_in, selected_sku, file_name, str(len(fb_save)), data_url, str(qty_in), "", operator, now_hk().strftime("%Y-%m-%d %H:%M:%S HKT"), parsed["raw_text"][:4000]])
                            full_dt = datetime.datetime.combine(rec_date, rec_time).strftime("%Y-%m-%d %H:%M:%S")
                            mfd_display = f"{mfd_in.strftime('%d/%m/%Y')} | {lot_in}" if mfd_in and lot_in else (mfd_in.strftime("%d/%m/%Y") if mfd_in else lot_in)
                            base_sku_for_stock = selected_sku.split("---")[0].strip().upper() if "---" in selected_sku else selected_sku.strip().upper()
                            append_row_safe("Inbound", [full_dt, ins_no, mfd_display, base_sku_for_stock, str(qty_in), uom_in, operator])
                            st.success(f"🎉 已入庫 {selected_sku} -> {base_sku_for_stock} {qty_in}{uom_in} {mfd_display}")
                            st.balloons()
    else:
        st.info("請先上傳")

if role=="admin":
    with tab_master:
        st.dataframe(df_master, use_container_width=True, height=350)
    with tab_bulk_master:
        f=st.file_uploader("上傳 Master", type=["xlsx","csv"], key="master_up")
        if f:
            df_up=pd.read_csv(f) if f.name.endswith(".csv") else pd.read_excel(f)
            st.dataframe(df_up.head())
            if st.button("覆蓋上傳"):
                overwrite_master(df_up); st.success(f"已上傳 {len(df_up)}"); st.rerun()

df_master=load_df("Master")
sku_list=df_master["SKU"].astype(str).tolist() if not df_master.empty and "SKU" in df_master.columns else []

with tab_in:
    col1,col2=st.columns([1,1])
    with col1:
        st.markdown("**1. 單個入庫**")
        with st.form("in_form", clear_on_submit=True):
            rec_date=st.date_input("Receiving Date", value=today_hk())
            rec_time=st.time_input("Receiving Time", value=now_hk().time())
            ins=st.text_input("Inspection Note #")
            mfd=st.date_input("MFD", value=None)
            lot=st.text_input("Lot No")
            sku=st.selectbox("SKU", [""]+sku_list)
            qty=st.number_input("QTY", min_value=0.0, step=1.0)
            uom=st.selectbox("UOM", ["PCS","KG","BOX"])
            if st.form_submit_button("確認入庫"):
                full_dt=datetime.datetime.combine(rec_date, rec_time).strftime("%Y-%m-%d %H:%M:%S")
                mfd_display = f"{mfd.strftime('%d/%m/%Y')} | {lot}" if mfd and lot else (mfd.strftime("%d/%m/%Y") if mfd else lot)
                base_sku = sku.split("---")[0].strip().upper() if "---" in sku else sku.strip().upper()
                append_row("Inbound", [full_dt, ins, mfd_display, base_sku, qty, uom, operator])
                st.success(f"入庫成功 {base_sku}"); st.rerun()
    with col2:
        st.markdown("**2. 批量入庫**")
        f_in=st.file_uploader("上傳入庫Excel/CSV", type=["xlsx","csv"])
        if f_in:
            df_in=pd.read_csv(f_in) if f_in.name.endswith(".csv") else pd.read_excel(f_in)
            st.dataframe(df_in.head())
            if st.button("確認批量入庫"):
                rows=[]
                for _, r in df_in.iterrows():
                    mfd_val = str(r.get("MFD",""))
                    lot_val = str(r.get("Lot No","") or r.get("Lot",""))
                    mfd_display = f"{mfd_val} | {lot_val}" if mfd_val and lot_val and "|" not in mfd_val else mfd_val
                    sku_raw = str(r.get("SKU",""))
                    base_sku = sku_raw.split("---")[0].strip().upper() if "---" in sku_raw else sku_raw.strip().upper()
                    rows.append([str(r.get("Receiving Date","") or today_hk()), str(r.get("Inspection Note #","")), mfd_display, base_sku, str(r.get("QTY","")), str(r.get("UOM","PCS") or "PCS"), operator])
                append_rows("Inbound", rows); st.success(f"已批量入庫 {len(rows)}"); st.rerun()

with tab_out:
    col1,col2=st.columns([1,1])
    with col1:
        st.markdown("**1. 單個出庫**")
        with st.form("out_form", clear_on_submit=True):
            rel_date=st.date_input("Release Date", value=today_hk())
            rel_time=st.time_input("Release Time", value=now_hk().time())
            tr=st.text_input("Transfer Note #")
            mfd=st.date_input("MFD (出邊批)", value=None)
            sku=st.selectbox("SKU *必填", [""]+sku_list, key="sku_out")
            qty=st.number_input("QTY", min_value=0.0, step=1.0)
            uom=st.selectbox("UOM", ["PCS","KG","BOX"])
            if st.form_submit_button("確認出庫"):
                full_dt=datetime.datetime.combine(rel_date, rel_time).strftime("%Y-%m-%d %H:%M:%S")
                mfd_str=mfd.strftime("%d/%m/%Y") if mfd else ""
                base_sku = sku.split("---")[0].strip().upper() if "---" in sku else sku.strip().upper()
                append_row("Outbound", [full_dt, tr, mfd_str, base_sku, qty, uom, operator])
                st.success(f"出庫成功 {base_sku}"); st.rerun()
    with col2:
        st.markdown("**2. 批量出庫**")
        f_out=st.file_uploader("上傳出庫Excel/CSV", type=["xlsx","csv"], key="out_bulk")
        if f_out:
            df_out=pd.read_csv(f_out) if f_out.name.endswith(".csv") else pd.read_excel(f_out)
            st.dataframe(df_out.head())
            if st.button("確認批量出庫"):
                rows=[]
                for _, r in df_out.iterrows():
                    sku_raw = str(r.get("SKU",""))
                    base_sku = sku_raw.split("---")[0].strip().upper() if "---" in sku_raw else sku_raw.strip().upper()
                    rows.append([str(r.get("Release Date","") or today_hk()), str(r.get("Transfer Note #","")), str(r.get("MFD","")), base_sku, str(r.get("QTY","")), str(r.get("UOM","PCS") or "PCS"), operator])
                append_rows("Outbound", rows); st.success(f"已批量出庫 {len(rows)}"); st.rerun()

with tab_view:
    st.subheader(f"📦 總庫存")
    df_stock = get_stock_df_for_export()
    if df_stock.empty:
        st.info("Master未有資料")
    else:
        st.dataframe(df_stock[["SKU","Desc.","Code","UOM","現有庫存"]], use_container_width=True)

with tab_mfd:
    st.subheader("📅 按MFD庫存")
    df_stock_mfd = load_df("Stock_MFD")
    if df_stock_mfd.empty:
        st.info("暫無MFD庫存")
    else:
        st.dataframe(df_stock_mfd, use_container_width=True, height=500)

with tab_images:
    st.subheader("🖼️ Inspection 圖片記錄")
    df_img = load_df("InspectionImages")
    if df_img.empty:
        st.info("暫無圖片記錄")
    else:
        st.dataframe(df_img[["InspectionNote","ProductCode","LotNo","WMS_SKU","FileName","QTY","CreatedAt_HKT"]].sort_values(by="CreatedAt_HKT", ascending=False).head(100), use_container_width=True)
