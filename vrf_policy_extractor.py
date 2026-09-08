import re
import json
import sys
import argparse
import csv
from typing import Dict, Any, List, Set, Tuple

# --- Part 1: Route-Policy Analysis Logic ---

def parse_route_policies(file_path: str) -> Dict[str, Dict[str, Any]]:
    """
    Parses all route-policy blocks from the configuration file and extracts their complexity metrics.
    """
    policies: Dict[str, Dict[str, Any]] = {}
    current_policy: str | None = None
    
    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            
            match_start = re.match(r'^route-policy\s+([\w\-\.\:]+)(?:\s*\(.*?\))?\s*$', line)
            if match_start:
                current_policy = match_start.group(1)
                if current_policy not in policies:
                    policies[current_policy] = {
                        'lines': 0,
                        'execution_paths': 0,
                        'set_references': set(),
                        'filters': 0,  # New: Counter for pass/drop
                        'modifies': 0  # New: Counter for set statements
                    }
                continue
                
            if current_policy:
                if line == 'end-policy':
                    current_policy = None
                    continue
                
                policies[current_policy]['lines'] += 1
                
                # Count execution paths (if/elseif/else)
                if re.match(r'^(if|elseif|else)\b', line):
                    policies[current_policy]['execution_paths'] += 1
                    
                # Count pass/drop for num_filter
                if re.search(r'\b(pass|drop)\b', line):
                    policies[current_policy]['filters'] += 1
                    
                # Count set statements for num_modify
                if re.search(r'\bset\s+', line):
                    policies[current_policy]['modifies'] += 1
                    
                # Track external set references for num_set_refs
                for match in re.finditer(r'\b(?:in|matches-any|matches-every)\s+(?!community\b|destination\b)([\w\-\.\:]+)', line):
                    policies[current_policy]['set_references'].add(match.group(1))
                
                for match in re.finditer(r'\bdelete\s+(?:community|extcommunity\s+rt)\s+in\s+([\w\-\.\:]+)', line):
                    policies[current_policy]['set_references'].add(match.group(1))

    return policies

def analyze_policy_metrics(policies: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """
    Calculates final metrics for each policy, incorporating filters and modifiers.
    """
    analyzed_policies = {}
    for name, data in policies.items():
        analyzed_policies[name] = {
            "name": name,
            "num_filter": data['filters'],
            "num_modify": data['modifies'],
            "num_execution_paths": data['execution_paths'],
            "num_set_refs": len(data.get('set_references', [])), # Renamed from num_prefix_sets
            "num_lines": data['lines']
        }
    return analyzed_policies

# --- Part 2: VRF Parsing Logic ---

def parse_vrfs(file_path: str) -> Dict[str, Dict[str, Any]]:
    """
    Parses VRF definitions, extracting policies and route-target counts.
    """
    vrfs: Dict[str, Dict[str, Any]] = {}
    current_vrf: str | None = None
    current_afi: str | None = None
    in_rt_block: bool = False
    rt_direction: str | None = None

    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            if vrf_match := re.match(r'^vrf\s+(\S+)', line):
                current_vrf = vrf_match.group(1)
                if current_vrf not in vrfs:
                    vrfs[current_vrf] = {
                        'vrf_name': current_vrf,
                        'imports': {},
                        'exports': {}
                    }
                current_afi, in_rt_block = None, False
                continue

            if line == '!':
                if in_rt_block:
                    in_rt_block, rt_direction = False, None
                elif current_afi:
                    current_afi = None
                elif current_vrf:
                    current_vrf = None
                continue

            if not current_vrf:
                continue

            if afi_match := re.match(r'^address-family\s+(ipv4|ipv6)\s+unicast', line):
                current_afi = afi_match.group(1)
                in_rt_block = False
                continue
                
            if not current_afi:
                continue

            if policy_match := re.match(r'^(import|export)\s+route-policy\s+(\S+)', line):
                direction, policy_name = policy_match.groups()
                vrfs[current_vrf][f'{direction}s'].setdefault(current_afi, {})['route-policy'] = policy_name
                continue

            if rt_start_match := re.match(r'^(import|export)\s+route-target$', line):
                in_rt_block = True
                rt_direction = rt_start_match.group(1)
                continue

            if in_rt_block and rt_direction and re.match(r'^\d+:\d+', line):
                direction_map = {'import': 'imports', 'export': 'exports'}
                if direction_key := direction_map.get(rt_direction):
                    vrfs[current_vrf][direction_key].setdefault(current_afi, {})
                    vrfs[current_vrf][direction_key][current_afi].setdefault('num-route-target', 0)
                    vrfs[current_vrf][direction_key][current_afi]['num-route-target'] += 1
    return vrfs

# --- Part 4: Data Integration and JSON Output ---

def main():
    """
    Main function to orchestrate parsing, analysis, and JSON output generation.
    """
    parser = argparse.ArgumentParser(
        description='Parse IOS-XR configuration to generate a JSON report of VRF policies.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  # Basic run with default output name (vrf_policy_report.json)
  python %(prog)s running-config.txt

  # Run and specify a custom output path
  python %(prog)s running-config.txt -o custom_output.json

  # Run, specify a custom output, AND map Business Units from a CSV
  python %(prog)s running-config.txt -o report.json --bu-map vrf-bu-map.csv
"""
    )
    parser.add_argument('config_file', help='Path to the IOS-XR running configuration file')
    parser.add_argument(
        '-o', '--output', 
        default='vrf_policy_report.json', 
        help='Path to the output JSON file (default: vrf_policy_report.json)'
    )
    args = parser.parse_args()

    try:
        
        print(f"1. Parsing route policies from: {args.config_file}")
        raw_policies = parse_route_policies(args.config_file)
        
        print("2. Analyzing route policy metrics...")
        policy_metrics = analyze_policy_metrics(raw_policies)
        
        print(f"3. Parsing VRF definitions from: {args.config_file}")
        vrfs_data = parse_vrfs(args.config_file)

        print("4. Integrating policy metrics and BU data into VRFs...")
        final_vrf_list = []
        for vrf_name, vrf_info in vrfs_data.items():
            
            for direction in ['imports', 'exports']:
                for afi in ['ipv4', 'ipv6']:
                    if afi_data := vrf_info.get(direction, {}).get(afi):
                        afi_data.setdefault('num-route-target', 0)
                        if policy_name := afi_data.get('route-policy'):
                            # Updated fallback dictionary to match new structure
                            fallback_policy = {
                                "name": policy_name, 
                                "num_filter": 0, 
                                "num_modify": 0, 
                                "num_execution_paths": 0, 
                                "num_set_refs": 0, 
                                "num_lines": 0, 
                                "error": "Policy details not found."
                            }
                            afi_data['route-policy'] = policy_metrics.get(policy_name, fallback_policy)
            final_vrf_list.append(vrf_info)

        print(f"5. Writing report to: {args.output}")
        with open(args.output, 'w', encoding='utf-8') as f:
            json.dump(final_vrf_list, f, indent=2)

        print(f"\n✅ Success! Report generated with {len(final_vrf_list)} VRFs and saved to: {args.output}")

    except FileNotFoundError:
        print(f"❌ Error: The configuration file '{args.config_file}' was not found.", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"❌ An unexpected error occurred: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()

