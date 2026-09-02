import argparse
import json
import re
import logging
from datetime import datetime, timezone

# Try to import jsonschema
try:
    from jsonschema import validate, ValidationError
    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False

# --- Protocol Mapping ---
PROTOCOL_MAP = {
    'C': 'Connected', 'S': 'Static', 'B': 'BGP', 'O': 'OSPF',
    'i': 'IS-IS', 'D': 'EIGRP', 'R': 'RIP', 'L': 'Local', 'a': 'Application'
}

# --- Regex Patterns ---
TIME_PATTERN = re.compile(r'^([A-Z][a-z]{2}\s+[A-Z][a-z]{2}\s+\d+\s+\d{2}:\d{2}:\d{2}\.\d+)\s+([A-Z]+)')
VRF_PATTERN = re.compile(r'^VRF:\s+(\S+)')
GW_PATTERN = re.compile(r'^Gateway of last resort is ([\d\.]+) to network ([\d\.]+)')

# Matches primary route
ROUTE_PATTERN = re.compile(
    r'^([a-zA-Z\*0-2\s]+?)\s+'              # Protocol (e.g., 'B', 'S*')
    r'(\d{1,3}(?:\.\d{1,3}){3})/(\d{1,2})\s+' # Prefix and Length (e.g., '4.172.40.224', '28')
    r'\[(\d+)/(\d+)\]\s+'                   # [AD/Metric]
    r'via\s+(\d{1,3}(?:\.\d{1,3}){3})'      # via Nexthop
    r'(.*)'                                 # Details (e.g., vrf leak info)
)

# Matches ECMP / multi-path nexthops
NEXTHOP_PATTERN = re.compile(
    r'^\s+\[(\d+)/(\d+)\]\s+'               # [AD/Metric]
    r'via\s+(\d{1,3}(?:\.\d{1,3}){3})'      # via Nexthop
    r'(.*)'                                 # Details
)

def get_protocol_name(raw_proto):
    """Translates CLI protocol code to Schema enum."""
    clean_proto = raw_proto.replace('*', '').strip()
    parts = clean_proto.split()
    
    if not parts:
        return "Static" 
        
    base_code = parts[0]
    return PROTOCOL_MAP.get(base_code, "BGP")

