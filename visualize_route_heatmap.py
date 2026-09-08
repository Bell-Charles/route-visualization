import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import json
from ipaddress import ip_network
import radix
import io
from itertools import combinations
from collections import Counter
import numpy as np

# --- Page Configuration ---
st.set_page_config(layout="wide", page_title="IOS-XR Route Duplication Analyzer")

# --- Data Loading and Processing Functions ---

@st.cache_data
def load_route_data(uploaded_file):
    """Loads and processes the route data from the uploaded JSON file."""
    if uploaded_file is None:
        return pd.DataFrame()
    try:
        uploaded_file.seek(0)
        json_data = json.load(uploaded_file)
        
        if not isinstance(json_data, list):
            json_data = [json_data]

        records = []
        for vrf_data in json_data:
            vrf_name = vrf_data.get("vrf_name")
            if vrf_data.get("address_family") != "ipv4":
                continue
            
            for route in vrf_data.get("routes", []):
                if route.get("prefix") == "0.0.0.0" and route.get("prefix_len") == 0:
                    continue
                records.append({
                    "vrf_name": vrf_name,
                    "prefix": f"{route.get('prefix')}/{route.get('prefix_len')}"
                })
        
        df = pd.DataFrame(records)
        return df.drop_duplicates()

    except Exception as e:
        st.error(f"Error parsing the route JSON file: {e}")
        return pd.DataFrame()

@st.cache_data
def load_bu_mapping(mapping_file):
    """Loads the VRF to BU mapping from a CSV file."""
    if mapping_file is None:
        return pd.DataFrame(columns=['vrf_name', 'BU'])
    try:
        df = pd.read_csv(mapping_file, header=None)
        if len(df.columns) < 2:
            st.error("Mapping CSV must have at least two columns (vrf_name, BU).")
            return pd.DataFrame()
        df = df.iloc[:, [0, 1]] 
        df.columns = ['vrf_name', 'BU']
        return df.drop_duplicates(subset='vrf_name', keep='first')
    except Exception as e:
        st.error(f"Error reading the VRF-BU mapping CSV: {e}")
        return pd.DataFrame()

@st.cache_data
def analyze_subnet_overlaps_fast(_df):
    """Analyzes subnet overlaps using a highly efficient Radix tree."""
    if _df.empty:
        return pd.DataFrame()
    try:
        rtree = radix.Radix()
        prefix_groups = _df.groupby('prefix')
        
        for prefix, group in prefix_groups:
            node = rtree.add(prefix)
            node.data['vrfs'] = group['vrf_name'].tolist()

        overlap_records = []
        for supernet_node in rtree.nodes():
            supernet_prefix = supernet_node.prefix
            supernet_vrfs = supernet_node.data['vrfs']
            subnets = rtree.search_covered(supernet_prefix)
            
            for subnet_node in subnets:
                if subnet_node.prefix == supernet_prefix:
                    continue
                subnet_prefix = subnet_node.prefix
                subnet_vrfs = subnet_node.data['vrfs']
                for sup_vrf in supernet_vrfs:
                    for sub_vrf in subnet_vrfs:
                        overlap_records.append({
                            'Supernet': supernet_prefix, 'Supernet_VRF': sup_vrf,
                            'Subnet': subnet_prefix, 'Subnet_VRF': sub_vrf,
                        })
        return pd.DataFrame(overlap_records)
    except Exception as e:
        st.error(f"An error occurred during Radix tree overlap analysis: {e}")
        return pd.DataFrame()

@st.cache_data
def calculate_vrf_duplication_stats(_df):
    """
    Calculates how many shared prefixes each VRF contains.
    Returns the statistics dataframe and the shared routes dataframe.
    """
    if _df.empty or 'prefix' not in _df.columns:
        return pd.DataFrame(), pd.DataFrame()
    
    prefix_counts = _df['prefix'].value_counts()
    shared_prefixes = prefix_counts[prefix_counts > 1].index
    
    if shared_prefixes.empty:
        return pd.DataFrame(), pd.DataFrame()

    shared_routes_df = _df[_df['prefix'].isin(shared_prefixes)]
    vrf_dup_counts = shared_routes_df.groupby('vrf_name')['prefix'].nunique().reset_index()
    vrf_dup_counts.columns = ['vrf_name', 'num_shared_prefixes']
    
    return vrf_dup_counts, shared_routes_df

# --- Main App Layout ---
st.title("🗺️ IOS-XR Route Duplication & Overlap Analyzer")
st.markdown("Analyze prefix distribution and duplication patterns across multiple VRFs.")

