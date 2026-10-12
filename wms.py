import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
import datetime
import pytz

HKT = pytz.timezone("Asia/Hong_Kong")
def now_hk():
    return datetime.datetime.now(HKT)
def today_hk():
    return now_hk().date()

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
        return pd.DataFrame(ws.get_all_records())
    except:
        return pd.DataFrame()

def update_stock_sheet():
    try:
        df_m = load_df("Master")
        df_in = load_df("Inbound")
        df_out = load_df("Outbound")
        if df_m.empty: return
        df_m["SKU"] = df_m["SKU"].astype(str).str.strip()
        if not df_in.empty and "SKU" in df_in.columns:
            df_in["QTY"] = pd.to_numeric(df_in["QTY"], errors='coerce').fillna(0)
            df_in["SKU"] = df_in["SKU"].astype(str).str.strip()
            in_sum = df_in.groupby("SKU")["QTY"].sum().reset_index().rename(columns={"QTY":"總入庫"})
        else: in_sum = pd.DataFrame(columns=["SKU","總入庫"])
        if not df_out.empty and "SKU" in df_out.columns:
            df_out["QTY"] = pd.to_numeric(df_out["QTY"], errors='coerce').fillna(0)
            df_out["SKU"] = df_out["SKU"].astype(str).str.strip()
            out_sum = df_out.groupby("SKU")["QTY"].sum().reset_index().rename(columns={"QTY":"總出庫"})
        else: out_sum = pd.DataFrame(columns=["SKU","總出庫"])
        df_stock = pd.merge(df_m, in_sum, on="SKU", how="left")
        df_stock = pd.merge(df_stock, out_sum, on="SKU", how="left")
        df_stock["總入庫"] = pd.to_numeric(df_stock["總入庫"], errors='coerce').fillna(0)
        df_stock["總出庫"] = pd.to_numeric(df_stock["總出庫"], errors='coerce').fillna(0)
        df_stock["現有庫存"] = df_stock["總入庫"] - df_stock["總出庫"]
        df_stock["最後更新"] = now_hk().strftime("%Y-%m-%d %H:%M:%S HKT")
        cols = ["SKU","Desc.","Code","UOM","總入庫","總出庫","現有庫存","最後更新"]
        for c in cols:
            if c not in df_stock.columns: df_stock[c]=""
        df_stock = df_stock[cols].fillna("").astype(str)
        try: ws = get_sh().worksheet("Stock")
        except:
            get_sh().add_worksheet(title="Stock", rows=1000, cols=20)
            ws = get_sh().worksheet("Stock")
        ws.clear()
        ws.update([df_stock.columns.tolist()] + df_stock.values.tolist(), value_input_option='USER_ENTERED')
    except Exception as e: print(f"Stock error: {e}")

