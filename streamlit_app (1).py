import os
import json
import streamlit as st

st.set_page_config(
    page_title="Healthcare Cost & Diagnosis Analytics",
    page_icon=":hospital:",
    layout="wide",
)

conn = st.connection("snowflake", ttl=os.getenv("SNOWFLAKE_CONNECTION_TTL"))

STATE_ABBR_TO_NAME = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia",
}
STATE_NAME_TO_ABBR = {v: k for k, v in STATE_ABBR_TO_NAME.items()}


@st.cache_data
def load_encounter_types():
    df = conn.query(
        "SELECT DISTINCT ENCOUNTER_TYPE FROM HEALTHCARE_DW.CLINICAL.ENCOUNTERS ORDER BY 1"
    )
    return df["ENCOUNTER_TYPE"].tolist()


@st.cache_data
def load_diagnosis_categories():
    df = conn.query(
        "SELECT DISTINCT CATEGORY FROM HEALTHCARE_DW.CLINICAL.DIAGNOSES ORDER BY 1"
    )
    return df["CATEGORY"].tolist()


@st.cache_data
def load_geo_data(diagnosis_category: str):
    query = """
    SELECT
        z.STATE,
        COUNT(*) AS diagnosis_count,
        COUNT(DISTINCT p.PATIENT_ID) AS patient_count,
        ROUND(AVG(z.LATITUDE), 4) AS latitude,
        ROUND(AVG(z.LONGITUDE), 4) AS longitude
    FROM HEALTHCARE_DW.CLINICAL.DIAGNOSES d
    JOIN HEALTHCARE_DW.CORE.PATIENTS p ON d.PATIENT_ID = p.PATIENT_ID
    JOIN U_S_ZIP_CODE_METADATA_WITH_GEOMETRY.PUBLIC.ZIP_CODE_META_SHARE z
        ON p.ZIP_CODE = z.ZIP_CODE
    WHERE d.CATEGORY = ?
      AND z.LATITUDE IS NOT NULL
      AND z.LONGITUDE IS NOT NULL
      AND z.LATITUDE BETWEEN 24 AND 50
      AND z.LONGITUDE BETWEEN -130 AND -65
    GROUP BY z.STATE
    ORDER BY diagnosis_count DESC
    """
    return conn.query(query, params=[diagnosis_category])


