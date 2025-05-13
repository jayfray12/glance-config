import os
import requests
from flask import Flask, jsonify
import datetime
import pytz
import sys
from dateutil import parser

app = Flask(__name__)

# Your saved credentials
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
REFRESH_TOKEN = os.getenv("REFRESH_TOKEN")
REDIRECT_URI = "http://localhost:8075"
DISPLAY_HEIGHT_PX = 345
WORK_START_MINUTES = 7 * 60
WORK_END_MINUTES = 19 * 60
WORK_DURATION_MINUTES = WORK_END_MINUTES - WORK_START_MINUTES
PIXELS_PER_MINUTE = DISPLAY_HEIGHT_PX / WORK_DURATION_MINUTES
MIN_HEIGHT_PX = 24
COLUMN_GAP_PX = 4
TOTAL_WIDTH_PX = 100

def parse_time(dt_str):
    return parser.isoparse(dt_str)

def minutes_since_start(dt):
    return dt.hour * 60 + dt.minute - (START_HOUR * 60)

# Get access token from refresh token
def get_access_token():
    token_url = "https://oauth2.googleapis.com/token"
    data = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": REFRESH_TOKEN,
        "grant_type": "refresh_token",
    }
   
    response = requests.post(token_url, data=data)
    if response.status_code == 200:
        return response.json()['access_token']
    else:
        print("error getting access token: ", response.status_code, file=sys.stderr)
        return None
    
def get_em_quad_hint(input_string, width):
  string_length = len(input_string)
  if width < 31:
    if string_length <= 21:
        return 1
    elif 22 <= string_length < 25:
        return 0.9167
    elif 25 <= string_length < 29:
        return 0.8333
    elif 29 <= string_length < 34:
        return 0.75
    else:
        return 0.6667
      
  
  if string_length < 41:
    return 1
  elif 41 <= string_length < 46:
    return 0.9167
  elif 46 <= string_length < 51:
    return 0.8333
  else:
    return 0.75

# Process events
def process_events(events):
    parsed_events = []

    for event in events:
        attendees = event.get('attendees', [])
        accepted_event = False
        responseStatus = ""
        for attendee in attendees:
                if attendee.get('self') :
                    responseStatus = attendee.get('responseStatus')
                    accepted_event = (responseStatus == 'accepted' or 
                                      responseStatus == 'needsAction' or 
                                      bool(attendee.get('organizer')))
                    break  # No need to check other attendees for this event

        if accepted_event is False :
            continue;
        
        try:
            start = parser.isoparse(event["start"].get("dateTime") or event["start"].get("date"))
            end = parser.isoparse(event["end"].get("dateTime") or event["end"].get("date"))
        except Exception as e:
            continue

        start_min = (start.hour * 60 + start.minute)
        end_min = (end.hour * 60 + end.minute)

        # Clamp to workday
        start_min = max(start_min, WORK_START_MINUTES)
        end_min = min(end_min, WORK_END_MINUTES)
        if end_min <= start_min:
            continue  # skip invalid range

        top = round((start_min - WORK_START_MINUTES) * PIXELS_PER_MINUTE)
        height = max(round((end_min - start_min) * PIXELS_PER_MINUTE), MIN_HEIGHT_PX)

        parsed_events.append({
            "start": event["start"].get("dateTime"),
            "end": event["end"].get("dateTime"),
            "start_min": start_min,
            "end_min": end_min,
            "top": top,
            "height": height,
            "summary": event.get("summary", ""),
            "em_hint": 1,
            "responseStatus": responseStatus,
            "raw": event
        })

    # Step 2: Group overlapping events into "collision groups"
    collision_groups = []
    for event in parsed_events:
        placed = False
        for group in collision_groups:
            if any(not (event["end_min"] <= e["start_min"] or event["start_min"] >= e["end_min"]) for e in group):
                group.append(event)
                placed = True
                break
        if not placed:
            collision_groups.append([event])

    # Step 3: Assign columns within each group
    for group in collision_groups:
        columns = []

        for event in sorted(group, key=lambda e: e["start_min"]):
            placed = False
            for i, col in enumerate(columns):
                if col[-1]["end_min"] <= event["start_min"]:
                    col.append(event)
                    event["column"] = i
                    placed = True
                    break
            if not placed:
                columns.append([event])
                event["column"] = len(columns) - 1

        total_cols = len(columns)
        gap_total = (total_cols - 1) * COLUMN_GAP_PX
        column_width = (TOTAL_WIDTH_PX - gap_total) / total_cols

        for event in group:
            event["left"] = event["column"] * (column_width + COLUMN_GAP_PX)
            event["width"] = column_width
            if column_width < 100:
                event["em_hint"] = get_em_quad_hint(event["summary"], column_width)

    return parsed_events

# Fetch Google Calendar events
def get_calendar_events():
    access_token = get_access_token()
    if not access_token:
        print("no access token returning empty", file=sys.stderr)
        return []

    calendar_url = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
    headers = {
        "Authorization": f"Bearer {access_token}",
    }

    # Always start the day at 7am
    current_time = datetime.datetime.now(pytz.utc).replace(hour=7, minute=0, second=0, microsecond=0)
    time_min = current_time.isoformat()
    time_max = (current_time + datetime.timedelta(days=1)).isoformat()

    params = {
        "timeMin": time_min,
        "timeMax": time_max,
        "singleEvents": True,
        "orderBy": "startTime",
    }

    response = requests.get(calendar_url, headers=headers, params=params)
    if response.status_code == 200:
        return process_events(response.json().get('items', []))
    else:
        print("error getting response from google: ", reponse.status_code, file=sys.stderr)
        return []

@app.route('/data')
def calendar_data():
    events = get_calendar_events()

    # Compute current now line for calendar
    now = datetime.datetime.now(pytz.timezone('America/New_York'))
    now_minutes = now.hour * 60 + now.minute
    now_since_start = now_minutes - WORK_START_MINUTES
    now_top = 0
    if 0 <= now_since_start <= WORK_DURATION_MINUTES:
        now_top = round(now_since_start * PIXELS_PER_MINUTE)

    return jsonify({"events": events, "now_line": now_top})

if __name__ == "__main__":
    app.run(debug=True, host='0.0.0.0', port=8075)
