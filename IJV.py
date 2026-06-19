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
# KONFIGURASI HALAMAN (Harus dipanggil paling awal)
# ---------------------------------------------------------
st.set_page_config(page_title="IJV Crew Portal", page_icon="IJV.png", layout="wide", initial_sidebar_state="collapsed")

# ---------------------------------------------------------
# KELAS & FUNGSI HELPER
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

# Helper render gambar lokal ke base64 (Aman dari multiline issue)
def get_image_base64(path):
    if os.path.exists(path):
        with open(path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode("utf-8").replace('\n', '')
    return ""

# ---------------------------------------------------------
# SISTEM LOGIN (UI/UX CUSTOM)
# ---------------------------------------------------------
if 'logged_in' not in st.session_state:
    st.session_state['logged_in'] = False

def login_page():
    bg_base64 = get_image_base64("bg.png")
    ijv_logo_base64 = get_image_base64("IJV.png")
    
    st.markdown(f"""
    <style>
        /* 1. Paksa halaman penuh tanpa margin/padding bawaan Streamlit */
        .appview-container .main .block-container {{
            padding: 0rem !important;
            max-width: 100% !important;
            overflow: hidden !important;
        }}
        header[data-testid="stHeader"] {{ visibility: hidden !important; height: 0 !important; display: none !important; }}
        footer {{ visibility: hidden !important; display: none !important; }}
        
        /* 2. Menghilangkan Gap Antar Kolom */
        [data-testid="stHorizontalBlock"] {{
            gap: 0rem !important;
            height: 100vh !important;
            align-items: stretch !important;
        }}
        
        /* 3. KOLOM KIRI (Form Putih) - Menggunakan 2 selektor untuk semua versi Streamlit */
        [data-testid="column"]:nth-of-type(1), [data-testid="stColumn"]:nth-of-type(1) {{
            background-color: #FFFFFF !important;
            padding: 5% 4% !important;
            display: flex !important;
            flex-direction: column !important;
            justify-content: center !important;
            height: 100vh !important;
            z-index: 10;
            box-shadow: 2px 0 15px rgba(0,0,0,0.1);
        }}
        
        /* 4. KOLOM KANAN (Gambar Latar Belakang) */
        [data-testid="column"]:nth-of-type(2), [data-testid="stColumn"]:nth-of-type(2) {{
            background-image: url('data:image/png;base64,{bg_base64}') !important;
            background-size: cover !important;
            background-position: center !important;
            background-repeat: no-repeat !important;
            background-color: #02203c !important; /* Biru Dongker jika gambar lambat/gagal muat */
            height: 100vh !important;
            min-height: 100vh !important; /* Paksa minimal tinggi 100vh */
            padding: 0 !important;
        }}

        /* 5. Membersihkan Form Bawaan Streamlit */
        [data-testid="stForm"] {{
            border: none !important;
            padding: 0 !important;
            background-color: transparent !important;
        }}

        /* 6. Kotak Input Mirip Web Asli */
        .stTextInput input {{
            border: 1px solid #e0e0e0 !important;
            border-radius: 5px !important;
            padding: 0.6rem !important;
            font-size: 14px !important;
        }}
        
        /* 7. Paksa Tombol Log In Menjadi Full Width dan Biru */
        [data-testid="stFormSubmitButton"] button, .stButton button {{
            background-color: #2196F3 !important;
            color: white !important;
            border: none !important;
            border-radius: 5px !important;
            font-weight: bold !important;
            width: 100% !important;
            padding: 0.6rem !important;
            transition: background-color 0.3s;
        }}
        [data-testid="stFormSubmitButton"] button:hover, .stButton button:hover {{
            background-color: #1976D2 !important;
        }}
    </style>
    """, unsafe_allow_html=True)

    # Proporsi kolom: Kiri 1, Kanan 2.5 (Agar lebar gambar dominan seperti contoh)
    col1, col2 = st.columns([1, 2.5])
    
    with col1:
        # Spacer Atas
        st.markdown("<div style='height: 10vh;'></div>", unsafe_allow_html=True)
        
        # LOGO IJV & TULISAN CREW PORTAL
        if ijv_logo_base64:
            st.markdown(f"""
            <div style="text-align: center; margin-bottom: 2rem;">
                <img src="data:image/png;base64,{ijv_logo_base64}" style="max-width: 180px;"><br>
                <div style="font-family: Arial, sans-serif; font-size: 24px; font-weight: bold; color: #333; margin-top: 10px;">Crew Portal</div>
            </div>
            """, unsafe_allow_html=True)
        else:
            st.markdown("<h1 style='text-align: center; color: #2196F3; margin-bottom: 2rem;'>IJV Crew Portal</h1>", unsafe_allow_html=True)
        
        # FORM LOGIN
        with st.form("login_form"):
            username = st.text_input("Crew ID", placeholder="Crew ID", label_visibility="collapsed")
            password = st.text_input("Password", type="password", placeholder="Password", label_visibility="collapsed")
            submit = st.form_submit_button("Log in")
            
            if submit:
                if password == "IJV1" and username != "":
                    st.session_state['logged_in'] = True
                    st.session_state['username'] = username
                    st.rerun()
                else:
                    st.error("Crew ID atau Password tidak valid!")
        
        # Tulisan Lupa Password
        st.markdown("<div style='text-align: right; margin-top: 5px;'><a href='#' style='color: #2196F3; text-decoration: none; font-size: 13px; font-family: Arial, sans-serif;'>Forgot password?</a></div>", unsafe_allow_html=True)
        
        # Spacer Bawah
        st.markdown("<div style='height: 25vh;'></div>", unsafe_allow_html=True)
        
        # Footer Web di Kiri Bawah
        st.markdown("<div style='text-align: center; font-size: 11px; color: #888; font-family: Arial, sans-serif;'>www.indonesiajourneyvirtual.org</div>", unsafe_allow_html=True)

    with col2:
        # PENTING: Jangan gunakan st.empty(). Beri div raksasa tak terlihat agar Streamlit menarik kolomnya selebar 100vh!
        st.markdown("<div style='height: 100vh; width: 100%;'></div>", unsafe_allow_html=True)

# ---------------------------------------------------------
# DASHBOARD (GENERATOR OFP)
# ---------------------------------------------------------
def dashboard():
    # Mengembalikan padding untuk halaman Dashboard agar rapi kembali
    st.markdown("""
    <style>
        .appview-container .main .block-container { padding: 3rem 5rem !important; } 
        header[data-testid="stHeader"] { visibility: visible !important; }
    </style>
    """, unsafe_allow_html=True)
    
    st.sidebar.title(f"Welcome, {st.session_state.get('username', 'Crew')}")
    if st.sidebar.button("Logout"):
        st.session_state['logged_in'] = False
        st.rerun()

    st.title("OFP & Briefing Package Generator")
    
    # Membaca Logo untuk Template PDF (FDCGA.png)
    logo_path = "IJV.png"
    logo_base64 = get_image_base64(logo_path)

    # 1. Grab parameter from URL
    url_username = st.query_params.get("username", "")
    
    # 2. Swap field to read Username and assign the URL parameter as its default value
    sb_userid = st.text_input("Enter SimBrief Username:", value=url_username)
    
    # 3. Create the layout button
    trigger_generation = st.button("Generate OFP PDF")
    
    # 4. AUTO-RUN SWITCH: If a URL user is detected and we haven't processed them yet, force-trigger execution
    if url_username and "auto_run_executed" not in st.session_state:
        st.session_state["auto_run_executed"] = True
        trigger_generation = True

    if trigger_generation:
            
        with st.spinner(f"⏳ Mengunduh data dari SimBrief (Username: {sb_userid})..."):
            # Update 'userid=' menjadi 'username=' agar API SimBrief mencocokkan string dengan benar
            sb_url = f"https://www.simbrief.com/api/xml.fetcher.php?username={sb_userid}&json=1"
            try:
                response = requests.get(sb_url, timeout=15)
                response.raise_for_status()
                data_json = response.json()
                
                if 'fetch' in data_json and data_json['fetch']['status'] != 'Success':
                    st.error(f"⚠️ Peringatan SimBrief: {data_json['fetch']['status']}")
                    return
            except Exception as e:
                st.error(f"❌ Gagal mengunduh data: {e}")
                return

        with st.spinner("⚙️ Generating..."):
            try:
                # 3. DATA PREPARATION
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

                # Origin
                try:
                    t_val = php_date('Hi', data_obj.times.sched_out) + "Z"
                    airport_info.append({'icao': data_obj.origin.icao_code, 'iata': data_obj.origin.iata_code, 'label': 'STD', 'time': t_val, 'notams': get_filtered_notams(data_obj.origin.get('notam')), 'taf': data_obj.origin.get('taf', 'N/A')})
                    weather_info.append({'title': f"DEPARTURE AIRPORT : {data_obj.origin.icao_code}", 'data': (data_obj.origin.get('taf', '') or "") + "\n" + (data_obj.origin.get('metar', '') or "")})
                except: pass

                # Destination
                try:
                    t_val = php_date('Hi', data_obj.times.est_in) + "Z"
                    airport_info.append({'icao': data_obj.destination.icao_code, 'iata': data_obj.destination.iata_code, 'label': 'ETA', 'time': t_val, 'notams': get_filtered_notams(data_obj.destination.get('notam')), 'taf': data_obj.destination.get('taf', 'N/A')})
                    weather_info.append({'title': f"DESTINATION AIRPORT : {data_obj.destination.icao_code}", 'data': (data_obj.destination.get('taf', '') or "") + "\n" + (data_obj.destination.get('metar', '') or "")})
                except: pass

                # Alternates
                for alt in alternates_list:
                    try: t_val = php_date('Hi', int(data_obj.times.est_in) + int(alt.ete)) + "Z"
                    except: t_val = "...."
                    airport_info.append({'icao': alt.icao_code, 'iata': alt.iata_code, 'label': 'ETA (ALTN)', 'time': t_val, 'notams': get_filtered_notams(alt.get('notam')), 'taf': alt.get('taf', 'N/A')})
                    weather_info.append({'title': f"DESTINATION ALTERNATE AIRPORT : {alt.icao_code}", 'data': (alt.get('taf', '') or "") + "\n" + (alt.get('metar', '') or "")})

                # ETOPS
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

                # Notams
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
                
                # ==========================================
                # FULL TEMPLATE STRING DENGAN DESAIN BARU
                # ==========================================
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

        /* HEADER & FOOTER UPDATED UNTUK FORMAT BARU */
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
            background-color: #ADD8E6; /* Light Blue */
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

        /* Footer Box */
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

        /* Landscape Section (NOTAM & Maps) */
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

        /* WEATHER PAGE STYLES */
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

        /* MAP Styles */
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

        .nav-row { white-space: pre-wrap; page-break-inside: avoid; display: block; }
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
   APU       : SERVICEABLE
   HIL       : NIL

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
{{n.id}}
{{n.text}}
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
   TYPHOON      : NIL
   TURBULENCE   : LIGHT
   JETSTREAM    : PSE CHECK SIGWX
   CLOUDS       : PSE CHECK SIGWX
   WIND COMP.   : {{helper.formatAvgWindComp(data.general.avg_wind_comp)}}

5. EST PAYLOAD
   PAX          : {{data.weights.pax_count}}
   CARGO        : {{data.weights.cargo}}
   PAYLOAD      : {{data.weights.payload}} KGS
    </pre>

    <div class="footer-box">
        <b>Flight Dispatch Center</b><br>
        Operation Center Building 69th Floor | IJV Tower | Soekarno-Hatta International Airport<br>
        Cengkareng 19120, Indonesia<br>
        Office Phone: +62 21 267 1949, +62 21 1607 2001, +62 21 241 1970 | Fax: +62 21 178 1945<br>
        Email Address: contact@indonesiajourneyvirtual.org; flightdispatch@indonesiajourneyvirtual.org;<br>
        SITA Address: IJVOF
    </div>

    <div class="page-break"></div>

    <pre>---------------------------------------------------------------------------
                             DISPATCH RELEASE
---------------------------------------------------------------------------
VALID U/I {{php_date('Hi', (data.times.sched_out|int) + 21600)}}Z
REF PLAN {{data.params.request_id[-5:]}} / REV NBR {{data.general.release}}
{{data.atc.callsign}}   {{php_date('dMy',data.times.sched_out)}} ETD {{php_date('Hi',data.times.sched_out)}}Z  ETA {{php_date('Hi',data.times.est_in)}}Z / FT {{php_date('Hi',data.times.est_block)}} IFR {{data.aircraft.reg}}

1.  POD/POA : {{data.origin.icao_code}}/{{data.destination.icao_code}}

2.  INITIAL DESTINATION (FOR PLANNED RE-DISPATCH AS APPLICABLE):

3.  WX
    ORG {{data.origin.iata_code}}/{{data.origin.icao_code}}  CHECKED             {% if alternates|length > 0 %}AL1 {{alternates[0].iata_code}}/{{alternates[0].icao_code}}  CHECKED {% endif %}
    DES {{data.destination.iata_code}}/{{data.destination.icao_code}}  CHECKED             {% if alternates|length > 1 %}AL2 {{alternates[1].iata_code}}/{{alternates[1].icao_code}}  CHECKED {% endif %}

4.  NOTAM AND/OR AERONAUTICAL INFORMATION
    ALL NOTAMS SIGNIFICANT TO FLIGHT ARE CONSIDERED

5.  LOAD
    EST PAX ADL{{str_pad(data.weights.pax_count,3,'0','left')}}/CHD000/INF000          TOTAL  {{data.weights.pax_count}}
    EST CGO {{data.weights.cargo}} KGS
    EST PLD {{data.weights.payload}} KGS

6.  FLIGHT PLAN DATA
    TRP   {{str_pad(data.fuel.enroute_burn, 6, '0', 'left')}}   KGS {{php_date('H:i',data.times.est_time_enroute)}}         EZF    {{str_pad(data.weights.est_zfw, 6, '0', 'left')}}   MAX {{str_pad(data.weights.max_zfw, 6, '0', 'left')}}
    RES   {{str_pad(data.fuel.reserve, 6, '0', 'left')}}   KGS {{php_date('H:i',data.times.reserve_time)}}         ELW    {{str_pad(data.weights.est_ldw, 6, '0', 'left')}}   MAX {{str_pad(data.weights.max_ldw, 6, '0', 'left')}}
    {% if alternates|length > 0 %}ALT   {{str_pad(data.fuel.alternate_burn, 6, '0', 'left')}}   KGS {{php_date('H:i',alternates[0].burn)}}         ETW    {{str_pad(data.weights.est_tow, 6, '0', 'left')}}   MAX {{str_pad(data.weights.max_tow, 6, '0', 'left')}}{% endif %}
    BLK   {{str_pad(data.fuel.plan_ramp, 6, '0', 'left')}}   KGS {{php_date('H:i',data.times.endurance)}}

7.  ETOPS FLIGHT: {% if not data.etops or data.etops == '0' %} NO {% else %} YES     ETOPS DIVERSION TIME: {{data.etops.rule}} MIN {% endif %}

8.  ENROUTE / ETOPS ALTERNATE: {{ etops_str }}

9.  TAKE OFF ALTERNATE (IF REQUIRED) : ......

10. DESTINATION ALTERNATE: 1. {% if alternates|length > 0 %}{{alternates[0].icao_code}}{% else %}....{% endif %}   2. {% if alternates|length > 1 %}{{alternates[1].icao_code}}{% else %}....{% endif %}

11. FUEL REQ AFTER BRIEF: ............. KGS

    REASON FOR DISCRETIONARY FUEL :....................

12. NOTOC  / (DGR):

13. REMARKS: NONE

I HEREBY RELEASE THIS FLIGHT IN FULL COMPLIANCE WITH CIVIL AVIATION SAFETY
REGULATIONS AND OPERATION MANUAL PART A (OM-A)
    DISPATCHED BY               : FOO. {{data.crew.dx | upper}} - {{fooId}}

I HEREBY PREPARE AND ARRANGE THIS FLIGHT DISPATCH RELEASE ACCORDING TO THE
INSTRUCTION AND DATA PROVIDED BY PT. GARUDA INDONESIA (PERSERO) TBK.
    NAME / ID                   : .................. / ........

                                   SIGN ......................

I HEREBY ACCEPT THIS FLIGHT DISPATCH RELEASE WITH FULL ACKNOWLEDGEMENT.
    PILOT IN COMMAND            :  CAPT. {{data.crew.cpt | upper}}

                                   SIGN ......................</pre>

    <div class="page-break"></div>

    <pre>---------------------------------------------------------------------------
                        COMPUTERIZED FLIGHT PLAN
---------------------------------------------------------------------------
PLAN {{data.params.request_id[-5:]}} / REV NUM {{str_pad(data.general.release, 2, '0', 'left')}}       {{data.origin.icao_code}} TO {{data.destination.icao_code}}  {{data.aircraft.icaocode}}  {{helper.formatCruiseProfile(data.general.cruise_profile, data.general.costindex)}}/F  IFR  {{php_date('d/m/y',data.times.sched_out)}}
NONSTOP COMPUTED {{php_date('Hi',data.params.time_generated)}} ETD {{php_date('Hi',data.times.sched_out)}}Z PROGS {{helper.getWeatherPrognosisTimes(data.times.sched_out)}} {{data.aircraft.reg}} KGS

GARUDA INDONESIA CFP

SPD SKD   CLB-{{helper.formatClimbSpeedProfile(data.general.climb_profile)}}  CRZ-{{helper.formatCruiseProfile(data.general.cruise_profile, data.general.costindex)}}   DSC-{{helper.formatDescendSpeedProfile(data.general.descent_profile)}}
{% if data.etops and data.etops is mapping %}
ETOPS FLTPLN {{data.etops.rule}} MINUTES
{% endif %}

FUEL         CORR      ENDUR

{{str_pad(data.fuel.enroute_burn, 6, '0', 'left')}}       .. ..     {{php_date('H:i',data.times.est_time_enroute)}}    TRIPF INCL {{helper.formatPerfPerfFactor(data.aircraft.fuelfact)}}PCT HIGH CONS
{{str_pad(data.fuel.contingency, 6, '0', 'left')}}       .. ..     {{php_date('H:i',data.times.contfuel_time)}}    CONTINGENCY/RR
{{str_pad(data.fuel.reserve, 6, '0', 'left')}}       .. ..     {{php_date('H:i',data.times.reserve_time)}}    FINAL RESERVE FUEL
{% if alternates|length > 0 %}{{str_pad(data.fuel.alternate_burn, 6, '0', 'left')}}       .. ..     {{php_date('H:i',alternates[0].burn)}}    ALTN {{alternates[0].icao_code}}{% endif %}
{{str_pad(helper.getFuelBucketFuelValue(data.fuel_extra, 'ATC'), 6, '0', 'left')}}       .. ..     {{php_date('H:i',helper.getFuelBucketFuelTime(data.fuel_extra, 'ATC'))}}    EXTRA HOLDING FUEL
{{str_pad(helper.getFuelBucketFuelValue(data.fuel_extra, 'WXX') + (data.fuel.etops|int), 6, '0', 'left')}}       .. ..     {{php_date('H:i',helper.getFuelBucketFuelTime(data.fuel_extra, 'WXX') + (data.times.etopsfuel_time|int))}}    ADDITIONAL FUEL
{{str_pad((data.fuel.plan_takeoff|int) - (helper.getFuelBucketFuelValue(data.fuel_extra, 'TANKERING')|int) - (helper.getFuelBucketFuelValue(data.fuel_extra, 'EXTRA')|int), 6, '0', 'left')}}       .. ..     {{php_date('H:i',(data.times.endurance|int) - (helper.getFuelBucketFuelTime(data.fuel_extra, 'TANKERING')|int) - (helper.getFuelBucketFuelTime(data.fuel_extra, 'EXTRA')|int))}}    REQ
{{str_pad(helper.getFuelBucketFuelValue(data.fuel_extra, 'TANKERING'), 6, '0', 'left')}}       .. ..     {{php_date('H:i',helper.getFuelBucketFuelTime(data.fuel_extra, 'TANKERING'))}}    TANKERING
{{str_pad(helper.getFuelBucketFuelValue(data.fuel_extra, 'EXTRA'), 6, '0', 'left')}}       .. ..     {{php_date('H:i',helper.getFuelBucketFuelTime(data.fuel_extra, 'EXTRA'))}}    DISCRETIONARY FUEL
{{str_pad(data.fuel.plan_takeoff, 6, '0', 'left')}}       .. ..     {{php_date('H:i',data.times.endurance)}}    TKOF
{{str_pad(data.fuel.taxi, 6, '0', 'left')}}       .. ..                 TAXI
{{str_pad(data.fuel.plan_ramp, 6, '0', 'left')}}       .. ..     {{php_date('H:i',data.times.endurance)}}    BLOCK  FUEL REM .. ..

                ARR  .. ..     TDN   .. ..
                DEP  .. ..     A/B   .. ..
                FLT  .. ..     AIR   .. ..

FBURN ADJUSTMENT FOR 1000KGS INCR/DECR IN TOW {{str_pad(data.impacts.zfw_plus_1000.burn_difference,4,'0','left')}}KGS/{{str_pad(data.impacts.zfw_minus_1000.burn_difference|replace('-',''),4,'0','left')}}KGS

FL SUMMARIES
CRZ          TOW      TRF         TIM      FL
{% if data.impacts.plus_2000ft -%}
{{helper.formatCruiseProfile(data.general.cruise_profile, data.impacts.plus_2000ft.cost_index)}}       {{str_pad(data.weights.est_tow,6,'0','left')}}   {{str_pad(data.impacts.plus_2000ft.enroute_burn, 6, '0', 'left')}}      {{php_date('H:i',data.impacts.plus_2000ft.time_enroute)}}     {{(data.impacts.plus_2000ft.initial_fl)}}
{% endif -%}
{{helper.formatCruiseProfile(data.general.cruise_profile, data.general.costindex)}}       {{str_pad(data.weights.est_tow,6,'0','left')}}   {{str_pad(data.fuel.enroute_burn, 6, '0', 'left')}}      {{php_date('H:i',data.times.est_time_enroute)}}     {{(data.general.initial_altitude|int)//100}}
{% if data.impacts.minus_2000ft -%}
{{helper.formatCruiseProfile(data.general.cruise_profile, data.impacts.minus_2000ft.cost_index)}}       {{str_pad(data.weights.est_tow,6,'0','left')}}   {{str_pad(data.impacts.minus_2000ft.enroute_burn, 6, '0', 'left')}}      {{php_date('H:i',data.impacts.minus_2000ft.time_enroute)}}     {{(data.impacts.minus_2000ft.initial_fl)}}
{% endif %}

FLT NBR {{data.atc.callsign}}   DTE {{php_date('d/m/y',data.times.sched_out)}}

 EZF       PLD        ELW       ETW     CRZ
{{str_pad(data.weights.est_zfw,6,'0','left')}}    {{str_pad(data.weights.payload,6,'0','left')}}     {{str_pad(data.weights.est_ldw,6,'0','left')}}    {{str_pad(data.weights.est_tow,6,'0','left')}}   {{helper.formatCruiseProfile(data.general.cruise_profile, data.general.costindex)}}

{% if data.etops and data.etops is mapping %}
ENRT ALTN SUITABLE
{%- set etops_apts = data.etops.suitable_airport -%}
{%- if etops_apts is mapping %}{% set etops_apts = [etops_apts] %}{% endif -%}
{%- for airport in etops_apts %}
{{airport.icao_code}} VALIDITY WINDOW {{helper.formatIsoTime(airport.suitability_start)}}Z TO {{helper.formatIsoTime(airport.suitability_end)}}Z
{%- endfor %}

-E.ENT {{str_replace('.','',helper.formatLatLonEtops(data.etops.entry.pos_lat_fix,data.etops.entry.pos_long_fix))}}  {{helper.interpolateEtpDistance(data.etops.entry, data.navlog)}} NM {{php_date('H:i',data.etops.entry.elapsed_time)}}    {{data.etops.entry.icao_code}}  {{str_replace('.','',helper.formatLatLonEtops(data.etops.entry.pos_lat_apt,data.etops.entry.pos_long_apt))}}
-E.EXT {{str_replace('.','',helper.formatLatLonEtops(data.etops.exit.pos_lat_fix,data.etops.exit.pos_long_fix))}}  {{helper.interpolateEtpDistance(data.etops.exit, data.navlog)}} NM {{php_date('H:i',data.etops.exit.elapsed_time)}}    {{data.etops.exit.icao_code}}  {{str_replace('.','',helper.formatLatLonEtops(data.etops.exit.pos_lat_apt,data.etops.exit.pos_long_apt))}}

{%- set ns_etops = namespace(criticalEtp=data.etops.critical_point.fix_type, deficit=0) -%}
{%- set etps = data.etops.equal_time_point | default([]) -%}
{%- if etps is mapping %}{% set etps = [etps] %}{% endif -%}
{%- for etp in etps -%}
    {%- if etp.pos_lat|float == data.etops.critical_point.pos_lat|float and etp.pos_long|float == data.etops.critical_point.pos_long|float -%}
        {%- set ns_etops.criticalEtp = 'ETP' ~ loop.index -%}
    {%- endif -%}
{%- endfor -%}
{%- set def_val = data.etops.critical_point.est_fob|float - data.etops.critical_point.critical_fuel|float -%}
{%- if def_val > 0 %}{% set ns_etops.deficit = 0 %}{% else %}{% set ns_etops.deficit = def_val|abs %}{% endif %}
MOST CRITICAL FUEL SCENARIO AT : {{ns_etops.criticalEtp}} FUEL DEFICIT OF {{ns_etops.deficit}} KGS

{%- if etps|length > 0 %}
                                                         TIME TO
                   DIST        W/C    CFR   FOB    EXC   ETP/ALT
{%- for etp in etps %}
{%- set da1 = (etp.div_airport[0] if etp.div_airport is sequence else etp.div_airport) -%}
{%- set da2 = (etp.div_airport[1] if etp.div_airport is sequence and etp.div_airport|length > 1 else (etp.div_airport if etp.div_airport is mapping else etp.div_airport[0])) %}
ETP{{loop.index}} {{da1.icao_code}}/{{da2.icao_code}}   {{str_pad(da1.distance,4,'0','left')}}/{{str_pad(da2.distance,4,'0','left')}}  {{helper.formatAvgWindComp(da1.avg_wind_comp)}}/{{helper.formatAvgWindComp(da2.avg_wind_comp)}} {{str_pad(etp.critical_fuel,5,'0','left')}} {{str_pad(etp.est_fob,6,'0','left')}} {{str_pad((etp.est_fob|int - etp.critical_fuel|int),5,'0','left')}} {{php_date('Hi',etp.elapsed_time)}}/{{php_date('Hi',etp.div_time)}}
     {{helper.formatLatLonEtops(etp.pos_lat, etp.pos_long)}}
{%- endfor %}
{%- endif %}
{%- endif %}

ETO TIM   AWY     WPT/FRQ      TTK   DIS  TAS  FLV   TD /TP   FBO    PFRM
ATO TIM      COORD             MTK   TTL  G/S  GMA   WIND     ABO    AFRM

        {{data.origin.icao_code}}                                                       {{str_pad(data.fuel.taxi, 5, '0', 'left')}}  {{str_pad(data.fuel.plan_takeoff, 6, '0', 'left')}}
        ELEV {{helper.formatAirportElevation(data.origin.elevation)}} FT
        {{helper.formatLatLon(data.origin.pos_lat, data.origin.pos_long)}}
</pre>
<div>
{% set ns = namespace(totalDistance=0) %}
{% for fix in data.navlog.fix | default([]) %}
    {% set ns.totalDistance = ns.totalDistance + (fix.distance|int) %}
    {% if fix.fir_crossing.fir %}
        {% if fix.fir_crossing.fir is mapping %}
            {% set fir_list = [fix.fir_crossing.fir] %}
        {% else %}
            {% set fir_list = fix.fir_crossing.fir %}
        {% endif %}
        {% for fir in fir_list %}
<div class="nav-row">
    0000  {{str_pad(fix.via_airway,6)}} FIR/{{fir.fir_icao}}      {{fix.track_true}}T  000  {{str_pad(fix.true_airspeed,3,'0','left')}}  {% if fix.stage=='CLB' %}CLB {% else %}{{str_pad((fix.altitude_feet|int)//100,3,'0','left')}} {% endif %}  {{str_pad(helper.getIsa(fix),7)}} {{str_pad(fix.fuel_totalused,5,'0','left')}}  {{str_pad(fix.fuel_plan_onboard,6,'0','left')}}
    {{php_date('Hi',fix.time_total)}}    {{helper.formatLatLon(fir.pos_lat_entry, fir.pos_long_entry)}}    {{fix.track_mag}}M  0000 {{str_pad(fix.groundspeed,3,'0','left')}}  {{str_pad((fix.mora|int)//100,3,'0','left')}}   {{fix.wind_dir}}{{str_pad(fix.wind_spd,3,'0','left')}}
</div>
        {% endfor %}
    {% endif %}

<div class="nav-row">
    {{php_date('Hi',fix.time_leg)}}  {{str_pad(fix.via_airway,6)}} {{str_pad(helper.reformatCoordinate(fix.ident), 12, ' ', 'right')}}  {{fix.track_true}}T  {{str_pad(fix.distance,3,'0','left')}}  {{str_pad(fix.true_airspeed,3,'0','left')}}  {% if fix.stage=='CLB' %}CLB {% else %}{{str_pad((fix.altitude_feet|int)//100,3,'0','left')}} {% endif %}  {{str_pad(helper.getIsa(fix),7)}} {{str_pad(fix.fuel_totalused,5,'0','left')}}  {{str_pad(fix.fuel_plan_onboard,6,'0','left')}}
    {{php_date('Hi',fix.time_total)}}    {{helper.formatLatLon(fix.pos_lat, fix.pos_long)}}    {{fix.track_mag}}M  {{str_pad(ns.totalDistance,4,'0','left')}} {{str_pad(fix.groundspeed,3,'0','left')}}  {{str_pad((fix.mora|int)//100,3,'0','left')}}   {{fix.wind_dir}}{{str_pad(fix.wind_spd,3,'0','left')}}
    {% if fix.ident == data.destination.icao_code %}
        ELEV {{helper.formatAirportElevation(data.destination.elevation)}} FT
    {% endif %}
</div>
{% endfor %}
</div>
<pre>
{{helper.getFormattedFir(data.atc.section18)}}
TRACK USED = -OPT

G/C DIST {{data.origin.icao_code}}/{{data.destination.icao_code}}  {{data.general.gc_distance}} NM

ROUTE DIST {{data.general.route_distance}}NM

MAX FL / AVG.TAS  {{helper.getMaxAlt(data.navlog)}} / {{(data.general.cruise_tas|int)}}

AVG COMP {{helper.formatAvgWindComp(data.general.avg_comp_wind)}}

         GMA  DIST  TTK  W/C   FL   TIME   FUEL BOF
{% for alt in alternates %}
{{alt.icao_code}}          {{str_pad(alt.distance,4,'0','left')}}  {{str_pad(alt.track_true,3,'0','left')}}  {{alt.avg_wind_comp}}  {{(alt.cruise_altitude|int)//100}}  {{php_date('H.i',alt.ete)}}  {{str_pad(alt.burn,6,'0','left')}}
         {{data.destination.icao_code}} {{alt.route_ifps}} {{alt.icao_code}}
{% endfor %}

                             ALTERNATE DATA

ETO TIM   AWY     WPT/FRQ      TTK   DIS  TAS  FLV   TD /TP   FBO    PFRM
ATO TIM      COORD             MTK   TTL  G/S  GMA   WIND     ABO    AFRM
{%- if navlog_alt1 and navlog_alt1.fix -%}
{% set ns_alt = namespace(totalDistance=0) %}
{% for fix in navlog_alt1.fix %}
{% set ns_alt.totalDistance = ns_alt.totalDistance + (fix.distance|int) %}
    {{php_date('Hi',fix.time_leg)}}  {{str_pad(fix.via_airway,6, ' ', 'right')}} {{str_pad(helper.reformatCoordinate(fix.ident) ~ ' ' ~ (fix.frequency|default('',true)), 12, ' ', 'right')}}  {{fix.track_true}}T  {{str_pad(fix.distance,3,'0','left')}}  {{str_pad(fix.true_airspeed,3,'0','left')}}  {% if fix.stage=='CLB' %}CLB {% else %}{{str_pad((fix.altitude_feet|int)//100,3,'0','left')}} {% endif %}  {{str_pad(helper.getIsa(fix),7)}} {{str_pad(fix.fuel_totalused,5,'0','left')}}  {{str_pad(fix.fuel_plan_onboard,6,'0','left')}}
    {{php_date('Hi',fix.time_total)}}    {{helper.formatLatLon(fix.pos_lat, fix.pos_long)}}    {{fix.track_mag}}M  {{str_pad(ns_alt.totalDistance,4,'0','left')}} {{str_pad(fix.groundspeed,3,'0','left')}}  {{str_pad((fix.mora|int)//100,3,'0','left')}}   {{fix.wind_dir}}{{str_pad(fix.wind_spd,3,'0','left')}}
        {%- if fix.ident == alternates[0].icao_code %}ELEV {{helper.formatAirportElevation(alternates[0].elevation)}} FT {% endif %}
{% endfor %}
{% endif %}

CLIMB
         FL100     FL180     FL240     FL300     FL340     FL390     FL450
{% for fix in data.navlog.fix | default([]) -%}
{%- if fix.stage == 'CLB' and fix.ident != 'TOC' -%}
{{helper.formatWindMatrixRow(fix)}}
{% endif -%}
{% endfor %}

CRUISE
         FL100     FL180     FL240     FL300     FL340     FL390     FL450
{% for fix in data.navlog.fix | default([]) -%}
{%- if fix.stage == 'CRZ' and fix.ident != 'TOD' -%}
{{helper.formatWindMatrixRow(fix)}}
{% endif -%}
{% endfor %}

DESCENT
         FL100     FL180     FL240     FL300     FL340     FL390     FL450
{% for fix in data.navlog.fix | default([]) -%}
{%- if fix.stage == 'DSC' -%}
{{helper.formatWindMatrixRow(fix)}}
{% endif -%}
{% endfor %}

I CERTIFY THAT HAVE SATISFIED MYSELF THAT ALL FACTORS WHICH FORM THE BASIS OF
FLIGHT PREPARATION ARE IN ACCORDANCE WITH THE PERTINENT REGULATIONS LAID DOWN
BY THE INDONESIAN CIVIL AVIATION, CAPTAIN {{strtoupper(data.crew.cpt)}}

PIC             : CAPTAIN {{strtoupper(data.crew.cpt)}}

SIGN            : .. .. .. .. .. ..

PREPARED BY     : FOO. {{strtoupper(data.crew.dx)}} - {{fooId}}

CAPTAINS SIGNATURE FOR COMPLETION OF JOURNAL AFTER FLIGHT

                                      .. .. .. .. ..

{% for line in data.atc.flightplan_text.split('\n') -%}
{% if line -%}
{{line}}
{% endif -%}
{% endfor %}

{% if data.etops %}
- - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -
{% for etp in data.etops.equal_time_point | default([]) %}
2D
ETP {{str_replace('.','',helper.formatLatLonEtops(etp.pos_lat, etp.pos_long))}}
TO ETP BURN {{str_pad(data.fuel.plan_takeoff|int - etp.est_fob|int, 6, '0', 'left')}}
       TIME  {{php_date('H.i', etp.elapsed_time)}}
       DIST   {{str_pad(helper.interpolateEtpAnalysisDistance(etp, data.navlog), 4, '0', 'left')}}
       ETP AIRPORTS
       {% set da1 = (etp.div_airport[0] if etp.div_airport is sequence else etp.div_airport) %}
       {% set da2 = (etp.div_airport[1] if etp.div_airport is sequence and etp.div_airport|length > 1 else (etp.div_airport if etp.div_airport is mapping else etp.div_airport[0])) %}
       {{da1.icao_code}}    {{da2.icao_code}}
TIME   {{php_date('H.i', etp.div_time)}}   {{php_date('H.i', etp.div_time)}}
RQFUEL {{str_pad(etp.div_burn, 6, '0', 'left')}}  {{str_pad(etp.div_burn, 6, '0', 'left')}}
FL     {{(etp.div_altitude|int) / 100}}   {{(etp.div_altitude|int) / 100}}
DIST   {{str_pad(da1.distance, 4, '0', 'left')}}    {{str_pad(da2.distance, 4, '0', 'left')}}
WIND   {{helper.formatEtopsAvgWindComp(da1.avg_wind_comp)}}    {{helper.formatEtopsAvgWindComp(da2.avg_wind_comp)}}
- - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -

{% endfor %}
{% endif %}

END OF NAVTECH DATAPLAN
REQUEST NO. {{data.params.request_id[-5:]}} / REV NBR {{data.general.release}}
</pre>

    <div class="landscape-section">
        <div class="notam-header-landscape">
            NOTAM BRIEFING<br>
            {{data.general.icao_airline}}{{data.general.flight_number}} - {{php_date('Y-m-d', data.times.sched_out)}}
        </div>

        <div class="notam-columns">
        {% for group in notam_groups %}
            {% if group.notams %}
            <div class="notam-group">
                <div class="notam-group-header">{{ group.title }}</div>
                {% for n in group.notams %}
                <div class="notam-item">
                    <b>{{ n.notam_id }}</b> {{ n.notam_nrc }}<br>
                    Q) {{ n.notam_qcode }}<br>
                    A) {{ n.location_id }}
                    B) {{ n.get('notam_effective_dtg', n.get('date_effective', '')) }}
                    {% if n.get('notam_expire_dtg', n.get('date_expire', '')) %}C) {{ n.get('notam_expire_dtg', n.get('date_expire', '')) }}<br>{% endif %}
                    {% if n.notam_schedule %}D) {{ n.notam_schedule }}<br>{% endif %}
                    E) {{ n.notam_text }}<br>
                    {% if n.lower_limit %}F) {{ n.lower_limit }} G) {{ n.upper_limit }}{% endif %}
                </div>
                {% endfor %}
            </div>
            {% endif %}
        {% endfor %}
        </div>
    </div>

    <div class="page-break"></div>

    <div class="wx-header">
    <br><br>
        THE FOLLOWING ARE EXTRACT FROM:<br>
        BADAN METEOROLOGI, KLIMATOLOGI, DAN GEOFISIKA<br>
        BIDANG METEOROLOGI PENERBANGAN<br>
        WHICH MAY EFFECT TO THE OPERATION OF FLIGHT<br><br>
        WEATHER BRIEFING
    </div>
    <hr>

    {% for wx in weather_info %}
    <div class="wx-section">
        <div class="wx-airport-title">{{wx.title}}</div>
        <div class="wx-data">{{wx.data}}</div>
    </div>
    <hr style="border-top: 1px solid #ccc;">
    {% endfor %}

    <div class="page-break"></div>

    {% if map_images %}
    <div class="landscape-section">

<div style="text-align: center; font-weight: bold; font-size: 14pt; margin-bottom: 10px;">
            FLIGHT MAPS<br>
            {{data.general.icao_airline}}{{data.general.flight_number}} - {{php_date('Y-m-d', data.times.sched_out)}}
        </div>

        {% for map in map_images %}
            <div class="map-container">
                <div class="map-title">{{ map.name }}</div>
                <img src="{{ map.url }}" class="map-image">
            </div>
            {% if not loop.last %}<div class="page-break"></div>{% endif %}
        {% endfor %}
    </div>
    {% endif %}

</body>
</html>
"""

                # Execution Jinja2
                env = Environment(loader=BaseLoader())
                env.globals.update({
                    'helper': PythonHelper(), 'php_date': php_date, 'str_pad': php_str_pad,
                    'strtoupper': lambda x: str(x).upper() if x else "", 'date': php_date,
                    'abs': abs, 'int': int, 'str_replace': lambda o, n, s: str(s).replace(o, n),
                    'wordwrap': php_wordwrap, 'fooId': foo_id, 'etops_str': etops_alternates_str,
                    'notam_groups': notam_groups, 'alternates': alternates_list,
                    'weather_info': weather_info, 'map_images': map_images, 'navlog_alt1': navlog_alt1,
                    'logo_base64': logo_base64
                })
                
                template = env.from_string(template_str)
                rendered_html = template.render(data=data_obj, airport_info=airport_info, alternates=alternates_list, notam_groups=notam_groups, weather_info=weather_info, map_images=map_images)
                
                # Render PDF buffer with WeasyPrint
                pdf_buffer = io.BytesIO()
                HTML(string=rendered_html).write_pdf(pdf_buffer)
                
                st.success("✅ OFP Generated and Ready for Download")
                
                # --- BERIKUT EDITAN UNTUK DIRECT PDF PREVIEW ---
                pdf_filename = f"GIA{data_obj.general.flight_number}_Briefing_Final.pdf"
                st.download_button(
                    label="📥 Download OFP PDF",
                    data=pdf_buffer.getvalue(),
                    file_name=pdf_filename,
                    mime="application/pdf",
                    type="primary"
                )
                
            except Exception as e:
                st.error(f"❌ Terjadi kesalahan saat memproses data/PDF: {e}")

# ---------------------------------------------------------
# ROUTING APLIKASI & INTERNET URL PAYLOADS DARI PHP
# ---------------------------------------------------------
# Cek parameter URL '?username=...' dari Web PHP utama
# ---------------------------------------------------------
# ROUTING APLIKASI (UPDATED FOR INSTANT PHP REDIRECT)
# ---------------------------------------------------------
# Intercept parameter URL immediately upon runtime before rendering any pages
if "username" in st.query_params:
    st.session_state['logged_in'] = True
    st.session_state['username'] = st.query_params["username"]

if st.session_state['logged_in']:
    dashboard()
else:
    login_page()
