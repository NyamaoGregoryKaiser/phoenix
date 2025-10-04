import streamlit as st
import requests
import pandas as pd
import os
from datetime import datetime
import altair as alt

st.set_page_config(page_title="Loandisk Disbursement Report", layout="wide")

# --- API CONFIG ---
API_URL = "https://api-main.loandisk.com/7125/75350/disbursement_report"
AUTH_KEY = "Basic DgPRFGb7VPYrnz7K68b4KqHNkYZkvswVqFGaU7xD"  # 🔐 replace with .env in production

HEADERS = {
    "Content-Type": "application/json",
    "Authorization": AUTH_KEY
}

# --- CSV cache path ---
CSV_PATH = "disbursements_cache.csv"

# --- Helpers ---
def _normalize_columns(frame: pd.DataFrame) -> pd.DataFrame:
    canonical = {}
    for c in frame.columns:
        c_stripped = str(c).strip()
        c_key = c_stripped.lower()
        if c_key == "loan id":
            canonical[c] = "Loan Id"
        elif c_key == "disbursed date":
            canonical[c] = "Disbursed Date"
        else:
            canonical[c] = c_stripped
    return frame.rename(columns=canonical)

# --- Branch code → name mapping and helper ---
BRANCH_NAME_MAP = {
    75350: "Thika Branch",
    8550: "TOWN BRANCH",
    55886: "Utawala Branch",
    12936: "BURUBURU BRANCH",
    63796: "Kiambu Branch",
    27133: "KIlimani Branch",
    77791: "Kitengela Branch",
}

def _branch_code_to_name(value):
    try:
        code = int(str(value).strip())
        return BRANCH_NAME_MAP.get(code, str(value))
    except Exception:
        return str(value)

# Render helper: monthly disbursed amounts by branch as KPI cards
def _render_branch_monthly_cards(df_source: pd.DataFrame, value_column: str | None) -> None:
    try:
        now_dt_b = datetime.today()
        df_src = None
        if os.path.exists(CSV_PATH):
            try:
                df_src = pd.read_csv(CSV_PATH)
                df_src = _normalize_columns(df_src)
            except Exception:
                df_src = None
        if df_src is None:
            df_src = df_source.copy() if df_source is not None else pd.DataFrame()
        if df_src.empty or "Disbursed Date" not in df_src.columns or "Branch" not in df_src.columns:
            st.info("No branch disbursements to show for the current month.")
            return
        # Choose value column if not provided
        val_col = value_column
        if val_col is None:
            if "Principal" in df_src.columns:
                val_col = "Principal"
            elif "Outstanding" in df_src.columns:
                val_col = "Outstanding"
            else:
                st.info("No disbursement amount column available for branch cards.")
                return
        disb_dates = pd.to_datetime(df_src["Disbursed Date"], dayfirst=True, errors="coerce")
        mask_month = disb_dates.dt.month.eq(now_dt_b.month) & disb_dates.dt.year.eq(now_dt_b.year)
        tmp = df_src.loc[mask_month, ["Branch", val_col]].copy()
        if tmp.empty:
            st.info("No disbursements this month to display by branch.")
            return
        tmp[val_col] = tmp[val_col].astype(str).str.replace(",", "", regex=False)
        tmp[val_col] = pd.to_numeric(tmp[val_col], errors="coerce").fillna(0.0)
        tmp["BranchName"] = tmp["Branch"].apply(_branch_code_to_name)
        grouped = tmp.groupby("BranchName")[val_col].sum()
        ordered_pairs = [
            (75350, "Thika Branch"),
            (8550, "TOWN BRANCH"),
            (55886, "Utawala Branch"),
            (12936, "BURUBURU BRANCH"),
            (63796, "Kiambu Branch"),
            (27133, "KIlimani Branch"),
            (77791, "Kitengela Branch"),
        ]
        names = [nm for _, nm in ordered_pairs]
        amounts = [float(grouped.get(nm, 0.0)) for nm in names]
        # Filter out zero-amount branches (e.g., hide "Mombasa Road" 0.00)
        non_zero = [(nm, vl) for nm, vl in zip(names, amounts) if vl > 0]
        if len(non_zero) == 0:
            st.caption("Amounts disbursed this month by branch")
            st.info("No positive disbursements recorded per branch this month.")
            return
        st.caption("Amounts disbursed this month by branch")
        cols_branch = st.columns(len(non_zero))
        for i, (nm, vl) in enumerate(non_zero):
            cols_branch[i].metric(f"{nm}", f"{int(round(vl)):,}")
    except Exception:
        st.info("Branch cards unavailable.")

# --- Dynamic dates ---
default_start_date = "01/01/2024"
today = datetime.today().strftime("%d/%m/%Y")

# Determine start_date from cache: pick Disbursed Date of the largest Loan Id
start_date = default_start_date
if os.path.exists(CSV_PATH):
    try:
        cache_df = pd.read_csv(CSV_PATH)
        cache_df = _normalize_columns(cache_df)
        if "Loan Id" in cache_df.columns and "Disbursed Date" in cache_df.columns:
            # coerce Loan Id to numeric for correct max selection
            cache_df["_loan_id_num"] = pd.to_numeric(cache_df["Loan Id"], errors="coerce")
            # drop rows without loan id or date
            cache_df_valid = cache_df.dropna(subset=["_loan_id_num", "Disbursed Date"]) 
            if not cache_df_valid.empty:
                # get row with max loan id
                row_max = cache_df_valid.loc[cache_df_valid["_loan_id_num"].idxmax()]
                candidate_date = str(row_max["Disbursed Date"]).strip()
                # validate date format; if parse succeeds, use it
                try:
                    _ = datetime.strptime(candidate_date, "%d/%m/%Y")
                    start_date = candidate_date
                except Exception:
                    pass
    except Exception:
        # if cache read fails, keep default start_date
        pass

# --- Request body ---
PAGE_SIZE = 500  # request as many as allowed per page to reduce calls
payload = {
    "from": 1,
    "count": PAGE_SIZE,
    "start_date": start_date,
    "end_date": today,
    "do_not_include_restructured_loans": 1,
    "do_not_include_not_released_loans": 1,
    "branches_select": "12936||63796||27133||8678||75350||8550||55886||77791"
}

st.title("📊 Loandisk Disbursement Report")

# Auto-fetch on page load/refresh
fetch_clicked = True