@st.cache_data
def load_data(encounter_types: tuple, chronic_filter: str):
    type_filter = ""
    if encounter_types:
        placeholders = ", ".join([f"'{t}'" for t in encounter_types])
        type_filter = f"AND e.ENCOUNTER_TYPE IN ({placeholders})"

    chronic_filter_sql = ""
    if chronic_filter == "Chronic Only":
        chronic_filter_sql = "AND d.IS_CHRONIC = TRUE"
    elif chronic_filter == "Non-Chronic Only":
        chronic_filter_sql = "AND d.IS_CHRONIC = FALSE"

    query = f"""
    WITH encounter_dx AS (
        SELECT
            d.CATEGORY AS diagnosis_category,
            d.IS_CHRONIC,
            e.ENCOUNTER_ID,
            e.ENCOUNTER_TYPE,
            e.LENGTH_OF_STAY_DAYS,
            c.BILLED_AMOUNT
        FROM HEALTHCARE_DW.CLINICAL.DIAGNOSES d
        JOIN HEALTHCARE_DW.CLINICAL.ENCOUNTERS e ON d.ENCOUNTER_ID = e.ENCOUNTER_ID
        JOIN HEALTHCARE_DW.BILLING.CLAIMS c ON e.ENCOUNTER_ID = c.ENCOUNTER_ID
        WHERE 1=1 {type_filter} {chronic_filter_sql}
    ),
    rx_data AS (
        SELECT
            d.CATEGORY AS diagnosis_category,
            COUNT(p.PRESCRIPTION_ID) AS total_prescriptions,
            AVG(p.COST) AS avg_rx_cost,
            SUM(p.COST) AS total_rx_cost
        FROM HEALTHCARE_DW.CLINICAL.DIAGNOSES d
        JOIN HEALTHCARE_DW.CLINICAL.ENCOUNTERS e ON d.ENCOUNTER_ID = e.ENCOUNTER_ID
        JOIN HEALTHCARE_DW.PHARMACY.PRESCRIPTIONS p ON d.ENCOUNTER_ID = p.ENCOUNTER_ID
        WHERE 1=1 {type_filter} {chronic_filter_sql}
        GROUP BY d.CATEGORY
    )
    SELECT
        ed.diagnosis_category,
        COUNT(DISTINCT ed.ENCOUNTER_ID) AS encounters,
        ROUND(AVG(ed.LENGTH_OF_STAY_DAYS), 2) AS avg_los_days,
        ROUND(AVG(ed.BILLED_AMOUNT), 2) AS avg_cost_per_stay,
        ROUND(AVG(ed.BILLED_AMOUNT / NULLIF(ed.LENGTH_OF_STAY_DAYS, 0)), 2) AS avg_cost_per_day,
        ROUND(SUM(ed.BILLED_AMOUNT), 0) AS total_encounter_billed,
        COALESCE(rx.total_prescriptions, 0) AS total_prescriptions,
        COALESCE(ROUND(rx.avg_rx_cost, 2), 0) AS avg_rx_cost,
        COALESCE(ROUND(rx.total_rx_cost, 0), 0) AS total_rx_cost,
        ROUND(SUM(ed.BILLED_AMOUNT), 0) + COALESCE(ROUND(rx.total_rx_cost, 0), 0) AS total_combined_cost
    FROM encounter_dx ed
    LEFT JOIN rx_data rx ON ed.diagnosis_category = rx.diagnosis_category
    GROUP BY ed.diagnosis_category, rx.total_prescriptions, rx.avg_rx_cost, rx.total_rx_cost
    ORDER BY total_combined_cost DESC
    """
    return conn.query(query)


# --- Sidebar Filters ---
st.sidebar.header("Filters")

encounter_types_list = load_encounter_types()
selected_types = st.sidebar.multiselect(
    "Encounter Type",
    encounter_types_list,
    default=encounter_types_list,
)

chronic_filter = st.sidebar.radio(
    "Chronic Condition",
    ["All", "Chronic Only", "Non-Chronic Only"],
    index=0,
)

horizontal = st.sidebar.toggle("Horizontal Bars", value=False)

st.sidebar.divider()
if st.sidebar.button("Clear Cache & Refresh"):
    load_data.clear()
    load_encounter_types.clear()
    load_diagnosis_categories.clear()
    load_geo_data.clear()
    st.rerun()

# --- Load Data ---
with st.spinner("Loading data..."):
    df = load_data(tuple(selected_types), chronic_filter)

# --- Title ---
st.title("Healthcare Cost & Diagnosis Analytics")

# --- KPI Row ---
total_encounters = int(df["ENCOUNTERS"].sum())
total_combined = int(df["TOTAL_COMBINED_COST"].sum())
avg_los = round(df["AVG_LOS_DAYS"].mean(), 1) if len(df) > 0 else 0
avg_cost_stay = round(df["AVG_COST_PER_STAY"].mean(), 0) if len(df) > 0 else 0
total_rx = int(df["TOTAL_PRESCRIPTIONS"].sum())

with st.container(horizontal=True):
    st.metric("Total Encounters", f"{total_encounters:,}", border=True)
    st.metric("Total Combined Cost", f"${total_combined:,.0f}", border=True)
    st.metric("Avg Length of Stay", f"{avg_los} days", border=True)
    st.metric("Avg Cost / Stay", f"${avg_cost_stay:,.0f}", border=True)
    st.metric("Total Prescriptions", f"{total_rx:,}", border=True)

st.divider()

# --- Chart Row 1: Total Combined Cost + Avg LOS ---
col1, col2 = st.columns(2)

