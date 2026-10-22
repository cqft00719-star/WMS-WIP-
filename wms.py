import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
import datetime
import pytz

HKT = pytz.timezone("Asia/Hong_Kong")
def now_hk(): return datetime.datetime.now(HKT)
def today_hk(): return now_hk().date()

st.set_page_config(page_title="WMS - Master版", layout="wide")

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
        # 智能識別欄位，唔理大小寫同空格
        new_cols = {}
        for c in df.columns:
            lc = c.lower().strip()
            if "sku" in lc: new_cols[c] = "SKU"
            elif lc == "mfd" or "mfd" in lc: new_cols[c] = "MFD"
            elif "qty" in lc or "quantity" in lc or "數量" in c or "入庫數" in c or "出庫數" in c: new_cols[c] = "QTY"
        df = df.rename(columns=new_cols)
        return df
    except Exception as e:
        print(f"load_df {tab} error {e}")
        return pd.DataFrame()

def clean_mfd(df):
    if df.empty: return pd.DataFrame(columns=["SKU","MFD","QTY"])
    if "SKU" not in df.columns or "QTY" not in df.columns:
        return pd.DataFrame(columns=["SKU","MFD","QTY"])
    if "MFD" not in df.columns:
        df["MFD"] = "無MFD"
    df["SKU"]=df["SKU"].astype(str).str.strip()
    df=df[df["SKU"]!=""]
    df["MFD"]=df["MFD"].astype(str).str.strip().replace({"":"無MFD","None":"無MFD","nan":"無MFD","NaT":"無MFD","NAT":"無MFD"})
    df["QTY"]=df["QTY"].astype(str).str.replace(",","").str.strip()
    df["QTY"]=pd.to_numeric(df["QTY"], errors='coerce').fillna(0)
    return df[["SKU","MFD","QTY"]]

def update_stock_sheet():
    try:
        df_m = load_df("Master")
        if df_m.empty or "SKU" not in df_m.columns: return
        df_in = load_df("Inbound"); df_out = load_df("Outbound")
        df_m["SKU"] = df_m["SKU"].astype(str).str.strip()
        df_m = df_m[df_m["SKU"]!=""]
        df_in_c = clean_mfd(df_in)
        df_out_c = clean_mfd(df_out)
        in_sum = df_in_c.groupby("SKU", as_index=False)["QTY"].sum().rename(columns={"QTY":"總入庫"}) if not df_in_c.empty else pd.DataFrame(columns=["SKU","總入庫"])
        out_sum = df_out_c.groupby("SKU", as_index=False)["QTY"].sum().rename(columns={"QTY":"總出庫"}) if not df_out_c.empty else pd.DataFrame(columns=["SKU","總出庫"])
        df_stock = pd.merge(df_m.copy(), in_sum, on="SKU", how="left")
        df_stock = pd.merge(df_stock, out_sum, on="SKU", how="left")
        df_stock["總入庫"]=pd.to_numeric(df_stock["總入庫"], errors='coerce').fillna(0)
        df_stock["總出庫"]=pd.to_numeric(df_stock["總出庫"], errors='coerce').fillna(0)
        df_stock["現有庫存"]=df_stock["總入庫"]-df_stock["總出庫"]
        # 庫存唔可以係負數，負數代表入庫未同步，修正為0並保留真實數作參考
        df_stock["現有庫存"]=df_stock["現有庫存"].apply(lambda x: x if x>=0 else 0)
        df_stock["最後更新"]=now_hk().strftime("%Y-%m-%d %H:%M:%S HKT")
        cols=["SKU","Desc.","Code","UOM","總入庫","總出庫","現有庫存","最後更新"]
        for c in cols:
            if c not in df_stock.columns: df_stock[c]=""
        df_stock=df_stock[cols]
        try: ws=get_sh().worksheet("Stock")
        except:
            get_sh().add_worksheet(title="Stock", rows=1000, cols=20)
            ws=get_sh().worksheet("Stock")
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
        if in_mfd.empty and out_mfd.empty:
            df_mfd_stock=pd.DataFrame(columns=["SKU","MFD","總入庫","總出庫","現有庫存"])
        else:
            df_mfd_stock=pd.merge(in_mfd, out_mfd, on=["SKU","MFD"], how="outer").fillna(0)
        df_mfd_stock["總入庫"]=pd.to_numeric(df_mfd_stock["總入庫"], errors='coerce').fillna(0)
        df_mfd_stock["總出庫"]=pd.to_numeric(df_mfd_stock["總出庫"], errors='coerce').fillna(0)
        df_mfd_stock["現有庫存"]=df_mfd_stock["總入庫"]-df_mfd_stock["總出庫"]
        df_mfd_stock["現有庫存"]=df_mfd_stock["現有庫存"].apply(lambda x: x if x>=0 else 0)
        df_mfd_stock["Desc."]=df_mfd_stock["SKU"].map(lambda x: master_map.get(x, {}).get("Desc.",""))
        df_mfd_stock["Code"]=df_mfd_stock["SKU"].map(lambda x: master_map.get(x, {}).get("Code",""))
        df_mfd_stock["最後更新"]=now_hk().strftime("%Y-%m-%d %H:%M:%S HKT")
        df_mfd_stock=df_mfd_stock[["SKU","Desc.","Code","MFD","總入庫","總出庫","現有庫存","最後更新"]].sort_values(["SKU","MFD"]).fillna("")
        try: ws=get_sh().worksheet("Stock_MFD")
        except:
            get_sh().add_worksheet(title="Stock_MFD", rows=2000, cols=20)
            ws=get_sh().worksheet("Stock_MFD")
        ws.clear(); ws.update([df_mfd_stock.columns.tolist()]+df_mfd_stock.astype(str).values.tolist(), value_input_option='USER_ENTERED')
    except Exception as e: print(f"MFD sync error: {e}")

