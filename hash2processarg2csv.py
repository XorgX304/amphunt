# This script is based on https://github.com/CiscoSecurity/amp-04-check-sha256-execution
import sys
import requests
import time
import configparser
import csv

# Ignore insecure cert warnings (enable only if working with onsite-amp deployments without valid certificate)
from requests.packages.urllib3.exceptions import InsecureRequestWarning
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)


# Store objects:
computer_guids = {}

def format_arguments(_arguments):
    """ If arguments are in a list join them as a single string"""
    if isinstance(_arguments, list):
        return ' '.join(_arguments)
    return _arguments

def extractGUID(data):
    """ Extract GUIDs from data structure and store them in computer_guids variable"""
    for entry in data:
        connector_guid = entry['connector_guid']
        hostname = entry['hostname']
        computer_guids.setdefault(connector_guid, {'hostname':hostname})

def checkAPITimeout(headers, request):
    """Ensure we don't cross API limits, sleep if we are approaching close to limits"""
    if str(request.status_code) == '200':
        # Extract headers (these are also returned)
        headers=request.headers
        # check if we correctly got headers
        if headers:
            # We stop on 45 due to number of threads working
            if 'X-RateLimit-Remaining' and 'X-RateLimit-Reset' in str(headers):
                if(int(headers['X-RateLimit-Remaining']) < 45):
                    if(headers['Status'] == "200 OK"):
                        # We are close to the border, in theory 429 error code should never trigger if we capture this event
                        # For some reason simply using time.sleep does not work very well here
                        time.sleep((int(headers['X-RateLimit-Reset'])+5))
                    if(headers['Status'] == "429 Too Many Requests"):
                        # Triggered too many request, we need to sleep before it continues
                        # For some reason simply using time.sleep does not work very well here
                        time.sleep((int(headers['X-RateLimit-Reset'])+5))
            elif '503 Service Unavailable' in str(headers):
                time.sleep(60)
            else: # we got some new error
                time.sleep(45)
        else:
            # no headers, request probably failed
            time.sleep(45)
    elif str(request.status_code) == '404':
        # 404 - this could mean event timeline or event does no longer exists
        time.sleep(45)
        pass
    elif str(request.status_code) == '503':
        # server sarted to block us
        time.sleep(90)
        pass
    else:
        # in any other case, sleep
        time.sleep(90)
        pass


# Validate a command line parameter was provided
if len(sys.argv) < 2:
    sys.exit('Usage:\n %s <config.txt> <hashfile.txt>' % sys.argv[0])

#Parse config
config = configparser.ConfigParser()
config.read(sys.argv[1])
client_id = config['settings']['client_id']
api_key = config['settings']['api_key']
domainIP = config['settings']['domainIP']

# Store the command line parameter
sha256hashfile = sys.argv[2]



#Print header for CSV 
print('date,guid,hostname,sha256,parent_sha256,file_name,file_path,arguments')
try:
    fp = open(sha256hashfile,'r')
    for sha256hash in fp.readlines():
        # Creat session object
        # http://docs.python-requests.org/en/master/user/advanced/
        # Using a session object gains efficiency when making multiple requests
        session = requests.Session()
        session.auth = (client_id, api_key)

        # Define URL and parameters
        activity_url = 'https://{}/v1/computers/activity'.format(domainIP)
        payload = {'q': sha256hash.strip()}

        # Query API
        response = session.get(activity_url, params=payload, verify=False)
        # Get Headers
        headers=response.headers

        # Ensure we don't cross API limits, sleep if we are approaching close to limits
        checkAPITimeout(headers, response)
                
        # Decode first JSON response to determine if we got more pages to search
        response_event_json = response.json()

        # Call extract GUID function to get all matched GUIDs
        extractGUID(response_event_json['data'])

        # Handle paginated pages and extract computer GUIDs
        if('next' in response_event_json['metadata']['links']):
            while 'next' in response_event_json['metadata']['links']:
                next_url = response_event_json['metadata']['links']['next']
                response = session.get(next_url)
                headers=response.headers
                # Ensure we don't cross API limits, sleep if we are approaching close to limits
                checkAPITimeout(headers, response)
                # Extract
                response_event_json = response.json()
                extractGUID(response_event_json['data'])

        # Finally, for each GUID on the list we match the args with trajectory (trajectory is limited to last 500 events however)
        for guid in computer_guids:
            try:
                trajectory_url = 'https://{}/v1/computers/{}/trajectory'.format(domainIP,guid)
                trajectory_response = session.get(trajectory_url, params=payload, verify=False)
                headers=trajectory_response.headers
                # Handle potential API limits
                checkAPITimeout(headers, trajectory_response)
                trajectory_response_json = trajectory_response.json()
                # only focus on actual events, ignore DFC and other type of telemetry (hence pass for exception)
                events = trajectory_response_json['data']['events']
                # Parse trajectory events to find the network events
                for event in events:
                    timestamp=event['date']
                    event_type = event['event_type']
                    if 'command_line' in str(event) and 'arguments' in str(event['command_line']) and 'Executed' in str(event_type):
                        arguments = event['command_line']['arguments']
                        file_sha256 = event['file']['identity']['sha256']
                        parent_sha256 = event['file']['parent']['identity']['sha256']
                        file_name = event['file']['file_name']
                        file_path=event['file']['file_path']
                        #Print out line formatted for CSV0
                        print("{},{},{},{},{},{},{},{}".format(
                            timestamp,
                            guid,
                            computer_guids[guid]['hostname'],
                            file_sha256,
                            parent_sha256,
                            file_name,
                            file_path,
                            format_arguments(arguments)))
                    # this is only sensible to hunt for any binary not the arguments
                    # if 'file_name' in str(event) and 'command_line' not in str(event):
                    #     file_sha256 = event['file']['identity']['sha256']
                    #     parent_sha256 = event['file']['parent']['identity']['sha256']
                    #     file_name = event['file']['file_name']
                    #     file_path=event['file']['file_path']
                    #     #Print out line formatted for CSV
                    #     print("{},{},{},{},{},{},{},{}".format(
                    #         timestamp,
                    #         guid,
                    #         computer_guids[guid]['hostname'],
                    #         file_sha256,
                    #         parent_sha256,
                    #         file_name,
                    #         file_path,
                    #         "-"))
                        # the final line won't have command line so final argument is always "-"
            except:
                 pass

finally:
    fp.close()