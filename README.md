# route-visualization
Scripts used to visualize routing data for Bell Tor-PE

parse_vrf_routes.py :

Converts the "show vrf route all" output from IOS-XR router that is stored in a text file into a list of json objects in the format of vrf-route-schema.json.   The 
called with:
python3 ./parse_vrf_routes.py -c input-file.json -s vrf-route-schema.json

input-file.json is in the format of input-file-schema.json
vrf_route_schema.json is in the format 
