#!/usr/bin/env python3
"""
Texts Dashboard Enhanced - Interactive messaging analytics with GitHub-style heatmap
Features:
- GitHub contribution-style heatmap that adjusts to selected contacts/groups
- Group chat filtering and selection
- Contact multi-select with real-time chart updates
- Dark mode dashboard with Chart.js visualizations

Usage: python3 texts_dashboard_enhanced.py [--use-2024] [-o output.html]
"""

import sqlite3, os, sys, re, subprocess, argparse, glob, json
from datetime import datetime, timedelta
from collections import defaultdict

IMESSAGE_DB = os.path.expanduser("~/Library/Messages/chat.db")
ADDRESSBOOK_DIR = os.path.expanduser("~/Library/Application Support/AddressBook")
WHATSAPP_PATHS = [
    os.path.expanduser("~/Library/Group Containers/group.net.whatsapp.WhatsApp.shared/ChatStorage.sqlite"),
    os.path.expanduser("~/Library/Containers/com.whatsapp/Data/Library/Application Support/WhatsApp/ChatStorage.sqlite"),
    os.path.expanduser("~/Library/Containers/desktop.WhatsApp/Data/Library/Application Support/WhatsApp/ChatStorage.sqlite"),
]

WHATSAPP_DB = None
COCOA_OFFSET = 978307200

def extract_imessage_contacts():
    contacts = {}
    db_paths = glob.glob(os.path.join(ADDRESSBOOK_DIR, "Sources", "*", "AddressBook-v22.abcddb"))
    main_db = os.path.join(ADDRESSBOOK_DIR, "AddressBook-v22.abcddb")
    if os.path.exists(main_db): db_paths.append(main_db)
    for db_path in db_paths:
        try:
            conn = sqlite3.connect(db_path)
            people = {}
            for row in conn.execute("SELECT ROWID, ZFIRSTNAME, ZLASTNAME FROM ZABCDRECORD WHERE ZFIRSTNAME IS NOT NULL OR ZLASTNAME IS NOT NULL"):
                name = f"{row[1] or ''} {row[2] or ''}".strip()
                if name: people[row[0]] = name
            for owner, phone in conn.execute("SELECT ZOWNER, ZFULLNUMBER FROM ZABCDPHONENUMBER WHERE ZFULLNUMBER IS NOT NULL"):
                if owner in people:
                    name = people[owner]
                    digits = re.sub(r'\D', '', str(phone))
                    if digits:
                        contacts[digits] = name
                        if len(digits) >= 10: contacts[digits[-10:]] = name
                        if len(digits) >= 7: contacts[digits[-7:]] = name
                        if len(digits) == 11 and digits.startswith('1'): contacts[digits[1:]] = name
            for owner, email in conn.execute("SELECT ZOWNER, ZADDRESS FROM ZABCDEMAILADDRESS WHERE ZADDRESS IS NOT NULL"):
                if owner in people: contacts[email.lower().strip()] = people[owner]
            conn.close()
        except: pass
    return contacts

def extract_whatsapp_contacts():
    contacts = {}
    if not WHATSAPP_DB: return contacts
    try:
        conn = sqlite3.connect(WHATSAPP_DB)
        for row in conn.execute("SELECT ZJID, ZPUSHNAME FROM ZWAPROFILEPUSHNAME WHERE ZPUSHNAME IS NOT NULL"):
            jid, name = row
            if jid and name: contacts[jid] = name
        conn.close()
    except: pass
    return contacts

def get_name_imessage(handle, contacts):
    if '@' in handle:
        lookup = handle.lower().strip()
        if lookup in contacts: return contacts[lookup]
        return handle.split('@')[0]
    digits = re.sub(r'\D', '', str(handle))
    if digits in contacts: return contacts[digits]
    if len(digits) == 11 and digits.startswith('1') and digits[1:] in contacts:
        return contacts[digits[1:]]
    if len(digits) >= 10 and digits[-10:] in contacts:
        return contacts[digits[-10:]]
    if len(digits) >= 7 and digits[-7:] in contacts:
        return contacts[digits[-7:]]
    return handle

def get_name_whatsapp(jid, contacts):
    if not jid: return "Unknown"
    if jid in contacts: return contacts[jid]
    if '@' in jid:
        phone = jid.split('@')[0]
        if len(phone) == 10: return f"({phone[:3]}) {phone[3:6]}-{phone[6:]}"
        elif len(phone) == 11 and phone.startswith('1'):
            return f"+1 ({phone[1:4]}) {phone[4:7]}-{phone[7:]}"
        return f"+{phone}"
    return jid

def find_whatsapp_database():
    for path in WHATSAPP_PATHS:
        if os.path.exists(path): return path
    return None

def check_access():
    global WHATSAPP_DB
    has_imessage = has_whatsapp = False
    if os.path.exists(IMESSAGE_DB):
        try:
            conn = sqlite3.connect(IMESSAGE_DB)
            conn.execute("SELECT 1 FROM message LIMIT 1")
            conn.close()
            has_imessage = True
        except: pass
    WHATSAPP_DB = find_whatsapp_database()
    if WHATSAPP_DB:
        try:
            conn = sqlite3.connect(WHATSAPP_DB)
            conn.execute("SELECT 1 FROM ZWAMESSAGE LIMIT 1")
            conn.close()
            has_whatsapp = True
        except: pass
    if not has_imessage and not has_whatsapp:
        print("\n[!] ACCESS DENIED - Neither iMessage nor WhatsApp accessible")
        print("   System Settings -> Privacy & Security -> Full Disk Access -> Add Terminal")
        subprocess.run(['open', 'x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles'])
        sys.exit(1)
    return has_imessage, has_whatsapp

def q_imessage(sql):
    conn = sqlite3.connect(IMESSAGE_DB)
    r = conn.execute(sql).fetchall()
    conn.close()
    return r

def q_whatsapp(sql):
    conn = sqlite3.connect(WHATSAPP_DB)
    r = conn.execute(sql).fetchall()
    conn.close()
    return r

