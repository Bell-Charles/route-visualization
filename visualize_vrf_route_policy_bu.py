import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import json
from itertools import chain

st.set_page_config(layout="wide", page_title="Route Policy Complexity Dashboard")

# --- Initialize Session State for Persistent BU Colors ---
if 'bu_color_map' not in st.session_state:
    st.session_state.bu_color_map = {}

# --- DATA LOADING AND PROCESSING FUNCTIONS ---

@st.cache_data
def load_policy_data(uploaded_file):
    """Loads and flattens the route policy JSON data."""
    if uploaded_file is None:
        return pd.DataFrame(), 0

    try:
        uploaded_file.seek(0)
        data = json.load(uploaded_file)
        
        flat_policies = []
        policy_attributes = [
            "num_execution_paths", 
            "num_set_refs", 
            "num_filter", 
            "num_modify"
        ]

        for vrf_data in data:
            vrf_name = vrf_data.get("vrf_name")
            
            ipv4_policies_to_process = []
            if vrf_data.get("imports", {}).get("ipv4"):
                ipv4_policies_to_process.append(vrf_data["imports"]["ipv4"])
            if vrf_data.get("exports", {}).get("ipv4"):
                ipv4_policies_to_process.append(vrf_data["exports"]["ipv4"])
            
            for policy_container in ipv4_policies_to_process:
                policy = policy_container.get("route-policy")
                if policy and policy.get("name"): 
                    policy_data = {"vrf_name": vrf_name, "policy_name": policy.get("name")}
                    for attr in policy_attributes:
                        value = policy.get(attr)
                        if value is None and attr == "num_set_refs":
                           value = policy.get("num_sets") 
                        policy_data[attr] = value if value is not None else 0
                    flat_policies.append(policy_data)

        df = pd.DataFrame(flat_policies)
        total_unique_policies = 0
        if not df.empty:
            df['total_complexity'] = df[policy_attributes].sum(axis=1)
            total_unique_policies = df['policy_name'].nunique()
        
        return df, total_unique_policies

    except Exception as e:
        st.error(f"Error parsing the policy JSON file: {e}")
        return pd.DataFrame(), 0

@st.cache_data
def load_bu_mapping(mapping_file):
    """Loads the VRF to BU mapping from a CSV file."""
    if mapping_file is None:
        return pd.DataFrame()
    try:
        df = pd.read_csv(mapping_file, header=None)
        if len(df.columns) < 2:
            st.error("Mapping CSV must have at least two columns (vrf_name, BU).")
            return pd.DataFrame()
        df = df.iloc[:, [0, 1]] 
        df.columns = ['vrf_name', 'BU']
        df = df.drop_duplicates(subset='vrf_name', keep='first')
        return df
    except Exception as e:
        st.error(f"Error reading the VRF-BU mapping CSV: {e}")
        return pd.DataFrame()

# --- MAIN APP LAYOUT ---
st.title("📊 Route Policy Complexity Dashboard")

# --- FILE UPLOADERS ---
st.sidebar.title("📤 File Upload")
policy_json_file = st.sidebar.file_uploader("Upload Route Policy JSON", type=['json'])
vrf_bu_mapping_file = st.sidebar.file_uploader("Upload VRF-BU Mapping CSV", type=['csv'])

if policy_json_file is None:
    st.info("Please upload a Route Policy JSON file to begin analysis.")
    st.stop()

# --- DATA PROCESSING ---
policy_df, total_policies = load_policy_data(policy_json_file)
bu_mapping_df = load_bu_mapping(vrf_bu_mapping_file)

if policy_df.empty:
    st.warning("The uploaded policy JSON is empty or could not be processed.")
    st.stop()
 
if not bu_mapping_df.empty:
    analysis_df = pd.merge(policy_df, bu_mapping_df, on='vrf_name', how='left')
    analysis_df['BU'] = analysis_df['BU'].fillna('Unassigned')
else:
    analysis_df = policy_df.copy() 
    analysis_df['BU'] = 'Unassigned'
    
analysis_df['BU'] = analysis_df['BU'].astype('category')

# --- PERSISTENT COLOR MAPPING LOGIC ---
color_palette = px.colors.qualitative.Plotly
# Find any new BUs in the current data that are not in our persistent map
new_bus = [bu for bu in analysis_df['BU'].unique() if bu not in st.session_state.bu_color_map]
# Add new BUs to the map with a new color
for bu in new_bus:
    new_color_index = len(st.session_state.bu_color_map) % len(color_palette)
    st.session_state.bu_color_map[bu] = color_palette[new_color_index]
# Ensure 'Unassigned' is always grey
if 'Unassigned' in st.session_state.bu_color_map:
    st.session_state.bu_color_map['Unassigned'] = '#999999'