if fetch_clicked:
    try:
        # Paginate through all pages to retrieve every result
        all_results = []
        meta_first = {}
        current_page = 1
        max_pages = 1000  # safety guard
        progress = st.progress(0)
        status_text = st.empty()

        while current_page <= max_pages:
            status_text.write(f"Fetching page {current_page}…")
            page_payload = dict(payload)
            page_payload["from"] = current_page
            response = requests.post(API_URL, headers=HEADERS, json=page_payload)

            if response.status_code != 200:
                break

            data = response.json()
            if current_page == 1:
                # capture initial meta
                if isinstance(data, dict) and "response" in data:
                    meta_first = data.get("response", {})

            # Extract results for this page
            page_results = []
            if isinstance(data, dict) and "response" in data:
                r = data.get("response", {})
                raw_results = r.get("Results", [])
                if isinstance(raw_results, dict):
                    try:
                        page_results = [raw_results[k] for k in sorted(raw_results.keys(), key=lambda x: int(x))]
                    except Exception:
                        page_results = list(raw_results.values())
                else:
                    page_results = raw_results
                return_results = r.get("ReturnResults")
            elif isinstance(data, list):
                page_results = data
                return_results = len(page_results)
            else:
                return_results = 0

            all_results.extend(page_results if page_results else [])

            # Progress update (best-effort based on TotalResults if present)
            total_results_reported = meta_first.get("TotalResults") if isinstance(meta_first, dict) else None
            if isinstance(total_results_reported, int) and total_results_reported > 0:
                progress.progress(min(1.0, len(all_results) / float(total_results_reported)))
            else:
                # fallback approximate
                progress.progress(min(1.0, current_page / 10.0))

            # Stop if this page returned fewer than requested or no items
            if not return_results or (isinstance(return_results, int) and return_results < payload["count"]):
                break

            current_page += 1

        progress.empty()
        status_text.empty()

        if response.status_code == 200:
            data = data  # last page's data is already parsed above
            st.success("✅ Disbursements retrieved successfully!")

            # Handle both previous list format and the provided object format
            results = all_results
            meta = meta_first if isinstance(meta_first, dict) else {}

            # Build DataFrame if possible
            df = pd.DataFrame(results) if isinstance(results, list) and len(results) > 0 else pd.DataFrame()

            # KPIs will be rendered after cache merge so Total Records reflects merged data

            # Pre-allocate holder for current-year disbursement daily totals
            disb_ts_year = None

            if not df.empty:
                # --- Incremental CSV cache: append only new loans ---
                def attach_key_column(frame: pd.DataFrame) -> str | None:
                    # Use only Loan Id as the unique key; if missing, skip caching
                    if "Loan Id" not in frame.columns:
                        return None
                    frame["_key"] = frame["Loan Id"].astype(str)
                    return "_key"

                # Normalize columns for fetched data as well
                df = _normalize_columns(df)
                key_name = attach_key_column(df)
                cached_rows_before = 0
                if key_name is not None and os.path.exists(CSV_PATH):
                    try:
                        df_cache = pd.read_csv(CSV_PATH)
                        df_cache = _normalize_columns(df_cache)
                        if key_name not in df_cache.columns:
                            # Try to rebuild key in cache
                            attach_key_column(df_cache)
                        if key_name in df_cache.columns:
                            cached_rows_before = len(df_cache)
                            idx_cache = df_cache.set_index(key_name, drop=False)
                            idx_new = df.set_index(key_name, drop=False)
                            # Find strictly new keys
                            only_new_idx = idx_new.index.difference(idx_cache.index)
                            if len(only_new_idx) > 0:
                                df_appended = pd.concat([df_cache, idx_new.loc[only_new_idx].reset_index(drop=True)], ignore_index=True)
                            else:
                                df_appended = df_cache
                            # Save back
                            to_save = df_appended.drop(columns=[key_name], errors="ignore")
                            to_save.to_csv(CSV_PATH, index=False)
                            # Use merged data for visuals
                            df = df_appended
                    except Exception:
                        # If cache read fails, proceed without blocking
                        pass
                elif key_name is not None and not os.path.exists(CSV_PATH):
                    try:
                        to_save = df.drop(columns=[key_name], errors="ignore")
                        to_save.to_csv(CSV_PATH, index=False)
                    except Exception:
                        pass

                # After cache merge, show KPIs including total records
                # Compute Total Records from CSV unique Loan Ids if available
                total_records = None
                try:
                    if os.path.exists(CSV_PATH):
                        cache_for_count = pd.read_csv(CSV_PATH)
                        cache_for_count = _normalize_columns(cache_for_count)
                        if "Loan Id" in cache_for_count.columns:
                            total_records = cache_for_count["Loan Id"].astype(str).nunique()
                except Exception:
                    total_records = None
                if total_records is None:
                    total_records = len(df)

                # Compute current month disbursement count from CSV when available
                current_month_count = 0
                try:
                    if os.path.exists(CSV_PATH):
                        csv_df = pd.read_csv(CSV_PATH)
                        csv_df = _normalize_columns(csv_df)
                        if "Loan Id" in csv_df.columns and "Disbursed Date" in csv_df.columns:
                            disb_series_csv = pd.to_datetime(csv_df["Disbursed Date"], dayfirst=True, errors="coerce")
                            now_dt = datetime.today()
                            mask_csv = (
                                disb_series_csv.dt.month.eq(now_dt.month)
                                & disb_series_csv.dt.year.eq(now_dt.year)
                            )
                            current_month_count = int(csv_df.loc[mask_csv, "Loan Id"].astype(str).nunique())
                        else:
                            current_month_count = 0
                    else:
                        # Fallback to in-memory df if CSV absent
                        if "Disbursed Date" in df.columns and "Loan Id" in df.columns:
                            disb_series = pd.to_datetime(df["Disbursed Date"], dayfirst=True, errors="coerce")
                            now_dt = datetime.today()
                            mask_df = (
                                disb_series.dt.month.eq(now_dt.month)
                                & disb_series.dt.year.eq(now_dt.year)
                            )
                            current_month_count = int(df.loc[mask_df, "Loan Id"].astype(str).nunique())
                        else:
                            current_month_count = 0
                except Exception:
                    current_month_count = 0

                # Compute current month amount disbursed by summing Disbursed for current month
                current_month_amount = 0.0
                try:
                    if os.path.exists(CSV_PATH):
                        csv_df2 = pd.read_csv(CSV_PATH)
                        csv_df2 = _normalize_columns(csv_df2)
                        if "Disbursed Date" in csv_df2.columns and "Disbursed" in csv_df2.columns:
                            disb2 = pd.to_datetime(csv_df2["Disbursed Date"], dayfirst=True, errors="coerce")
                            now_dt2 = datetime.today()
                            mask2 = (
                                disb2.dt.month.eq(now_dt2.month)
                                & disb2.dt.year.eq(now_dt2.year)
                            )
                            principal_series = (
                                csv_df2.loc[mask2, "Disbursed"]
                                .astype(str)
                                .str.replace(",", "", regex=False)
                            )
                            current_month_amount = float(pd.to_numeric(principal_series, errors="coerce").sum())
                    else:
                        if "Disbursed Date" in df.columns and "Disbursed" in df.columns:
                            disb2 = pd.to_datetime(df["Disbursed Date"], dayfirst=True, errors="coerce")
                            now_dt2 = datetime.today()
                            mask2 = (
                                disb2.dt.month.eq(now_dt2.month)
                                & disb2.dt.year.eq(now_dt2.year)
                            )
                            principal_series = (
                                df.loc[mask2, "Disbursed"]
                                .astype(str)
                                .str.replace(",", "", regex=False)
                            )
                            current_month_amount = float(pd.to_numeric(principal_series, errors="coerce").sum())
                except Exception:
                    current_month_amount = 0.0

                # Compute today's amount disbursed (Disbursed field)
                amount_disbursed_today = 0.0
                try:
                    # Prefer CSV for completeness
                    if os.path.exists(CSV_PATH):
                        csv_df3 = pd.read_csv(CSV_PATH)
                        csv_df3 = _normalize_columns(csv_df3)
                        if "Disbursed Date" in csv_df3.columns and "Disbursed" in csv_df3.columns:
                            disb3 = pd.to_datetime(csv_df3["Disbursed Date"], dayfirst=True, errors="coerce")
                            today_dt = datetime.today().date()
                            mask_today = disb3.dt.date.eq(today_dt)
                            # Choose value column
                            val_series = csv_df3.loc[mask_today, "Disbursed"].astype(str).str.replace(",", "", regex=False)
                            amount_disbursed_today = float(pd.to_numeric(val_series, errors="coerce").sum())
                    else:
                        # Fallback to in-memory df if CSV absent
                        if "Disbursed Date" in df.columns and "Disbursed" in df.columns:
                            disb3 = pd.to_datetime(df["Disbursed Date"], dayfirst=True, errors="coerce")
                            today_dt = datetime.today().date()
                            mask_today = disb3.dt.date.eq(today_dt)
                            val_series2 = (
                                df.loc[mask_today, "Disbursed"]
                                .astype(str)
                                .str.replace(",", "", regex=False)
                            )
                            amount_disbursed_today = float(pd.to_numeric(val_series2, errors="coerce").sum())
                except Exception:
                    amount_disbursed_today = 0.0

                kpi_cols = st.columns(4)
                kpi_cols[0].metric("Total Records", f"{total_records}")
                kpi_cols[1].metric("Current Month", f"{current_month_count}")
                kpi_cols[2].metric("Amount Disbursed (This Month)", f"{current_month_amount:,.2f}")
                kpi_cols[3].metric("Amount Disbursed Today", f"{amount_disbursed_today:,.2f}")

                # Branch amounts (current month) cards below KPIs (second row)
                _render_branch_monthly_cards(df, None)
                # Show the effective date window used
                st.caption(f"Date window: {start_date} → {today}")

                # Attempt basic cleaning: numeric columns and dates commonly present
                numeric_like_cols = [
                    "Outstanding", "Balance", "Principal", "Interest", "Total Due",
                    "Fees Balance", "Interest Balance", "Penalty Balance", "Principal Balance",
                    "Fees Paid", "Interest Paid", "Penalty Paid", "Principal Paid"
                ]
                for col in numeric_like_cols:
                    if col in df.columns:
                        df[col] = (
                            df[col]
                            .astype(str)
                            .str.replace(",", "", regex=False)
                            .replace({"": None})
                        )
                        df[col] = pd.to_numeric(df[col], errors="coerce")

                date_cols = ["Disbursed Date", "Released", "Maturity", "NextDue", "Last Repayment", "DOB"]
                for col in date_cols:
                    if col in df.columns:
                        df[col] = pd.to_datetime(df[col], format="%d/%m/%Y", errors="coerce")

                # Visualizations
                st.subheader("Visualisations")
                vc1, vc2 = st.columns(2)

                # By Branch: total Principal (fallback to Outstanding if Principal missing)
                value_col = "Principal" if "Principal" in df.columns else ("Outstanding" if "Outstanding" in df.columns else None)
                if value_col and "Branch" in df.columns:
                    # Map branch codes to names where possible
                    branch_series = df["Branch"].apply(_branch_code_to_name)
                    by_branch = (
                        pd.DataFrame({"Branch": branch_series, value_col: df[value_col]})
                        .groupby("Branch", dropna=False)[value_col]
                        .sum()
                        .sort_values(ascending=False)
                        .head(10)
                        .reset_index()
                    )
                    vc1.caption(f"Top 10 branches by {value_col}")
                    vc1.bar_chart(by_branch.set_index("Branch"))

                # Status counts
                if "Status" in df.columns:
                    status_counts = df["Status"].fillna("(blank)").value_counts().reset_index()
                    status_counts.columns = ["Status", "Count"]
                    vc2.caption("Loans by status")
                    vc2.bar_chart(status_counts.set_index("Status"))

                # Time series: daily disbursed totals by Disbursed Date (all-time range)
                if "Disbursed Date" in df.columns and value_col:
                    ts = (
                        df.dropna(subset=["Disbursed Date"])
                        .groupby(pd.Grouper(key="Disbursed Date", freq="D"))[value_col]
                        .sum()
                        .reset_index()
                        .sort_values("Disbursed Date")
                    )
                    if not ts.empty:
                        st.caption(f"Daily total {value_col.lower()} by disbursed date")
                        st.line_chart(ts.set_index("Disbursed Date"))

                # Time series: current month daily totals
                if "Disbursed Date" in df.columns and value_col:
                    now_dt = datetime.today()
                    month_mask = (
                        df["Disbursed Date"].dt.month.eq(now_dt.month)
                        & df["Disbursed Date"].dt.year.eq(now_dt.year)
                    )
                    df_month = df.loc[month_mask]
                    if not df_month.empty:
                        ts_month = (
                            df_month
                            .groupby(pd.Grouper(key="Disbursed Date", freq="D"))[value_col]
                            .sum()
                            .reset_index()
                            .sort_values("Disbursed Date")
                        )
                        if not ts_month.empty:
                            col_l, col_r = st.columns(2)
                            with col_l:
                                st.caption(f"Current month daily total {value_col.lower()} ({now_dt.strftime('%B %Y')})")
                                chart = (
                                    alt.Chart(ts_month)
                                    .mark_line(interpolate='monotone')
                                    .encode(
                                        x=alt.X('Disbursed Date:T', title='Date'),
                                        y=alt.Y(f'{value_col}:Q', title=value_col)
                                    )
                                    .properties(height=300)
                                )
                                st.altair_chart(chart, use_container_width=True)
                            with col_r:
                                # Loan officers disbursements table (current month)
                                try:
                                    # Prefer CSV for completeness
                                    df_off = None
                                    if os.path.exists(CSV_PATH):
                                        try:
                                            df_off = pd.read_csv(CSV_PATH)
                                            df_off = _normalize_columns(df_off)
                                        except Exception:
                                            df_off = None
                                    if df_off is None:
                                        df_off = df.copy()

                                    # Local case-insensitive finder to avoid scope issues
                                    def _find_ci(columns, candidates):
                                        lowered = {str(c).strip().lower(): c for c in columns}
                                        for cand in candidates:
                                            key = str(cand).strip().lower()
                                            if key in lowered:
                                                return lowered[key]
                                        return None

                                    # Prefer 'Sales Person' field; fall back to LoanOfficer variants
                                    officer_col = _find_ci(
                                        df_off.columns,
                                        [
                                            "Sales Person", "Salesperson", "Sales_Person", "SalesPerson",
                                            "Sales Representative", "SalesRep", "Sales Rep",
                                        ]
                                    )
                                    if officer_col is None:
                                        officer_col = _find_ci(
                                            df_off.columns,
                                            [
                                                "LoanOfficer", "Loan Officer", "Loan Officer Name",
                                                "Officer", "Officer Name", "Field Officer", "FieldOfficer",
                                                "Account Officer", "AccountOfficer", "LoanOfficerName",
                                                "OfficerInCharge"
                                            ]
                                        )

                                    st.caption("Sales Person - amount disbursed (current month)")
                                    # Choose amount column from df_off for robustness (prefer Principal, else Outstanding)
                                    amt_col_off = "Principal" if "Principal" in df_off.columns else ("Outstanding" if "Outstanding" in df_off.columns else None)
                                    if officer_col and "Disbursed Date" in df_off.columns and amt_col_off is not None:
                                        ddates = pd.to_datetime(df_off["Disbursed Date"], dayfirst=True, errors="coerce")
                                        mask_m = ddates.dt.month.eq(now_dt.month) & ddates.dt.year.eq(now_dt.year)
                                        tmp = df_off.loc[mask_m, [officer_col, amt_col_off]].copy()
                                        if tmp.empty:
                                            st.info("No disbursements for the current month.")
                                        else:
                                            tmp[amt_col_off] = tmp[amt_col_off].astype(str).str.replace(",", "", regex=False)
                                            tmp[amt_col_off] = pd.to_numeric(tmp[amt_col_off], errors='coerce').fillna(0.0)
                                            tbl = (
                                                tmp.groupby(officer_col)[amt_col_off]
                                                .sum()
                                                .sort_values(ascending=False)
                                                .reset_index()
                                            )
                                            tbl.columns = ["Sales Person", "Amount Disbursed"]
                                            # Ensure proper data types for Arrow compatibility
                                            tbl["Sales Person"] = tbl["Sales Person"].astype(str)
                                            tbl["Amount Disbursed"] = tbl["Amount Disbursed"].round(0).astype(int)
                                            st.dataframe(tbl, width='stretch')
                                    else:
                                        st.info("Sales Person or amount column not found in data.")
                                except Exception as e:
                                    st.error(f"Sales Person table unavailable. Error: {e}")

                # Current year daily totals (for combined chart later)
                if "Disbursed Date" in df.columns and value_col:
                    now_dt_y = datetime.today()
                    year_mask = df["Disbursed Date"].dt.year.eq(now_dt_y.year)
                    df_year = df.loc[year_mask]
                    if not df_year.empty:
                        disb_ts_year = (
                            df_year
                            .groupby(pd.Grouper(key="Disbursed Date", freq="D"))[value_col]
                            .sum()
                            .reset_index()
                            .sort_values("Disbursed Date")
                        )

                # Branch disbursements with date range filter
                st.subheader("Branch Disbursements (Date Range)")
                try:
                    # Prefer CSV for completeness
                    df_branch = None
                    if os.path.exists(CSV_PATH):
                        try:
                            df_branch = pd.read_csv(CSV_PATH)
                            df_branch = _normalize_columns(df_branch)
                        except Exception:
                            df_branch = None
                    if df_branch is None:
                        df_branch = df.copy()
                    
                    if not df_branch.empty and "Disbursed Date" in df_branch.columns and "Branch" in df_branch.columns:
                        # Date range inputs
                        col_d1, col_d2 = st.columns(2)
                        with col_d1:
                            br_from = st.date_input("Start Date", value=datetime.today().replace(day=1))
                        with col_d2:
                            br_to = st.date_input("End Date", value=datetime.today())

                        # Choose amount column
                        amt_col_branch = "Principal" if "Principal" in df_branch.columns else ("Outstanding" if "Outstanding" in df_branch.columns else None)
                        if amt_col_branch:
                            # Parse dates and filter for range
                            ddates_branch = pd.to_datetime(df_branch["Disbursed Date"], dayfirst=True, errors="coerce")
                            mask_range = (ddates_branch.dt.date >= br_from) & (ddates_branch.dt.date <= br_to)
                            tmp_branch = df_branch.loc[mask_range, ["Branch", amt_col_branch]].copy()
                            
                            # Always render a table (empty or with data)
                            # Clean numeric and map branch codes to names
                            if not tmp_branch.empty:
                                tmp_branch[amt_col_branch] = tmp_branch[amt_col_branch].astype(str).str.replace(",", "", regex=False)
                                tmp_branch[amt_col_branch] = pd.to_numeric(tmp_branch[amt_col_branch], errors='coerce').fillna(0.0)
                                tmp_branch["BranchName"] = tmp_branch["Branch"].apply(_branch_code_to_name)
                                # Group by branch and sum
                                branch_table = (
                                    tmp_branch.groupby("BranchName")[amt_col_branch]
                                    .sum()
                                    .sort_values(ascending=False)
                                    .reset_index()
                                )
                                branch_table.columns = ["Branch", "Amount Disbursed"]
                                branch_table["Branch"] = branch_table["Branch"].astype(str)
                                branch_table["Amount Disbursed"] = branch_table["Amount Disbursed"].round(0).astype(int)
                            else:
                                branch_table = pd.DataFrame({"Branch": [], "Amount Disbursed": []})
                            st.caption(f"Disbursements by branch ({br_from.strftime('%d/%m/%Y')} to {br_to.strftime('%d/%m/%Y')})")
                            st.dataframe(branch_table, width='stretch')
                        else:
                            st.info("Amount column not found for branch disbursements.")
                    else:
                        st.info("Required columns (Disbursed Date, Branch) not found.")
                except Exception as e:
                    st.error(f"Error loading branch disbursements: {e}")

                # Detailed table
                with st.expander("View raw table", expanded=False):
                    # Clean data types for Arrow compatibility
                    df_display = df.copy()
                    for col in df_display.columns:
                        if df_display[col].dtype == 'object':
                            # Convert mixed types to strings
                            df_display[col] = df_display[col].astype(str)
                    st.dataframe(df_display, width='stretch')

                
            else:
                st.info("No disbursement records found for this period.")
                
        else:
            st.error(f"❌ Error {response.status_code}")
            st.subheader("Error body")
            st.text(response.text)

    except Exception as e:
        st.error(f"⚠️ Request failed: {e}")