def get_all_imessage_data(ts_start, contacts):
    """Extract all iMessage data including per-message timestamps for heatmap"""
    data = {
        'contacts': [],
        'group_chats': [],
        'messages': [],  # [timestamp, contact_idx, is_sent, is_group]
        'daily_counts': {},
        'hourly_counts': defaultdict(int),
        'day_of_week_counts': defaultdict(int)
    }
    
    # CTE for one-on-one messages
    one_on_one_cte = """
        WITH chat_participants AS (SELECT chat_id, COUNT(*) as pc FROM chat_handle_join GROUP BY chat_id),
        one_on_one AS (SELECT m.ROWID as msg_id FROM message m 
            JOIN chat_message_join cmj ON m.ROWID = cmj.message_id
            JOIN chat_participants cp ON cmj.chat_id = cp.chat_id WHERE cp.pc = 1)
    """
    
    # Get contact stats
    rows = q_imessage(f"""{one_on_one_cte}
        SELECT h.id, COUNT(*) as total, SUM(CASE WHEN m.is_from_me=1 THEN 1 ELSE 0 END) as sent,
            SUM(CASE WHEN m.is_from_me=0 THEN 1 ELSE 0 END) as received,
            SUM(CASE WHEN CAST(strftime('%H',datetime((m.date/1000000000+978307200),'unixepoch','localtime')) AS INT)<5 THEN 1 ELSE 0 END) as late
        FROM message m JOIN handle h ON m.handle_id=h.ROWID
        WHERE (m.date/1000000000+978307200)>{ts_start} AND m.ROWID IN (SELECT msg_id FROM one_on_one)
        AND NOT (LENGTH(REPLACE(REPLACE(h.id, '+', ''), '-', '')) BETWEEN 5 AND 6 AND REPLACE(REPLACE(h.id, '+', ''), '-', '') GLOB '[0-9]*')
        GROUP BY h.id ORDER BY total DESC""")
    
    handle_to_idx = {}
    for handle, total, sent, received, late in rows:
        name = get_name_imessage(handle, contacts)
        handle_to_idx[handle] = len(data['contacts'])
        data['contacts'].append({
            'id': handle, 'name': name, 'total': total, 'sent': sent, 
            'received': received, 'late_night': late, 
            'ratio': round(sent/max(received,1), 2), 'platform': 'imessage', 'type': 'contact'
        })
    
    # Get all individual message timestamps for contacts
    rows = q_imessage(f"""{one_on_one_cte}
        SELECT h.id, (m.date/1000000000+978307200) as ts, m.is_from_me
        FROM message m JOIN handle h ON m.handle_id=h.ROWID
        WHERE (m.date/1000000000+978307200)>{ts_start} AND m.ROWID IN (SELECT msg_id FROM one_on_one)
        AND NOT (LENGTH(REPLACE(REPLACE(h.id, '+', ''), '-', '')) BETWEEN 5 AND 6 AND REPLACE(REPLACE(h.id, '+', ''), '-', '') GLOB '[0-9]*')
        ORDER BY ts""")
    
    for handle, ts, is_from_me in rows:
        if handle in handle_to_idx:
            # [timestamp, contact_index, is_sent, is_group_chat]
            data['messages'].append([int(ts), handle_to_idx[handle], int(is_from_me), 0])
    
    # Get group chats
    group_cte = """
        WITH cp AS (SELECT chat_id, COUNT(*) as pc FROM chat_handle_join GROUP BY chat_id),
        gc AS (SELECT chat_id FROM cp WHERE pc >= 2)
    """
    
    rows = q_imessage(f"""{group_cte}
        SELECT c.ROWID, c.display_name, COUNT(*), 
            (SELECT COUNT(*) FROM chat_handle_join WHERE chat_id = c.ROWID),
            SUM(CASE WHEN m.is_from_me=1 THEN 1 ELSE 0 END),
            SUM(CASE WHEN m.is_from_me=0 THEN 1 ELSE 0 END)
        FROM chat c JOIN chat_message_join cmj ON c.ROWID = cmj.chat_id 
        JOIN message m ON cmj.message_id = m.ROWID
        WHERE c.ROWID IN (SELECT chat_id FROM gc) AND (m.date/1000000000+978307200)>{ts_start} 
        GROUP BY c.ROWID ORDER BY 3 DESC""")
    
    chat_id_to_idx = {}
    group_start_idx = len(data['contacts'])  # Groups are indexed after contacts
    for chat_id, name, msg_count, members, sent, received in rows:
        chat_id_to_idx[chat_id] = group_start_idx + len(data['group_chats'])
        data['group_chats'].append({
            'id': chat_id, 'name': name or f"Group ({members})", 
            'total': msg_count, 'members': members, 'sent': sent, 'received': received,
            'platform': 'imessage', 'type': 'group'
        })
    
    # Get group chat message timestamps
    rows = q_imessage(f"""{group_cte}
        SELECT cmj.chat_id, (m.date/1000000000+978307200) as ts, m.is_from_me
        FROM message m JOIN chat_message_join cmj ON m.ROWID = cmj.message_id
        WHERE cmj.chat_id IN (SELECT chat_id FROM gc) AND (m.date/1000000000+978307200)>{ts_start}
        ORDER BY ts""")
    
    for chat_id, ts, is_from_me in rows:
        if chat_id in chat_id_to_idx:
            data['messages'].append([int(ts), chat_id_to_idx[chat_id], int(is_from_me), 1])
    
    # Daily counts (for overall stats)
    for date, total, sent, received in q_imessage(f"""
        SELECT DATE(datetime((date/1000000000+978307200),'unixepoch','localtime')) as d,
            COUNT(*), SUM(CASE WHEN is_from_me=1 THEN 1 ELSE 0 END),
            SUM(CASE WHEN is_from_me=0 THEN 1 ELSE 0 END)
        FROM message WHERE (date/1000000000+978307200)>{ts_start} GROUP BY d"""):
        data['daily_counts'][date] = {'total': total, 'sent': sent, 'received': received}
    
    # Hourly counts
    for hour, count in q_imessage(f"""
        SELECT CAST(strftime('%H',datetime((date/1000000000+978307200),'unixepoch','localtime')) AS INT), COUNT(*) 
        FROM message WHERE (date/1000000000+978307200)>{ts_start} GROUP BY 1"""):
        data['hourly_counts'][hour] = count
    
    # Day of week counts
    days = ['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday']
    for day_num, count in q_imessage(f"""
        SELECT CAST(strftime('%w',datetime((date/1000000000+978307200),'unixepoch','localtime')) AS INT), COUNT(*) 
        FROM message WHERE (date/1000000000+978307200)>{ts_start} GROUP BY 1"""):
        data['day_of_week_counts'][days[day_num]] = count
    
    return data

