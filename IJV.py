import streamlit as st
import json
import requests
import math
import re
import io
import os
import base64
from datetime import datetime, timedelta, timezone
from jinja2 import Environment, BaseLoader
from weasyprint import HTML

# ---------------------------------------------------------
# INITIAL PAGE CONFIGURATION (Must be called first)
# ---------------------------------------------------------
st.set_page_config(page_title="IJV Crew Portal", page_icon="✈️", layout="wide", initial_sidebar_state="collapsed")

# ---------------------------------------------------------
# HELPER CLASSES & OPERATIONS
# ---------------------------------------------------------
class DotDict(dict):
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__
    __delattr__ = dict.__delitem__

def dict_to_obj(d):
    if isinstance(d, list): return [dict_to_obj(i) for i in d]
    if isinstance(d, dict): return DotDict({k: dict_to_obj(v) for k, v in d.items()})
    return d

def php_date(fmt, timestamp):
    try:
        ts = int(timestamp)
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        if fmt == 'd': return dt.strftime('%d')
        if fmt == 'm': return dt.strftime('%m')
        if fmt == 'y': return dt.strftime('%y')
        if fmt == 'Hi': return dt.strftime('%H%M')
        if fmt == 'H:i': return dt.strftime('%H:%M')
        if fmt == 'H.i': return dt.strftime('%H.%M')
        if fmt == 'dMy': return dt.strftime('%d%b%y').upper()
        if fmt == 'Y-m-d': return dt.strftime('%Y-%m-%d')
        if fmt == 'd-m-y': return dt.strftime('%d-%m-%y')
        return dt.strftime('%Y-%m-%d')
    except: return ""

def get_filtered_notams(notam_raw, limit=4):
    notam_list = []
    if isinstance(notam_raw, list): notam_list = notam_raw
    elif isinstance(notam_raw, dict): notam_list = [notam_raw]
    if not notam_list: return []

    urgent_keywords = ['CLSD', 'CLOSED', 'U/S', 'UNSERVICEABLE', 'DANGER', 'RESTRICTED', 'RWY', 'RUNWAY', 'ILS', 'GNSS', 'GPS']
    scored = []
    seen = set()

    for n in notam_list:
        nid = n.get('notam_id', '')
        if nid in seen: continue
        txt = n.get('notam_text', n.get('notam_raw', ''))
        score = 0
        for kw in urgent_keywords:
            if kw in txt.upper(): score += 1
        if score == 0: score = 0.1
        seen.add(nid)
        scored.append({'id': nid, 'text': txt, 'score': score})

    scored.sort(key=lambda x: x['score'], reverse=True)
    return scored[:limit]