# --- File Uploaders in Sidebar ---
with st.sidebar:
    st.title("📤 File Upload")
    route_json_file = st.file_uploader("Upload Routes JSON", type=['json'], help="Upload a file conforming to the vrf-route-schema.json")
    vrf_bu_mapping_file = st.file_uploader("Upload VRF-BU Mapping (Optional)", type=['csv'], help="Two-column CSV: vrf_name,BU")

if route_json_file is None:
    st.info("⬅️ Please upload a Route JSON file to begin analysis.")
    st.stop()

# --- Data Processing ---
routes_df = load_route_data(route_json_file)
bu_mapping_df = load_bu_mapping(vrf_bu_mapping_file)

if routes_df.empty:
    st.warning("The uploaded route JSON is empty, could not be processed, or contains no valid IPv4 routes.")
    st.stop()

analysis_df = pd.merge(routes_df, bu_mapping_df, on='vrf_name', how='left')
analysis_df['BU'] = analysis_df['BU'].fillna('Unassigned')

# --- Main View ---
st.markdown(f"#### Analyzing IPv4 routes from: `{route_json_file.name}`")
if vrf_bu_mapping_file:
    st.markdown(f"**BU Mapping from:** `{vrf_bu_mapping_file.name}`")
st.markdown("---")

total_vrfs = analysis_df['vrf_name'].nunique()
total_prefixes = analysis_df['prefix'].nunique()
st.metric("Total Unique Prefixes Analyzed", f"{total_prefixes} across {total_vrfs} VRFs")

# --- Calculations for all charts and reports ---
with st.spinner("Calculating statistics..."):
    vrf_stats_df, shared_routes_df = calculate_vrf_duplication_stats(analysis_df)
    overlap_df = analyze_subnet_overlaps_fast(analysis_df)

# --- Debug File Generation ---
st.sidebar.title("⚙️ Debugging")
if st.sidebar.button("Prepare Debug File"):
    st.sidebar.info("Debug file is ready for download below.")
    debug_buffer = io.StringIO()
    debug_buffer.write("### --- DEBUG FILE --- ###\n\n")
    debug_buffer.write("="*30 + "\n1. GLOBAL SHARED PREFIXES & THEIR VRFs\n" + "="*30 + "\n")
    if not shared_routes_df.empty:
        vrf_per_prefix = shared_routes_df.groupby('prefix')['vrf_name'].apply(lambda x: sorted(list(x)))
        debug_buffer.write(f"Total prefixes found in 2 or more VRFs: {len(vrf_per_prefix)}\n\n")
        for prefix, vrf_list in vrf_per_prefix.items():
            debug_buffer.write(f"- Prefix: {prefix}\n  Shared across ({len(vrf_list)} VRFs): {', '.join(vrf_list)}\n\n")
    else:
        debug_buffer.write("No shared prefixes found.\n\n")
    debug_buffer.write("="*30 + "\n2. PER-VRF SHARED COUNT (Intermediate Data)\n" + "="*30 + "\n")
    vrf_stats_df.to_csv(debug_buffer, index=False)
    debug_buffer.write("\n\n")
    debug_buffer.write("="*30 + "\n3. SUBNET OVERLAP REPORT DATA\n" + "="*30 + "\n")
    if not overlap_df.empty:
        overlap_df.to_csv(debug_buffer, index=False)
    else:
        debug_buffer.write("No subnet overlaps found.\n")
    st.sidebar.download_button(label="Download Debug File", data=debug_buffer.getvalue(), file_name="debug_output.txt", mime="text/plain")