def get_all_whatsapp_data(ts_start, contacts):
    """Extract all WhatsApp data including per-message timestamps"""
    data = {
        'contacts': [],
        'group_chats': [],
        'messages': [],
        'daily_counts': {},
        'hourly_counts': defaultdict(int),
        'day_of_week_counts': defaultdict(int)
    }
    
    if not WHATSAPP_DB:
        return data
    
    dm_cte = """
        WITH dm_sessions AS (SELECT Z_PK, ZCONTACTJID FROM ZWACHATSESSION WHERE ZSESSIONTYPE = 0),
        dm_messages AS (SELECT m.Z_PK as msg_id, m.ZCHATSESSION, s.ZCONTACTJID
            FROM ZWAMESSAGE m JOIN dm_sessions s ON m.ZCHATSESSION = s.Z_PK)
    """
    
    # Get contact stats
    rows = q_whatsapp(f"""{dm_cte}
        SELECT dm.ZCONTACTJID, COUNT(*) as total, 
            SUM(CASE WHEN m.ZISFROMME=1 THEN 1 ELSE 0 END) as sent,
            SUM(CASE WHEN m.ZISFROMME=0 THEN 1 ELSE 0 END) as received,
            SUM(CASE WHEN CAST(strftime('%H',datetime(m.ZMESSAGEDATE+{COCOA_OFFSET},'unixepoch','localtime')) AS INT)<5 THEN 1 ELSE 0 END) as late
        FROM ZWAMESSAGE m JOIN dm_messages dm ON m.Z_PK = dm.msg_id
        WHERE m.ZMESSAGEDATE>{ts_start} GROUP BY dm.ZCONTACTJID ORDER BY total DESC""")
    
    jid_to_idx = {}
    for jid, total, sent, received, late in rows:
        name = get_name_whatsapp(jid, contacts)
        jid_to_idx[jid] = len(data['contacts'])
        data['contacts'].append({
            'id': jid, 'name': name, 'total': total, 'sent': sent,
            'received': received, 'late_night': late,
            'ratio': round(sent/max(received,1), 2), 'platform': 'whatsapp', 'type': 'contact'
        })
    
    # Get individual message timestamps
    rows = q_whatsapp(f"""{dm_cte}
        SELECT dm.ZCONTACTJID, (m.ZMESSAGEDATE+{COCOA_OFFSET}) as ts, m.ZISFROMME
        FROM ZWAMESSAGE m JOIN dm_messages dm ON m.Z_PK = dm.msg_id
        WHERE m.ZMESSAGEDATE>{ts_start} ORDER BY ts""")
    
    for jid, ts, is_from_me in rows:
        if jid in jid_to_idx:
            data['messages'].append([int(ts), jid_to_idx[jid], int(is_from_me), 0])
    
    # Get group chats
    group_start_idx = len(data['contacts'])
    rows = q_whatsapp(f"""
        WITH group_sessions AS (SELECT Z_PK, ZPARTNERNAME FROM ZWACHATSESSION WHERE ZSESSIONTYPE = 1)
        SELECT s.Z_PK, s.ZPARTNERNAME, COUNT(*),
            SUM(CASE WHEN m.ZISFROMME=1 THEN 1 ELSE 0 END),
            SUM(CASE WHEN m.ZISFROMME=0 THEN 1 ELSE 0 END)
        FROM ZWAMESSAGE m JOIN group_sessions s ON m.ZCHATSESSION = s.Z_PK
        WHERE m.ZMESSAGEDATE>{ts_start} GROUP BY s.Z_PK ORDER BY 3 DESC""")
    
    session_to_idx = {}
    for session_id, name, total, sent, received in rows:
        session_to_idx[session_id] = group_start_idx + len(data['group_chats'])
        data['group_chats'].append({
            'id': session_id, 'name': name or "Unnamed Group",
            'total': total, 'members': 0, 'sent': sent, 'received': received,
            'platform': 'whatsapp', 'type': 'group'
        })
    
    # Get group message timestamps
    rows = q_whatsapp(f"""
        WITH group_sessions AS (SELECT Z_PK FROM ZWACHATSESSION WHERE ZSESSIONTYPE = 1)
        SELECT m.ZCHATSESSION, (m.ZMESSAGEDATE+{COCOA_OFFSET}) as ts, m.ZISFROMME
        FROM ZWAMESSAGE m WHERE m.ZCHATSESSION IN (SELECT Z_PK FROM group_sessions)
        AND m.ZMESSAGEDATE>{ts_start} ORDER BY ts""")
    
    for session_id, ts, is_from_me in rows:
        if session_id in session_to_idx:
            data['messages'].append([int(ts), session_to_idx[session_id], int(is_from_me), 1])
    
    # Daily counts
    for date, count in q_whatsapp(f"""
        SELECT DATE(datetime(ZMESSAGEDATE+{COCOA_OFFSET},'unixepoch','localtime')) as d, COUNT(*)
        FROM ZWAMESSAGE WHERE ZMESSAGEDATE>{ts_start} GROUP BY d"""):
        data['daily_counts'][date] = {'total': count, 'sent': 0, 'received': 0}
    
    # Hourly counts
    for hour, count in q_whatsapp(f"""
        SELECT CAST(strftime('%H',datetime(ZMESSAGEDATE+{COCOA_OFFSET},'unixepoch','localtime')) AS INT), COUNT(*)
        FROM ZWAMESSAGE WHERE ZMESSAGEDATE>{ts_start} GROUP BY 1"""):
        data['hourly_counts'][hour] = count
    
    # Day of week
    days = ['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday']
    for day_num, count in q_whatsapp(f"""
        SELECT CAST(strftime('%w',datetime(ZMESSAGEDATE+{COCOA_OFFSET},'unixepoch','localtime')) AS INT), COUNT(*)
        FROM ZWAMESSAGE WHERE ZMESSAGEDATE>{ts_start} GROUP BY 1"""):
        data['day_of_week_counts'][days[day_num]] = count
    
    return data

def merge_data(im_data, wa_data, has_im, has_wa):
    """Merge iMessage and WhatsApp data"""
    data = {
        'contacts': [],
        'group_chats': [],
        'messages': [],
        'summary': {
            'total_messages': 0,
            'total_sent': 0,
            'total_received': 0,
            'imessage_total': 0,
            'whatsapp_total': 0,
            'has_imessage': has_im,
            'has_whatsapp': has_wa
        }
    }
    
    # Add iMessage contacts
    idx_offset_im = 0
    if has_im:
        for c in im_data['contacts']:
            data['contacts'].append(c)
        for g in im_data['group_chats']:
            data['group_chats'].append(g)
        for msg in im_data['messages']:
            data['messages'].append(msg)
        data['summary']['imessage_total'] = sum(c['total'] for c in im_data['contacts']) + sum(g['total'] for g in im_data['group_chats'])
    
    # Add WhatsApp contacts with offset
    idx_offset_wa = len(data['contacts']) + len(data['group_chats'])
    if has_wa:
        for c in wa_data['contacts']:
            data['contacts'].append(c)
        for g in wa_data['group_chats']:
            data['group_chats'].append(g)
        # Offset WhatsApp message indices
        for msg in wa_data['messages']:
            data['messages'].append([msg[0], msg[1] + idx_offset_wa, msg[2], msg[3]])
        data['summary']['whatsapp_total'] = sum(c['total'] for c in wa_data['contacts']) + sum(g['total'] for g in wa_data['group_chats'])
    
    # Calculate totals
    data['summary']['total_messages'] = data['summary']['imessage_total'] + data['summary']['whatsapp_total']
    data['summary']['total_sent'] = sum(c['sent'] for c in data['contacts']) + sum(g['sent'] for g in data['group_chats'])
    data['summary']['total_received'] = sum(c['received'] for c in data['contacts']) + sum(g.get('received', 0) for g in data['group_chats'])
    
    return data