class PythonHelper:
    def formatCruiseProfile(self, profile, cost_index):
        if not profile: return ""
        p = profile.upper().replace(" ", "")
        if p.startswith('M') or p == 'LRC': return profile
        if p.startswith('CI'):
            match = re.search(r'(\d+)', p)
            if match: return f"CI{int(match.group(1)):03d}"
            else:
                try: return f"CI{int(cost_index):03d}"
                except: return profile
        return profile

    def getWeatherPrognosisTimes(self, etd):
        try:
            date = datetime.fromtimestamp(int(etd), tz=timezone.utc)
            hour = int(date.strftime('%H'))
            next_hour = math.ceil(hour / 3) * 3
            weather_prog_times = []
            for _ in range(5):
                days_to_add = 0
                calc_hour = next_hour
                while calc_hour >= 24:
                    calc_hour -= 24
                    days_to_add += 1
                current_prog_date = date + timedelta(days=days_to_add)
                formatted_time = current_prog_date.strftime('%d') + '00' + f"{calc_hour:02d}"
                weather_prog_times.append(formatted_time)
                next_hour += 3
            return " ".join(weather_prog_times) + 'UKM'
        except: return "PROG TIMES N/A"

    def formatLatLon(self, lat, lon): return self._format_coord(lat, True) + " " + self._format_coord(lon, False)
    def formatLatLonEtops(self, lat, lon): return f"{self._format_coord(lat, True)} {self._format_coord(lon, False)}"

    def _format_coord(self, val, is_lat):
        try:
            val = float(val)
            if is_lat:
                direction = 'N' if val >= 0 else 'S'
                deg = int(abs(val))
                minutes = (abs(val) - deg) * 60
                return f"{direction}{deg:02d}{minutes:04.1f}"
            else:
                direction = 'E' if val >= 0 else 'W'
                deg = int(abs(val))
                minutes = (abs(val) - deg) * 60
                return f"{direction}{deg:03d}{minutes:04.1f}"
        except: return ""

    def reformatCoordinate(self, coordinate): return str(coordinate)
    def formatClimbSpeedProfile(self, p): return str(p) if p else ""
    def formatDescendSpeedProfile(self, p): return str(p) if p else ""
    def formatPerfPerfFactor(self, p):
        try: return f"{('+' if float(p)-1 >=0 else '-')}{abs((float(p)-1)*100):04.1f}"
        except: return "+00.0"
    def formatAirportElevation(self, v):
        try: return f"{int(v):04d}"
        except: return "0000"
    def formatAvgWindComp(self, v):
        try: return f"{('M' if int(v)<0 else 'P')}{abs(int(v)):03d}"
        except: return "P000"
    def formatEtopsAvgWindComp(self, v): return self.formatAvgWindComp(v)
    def formatOat(self, v):
        try: return f"{('M' if int(v)<0 else 'P')}{abs(int(v)):02d}"
        except: return "P00"
    def getIsa(self, f): return "ISA"
    def getFormattedFir(self, s):
        match = re.search(r'EET\/([A-Z0-9\s]+)', str(s))
        return ("EET/" + match.group(1)) if match else ""
    def getMaxAlt(self, n): return "FL390"
    def getFuelBucketFuelValue(self, f, l): return 0
    def getFuelBucketFuelTime(self, f, l): return 0
    def interpolateEtpDistance(self, e, n): return "0000"
    def interpolateEtpAnalysisDistance(self, e, n): return "0000"
    def formatIsoTime(self, s): return s[11:16] if s else ""

    def formatWindMatrixRow(self, f):
        target_levels = ['10000', '18000', '24000', '30000', '34000', '39000', '45000']
        ident = self.reformatCoordinate(f.get('ident', ''))
        row_str = f"{ident:<7} "
        levels_data = {}
        if 'wind_data' in f and 'level' in f['wind_data']:
            levels = f['wind_data']['level']
            if isinstance(levels, dict): levels = [levels]
            for lvl in levels: levels_data[str(lvl.get('altitude'))] = lvl
        for altitude in target_levels:
            data = levels_data.get(altitude)
            if data:
                wdir, wspd, oat = int(data.get('wind_dir', 0)), int(data.get('wind_spd', 0)), int(data.get('oat', 0))
                cell_str = f"{wdir:03d}{wspd:03d}{'M' if oat < 0 else 'P'}{abs(oat):02d}"
                row_str += f"{cell_str:<10}"
            else:
                row_str += "......... "
        return row_str

def php_str_pad(string, length, pad_char=' ', pad_type='left'):
    s = str(string)
    if len(s) >= length: return s
    if pad_type == 'left': return s.rjust(length, pad_char)
    return s.ljust(length, pad_char)

def php_wordwrap(text, width=60, break_str="\n"): return text