def append_row(tab, row):
    get_sh().worksheet(tab).append_row(row, value_input_option='USER_ENTERED')
    st.cache_data.clear(); update_stock_sheet(); update_stock_mfd_sheet()
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

if "user" not in st.session_state: st.session_state.user=None
if not st.session_state.user:
    st.title("WMS 登入")
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
if st.sidebar.button("登出", key="logout_btn_final_v2"): st.session_state.user=None; st.cache_data.clear(); st.rerun()
st.title("WMS 永久版 - 香港時間")

df_master=load_df("Master")
if df_master.empty or "SKU" not in df_master.columns:
    st.warning("⚠️ Master分頁無資料或無SKU欄，請去SKU Master管理上傳番Import file.xlsx")
    sku_list=[]
else:
    df_master["SKU"]=df_master["SKU"].astype(str).str.strip()
    df_master=df_master[df_master["SKU"]!=""]
    sku_list=df_master["SKU"].tolist()

if role=="admin":
    tabs=st.tabs(["SKU Master管理","Master批量","入庫(單+批量)","出庫(單+批量)","總庫存","按MFD庫存"])
    tab_master, tab_bulk_master, tab_in, tab_out, tab_view, tab_mfd=tabs
else:
    tabs=st.tabs(["入庫(單+批量)","出庫(單+批量)","總庫存","按MFD庫存"])
    tab_in, tab_out, tab_view, tab_mfd=tabs

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
        st.markdown("**1. 單個入庫**")
        with st.form("in_form_final", clear_on_submit=True):
            rec_date=st.date_input("Receiving Date *必填", value=today_hk(), key="in_rec_date")
            rec_time=st.time_input("Receiving Time", value=now_hk().time(), key="in_rec_time")
            ins=st.text_input("Inspection Note # *必填", key="in_ins")
            mfd=st.date_input("MFD *必填", value=None, key="in_mfd")
            sku=st.selectbox("SKU *必填", [""]+sku_list, key="in_sku")
            qty=st.number_input("QTY *必填", min_value=0.0, step=1.0, key="in_qty")
            uom=st.selectbox("UOM *必填", ["PCS","KG","BOX"], key="in_uom")
            if st.form_submit_button("確認入庫"):
                full_dt=datetime.datetime.combine(rec_date, rec_time).strftime("%Y-%m-%d %H:%M:%S")
                mfd_str=mfd.strftime("%d/%m/%Y") if mfd else ""
                append_row("Inbound", [full_dt, ins, mfd_str, sku, qty, uom, operator])
                st.success(f"入庫成功 {full_dt} HKT"); st.balloons(); st.rerun()
    with col2:
        st.markdown("**2. 批量入庫**")
        f_in=st.file_uploader("上傳入庫Excel/CSV", type=["xlsx","csv"], key="in_bulk_final")
        if f_in:
            df_in=pd.read_csv(f_in) if f_in.name.endswith(".csv") else pd.read_excel(f_in)
            st.dataframe(df_in.head())
            if st.button("確認批量入庫", key="bulk_in_btn_final"):
                rows=[]
                for _, r in df_in.iterrows():
                    rows.append([str(r.get("Receiving Date","") or today_hk()), str(r.get("Inspection Note #","")), str(r.get("MFD","")), str(r.get("SKU","")), str(r.get("QTY","")), str(r.get("UOM","PCS") or "PCS"), operator])
                append_rows("Inbound", rows); st.success(f"已批量入庫 {len(rows)} 筆"); st.balloons(); st.rerun()