# --- Repayments fetch and cache (Advanced Search) ---
st.divider()
st.subheader("Repayments (Advanced Search)")

REPAYMENTS_API_URL = "https://api-main.loandisk.com/7125/{branch_id}/advanced_search_repayments"
REPAYMENT_PAGE_SIZE = 100  # API caps ReturnResults at 100 for advanced search
REPAYMENTS_CSV_PATH = "repayments_cache.csv"
REPAYMENT_BRANCH_IDS = [55886, 12936, 63796, 27133, 75350, 8550, 77791]

# (moved earlier near helpers)

# Determine default from-date for repayments from CSV: use collected date of max repayment_id
def _default_repayments_from_date() -> str:
    fallback = "01/01/2025"
    try:
        if os.path.exists(REPAYMENTS_CSV_PATH):
            df = pd.read_csv(REPAYMENTS_CSV_PATH)
            df = _normalize_columns(df)
            # Harmonize column names that may appear from API
            id_col = None
            for cand in ["repayment_id", "Repayment Id"]:
                if cand in df.columns:
                    id_col = cand
                    break
            date_col = None
            for cand in ["repayment_collected_date", "Paid Date"]:
                if cand in df.columns:
                    date_col = cand
                    break
            if id_col and date_col:
                df["_rid_num"] = pd.to_numeric(df[id_col], errors="coerce")
                valid = df.dropna(subset=["_rid_num", date_col])
                if not valid.empty:
                    row = valid.loc[valid["_rid_num"].idxmax()]
                    candidate = str(row[date_col]).strip()
                    # Validate dd/mm/YYYY
                    try:
                        _ = datetime.strptime(candidate, "%d/%m/%Y")
                        return candidate
                    except Exception:
                        pass
    except Exception:
        pass
    return fallback