def generate_html(data, year, output_path):
    """Generate the interactive dashboard HTML with GitHub heatmap"""
    json_data = json.dumps(data, separators=(',', ':'))
    
    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Texts Dashboard {year}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root {{
    --bg: #0d0d0f;
    --surface: #161618;
    --surface2: #1c1c1f;
    --border: #2a2a2d;
    --text: #e8e8e8;
    --muted: #8b8b8f;
    --green: #3fb950;
    --green-dim: rgba(63, 185, 80, 0.15);
    --blue: #58a6ff;
    --purple: #a371f7;
    --yellow: #d29922;
    --red: #f85149;
    --cyan: #39c5cf;
    --imessage: #34c759;
    --whatsapp: #25d366;
}}

* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ 
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    background: var(--bg); 
    color: var(--text);
    min-height: 100vh;
    line-height: 1.5;
}}

.dashboard {{
    max-width: 1600px;
    margin: 0 auto;
    padding: 24px;
}}

/* Header */
.header {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 24px;
    padding-bottom: 20px;
    border-bottom: 1px solid var(--border);
}}
.header h1 {{
    font-size: 24px;
    font-weight: 600;
    display: flex;
    align-items: center;
    gap: 12px;
}}
.header h1 span {{ font-size: 28px; }}
.badges {{
    display: flex;
    gap: 12px;
}}
.badge {{
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 8px 14px;
    border-radius: 20px;
    font-size: 13px;
    font-weight: 500;
    font-family: 'JetBrains Mono', monospace;
}}
.badge.im {{ background: rgba(52, 199, 89, 0.12); color: var(--imessage); border: 1px solid rgba(52, 199, 89, 0.25); }}
.badge.wa {{ background: rgba(37, 211, 102, 0.12); color: var(--whatsapp); border: 1px solid rgba(37, 211, 102, 0.25); }}

/* Stats Row */
.stats-row {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 16px;
    margin-bottom: 24px;
}}
.stat-card {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 20px;
}}
.stat-label {{
    font-size: 12px;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.5px;
    margin-bottom: 8px;
}}
.stat-value {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 28px;
    font-weight: 600;
    color: var(--green);
}}
.stat-value.cyan {{ color: var(--cyan); }}
.stat-value.purple {{ color: var(--purple); }}
.stat-value.yellow {{ color: var(--yellow); }}

/* Filters */
.filters {{
    display: flex;
    gap: 16px;
    align-items: center;
    margin-bottom: 24px;
    flex-wrap: wrap;
}}
.search-box {{
    flex: 1;
    min-width: 200px;
    max-width: 300px;
}}
.search-box input {{
    width: 100%;
    padding: 10px 14px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    color: var(--text);
    font-size: 14px;
    outline: none;
    transition: border-color 0.2s;
}}
.search-box input:focus {{ border-color: var(--green); }}
.search-box input::placeholder {{ color: var(--muted); }}

.filter-group {{
    display: flex;
    gap: 4px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 4px;
}}
.filter-btn {{
    padding: 8px 16px;
    border: none;
    background: transparent;
    color: var(--muted);
    font-size: 13px;
    font-weight: 500;
    border-radius: 6px;
    cursor: pointer;
    transition: all 0.2s;
}}
.filter-btn:hover {{ color: var(--text); }}
.filter-btn.active {{
    background: var(--green);
    color: #000;
}}
.filter-btn.group-active {{
    background: var(--purple);
    color: #000;
}}

.selection-info {{
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 8px 16px;
    background: var(--green-dim);
    border: 1px solid rgba(63, 185, 80, 0.3);
    border-radius: 8px;
}}
.selection-info.hidden {{ display: none; }}
.selection-count {{
    font-family: 'JetBrains Mono', monospace;
    font-weight: 600;
    color: var(--green);
}}
.clear-btn {{
    padding: 4px 10px;
    background: transparent;
    border: 1px solid var(--green);
    border-radius: 4px;
    color: var(--green);
    font-size: 12px;
    cursor: pointer;
    transition: all 0.2s;
}}
.clear-btn:hover {{
    background: var(--green);
    color: #000;
}}

/* Main Grid */
.main-grid {{
    display: grid;
    grid-template-columns: 1fr 380px;
    gap: 24px;
}}

@media (max-width: 1200px) {{
    .main-grid {{ grid-template-columns: 1fr; }}
}}

/* Cards */
.card {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    overflow: hidden;
}}
.card-header {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 16px 20px;
    border-bottom: 1px solid var(--border);
}}
.card-title {{
    font-size: 14px;
    font-weight: 600;
    color: var(--text);
}}
.card-subtitle {{
    font-size: 12px;
    color: var(--muted);
}}
.card-body {{
    padding: 20px;
}}

/* Heatmap (GitHub style) */
.heatmap-container {{
    overflow-x: auto;
    padding-bottom: 8px;
}}
.heatmap {{
    display: flex;
    gap: 3px;
}}
.heatmap-column {{
    display: flex;
    flex-direction: column;
    gap: 3px;
}}
.heatmap-cell {{
    width: 12px;
    height: 12px;
    border-radius: 2px;
    background: var(--surface2);
    cursor: pointer;
    transition: transform 0.1s;
}}
.heatmap-cell:hover {{
    transform: scale(1.3);
    outline: 2px solid var(--text);
    outline-offset: 1px;
}}
.heatmap-cell.level-0 {{ background: var(--surface2); }}
.heatmap-cell.level-1 {{ background: rgba(63, 185, 80, 0.25); }}
.heatmap-cell.level-2 {{ background: rgba(63, 185, 80, 0.45); }}
.heatmap-cell.level-3 {{ background: rgba(63, 185, 80, 0.7); }}
.heatmap-cell.level-4 {{ background: var(--green); }}
.heatmap-cell.empty {{ background: transparent; pointer-events: none; }}

