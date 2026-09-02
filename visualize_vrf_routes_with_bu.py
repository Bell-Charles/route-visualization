import json
import streamlit as st
import pandas as pd
import plotly.express as px
import io
import ijson # For memory-efficient parsing of large JSON files

st.set_page_config(layout="wide", page_title="VRF Routing Dashboard")

# --- DATA LOADING FUNCTIONS (OPTIMIZED FOR LARGE FILES) ---

@st.cache_data
def load_routing_data(uploaded_file):
    """Loads and flattens large JSON routing data iteratively to save memory."""
    if uploaded_file is None:
        return pd.DataFrame()

    flat_routes = []
    try:
        uploaded_file.seek(0)
        parser = ijson.items(uploaded_file, 'item')
        
        for vrf in parser:
            vrf_name = vrf.get("vrf_name", "unknown")
            for route in vrf.get("routes", []):
                prefix = route.get("prefix", "N/A")
                prefix_len = route.get("prefix_len", "N/A")
                protocol = route.get("protocol_name")
                is_candidate_default = route.get("is_candidate_default")
                
                for path in route.get("paths", []):
                    next_hop_vrf = path.get("next_hop_vrf", vrf_name)
                    is_leaked = (vrf_name != next_hop_vrf)
                    
                    flat_routes.append({
                        "VRF": vrf_name,
                        "Prefix": f"{prefix}/{prefix_len}",
                        "Protocol": protocol,
                        "Candidate_Default": is_candidate_default,
                        "Next_Hop": path.get("next_hop_ip"),
                        "Next_Hop_VRF": next_hop_vrf,
                        "Is_Leaked": is_leaked,
                        "Metric": path.get("metric"),
                        "Admin_Distance": path.get("administrative_distance")
                    })
                    
    except Exception as e:
        st.error(f"Error parsing large JSON file with ijson: {e}")
        return pd.DataFrame()

    df = pd.DataFrame(flat_routes)
    if not df.empty:
        # Unify categories for VRF columns to prevent comparison errors
        all_vrf_names = pd.unique(df[['VRF', 'Next_Hop_VRF']].values.ravel('K'))
        vrf_cat_type = pd.CategoricalDtype(categories=all_vrf_names, ordered=False)
        df['VRF'] = df['VRF'].astype(vrf_cat_type)
        df['Next_Hop_VRF'] = df['Next_Hop_VRF'].astype(vrf_cat_type)

        df["Protocol"] = df["Protocol"].astype("category")
        df["Is_Leaked"] = df["Is_Leaked"].astype(bool)
        
    return df

@st.cache_data
def load_bu_mapping(mapping_file):
    """Loads the VRF to BU mapping from a CSV file."""
    if mapping_file is None:
        return pd.DataFrame()
    try:
        df = pd.read_csv(mapping_file)
        df.columns = df.columns.str.strip()
        if 'vrf_name' in df.columns:
            df = df.rename(columns={'vrf_name': 'VRF'})
        df = df.drop_duplicates(subset='VRF', keep='first')
        return df
    except Exception as e:
        st.error(f"Error reading the VRF-BU mapping CSV: {e}")
        return pd.DataFrame()

# --- MAIN APP LAYOUT ---
st.title("🗺️ IOS-XR VRF Routing Dashboard")

# --- FILE UPLOADERS ---
st.sidebar.title("📤 File Upload")
uploaded_file = st.sidebar.file_uploader("Upload Routing JSON", type=['json'])
vrf_bu_mapping_file = st.sidebar.file_uploader("Upload VRF-BU Mapping CSV", type=['csv'])

if uploaded_file is None:
    st.info("Please upload a JSON file to begin analysis.")
    st.stop()

# --- DISPLAY FILENAMES ---
st.markdown(f"#### Analyzing data from: `{uploaded_file.name}`")
if vrf_bu_mapping_file is not None:
    st.markdown(f"**BU Mapping from:** `{vrf_bu_mapping_file.name}`")
st.markdown("---")

# --- DATA PROCESSING ---
df = load_routing_data(uploaded_file)
bu_mapping_df = load_bu_mapping(vrf_bu_mapping_file)

if df.empty:
    st.warning("The uploaded routing JSON is empty or could not be processed.")
    st.stop()

# Create a complete baseline dataframe with BU info that will NOT be filtered
unfiltered_df_with_bu = df.copy()
if not bu_mapping_df.empty and 'VRF' in bu_mapping_df.columns and 'BU' in bu_mapping_df.columns:
    unfiltered_df_with_bu = pd.merge(unfiltered_df_with_bu, bu_mapping_df[['VRF', 'BU']], on='VRF', how='left')
    unfiltered_df_with_bu['BU'] = unfiltered_df_with_bu['BU'].fillna('Unassigned')
else:
    unfiltered_df_with_bu['BU'] = 'Unassigned'


# --- SIDEBAR FILTERS ---
st.sidebar.header("⚙️ Filter Settings")
ignore_default_leaks = st.sidebar.toggle("🚫 Exclude leaks to 'default' VRF", value=False)
ignore_self_leaks = st.sidebar.toggle("🔄 Exclude leaking to same vrf", value=False, help="When ON, this will only show leaked routes.")

all_vrfs = sorted(df['VRF'].cat.categories)
selected_vrfs = st.sidebar.multiselect("Select Origin VRFs", all_vrfs, default=all_vrfs)