# Cleaning helper so that each field is a single, primitive cell
def _clean_frame_for_csv(frame: pd.DataFrame) -> pd.DataFrame:
    dfc = frame.copy()
    # Drop well-known nested columns that duplicate info
    for col in ["custom_fields"]:
        if col in dfc.columns:
            dfc = dfc.drop(columns=[col])
    # Convert list/dict objects to JSON strings to keep one value per cell
    try:
        import json
        for col in list(dfc.columns):
            if dfc[col].dtype == object:
                if dfc[col].map(lambda v: isinstance(v, (list, dict))).any():
                    dfc[col] = dfc[col].apply(lambda v: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v)
    except Exception:
        pass
    # Remove accidental index columns
    for col in list(dfc.columns):
        if str(col).startswith("Unnamed:"):
            dfc = dfc.drop(columns=[col])
    return dfc

# Utility: find a column by case-insensitive match against candidate names
def _find_column_case_insensitive(frame: pd.DataFrame, candidates: list[str]) -> str | None:
    if frame is None or len(frame.columns) == 0:
        return None
    lowered = {str(c).strip().lower(): c for c in frame.columns}
    for cand in candidates:
        key = str(cand).strip().lower()
        if key in lowered:
            return lowered[key]
    return None

repay_from_date = _default_repayments_from_date()
repay_to_date = today
if fetch_clicked:
    try:
        # Build base payload (use same date window as disbursements)
        repay_payload = {
            "from": 1,  # Start at 1 per Postman example
            "count": REPAYMENT_PAGE_SIZE,
            "repayment_search_from_date": repay_from_date,
            "repayment_search_to_date": repay_to_date,
        }

        all_repayments = []
        max_pages = 1000
        progress = st.progress(0)
        status_text = st.empty()

        def extract_repayment_results(data_obj):
            # Robustly flatten nested lists to a list of dicts
            def flatten_list(lst):
                out = []
                stack = list(lst)
                while stack:
                    v = stack.pop(0)
                    if isinstance(v, dict):
                        out.append(v)
                    elif isinstance(v, list):
                        stack[:0] = v
                return out

            if isinstance(data_obj, dict) and "response" in data_obj:
                r = data_obj.get("response", {})
                raw = r.get("Results", [])
                if isinstance(raw, dict):
                    raw = list(raw.values())
                if isinstance(raw, list):
                    return flatten_list(raw)
                return []
            elif isinstance(data_obj, list):
                return [x for x in flatten_list(data_obj) if isinstance(x, dict)]
            return []

        total_branches = len(REPAYMENT_BRANCH_IDS)
        for b_index, branch_id in enumerate(REPAYMENT_BRANCH_IDS, start=1):
            current_page = 1
            total_reported = None
            while current_page <= max_pages:
                status_text.write(f"Fetching branch {branch_id} page {current_page}…")
                repay_payload["from"] = current_page
                url = REPAYMENTS_API_URL.format(branch_id=branch_id)
                resp = requests.post(url, headers=HEADERS, json=repay_payload)
                if resp.status_code != 200:
                    break
                data_r = resp.json()
                # Capture total on first page per-branch
                if total_reported is None and isinstance(data_r, dict):
                    try:
                        total_reported = data_r.get("response", {}).get("TotalResults")
                    except Exception:
                        total_reported = None
                page_items = extract_repayment_results(data_r)
                # Tag items with source branch id
                for it in page_items:
                    try:
                        it["branch_id"] = branch_id
                    except Exception:
                        pass
                all_repayments.extend(page_items)

                # Progress: approximate by branch progress and page progress
                base_prog = (b_index - 1) / float(total_branches)
                if isinstance(total_reported, int) and total_reported > 0:
                    branch_prog = min(1.0, current_page / 20.0)
                else:
                    branch_prog = min(1.0, current_page / 20.0)
                overall = min(1.0, base_prog + branch_prog / float(total_branches))
                progress.progress(overall)

                # Stop if short page (API returns up to 100 per page)
                try:
                    rmeta = data_r.get("response", {}) if isinstance(data_r, dict) else {}
                    returned = rmeta.get("ReturnResults")
                except Exception:
                    returned = None
                if not page_items or (isinstance(returned, int) and returned < REPAYMENT_PAGE_SIZE):
                    break
                current_page += 1

        progress.empty()
        status_text.empty()

        # Build DataFrame
        df_rep = pd.DataFrame(all_repayments) if len(all_repayments) > 0 else pd.DataFrame()
        if df_rep.empty:
            st.info("No repayments returned.")
        else:
            df_rep = _normalize_columns(df_rep)
            df_rep = _clean_frame_for_csv(df_rep)
            # Ensure repayment_amount is numeric for charts and display
            rep_amt_col_actual = _find_column_case_insensitive(df_rep, ["repayment_amount", "repayment amount", "amount", "payment", "paid"]) 
            if rep_amt_col_actual:
                df_rep[rep_amt_col_actual] = (
                    df_rep[rep_amt_col_actual].astype(str).str.replace(",", "", regex=False)
                )
                df_rep[rep_amt_col_actual] = pd.to_numeric(df_rep[rep_amt_col_actual], errors="coerce")
            st.caption(f"Fetched repayments rows this run: {len(df_rep)} (total reported: {total_reported if total_reported is not None else '—'})")
            # Dedupe key: repayment_id preferred
            if "repayment_id" in df_rep.columns:
                df_rep["_rkey"] = df_rep["repayment_id"].astype(str)
            elif "Repayment Id" in df_rep.columns:
                df_rep["_rkey"] = df_rep["Repayment Id"].astype(str)
            else:
                comp_cols = [c for c in [
                    "loan_id", "Loan Id", "repayment_collected_date", "Paid Date",
                    "repayment_amount", "Amount", "Payment", "Paid"
                ] if c in df_rep.columns]
                if len(comp_cols) >= 2:
                    df_rep["_rkey"] = df_rep[comp_cols].astype(str).agg('|'.join, axis=1).str.replace('\n', ' ', regex=False)
                else:
                    df_rep["_rkey"] = df_rep.astype(str).agg('|'.join, axis=1).str.replace('\n', ' ', regex=False)

            # Incremental CSV save with dedupe
            if os.path.exists(REPAYMENTS_CSV_PATH):
                try:
                    cache_r = pd.read_csv(REPAYMENTS_CSV_PATH)
                    cache_r = _normalize_columns(cache_r)
                    cache_r = _clean_frame_for_csv(cache_r)
                    if "_rkey" not in cache_r.columns:
                        if "repayment_id" in cache_r.columns:
                            cache_r["_rkey"] = cache_r["repayment_id"].astype(str)
                        elif "Repayment Id" in cache_r.columns:
                            cache_r["_rkey"] = cache_r["Repayment Id"].astype(str)
                        else:
                            comp_cols_c = [c for c in [
                                "loan_id", "Loan Id", "repayment_collected_date", "Paid Date",
                                "repayment_amount", "Amount", "Payment", "Paid"
                            ] if c in cache_r.columns]
                            if len(comp_cols_c) >= 2:
                                cache_r["_rkey"] = cache_r[comp_cols_c].astype(str).agg('|'.join, axis=1).str.replace('\n', ' ', regex=False)
                            else:
                                cache_r["_rkey"] = cache_r.astype(str).agg('|'.join, axis=1).str.replace('\n', ' ', regex=False)

                    idx_cache = cache_r.set_index("_rkey", drop=False)
                    idx_new = df_rep.set_index("_rkey", drop=False)
                    only_new_idx = idx_new.index.difference(idx_cache.index)
                    merged = pd.concat([cache_r, idx_new.loc[only_new_idx].reset_index(drop=True)], ignore_index=True) if len(only_new_idx) > 0 else cache_r
                    # Drop helper/index-like columns before saving
                    to_save = merged.drop(columns=["_rkey"], errors="ignore")
                    to_save = _clean_frame_for_csv(to_save)
                    # Make sure repayment_amount is numeric for display
                    rep_amt_col_actual = _find_column_case_insensitive(to_save, ["repayment_amount", "repayment amount", "amount", "payment", "paid"]) 
                    if rep_amt_col_actual:
                        to_save[rep_amt_col_actual] = (
                            to_save[rep_amt_col_actual].astype(str).str.replace(",", "", regex=False)
                        )
                        to_save[rep_amt_col_actual] = pd.to_numeric(to_save[rep_amt_col_actual], errors="coerce")
                    to_save.to_csv(REPAYMENTS_CSV_PATH, index=False)
                    st.success(f"Saved repayments. Total rows: {len(to_save)} (added {len(only_new_idx)})")
                    # Clean data types for Arrow compatibility
                    to_save_display = to_save.copy()
                    for col in to_save_display.columns:
                        if to_save_display[col].dtype == 'object':
                            to_save_display[col] = to_save_display[col].astype(str)
                    st.dataframe(to_save_display, width='stretch')
                except Exception as e:
                    to_save = _clean_frame_for_csv(df_rep.drop(columns=["_rkey"], errors="ignore"))
                    rep_amt_col_actual = _find_column_case_insensitive(to_save, ["repayment_amount", "repayment amount", "amount", "payment", "paid"]) 
                    if rep_amt_col_actual:
                        to_save[rep_amt_col_actual] = (
                            to_save[rep_amt_col_actual].astype(str).str.replace(",", "", regex=False)
                        )
                        to_save[rep_amt_col_actual] = pd.to_numeric(to_save[rep_amt_col_actual], errors="coerce")
                    to_save.to_csv(REPAYMENTS_CSV_PATH, index=False)
                    st.warning(f"Cache issue; wrote fresh repayments CSV. Rows: {len(to_save)}. Error: {e}")
                    # Clean data types for Arrow compatibility
                    to_save_display = to_save.copy()
                    for col in to_save_display.columns:
                        if to_save_display[col].dtype == 'object':
                            to_save_display[col] = to_save_display[col].astype(str)
                    st.dataframe(to_save_display, width='stretch')
            else:
                to_save = _clean_frame_for_csv(df_rep.drop(columns=["_rkey"], errors="ignore"))
                rep_amt_col_actual = _find_column_case_insensitive(to_save, ["repayment_amount", "repayment amount", "amount", "payment", "paid"]) 
                if rep_amt_col_actual:
                    to_save[rep_amt_col_actual] = (
                        to_save[rep_amt_col_actual].astype(str).str.replace(",", "", regex=False)
                    )
                    to_save[rep_amt_col_actual] = pd.to_numeric(to_save[rep_amt_col_actual], errors="coerce")
                to_save.to_csv(REPAYMENTS_CSV_PATH, index=False)
                st.success(f"Saved repayments CSV with {len(to_save)} rows.")
                # Clean data types for Arrow compatibility
                to_save_display = to_save.copy()
                for col in to_save_display.columns:
                    if to_save_display[col].dtype == 'object':
                        to_save_display[col] = to_save_display[col].astype(str)
                st.dataframe(to_save_display, width='stretch')

            # --- Repayments KPIs (two rows) ---
            try:
                # Prefer CSV for complete view
                df_kpi = None
                if os.path.exists(REPAYMENTS_CSV_PATH):
                    try:
                        df_kpi = pd.read_csv(REPAYMENTS_CSV_PATH)
                        df_kpi = _normalize_columns(df_kpi)
                        df_kpi = _clean_frame_for_csv(df_kpi)
                    except Exception:
                        df_kpi = None
                if df_kpi is None:
                    df_kpi = df_rep.copy()

                # Identify key columns
                repay_id_col = _find_column_case_insensitive(df_kpi, ["repayment_id", "repayment id"]) 
                repay_amt_col = _find_column_case_insensitive(df_kpi, ["repayment_amount", "repayment amount", "amount", "payment", "paid"]) 
                repay_date_col = _find_column_case_insensitive(df_kpi, ["repayment_collected_date", "repayment collected date", "paid date"]) 

                # Total records
                if repay_id_col:
                    total_records_repay = df_kpi[repay_id_col].astype(str).nunique()
                else:
                    total_records_repay = len(df_kpi)

                # Date parsing
                repay_dates_all = pd.to_datetime(df_kpi[repay_date_col], dayfirst=True, errors="coerce") if repay_date_col else pd.to_datetime(pd.Series([], dtype=str))
                now_dt = datetime.today()
                mask_month_rep = repay_dates_all.dt.month.eq(now_dt.month) & repay_dates_all.dt.year.eq(now_dt.year) if repay_date_col else pd.Series([], dtype=bool)
                mask_today_rep = repay_dates_all.dt.date.eq(now_dt.date()) if repay_date_col else pd.Series([], dtype=bool)

                # Amount series
                if repay_amt_col:
                    amt_series_all = pd.to_numeric(df_kpi[repay_amt_col].astype(str).str.replace(",", "", regex=False), errors="coerce").fillna(0.0)
                else:
                    amt_series_all = pd.Series([], dtype=float)

                # Current month count (unique repayment ids this month if available)
                if repay_id_col and repay_date_col:
                    current_month_count_repay = int(df_kpi.loc[mask_month_rep, repay_id_col].astype(str).nunique())
                elif repay_date_col:
                    current_month_count_repay = int(mask_month_rep.sum())
                else:
                    current_month_count_repay = 0

                # Amounts
                amount_repaid_month = float(amt_series_all.loc[mask_month_rep].sum()) if repay_date_col else 0.0
                amount_repaid_today = float(amt_series_all.loc[mask_today_rep].sum()) if repay_date_col else 0.0

                # First KPI row (repayments)
                r_kpi_cols = st.columns(4)
                r_kpi_cols[0].metric("Repayments - Total Records", f"{total_records_repay}")
                r_kpi_cols[1].metric("Repayments - Current Month", f"{current_month_count_repay}")
                r_kpi_cols[2].metric("Amount Repaid (This Month)", f"{amount_repaid_month:,.2f}")
                r_kpi_cols[3].metric("Amount Repaid Today", f"{amount_repaid_today:,.2f}")

                # Second KPI row (repayments by branch - rounded integers, hide zeros)
                branch_code_col = _find_column_case_insensitive(df_kpi, ["branch_id", "branch id"]) 
                branch_name_col = _find_column_case_insensitive(df_kpi, ["branch"]) 
                if repay_date_col and (branch_code_col or branch_name_col) and repay_amt_col:
                    df_b = pd.DataFrame({
                        "_date": repay_dates_all,
                        "_amt": amt_series_all,
                        "_branch": df_kpi[branch_code_col] if branch_code_col else df_kpi[branch_name_col]
                    })
                    df_b = df_b.loc[df_b["_date"].dt.month.eq(now_dt.month) & df_b["_date"].dt.year.eq(now_dt.year)]
                    if not df_b.empty:
                        if branch_code_col:
                            df_b["BranchName"] = df_b["_branch"].apply(_branch_code_to_name)
                        else:
                            df_b["BranchName"] = df_b["_branch"].astype(str)
                        grouped_b = df_b.groupby("BranchName")["_amt"].sum()
                        ordered_pairs = [
                            (75350, "Thika Branch"),
                            (8550, "TOWN BRANCH"),
                            (55886, "Utawala Branch"),
                            (12936, "BURUBURU BRANCH"),
                            (63796, "Kiambu Branch"),
                            (27133, "KIlimani Branch"),
                            (8678, "Mombasa Road"),
                        ]
                        names = [nm for _, nm in ordered_pairs]
                        non_zero_pairs = [(nm, float(grouped_b.get(nm, 0.0))) for nm in names if float(grouped_b.get(nm, 0.0)) > 0]
                        if len(non_zero_pairs) > 0:
                            st.caption("Repayments this month by branch")
                            r_cols_branch = st.columns(len(non_zero_pairs))
                            for i, (nm, vl) in enumerate(non_zero_pairs):
                                r_cols_branch[i].metric(f"{nm}", f"{int(round(vl)):,}")
            except Exception:
                pass

            # Build current-year daily totals for repayments from full cache (if available) and plot alongside disbursements
            combined_plotted = False
            try:
                # Prefer full cache for time series to avoid zeros from narrow fetch windows
                df_rep_source = None
                if os.path.exists(REPAYMENTS_CSV_PATH):
                    try:
                        df_rep_source = pd.read_csv(REPAYMENTS_CSV_PATH)
                        df_rep_source = _normalize_columns(df_rep_source)
                        df_rep_source = _clean_frame_for_csv(df_rep_source)
                    except Exception:
                        df_rep_source = None
                if df_rep_source is None:
                    df_rep_source = df_rep.copy()

                rep_value_col = _find_column_case_insensitive(df_rep_source, ["repayment_amount", "repayment amount"]) 
                if rep_value_col is None:
                    rep_value_col = _find_column_case_insensitive(df_rep_source, ["amount", "payment", "paid"]) 
                rep_date_col = _find_column_case_insensitive(df_rep_source, ["repayment_collected_date", "repayment collected date"]) 
                if rep_date_col is None:
                    rep_date_col = _find_column_case_insensitive(df_rep_source, ["paid date"]) 

                if rep_value_col and rep_date_col:
                    rep_dates_all = pd.to_datetime(df_rep_source[rep_date_col], dayfirst=True, errors="coerce")
                    rep_amount_all = pd.to_numeric(
                        df_rep_source[rep_value_col].astype(str).str.replace(",", "", regex=False),
                        errors="coerce"
                    ).fillna(0.0)
                    df_rep_all = pd.DataFrame({"_date": rep_dates_all, "Repaid": rep_amount_all})
                    now_dt = datetime.today()
                    df_rep_year = df_rep_all.loc[df_rep_all["_date"].dt.year.eq(now_dt.year)]
                    if not df_rep_year.empty:
                        rep_ts_year = (
                            df_rep_year
                            .groupby(pd.Grouper(key="_date", freq="D"))["Repaid"]
                            .sum()
                            .reset_index()
                            .sort_values("_date")
                        )

                        # Align with disbursements for the same period
                        if 'disb_ts_year' in locals() and disb_ts_year is not None:
                            # Disbursements monthly aggregation
                            disb_series = disb_ts_year.copy()
                            disb_series = disb_series.rename(columns={"Disbursed Date": "_date", "Principal": "Disbursed"})
                            if "Disbursed" not in disb_series.columns:
                                for c in list(disb_series.columns):
                                    if c != "_date":
                                        disb_series = disb_series.rename(columns={c: "Disbursed"})
                                        break
                            # Month order and display name
                            disb_series["MonthOrder"] = pd.to_datetime(disb_series["_date"]).dt.month
                            disb_series["Month"] = pd.to_datetime(disb_series["_date"]).dt.month_name()
                            disb_month = (
                                disb_series.groupby(["MonthOrder", "Month"], as_index=False)["Disbursed"].sum()
                            )
                            # Repayments monthly aggregation
                            rep_ts_year_month = rep_ts_year.copy()
                            rep_ts_year_month["MonthOrder"] = pd.to_datetime(rep_ts_year_month["_date"]).dt.month
                            rep_ts_year_month["Month"] = pd.to_datetime(rep_ts_year_month["_date"]).dt.month_name()
                            rep_month = (
                                rep_ts_year_month.groupby(["MonthOrder", "Month"], as_index=False)["Repaid"].sum()
                            )
                            # Merge monthly and plot clustered bars
                            merged_month = pd.merge(disb_month, rep_month, on=["MonthOrder", "Month"], how="outer").fillna(0)
                            merged_month = merged_month.sort_values("MonthOrder")
                            # Long format for clustered bars
                            merged_long = merged_month.melt(id_vars=["MonthOrder", "Month"], value_vars=["Disbursed", "Repaid"],
                                                           var_name="Series", value_name="Value")
                            st.caption("Monthly totals (current year): Disbursed vs Repaid")
                            chart_clustered = (
                                alt.Chart(merged_long)
                                .mark_bar()
                                .encode(
                                    x=alt.X('Month:N', sort=list(merged_month["Month"].unique()), title='Month'),
                                    xOffset=alt.XOffset('Series:N'),
                                    y=alt.Y('Value:Q', title='Amount'),
                                    color=alt.Color('Series:N', legend=alt.Legend(title=''))
                                )
                                .properties(height=320)
                            )
                            st.altair_chart(chart_clustered, use_container_width=True)
                            combined_plotted = True
            except Exception:
                combined_plotted = False
            if not combined_plotted:
                # Fallback: show repayments current-year chart alone if disbursements not available
                try:
                    df_src = None
                    if os.path.exists(REPAYMENTS_CSV_PATH):
                        try:
                            df_src = pd.read_csv(REPAYMENTS_CSV_PATH)
                            df_src = _normalize_columns(df_src)
                            df_src = _clean_frame_for_csv(df_src)
                        except Exception:
                            df_src = None
                    if df_src is None:
                        df_src = df_rep

                    rep_value_col = _find_column_case_insensitive(df_src, ["repayment_amount", "repayment amount", "amount", "payment", "paid"]) 
                    rep_date_col = _find_column_case_insensitive(df_src, ["repayment_collected_date", "repayment collected date", "paid date"]) 
                    if rep_value_col and rep_date_col:
                        rep_dates = pd.to_datetime(df_src[rep_date_col], dayfirst=True, errors="coerce")
                        rep_amount_series2 = pd.to_numeric(
                            df_src[rep_value_col].astype(str).str.replace(",", "", regex=False),
                            errors="coerce"
                        ).fillna(0.0)
                        now_dt = datetime.today()
                        mask_year2 = rep_dates.dt.year.eq(now_dt.year)
                        rep_ts_only = (
                            pd.DataFrame({"_date": rep_dates[mask_year2], "Repaid": rep_amount_series2[mask_year2]})
                            .groupby(pd.Grouper(key="_date", freq="D"))["Repaid"]
                            .sum()
                            .reset_index()
                            .sort_values("_date")
                        )
                        if not rep_ts_only.empty:
                            rep_ts_only["MonthOrder"] = pd.to_datetime(rep_ts_only["_date"]).dt.month
                            rep_ts_only["Month"] = pd.to_datetime(rep_ts_only["_date"]).dt.month_name()
                            rep_month_only = rep_ts_only.groupby(["MonthOrder", "Month"], as_index=False)["Repaid"].sum()
                            rep_month_only = rep_month_only.sort_values("MonthOrder").set_index("Month")
                            st.caption("Monthly totals (current year): Repaid")
                            st.bar_chart(rep_month_only)
                except Exception:
                    pass

    except Exception as e:
        st.error(f"⚠️ Repayments fetch failed: {e}")