if not vrf_stats_df.empty:
    bu_map_provided = not bu_mapping_df.empty
    if bu_map_provided:
        vrf_stats_with_bu_df = pd.merge(vrf_stats_df, bu_mapping_df, on='vrf_name', how='left').fillna({'BU': 'Unassigned'})
    else:
        vrf_stats_with_bu_df = vrf_stats_df.copy()
        vrf_stats_with_bu_df['BU'] = 'Unassigned'
    vrf_stats_sorted_df = vrf_stats_with_bu_df.sort_values('num_shared_prefixes', ascending=False)

    st.header("Shared Prefix Count per VRF")
    fig_vrf_breakdown = px.bar(vrf_stats_sorted_df, x='vrf_name', y='num_shared_prefixes', color='BU', title="Count of Shared Prefixes in Each VRF", labels={"vrf_name": "VRF Name", "num_shared_prefixes": "Number of Shared Prefixes Held", "BU": "Business Unit"}, text='num_shared_prefixes')
    fig_vrf_breakdown.update_traces(textposition='outside').update_layout(xaxis_tickangle=-90, height=600)
    st.plotly_chart(fig_vrf_breakdown, use_container_width=True)

    st.header("Shared Prefix Heatmap: VRF-to-VRF")
    st.markdown("This heatmap shows the number of exact-match shared prefixes between each pair of VRFs.")
    
    with st.spinner("Generating VRF-to-VRF heatmap..."):
        prefix_groups = shared_routes_df.groupby('prefix')['vrf_name'].apply(list)
        all_pairs = [comb for vrf_list in prefix_groups for comb in combinations(sorted(vrf_list), 2)]
        pair_counts = Counter(all_pairs)
        
        if pair_counts:
            vrf_pairs_df = pd.DataFrame(pair_counts.items(), columns=['vrf_pair', 'count'])
            vrf_pairs_df[['vrf1', 'vrf2']] = pd.DataFrame(vrf_pairs_df['vrf_pair'].tolist(), index=vrf_pairs_df.index)
            all_involved_vrfs = sorted(pd.concat([vrf_pairs_df['vrf1'], vrf_pairs_df['vrf2']]).unique())
            pivot_df = vrf_pairs_df.pivot_table(index='vrf1', columns='vrf2', values='count').fillna(0)
            pivot_df_transposed = vrf_pairs_df.pivot_table(index='vrf2', columns='vrf1', values='count').fillna(0)
            symmetric_matrix = pivot_df.add(pivot_df_transposed, fill_value=0).reindex(index=all_involved_vrfs, columns=all_involved_vrfs, fill_value=0)
            
            # Replace 0 with NaN for cleaner visualization
            symmetric_matrix.replace(0, np.nan, inplace=True)

            fig_shared_heatmap = px.imshow(symmetric_matrix, labels=dict(x="VRF", y="VRF", color="Shared Prefix Count"), text_auto=True, color_continuous_scale="Blues", aspect="auto")
            fig_shared_heatmap.update_layout(title="Count of Shared Prefixes Between VRF Pairs", title_x=0.5, xaxis_tickangle=-45)
            st.plotly_chart(fig_shared_heatmap, use_container_width=True)
        else:
            st.info("Not enough shared prefixes to generate a VRF-to-VRF heatmap.")

    st.header("Shared Prefix Distribution Histogram")
    st.markdown("This histogram groups all VRFs by their shared prefix counts, showing the overall distribution regardless of Business Unit.")
    distribution_df = vrf_stats_df['num_shared_prefixes'].value_counts().reset_index(name='num_vrfs')
    fig_dist = px.bar(distribution_df.sort_values('num_shared_prefixes'), x='num_shared_prefixes', y='num_vrfs', title="Distribution of Shared Prefixes Across All VRFs", labels={"num_shared_prefixes": "Number of Shared Prefixes Held by a Single VRF", "num_vrfs": "Count of VRFs in Category"}, text='num_vrfs')
    fig_dist.update_traces(hovertemplate='<b>%{y} VRFs</b> each hold<br>exactly <b>%{x} shared prefixes</b>.<extra></extra>')
    fig_dist.update_layout(xaxis_type='category').update_traces(textposition='outside')
    st.plotly_chart(fig_dist, use_container_width=True)

else:
    st.info("No shared (duplicated) prefixes were found across the provided VRFs.")

st.header("🚨 Subnet Overlap Analysis")
if not overlap_df.empty:
    st.subheader("Overlap Heatmap: Supernet VRF vs. Subnet VRF")
    overlap_counts = overlap_df.groupby(['Supernet_VRF', 'Subnet_VRF']).size().reset_index(name='count')
    heatmap_pivot = overlap_counts.pivot_table(index='Supernet_VRF', columns='Subnet_VRF', values='count', fill_value=0)
    
    # Replace 0 with NaN for cleaner visualization
    heatmap_pivot.replace(0, np.nan, inplace=True)
    
    fig_overlap_heatmap = px.imshow(heatmap_pivot, labels=dict(x="Subnet VRF (Contains Specific Route)", y="Supernet VRF (Contains Broad Route)", color="Overlap Count"), text_auto=True, color_continuous_scale="Reds", aspect="auto")
    fig_overlap_heatmap.update_layout(title="Count of Subnet Overlaps Between VRFs", title_x=0.5, xaxis_tickangle=-45)
    st.plotly_chart(fig_overlap_heatmap, use_container_width=True)
    
    st.subheader("Detailed Subnet Overlap Report")
    st.dataframe(overlap_df.sort_values(by=['Supernet', 'Subnet']).reset_index(drop=True), use_container_width=True)
else:
    st.success("No subnet overlaps were found among the analyzed prefixes.")

st.subheader("📄 Raw Data View")
st.dataframe(analysis_df.sort_values(by=['prefix', 'vrf_name']), use_container_width=True)