# --- APPLY FILTERS TO A *SEPARATE* DATAFRAME ---
filtered_df = unfiltered_df_with_bu[unfiltered_df_with_bu['VRF'].isin(selected_vrfs)].copy()

if ignore_default_leaks:
    filtered_df = filtered_df[filtered_df['Next_Hop_VRF'].astype(str) != 'default']

if ignore_self_leaks:
    filtered_df = filtered_df[filtered_df['VRF'].astype(str) != filtered_df['Next_Hop_VRF'].astype(str)]


# --- DASHBOARD METRICS (shows count for the filtered data) ---
st.metric("Total Active Paths Shown (in filtered view)", len(filtered_df))

# --- DYNAMIC COLOR MAPPING (based on the full dataset for consistency) ---
unique_bus = unfiltered_df_with_bu['BU'].unique()
colors = px.colors.qualitative.Plotly 
color_map = {bu: colors[i % len(colors)] for i, bu in enumerate(unique_bus) if bu != 'Unassigned'}
color_map['Unassigned'] = '#CCCCCC'


# --- CHARTS SECTION (uses the filtered data) ---
chart_col1, chart_col2, chart_col3 = st.columns(3)

with chart_col1:
    st.subheader("Paths per VRF (Stacked by BU)")
    if not filtered_df.empty:
        plot_df = filtered_df.groupby(['VRF', 'BU']).size().reset_index(name='count')
        fig_vrf = px.bar(
            plot_df,
            x='VRF',
            y='count',
            color='BU',
            labels={'count': 'Number of Routes', 'BU': 'Business Unit'},
            color_discrete_map=color_map
        )
        fig_vrf.update_layout(xaxis={'categoryorder':'total descending'})
        st.plotly_chart(fig_vrf, use_container_width=True)
    else:
        st.info("No data available for the current filter selection.")

with chart_col2:
    st.subheader("Route Distribution by BU/VRF")
    if not filtered_df.empty:
        path_hierarchy = ['BU', 'VRF'] if filtered_df['BU'].nunique() > 1 else ['VRF']
        fig_leak = px.sunburst(
            filtered_df,
            path=path_hierarchy,
            color='BU',
            color_discrete_map=color_map
        )
        fig_leak.update_traces(textinfo="label+percent root")
        st.plotly_chart(fig_leak, use_container_width=True)
    else:
        st.info("No data to display based on current filters.")

with chart_col3:
    st.subheader("Route Distribution by BU")
    if not unfiltered_df_with_bu.empty and 'BU' in unfiltered_df_with_bu.columns:
        bu_counts = unfiltered_df_with_bu['BU'].value_counts().reset_index()
        bu_counts.columns = ['BU', 'count']
        
        fig_pie = px.pie(
            bu_counts,
            names='BU',
            values='count',
            color='BU',
            color_discrete_map=color_map
        )
        fig_pie.update_traces(textinfo='percent+label', pull=[0.05 if i == 0 else 0 for i in range(len(bu_counts))])
        st.plotly_chart(fig_pie, use_container_width=True)
    else:
        st.info("No BU data to display.")

st.markdown("---")


# --- UNFILTERED BAR GRAPH ---
st.subheader("Total Routes per VRF (Full Dataset)")
if not unfiltered_df_with_bu.empty:
    raw_counts_df = unfiltered_df_with_bu['VRF'].value_counts().reset_index()
    raw_counts_df.columns = ['VRF', 'count']
    
    bu_for_coloring = unfiltered_df_with_bu[['VRF', 'BU']].drop_duplicates(subset=['VRF'])
    raw_counts_df = pd.merge(raw_counts_df, bu_for_coloring, on='VRF', how='left')

    fig_raw = px.bar(
        raw_counts_df,
        x='VRF',
        y='count',
        color='BU',
        labels={'count': 'Total Number of Routes', 'BU': 'Business Unit'},
        color_discrete_map=color_map
    )
    fig_raw.update_layout(xaxis={'categoryorder':'total descending'})
    st.plotly_chart(fig_raw, use_container_width=True)
else:
    st.info("No data available for this chart.")


# --- DATA GRID (uses the filtered data) ---
st.subheader("📂 Interactive Routing Table View")
if not filtered_df.empty:
    if 'page_number' not in st.session_state:
        st.session_state.page_number = 1
    
    page_size = 50
    total_rows = len(filtered_df)
    total_pages = max(1, (total_rows - 1) // page_size + 1)
    
    if st.session_state.page_number > total_pages:
        st.session_state.page_number = 1

    p_col1, p_col2, p_col3 = st.columns([1, 2, 1])
    if p_col1.button("⬅️ Previous") and st.session_state.page_number > 1:
        st.session_state.page_number -= 1
    if p_col3.button("Next ➡️") and st.session_state.page_number < total_pages:
        st.session_state.page_number += 1
    p_col2.write(f"Page **{st.session_state.page_number}** of **{total_pages}**")

    start_index = (st.session_state.page_number - 1) * page_size
    paginated_df = filtered_df.iloc[start_index:start_index + page_size]

    st.dataframe(
        paginated_df,
        use_container_width=True,
        column_config={
            "Is_Leaked": st.column_config.CheckboxColumn("Leaked?", default=False),
            "BU": st.column_config.Column("Business Unit", width="medium")
        },
        column_order=("VRF", "BU", "Prefix", "Is_Leaked", "Next_Hop_VRF", "Protocol", "Next_Hop")
    )
else:
    st.warning("⚠️ No routes matched your filter criteria.")