def update_stock_mfd_sheet():
    try:
        df_m = load_df("Master")
        df_in = load_df("Inbound")
        df_out = load_df("Outbound")
        if df_m.empty: return
        df_m["SKU"] = df_m["SKU"].astype(str).str.strip()
        master_map = df_m.set_index("SKU")[["Desc.","Code"]].to_dict('index')
        def clean_mfd(df):
            if df.empty or "MFD" not in df.columns or "SKU" not in df.columns: return pd.DataFrame(columns=["SKU","MFD","QTY"])
            df["SKU"] = df["SKU"].astype(str).str.strip()
            df["MFD"] = df["MFD"].astype(str).str.strip().replace({"": "無MFD", "None": "無MFD", "nan": "無MFD"})
            df["QTY"] = pd.to_numeric(df["QTY"], errors='coerce').fillna(0)
            return df[["SKU","MFD","QTY"]]
        df_in_c = clean_mfd(df_in); df_out_c = clean_mfd(df_out)
        in_mfd = df_in_c.groupby(["SKU","MFD"])["QTY"].sum().reset_index().rename(columns={"QTY":"總入庫"}) if not df_in_c.empty else pd.DataFrame(columns=["SKU","MFD","總入庫"])
        out_mfd = df_out_c.groupby(["SKU","MFD"])["QTY"].sum().reset_index().rename(columns={"QTY":"總出庫"}) if not df_out_c.empty else pd.DataFrame(columns=["SKU","MFD","總出庫"])
        df_mfd_stock = pd.merge(in_mfd, out_mfd, on=["SKU","MFD"], how="outer")
        df_mfd_stock["總入庫"] = pd.to_numeric(df_mfd_stock["總入庫"], errors='coerce').fillna(0)
        df_mfd_stock["總出庫"] = pd.to_numeric(df_mfd_stock["總出庫"], errors='coerce').fillna(0)
        df_mfd_stock["現有庫存"] = df_mfd_stock["總入庫"] - df_mfd_stock["總出庫"]
        df_mfd_stock["Desc."] = df_mfd_stock["SKU"].map(lambda x: master_map.get(x, {}).get("Desc.",""))
        df_mfd_stock["Code"] = df_mfd_stock["SKU"].map(lambda x: master_map.get(x, {}).get("Code",""))
        df_mfd_stock["最後更新"] = now_hk().strftime("%Y-%m-%d %H:%M:%S HKT")
        df_mfd_stock = df_mfd_stock[["SKU","Desc.","Code","MFD","總入庫","總出庫","現有庫存","最後更新"]].sort_values(["SKU","MFD"]).fillna("").astype(str)
        try: ws = get_sh().worksheet("Stock_MFD")
        except:
            get_sh().add_worksheet(title="Stock_MFD", rows=2000, cols=20)
            ws = get_sh().worksheet("Stock_MFD")
        ws.clear()
        ws.update([df_mfd_stock.columns.tolist()] + df_mfd_stock.values.tolist(), value_input_option='USER_ENTERED')
    except Exception as e: print(f"Stock_MFD error: {e}")

def append_row(tab, row):
    get_sh().worksheet(tab).append_row(row, value_input_option='USER_ENTERED')
    st.cache_data.clear(); update_stock_sheet(); update_stock_mfd_sheet()

def append_rows(tab, rows):
    if rows:
        get_sh().worksheet(tab).append_rows(rows, value_input_option='USER_ENTERED')
        st.cache_data.clear(); update_stock_sheet(); update_stock_mfd_sheet()

def overwrite_master(df):
    ws = get_sh().worksheet("Master")
    df = df.fillna("")
    for col in df.columns: df[col] = df[col].astype(str).replace({"None":"","nan":"","NaT":""})
    if "UOM" in df.columns: df["UOM"] = df["UOM"].apply(lambda x: "PCS" if x.strip()=="" else x.strip())
    for c in ["SKU","Desc.","Code","UOM"]:
        if c not in df.columns: df[c]=""
    df = df[["SKU","Desc.","Code","UOM"]]
    ws.clear(); ws.update([df.columns.tolist()] + df.values.tolist(), value_input_option='USER_ENTERED')
    st.cache_data.clear(); update_stock_sheet(); update_stock_mfd_sheet()

if "user" not in st.session_state: st.session_state.user = None
if not st.session_state.user:
    st.title("WMS 登入")
    df_users = load_df("Users")
    if df_users.empty: df_users = pd.DataFrame([{"username":"admin","password":"admin123","name":"Admin","role":"admin"}])
    df_users["username"] = df_users["username"].astype(str).str.strip()
    df_users["password"] = df_users["password"].astype(str).str.strip()
    u = st.text_input("用戶名", key="login_u"); p = st.text_input("密碼", type="password", key="login_p")
    if st.button("登入", key="login_btn_final"):
        m = df_users[(df_users["username"]==str(u).strip()) & (df_users["password"]==str(p).strip())]
        if not m.empty: st.session_state.user = m.iloc[0].to_dict(); st.rerun()
        else: st.error("帳號或密碼錯誤")
    st.stop()