.heatmap-months {{
    display: flex;
    margin-bottom: 8px;
    padding-left: 32px;
    font-size: 11px;
    color: var(--muted);
}}
.heatmap-month {{
    flex: 1;
    min-width: 48px;
}}
.heatmap-days {{
    display: flex;
    flex-direction: column;
    gap: 3px;
    margin-right: 8px;
    font-size: 10px;
    color: var(--muted);
}}
.heatmap-day {{
    height: 12px;
    display: flex;
    align-items: center;
}}
.heatmap-wrapper {{
    display: flex;
}}
.heatmap-legend {{
    display: flex;
    align-items: center;
    gap: 6px;
    margin-top: 16px;
    justify-content: flex-end;
    font-size: 11px;
    color: var(--muted);
}}
.heatmap-legend .heatmap-cell {{
    cursor: default;
}}
.heatmap-legend .heatmap-cell:hover {{
    transform: none;
    outline: none;
}}

.heatmap-tooltip {{
    position: fixed;
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 8px 12px;
    font-size: 12px;
    pointer-events: none;
    z-index: 1000;
    box-shadow: 0 4px 12px rgba(0,0,0,0.4);
    display: none;
}}
.heatmap-tooltip strong {{
    color: var(--green);
    font-family: 'JetBrains Mono', monospace;
}}

/* Charts */
.charts-row {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
    margin-top: 24px;
}}
.chart-container {{
    height: 200px;
    position: relative;
}}

/* Contact List */
.list-header {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 12px 20px;
    background: var(--surface2);
    border-bottom: 1px solid var(--border);
}}
.list-tabs {{
    display: flex;
    gap: 4px;
}}
.list-tab {{
    padding: 6px 14px;
    border: none;
    background: transparent;
    color: var(--muted);
    font-size: 13px;
    font-weight: 500;
    border-radius: 6px;
    cursor: pointer;
    transition: all 0.2s;
}}
.list-tab:hover {{ color: var(--text); }}
.list-tab.active {{
    background: var(--green-dim);
    color: var(--green);
}}
.list-tab.groups.active {{
    background: rgba(163, 113, 247, 0.15);
    color: var(--purple);
}}

.contact-list {{
    max-height: 600px;
    overflow-y: auto;
}}
.contact-item {{
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 14px 20px;
    border-bottom: 1px solid var(--border);
    cursor: pointer;
    transition: background 0.15s;
}}
.contact-item:hover {{ background: var(--surface2); }}
.contact-item.selected {{
    background: var(--green-dim);
    border-left: 3px solid var(--green);
}}
.contact-item.selected.group {{
    background: rgba(163, 113, 247, 0.1);
    border-left-color: var(--purple);
}}

.contact-checkbox {{
    appearance: none;
    width: 18px;
    height: 18px;
    border: 2px solid var(--border);
    border-radius: 4px;
    cursor: pointer;
    flex-shrink: 0;
    transition: all 0.15s;
}}
.contact-checkbox:checked {{
    background: var(--green);
    border-color: var(--green);
}}
.contact-checkbox:checked::after {{
    content: '✓';
    display: flex;
    justify-content: center;
    align-items: center;
    color: #000;
    font-size: 12px;
    font-weight: 700;
}}
.group .contact-checkbox:checked {{
    background: var(--purple);
    border-color: var(--purple);
}}

.contact-rank {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 12px;
    color: var(--muted);
    width: 28px;
    text-align: center;
}}
.contact-info {{
    flex: 1;
    min-width: 0;
}}
.contact-name {{
    font-size: 14px;
    font-weight: 500;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}}
.contact-meta {{
    display: flex;
    gap: 10px;
    font-size: 12px;
    color: var(--muted);
    margin-top: 2px;
}}
.contact-platform {{
    font-size: 14px;
}}
.contact-count {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 14px;
    font-weight: 600;
    color: var(--cyan);
}}
.group .contact-count {{
    color: var(--purple);
}}
.contact-bar {{
    width: 100%;
    height: 3px;
    background: var(--surface2);
    border-radius: 2px;
    margin-top: 6px;
    overflow: hidden;
}}
.contact-bar-fill {{
    height: 100%;
    background: var(--green);
    border-radius: 2px;
    transition: width 0.3s;
}}
.group .contact-bar-fill {{
    background: var(--purple);
}}

/* Scrollbar */
::-webkit-scrollbar {{ width: 8px; height: 8px; }}
::-webkit-scrollbar-track {{ background: var(--surface); }}
::-webkit-scrollbar-thumb {{ background: var(--border); border-radius: 4px; }}
::-webkit-scrollbar-thumb:hover {{ background: var(--muted); }}

/* Empty State */
.empty-state {{
    padding: 40px 20px;
    text-align: center;
    color: var(--muted);
}}
.empty-state-icon {{
    font-size: 48px;
    margin-bottom: 16px;
}}
</style>
</head>
<body>
<div class="dashboard">
    <!-- Header -->
    <header class="header">
        <h1><span>📊</span> Texts Dashboard {year}</h1>
        <div class="badges" id="badges"></div>
    </header>
    
    <!-- Stats Row -->
    <div class="stats-row" id="statsRow"></div>
    
    <!-- Filters -->
    <div class="filters">
        <div class="search-box">
            <input type="text" id="searchInput" placeholder="Search contacts or groups...">
        </div>
        <div class="filter-group" id="platformFilter">
            <button class="filter-btn active" data-platform="all">All</button>
            <button class="filter-btn" data-platform="imessage">📱 iMessage</button>
            <button class="filter-btn" data-platform="whatsapp">💬 WhatsApp</button>
        </div>
        <div class="filter-group" id="sortFilter">
            <button class="filter-btn active" data-sort="total">Total</button>
            <button class="filter-btn" data-sort="sent">Sent</button>
            <button class="filter-btn" data-sort="received">Received</button>
        </div>
        <div class="selection-info hidden" id="selectionInfo">
            <span><span class="selection-count" id="selectionCount">0</span> selected</span>
            <button class="clear-btn" id="clearBtn">Clear All</button>
        </div>
    </div>
    
    <!-- Main Grid -->
    <div class="main-grid">
        <div class="left-column">
            <!-- Heatmap -->
            <div class="card">
                <div class="card-header">
                    <div>
                        <div class="card-title">Message Activity</div>
                        <div class="card-subtitle" id="heatmapSubtitle">All messages throughout the year</div>
                    </div>
                </div>
                <div class="card-body">
                    <div class="heatmap-container" id="heatmapContainer"></div>
                    <div class="heatmap-legend">
                        <span>Less</span>
                        <div class="heatmap-cell level-0"></div>
                        <div class="heatmap-cell level-1"></div>
                        <div class="heatmap-cell level-2"></div>
                        <div class="heatmap-cell level-3"></div>
                        <div class="heatmap-cell level-4"></div>
                        <span>More</span>
                    </div>
                </div>
            </div>
            
            <!-- Charts -->
            <div class="charts-row">
                <div class="card">
                    <div class="card-header">
                        <div class="card-title">By Hour</div>
                    </div>
                    <div class="card-body">
                        <div class="chart-container">
                            <canvas id="hourChart"></canvas>
                        </div>
                    </div>
                </div>
                <div class="card">
                    <div class="card-header">
                        <div class="card-title">By Day of Week</div>
                    </div>
                    <div class="card-body">
                        <div class="chart-container">
                            <canvas id="dayChart"></canvas>
                        </div>
                    </div>
                </div>
            </div>
        </div>
        
        <!-- Contact/Group List -->
        <div class="card">
            <div class="list-header">
                <div class="list-tabs">
                    <button class="list-tab active" data-view="contacts">Contacts</button>
                    <button class="list-tab groups" data-view="groups">Groups</button>
                    <button class="list-tab" data-view="all">All</button>
                </div>
                <span class="card-subtitle" id="listCount">0 items</span>
            </div>
            <div class="contact-list" id="contactList"></div>
        </div>
    </div>
