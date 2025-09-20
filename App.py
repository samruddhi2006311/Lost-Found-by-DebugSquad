import streamlit as st
import sqlite3
import os
from datetime import datetime, timedelta
import hashlib
import pandas as pd
import altair as alt
import matplotlib.pyplot as plt

# ---------- Configuration ----------
DB_PATH = "lostfound.db"
IMAGES_DIR = "images"
AUTO_ARCHIVE_DAYS = 30  # if not collected in this many days -> archived

# ---------- Helpers ----------
def ensure_dirs():
    os.makedirs(IMAGES_DIR, exist_ok=True)

def get_conn():
    return sqlite3.connect(DB_PATH, check_same_thread=False)

def init_db():
    conn = get_conn()
    cur = conn.cursor()
    # Teachers table
    cur.execute("""
    CREATE TABLE IF NOT EXISTS teachers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL
    );
    """)
    # Items table
    cur.execute("""
    CREATE TABLE IF NOT EXISTS items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        description TEXT,
        found_location TEXT,
        collect_location TEXT,
        image_path TEXT,
        uploaded_at TEXT,
        status TEXT, -- 'lost', 'collected', 'archived'
        collected_at TEXT
    );
    """)
    conn.commit()
    conn.close()

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()

def check_teacher_exists():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM teachers;")
    c = cur.fetchone()[0]
    conn.close()
    return c > 0

def create_teacher(username, password):
    conn = get_conn()
    cur = conn.cursor()
    ph = hash_password(password)
    try:
        cur.execute("INSERT INTO teachers (username, password_hash) VALUES (?, ?)", (username, ph))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return False, "Username already exists"
    conn.close()
    return True, "Created"

def verify_teacher(username, password):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT password_hash FROM teachers WHERE username = ?", (username,))
    r = cur.fetchone()
    conn.close()
    if not r:
        return False
    return hash_password(password) == r[0]

def save_image(uploaded_file):
    # save to IMAGES_DIR with timestamp prefix + original name
    ts = datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
    filename = f"{ts}_{uploaded_file.name}"
    path = os.path.join(IMAGES_DIR, filename)
    with open(path, "wb") as f:
        f.write(uploaded_file.getbuffer())
    return path

def add_item(description, found_location, collect_location, image_path):
    conn = get_conn()
    cur = conn.cursor()
    now = datetime.utcnow().isoformat()
    cur.execute(
        "INSERT INTO items (description, found_location, collect_location, image_path, uploaded_at, status, collected_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (description, found_location, collect_location, image_path, now, "lost", None)
    )
    conn.commit()
    conn.close()

def get_items(status_filter=None, start_date=None, end_date=None):
    conn = get_conn()
    cur = conn.cursor()
    q = "SELECT id, description, found_location, collect_location, image_path, uploaded_at, status, collected_at FROM items"
    params = []
    clauses = []
    if status_filter:
        clauses.append("status = ?")
        params.append(status_filter)
    if start_date:
        clauses.append("date(uploaded_at) >= date(?)")
        params.append(start_date.isoformat())
    if end_date:
        clauses.append("date(uploaded_at) <= date(?)")
        params.append(end_date.isoformat())
    if clauses:
        q += " WHERE " + " AND ".join(clauses)
    q += " ORDER BY uploaded_at DESC"
    cur.execute(q, tuple(params))
    rows = cur.fetchall()
    conn.close()
    cols = ["id", "description", "found_location", "collect_location", "image_path", "uploaded_at", "status", "collected_at"]
    df = pd.DataFrame(rows, columns=cols)
    return df

def mark_collected(item_id):
    conn = get_conn()
    cur = conn.cursor()
    now = datetime.utcnow().isoformat()
    cur.execute("UPDATE items SET status = 'collected', collected_at = ? WHERE id = ?", (now, item_id))
    conn.commit()
    conn.close()