user = st.session_state.user
role = str(user.get("role","staff")).lower()
operator = user.get("username","unknown")
st.sidebar.write(f"👤 {user['name']} | {role} | {now_hk().strftime('%H:%M HKT')}")
if st.sidebar.button("登出", key="logout_btn_final_v2"):
    st.session_state.user=None; st.cache_data.clear(); st.rerun()

st.title("WMS 永久版 - 香港時間")
df_master = load_df("Master")
sku_list = df_master["SKU"].astype(str).tolist() if not df_master.empty else []

if role=="admin":
    tabs = st.tabs(["SKU Master管理", "Master批量", "入庫(單+批量)", "出庫(單+批量)", "總庫存", "按MFD庫存", "Sheet預覽"])
    tab_master, tab_bulk_master, tab_in, tab_out, tab_view, tab_mfd, tab_preview = tabs
else:
    tabs = st.tabs(["入庫(單+批量)", "出庫(單+批量)", "總庫存", "按MFD庫存"])
    tab_in, tab_out, tab_view, tab_mfd = tabs

if role=="admin":
    with tab_master:
        st.dataframe(df_master, use_container_width=True, height=350)
        with st.form("add_sku_final", clear_on_submit=True):
            c1,c2,c3,c4 = st.columns(4)
            sku = c1.text_input("SKU *必填"); desc = c2.text_input("Desc. *必填"); code = c3.text_input("Code"); uom = c4.selectbox("UOM *必填", ["PCS","KG","BOX"])
            if st.form_submit_button("新增SKU"):
                append_row("Master", [sku, desc, code, uom]); st.success(f"{sku} 已新增"); st.rerun()
    with tab_bulk_master:
        f = st.file_uploader("上傳 Import file.xlsx", type=["xlsx","csv"], key="master_up_final")
        if f:
            df_up = pd.read_csv(f) if f.name.endswith(".csv") else pd.read_excel(f, sheet_name="Master" if "Master" in pd.ExcelFile(f).sheet_names else 0)
            st.dataframe(df_up.head())
            if st.button("覆蓋上傳到Master", key="master_overwrite_final"):
                overwrite_master(df_up); st.success(f"已上傳 {len(df_up)} 個SKU"); st.balloons(); st.rerun()

df_master = load_df("Master")
sku_list = df_master["SKU"].astype(str).tolist() if not df_master.empty else []

with tab_in:
    st.subheader("入庫 Receiving")
    col1, col2 = st.columns([1,1])
    with col1:
        st.markdown("**1. 單個入庫**")
        with st.form("in_form_final", clear_on_submit=True):
            rec_date = st.date_input("Receiving Date *必填", value=today_hk(), key="in_rec_date")
            rec_time = st.time_input("Receiving Time", value=now_hk().time(), key="in_rec_time")
            ins = st.text_input("Inspection Note # *必填", key="in_ins")
            mfd = st.date_input("MFD *必填", value=None, key="in_mfd")
            sku = st.selectbox("SKU *必填", [""]+sku_list, key="in_sku")
            qty = st.number_input("QTY *必填", min_value=0.0, step=1.0, key="in_qty")
            uom = st.selectbox("UOM *必填", ["PCS","KG","BOX"], key="in_uom")
            if st.form_submit_button("確認入庫"):
                full_dt = datetime.datetime.combine(rec_date, rec_time).strftime("%Y-%m-%d %H:%M:%S")
                mfd_str = mfd.strftime("%d/%m/%Y") if mfd else ""
                append_row("Inbound", [full_dt, ins, mfd_str, sku, qty, uom, operator])
                st.success(f"入庫成功 {full_dt} HKT"); st.balloons(); st.rerun()
    with col2:
        st.markdown("**2. 批量入庫**")
        f_in = st.file_uploader("上傳入庫Excel/CSV", type=["xlsx","csv"], key="in_bulk_final")
        if f_in:
            df_in = pd.read_csv(f_in) if f_in.name.endswith(".csv") else pd.read_excel(f_in)
            df_in = df_in.fillna(""); st.dataframe(df_in.head())
            if st.button("確認批量入庫", key="bulk_in_btn_final"):
                rows=[]
                for _, r in df_in.iterrows():
                    rows.append([str(r.get("Receiving Date","") or today_hk()), str(r.get("Inspection Note #","")), str(r.get("MFD","")), str(r.get("SKU","")), str(r.get("QTY","")), str(r.get("UOM","PCS") or "PCS"), operator])
                append_rows("Inbound", rows); st.success(f"已批量入庫 {len(rows)} 筆"); st.balloons(); st.rerun()