def parse_iosxr_routes(file_path):
    """Parses the text output into an array of strict schema VRF objects."""
    vrf_tables = []
    
    # Global state
    global_collection_time = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    
    # Current VRF state
    current_vrf_name = "default"
    current_gateway = None
    current_routes = []
    current_route = None
    vrf_started = False # Tracks if we have data to save

    def parse_path(ad, metric, nexthop, details, active_vrf):
        nh_vrf = active_vrf
        vrf_match = re.search(r'nexthop in vrf (\S+)\)', details)
        if vrf_match:
            nh_vrf = vrf_match.group(1)
        return {
            "next_hop_ip": nexthop,
            "egress_interface": None, 
            "next_hop_vrf": nh_vrf,
            "administrative_distance": int(ad),
            "metric": int(metric)
        }

    try:
        with open(file_path, 'r') as f:
            for line in f:
                stripped = line.strip()
                if not stripped: continue

                # 1. Capture Time (Usually at the very top of the output)
                t_match = TIME_PATTERN.match(stripped)
                if t_match:
                    try:
                        time_str = t_match.group(1).split(' ', 1)[1] 
                        dt = datetime.strptime(f"{datetime.now().year} {time_str}", "%Y %b %d %H:%M:%S.%f")
                        dt = dt.replace(tzinfo=timezone.utc)
                        global_collection_time = dt.isoformat().replace("+00:00", "Z")
                    except Exception:
                        pass
                    continue

                # 2. Capture VRF - indicates the start of a NEW routing table block
                v_match = VRF_PATTERN.match(stripped)
                if v_match:
                    # Save the previous VRF block if it contains data
                    if vrf_started:
                        if current_route:
                            current_routes.append(current_route)
                            current_route = None
                            
                        payload = {
                            "collection_timestamp": global_collection_time,
                            "vrf_name": current_vrf_name,
                            "address_family": "ipv4",
                            "routes": current_routes
                        }
                        if current_gateway:
                            payload["gateway_of_last_resort"] = current_gateway
                        vrf_tables.append(payload)

                    # Reset tracking variables for the new VRF
                    current_vrf_name = v_match.group(1)
                    current_gateway = None
                    current_routes = []
                    current_route = None
                    vrf_started = True
                    continue

                # 3. Capture Gateway
                g_match = GW_PATTERN.match(stripped)
                if g_match:
                    current_gateway = {"ip": g_match.group(1), "network": g_match.group(2)}
                    vrf_started = True
                    continue

                # 4. Capture Primary Route
                route_match = ROUTE_PATTERN.match(line)
                if route_match:
                    vrf_started = True
                    if current_route: 
                        current_routes.append(current_route)
                    
                    raw_proto = route_match.group(1).strip()
                    current_route = {
                        "prefix": route_match.group(2),
                        "prefix_len": int(route_match.group(3)),
                        "protocol_name": get_protocol_name(raw_proto),
                        "is_candidate_default": '*' in raw_proto,
                        "paths": [parse_path(route_match.group(4), route_match.group(5), route_match.group(6), route_match.group(7), current_vrf_name)]
                    }
                    continue

                # 5. Capture Multi-path Nexthops
                nh_match = NEXTHOP_PATTERN.match(line)
                if nh_match and current_route:
                    current_route["paths"].append(
                        parse_path(nh_match.group(1), nh_match.group(2), nh_match.group(3), nh_match.group(4), current_vrf_name)
                    )

        # 6. End of file: save the very last VRF block
        if vrf_started:
            if current_route:
                current_routes.append(current_route)
            payload = {
                "collection_timestamp": global_collection_time,
                "vrf_name": current_vrf_name,
                "address_family": "ipv4",
                "routes": current_routes
            }
            if current_gateway:
                payload["gateway_of_last_resort"] = current_gateway
            vrf_tables.append(payload)

    except FileNotFoundError:
        logging.error(f"Input file not found: {file_path}")

    return vrf_tables

def process_files(config_file, schema_file):
    with open(schema_file, 'r') as sf:
        schema = json.load(sf)
        
    with open(config_file, 'r') as cf:
        routers = json.load(cf)

    if isinstance(routers, dict):
        routers = [routers]
    elif not isinstance(routers, list):
        logging.error("Configuration file must contain either a JSON object or a list of JSON objects.")
        return

    for router in routers:
        in_file = router.get("input-file-name")
        out_file = router.get("output-file-name")
        if not in_file or not out_file: 
            continue

        logging.info(f"Processing input file: {in_file}")
        
        # Get the LIST of schema-compliant payloads (one per VRF)
        vrf_payload_list = parse_iosxr_routes(in_file)

        # Validate EACH VRF independently against the schema
        if HAS_JSONSCHEMA:
            for vrf_payload in vrf_payload_list:
                try:
                    validate(instance=vrf_payload, schema=schema)
                    logging.debug(f"Schema validation PASSED for VRF '{vrf_payload['vrf_name']}'")
                except ValidationError as e:
                    logging.error(f"Schema validation FAILED for VRF '{vrf_payload['vrf_name']}': {e.message}")
        else:
            logging.warning("jsonschema library not installed. Skipping schema validation step.")

        # Write out the complete array
        with open(out_file, 'w') as of:
            json.dump(vrf_payload_list, of, indent=4)
        logging.info(f"Successfully wrote {len(vrf_payload_list)} VRF tables to {out_file}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Parse IOS-XR routes to Strict JSON Array")
    parser.add_argument("-c", "--config", required=True, help="Input configuration JSON file")
    parser.add_argument("-s", "--schema", required=True, help="JSON Schema file")
    parser.add_argument("-d", "--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO, format='%(levelname)s: %(message)s')
    process_files(args.config, args.schema)