st.markdown(f"#### Analyzing IPv4 policies from: `{policy_json_file.name}`")
if vrf_bu_mapping_file:
    st.markdown(f"**BU Mapping from:** `{vrf_bu_mapping_file.name}`")
st.markdown("---")

st.metric("Total Unique IPv4 Policies Analyzed", total_policies)

# --- STACKED BAR GRAPH ---
st.header("Route Policy Complexity Breakdown")

all_bus = sorted(analysis_df['BU'].unique())
selected_bu = st.selectbox("Filter by Business Unit (BU)", ["All BUs"] + all_bus)

if selected_bu == "All BUs":
    filtered_df = analysis_df
else:
    filtered_df = analysis_df[analysis_df['BU'] == selected_bu]

policy_order = filtered_df.sort_values(by='total_complexity', ascending=False)['policy_name'].unique()

if not filtered_df.empty:
    attributes_to_plot = ["num_execution_paths", "num_set_refs", "num_filter", "num_modify"]
    plot_df = filtered_df.drop_duplicates(subset=['policy_name']).melt(
        id_vars=['policy_name', 'BU'],
        value_vars=attributes_to_plot,
        var_name='complexity_metric',
        value_name='value'
    )
        
    policy_bu_map = filtered_df.drop_duplicates(subset='policy_name').set_index('policy_name')['BU'].to_dict()
    
    # Use the persistent color map for the x-axis labels
    ticktext = [f"<span style='color:{st.session_state.bu_color_map.get(policy_bu_map.get(p), '#000')}'><b>{p}</b></span>" for p in policy_order]

    new_title = f"Complexity of IPv4 Route-policy (Total: {total_policies})"
    
    fig_bar = px.bar(
        plot_df,
        x='policy_name',
        y='value',
        color='complexity_metric',
        title=new_title,
        labels={'value': 'Metric Count', 'policy_name': 'Route Policy Name', 'complexity_metric': 'Metric'}
    )
    
    # Use the persistent color map to create the embedded legend
    for bu, color in st.session_state.bu_color_map.items():
        # Only show BUs in the legend that are present in the current filtered data
        if bu in filtered_df['BU'].unique():
            fig_bar.add_trace(go.Scatter(
                x=[None], y=[None],
                mode='markers',
                marker=dict(size=10, color=color),
                name=bu,
                legendgroup='BU',
                legendgrouptitle_text='Business Unit (X-Axis Label Color)'
            ))

    fig_bar.update_layout(
        xaxis=dict(
            categoryorder='array', 
            categoryarray=policy_order,
            tickmode='array',
            tickvals=policy_order,
            ticktext=ticktext,
            title_text=f"Filtered by BU: {selected_bu}"
        ),
        barmode='stack',
        title_x=0.5,
        height=800 
    )
    
    st.plotly_chart(fig_bar, use_container_width=True)

else:
    st.info("No policy data available for the selected Business Unit.")

# --- BUSINESS UNIT (BU) ANALYSIS ---
st.header("Business Unit (BU) Analysis")

if 'BU' in analysis_df.columns and analysis_df['BU'].nunique() > 1:
    col1, col2 = st.columns([1, 2])

    with col1:
        st.subheader("IPv4 Policy Attachments per BU")
        bu_counts = analysis_df['BU'].value_counts().reset_index()
        bu_counts.columns = ['BU', 'count']
        fig_pie = px.pie(
            bu_counts, names='BU', values='count',
            title="Share of Policy Attachments by BU", hole=0.3,
            # Use the persistent color map here too for consistency
            color_discrete_map=st.session_state.bu_color_map
        )
        st.plotly_chart(fig_pie, use_container_width=True)

    with col2:
        st.subheader("BU Complexity Statistics (Mean / Median)")
        
        bu_stats = analysis_df.groupby('BU')[attributes_to_plot].agg(['mean', 'median'])
        
        combined_stats = pd.DataFrame(index=bu_stats.index)
        for attr in attributes_to_plot:
            mean_val = bu_stats[(attr, 'mean')].map('{:.1f}'.format)
            median_val = bu_stats[(attr, 'median')].map('{:.1f}'.format)
            
            col_name = f"{attr.replace('_', ' ').title()}"
            combined_stats[col_name] = mean_val + " / " + median_val
            
        combined_stats.reset_index(inplace=True)
        st.dataframe(combined_stats, use_container_width=True, hide_index=True)
else:
    st.info("BU analysis requires a valid VRF-BU mapping file and more than one Business Unit.")

st.markdown("---")
st.subheader("📄 Raw Data View")
st.dataframe(analysis_df.sort_values(by='total_complexity', ascending=False), use_container_width=True)