with tab_out:
    st.subheader("出庫 Release")
    col1, col2 = st.columns([1,1])
    with col1:
        st.markdown("**1. 單個出庫**")
        with st.form("out_form_final", clear_on_submit=True):
            rel_date = st.date_input("Release Date *必填", value=today_hk(), key="rel_date_f")
            rel_time = st.time_input("Release Time", value=now_hk().time(), key="rel_time_f")
            tr = st.text_input("Transfer Note # *必填", key="tr_f")
            mfd = st.date_input("MFD *必填 (出邊批)", value=None, key="mfd_out_f")
            sku = st.selectbox("SKU *必填", [""]+sku_list, key="sku_out_f")
            qty = st.number_input("QTY *必填", min_value=0.0, step=1.0, key="qty_out_f")
            uom = st.selectbox("UOM *必填", ["PCS","KG","BOX"], key="uom_out_f")
            if st.form_submit_button("確認出庫"):
                full_dt = datetime.datetime.combine(rel_date, rel_time).strftime("%Y-%m-%d %H:%M:%S")
                mfd_str = mfd.strftime("%d/%m/%Y") if mfd else ""
                append_row("Outbound", [full_dt, tr, mfd_str, sku, qty, uom, operator])
                st.success(f"出庫成功 {full_dt} HKT"); st.balloons(); st.rerun()
    with col2:
        st.markdown("**2. 批量出庫**")
        f_out = st.file_uploader("上傳出庫Excel/CSV", type=["xlsx","csv"], key="out_bulk_final")
        if f_out:
            df_out = pd.read_csv(f_out) if f_out.name.endswith(".csv") else pd.read_excel(f_out)
            df_out = df_out.fillna(""); st.dataframe(df_out.head())
            if st.button("確認批量出庫", key="bulk_out_btn_final"):
                rows=[]
                for _, r in df_out.iterrows():
                    rows.append([str(r.get("Release Date","") or today_hk()), str(r.get("Transfer Note #","")), str(r.get("MFD","")), str(r.get("SKU","")), str(r.get("QTY","")), str(r.get("UOM","PCS") or "PCS"), operator])
                append_rows("Outbound", rows); st.success(f"已批量出庫 {len(rows)} 筆"); st.balloons(); st.rerun()