# --- Advanced Loans: principal_balance_amount summaries ---
st.divider()
st.subheader("PAR CALCULATION AND SUMMARY")

ADV_LOANS_API_URL = "https://api-main.loandisk.com/7125/{branch_id}/advanced_search_loans"
ADV_LOANS_CSV_PATH_1 = "advanced_loans_past_missed_arrears.csv"
ADV_LOANS_CSV_PATH_2 = "advanced_loans_status_1.csv"

# Determine if we need to fetch fresh data (check if CSVs exist and are recent)
need_fetch_adv_loans = True
if os.path.exists(ADV_LOANS_CSV_PATH_1) and os.path.exists(ADV_LOANS_CSV_PATH_2):
    try:
        # Check if files are recent (less than 1 hour old)
        import time
        file1_age = time.time() - os.path.getmtime(ADV_LOANS_CSV_PATH_1)
        file2_age = time.time() - os.path.getmtime(ADV_LOANS_CSV_PATH_2)
        if file1_age < 3600 and file2_age < 3600:  # Less than 1 hour
            need_fetch_adv_loans = False
    except Exception:
        pass

if need_fetch_adv_loans:
    st.info("Fetching latest advanced loans data...")
else:
    st.info("Using cached advanced loans data (refresh page to force update)")

try:
    # Load data from cache or fetch fresh
    df1_all = []
    df2_all = []
    total_pba_1 = 0.0
    total_pba_2 = 0.0

    if not need_fetch_adv_loans:
        # Load from cached CSVs
        try:
            if os.path.exists(ADV_LOANS_CSV_PATH_1):
                df1_all = [pd.read_csv(ADV_LOANS_CSV_PATH_1)]
                # Calculate total from cached data
                if not df1_all[0].empty:
                    p_col = None
                    for cand in ["principal_balance_amount", "Principal Balance Amount", "principal_balance", "Principal Balance"]:
                        if cand in df1_all[0].columns:
                            p_col = cand
                            break
                    if p_col is None:
                        lowered = {str(c).strip().lower(): c for c in df1_all[0].columns}
                        if "principal_balance_amount" in lowered:
                            p_col = lowered["principal_balance_amount"]
                    if p_col is not None:
                        series = df1_all[0][p_col].astype(str).str.replace(",", "", regex=False)
                        total_pba_1 = float(pd.to_numeric(series, errors="coerce").sum())
            
            if os.path.exists(ADV_LOANS_CSV_PATH_2):
                df2_all = [pd.read_csv(ADV_LOANS_CSV_PATH_2)]
                # Calculate total from cached data
                if not df2_all[0].empty:
                    p_col = None
                    for cand in ["principal_balance_amount", "Principal Balance Amount", "principal_balance", "Principal Balance"]:
                        if cand in df2_all[0].columns:
                            p_col = cand
                            break
                    if p_col is None:
                        lowered = {str(c).strip().lower(): c for c in df2_all[0].columns}
                        if "principal_balance_amount" in lowered:
                            p_col = lowered["principal_balance_amount"]
                    if p_col is not None:
                        series = df2_all[0][p_col].astype(str).str.replace(",", "", regex=False)
                        total_pba_2 = float(pd.to_numeric(series, errors="coerce").sum())
        except Exception as e:
            st.warning(f"Failed to load cached data: {e}. Fetching fresh data...")
            need_fetch_adv_loans = True

    if need_fetch_adv_loans:
        # Fetch fresh data from API
        st.info("🔄 Fetching fresh advanced loans data...")
        def _extract_results_generic(data_obj):
            # Flatten helper: returns a flat list of dicts
            def _flatten(items):
                out = []
                stack = list(items if isinstance(items, list) else [items])
                while stack:
                    v = stack.pop(0)
                    if isinstance(v, dict):
                        out.append(v)
                    elif isinstance(v, list):
                        stack[:0] = v
                return out

            if isinstance(data_obj, dict) and "response" in data_obj:
                r = data_obj.get("response", {})
                raw = r.get("Results", [])
                if isinstance(raw, dict):
                    try:
                        raw_list = [raw[k] for k in sorted(raw.keys(), key=lambda x: int(x))]
                    except Exception:
                        raw_list = list(raw.values())
                else:
                    raw_list = raw if isinstance(raw, list) else []
                return _flatten(raw_list)
            elif isinstance(data_obj, list):
                return _flatten(data_obj)
            return []

        # Helper: post with JSON first; on "Wrong content type" retry as form-encoded
        def _post_adv(branch_id: int, payload_dict: dict):
            try:
                url = ADV_LOANS_API_URL.format(branch_id=branch_id)
                r = requests.post(url, headers=HEADERS, json=payload_dict)
                data = r.json() if r.status_code == 200 else {}
                err = data.get("error", {}) if isinstance(data, dict) else {}
                if isinstance(err, dict) and str(err.get("message", "")).lower().strip() == "wrong content type":
                    # retry with form encoding
                    form_headers = dict(HEADERS)
                    form_headers["Content-Type"] = "application/x-www-form-urlencoded"
                    r = requests.post(url, headers=form_headers, data=payload_dict)
                    data = r.json() if r.status_code == 200 else {}
                return r.status_code, data
            except Exception:
                return 0, {}

        # First request: past maturity + missed + arrears (5||16||6)
        payload_past_missed_arrears = {
            "from": 1,
            "count": 500,
            "loan_status_id": "5||16||6",
        }
        total_pba_1 = 0.0
        df1_all = []
        for br in REPAYMENT_BRANCH_IDS:
            status1, data1 = _post_adv(br, payload_past_missed_arrears)
            results1 = _extract_results_generic(data1) if status1 == 200 else []
            df1 = (pd.json_normalize(results1) if len(results1) > 0 else pd.DataFrame())
            if not df1.empty:
                df1["_branch_id"] = br
                df1_all.append(df1)
                # robustly locate principal balance column
                p_col = None
                for cand in [
                    "principal_balance_amount", "Principal Balance Amount",
                    "principal_balance", "Principal Balance",
                ]:
                    if cand in df1.columns:
                        p_col = cand
                        break
                if p_col is None:
                    lowered = {str(c).strip().lower(): c for c in df1.columns}
                    if "principal_balance_amount" in lowered:
                        p_col = lowered["principal_balance_amount"]
                if p_col is not None:
                    series = df1[p_col].astype(str).str.replace(",", "", regex=False)
                    branch_sum = float(pd.to_numeric(series, errors="coerce").sum())
                    total_pba_1 += branch_sum
                    print(f"DEBUG: Branch {br}, column '{p_col}', sum: {branch_sum:,.2f}, total so far: {total_pba_1:,.2f}")
                else:
                    print(f"DEBUG: Branch {br}, no principal balance column found. Available columns: {list(df1.columns)}")
        # Save first request combined results to CSV for debugging
        try:
            if len(df1_all) > 0:
                pd.concat(df1_all, ignore_index=True).to_csv("advanced_loans_past_missed_arrears.csv", index=False)
            else:
                pd.DataFrame().to_csv("advanced_loans_past_missed_arrears.csv", index=False)
        except Exception:
            pass

        # Second request: active/open (status 1)
        payload_status_1 = {
            "from": 1,
            "count": 500,
            "loan_status_id": "1",
        }
        total_pba_2 = 0.0
        df2_all = []
        for br in REPAYMENT_BRANCH_IDS:
            status2, data2 = _post_adv(br, payload_status_1)
            results2 = _extract_results_generic(data2) if status2 == 200 else []
            df2 = (pd.json_normalize(results2) if len(results2) > 0 else pd.DataFrame())
            if not df2.empty:
                df2["_branch_id"] = br
                df2_all.append(df2)
                p_col2 = None
                for cand in [
                    "principal_balance_amount", "Principal Balance Amount",
                    "principal_balance", "Principal Balance",
                ]:
                    if cand in df2.columns:
                        p_col2 = cand
                        break
                if p_col2 is None:
                    lowered2 = {str(c).strip().lower(): c for c in df2.columns}
                    if "principal_balance_amount" in lowered2:
                        p_col2 = lowered2["principal_balance_amount"]
                if p_col2 is not None:
                    series2 = df2[p_col2].astype(str).str.replace(",", "", regex=False)
                    branch_sum2 = float(pd.to_numeric(series2, errors="coerce").sum())
                    total_pba_2 += branch_sum2
                    print(f"DEBUG: Branch {br} (Status 1), column '{p_col2}', sum: {branch_sum2:,.2f}, total so far: {total_pba_2:,.2f}")
                else:
                    print(f"DEBUG: Branch {br} (Status 1), no principal balance column found. Available columns: {list(df2.columns)}")
        # Save second request combined results to CSV for debugging
        try:
            if len(df2_all) > 0:
                pd.concat(df2_all, ignore_index=True).to_csv("advanced_loans_status_1.csv", index=False)
            else:
                pd.DataFrame().to_csv("advanced_loans_status_1.csv", index=False)
        except Exception:
            pass

    # Calculate PAR (Portfolio at Risk)
    par_percentage = (total_pba_1 / total_pba_2 * 100) if total_pba_2 > 0 else 0.0
    
    # Create branch-wise summary data
    branch_summary = []
    for br in REPAYMENT_BRANCH_IDS:
        # Get data for this branch from both requests
        branch_pba_1 = 0.0
        branch_pba_2 = 0.0
        
        # Find data for this branch in df1_all (Past maturity + Missed + Arrears)
        for df in df1_all:
            if not df.empty and "_branch_id" in df.columns:
                branch_df = df[df["_branch_id"] == br]
                if not branch_df.empty:
                    # Find principal balance column
                    p_col = None
                    for cand in ["principal_balance_amount", "Principal Balance Amount", "principal_balance", "Principal Balance"]:
                        if cand in branch_df.columns:
                            p_col = cand
                            break
                    if p_col is None:
                        lowered = {str(c).strip().lower(): c for c in branch_df.columns}
                        if "principal_balance_amount" in lowered:
                            p_col = lowered["principal_balance_amount"]
                    if p_col is not None:
                        series = branch_df[p_col].astype(str).str.replace(",", "", regex=False)
                        branch_pba_1 = float(pd.to_numeric(series, errors="coerce").sum())
        
        # Find data for this branch in df2_all (Status 1)
        for df in df2_all:
            if not df.empty and "_branch_id" in df.columns:
                branch_df = df[df["_branch_id"] == br]
                if not branch_df.empty:
                    # Find principal balance column
                    p_col = None
                    for cand in ["principal_balance_amount", "Principal Balance Amount", "principal_balance", "Principal Balance"]:
                        if cand in branch_df.columns:
                            p_col = cand
                            break
                    if p_col is None:
                        lowered = {str(c).strip().lower(): c for c in branch_df.columns}
                        if "principal_balance_amount" in lowered:
                            p_col = lowered["principal_balance_amount"]
                    if p_col is not None:
                        series = branch_df[p_col].astype(str).str.replace(",", "", regex=False)
                        branch_pba_2 = float(pd.to_numeric(series, errors="coerce").sum())
        
        # Calculate branch PAR
        branch_par = (branch_pba_1 / branch_pba_2 * 100) if branch_pba_2 > 0 else 0.0
        
        branch_summary.append({
            "Branch": _branch_code_to_name(br),
            "Past Maturity + Missed + Arrears": f"{branch_pba_1:,.2f}",
            "Status 1 (Active)": f"{branch_pba_2:,.2f}",
            "PAR (%)": f"{branch_par:.2f}"
        })
    
    # Display as cards
    k1, k2, k3 = st.columns(3)
    k1.metric("Sum principal_balance_amount (Past maturity + Missed + Arrears)", f"{total_pba_1:,.2f}")
    k2.metric("Sum principal_balance_amount (Status 1)", f"{total_pba_2:,.2f}")
    k3.metric("PAR (Portfolio at Risk)", f"{par_percentage:.2f}%")
    
    # Display branch-wise table
    st.subheader("Branch-wise PAR Analysis")
    if branch_summary:
        df_branch_summary = pd.DataFrame(branch_summary)
        st.dataframe(df_branch_summary, width='stretch')
    else:
        st.info("No branch data available")