def get_image_base64(path):
    if os.path.exists(path):
        with open(path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode("utf-8").replace('\n', '')
    return ""

# ---------------------------------------------------------
# BACKEND JINJA2 TEMPLATE STRING CACHE
# ---------------------------------------------------------
# This variable placeholder represents your long customized base HTML layout structure
# ==========================================================
# COMPLETED AND UNBROKEN ENGLISH TEMPLATE STRING
# ==========================================================
template_str = """
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8"/>
    <title>{{data.general.icao_airline}}-{{data.general.flight_number}}</title>
    <style>
        * { margin: 0; padding: 0; }
        @page {
            margin: 0.50cm 1.38cm 0.81cm 0.92cm;
            size: A4;
            font-family: Courier, monospace;
        }

        /* LANDSCAPE PAGE DEFINITION FOR NOTAMS AND MAPS */
        @page landscape-page {
            size: A4 landscape;
            margin: 0.5cm;
            @bottom-right { content: "PAGE " counter(page) " OF " counter(pages); font-size: 8pt; font-family: Courier, monospace; color: #7D7D7D;}
            @top-right { content: "PACKAGE GIA{{data.general.flight_number}}"; font-size: 8pt; font-family: Courier, monospace; color: #7D7D7D;}
        }

        body {
            font-family: Courier, monospace;
            font-size: 10.5pt;
            line-height: 12pt;
            margin: 0;
        }

        .header { position: fixed; top: -10px; left: 0; right: 0; text-align: right; font-size: 8pt; color: #7D7D7D; font-family: Courier, monospace; }
        .footer { position: fixed; bottom: -20px; left: 0; right: 0; height: 20px; text-align: right; font-size: 8pt; color: #7D7D7D; font-family: Courier, monospace; }
        .page-number:before { content: "BRIEFING TEXT {{data.general.icao_airline}}{{data.general.flight_number}}-{{php_date('d-m-y', data.times.sched_out)}}-{{php_date('Hi', data.times.sched_out)}}-{{data.origin.icao_code}} PAGE " counter(page) " OF " counter(pages); }
        .page-number-footer:before { content: "PAGE " counter(page) " of " counter(pages); }

        pre {
            margin: 0;
            white-space: pre-wrap;
            font-family: Courier, monospace;
            display: block;
            font-size: 11pt;
            line-height: 13.5pt;
        }

        .nw-container { width: 100%; border: 2px solid #000; margin-bottom: 5px; page-break-inside: avoid; }
        .nw-header {
            background-color: #ADD8E6;
            border-bottom: 1px solid #000;
            text-align: center;
            font-weight: bold;
            padding: 2px;
            font-size: 11pt;
            line-height: 1.2;
        }
        .nw-content { padding: 5px; font-size: 10.5pt; }
        .section-title {
            font-weight: bold;
            text-decoration: underline;
            display: block;
            margin-bottom: 2px;
            font-size: 10.5pt;
        }

        .footer-box {
            border: 2px solid black;
            border-radius: 15px;
            padding: 10px;
            text-align: center;
            margin-top: 15px;
            font-family: Courier, monospace;
            font-size: 9pt;
            page-break-inside: avoid;
        }

        .landscape-section {
            page: landscape-page;
            font-family: Courier, monospace;
            font-size: 10.5pt;
        }

        .notam-header-landscape {
            text-align: center;
            font-weight: bold;
            font-size: 14pt;
            margin-bottom: 5px;
            width: 100%;
        }

        .notam-columns {
            column-count: 2;
            column-gap: 1cm;
            column-rule: 1px solid #ccc;
            text-align: justify;
        }

        .notam-group {
            break-inside: auto;
            margin-bottom: 15px;
            display: block;
        }

        .notam-group-header {
            font-weight: bold;
            border-bottom: 1px solid black;
            margin-bottom: 5px;
            margin-top: 10px;
            font-size: 11pt;
            padding: 2px;
        }

        .notam-item {
            margin-bottom: 12px;
            break-inside: avoid;
        }

        .wx-header {
            text-align: center;
            font-weight: bold;
            margin-bottom: 20px;
            font-size: 12pt;
        }
        .wx-section {
            margin-bottom: 20px;
            break-inside: avoid;
        }
        .wx-airport-title {
            font-weight: bold;
            border-bottom: 1px solid #ccc;
            padding-bottom: 2px;
            margin-bottom: 5px;
            font-family: Courier, monospace;
        }
        .wx-data {
            font-family: Courier, monospace;
            font-size: 10.5pt;
            white-space: pre-wrap;
        }

        .map-container {
            text-align: center;
            width: 100%;
            height: 100%;
        }
        .map-title {
            font-weight: bold;
            font-size: 14pt;
            margin-bottom: 10px;
            text-align: center;
        }
        .map-image {
            max-width: 100%;
            max-height: 17cm;
            object-fit: contain;
            border: 1px solid #000;
        }
        .nav-row {
            white-space: pre-wrap;
            page-break-inside: avoid;
            display: block;
        }
        .page-break { page-break-after: always; }
    </style>
</head>
<body>
    <div class="header"><div class="page-number"></div></div>
    <div class="footer"><div class="page-number-footer"></div></div>
    
    <div style="text-align: center; margin-bottom: 15px;">
        {% if logo_base64 %}
        <img src="data:image/png;base64,{{logo_base64}}" style="max-height: 60px; width: auto;"><br>
        {% endif %}
        <span style="font-size: 15pt; font-family: Courier, monospace;">BRIEFING TEXT</span><br>
        {{data.general.icao_airline}}{{data.general.flight_number}} {{data.origin.icao_code}}-{{data.destination.icao_code}} {{data.aircraft.reg}} {{php_date('d/m/y', data.times.sched_out)}}
    </div>
    
    <pre>
1. CREW ALERT
NIL

2. AIRCRAFT STATUS
APU : SERVICEABLE
HIL : NIL

3. NOTAM & WEATHER
    </pre>

    {% for apt in airport_info %}
    <div class="nw-container">
        <div class="nw-header">
            {{apt.icao}}/{{apt.iata}}<br>
            {{apt.time}}
        </div>
        <div class="nw-content">
            <span class="section-title">NOTAM:</span>
            <pre style="margin:0; padding:0;">
{% if apt.notams %}
{%- for n in apt.notams -%}
{{n.id}} {{n.text}}
{%- if not loop.last -%}
.
{% endif %}
{% endfor -%}
{%- else -%}
NO SIGNIFICANT NOTAM.
{%- endif -%}
            </pre>
            <div style="border-top: 1px solid #000; margin: 5px 0;"></div>
            <span class="section-title">FORECAST WEATHER:</span>
            <pre style="margin:0;">{{apt.taf if apt.taf else 'REFER TO WX PKG'}}</pre>
        </div>
    </div>
    {% endfor %}

    <pre>
4. SIGNIFICANT WX EN-ROUTE
TYPHOON : NIL
TURBULENCE : LIGHT
JETSTREAM : PLEASE CHECK SIGWX
CLOUDS : PLEASE CHECK SIGWX
WIND COMP. : {{helper.formatAvgWindComp(data.general.avg_wind_comp)}}

5. EST PAYLOAD
PAX : {{data.weights.pax_count}}   CARGO : {{data.weights.cargo}}   PAYLOAD : {{data.weights.payload}} KGS
    </pre>

    <div class="footer-box">
        <b>Flight Dispatch Center</b><br>
        Operation Center II Building 3rd Floor | Garuda City | Soekarno-Hatta International Airport<br>
        Cengkareng 19120, Indonesia<br>
        Office Phone: +62 21 559 0451, +62 21 2560 1524 | Fax: +62 21 550 1911<br>
        Email: flight-dispatch-center@garuda-indonesia.com | SITA: JKTOIGA
    </div>
    
    <div class="page-break"></div>

    <pre>
---------------------------------------------------------------------------
                            DISPATCH RELEASE
---------------------------------------------------------------------------
VALID U/I {{php_date('Hi', (data.times.sched_out|int) + 21600)}}Z  REF PLAN {{data.params.request_id[-5:]}} / REV NBR {{data.general.release}}
{{data.atc.callsign}}  {{php_date('dMy',data.times.sched_out)}}  ETD {{php_date('Hi',data.times.sched_out)}}Z  ETA {{php_date('Hi',data.times.est_in)}}Z / FT {{php_date('Hi',data.times.est_block)}} IFR {{data.aircraft.reg}}

1. POD/POA : {{data.origin.icao_code}}/{{data.destination.icao_code}}
2. INITIAL DESTINATION (FOR PLANNED RE-DISPATCH AS APPLICABLE):
3. WX ORG {{data.origin.iata_code}}/{{data.origin.icao_code}} CHECKED
   {% if alternates|length > 0 %}AL1 {{alternates[0].iata_code}}/{{alternates[0].icao_code}} CHECKED {% endif %}
   DES {{data.destination.iata_code}}/{{data.destination.icao_code}} CHECKED
   {% if alternates|length > 1 %}AL2 {{alternates[1].iata_code}}/{{alternates[1].icao_code}} CHECKED {% endif %}
4. NOTAM AND/OR AERONAUTICAL INFORMATION ALL NOTAMS SIGNIFICANT TO FLIGHT ARE CONSIDERED
5. LOAD EST PAX ADL{{php_str_pad(data.weights.pax_count,3,'0','left')}}/CHD000/INF000 TOTAL {{data.weights.pax_count}}
   EST CGO {{data.weights.cargo}} KGS   EST PLD {{data.weights.payload}} KGS
6. FLIGHT PLAN DATA
   TRP {{php_str_pad(data.fuel.enroute_burn, 6, '0', 'left')}} KGS  {{php_date('H:i',data.times.est_time_enroute)}}
   EZF {{php_str_pad(data.weights.est_zfw, 6, '0', 'left')}} MAX {{php_str_pad(data.weights.max_zfw, 6, '0', 'left')}}
   RES {{php_str_pad(data.fuel.reserve, 6, '0', 'left')}} KGS  {{php_date('H:i',data.times.reserve_time)}}
   ELW {{php_str_pad(data.weights.est_ldw, 6, '0', 'left')}} MAX {{php_str_pad(data.weights.max_ldw, 6, '0', 'left')}}
   {% if alternates|length > 0 %}ALT {{php_str_pad(data.fuel.alternate_burn, 6, '0', 'left')}} KGS  {{php_date('H:i',alternates[0].burn)}}
   ETW {{php_str_pad(data.weights.est_tow, 6, '0', 'left')}} MAX {{php_str_pad(data.weights.max_tow, 6, '0', 'left')}}{% endif %}
   BLK {{php_str_pad(data.fuel.plan_ramp, 6, '0', 'left')}} KGS  {{php_date('H:i',data.times.endurance)}}
7. ETOPS FLIGHT: {% if not data.etops or data.etops == '0' %} NO {% else %} YES ETOPS DIVERSION TIME: {{data.etops.rule}} MIN {% endif %}
8. ENROUTE / ETOPS ALTERNATE: {{etops_alternates_str}}
9. TAKE OFF ALTERNATE (IF REQUIRED) : ......
10. DESTINATION ALTERNATE: {% if alternates|length > 0 %}{{alternates[0].icao_code}}{% else %}NIL{% endif %}

THIS OPERATIONAL FLIGHT PLAN COMPLIES WITH ALL CAR/CASR RULES AND APPLICABLE REQUIREMENTS.
THE FLIGHT IS AUTHORIZED AND RELEASED.

DISPATCHER SIGNATURE: ID #{{foo_id}}
PIC SIGNATURE: .......................................
    </pre>

    <div class="page-break"></div>

    <pre>
---------------------------------------------------------------------------
                         OPERATIONAL FLIGHT PLAN
---------------------------------------------------------------------------
{{data.general.icao_airline}}{{data.general.flight_number}} / {{data.atc.callsign}}  OFP NBR {{data.general.release}}  {{php_date('dMy',data.times.sched_out)}}
ROUTE: {{data.general.route}}

---------------------------------------------------------------------------
FUEL CALCULATION           WEIGHTS         FUEL    TIME
---------------------------------------------------------------------------
MINIMUM TAKEOFF FUEL       EST ZFW  {{php_str_pad(data.weights.est_zfw,6)}}  TAXI    {{php_str_pad(data.fuel.taxi,5)}}  {{php_date('H:i',data.times.taxi)}}
TRIP FUEL    {{php_str_pad(data.fuel.enroute_burn,6)}}     EST TOW  {{php_str_pad(data.weights.est_tow,6)}}  TRIP    {{php_str_pad(data.fuel.enroute_burn,5)}}  {{php_date('H:i',data.times.est_time_enroute)}}
CONT 5%      {{php_str_pad(data.fuel.contingency,6)}}     EST LDW  {{php_str_pad(data.weights.est_ldw,6)}}  CONT    {{php_str_pad(data.fuel.contingency,5)}}  {{php_date('H:i',data.times.contingency_time)}}
ALTN         {{php_str_pad(data.fuel.alternate_burn,6)}}                             ALTN    {{php_str_pad(data.fuel.alternate_burn,5)}}  {{php_date('H:i',data.times.alternate_time)}}
HOLD/FINAL   {{php_str_pad(data.fuel.reserve,6)}}                             HOLD    {{php_str_pad(data.fuel.reserve,5)}}  {{php_date('H:i',data.times.reserve_time)}}
MIN T/O FUEL {{php_str_pad(data.fuel.min_takeoff,6)}}                             MIN TO  {{php_str_pad(data.fuel.min_takeoff,5)}}  {{php_date('H:i',data.times.endurance)}}
EXTRA/EXTRA  {{php_str_pad(data.fuel.extra,6)}}                             EXTRA   {{php_str_pad(data.fuel.extra,5)}}  {{php_date('H:i',data.times.extra_time)}}
BLOCK FUEL   {{php_str_pad(data.fuel.plan_ramp,6)}}                             BLOCK   {{php_str_pad(data.fuel.plan_ramp,5)}}  {{php_date('H:i',data.times.endurance)}}

---------------------------------------------------------------------------
ATC FLIGHT PLAN DATA
---------------------------------------------------------------------------
{{data.atc.fpl}}
    </pre>

    <div class="page-break"></div>

    <pre>
---------------------------------------------------------------------------
                            NAVIGATION LOG
---------------------------------------------------------------------------
AWY     WAYPOINT  LAT/LON     ALT   WIND   OAT  FREQ   TAS  GSPD  DIST  REM
        TAS/GSPD  TT/MC       EET   ATO    MRE  FUEL   PBD  ZID   ACC   USED
---------------------------------------------------------------------------
        {{data.origin.icao_code}}
        Elev:{{helper.formatAirportElevation(data.origin.elevation)}}FT  {{helper.formatLatLon(data.origin.pos_lat, data.origin.pos_lon)}}                {{php_str_pad(data.fuel.plan_ramp,5)}}
    </pre>
    {% if data.navlog and data.navlog.fix %}
    {% for f in data.navlog.fix %}
    <div class="nav-row">
    <pre>
{{php_str_pad(f.awid,7,' ','right')}} {{php_str_pad(f.ident,9,' ','right')}} {{helper.formatLatLon(f.pos_lat, f.pos_lon)}}  {{php_str_pad(f.altitude,5)}} {{f.wind_dir:03d}}/{{f.wind_spd:03d}} {{helper.formatOat(f.oat)}} -----  {{f.tas:03d}}  {{f.gs:03d}}  {{php_str_pad(f.distance,4)}}  {{php_str_pad(f.distance_remaining,4)}}
        .../...   .../...     {{php_date('H:i', f.time_egt|int)}}  ...:...  ...  {{php_str_pad(f.fuel_remaining,5)}} .../... ...   ...   .....
    </pre>
    </div>
    {% endfor %}
    {% endif %}
    <pre>
        {{data.destination.icao_code}}
        Elev:{{helper.formatAirportElevation(data.destination.elevation)}}FT  {{helper.formatLatLon(data.destination.pos_lat, data.destination.pos_lon)}}                {{php_str_pad(data.fuel.reserve,5)}}
---------------------------------------------------------------------------
    </pre>

    <div class="page-break"></div>

    <pre>
---------------------------------------------------------------------------
                        ENROUTE WIND & OAT MATRIX
---------------------------------------------------------------------------
IDENT   FL100     FL180     FL240     FL300     FL340     FL390     FL450
---------------------------------------------------------------------------
    </pre>
    {% if data.navlog and data.navlog.fix %}
    {% for f in data.navlog.fix %}
    <div class="nav-row">
    <pre>{{helper.formatWindMatrixRow(f)}}</pre>
    </div>
    {% endfor %}
    {% endif %}

    <div class="page-break"></div>

    <div class="landscape-section">
        <div class="notam-header-landscape">AERONAUTICAL NOTAM RECORDS</div>
        <div class="notam-columns">
            {% for group in notam_groups %}
            <div class="notam-group">
                <div class="notam-group-header">{{group.title}}</div>
                {% if group.notams %}
                    {% for n in group.notams %}
                    <div class="notam-item">
                        <b>{{n.notam_id or n.notamdrec_id}}</b><br>
                        <pre style="font-size: 9pt; line-height: 11pt;">{{n.notam_text or n.notam_raw}}</pre>
                    </div>
                    {% endfor %}
                {% else %}
                    <p style="font-size: 10pt; font-style: italic;">No critical NOTAM constraints found for this sector.</p>
                {% endif %}
            </div>
            {% endfor %}
        </div>
    </div>

    <div class="page-break"></div>

    <div class="wx-header">METEOROLOGICAL PACKAGES (METAR & TAF DATA)</div>
    {% for wx in weather_info %}
    <div class="wx-section">
        <div class="wx-airport-title">{{wx.title}}</div>
        <div class="wx-data"><pre>{{wx.data}}</pre></div>
    </div>
    {% endfor %}

    {% if map_images %}
    {% for m in map_images %}
    <div class="page-break"></div>
    <div class="map-container">
        <div class="map-title">{{m.name|upper}}</div>
        <img class="map-image" src="{{m.url}}" alt="{{m.name}}">
    </div>
    {% endfor %}
    {% endif %}

</body>
</html>
"""

# ---------------------------------------------------------
# MAIN INTERFACE DISPATCH PORTAL (DASHBOARD)
# ---------------------------------------------------------
def dashboard():
    st.markdown("""
    <style>
        .appview-container .main .block-container { padding: 3rem 5rem !important; } 
        header[data-testid="stHeader"] { visibility: visible !important; }
    </style>
    """, unsafe_allow_html=True)
    
    # Extract query context parameters passed by the PHP network endpoint (?username=...)
    query_params = st.query_params
    sb_username_url = query_params.get("username", "")

    st.sidebar.title(f"Welcome, {sb_username_url if sb_username_url else 'Crew'}")
    st.title("OFP & Briefing Package Generator")
    
    logo_path = "FDCGA.png"
    logo_base64 = get_image_base64(logo_path)

    # Context autofills the entry block safely
    sb_userid = st.text_input("SimBrief Username / User ID Account Input:", value=sb_username_url)
    
    # Layout rendering anchor container guarantees download UI elements display correctly outside closures
    download_placeholder = st.container()
    
    if sb_userid:
        with st.spinner(f"⏳ Syncing operational data feed from SimBrief ({sb_userid})..."):
            if sb_userid.isdigit():
                sb_url = f"https://www.simbrief.com/api/xml.fetcher.php?userid={sb_userid}&json=1"
            else:
                sb_url = f"https://www.simbrief.com/api/xml.fetcher.php?username={sb_userid}&json=1"
                
            try:
                response = requests.get(sb_url, timeout=15)
                response.raise_for_status()
                data_json = response.json()
                
                if 'fetch' in data_json and data_json['fetch']['status'] != 'Success':
                    st.error(f"⚠️ SimBrief Network Exception: {data_json['fetch']['status']}")
                    return
            except Exception as e:
                st.error(f"❌ Network Layer Connection Denied: {e}")
                return

        with st.spinner("⚙️ Transforming payload context data maps and generating layout rules..."):
            try:
                # 1. STRUCTURAL DATA MUTATIONS
                data_obj = dict_to_obj(data_json)
                
                raw_alternates = data_obj.get('alternate', [])
                if isinstance(raw_alternates, dict): alternates_list = [raw_alternates]
                elif isinstance(raw_alternates, list): alternates_list = raw_alternates
                else: alternates_list = []
                data_obj['alternate'] = alternates_list 
                
                raw_alt_nav = data_obj.get('alternate_navlog')
                if isinstance(raw_alt_nav, list) and len(raw_alt_nav) > 0: navlog_alt1 = raw_alt_nav[0]
                elif isinstance(raw_alt_nav, dict): navlog_alt1 = raw_alt_nav
                else: navlog_alt1 = None
                
                map_images = []
                if data_obj.get('images') and data_obj.images.get('map'):
                    base_url = data_obj.images.directory
                    maps_raw = data_obj.images.map
                    if isinstance(maps_raw, dict): maps_raw = [maps_raw]
                    for m in maps_raw: map_images.append({'name': m.name, 'url': base_url + m.link})

                airport_info = []
                weather_info = []

                # Departure Info Mapping
                try:
                    t_val = php_date('Hi', data_obj.times.sched_out) + "Z"
                    airport_info.append({'icao': data_obj.origin.icao_code, 'iata': data_obj.origin.iata_code, 'label': 'STD', 'time': t_val, 'notams': get_filtered_notams(data_obj.origin.get('notam')), 'taf': data_obj.origin.get('taf', 'N/A')})
                    weather_info.append({'title': f"DEPARTURE AIRPORT : {data_obj.origin.icao_code}", 'data': (data_obj.origin.get('taf', '') or "") + "\n" + (data_obj.origin.get('metar', '') or "")})
                except: pass

                # Destination Info Mapping
                try:
                    t_val = php_date('Hi', data_obj.times.est_in) + "Z"
                    airport_info.append({'icao': data_obj.destination.icao_code, 'iata': data_obj.destination.iata_code, 'label': 'ETA', 'time': t_val, 'notams': get_filtered_notams(data_obj.destination.get('notam')), 'taf': data_obj.destination.get('taf', 'N/A')})
                    weather_info.append({'title': f"DESTINATION AIRPORT : {data_obj.destination.icao_code}", 'data': (data_obj.destination.get('taf', '') or "") + "\n" + (data_obj.destination.get('metar', '') or "")})
                except: pass

                # Diversion Airports Loops
                for alt in alternates_list:
                    try: t_val = php_date('Hi', int(data_obj.times.est_in) + int(alt.ete)) + "Z"
                    except: t_val = "...."
                    airport_info.append({'icao': alt.icao_code, 'iata': alt.iata_code, 'label': 'ETA (ALTN)', 'time': t_val, 'notams': get_filtered_notams(alt.get('notam')), 'taf': alt.get('taf', 'N/A')})
                    weather_info.append({'title': f"DESTINATION ALTERNATE AIRPORT : {alt.icao_code}", 'data': (alt.get('taf', '') or "") + "\n" + (alt.get('metar', '') or "")})

                # Extended Twin Engine Ops (ETOPS Verification)
                etops_apts_list = []
                if data_obj.get('etops') and 'suitable_airport' in data_obj.etops:
                    etops_apts = data_obj.etops.suitable_airport
                    if isinstance(etops_apts, dict): etops_apts = [etops_apts]
                    for apt in etops_apts:
                        etops_apts_list.append(apt.icao_code)
                        airport_info.append({'icao': apt.icao_code, 'iata': apt.get('iata_code', ''), 'label': 'VALIDITY', 'time': 'REFER ETOPS', 'notams': [], 'taf': 'Refer to Wx Pkg'})
                        wx_data = (apt.get('taf', '') or "") + "\n" + (apt.get('metar', '') or "")
                        if not wx_data.strip(): wx_data = "WEATHER DATA NOT AVAILABLE IN JSON"
                        weather_info.append({'title': f"ENROUTE ALTERNATE AIRPORT : {apt.icao_code}", 'data': wx_data})

                # Aeronautical NOTAM Records Filter
                notam_groups = []
                notam_groups.append({'title': f"DEPARTURE AIRPORT : {data_obj.origin.icao_code}", 'notams': data_obj.origin.get('notam', [])})
                notam_groups.append({'title': f"DESTINATION AIRPORT : {data_obj.destination.icao_code}", 'notams': data_obj.destination.get('notam', [])})
                for alt in alternates_list: notam_groups.append({'title': f"ALTERNATE AIRPORT : {alt.icao_code}", 'notams': alt.get('notam', [])})
                
                global_notams = []
                if 'notams' in data_obj and 'notamdrec' in data_obj.notams:
                    global_notams = data_obj.notams.notamdrec
                    if isinstance(global_notams, dict): global_notams = [global_notams]

                for etops_icao in etops_apts_list:
                    apt_notams = [n for n in global_notams if n.get('icao_id') == etops_icao]
                    if apt_notams: notam_groups.append({'title': f"ETOPS ALTERNATE : {etops_icao}", 'notams': apt_notams})

                enroute_firs = data_obj.atc.get('fir_enroute', [])
                for fir in enroute_firs:
                    fir_n = [n for n in global_notams if n.get('icao_id') == fir]
                    if fir_n: notam_groups.append({'title': f"ENROUTE FIR : {fir}", 'notams': fir_n})

                for group in notam_groups:
                    if isinstance(group['notams'], dict): group['notams'] = [group['notams']]

                etops_alternates_str = "..."
                if data_obj.get('etops') and isinstance(data_obj.etops, dict) and 'suitable_airport' in data_obj.etops:
                    airports = data_obj.etops.suitable_airport
                    if isinstance(airports, dict): airports = [airports]
                    if isinstance(airports, list):
                         codes = [apt.get('icao_code', '') for apt in airports]
                         etops_alternates_str = " ".join(codes)

                try:
                    req_id = data_obj.params.request_id
                    hash_int = int(req_id[:8], 16) if req_id else 0
                    foo_id = 1000 + (hash_int % 9000)
                except: foo_id = 1234
                
                # 2. RUNTIME GRAPHICS COMPILATION & TEMPLATE INJECTION
                env = Environment(loader=BaseLoader(), extensions=['jinja2.ext.loopcontrols'])
                env.globals.update({
                    'php_date': php_date, 'php_str_pad': php_str_pad, 'php_wordwrap': php_wordwrap,
                    'helper': PythonHelper(), 'foo_id': foo_id, 'etops_alternates_str': etops_alternates_str,
                    'airport_info': airport_info, 'notam_groups': notam_groups, 'alternates': alternates_list,
                    'weather_info': weather_info, 'map_images': map_images, 'navlog_alt1': navlog_alt1,
                    'logo_base64': logo_base64
                })
                
                template = env.from_string(template_str)
                rendered_html = template.render(data=data_obj, airport_info=airport_info, alternates=alternates_list, notam_groups=notam_groups, weather_info=weather_info, map_images=map_images)
                
                # Generate exact PDF file buffer binary stream via WeasyPrint engine
                pdf_buffer = io.BytesIO()
                HTML(string=rendered_html).write_pdf(pdf_buffer)
                
                # 3. STREAM INTERACTIVE UI CONTAINER COMPONENT
                with download_placeholder:
                    st.success("✅ Operational Briefing Package processed successfully!")
                    pdf_filename = f"GIA{data_obj.general.flight_number}_Briefing_Final.pdf"
                    st.download_button(
                        label="📥 Download Flight Plan PDF",
                        data=pdf_buffer.getvalue(),
                        file_name=pdf_filename,
                        mime="application/pdf",
                        type="primary"
                    )
            except Exception as e:
                st.error(f"❌ File Rendering Exception: {e}")
    else:
        st.info("💡 Awaiting SimBrief runtime parameter context synced from active EFB PHP dashboards...")

# ---------------------------------------------------------
# APPLICATION LIFECYCLE DISPATCHER
# ---------------------------------------------------------
dashboard()