with tab_view:
    st.subheader(f"📦 總庫存 (香港時間 {now_hk().strftime('%Y-%m-%d %H:%M')})")
    df_in = load_df("Inbound"); df_out = load_df("Outbound")
    if not df_in.empty:
        df_in["QTY"] = pd.to_numeric(df_in["QTY"], errors='coerce').fillna(0)
        in_sum = df_in.groupby(df_in["SKU"].astype(str))["QTY"].sum().reset_index().rename(columns={"QTY":"總入庫"})
    else: in_sum = pd.DataFrame(columns=["SKU","總入庫"])
    if not df_out.empty:
        df_out["QTY"] = pd.to_numeric(df_out["QTY"], errors='coerce').fillna(0)
        out_sum = df_out.groupby(df_out["SKU"].astype(str))["QTY"].sum().reset_index().rename(columns={"QTY":"總出庫"})
    else: out_sum = pd.DataFrame(columns=["SKU","總出庫"])
    df_stock = pd.merge(df_master, in_sum, left_on=df_master["SKU"].astype(str), right_on="SKU", how="left")
    df_stock = df_stock.drop(columns=["SKU_y"], errors='ignore').rename(columns={"SKU_x":"SKU"})
    df_stock = pd.merge(df_stock, out_sum, left_on=df_stock["SKU"].astype(str), right_on="SKU", how="left")
    df_stock = df_stock.drop(columns=["SKU_y"], errors='ignore').rename(columns={"SKU_x":"SKU"})
    df_stock["總入庫"] = pd.to_numeric(df_stock["總入庫"], errors='coerce').fillna(0)
    df_stock["總出庫"] = pd.to_numeric(df_stock["總出庫"], errors='coerce').fillna(0)
    df_stock["現有庫存"] = df_stock["總入庫"] - df_stock["總出庫"]
    st.dataframe(df_stock, use_container_width=True)

with tab_mfd:
    st.subheader("📅 按MFD庫存")
    df_in = load_df("Inbound"); df_out = load_df("Outbound")
    def clean_mfd(df):
        if df.empty or "MFD" not in df.columns or "SKU" not in df.columns: return pd.DataFrame(columns=["SKU","MFD","QTY"])
        df["SKU"] = df["SKU"].astype(str).str.strip()
        df["MFD"] = df["MFD"].astype(str).str.strip().replace({"": "無MFD", "None": "無MFD", "nan": "無MFD"})
        df["QTY"] = pd.to_numeric(df["QTY"], errors='coerce').fillna(0)
        return df[["SKU","MFD","QTY"]]
    df_in_c = clean_mfd(df_in); df_out_c = clean_mfd(df_out)
    in_mfd = df_in_c.groupby(["SKU","MFD"])["QTY"].sum().reset_index().rename(columns={"QTY":"總入庫"}) if not df_in_c.empty else pd.DataFrame(columns=["SKU","MFD","總入庫"])
    out_mfd = df_out_c.groupby(["SKU","MFD"])["QTY"].sum().reset_index().rename(columns={"QTY":"總出庫"}) if not df_out_c.empty else pd.DataFrame(columns=["SKU","MFD","總出庫"])
    df_mfd_stock = pd.merge(in_mfd, out_mfd, on=["SKU","MFD"], how="outer")
    df_mfd_stock["總入庫"] = pd.to_numeric(df_mfd_stock["總入庫"], errors='coerce').fillna(0)
    df_mfd_stock["總出庫"] = pd.to_numeric(df_mfd_stock["總出庫"], errors='coerce').fillna(0)
    df_mfd_stock["現有庫存"] = df_mfd_stock["總入庫"] - df_mfd_stock["總出庫"]
    df_mfd_stock = pd.merge(df_mfd_stock, df_master[["SKU","Desc.","Code"]], on="SKU", how="left")
    df_mfd_stock = df_mfd_stock[["SKU","Desc.","Code","MFD","總入庫","總出庫","現有庫存"]]
    sku_filter = st.selectbox("篩選SKU", ["全部"]+sorted(df_mfd_stock["SKU"].astype(str).unique().tolist()), key="mfd_sku_filter")
    view = df_mfd_stock.copy()
    if sku_filter!="全部": view = view[view["SKU"]==sku_filter]
    view = view[view["現有庫存"]>0]
    st.dataframe(view, use_container_width=True, height=500)

if role=="admin":
    with tab_preview:
        st.dataframe(load_df("Stock"), use_container_width=True, height=300)
        st.dataframe(load_df("Stock_MFD"), use_container_width=True, height=300)
        if st.button("🔄 強制同步全部", key="sync_all"):
            update_stock_sheet(); update_stock_mfd_sheet(); st.cache_data.clear(); st.rerun()