except Exception as e:
    st.error(f"⚠️ Advanced loans summary failed: {e}")

# --- Advanced Loans: Status 10 (table) ---
st.divider()
st.subheader("Advanced Loans - Status 10")
try:
    # Local helpers (mirror behavior used above):
    def _post_adv_status10(branch_id: int, payload_dict: dict):
        try:
            url = ADV_LOANS_API_URL.format(branch_id=branch_id)
            r = requests.post(url, headers=HEADERS, json=payload_dict)
            data = r.json() if r.status_code == 200 else {}
            err = data.get("error", {}) if isinstance(data, dict) else {}
            if isinstance(err, dict) and str(err.get("message", "")).lower().strip() == "wrong content type":
                form_headers = dict(HEADERS)
                form_headers["Content-Type"] = "application/x-www-form-urlencoded"
                r = requests.post(url, headers=form_headers, data=payload_dict)
                data = r.json() if r.status_code == 200 else {}
            return r.status_code, data
        except Exception:
            return 0, {}

    def _extract_results_generic_status10(data_obj):
        def _flatten(items):
            out = []
            stack = list(items if isinstance(items, list) else [items])
            while stack:
                v = stack.pop(0)
                if isinstance(v, dict):
                    out.append(v)
                elif isinstance(v, list):
                    stack[:0] = v
            return out

        if isinstance(data_obj, dict) and "response" in data_obj:
            r = data_obj.get("response", {})
            raw = r.get("Results", [])
            if isinstance(raw, dict):
                try:
                    raw_list = [raw[k] for k in sorted(raw.keys(), key=lambda x: int(x))]
                except Exception:
                    raw_list = list(raw.values())
            else:
                raw_list = raw if isinstance(raw, list) else []
            return _flatten(raw_list)
        elif isinstance(data_obj, list):
            return _flatten(data_obj)
        return []

    payload_status_10 = {
        "from": 1,
        "count": 100,
        "loan_status_id": "10",
    }

    dfs_status10 = []
    for br in REPAYMENT_BRANCH_IDS:
        # Robust pagination supporting both page-based and offset-based semantics
        mode = "page"  # try page index first
        page_index = 1
        offset = 1
        max_pages = 2000
        collected_for_branch = 0
        total_for_branch = None
        seen_keys = set()
        while True:
            if mode == "page":
                payload_status_10["from"] = page_index
            else:
                payload_status_10["from"] = offset

            status, data = _post_adv_status10(br, payload_status_10)
            results = _extract_results_generic_status10(data) if status == 200 else []
            df_b = pd.json_normalize(results) if len(results) > 0 else pd.DataFrame()

            # metadata
            try:
                rmeta = data.get("response", {}) if isinstance(data, dict) else {}
                total_for_branch = rmeta.get("TotalResults", total_for_branch)
                returned = rmeta.get("ReturnResults")
                start_index = rmeta.get("StartIndex")
            except Exception:
                returned = None
                start_index = None

            if not df_b.empty:
                df_b["_branch_id"] = br
                try:
                    df_b["_branch_name"] = _branch_code_to_name(br)
                except Exception:
                    df_b["_branch_name"] = str(br)

                # optional de-duplication by a composite key if present
                dedupe_key = None
                for c in ["loan_id", "Loan Id", "id", "Id"]:
                    if c in df_b.columns:
                        dedupe_key = c
                        break
                if dedupe_key is not None:
                    df_b = df_b[~df_b[dedupe_key].astype(str).isin(seen_keys)]
                    seen_keys.update(df_b[dedupe_key].astype(str).tolist())

                if not df_b.empty:
                    dfs_status10.append(df_b)
                    collected_for_branch += len(df_b)
            else:
                # If page mode produced empty but we expect more, switch to offset once
                if mode == "page" and (isinstance(total_for_branch, int) and collected_for_branch < total_for_branch):
                    mode = "offset"
                    offset = collected_for_branch + 1
                    continue
                break

            # stop conditions
            if isinstance(total_for_branch, int) and collected_for_branch >= total_for_branch:
                break

            if mode == "page":
                # If page returned fewer than requested and no explicit total, assume last page
                if isinstance(returned, int) and isinstance(payload_status_10.get("count"), int) and returned < payload_status_10["count"]:
                    # but if collected < total, switch to offset to be safe
                    if isinstance(total_for_branch, int) and collected_for_branch < total_for_branch:
                        mode = "offset"
                        offset = collected_for_branch + 1
                        continue
                    break
                page_index += 1
                if page_index > max_pages:
                    break
            else:
                # offset mode: advance by returned or by count if missing
                step = returned if isinstance(returned, int) and returned > 0 else payload_status_10.get("count", 100)
                offset += step
                if step == 0:
                    break
                if offset > (total_for_branch or (collected_for_branch + 1) + 100000):
                    break

    if len(dfs_status10) > 0:
        out10 = pd.concat(dfs_status10, ignore_index=True)
        # Arrow safety: coerce object columns to string
        for c in out10.columns:
            if out10[c].dtype == "object":
                out10[c] = out10[c].astype(str)
        st.dataframe(out10, width='stretch')
    else:
        st.info("No records found for status 10 across selected branches.")

except Exception as e:
    st.error(f"⚠️ Status 10 fetch failed: {e}")