with col1:
    with st.container(border=True):
        st.subheader("Total Combined Cost by Diagnosis")
        chart_df = df[["DIAGNOSIS_CATEGORY", "TOTAL_COMBINED_COST"]].sort_values(
            "TOTAL_COMBINED_COST", ascending=True
        )
        st.bar_chart(
            chart_df,
            x="DIAGNOSIS_CATEGORY",
            y="TOTAL_COMBINED_COST",
            horizontal=horizontal,
            color="#2563eb",
        )

with col2:
    with st.container(border=True):
        st.subheader("Average Length of Stay (days)")
        los_df = df[["DIAGNOSIS_CATEGORY", "AVG_LOS_DAYS"]].sort_values(
            "AVG_LOS_DAYS", ascending=True
        )
        st.bar_chart(
            los_df,
            x="DIAGNOSIS_CATEGORY",
            y="AVG_LOS_DAYS",
            horizontal=horizontal,
            color="#16a34a",
        )

# --- Chart Row 2: Cost per Stay vs Cost per Day + Rx Volume ---
col3, col4 = st.columns(2)

with col3:
    with st.container(border=True):
        st.subheader("Avg Cost per Stay vs. Cost per Day")
        cost_compare_df = df[
            ["DIAGNOSIS_CATEGORY", "AVG_COST_PER_STAY", "AVG_COST_PER_DAY"]
        ].sort_values("AVG_COST_PER_STAY", ascending=True)
        st.bar_chart(
            cost_compare_df,
            x="DIAGNOSIS_CATEGORY",
            y=["AVG_COST_PER_STAY", "AVG_COST_PER_DAY"],
            horizontal=horizontal,
            color=["#2563eb", "#f59e0b"],
        )

with col4:
    with st.container(border=True):
        st.subheader("Prescription Volume by Diagnosis")
        rx_df = df[["DIAGNOSIS_CATEGORY", "TOTAL_PRESCRIPTIONS"]].sort_values(
            "TOTAL_PRESCRIPTIONS", ascending=True
        )
        st.bar_chart(
            rx_df,
            x="DIAGNOSIS_CATEGORY",
            y="TOTAL_PRESCRIPTIONS",
            horizontal=horizontal,
            color="#9333ea",
        )

# --- Chart Row 3: Cost Composition ---
with st.container(border=True):
    st.subheader("Cost Composition: Encounter vs. Rx")
    composition_df = df[
        ["DIAGNOSIS_CATEGORY", "TOTAL_ENCOUNTER_BILLED", "TOTAL_RX_COST"]
    ].sort_values("TOTAL_ENCOUNTER_BILLED", ascending=True)
    composition_df = composition_df.rename(
        columns={
            "TOTAL_ENCOUNTER_BILLED": "Encounter Cost",
            "TOTAL_RX_COST": "Rx Cost",
        }
    )
    st.bar_chart(
        composition_df,
        x="DIAGNOSIS_CATEGORY",
        y=["Encounter Cost", "Rx Cost"],
        horizontal=horizontal,
        color=["#2563eb", "#ef4444"],
        stack=True,
    )