</div>

<!-- Tooltip -->
<div class="heatmap-tooltip" id="tooltip"></div>

<script>
const D = {json_data};
const YEAR = {year};

// State
let selectedIds = new Set();
let currentPlatform = 'all';
let currentSort = 'total';
let currentView = 'contacts';
let searchQuery = '';

// Charts
let hourChart, dayChart;

// Initialize
function init() {{
    renderBadges();
    renderStats();
    renderHeatmap();
    renderList();
    initCharts();
    setupEventListeners();
}}

function renderBadges() {{
    const badges = document.getElementById('badges');
    let html = '';
    if (D.summary.has_imessage) {{
        html += `<div class="badge im">📱 ${{D.summary.imessage_total.toLocaleString()}}</div>`;
    }}
    if (D.summary.has_whatsapp) {{
        html += `<div class="badge wa">💬 ${{D.summary.whatsapp_total.toLocaleString()}}</div>`;
    }}
    badges.innerHTML = html;
}}

function renderStats() {{
    const stats = getFilteredStats();
    const row = document.getElementById('statsRow');
    row.innerHTML = `
        <div class="stat-card">
            <div class="stat-label">Total Messages</div>
            <div class="stat-value">${{stats.total.toLocaleString()}}</div>
        </div>
        <div class="stat-card">
            <div class="stat-label">Sent</div>
            <div class="stat-value cyan">${{stats.sent.toLocaleString()}}</div>
        </div>
        <div class="stat-card">
            <div class="stat-label">Received</div>
            <div class="stat-value purple">${{stats.received.toLocaleString()}}</div>
        </div>
        <div class="stat-card">
            <div class="stat-label">${{selectedIds.size > 0 ? 'Selected' : 'People'}}</div>
            <div class="stat-value yellow">${{selectedIds.size > 0 ? selectedIds.size : D.contacts.length}}</div>
        </div>
    `;
}}

function getFilteredStats() {{
    if (selectedIds.size === 0) {{
        return {{
            total: D.summary.total_messages,
            sent: D.summary.total_sent,
            received: D.summary.total_received
        }};
    }}
    
    let total = 0, sent = 0, received = 0;
    selectedIds.forEach(id => {{
        const isGroup = id.startsWith('g_');
        const idx = parseInt(id.replace('g_', '').replace('c_', ''));
        const item = isGroup ? D.group_chats[idx] : D.contacts[idx];
        if (item) {{
            total += item.total || 0;
            sent += item.sent || 0;
            received += item.received || 0;
        }}
    }});
    return {{ total, sent, received }};
}}

// Heatmap
function renderHeatmap() {{
    const container = document.getElementById('heatmapContainer');
    const dailyCounts = getDailyCounts();
    const maxCount = Math.max(...Object.values(dailyCounts), 1);
    
    // Update subtitle
    const subtitle = document.getElementById('heatmapSubtitle');
    if (selectedIds.size > 0) {{
        subtitle.textContent = `Showing ${{selectedIds.size}} selected item(s)`;
    }} else {{
        subtitle.textContent = 'All messages throughout the year';
    }}
    
    // Build calendar
    const yearStart = new Date(YEAR, 0, 1);
    const yearEnd = new Date() > new Date(YEAR, 11, 31) ? new Date(YEAR, 11, 31) : new Date();
    
    // Start from first Sunday before year start
    const firstDay = new Date(yearStart);
    firstDay.setDate(firstDay.getDate() - firstDay.getDay());
    
    // End on last Saturday after year end
    const lastDay = new Date(yearEnd);
    lastDay.setDate(lastDay.getDate() + (6 - lastDay.getDay()));
    
    // Months header
    let monthsHtml = '<div class="heatmap-months">';
    let currentMonth = -1;
    let weekCount = 0;
    let tempDate = new Date(firstDay);
    while (tempDate <= lastDay) {{
        if (tempDate.getMonth() !== currentMonth && tempDate >= yearStart && tempDate <= yearEnd) {{
            currentMonth = tempDate.getMonth();
            const monthNames = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
            monthsHtml += `<span class="heatmap-month" style="margin-left:${{weekCount > 0 ? 0 : 0}}px">${{monthNames[currentMonth]}}</span>`;
        }}
        tempDate.setDate(tempDate.getDate() + 7);
        weekCount++;
    }}
    monthsHtml += '</div>';
    
    // Days labels
    const daysHtml = `
        <div class="heatmap-days">
            <div class="heatmap-day"></div>
            <div class="heatmap-day">Mon</div>
            <div class="heatmap-day"></div>
            <div class="heatmap-day">Wed</div>
            <div class="heatmap-day"></div>
            <div class="heatmap-day">Fri</div>
            <div class="heatmap-day"></div>
        </div>
    `;
    
    // Grid
    let gridHtml = '<div class="heatmap">';
    let current = new Date(firstDay);
    
    while (current <= lastDay) {{
        gridHtml += '<div class="heatmap-column">';
        for (let i = 0; i < 7; i++) {{
            const dateStr = current.toISOString().split('T')[0];
            const count = dailyCounts[dateStr] || 0;
            const inYear = current >= yearStart && current <= yearEnd;
            
            if (!inYear) {{
                gridHtml += '<div class="heatmap-cell empty"></div>';
            }} else {{
                const level = getLevel(count, maxCount);
                const formatted = current.toLocaleDateString('en-US', {{ month: 'short', day: 'numeric', year: 'numeric' }});
                gridHtml += `<div class="heatmap-cell level-${{level}}" data-date="${{formatted}}" data-count="${{count}}"></div>`;
            }}
            current.setDate(current.getDate() + 1);
        }}
        gridHtml += '</div>';
    }}
    gridHtml += '</div>';
    
    container.innerHTML = monthsHtml + '<div class="heatmap-wrapper">' + daysHtml + gridHtml + '</div>';
    
    // Add tooltip listeners
    container.querySelectorAll('.heatmap-cell:not(.empty)').forEach(cell => {{
        cell.addEventListener('mouseenter', showTooltip);
        cell.addEventListener('mouseleave', hideTooltip);
    }});
}}