with tab_out:
    col1,col2=st.columns([1,1])
    with col1:
        st.markdown("**1. 單個出庫**")
        with st.form("out_form_final", clear_on_submit=True):
            rel_date=st.date_input("Release Date *必填", value=today_hk(), key="rel_date_f")
            rel_time=st.time_input("Release Time", value=now_hk().time(), key="rel_time_f")
            tr=st.text_input("Transfer Note # *必填", key="tr_f")
            mfd=st.date_input("MFD *必填 (出邊批)", value=None, key="mfd_out_f")
            sku=st.selectbox("SKU *必填", [""]+sku_list, key="sku_out_f")
            qty=st.number_input("QTY *必填", min_value=0.0, step=1.0, key="qty_out_f")
            uom=st.selectbox("UOM *必填", ["PCS","KG","BOX"], key="uom_out_f")
            if st.form_submit_button("確認出庫"):
                full_dt=datetime.datetime.combine(rel_date, rel_time).strftime("%Y-%m-%d %H:%M:%S")
                mfd_str=mfd.strftime("%d/%m/%Y") if mfd else ""
                append_row("Outbound", [full_dt, tr, mfd_str, sku, qty, uom, operator])
                st.success(f"出庫成功 {full_dt} HKT"); st.balloons(); st.rerun()
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
    st.subheader(f"📦 總庫存 (香港時間 {now_hk().strftime('%Y-%m-%d %H:%M')})")
    if df_master.empty or "SKU" not in df_master.columns:
        st.info("Master未有資料，無法顯示庫存")
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
        # 保證唔負數
        df_stock_view["現有庫存"]=df_stock_view["現有庫存"].apply(lambda x: x if x>=0 else 0)
        st.dataframe(df_stock_view, use_container_width=True)

with tab_mfd:
    st.subheader("📅 按MFD庫存 - 已修復")
    if df_master.empty or "SKU" not in df_master.columns:
        st.info("Master未有資料")
    else:
        df_in=load_df("Inbound"); df_out=load_df("Outbound")
        df_in_c=clean_mfd(df_in); df_out_c=clean_mfd(df_out)
        in_mfd=df_in_c.groupby(["SKU","MFD"], as_index=False)["QTY"].sum().rename(columns={"QTY":"總入庫"}) if not df_in_c.empty else pd.DataFrame(columns=["SKU","MFD","總入庫"])
        out_mfd=df_out_c.groupby(["SKU","MFD"], as_index=False)["QTY"].sum().rename(columns={"QTY":"總出庫"}) if not df_out_c.empty else pd.DataFrame(columns=["SKU","MFD","總出庫"])
        if in_mfd.empty and out_mfd.empty:
            st.info("未有入出庫MFD紀錄")
        else:
            df_mfd_stock=pd.merge(in_mfd, out_mfd, on=["SKU","MFD"], how="outer").fillna(0)
            df_mfd_stock["總入庫"]=pd.to_numeric(df_mfd_stock["總入庫"], errors='coerce').fillna(0)
            df_mfd_stock["總出庫"]=pd.to_numeric(df_mfd_stock["總出庫"], errors='coerce').fillna(0)
            df_mfd_stock["現有庫存"]=df_mfd_stock["總入庫"]-df_mfd_stock["總出庫"]
            df_mfd_stock["現有庫存"]=df_mfd_stock["現有庫存"].apply(lambda x: x if x>=0 else 0)
            df_mfd_stock=pd.merge(df_mfd_stock, df_master[["SKU","Desc.","Code"]], on="SKU", how="left")
            df_mfd_stock=df_mfd_stock[["SKU","Desc.","Code","MFD","總入庫","總出庫","現有庫存"]]
            c1,c2=st.columns([2,1])
            sku_filter=c1.selectbox("篩選SKU", ["全部"]+sorted(df_mfd_stock["SKU"].astype(str).unique().tolist()), key="mfd_sku_filter_final")
            show_zero=c2.checkbox("顯示0庫存", value=False)
            view=df_mfd_stock.copy()
            if sku_filter!="全部": view=view[view["SKU"]==sku_filter]
            if not show_zero: view=view[view["現有庫存"]!=0]
            st.success(f"找到 {len(view)} 筆MFD庫存 (總 {len(df_mfd_stock)} 筆)")
            st.dataframe(view, use_container_width=True, height=500)
