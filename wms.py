import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
import datetime

st.set_page_config(page_title="WMS - Master版", layout="wide")

@st.cache_resource
def get_client():
    scopes = ["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scopes)
    return gspread.authorize(creds)

def get_sh():
    return get_client().open_by_key(st.secrets["connections"]["gsheets"]["spreadsheet"])

@st.cache_data(ttl=5) # 5秒自動刷新，解決入庫後睇唔到問題
def load_df(tab):
    try:
        ws = get_sh().worksheet(tab)
        return pd.DataFrame(ws.get_all_records())
    except:
        return pd.DataFrame()

def append_row(tab, row):
    get_sh().worksheet(tab).append_row(row, value_input_option='USER_ENTERED')
    st.cache_data.clear() # 寫入後即清cache

def append_rows(tab, rows):
    if rows:
        get_sh().worksheet(tab).append_rows(rows, value_input_option='USER_ENTERED')
        st.cache_data.clear()

def overwrite_master(df):
    ws = get_sh().worksheet("Master")
    df = df.fillna("")
    for col in df.columns:
        df[col] = df[col].astype(str).replace({"None":"","nan":"","NaT":""})
    if "UOM" in df.columns:
        df["UOM"] = df["UOM"].apply(lambda x: "PCS" if x.strip()=="" else x.strip())
    df = df[["SKU","Desc.","Code","UOM"]]
    ws.clear()
    ws.update([df.columns.tolist()] + df.values.tolist(), value_input_option='USER_ENTERED')
    st.cache_data.clear()

# --- LOGIN ---
if "user" not in st.session_state:
    st.session_state.user = None

if not st.session_state.user:
    st.title("WMS 登入")
    df_users = load_df("Users")
    if df_users.empty:
        df_users = pd.DataFrame([{"username":"admin","password":"admin123","name":"Admin","role":"admin"}])
    df_users["username"] = df_users["username"].astype(str).str.strip()
    df_users["password"] = df_users["password"].astype(str).str.strip()
    u = st.text_input("用戶名")
    p = st.text_input("密碼", type="password")
    if st.button("登入"):
        m = df_users[(df_users["username"]==str(u).strip()) & (df_users["password"]==str(p).strip())]
        if not m.empty:
            st.session_state.user = m.iloc[0].to_dict()
            st.rerun()
        else:
            st.error("錯密碼")
    st.stop()

user = st.session_state.user
role = str(user.get("role","staff")).lower()
operator = user.get("username","unknown")
st.sidebar.write(f"👤 {user['name']} | {role}")
if st.sidebar.button("登出"):
    st.session_state.user=None
    st.cache_data.clear()
    st.rerun()

st.title("WMS 永久版")

df_master = load_df("Master")
sku_list = df_master["SKU"].astype(str).tolist() if not df_master.empty else []

if role=="admin":
    tab_master, tab_bulk_master, tab_in, tab_out, tab_view = st.tabs(["SKU Master管理", "Master批量", "入庫(單+批量)", "出庫(單+批量)", "紀錄"])
else:
    tab_in, tab_out, tab_view = st.tabs(["入庫(單+批量)", "出庫(單+批量)", "查看"])

if role=="admin":
    with tab_master:
        st.subheader("SKU Master - 逐個增加")
        st.dataframe(df_master, use_container_width=True)
        with st.form("add_sku"):
            c1,c2,c3,c4 = st.columns(4)
            sku = c1.text_input("SKU *必填")
            desc = c2.text_input("Desc. *必填")
            code = c3.text_input("Code")
            uom = c4.selectbox("UOM *必填", ["PCS","KG","BOX"])
            if st.form_submit_button("新增SKU"):
                append_row("Master", [sku, desc, code, uom])
                st.success(f"{sku} 已新增"); st.rerun()
    with tab_bulk_master:
        st.subheader("Master 批量上傳")
        f = st.file_uploader("上傳 Import file.xlsx", type=["xlsx","csv"], key="master_up")
        if f:
            df_up = pd.read_csv(f) if f.name.endswith(".csv") else pd.read_excel(f, sheet_name="Master" if "Master" in pd.ExcelFile(f).sheet_names else 0)
            st.dataframe(df_up.head())
            if st.button("覆蓋上傳到Master"):
                overwrite_master(df_up)
                st.success(f"已上傳 {len(df_up)} 個SKU"); st.balloons(); st.rerun()

df_master = load_df("Master")
sku_list = df_master["SKU"].astype(str).tolist() if not df_master.empty else []

# --- 入庫：單個 + 批量 ---
with tab_in:
    st.subheader("入庫 Receiving - 日曆揀日期")
    col1, col2 = st.columns([1,1])
    with col1:
        st.markdown("**1. 單個入庫**")
        with st.form("in_form"):
            rec_date = st.date_input("Receiving Date *必填", value=datetime.date.today())
            rec_time = st.time_input("Receiving Time", value=datetime.datetime.now().time())
            ins = st.text_input("Inspection Note # *必填")
            mfd = st.date_input("MFD *必填 (日曆揀)", value=None)
            sku = st.selectbox("SKU *必填", [""]+sku_list)
            qty = st.number_input("QTY *必填", min_value=0.0, step=1.0)
            uom = st.selectbox("UOM *必填", ["PCS","KG","BOX"])
            if st.form_submit_button("確認入庫"):
                full_dt = datetime.datetime.combine(rec_date, rec_time).strftime("%Y-%m-%d %H:%M:%S")
                append_row("Inbound", [full_dt, ins, mfd.strftime("%d/%m/%Y") if mfd else "", sku, qty, uom, operator])
                st.success(f"入庫成功 - 操作員: {operator}"); st.balloons()
                st.rerun()
    with col2:
        st.markdown("**2. 批量入庫上傳**")
        st.download_button("下載入庫範本", data="Receiving Date,Inspection Note #,MFD,SKU,QTY,UOM\n2026-05-13,IN-001,01/01/2026,SPBMC042,10,PCS\n", file_name="Inbound_Template.csv")
        f_in = st.file_uploader("上傳入庫Excel/CSV", type=["xlsx","csv"], key="in_bulk")
        if f_in:
            df_in = pd.read_csv(f_in) if f_in.name.endswith(".csv") else pd.read_excel(f_in)
            df_in = df_in.fillna("")
            st.dataframe(df_in.head())
            if st.button("確認批量入庫"):
                rows=[]
                for _, r in df_in.iterrows():
                    rd = str(r.get("Receiving Date","") or datetime.date.today().strftime("%Y-%m-%d"))
                    mfd_str = str(r.get("MFD",""))
                    rows.append([rd, str(r.get("Inspection Note #","")), mfd_str, str(r.get("SKU","")), str(r.get("QTY","")), str(r.get("UOM","PCS") or "PCS"), operator])
                append_rows("Inbound", rows)
                st.success(f"已批量入庫 {len(rows)} 筆 - 操作員: {operator}"); st.balloons()
                st.rerun()

# --- 出庫：單個 + 批量 ---
with tab_out:
    st.subheader("出庫 Release - 日曆揀日期")
    col1, col2 = st.columns([1,1])
    with col1:
        st.markdown("**1. 單個出庫**")
        with st.form("out_form"):
            rel_date = st.date_input("Release Date *必填", value=datetime.date.today(), key="rel_date")
            rel_time = st.time_input("Release Time", value=datetime.datetime.now().time(), key="rel_time")
            tr = st.text_input("Transfer Note # *必填")
            mfd = st.date_input("MFD *必填", value=None, key="mfd_out")
            sku = st.selectbox("SKU *必填", [""]+sku_list, key="sku_out")
            qty = st.number_input("QTY *必填", min_value=0.0, step=1.0, key="qty_out")
            uom = st.selectbox("UOM *必填", ["PCS","KG","BOX"], key="uom_out")
            if st.form_submit_button("確認出庫"):
                full_dt = datetime.datetime.combine(rel_date, rel_time).strftime("%Y-%m-%d %H:%M:%S")
                append_row("Outbound", [full_dt, tr, mfd.strftime("%d/%m/%Y") if mfd else "", sku, qty, uom, operator])
                st.success(f"出庫成功 - 操作員: {operator}"); st.balloons()
                st.rerun()
    with col2:
        st.markdown("**2. 批量出庫上傳**")
        st.download_button("下載出庫範本", data="Release Date,Transfer Note #,MFD,SKU,QTY,UOM\n2026-05-13,TR-001,01/01/2026,SPBMC042,5,PCS\n", file_name="Outbound_Template.csv", key="dl_out")
        f_out = st.file_uploader("上傳出庫Excel/CSV", type=["xlsx","csv"], key="out_bulk")
        if f_out:
            df_out = pd.read_csv(f_out) if f_out.name.endswith(".csv") else pd.read_excel(f_out)
            df_out = df_out.fillna("")
            st.dataframe(df_out.head())
            if st.button("確認批量出庫"):
                rows=[]
                for _, r in df_out.iterrows():
                    rd = str(r.get("Release Date","") or datetime.date.today().strftime("%Y-%m-%d"))
                    mfd_str = str(r.get("MFD",""))
                    rows.append([rd, str(r.get("Transfer Note #","")), mfd_str, str(r.get("SKU","")), str(r.get("QTY","")), str(r.get("UOM","PCS") or "PCS"), operator])
                append_rows("Outbound", rows)
                st.success(f"已批量出庫 {len(rows)} 筆 - 操作員: {operator}"); st.balloons()
                st.rerun()

with tab_view:
    st.subheader("📦 即時庫存結餘 = 入庫 - 出庫")
    df_in = load_df("Inbound")
    df_out = load_df("Outbound")

    if df_in.empty and df_out.empty:
        st.info("未有入出庫紀錄 - 請先做入庫")
        st.write("除錯：檢查 Inbound / Outbound 分頁名係咪串錯，大小階要一樣")
    else:
        if not df_in.empty:
            df_in["QTY"] = pd.to_numeric(df_in["QTY"], errors='coerce').fillna(0)
            in_sum = df_in.groupby("SKU")["QTY"].sum().reset_index().rename(columns={"QTY":"總入庫"})
        else:
            in_sum = pd.DataFrame(columns=["SKU","總入庫"])

        if not df_out.empty:
            df_out["QTY"] = pd.to_numeric(df_out["QTY"], errors='coerce').fillna(0)
            out_sum = df_out.groupby("SKU")["QTY"].sum().reset_index().rename(columns={"QTY":"總出庫"})
        else:
            out_sum = pd.DataFrame(columns=["SKU","總出庫"])

        df_stock = pd.merge(df_master, in_sum, on="SKU", how="left")
        df_stock = pd.merge(df_stock, out_sum, on="SKU", how="left")
        df_stock["總入庫"] = df_stock["總入庫"].fillna(0)
        df_stock["總出庫"] = df_stock["總出庫"].fillna(0)
        df_stock["現有庫存"] = df_stock["總入庫"] - df_stock["總出庫"]

        st.dataframe(df_stock.style.map(lambda x: 'color: red; font-weight: bold' if x < 0 else '', subset=['現有庫存']), use_container_width=True)

        st.divider()
        col_a, col_b = st.columns(2)
        with col_a:
            st.write(f"Inbound 明細 (共 {len(df_in)} 筆)"); st.dataframe(df_in.tail(20), use_container_width=True, height=300)
        with col_b:
            st.write(f"Outbound 明細 (共 {len(df_out)} 筆)"); st.dataframe(df_out.tail(20), use_container_width=True, height=300)

        if st.button("🔄 手動刷新庫存"):
            st.cache_data.clear()
            st.rerun()
if "user" not in st.session_state:
    st.session_state.user = None

if not st.session_state.user:
    st.title("WMS 登入")
    df_users = load_df("Users")
    if df_users.empty:
        df_users = pd.DataFrame([{"username":"admin","password":"admin123","name":"Admin","role":"admin"}])
    df_users["username"] = df_users["username"].astype(str).str.strip()
    df_users["password"] = df_users["password"].astype(str).str.strip()
    u = st.text_input("用戶名")
    p = st.text_input("密碼", type="password")
    if st.button("登入"):
        m = df_users[(df_users["username"]==str(u).strip()) & (df_users["password"]==str(p).strip())]
        if not m.empty:
            st.session_state.user = m.iloc[0].to_dict()
            st.rerun()
        else:
            st.error("錯密碼")
    st.stop()

user = st.session_state.user
role = str(user.get("role","staff")).lower()
operator = user.get("username","unknown") # 用嚟記錄係邊個做
st.sidebar.write(f"👤 {user['name']} | {role}")
if st.sidebar.button("登出"):
    st.session_state.user=None
    st.rerun()

st.title("WMS 永久版")

df_master = load_df("Master")
sku_list = df_master["SKU"].astype(str).tolist() if not df_master.empty else []

if role=="admin":
    tab_master, tab_bulk_master, tab_in, tab_out, tab_view = st.tabs(["SKU Master管理", "Master批量", "入庫(單+批量)", "出庫(單+批量)", "紀錄"])
else:
    tab_in, tab_out, tab_view = st.tabs(["入庫(單+批量)", "出庫(單+批量)", "查看"])

if role=="admin":
    with tab_master:
        st.subheader("SKU Master - 逐個增加")
        st.dataframe(df_master, use_container_width=True)
        with st.form("add_sku"):
            c1,c2,c3,c4 = st.columns(4)
            sku = c1.text_input("SKU *必填")
            desc = c2.text_input("Desc. *必填")
            code = c3.text_input("Code")
            uom = c4.selectbox("UOM *必填", ["PCS","KG","BOX"])
            if st.form_submit_button("新增SKU"):
                append_row("Master", [sku, desc, code, uom])
                st.success(f"{sku} 已新增"); st.rerun()
    with tab_bulk_master:
        st.subheader("Master 批量上傳")
        f = st.file_uploader("上傳 Import file.xlsx", type=["xlsx","csv"], key="master_up")
        if f:
            df_up = pd.read_csv(f) if f.name.endswith(".csv") else pd.read_excel(f, sheet_name="Master" if "Master" in pd.ExcelFile(f).sheet_names else 0)
            st.dataframe(df_up.head())
            if st.button("覆蓋上傳到Master"):
                overwrite_master(df_up)
                st.success(f"已上傳 {len(df_up)} 個SKU"); st.balloons(); st.rerun()

df_master = load_df("Master")
sku_list = df_master["SKU"].astype(str).tolist() if not df_master.empty else []

# --- 入庫：單個 + 批量 ---
with tab_in:
    st.subheader("入庫 Receiving - 日曆揀日期")
    col1, col2 = st.columns([1,1])
    with col1:
        st.markdown("**1. 單個入庫**")
        with st.form("in_form"):
            rec_date = st.date_input("Receiving Date *必填", value=datetime.date.today())
            rec_time = st.time_input("Receiving Time", value=datetime.datetime.now().time())
            ins = st.text_input("Inspection Note # *必填")
            mfd = st.date_input("MFD *必填 (日曆揀)", value=None)
            sku = st.selectbox("SKU *必填", [""]+sku_list)
            qty = st.number_input("QTY *必填", min_value=0.0, step=1.0)
            uom = st.selectbox("UOM *必填", ["PCS","KG","BOX"])
            if st.form_submit_button("確認入庫"):
                full_dt = datetime.datetime.combine(rec_date, rec_time).strftime("%Y-%m-%d %H:%M:%S")
                append_row("Inbound", [full_dt, ins, mfd.strftime("%d/%m/%Y") if mfd else "", sku, qty, uom, operator])
                st.success(f"入庫成功 - 操作員: {operator}")
    with col2:
        st.markdown("**2. 批量入庫上傳**")
        st.download_button("下載入庫範本", data="Receiving Date,Inspection Note #,MFD,SKU,QTY,UOM\n2026-05-13,IN-001,01/01/2026,SPBMC042,10,PCS\n", file_name="Inbound_Template.csv")
        f_in = st.file_uploader("上傳入庫Excel/CSV", type=["xlsx","csv"], key="in_bulk")
        if f_in:
            df_in = pd.read_csv(f_in) if f_in.name.endswith(".csv") else pd.read_excel(f_in)
            df_in = df_in.fillna("")
            st.dataframe(df_in.head())
            if st.button("確認批量入庫"):
                rows=[]
                for _, r in df_in.iterrows():
                    rd = str(r.get("Receiving Date","") or datetime.date.today().strftime("%Y-%m-%d"))
                    mfd_str = str(r.get("MFD",""))
                    rows.append([rd, str(r.get("Inspection Note #","")), mfd_str, str(r.get("SKU","")), str(r.get("QTY","")), str(r.get("UOM","PCS") or "PCS"), operator])
                append_rows("Inbound", rows)
                st.success(f"已批量入庫 {len(rows)} 筆 - 操作員: {operator}"); st.balloons()

# --- 出庫：單個 + 批量 ---
with tab_out:
    st.subheader("出庫 Release - 日曆揀日期")
    col1, col2 = st.columns([1,1])
    with col1:
        st.markdown("**1. 單個出庫**")
        with st.form("out_form"):
            rel_date = st.date_input("Release Date *必填", value=datetime.date.today(), key="rel_date")
            rel_time = st.time_input("Release Time", value=datetime.datetime.now().time(), key="rel_time")
            tr = st.text_input("Transfer Note # *必填")
            mfd = st.date_input("MFD *必填", value=None, key="mfd_out")
            sku = st.selectbox("SKU *必填", [""]+sku_list, key="sku_out")
            qty = st.number_input("QTY *必填", min_value=0.0, step=1.0, key="qty_out")
            uom = st.selectbox("UOM *必填", ["PCS","KG","BOX"], key="uom_out")
            if st.form_submit_button("確認出庫"):
                full_dt = datetime.datetime.combine(rel_date, rel_time).strftime("%Y-%m-%d %H:%M:%S")
                append_row("Outbound", [full_dt, tr, mfd.strftime("%d/%m/%Y") if mfd else "", sku, qty, uom, operator])
                st.success(f"出庫成功 - 操作員: {operator}")
    with col2:
        st.markdown("**2. 批量出庫上傳**")
        st.download_button("下載出庫範本", data="Release Date,Transfer Note #,MFD,SKU,QTY,UOM\n2026-05-13,TR-001,01/01/2026,SPBMC042,5,PCS\n", file_name="Outbound_Template.csv", key="dl_out")
        f_out = st.file_uploader("上傳出庫Excel/CSV", type=["xlsx","csv"], key="out_bulk")
        if f_out:
            df_out = pd.read_csv(f_out) if f_out.name.endswith(".csv") else pd.read_excel(f_out)
            df_out = df_out.fillna("")
            st.dataframe(df_out.head())
            if st.button("確認批量出庫"):
                rows=[]
                for _, r in df_out.iterrows():
                    rd = str(r.get("Release Date","") or datetime.date.today().strftime("%Y-%m-%d"))
                    mfd_str = str(r.get("MFD",""))
                    rows.append([rd, str(r.get("Transfer Note #","")), mfd_str, str(r.get("SKU","")), str(r.get("QTY","")), str(r.get("UOM","PCS") or "PCS"), operator])
                append_rows("Outbound", rows)
                st.success(f"已批量出庫 {len(rows)} 筆 - 操作員: {operator}"); st.balloons()

with tab_view:
    st.subheader("📦 即時庫存結餘 = 入庫 - 出庫")
    df_in = load_df("Inbound")
    df_out = load_df("Outbound")

    if df_in.empty and df_out.empty:
        st.info("未有入出庫紀錄")
    else:
        if not df_in.empty:
            df_in["QTY"] = pd.to_numeric(df_in["QTY"], errors='coerce').fillna(0)
            in_sum = df_in.groupby("SKU")["QTY"].sum().reset_index().rename(columns={"QTY":"總入庫"})
        else:
            in_sum = pd.DataFrame(columns=["SKU","總入庫"])

        if not df_out.empty:
            df_out["QTY"] = pd.to_numeric(df_out["QTY"], errors='coerce').fillna(0)
            out_sum = df_out.groupby("SKU")["QTY"].sum().reset_index().rename(columns={"QTY":"總出庫"})
        else:
            out_sum = pd.DataFrame(columns=["SKU","總出庫"])

        df_stock = pd.merge(df_master, in_sum, on="SKU", how="left")
        df_stock = pd.merge(df_stock, out_sum, on="SKU", how="left")
        df_stock["總入庫"] = df_stock["總入庫"].fillna(0)
        df_stock["總出庫"] = df_stock["總出庫"].fillna(0)
        df_stock["現有庫存"] = df_stock["總入庫"] - df_stock["總出庫"]

        st.dataframe(df_stock.style.map(lambda x: 'color: red; font-weight: bold' if x < 0 else '', subset=['現有庫存']), use_container_width=True)

        st.divider()
        col_a, col_b = st.columns(2)
        with col_a:
            st.write("Inbound 明細 (含操作員)"); st.dataframe(df_in, use_container_width=True, height=300)
        with col_b:
            st.write("Outbound 明細 (含操作員)"); st.dataframe(df_out, use_container_width=True, height=300)