function getDailyCounts() {{
    const counts = {{}};
    
    if (selectedIds.size === 0) {{
        // All messages
        D.messages.forEach(msg => {{
            const date = new Date(msg[0] * 1000).toISOString().split('T')[0];
            counts[date] = (counts[date] || 0) + 1;
        }});
    }} else {{
        // Filter by selected
        const contactIndices = new Set();
        const groupIndices = new Set();
        
        selectedIds.forEach(id => {{
            if (id.startsWith('g_')) {{
                groupIndices.add(parseInt(id.replace('g_', '')) + D.contacts.length);
            }} else {{
                contactIndices.add(parseInt(id.replace('c_', '')));
            }}
        }});
        
        D.messages.forEach(msg => {{
            const [ts, idx, isSent, isGroup] = msg;
            if (isGroup && groupIndices.has(idx)) {{
                const date = new Date(ts * 1000).toISOString().split('T')[0];
                counts[date] = (counts[date] || 0) + 1;
            }} else if (!isGroup && contactIndices.has(idx)) {{
                const date = new Date(ts * 1000).toISOString().split('T')[0];
                counts[date] = (counts[date] || 0) + 1;
            }}
        }});
    }}
    
    return counts;
}}

function getLevel(count, max) {{
    if (count === 0) return 0;
    const ratio = count / max;
    if (ratio <= 0.25) return 1;
    if (ratio <= 0.5) return 2;
    if (ratio <= 0.75) return 3;
    return 4;
}}

function showTooltip(e) {{
    const tooltip = document.getElementById('tooltip');
    const count = e.target.dataset.count;
    const date = e.target.dataset.date;
    tooltip.innerHTML = `<strong>${{count}}</strong> messages on ${{date}}`;
    tooltip.style.display = 'block';
    tooltip.style.left = (e.pageX + 10) + 'px';
    tooltip.style.top = (e.pageY - 30) + 'px';
}}

function hideTooltip() {{
    document.getElementById('tooltip').style.display = 'none';
}}

// Charts
function initCharts() {{
    const hourCtx = document.getElementById('hourChart').getContext('2d');
    const dayCtx = document.getElementById('dayChart').getContext('2d');
    
    const chartOptions = {{
        responsive: true,
        maintainAspectRatio: false,
        plugins: {{
            legend: {{ display: false }}
        }},
        scales: {{
            x: {{
                grid: {{ color: 'rgba(255,255,255,0.05)' }},
                ticks: {{ color: '#8b8b8f', font: {{ size: 10 }} }}
            }},
            y: {{
                grid: {{ color: 'rgba(255,255,255,0.05)' }},
                ticks: {{ color: '#8b8b8f', font: {{ size: 10 }} }}
            }}
        }}
    }};
    
    hourChart = new Chart(hourCtx, {{
        type: 'bar',
        data: {{
            labels: Array.from({{length: 24}}, (_, i) => i.toString().padStart(2, '0')),
            datasets: [{{
                data: getHourlyCounts(),
                backgroundColor: 'rgba(63, 185, 80, 0.6)',
                borderColor: '#3fb950',
                borderWidth: 1,
                borderRadius: 4
            }}]
        }},
        options: chartOptions
    }});
    
    dayChart = new Chart(dayCtx, {{
        type: 'bar',
        data: {{
            labels: ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'],
            datasets: [{{
                data: getDayOfWeekCounts(),
                backgroundColor: 'rgba(88, 166, 255, 0.6)',
                borderColor: '#58a6ff',
                borderWidth: 1,
                borderRadius: 4
            }}]
        }},
        options: chartOptions
    }});
}}

function getHourlyCounts() {{
    const counts = Array(24).fill(0);
    
    if (selectedIds.size === 0) {{
        D.messages.forEach(msg => {{
            const hour = new Date(msg[0] * 1000).getHours();
            counts[hour]++;
        }});
    }} else {{
        const contactIndices = new Set();
        const groupIndices = new Set();
        
        selectedIds.forEach(id => {{
            if (id.startsWith('g_')) {{
                groupIndices.add(parseInt(id.replace('g_', '')) + D.contacts.length);
            }} else {{
                contactIndices.add(parseInt(id.replace('c_', '')));
            }}
        }});
        
        D.messages.forEach(msg => {{
            const [ts, idx, isSent, isGroup] = msg;
            if ((isGroup && groupIndices.has(idx)) || (!isGroup && contactIndices.has(idx))) {{
                const hour = new Date(ts * 1000).getHours();
                counts[hour]++;
            }}
        }});
    }}
    
    return counts;
}}

function getDayOfWeekCounts() {{
    const counts = Array(7).fill(0);
    
    if (selectedIds.size === 0) {{
        D.messages.forEach(msg => {{
            const day = new Date(msg[0] * 1000).getDay();
            counts[day]++;
        }});
    }} else {{
        const contactIndices = new Set();
        const groupIndices = new Set();
        
        selectedIds.forEach(id => {{
            if (id.startsWith('g_')) {{
                groupIndices.add(parseInt(id.replace('g_', '')) + D.contacts.length);
            }} else {{
                contactIndices.add(parseInt(id.replace('c_', '')));
            }}
        }});
        
        D.messages.forEach(msg => {{
            const [ts, idx, isSent, isGroup] = msg;
            if ((isGroup && groupIndices.has(idx)) || (!isGroup && contactIndices.has(idx))) {{
                const day = new Date(ts * 1000).getDay();
                counts[day]++;
            }}
        }});
    }}
    
    return counts;
}}

function updateCharts() {{
    hourChart.data.datasets[0].data = getHourlyCounts();
    dayChart.data.datasets[0].data = getDayOfWeekCounts();
    hourChart.update('none');
    dayChart.update('none');
}}

