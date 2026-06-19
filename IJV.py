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
# INITIAL APP & THEME CONFIGURATION
# ---------------------------------------------------------
st.set_page_config(
    page_title="GIA EFB - Briefing Streamer", 
    page_icon="✈️", 
    layout="wide", 
    initial_sidebar_state="collapsed"
)

# Force clean minimalist styles to hide Streamlit header and footer wrappers
st.markdown("""
    <style>
        #MainMenu {visibility: hidden;}
        footer {visibility: hidden;}
        header {visibility: hidden;}
        .reportview-container .main .block-container{ padding-top: 1rem; }
    </style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------
# DATA LAYER & FILTER HELPER OBJECTS
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
    if not timestamp or str(timestamp).strip() in ["0", ""]:
        return ""
    try:
        # Check if the timestamp is an ISO format string (contains 'T' or '-')
        if isinstance(timestamp, str) and ('T' in timestamp or '-' in timestamp):
            # Clean up the string representation trailing 'Z' if present
            clean_ts = timestamp.replace('Z', '+00:00')
            dt = datetime.fromisoformat(clean_ts)
        else:
            # Fall back to standard epoch integer timestamp parsing
            ts = int(float(timestamp))
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
        if fmt == 'd/m/y': return dt.strftime('%d/%m/%y')
        return dt.strftime('%Y-%m-%d')
    except Exception:
        return ""

def format_cruise_profile(profile, cost_index):
    if not profile: return ""
    p = str(profile).upper().replace(" ", "")
    if p.startswith('M') or p == 'LRC': return str(profile)
    if p.startswith('CI'):
        matches = re.findall(r'\d+', p)
        return f"CI{int(matches[0]):03d}" if matches else f"CI{int(cost_index):03d}"
    return str(profile)

def format_fuelfact(p):
    try:
        val = float(p) - 1.0
        sign = '+' if val >= 0 else '-'
        return f"{sign}{abs(val * 100):05.1f}"
    except:
        return "P00.0"

def format_elevation(v):
    try:
        return f"{int(v):04d}"
    except:
        return "0000"

def format_wind_comp(v):
    try:
        int_v = int(v)
        sign = 'P' if int_v >= 0 else 'M'
        return f"{sign}{abs(int_v):03d}"
    except:
        return "P000"

def get_formatted_fir(s):
    matches = re.search(r'EET\/([A-Z0-9\s]+)', str(s))
    return f"EET/{matches.group(1)}" if matches else ""

def format_coord(val, is_lat):
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
    except:
        return ""

def format_latlon(v1, v2):
    return f"{format_coord(v1, True)} {format_coord(v2, False)}"

def get_filtered_notams(notam_raw, limit=4):
    notam_list = []
    if isinstance(notam_raw, list): notam_list = notam_raw
    elif notam_raw: notam_list = [notam_raw]
    
    if not notam_list: return []
    urgent_keywords = ['CLSD', 'CLOSED', 'U/S', 'UNSERVICEABLE', 'DANGER', 'RESTRICTED', 'RWY', 'RUNWAY', 'ILS', 'GNSS', 'GPS']
    scored, seen = [], []
    
    for n in notam_list:
        nid = n.get('notam_id', '')
        if nid in seen: continue
        txt = n.get('notam_text', n.get('notam_raw', ''))
        score = sum(1 for kw in urgent_keywords if kw in txt.upper())
        if score == 0: score = 0.1
        seen.append(nid)
        scored.append({'id': nid, 'text': txt, 'score': score})
        
    scored.sort(key=lambda x: x['score'], reverse=True)
    return scored[:limit]

def format_wind_matrix_row(f):
    target_levels = ['10000', '18000', '24000', '30000', '34000', '39000', '45000']
    ident = f.get('ident', '')
    row_str = f"{ident:<7} "
    
    levels_data = {}
    if 'wind_data' in f and 'level' in f['wind_data']:
        levels = f['wind_data']['level']
        if isinstance(levels, dict): levels = [levels]
        for lvl in levels:
            levels_data[str(lvl.get('altitude', ''))] = lvl
            
    for alt in target_levels:
        if alt in levels_data:
            d = levels_data[alt]
            wdir = int(d.get('wind_dir', 0))
            wspd = int(d.get('wind_spd', 0))
            oat = int(d.get('oat', 0))
            sign = 'M' if oat < 0 else 'P'
            cell = f"{wdir:03d}{wspd:03d}{sign}{abs(oat):02d}"
            row_str += f"{cell:<10}"
        else:
            row_str += "......... "
    return row_str

def get_weather_prognosis_times(etd):
    try:
        dt = datetime.fromtimestamp(int(etd), tz=timezone.utc)
        hour = dt.hour
        next_hour = math.ceil(hour / 3) * 3
        times = []
        for i in range(5):
            calc_hour = next_hour
            days_to_add = 0
            while calc_hour >= 24:
                calc_hour -= 24
                days_to_add += 1
            curr_date = dt
            if days_to_add > 0:
                curr_date += timedelta(days=days_to_add)
            times.append(f"{curr_date.strftime('%d')}00{calc_hour:02d}")
            next_hour += 3
        return " ".join(times) + 'UKM'
    except Exception:
        return "PROG TIMES N/A"

# ---------------------------------------------------------
# JINJA HTML SOURCE TEMPLATE (GIA805 STYLES)
# ---------------------------------------------------------
TEMPLATE_STR = """
<!doctype html>
<html>
<head>
    <meta charset="utf-8"/>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        @page {
            margin: 1.40cm 1.20cm 1.20cm 1.20cm;
            size: A4 portrait;
            font-family: 'Courier New', Courier, monospace;
            @top-left {
                content: "{{ gia805MetaHeader }} PAGE " counter(page) " OF " counter(pages);
                font-family: 'Courier New', Courier, monospace;
                font-size: 10.5pt;
                font-weight: bold;
                padding-bottom: 20px;
            }
            @bottom-left {
                content: "PAGE " counter(page) " of " counter(pages);
                font-family: 'Courier New', Courier, monospace;
                font-size: 10.5pt;
                font-weight: bold;
                padding-top: 15px;
            }
        }
        @page landscape-page {
            size: A4 landscape;
            margin: 0.8cm 1.0cm;
            @top-left {
                content: "PACKAGE GIA{{ data.general.flight_number }}";
                font-family: 'Courier New', Courier, monospace;
                font-size: 10pt;
                font-weight: bold;
            }
            @bottom-right {
                content: "PAGE " counter(page) " OF " counter(pages);
                font-family: 'Courier New', Courier, monospace;
                font-size: 10pt;
                font-weight: bold;
            }
        }
        body {
            font-family: 'Courier New', Courier, monospace;
            font-size: 11pt;
            line-height: 14pt;
            color: #000;
        }
        pre {
            white-space: pre-wrap;
            font-family: 'Courier New', Courier, monospace;
            display: block;
            font-size: 11pt;
            line-height: 14pt;
        }
        .nw-container { width: 100%; margin-bottom: 15px; page-break-inside: avoid; }
        .nw-header { font-weight: bold; font-size: 11.5pt; margin-bottom: 4px; text-transform: uppercase; }
        .nw-content { font-size: 11pt; }
        .section-title { font-weight: bold; display: block; margin-top: 4px; margin-bottom: 2px; }
        .footer-box {
            border: 1px solid black; padding: 8px; text-align: center; margin-top: 25px;
            font-size: 9.5pt; line-height: 12pt; page-break-inside: avoid;
        }
        .landscape-section { page: landscape-page; font-size: 10.5pt; }
        .notam-header-landscape { text-align: center; font-weight: bold; font-size: 14pt; margin-bottom: 12px; width: 100%; }
        .notam-columns { column-count: 2; column-gap: 0.8cm; text-align: justify; }
        .notam-group { break-inside: auto; margin-bottom: 15px; display: block; }
        .notam-group-header { font-weight: bold; border-bottom: 1px dashed black; margin-bottom: 5px; margin-top: 10px; font-size: 11.5pt; padding: 2px; }
        .notam-item { margin-bottom: 12px; break-inside: avoid; }
        .wx-header { text-align: center; font-weight: bold; margin-bottom: 25px; font-size: 12pt; }
        .wx-section { margin-bottom: 25px; break-inside: avoid; }
        .wx-airport-title { font-weight: bold; border-bottom: 1px solid #000; padding-bottom: 2px; margin-bottom: 8px; }
        .wx-data { font-size: 11pt; white-space: pre-wrap; line-height: 14pt; }
        .map-container { text-align: center; width: 100%; height: 100%; }
        .map-title { font-weight: bold; font-size: 13pt; margin-bottom: 10px; text-align: center; }
        .map-image { max-width: 100%; max-height: 16.5cm; object-fit: contain; border: 1px solid #000; }
        .nav-row { white-space: pre-wrap; page-break-inside: avoid; display: block; }
        .page-break { page-break-after: always; }
        .dashed-separator { border: 0; border-top: 1px dashed #000; margin: 12px 0; width: 100%; }
    </style>
</head>
<body>

    <div style="margin-bottom: 25px;">
        <span style="font-size: 12pt; font-weight: bold; display: block;">Garuda Indonesia</span>
        <span style="font-size: 11pt; display: block; margin-bottom: 15px;">FLIGHT DISPATCH CENTER</span>
        <span style="font-size: 11pt; font-weight: bold; display: block;">BRIEFING TEXT</span>
        <span style="font-size: 11pt; display: block; font-weight: bold; margin-top: 2px;">
            {{ callsign }} {{ data.origin.icao_code }}-{{ data.destination.icao_code }} {{ data.aircraft.reg }} {{ flight_date }}
        </span>
    </div>

    <pre>
1. CREW ALERT
   NIL

2. AIRCRAFT STATUS
   APU       : SERVICEABLE
   HIL       : NIL

3. NOTAM & WEATHER
    </pre>

    {% for apt in airport_info %}
    <div class="nw-container">
        <div class="nw-header">
            {{ apt.icao }}/{{ apt.iata }} &nbsp;&nbsp; {{ apt.time }}
        </div>
        <div class="nw-content">
            <span class="section-title">NOTAM:</span>
            <pre style="margin: 0; font-size: 11pt;">{% if apt.notams %}{% for n in apt.notams %}{{ n.id }}
{{ n.text }}

{% endfor %}{% else %}NO SIGNIFICANT NOTAM.
{% endif %}</pre>
            <span class="section-title">FORECAST WEATHER:</span>
            <pre style="margin: 0; font-size: 11pt;">{{ apt.taf }}</pre>
        </div>
    </div>
    <div class="dashed-separator"></div>
    {% endfor %}

    <pre>
4. SIGNIFICANT WX EN-ROUTE
   TYPHOON      : NIL
   TURBULENCE   : LIGHT
   JETSTREAM    : PSE CHECK SIGWX
   CLOUDS       : PSE CHECK SIGWX
   WIND COMP.   : {{ avg_wind_comp }}

5. EST PAYLOAD
   PAX          : {{ data.weights.pax_count }}
   CARGO        : {{ data.weights.cargo }}
   PAYLOAD      : {{ data.weights.payload }} KGS
    </pre>

    <div class="footer-box">
        <b>Flight Dispatch Center</b><br>
        Operation Center II Building 3rd Floor | Garuda City | Soekarno-Hatta International Airport<br>
        Cengkareng 19120, Indonesia<br>
        Office Phone: +62 21 559 0451, +62 21 2560 1524, +62 21 559 15428 | Fax: +62 21 550 1911<br>
        Email Address: flight-dispatch-center@garuda-indonesia.com; cflightdispatch@gmail.com;<br>
        SITA Address: JKTOIGA
    </div>

    <div class="page-break"></div>

    <pre>---------------------------------------------------------------------------
                             DISPATCH RELEASE
---------------------------------------------------------------------------
VALID U/I {{ valid_ui }}Z
REF PLAN {{ req_id_short }} / REV NBR {{ data.general.release }}
{{ callsign }}   {{ date_dMy }} ETD {{ etd_z }}Z  ETA {{ eta_z }}Z / FT {{ block_ft }} IFR {{ data.aircraft.reg }}

1.  POD/POA : {{ data.origin.icao_code }}/{{ data.destination.icao_code }}

2.  INITIAL DESTINATION (FOR PLANNED RE-DISPATCH AS APPLICABLE):

3.  WX
    ORG {{ data.origin.iata_code }}/{{ data.origin.icao_code }}  CHECKED             {% if alternates|length > 0 %}AL1 {{ alternates[0].iata_code }}/{{ alternates[0].icao_code }}  CHECKED {% endif %}
    DES {{ data.destination.iata_code }}/{{ data.destination.icao_code }}  CHECKED             {% if alternates|length > 1 %}AL2 {{ alternates[1].iata_code }}/{{ alternates[1].icao_code }}  CHECKED {% endif %}

4.  NOTAM AND/OR AERONAUTICAL INFORMATION
    ALL NOTAMS SIGNIFICANT TO FLIGHT ARE CONSIDERED

5.  LOAD
    EST PAX ADL{{ "%03d"|format(data.weights.pax_count|int) }}/CHD000/INF000          TOTAL  {{ data.weights.pax_count }}
    EST CGO {{ data.weights.cargo }} KGS
    EST PLD {{ data.weights.payload }} KGS

6.  FLIGHT PLAN DATA
    TRP   {{ "%06d"|format(data.fuel.enroute_burn|int) }}   KGS {{ trip_time }}         EZF    {{ "%06d"|format(data.weights.est_zfw|int) }}   MAX {{ "%06d"|format(data.weights.max_zfw|int) }}
    RES   {{ "%06d"|format(data.fuel.reserve|int) }}   KGS {{ reserve_time }}         ELW    {{ "%06d"|format(data.weights.est_ldw|int) }}   MAX {{ "%06d"|format(data.weights.max_ldw|int) }}
    {% if alternates|length > 0 %}ALT   {{ "%06d"|format(data.fuel.alternate_burn|int) }}   KGS {{ alternates[0].burn_time_formatted }}         ETW    {{ "%06d"|format(data.weights.est_tow|int) }}   MAX {{ "%06d"|format(data.weights.max_tow|int) }}{% endif %}
    BLK   {{ "%06d"|format(data.fuel.plan_ramp|int) }}   KGS {{ endurance }}

7.  ETOPS FLIGHT: {% if not data.etops or data.etops == '0' %} NO {% else %} YES     ETOPS DIVERSION TIME: {{ data.etops.rule }} MIN {% endif %}

8.  ENROUTE / ETOPS ALTERNATE: {{ etops_str }}

9.  TAKE OFF ALTERNATE (IF REQUIRED) : ......

10. DESTINATION ALTERNATE: 1. {% if alternates|length > 0 %}{{ alternates[0].icao_code }}{% else %}....{% endif %}   2. {% if alternates|length > 1 %}{{ alternates[1].icao_code }}{% else %}....{% endif %}

11. FUEL REQ AFTER BRIEF: ............. KGS

    REASON FOR DISCRETIONARY FUEL :....................

12. NOTOC  / (DGR):

13. REMARKS: NONE

I HEREBY RELEASE THIS FLIGHT IN FULL COMPLIANCE WITH CIVIL AVIATION SAFETY
REGULATIONS AND OPERATION MANUAL PART A (OM-A)
    DISPATCHED BY               : FOO. {{ data.crew.dx|upper }} - {{ fooId }}

I HEREBY PREPARE AND ARRANGE THIS FLIGHT DISPATCH RELEASE ACCORDING TO THE
INSTRUCTION AND DATA PROVIDED BY PT. GARUDA INDONESIA (PERSERO) TBK.
    NAME / ID                   : .................. / ........

                                   SIGN ......................

I HEREBY ACCEPT THIS FLIGHT DISPATCH RELEASE WITH FULL ACKNOWLEDGEMENT.
    PILOT IN COMMAND            :  CAPT. {{ data.crew.cpt|upper }}

                                   SIGN ......................</pre>

    <div class="page-break"></div>

    <pre>---------------------------------------------------------------------------
                        COMPUTERIZED FLIGHT PLAN
---------------------------------------------------------------------------
PLAN {{ req_id_short }} / REV NUM {{ "%02d"|format(data.general.release|int) }}       {{ data.origin.icao_code }} TO {{ data.destination.icao_code }}  {{ data.aircraft.icaocode }}  {{ cruise_profile }}/F  IFR  {{ date_slash }}
NONSTOP COMPUTED {{ time_generated }} ETD {{ etd_z }}Z PROGS {{ prog_times }} {{ data.aircraft.reg }} KGS

GARUDA INDONESIA CFP

SPD SKD   CLB-{{ data.general.climb_profile }}  CRZ-{{ cruise_profile }}   DSC-{{ data.general.descent_profile }}
{% if data.etops and data.etops.rule %}
ETOPS FLTPLN {{ data.etops.rule }} MINUTES
{% endif %}

FUEL         CORR      ENDUR

{{ "%06d"|format(data.fuel.enroute_burn|int) }}       .. ..     {{ trip_time }}    TRIPF INCL {{ fuelfact }}PCT HIGH CONS
{{ "%06d"|format(data.fuel.contingency|int) }}       .. ..     {{ cont_time }}    CONTINGENCY/RR
{{ "%06d"|format(data.fuel.reserve|int) }}       .. ..     {{ reserve_time }}    FINAL RESERVE FUEL
{% if alternates|length > 0 %}{{ "%06d"|format(data.fuel.alternate_burn|int) }}       .. ..     {{ alternates[0].burn_time_formatted }}    ALTN {{ alternates[0].icao_code }}{% endif %}
000000       .. ..     00:00    EXTRA HOLDING FUEL
{{ "%06d"|format(data.fuel.etops|default(0)|int) }}       .. ..     {{ etops_fuel_time }}    ADDITIONAL FUEL
{{ "%06d"|format(data.fuel.plan_takeoff|int) }}       .. ..     {{ endurance }}    REQ
000000       .. ..     00:00    TANKERING
000000       .. ..     00:00    DISCRETIONARY FUEL
{{ "%06d"|format(data.fuel.plan_takeoff|int) }}       .. ..     {{ endurance }}    TKOF
{{ "%06d"|format(data.fuel.taxi|int) }}       .. ..                 TAXI
{{ "%06d"|format(data.fuel.plan_ramp|int) }}       .. ..     {{ endurance }}    BLOCK  FUEL REM .. ..

                ARR  .. ..     TDN   .. ..
                DEP  .. ..     A/B   .. ..
                FLT  .. ..     AIR   .. ..

FBURN ADJUSTMENT FOR 1000KGS INCR/DECR IN TOW {{ "%04d"|format(data.impacts.zfw_plus_1000.burn_difference|default(0)|int) }}KGS/{{ "%04d"|format(data.impacts.zfw_minus_1000.burn_difference|default(0)|int|abs) }}KGS

FL SUMMARIES
CRZ          TOW      TRF         TIM      FL
{% if data.impacts.plus_2000ft %}
{{ cruise_profile }}       {{ "%06d"|format(data.weights.est_tow|int) }}   {{ "%06d"|format(data.impacts.plus_2000ft.enroute_burn|int) }}      {{ plus_2000_time }}     {{ data.impacts.plus_2000ft.initial_fl }}
{% endif %}
{{ cruise_profile }}       {{ "%06d"|format(data.weights.est_tow|int) }}   {{ "%06d"|format(data.fuel.enroute_burn|int) }}      {{ trip_time }}     {{ (data.general.initial_altitude|int // 100) }}
{% if data.impacts.minus_2000ft %}
{{ cruise_profile }}       {{ "%06d"|format(data.weights.est_tow|int) }}   {{ "%06d"|format(data.impacts.minus_2000ft.enroute_burn|int) }}      {{ minus_2000_time }}     {{ data.impacts.minus_2000ft.initial_fl }}
{% endif %}

FLT NBR {{ callsign }}   DTE {{ date_slash }}

 EZF       PLD        ELW       ETW     CRZ
{{ "%06d"|format(data.weights.est_zfw|int) }}    {{ "%06d"|format(data.weights.payload|int) }}     {{ "%06d"|format(data.weights.est_ldw|int) }}    {{ "%06d"|format(data.weights.est_tow|int) }}   {{ cruise_profile }}

{% if data.etops and data.etops.suitable_airport %}
ENRT ALTN SUITABLE
{% for airport in suitable_airports %}
{{ airport.icao_code }} VALIDITY WINDOW {{ airport.suitability_start[11:16] }}Z TO {{ airport.suitability_end[11:16] }}Z
{% endfor %}

-E.ENT {{ etops_entry_coord }}  0000 NM {{ etops_entry_time }}    {{ data.etops.entry.icao_code }}  {{ etops_entry_apt_coord }}
-E.EXT {{ etops_exit_coord }}  0000 NM {{ etops_exit_time }}    {{ data.etops.exit.icao_code }}  {{ etops_exit_apt_coord }}

MOST CRITICAL FUEL SCENARIO AT : {{ critical_cp }} FUEL DEFICIT OF {{ deficit }} KGS

                                                         TIME TO
                   DIST        W/C    CFR   FOB    EXC   ETP/ALT
{% for etp in equal_time_points %}
ETP{{ loop.index }} {{ etp.da1_code }}/{{ etp.da2_code }}   {{ "%04d"|format(etp.da1_dist|int) }}/{{ "%04d"|format(etp.da2_dist|int) }}  {{ etp.da1_wc }}/{{ etp.da2_wc }} {{ "%05d"|format(etp.critical_fuel|int) }} {{ "%06d"|format(etp.est_fob|int) }} {{ "%05d"|format(etp.excess|int) }} {{ etp.elapsed_time_formatted }}/{{ etp.div_time_formatted }}
     {{ etp.coord_string }}
{% endfor %}
{% endif %}

XTO TIM   AWY     WPT/FRQ      TTK   DIS  TAS  FLV   TD /TP   FBO    PFRM
ATO TIM      COORD             MTK   TTL  G/S  GMA   WIND     ABO    AFRM

        {{ data.origin.icao_code }}                                                       {{ "%05d"|format(data.fuel.taxi|int) }}  {{ "%06d"|format(data.fuel.plan_takeoff|int) }}
        ELEV {{ origin_elevation }} FT
        {{ origin_latlon }}
</pre>
<div>
{% for row in navlog_rows %}
{{ row|safe }}
{% endfor %}
</div>
<pre>
{{ sec18_firs }}
TRACK USED = -OPT

G/C DIST {{ data.origin.icao_code }}/{{ data.destination.icao_code }}  {{ data.general.gc_distance }} NM

ROUTE DIST {{ data.general.route_distance }}NM

MAX FL / AVG.TAS  FL390 / {{ data.general.cruise_tas|int }}

AVG COMP P{{ "%03d"|format(data.general.avg_comp_wind|int|abs) }}

         GMA  DIST  TTK  W/C   FL   TIME   FUEL BOF
{% for alt in alternates %}
{{ alt.icao_code }}          {{ "%04d"|format(alt.distance|int) }}  {{ "%03d"|format(alt.track_true|int) }}  {{ alt.avg_wind_comp }}  {{ (alt.cruise_altitude|int // 100) }}  {{ alt.ete_formatted }}  {{ "%06d"|format(alt.burn|int) }}
         {{ data.destination.icao_code }} {{ alt.route_ifps }} {{ alt.icao_code }}
{% endfor %}

                             ALTERNATE DATA

XTO TIM   AWY     WPT/FRQ      TTK   DIS  TAS  FLV   TD /TP   FBO    PFRM
ATO TIM      COORD             MTK   TTL  G/S  GMA   WIND     ABO    AFRM
{% if navlog_alt1 and navlog_alt1.fix %}
{% for r in alt_navlog_rows %}
{{ r|safe }}
{% endfor %}
{% endif %}

CLIMB
         FL100     FL180     FL240     FL300     FL340     FL390     FL450
{% for r in climb_matrix %}{{ r }}
{% endfor %}
CRUISE
         FL100     FL180     FL240     FL300     FL340     FL390     FL450
{% for r in cruise_matrix %}{{ r }}
{% endfor %}
DESCENT
         FL100     FL180     FL240     FL300     FL340     FL390     FL450
{% for r in descent_matrix %}{{ r }}
{% endfor %}

I CERTIFY THAT HAVE SATISFIED MYSELF THAT ALL FACTORS WHICH FORM THE BASIS OF
FLIGHT PREPARATION ARE IN ACCORDANCE WITH THE PERTINENT REGULATIONS LAID DOWN
BY THE INDONESIAN CIVIL AVIATION, CAPTAIN {{ data.crew.cpt|upper }}

PIC             : CAPTAIN {{ data.crew.cpt|upper }}

SIGN            : .. .. .. .. .. ..

PREPARED BY     : FOO. {{ data.crew.dx|upper }} - {{ fooId }}

CAPTAINS SIGNATURE FOR COMPLETION OF JOURNAL AFTER FLIGHT

                                      .. .. .. .. ..

{{ atc_flightplan_text }}

{% if data.etops and data.etops.equal_time_point %}
{% for etp_a in etp_analysis_blocks %}
- - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -
2D
ETP {{ etp_a.coord_short }}
TO ETP BURN {{ etp_a.burn_to }}
       TIME  {{ etp_a.time_to }}
       DIST   0000
       ETP AIRPORTS
       {{ etp_a.apt1 }}    {{ etp_a.apt2 }}
TIME   {{ etp_a.div_time }}   {{ etp_a.div_time }}
RQFUEL {{ etp_a.rq_fuel }}  {{ etp_a.rq_fuel }}
FL     {{ etp_a.fl }}   {{ etp_a.fl }}
DIST   {{ etp_a.dist1 }}    {{ etp_a.dist2 }}
WIND   {{ etp_a.wind1 }}    {{ etp_a.wind2 }}
- - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -
{% endfor %}
{% endif %}

END OF NAVTECH DATAPLAN
REQUEST NO. {{ req_id_short }} / REV NBR {{ data.general.release }}
</pre>

    <div class="landscape-section">
        <div class="notam-header-landscape">
            NOTAM BRIEFING<br>
            {{ callsign }} - {{ flight_date }}
        </div>
        <div class="notam-columns">
        {% for group in notam_groups %}
            {% if group.notams %}
            <div class="notam-group">
                <div class="notam-group-header">{{ group.title }}</div>
                {% for n in group.notams %}
                <div class="notam-item">
                    <b>{{ n.id }}</b><br>
                    E) {{ n.text }}
                </div>
                {% endfor %}
            </div>
            {% endif %}
        {% endfor %}
        </div>
    </div>

    <div class="page-break"></div>

    <div class="wx-header">
        THE FOLLOWING ARE EXTRACT FROM:<br>
        BADAN METEOROLOGI, KLIMATOLOGI, DAN GEOFISIKA<br>
        BIDANG METEOROLOGI PENERBANGAN<br>
        WHICH MAY EFFECT TO THE OPERATION OF FLIGHT<br><br>
        WEATHER BRIEFING
    </div>
    <hr style="border-top: 1px solid #000; margin-bottom: 20px;">

    {% for wx in weather_info %}
    <div class="wx-section">
        <div class="wx-airport-title">{{ wx.title }}</div>
        <div class="wx-data">{{ wx.data }}</div>
    </div>
    {% endfor %}

    {% if map_images %}
    {% for map in map_images %}
    <div class="page-break"></div>
    <div class="landscape-section">
        <div style="text-align: center; font-weight: bold; font-size: 14pt; margin-bottom: 15px;">
            FLIGHT MAPS<br>
            {{ callsign }} - {{ flight_date }}
        </div>
        <div class="map-container">
            <div class="map-title">{{ map.name }}</div>
            <img src="{{ map.url }}" class="map-image">
        </div>
    </div>
    {% endfor %}
    {% endif %}

</body>
</html>
"""

# ---------------------------------------------------------
# STEAMLIT INTERFACE CONTROL LAYER
# ---------------------------------------------------------
st.title("✈️ Garuda Indonesia EFB - PDF Briefing Generator")

# Sniff browser query parameters natively
query_params = st.query_params
url_username = query_params.get("username", "").strip()

if not url_username:
    st.warning("⚠️ No dashboard username context provided. Please manually key in below:")
    input_username = st.text_input("SimBrief Username:", value="").strip()
    target_username = input_username
else:
    target_username = url_username
    st.info(f"🔄 Active EFB Session Context Synced: **{target_username}**")

if target_username:
    with st.spinner("🔄 Downloading active operational payload from SimBrief core network..."):
        try:
            url = f"https://www.simbrief.com/api/xml.fetcher.php?username={requests.utils.quote(target_username)}&json=v2"
            res = requests.get(url, timeout=15)
            if res.status_code != 200:
                st.error("❌ Failed to query SimBrief network layer. Verify your internet connection.")
                st.stop()
            
            raw_json = res.json()
            if "general" not in raw_json:
                st.error("❌ Invalid response object returned. Check if this username has generated an active flight plan first.")
                st.stop()
                
            data_obj = dict_to_obj(raw_json)
        except Exception as e:
            st.error(f"❌ Network Parsing Refused: {e}")
            st.stop()

    # Process structured metadata properties
# Process structured metadata properties safely whether they are strings or integers
    sched_out = raw_json['times']['sched_out'] 
    callsign = raw_json['atc'].get('callsign', f"GIA{raw_json['general']['flight_number']}")
    
    flight_date = php_date('Y-m-d', sched_out)
    brief_d_m_y = php_date('d-m-y', sched_out)
    brief_H_i = php_date('Hi', sched_out)
    origin_icao = raw_json['origin']['icao_code']
    gia805MetaHeader = f"BRIEFING TEXT {callsign.upper()}-{brief_d_m_y}-{brief_H_i}-{origin_icao.upper()}"
    
    airport_info = []
    weather_info = []
    alternates_list = []
    
    if 'alternate' in raw_json:
        raw_alts = raw_json['alternate']
        if isinstance(raw_alts, dict): raw_alts = [raw_alts]
        for a in raw_alts:
            a_obj = dict_to_obj(a)
            a_obj.ete_formatted = php_date('H.i', a.get('ete', 0))
            a_obj.burn_time_formatted = php_date('H:i', a.get('burn', 0))
            alternates_list.append(a_obj)

    # Compile Departure/Arrival nodes
    airport_info.append({
        'icao': raw_json['origin']['icao_code'], 'iata': raw_json['origin'].get('iata_code', '---'),
        'time': f"{php_date('Hi', raw_json['times']['sched_out'])}Z", 'notams': get_filtered_notams(raw_json['origin'].get('notam', [])),
        'taf': raw_json['origin'].get('taf', 'N/A')
    })
    weather_info.append({
        'title': f"DEPARTURE AIRPORT : {raw_json['origin']['icao_code']}",
        'data': f"{raw_json['origin'].get('taf', '')}\n\n{raw_json['origin'].get('metar', '')}"
    })
    
    airport_info.append({
        'icao': raw_json['destination']['icao_code'], 'iata': raw_json['destination'].get('iata_code', '---'),
        'time': f"{php_date('Hi', raw_json['times']['est_in'])}Z", 'notams': get_filtered_notams(raw_json['destination'].get('notam', [])),
        'taf': raw_json['destination'].get('taf', 'N/A')
    })
    weather_info.append({
        'title': f"DESTINATION AIRPORT : {raw_json['destination']['icao_code']}",
        'data': f"{raw_json['destination'].get('taf', '')}\n\n{raw_json['destination'].get('metar', '')}"
    })
    
    for alt in alternates_list:
        airport_info.append({
            'icao': alt.icao_code, 'iata': alt.iata_code, 'time': '....',
            'notams': get_filtered_notams(alt.get('notam', [])), 'taf': alt.get('taf', 'N/A')
        })
        weather_info.append({
            'title': f"DESTINATION ALTERNATE AIRPORT : {alt.icao_code}",
            'data': f"{alt.get('taf', '')}\n\n{alt.get('metar', '')}"
        })

    # Global landscape NOTAM array layout maps
    notam_groups = [
        {'title': f"DEPARTURE AIRPORT : {raw_json['origin']['icao_code']}", 'notams': get_filtered_notams(raw_json['origin'].get('notam', []), 25)},
        {'title': f"DESTINATION AIRPORT : {raw_json['destination']['icao_code']}", 'notams': get_filtered_notams(raw_json['destination'].get('notam', []), 25)}
    ]
    
    # Process Flight Log (Navlog) Data Rows
# Process Flight Log (Navlog) Data Rows safely
    navlog_rows = []
    total_dist_cum = 0
    
    # Check if navlog is already a direct list of fixes, or a dict containing 'fix'
    navlog_data = raw_json.get('navlog', [])
    if isinstance(navlog_data, list):
        fixes = navlog_data
    elif isinstance(navlog_data, dict):
        fixes = navlog_data.get('fix', [])
    else:
        fixes = []
        
    if isinstance(fixes, dict): 
        fixes = [fixes]
    
    for f in fixes:
        total_dist_cum += int(f.get('distance', 0))
        stage_lbl = 'CLB ' if f.get('stage', '') == 'CLB' else f"{int(f.get('altitude_feet', 0)) // 100:03d} "
        
        if 'fir_crossing' in f and 'fir' in f['fir_crossing']:
            firs = f['fir_crossing']['fir']
            if isinstance(firs, dict): firs = [firs]
            for fir in firs:
                r1 = f'<div class="nav-row">0000  {f.get("via_airway", ""):<6} FIR/{fir.get("fir_icao", ""):<10}  {f.get("track_true", "")}T  000  {int(f.get("true_airspeed", 0)):03d}  {stage_lbl} ISA     {int(f.get("fuel_totalused", 0)):05d}  {int(f.get("fuel_plan_onboard", 0)):06d}</div>'
                r2 = f'<div class="nav-row">{php_date("Hi", f.get("time_leg", 0))}    {format_latlon(fir.get("pos_lat_entry", 0), fir.get("pos_long_entry", 0))}    {f.get("track_mag", "")}M  0000 {int(f.get("groundspeed", 0)):03d}  {int(f.get("mora", 0)) // 100:03d}   {f.get("wind_dir", 0)}{int(f.get("wind_spd", 0)):03d}</div>'
                navlog_rows.extend([r1, r2])
                
        r3 = f'<div class="nav-row">{php_date("Hi", f.get("time_leg", 0))}  {f.get("via_airway", ""):<6} {f.get("ident", ""):<12}  {f.get("track_true", "")}T  {int(f.get("distance", 0)):03d}  {int(f.get("true_airspeed", 0)):03d}  {stage_lbl} ISA     {int(f.get("fuel_totalused", 0)):05d}  {int(f.get("fuel_plan_onboard", 0)):06d}</div>'
        r4 = f'<div class="nav-row">{php_date("Hi", f.get("time_total", 0))}    {format_latlon(f.get("pos_lat", 0), f.get("pos_long", 0))}    {f.get("track_mag", "")}M  {total_dist_cum:04d} {int(f.get("groundspeed", 0)):03d}  {int(f.get("mora", 0)) // 100:03d}   {f.get("wind_dir", 0)}{int(f.get("wind_spd", 0)):03d}</div>'
        navlog_rows.extend([r3, r4])

    # Dynamic Profile Matrices
    climb_matrix = [format_wind_matrix_row(fx) for fx in fixes if fx.get('stage') == 'CLB' and fx.get('ident') != 'TOC']
    cruise_matrix = [format_wind_matrix_row(fx) for fx in fixes if fx.get('stage') == 'CRZ' and fx.get('ident') != 'TOD']
    descent_matrix = [format_wind_matrix_row(fx) for fx in fixes if fx.get('stage') == 'DSC']

    # Map Charts Parsing
    map_images = []
    if 'images' in raw_json and 'map' in raw_json['images']:
        base_dir = raw_json['images'].get('directory', '')
        maps = raw_json['images']['map']
        if isinstance(maps, dict): maps = [maps]
        for m in maps:
            map_images.append({'name': m.get('name', 'Enroute Chart'), 'url': base_dir + m.get('link', '')})

    # ETOPS Validation Logic Block
    etops_str, etops_entry_coord, etops_entry_time, etops_entry_apt_coord = "...", "", "", ""
    etops_exit_coord, etops_exit_time, etops_exit_apt_coord = "", "", ""
    critical_cp, deficit = "CP1", 0
    suitable_airports, equal_time_points, etp_analysis_blocks = [], [], []
    
    if 'etops' in raw_json and isinstance(raw_json['etops'], dict):
        etops_raw = raw_json['etops']
        if 'suitable_airport' in etops_raw:
            s_apts = etops_raw['suitable_airport']
            if isinstance(s_apts, dict): s_apts = [s_apts]
            suitable_airports = s_apts
            etops_str = " ".join([sa.get('icao_code', '') for sa in s_apts])
            
        if 'entry' in etops_raw:
            etops_entry_coord = format_latlon(etops_raw['entry'].get('pos_lat_fix', 0), etops_raw['entry'].get('pos_long_fix', 0)).replace('.', '')
            etops_entry_time = php_date('H:i', etops_raw['entry'].get('elapsed_time', 0))
            etops_entry_apt_coord = format_latlon(etops_raw['entry'].get('pos_lat_apt', 0), etops_raw['entry'].get('pos_long_apt', 0)).replace('.', '')
            
        if 'exit' in etops_raw:
            etops_exit_coord = format_latlon(etops_raw['exit'].get('pos_lat_fix', 0), etops_raw['exit'].get('pos_long_fix', 0)).replace('.', '')
            etops_exit_time = php_date('H:i', etops_raw['exit'].get('elapsed_time', 0))
            etops_exit_apt_coord = format_latlon(etops_raw['exit'].get('pos_lat_apt', 0), etops_raw['exit'].get('pos_long_apt', 0)).replace('.', '')

        if 'critical_point' in etops_raw:
            fob = float(etops_raw['critical_point'].get('est_fob', 0))
            crit_f = float(etops_raw['critical_point'].get('critical_fuel', 0))
            deficit = max(0, int(crit_f - fob))
            
        if 'equal_time_point' in etops_raw:
            etps_raw = etops_raw['equal_time_point']
            if isinstance(etps_raw, dict): etps_raw = [etps_raw]
            for idx, etp in enumerate(etps_raw):
                da1 = etp['div_airport'][0] if isinstance(etp['div_airport'], list) else etp['div_airport']
                da2 = etp['div_airport'][1] if isinstance(etp['div_airport'], list) and len(etp['div_airport']) > 1 else da1
                
                fob_val = float(etp.get('est_fob', 0))
                cf_val = float(etp.get('critical_fuel', 0))
                
                equal_time_points.append({
                    'da1_code': da1.get('icao_code'), 'da2_code': da2.get('icao_code'),
                    'da1_dist': da1.get('distance'), 'da2_dist': da2.get('distance'),
                    'da1_wc': format_wind_comp(da1.get('avg_wind_comp', 0)), 'da2_wc': format_wind_comp(da2.get('avg_wind_comp', 0)),
                    'critical_fuel': cf_val, 'est_fob': fob_val, 'excess': max(0, int(fob_val - cf_val)),
                    'elapsed_time_formatted': php_date('Hi', etp.get('elapsed_time')), 'div_time_formatted': php_date('Hi', etp.get('div_time')),
                    'coord_string': format_latlon(etp.get('pos_lat'), etp.get('pos_long'))
                })
                
                etp_analysis_blocks.append({
                    'coord_short': format_latlon(etp.get('pos_lat'), etp.get('pos_long')).replace('.', ''),
                    'burn_to': f"{int(raw_json['fuel']['plan_takeoff']) - int(fob_val):06d}",
                    'time_to': php_date('H.i', etp.get('elapsed_time')),
                    'apt1': da1.get('icao_code'), 'apt2': da2.get('icao_code'),
                    'div_time': php_date('H.i', etp.get('div_time')),
                    'rq_fuel': f"{int(etp.get('div_burn', 0)):06d}",
                    'fl': f"{int(etp.get('div_altitude', 0)) // 100}",
                    'dist1': f"{int(da1.get('distance', 0)):04d}", 'dist2': f"{int(da2.get('distance', 0)):04d}",
                    'wind1': format_wind_comp(da1.get('avg_wind_comp', 0)), 'wind2': format_wind_comp(da2.get('avg_wind_comp', 0))
                })

    try:
        req_id = raw_json['params']['request_id']
        fooId = 1000 + (hash(req_id) % 9000)
    except:
        fooId = 3241

    # Render metrics cleanly inside the Jinja structural template context engine
    template_env = Environment(loader=BaseLoader())
    template = template_env.from_string(TEMPLATE_STR)
    
    rendered_html = template.render(
        data=data_obj,
        gia805MetaHeader=gia805MetaHeader,
        callsign=callsign.upper(),
        flight_date=flight_date,
        avg_wind_comp=format_wind_comp(raw_json['general'].get('avg_wind_comp', 0)),
        airport_info=airport_info,
        weather_info=weather_info,
        alternates=alternates_list,
        valid_ui=php_date('Hi', sched_out + 21600),
        req_id_short=str(raw_json['params']['request_id'])[-5:],
        date_dMy=php_date('dMy', sched_out),
        etd_z=php_date('Hi', sched_out),
        eta_z=php_date('Hi', raw_json['times']['est_in']),
        block_ft=php_date('Hi', raw_json['times']['est_block']),
        trip_time=php_date('H:i', raw_json['times']['est_time_enroute']),
        reserve_time=php_date('H:i', raw_json['times']['reserve_time']),
        endurance=php_date('H:i', raw_json['times']['endurance']),
        etops_str=etops_str,
        fooId=fooId,
        cruise_profile=format_cruise_profile(raw_json['general'].get('cruise_profile'), raw_json['general'].get('costindex')),
        date_slash=php_date('d/m/y', sched_out),
        time_generated=php_date('Hi', raw_json['params']['time_generated']),
        prog_times=get_weather_prognosis_times(sched_out),
        fuelfact=format_fuelfact(raw_json['aircraft'].get('fuelfact', 1.0)),
        cont_time=php_date('H:i', raw_json['times'].get('contfuel_time', 0)),
        etops_fuel_time=php_date('H:i', raw_json['times'].get('etopsfuel_time', 0)),
        plus_2000_time=php_date('H:i', raw_json['impacts'].get('plus_2000ft', {}).get('time_enroute', 0)),
        minus_2000_time=php_date('H:i', raw_json['impacts'].get('minus_2000ft', {}).get('time_enroute', 0)),
        suitable_airports=suitable_airports,
        etops_entry_coord=etops_entry_coord, etops_entry_time=etops_entry_time, etops_entry_apt_coord=etops_entry_apt_coord,
        etops_exit_coord=etops_exit_coord, etops_exit_time=etops_exit_time, etops_exit_apt_coord=etops_exit_apt_coord,
        critical_cp=critical_cp, deficit=deficit, equal_time_points=equal_time_points,
        origin_elevation=format_elevation(raw_json['origin'].get('elevation', 0)),
        origin_latlon=format_latlon(raw_json['origin']['pos_lat'], raw_json['origin']['pos_long']),
        navlog_rows=navlog_rows,
        sec18_firs=get_formatted_fir(raw_json['atc'].get('section18', '')),
        alt_navlog_rows=[f'<div class="nav-row">{php_date("Hi", fx.get("time_leg"))}  {fx.get("via_airway", ""):<6} {fx.get("ident", ""):<12}  {fx.get("track_true")}T  {int(fx.get("distance", 0)):03d}  {int(fx.get("true_airspeed", 0)):03d}  CRZ  ISA     {int(fx.get("fuel_totalused", 0)):05d}  {int(fx.get("fuel_plan_onboard", 0)):06d}</div>' for fx in raw_json.get('alternate_navlog', {}).get('fix', [])] if 'alternate_navlog' in raw_json and 'fix' in raw_json['alternate_navlog'] else [],
        climb_matrix=climb_matrix, cruise_matrix=cruise_matrix, descent_matrix=descent_matrix,
        atc_flightplan_text=raw_json['atc'].get('flightplan_text', ''),
        etp_analysis_blocks=etp_analysis_blocks,
        notam_groups=notam_groups,
        map_images=map_images
    )

    # Output to virtual binary stream using WeasyPrint
    pdf_buffer = io.BytesIO()
    HTML(string=rendered_html).write_pdf(pdf_buffer)
    pdf_bytes = pdf_buffer.getvalue()
    
    st.success("✅ Operational Briefing compiled successfully!")
    
    # Download Action Triggers
    pdf_filename = f"{callsign.upper()}_Briefing_Final.pdf"
    st.download_button(
        label=f"📥 Download Briefing Package ({pdf_filename})",
        data=pdf_bytes,
        file_name=pdf_filename,
        mime="application/pdf",
        type="primary"
    )
