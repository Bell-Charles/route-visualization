# route-visualization
Scripts used to visualize routing data for Bell Tor-PE

1. Create vrf route object

   - parse_vrf_routes.py :
     python scripts that converts the "show vrf route all" output from IOS-XR router that is stored in a text file into a list of json objects in the format of vrf-route-schema.json.   The 
called with:
     python3 ./parse_vrf_routes.py -c input-file.json -s vrf-route-schema.json
  The input parameters are:
  input-file.json is in the format of input-file-schema.json
  vrf_route_schema.json is in the format 

2. Visualization of route distribution among VRF and BUs.

   - visualize_vrf_routes_with_bu.py
     python script to visualize the distribution of number of routes in each VRF and in the different BUs

   - run_visualize_vrf_route_with_bu
     small bash script to invoke visualize_vrf_routes_with_bu.py with the correct streamlit parameters

3. Visualize shared (duplicate) route between different routes as a heatmap

   - visualize_route_heatmap.py
     python script that visualizes how many routes are shared between 2 routes.  The heatmap shows how many routes are shared between 2 routes
   - run_visualize_route_heatmap
     small bash script to invoke visualize_route_heatmap

4. Create route policy object
  
   - route-policy-extractor.py
     Python script that extracts the route-policy complexity metric for each policy attached to a vrf (v4 & v6, import & export) and put the data into a json object list

5.  Visualize the route policy complexity
   
   - visualized_vrf_route_policy_bu.py
     python script to visualize the route policy complexity using a tiered bar graph that stack the various metric of a policy-map
   - run_visualized_vrf_route_policy_bu
     small bash script to invoke visualized_vrf_route_policy_bu