def archive_item(item_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE items SET status = 'archived' WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()

def auto_archive():
    # find items with status 'lost' older than AUTO_ARCHIVE_DAYS and archive them
    cutoff = datetime.utcnow() - timedelta(days=AUTO_ARCHIVE_DAYS)
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id, uploaded_at FROM items WHERE status = 'lost'")
    rows = cur.fetchall()
    for r in rows:
        item_id, uploaded_at = r
        try:
            up = datetime.fromisoformat(uploaded_at)
        except Exception:
            continue
        if up < cutoff:
            cur.execute("UPDATE items SET status = 'archived' WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()

# ---------- UI ----------
st.set_page_config(page_title="Lost & Found Portal", layout="wide")
ensure_dirs()
init_db()
auto_archive()  # run auto-archive on each load

st.title("🏷️ College Lost & Found Portal")

# Sidebar for mode selection
mode = st.sidebar.selectbox("View as", ["Student (no login)", "Teacher (login)"])

# ---------------- Student View ----------------
if mode == "Student (no login)":
    st.header("🔎 Browse Lost Items (Student view)")
    # Filter controls
    col1, col2, col3 = st.columns([1,1,1])
    with col1:
        status_view = st.selectbox("Show", ["All current lost items", "History (collected)", "Archived"], index=0)
    with col2:
        date_filter = st.checkbox("Filter by upload date")
    with col3:
        if date_filter:
            start_date = st.date_input("From", value=datetime.utcnow().date() - timedelta(days=90))
            end_date = st.date_input("To", value=datetime.utcnow().date())
        else:
            start_date = end_date = None

    status_map = {
        "All current lost items": ("lost",),
        "History (collected)": ("collected",),
        "Archived": ("archived",)
    }
    statuses = status_map[status_view]
    # show items for each selected status
    dfs = []
    for s in statuses:
        df = get_items(status_filter=s, start_date=start_date, end_date=end_date)
        dfs.append(df)
    if dfs:
        df_all = pd.concat(dfs, ignore_index=True) if len(dfs) > 1 else dfs[0]
    else:
        df_all = pd.DataFrame(columns=["id","description","found_location","collect_location","image_path","uploaded_at","status","collected_at"])

    if df_all.empty:
        st.info("No items found.")
    else:
        # Show details in a card-like list
        for idx, row in df_all.iterrows():
            cols = st.columns([2,3,3,1])
            with cols[0]:
                st.markdown(f"**{row['description']}**")
                st.write(f"Found at: {row['found_location']}")
                st.write(f"Collect at: {row['collect_location']}")
            with cols[1]:
                st.write("Uploaded at:")
                uploaded = row['uploaded_at']
                st.write(uploaded.split("T")[0] if uploaded else "")
                if row['status'] == 'collected':
                    st.success("✅ Collected")
                    st.write("Collected at:")
                    st.write(row['collected_at'].split("T")[0] if row['collected_at'] else "")
                elif row['status'] == 'archived':
                    st.warning("📦 Archived")
                else:
                    st.info("🔴 Currently available")
            with cols[2]:
                # image preview if exists
                if pd.notna(row['image_path']) and row['image_path'] and os.path.exists(row['image_path']):
                    st.image(row['image_path'], use_column_width=True)
                else:
                    st.write("No image")
            with cols[3]:
                st.write(f"ID: {row['id']}")
            st.markdown("---")

    # Stats chart (simple monthly stats) - optional but helpful
    st.subheader("📊 Monthly Lost Items (last 12 months)")
    all_items = get_items()
    if not all_items.empty:
        all_items['uploaded_at_date'] = pd.to_datetime(all_items['uploaded_at']).dt.to_period("M").astype(str)
        monthly = all_items.groupby('uploaded_at_date').size().reset_index(name='count')
        monthly = monthly.sort_values('uploaded_at_date')
        chart = alt.Chart(monthly).mark_bar().encode(
            x=alt.X('uploaded_at_date:N', title='Month'),
            y=alt.Y('count:Q', title='Items')
        ).properties(width=800, height=300)
        st.altair_chart(chart)
    else:
        st.write("No data for chart.")

# ---------------- Teacher View ----------------
else:
    st.header("🔐 Teacher (Admin) Portal")

    # initialize session state
    if "logged_in" not in st.session_state:
        st.session_state.logged_in = False
        st.session_state.username = None

    # If no teacher exists, allow creating first admin
    if not check_teacher_exists():
        st.warning("No teacher account found. Create the first teacher account.")
        with st.form("create_admin"):
            u = st.text_input("Username")
            p = st.text_input("Password", type="password")
            p2 = st.text_input("Confirm Password", type="password")
            submitted = st.form_submit_button("Create Admin")
            if submitted:
                if p != p2:
                    st.error("Passwords do not match.")
                elif not u or not p:
                    st.error("Provide username and password.")
                else:
                    ok, msg = create_teacher(u, p)
                    if ok:
                        st.success("Teacher created. Please login below.")
                    else:
                        st.error(msg)

    # Show login form if not logged in
    if not st.session_state.logged_in:
        with st.form("login_form"):
            st.write("Teacher Login")
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            login = st.form_submit_button("Login")
            if login:
                if verify_teacher(username, password):
                    st.session_state.logged_in = True
                    st.session_state.username = username
                    st.success(f"Logged in as {username}")
                else:
                    st.error("Invalid credentials.")
        st.stop()

    # Logged in content
    st.success(f"Welcome, {st.session_state.username}!")
    t1, t2, t3, t4 = st.tabs(["Add Item", "Manage Items", "History / Archive", "Account"])

    # ----- Add Item Tab -----
    with t1:
        st.subheader("➕ Add Lost Item")
        with st.form("add_item_form", clear_on_submit=True):
            desc = st.text_input("Item Description", placeholder="e.g., Black wallet with student ID")
            found_loc = st.text_input("Where it was found", placeholder="e.g., Library, Ground floor")
            collect_loc = st.text_input("Where to collect", placeholder="e.g., Admin Office")
            image_file = st.file_uploader("Photo of item (optional)", type=["png","jpg","jpeg"])
            submitted = st.form_submit_button("Add Item")
            if submitted:
                if not desc or not found_loc or not collect_loc:
                    st.error("Please fill all text fields.")
                else:
                    image_path = None
                    if image_file:
                        image_path = save_image(image_file)
                    add_item(desc, found_loc, collect_loc, image_path)
                    st.success("Item added successfully.")

    # ----- Manage Items Tab -----
    with t2:
        st.subheader("🛠 Manage Current Lost Items")
        df_lost = get_items(status_filter="lost")
        if df_lost.empty:
            st.info("No current lost items.")
        else:
            for idx, row in df_lost.iterrows():
                c1, c2, c3 = st.columns([3,2,1])
                with c1:
                    st.markdown(f"**{row['description']}**")
                    st.write(f"Found at: {row['found_location']}")
                    st.write(f"Collect at: {row['collect_location']}")
                with c2:
                    st.write("Uploaded:")
                    st.write(row['uploaded_at'].split("T")[0] if row['uploaded_at'] else "")
                    if pd.notna(row['image_path']) and row['image_path'] and os.path.exists(row['image_path']):
                        st.image(row['image_path'], width=200)
                with c3:
                    if st.button(f"Mark Collected (ID {int(row['id'])})", key=f"collect_{int(row['id'])}"):
                        mark_collected(int(row['id']))
                        st.success("Marked as collected.")
                        st.rerun()
                st.markdown("---")

    # ----- History / Archive Tab -----
    with t3:
        st.subheader("📚 History and Archived Items")
        tab_choice = st.radio("Choose", ["Collected History", "Archived"])
        if tab_choice == "Collected History":
            df = get_items(status_filter="collected")
        else:
            df = get_items(status_filter="archived")
        if df.empty:
            st.info("No items.")
        else:
            st.dataframe(df[['id','description','found_location','collect_location','uploaded_at','status','collected_at']])
            # allow unarchive or re-activate? Provide button to permanently delete or move back to lost for admin
            st.write("Admin actions")
            col_a, col_b = st.columns(2)
            with col_a:
                delete_id = st.number_input("Delete item by ID (permanent)", min_value=1, step=1, value=1)
                if st.button("Delete"):
                    if delete_id > 0:
                        conn = get_conn()
                        cur = conn.cursor()
                        cur.execute("DELETE FROM items WHERE id = ?", (delete_id,))
                        conn.commit()
                        conn.close()
                        st.success("Deleted (if ID existed).")
                        st.experimental_rerun()
            with col_b:
                if tab_choice == "Archived":
                    restore_id = st.number_input("Restore archived item ID to 'lost'", min_value=0, step=1, value=0, key="restoreid")
                    if st.button("Restore"):
                        if restore_id > 0:
                            conn = get_conn()
                            cur = conn.cursor()
                            cur.execute("UPDATE items SET status = 'lost' WHERE id = ?", (restore_id,))
                            conn.commit()
                            conn.close()
                            st.success("Restored to lost.")
                            st.experimental_rerun()

    # ----- Account Tab -----
    with t4:
        st.subheader("⚙️ Account")
        st.write(f"Logged in as **{st.session_state.username}**")
        if st.button("Logout"):
            st.session_state.logged_in = False
            st.session_state.username = None
            st.experimental_rerun()

        st.markdown("---")
        st.write("Create additional teacher account (for demo/testing)")
        with st.form("create_extra_teacher"):
            new_user = st.text_input("New username")
            new_pass = st.text_input("Password", type="password")
            create = st.form_submit_button("Create Teacher")
            if create:
                if not new_user or not new_pass:
                    st.error("Fill both fields.")
                else:
                    ok, msg = create_teacher(new_user, new_pass)
                    if ok:
                        st.success("Teacher created.")
                    else:
                        st.error(msg)

# ---------- End ----------