// Contact/Group List
function renderList() {{
    const list = document.getElementById('contactList');
    const items = getFilteredItems();
    
    // Update count
    document.getElementById('listCount').textContent = `${{items.length}} items`;
    
    if (items.length === 0) {{
        list.innerHTML = `
            <div class="empty-state">
                <div class="empty-state-icon">🔍</div>
                <div>No items found</div>
            </div>
        `;
        return;
    }}
    
    const maxTotal = Math.max(...items.map(i => i.item.total));
    
    list.innerHTML = items.map(({{ item, type, idx }}, i) => {{
        const id = type === 'group' ? `g_${{idx}}` : `c_${{idx}}`;
        const isSelected = selectedIds.has(id);
        const platform = item.platform === 'imessage' ? '📱' : '💬';
        const barWidth = (item.total / maxTotal * 100).toFixed(1);
        
        return `
            <div class="contact-item ${{isSelected ? 'selected' : ''}} ${{type === 'group' ? 'group' : ''}}" data-id="${{id}}">
                <input type="checkbox" class="contact-checkbox" ${{isSelected ? 'checked' : ''}}>
                <span class="contact-rank">${{i + 1}}</span>
                <div class="contact-info">
                    <div class="contact-name">${{escapeHtml(item.name)}}</div>
                    <div class="contact-meta">
                        <span class="contact-platform">${{platform}}</span>
                        <span>↑${{item.sent?.toLocaleString() || 0}}</span>
                        <span>↓${{item.received?.toLocaleString() || 0}}</span>
                        ${{type === 'group' && item.members ? `<span>👥${{item.members}}</span>` : ''}}
                    </div>
                    <div class="contact-bar">
                        <div class="contact-bar-fill" style="width:${{barWidth}}%"></div>
                    </div>
                </div>
                <span class="contact-count">${{item.total.toLocaleString()}}</span>
            </div>
        `;
    }}).join('');
}}

function getFilteredItems() {{
    let items = [];
    
    // Add contacts
    if (currentView === 'contacts' || currentView === 'all') {{
        D.contacts.forEach((c, i) => {{
            if (currentPlatform !== 'all' && c.platform !== currentPlatform) return;
            if (searchQuery && !c.name.toLowerCase().includes(searchQuery)) return;
            items.push({{ item: c, type: 'contact', idx: i }});
        }});
    }}
    
    // Add groups
    if (currentView === 'groups' || currentView === 'all') {{
        D.group_chats.forEach((g, i) => {{
            if (currentPlatform !== 'all' && g.platform !== currentPlatform) return;
            if (searchQuery && !g.name.toLowerCase().includes(searchQuery)) return;
            items.push({{ item: g, type: 'group', idx: i }});
        }});
    }}
    
    // Sort
    items.sort((a, b) => b.item[currentSort] - a.item[currentSort]);
    
    return items;
}}

function escapeHtml(str) {{
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}}

// Selection
function toggleSelection(id) {{
    if (selectedIds.has(id)) {{
        selectedIds.delete(id);
    }} else {{
        selectedIds.add(id);
    }}
    updateSelection();
}}

function clearSelection() {{
    selectedIds.clear();
    updateSelection();
}}

function updateSelection() {{
    // Update UI
    const info = document.getElementById('selectionInfo');
    const count = document.getElementById('selectionCount');
    
    if (selectedIds.size > 0) {{
        info.classList.remove('hidden');
        count.textContent = selectedIds.size;
    }} else {{
        info.classList.add('hidden');
    }}
    
    // Update stats, heatmap, charts, list
    renderStats();
    renderHeatmap();
    updateCharts();
    renderList();
}}

// Event Listeners
function setupEventListeners() {{
    // Platform filter
    document.querySelectorAll('#platformFilter .filter-btn').forEach(btn => {{
        btn.addEventListener('click', () => {{
            document.querySelectorAll('#platformFilter .filter-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            currentPlatform = btn.dataset.platform;
            renderList();
        }});
    }});
    
    // Sort filter
    document.querySelectorAll('#sortFilter .filter-btn').forEach(btn => {{
        btn.addEventListener('click', () => {{
            document.querySelectorAll('#sortFilter .filter-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            currentSort = btn.dataset.sort;
            renderList();
        }});
    }});
    
    // View tabs (contacts/groups/all)
    document.querySelectorAll('.list-tab').forEach(tab => {{
        tab.addEventListener('click', () => {{
            document.querySelectorAll('.list-tab').forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            currentView = tab.dataset.view;
            renderList();
        }});
    }});
    
    // Search
    document.getElementById('searchInput').addEventListener('input', (e) => {{
        searchQuery = e.target.value.toLowerCase();
        renderList();
    }});
    
    // Clear selection
    document.getElementById('clearBtn').addEventListener('click', clearSelection);
    
    // Contact/group click
    document.getElementById('contactList').addEventListener('click', (e) => {{
        const item = e.target.closest('.contact-item');
        if (item) {{
            toggleSelection(item.dataset.id);
        }}
    }});
}}

// Init
init();
</script>
</body>
</html>'''
    
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)
    return output_path

def main():
    parser = argparse.ArgumentParser(description='Generate enhanced texts dashboard')
    parser.add_argument('--use-2024', action='store_true', help='Use 2024 data instead of 2025')
    parser.add_argument('-o', '--output', default='texts_dashboard.html', help='Output file path')
    args = parser.parse_args()
    
    year = '2024' if args.use_2024 else '2025'
    ts_im = 1704067200 if year == '2024' else 1735689600
    ts_wa = 725846400 if year == '2024' else 757382400
    
    print(f"\n📊 Texts Dashboard Enhanced - {year}")
    print("=" * 50)
    
    print("Checking database access...")
    has_im, has_wa = check_access()
    platforms = []
    if has_im: platforms.append("iMessage")
    if has_wa: platforms.append("WhatsApp")
    print(f"✓ Found: {', '.join(platforms)}")
    
    im_data = {'contacts': [], 'group_chats': [], 'messages': [], 'daily_counts': {}, 'hourly_counts': {}, 'day_of_week_counts': {}}
    wa_data = {'contacts': [], 'group_chats': [], 'messages': [], 'daily_counts': {}, 'hourly_counts': {}, 'day_of_week_counts': {}}
    
    if has_im:
        print("Analyzing iMessage...")
        im_contacts = extract_imessage_contacts()
        im_data = get_all_imessage_data(ts_im, im_contacts)
        print(f"✓ {len(im_data['contacts'])} contacts, {len(im_data['group_chats'])} groups")
    
    if has_wa:
        print("Analyzing WhatsApp...")
        wa_contacts = extract_whatsapp_contacts()
        wa_data = get_all_whatsapp_data(ts_wa, wa_contacts)
        print(f"✓ {len(wa_data['contacts'])} contacts, {len(wa_data['group_chats'])} groups")
    
    print("Merging data...")
    data = merge_data(im_data, wa_data, has_im, has_wa)
    print(f"✓ {len(data['messages']):,} total message timestamps")
    
    print("Generating dashboard...")
    output = generate_html(data, year, args.output)
    print(f"✓ {data['summary']['total_messages']:,} total messages")
    print(f"✓ Saved to {output}")
    
    subprocess.run(['open', output])
    print("\n✨ Done!")

if __name__ == '__main__':
    main()