# --- Geographic Map: Diagnoses by ZIP Code ---
st.divider()
with st.container(border=True):
    st.subheader("Geographic Distribution of Diagnoses")
    dx_categories = load_diagnosis_categories()
    selected_dx = st.selectbox("Diagnosis Category", dx_categories, index=dx_categories.index("Musculoskeletal") if "Musculoskeletal" in dx_categories else 0)

    with st.spinner("Loading map data..."):
        geo_df = load_geo_data(selected_dx)

    if len(geo_df) > 0:
        with st.container(horizontal=True):
            st.metric("States", f"{len(geo_df):,}", border=True)
            st.metric("Total Diagnoses", f"{int(geo_df['DIAGNOSIS_COUNT'].sum()):,}", border=True)
            st.metric("Unique Patients", f"{int(geo_df['PATIENT_COUNT'].sum()):,}", border=True)
            st.metric("Top State", geo_df.iloc[0]["STATE"], border=True)

        import pydeck as pdk

        map_df = geo_df.copy()
        max_count = map_df["DIAGNOSIS_COUNT"].max()
        min_count = map_df["DIAGNOSIS_COUNT"].min()
        count_range = max_count - min_count if max_count != min_count else 1
        map_df["normalized"] = (map_df["DIAGNOSIS_COUNT"] - min_count) / count_range
        map_df["r"] = (map_df["normalized"] * 180 + 40).astype(int)
        map_df["g"] = ((1 - map_df["normalized"]) * 80 + 20).astype(int)
        map_df["b"] = ((1 - map_df["normalized"]) * 40 + 30).astype(int)
        map_df["a"] = 200
        map_df["radius"] = (map_df["normalized"] * 120000 + 30000).astype(int)

        circles = pdk.Layer(
            "ScatterplotLayer",
            data=map_df,
            get_position=["LONGITUDE", "LATITUDE"],
            get_radius="radius",
            get_fill_color=["r", "g", "b", "a"],
            pickable=True,
            stroked=True,
            get_line_color=[40, 40, 40, 160],
            line_width_min_pixels=1,
        )

        labels = pdk.Layer(
            "TextLayer",
            data=map_df,
            get_position=["LONGITUDE", "LATITUDE"],
            get_text="STATE",
            get_size=14,
            get_color=[0, 0, 0, 220],
            get_alignment_baseline="'center'",
            get_text_anchor="'middle'",
            font_family="'Arial, sans-serif'",
            font_weight=700,
        )

        view_state = pdk.ViewState(
            latitude=39.0,
            longitude=-96.0,
            zoom=3.5,
            pitch=0,
        )

        tooltip = {
            "html": "<b>{STATE}</b><br/>Diagnoses: {DIAGNOSIS_COUNT}<br/>Patients: {PATIENT_COUNT}",
            "style": {"backgroundColor": "#1e293b", "color": "white", "fontSize": "14px", "padding": "8px 12px"},
        }

        st.pydeck_chart(pdk.Deck(
            layers=[circles, labels],
            initial_view_state=view_state,
            tooltip=tooltip,
            map_style="mapbox://styles/mapbox/outdoors-v12",
        ))

        col_legend_l, col_legend_r = st.columns([1, 1])
        with col_legend_l:
            st.caption(f"Circle size and color intensity = prevalence of **{selected_dx}** diagnoses per state. Hover for details.")
        with col_legend_r:
            top5 = map_df.nlargest(5, "DIAGNOSIS_COUNT")[["STATE", "DIAGNOSIS_COUNT", "PATIENT_COUNT"]]
            top5.columns = ["State", "Diagnoses", "Patients"]
            st.caption("**Top 5 States**")
            st.dataframe(top5, hide_index=True, use_container_width=True)
    else:
        st.info("No geographic data available for the selected diagnosis category.")

# --- Data Table ---
st.divider()
with st.container(border=True):
    st.subheader("Detailed Data")
    display_df = df.copy()
    display_df.columns = [
        "Diagnosis Category",
        "Encounters",
        "Avg LOS (days)",
        "Avg Cost/Stay",
        "Avg Cost/Day",
        "Total Encounter Billed",
        "Total Prescriptions",
        "Avg Rx Cost",
        "Total Rx Cost",
        "Total Combined Cost",
    ]
    st.dataframe(
        display_df,
        hide_index=True,
        use_container_width=True,
        column_config={
            "Avg Cost/Stay": st.column_config.NumberColumn(format="$%.2f"),
            "Avg Cost/Day": st.column_config.NumberColumn(format="$%.2f"),
            "Total Encounter Billed": st.column_config.NumberColumn(format="$%d"),
            "Avg Rx Cost": st.column_config.NumberColumn(format="$%.2f"),
            "Total Rx Cost": st.column_config.NumberColumn(format="$%d"),
            "Total Combined Cost": st.column_config.NumberColumn(format="$%d"),
        },
    )
