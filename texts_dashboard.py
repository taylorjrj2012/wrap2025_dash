#!/usr/bin/env python3
"""
Texts Dashboard - All your messaging data in one interactive dashboard
Usage: python3 texts_dashboard.py [--use-2024]
"""

import sqlite3, os, sys, re, subprocess, argparse, glob, json
from datetime import datetime, timedelta
from collections import defaultdict

# Database paths
IMESSAGE_DB = os.path.expanduser("~/Library/Messages/chat.db")
ADDRESSBOOK_DIR = os.path.expanduser("~/Library/Application Support/AddressBook")
WHATSAPP_PATHS = [
    os.path.expanduser("~/Library/Group Containers/group.net.whatsapp.WhatsApp.shared/ChatStorage.sqlite"),
    os.path.expanduser("~/Library/Containers/com.whatsapp/Data/Library/Application Support/WhatsApp/ChatStorage.sqlite"),
    os.path.expanduser("~/Library/Containers/desktop.WhatsApp/Data/Library/Application Support/WhatsApp/ChatStorage.sqlite"),
]

WHATSAPP_DB = None
COCOA_OFFSET = 978307200

def normalize_phone(phone):
    if not phone: return None
    digits = re.sub(r'\D', '', str(phone))
    if len(digits) == 11 and digits.startswith('1'):
        digits = digits[1:]
    elif len(digits) > 10:
        return digits
    return digits[-10:] if len(digits) >= 10 else (digits if len(digits) >= 7 else None)

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
    if len(digits) == 11 and digits.startswith('1'):
        if digits[1:] in contacts: return contacts[digits[1:]]
    if len(digits) >= 10 and digits[-10:] in contacts: return contacts[digits[-10:]]
    if len(digits) >= 7 and digits[-7:] in contacts: return contacts[digits[-7:]]
    return handle

def get_name_whatsapp(jid, contacts):
    if not jid: return "Unknown"
    if jid in contacts: return contacts[jid]
    if '@' in jid:
        phone = jid.split('@')[0]
        if len(phone) == 10: return f"({phone[:3]}) {phone[3:6]}-{phone[6:]}"
        elif len(phone) == 11 and phone.startswith('1'): return f"+1 ({phone[1:4]}) {phone[4:7]}-{phone[7:]}"
        return f"+{phone}"
    return jid

def find_whatsapp_database():
    for path in WHATSAPP_PATHS:
        if os.path.exists(path): return path
    return None

def check_access():
    global WHATSAPP_DB
    has_imessage = False
    has_whatsapp = False
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
    """Extract ALL iMessage data for the dashboard."""
    data = {
        'contacts': [],
        'daily_counts': {},
        'hourly_counts': defaultdict(int),
        'day_of_week_counts': defaultdict(int),
        'messages': [],
        'group_chats': [],
        'response_times': []
    }
    
    # Get ALL contacts with their stats
    one_on_one_cte = """
        WITH chat_participants AS (
            SELECT chat_id, COUNT(*) as participant_count FROM chat_handle_join GROUP BY chat_id
        ),
        one_on_one_messages AS (
            SELECT m.ROWID as msg_id FROM message m
            JOIN chat_message_join cmj ON m.ROWID = cmj.message_id
            JOIN chat_participants cp ON cmj.chat_id = cp.chat_id
            WHERE cp.participant_count = 1
        )
    """
    
    # All contacts with detailed stats
    rows = q_imessage(f"""{one_on_one_cte}
        SELECT h.id,
            COUNT(*) as total,
            SUM(CASE WHEN m.is_from_me=1 THEN 1 ELSE 0 END) as sent,
            SUM(CASE WHEN m.is_from_me=0 THEN 1 ELSE 0 END) as received,
            MIN(m.date/1000000000+978307200) as first_msg,
            MAX(m.date/1000000000+978307200) as last_msg,
            SUM(CASE WHEN CAST(strftime('%H',datetime((m.date/1000000000+978307200),'unixepoch','localtime')) AS INT)<5 THEN 1 ELSE 0 END) as late_night
        FROM message m 
        JOIN handle h ON m.handle_id=h.ROWID
        WHERE (m.date/1000000000+978307200)>{ts_start}
        AND m.ROWID IN (SELECT msg_id FROM one_on_one_messages)
        AND NOT (LENGTH(REPLACE(REPLACE(h.id, '+', ''), '-', '')) BETWEEN 5 AND 6 
            AND REPLACE(REPLACE(h.id, '+', ''), '-', '') GLOB '[0-9]*')
        GROUP BY h.id
        ORDER BY total DESC
    """)
    
    for row in rows:
        handle, total, sent, received, first_msg, last_msg, late_night = row
        name = get_name_imessage(handle, contacts)
        ratio = sent / max(received, 1)
        data['contacts'].append({
            'id': handle,
            'name': name,
            'total': total,
            'sent': sent,
            'received': received,
            'first_msg': datetime.fromtimestamp(first_msg).isoformat() if first_msg else None,
            'last_msg': datetime.fromtimestamp(last_msg).isoformat() if last_msg else None,
            'late_night': late_night,
            'ratio': round(ratio, 2),
            'platform': 'imessage'
        })
    
    # Daily message counts
    rows = q_imessage(f"""
        SELECT DATE(datetime((date/1000000000+978307200),'unixepoch','localtime')) as d,
            COUNT(*) as total,
            SUM(CASE WHEN is_from_me=1 THEN 1 ELSE 0 END) as sent,
            SUM(CASE WHEN is_from_me=0 THEN 1 ELSE 0 END) as received
        FROM message WHERE (date/1000000000+978307200)>{ts_start}
        GROUP BY d ORDER BY d
    """)
    for date, total, sent, received in rows:
        if date not in data['daily_counts']:
            data['daily_counts'][date] = {'total': 0, 'sent': 0, 'received': 0}
        data['daily_counts'][date]['total'] += total
        data['daily_counts'][date]['sent'] += sent
        data['daily_counts'][date]['received'] += received
    
    # Hourly distribution
    rows = q_imessage(f"""
        SELECT CAST(strftime('%H',datetime((date/1000000000+978307200),'unixepoch','localtime')) AS INT) as h,
            COUNT(*) as c
        FROM message WHERE (date/1000000000+978307200)>{ts_start}
        GROUP BY h
    """)
    for hour, count in rows:
        data['hourly_counts'][hour] += count
    
    # Day of week distribution
    days = ['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday']
    rows = q_imessage(f"""
        SELECT CAST(strftime('%w',datetime((date/1000000000+978307200),'unixepoch','localtime')) AS INT) as d,
            COUNT(*) as c
        FROM message WHERE (date/1000000000+978307200)>{ts_start}
        GROUP BY d
    """)
    for day_num, count in rows:
        data['day_of_week_counts'][days[day_num]] += count
    
    # Group chats
    rows = q_imessage(f"""
        WITH chat_participants AS (
            SELECT chat_id, COUNT(*) as participant_count FROM chat_handle_join GROUP BY chat_id
        ),
        group_chats AS (
            SELECT chat_id FROM chat_participants WHERE participant_count >= 2
        )
        SELECT c.ROWID, c.display_name, COUNT(*) as msg_count,
            (SELECT COUNT(*) FROM chat_handle_join WHERE chat_id = c.ROWID) as members,
            SUM(CASE WHEN m.is_from_me=1 THEN 1 ELSE 0 END) as sent
        FROM chat c
        JOIN chat_message_join cmj ON c.ROWID = cmj.chat_id
        JOIN message m ON cmj.message_id = m.ROWID
        WHERE c.ROWID IN (SELECT chat_id FROM group_chats)
        AND (m.date/1000000000+978307200)>{ts_start}
        GROUP BY c.ROWID
        ORDER BY msg_count DESC
    """)
    for chat_id, name, msg_count, members, sent in rows:
        data['group_chats'].append({
            'id': chat_id,
            'name': name or f"Group ({members} people)",
            'total': msg_count,
            'members': members,
            'sent': sent,
            'platform': 'imessage'
        })
    
    return data

def get_all_whatsapp_data(ts_start, contacts):
    """Extract ALL WhatsApp data for the dashboard."""
    data = {
        'contacts': [],
        'daily_counts': {},
        'hourly_counts': defaultdict(int),
        'day_of_week_counts': defaultdict(int),
        'messages': [],
        'group_chats': [],
        'response_times': []
    }
    
    one_on_one_cte = """
        WITH dm_sessions AS (
            SELECT Z_PK, ZCONTACTJID FROM ZWACHATSESSION WHERE ZSESSIONTYPE = 0
        ),
        dm_messages AS (
            SELECT m.Z_PK as msg_id, m.ZCHATSESSION, s.ZCONTACTJID
            FROM ZWAMESSAGE m JOIN dm_sessions s ON m.ZCHATSESSION = s.Z_PK
        )
    """
    
    # All contacts
    rows = q_whatsapp(f"""{one_on_one_cte}
        SELECT dm.ZCONTACTJID,
            COUNT(*) as total,
            SUM(CASE WHEN m.ZISFROMME=1 THEN 1 ELSE 0 END) as sent,
            SUM(CASE WHEN m.ZISFROMME=0 THEN 1 ELSE 0 END) as received,
            MIN(m.ZMESSAGEDATE) as first_msg,
            MAX(m.ZMESSAGEDATE) as last_msg,
            SUM(CASE WHEN CAST(strftime('%H',datetime(m.ZMESSAGEDATE+{COCOA_OFFSET},'unixepoch','localtime')) AS INT)<5 THEN 1 ELSE 0 END) as late_night
        FROM ZWAMESSAGE m
        JOIN dm_messages dm ON m.Z_PK = dm.msg_id
        WHERE m.ZMESSAGEDATE>{ts_start}
        GROUP BY dm.ZCONTACTJID
        ORDER BY total DESC
    """)
    
    for row in rows:
        jid, total, sent, received, first_msg, last_msg, late_night = row
        name = get_name_whatsapp(jid, contacts)
        ratio = sent / max(received, 1)
        data['contacts'].append({
            'id': jid,
            'name': name,
            'total': total,
            'sent': sent,
            'received': received,
            'first_msg': datetime.fromtimestamp(first_msg + COCOA_OFFSET).isoformat() if first_msg else None,
            'last_msg': datetime.fromtimestamp(last_msg + COCOA_OFFSET).isoformat() if last_msg else None,
            'late_night': late_night,
            'ratio': round(ratio, 2),
            'platform': 'whatsapp'
        })
    
    # Daily counts
    rows = q_whatsapp(f"""
        SELECT DATE(datetime(ZMESSAGEDATE+{COCOA_OFFSET},'unixepoch','localtime')) as d,
            COUNT(*) as total,
            SUM(CASE WHEN ZISFROMME=1 THEN 1 ELSE 0 END) as sent,
            SUM(CASE WHEN ZISFROMME=0 THEN 1 ELSE 0 END) as received
        FROM ZWAMESSAGE WHERE ZMESSAGEDATE>{ts_start}
        GROUP BY d ORDER BY d
    """)
    for date, total, sent, received in rows:
        if date not in data['daily_counts']:
            data['daily_counts'][date] = {'total': 0, 'sent': 0, 'received': 0}
        data['daily_counts'][date]['total'] += total
        data['daily_counts'][date]['sent'] += sent
        data['daily_counts'][date]['received'] += received
    
    # Hourly distribution
    rows = q_whatsapp(f"""
        SELECT CAST(strftime('%H',datetime(ZMESSAGEDATE+{COCOA_OFFSET},'unixepoch','localtime')) AS INT) as h,
            COUNT(*) as c
        FROM ZWAMESSAGE WHERE ZMESSAGEDATE>{ts_start}
        GROUP BY h
    """)
    for hour, count in rows:
        data['hourly_counts'][hour] += count
    
    # Day of week
    days = ['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday']
    rows = q_whatsapp(f"""
        SELECT CAST(strftime('%w',datetime(ZMESSAGEDATE+{COCOA_OFFSET},'unixepoch','localtime')) AS INT) as d,
            COUNT(*) as c
        FROM ZWAMESSAGE WHERE ZMESSAGEDATE>{ts_start}
        GROUP BY d
    """)
    for day_num, count in rows:
        data['day_of_week_counts'][days[day_num]] += count
    
    # Group chats
    rows = q_whatsapp(f"""
        WITH group_sessions AS (
            SELECT Z_PK, ZPARTNERNAME FROM ZWACHATSESSION WHERE ZSESSIONTYPE = 1
        )
        SELECT s.Z_PK, s.ZPARTNERNAME, COUNT(*) as msg_count,
            SUM(CASE WHEN m.ZISFROMME=1 THEN 1 ELSE 0 END) as sent
        FROM ZWAMESSAGE m
        JOIN group_sessions s ON m.ZCHATSESSION = s.Z_PK
        WHERE m.ZMESSAGEDATE>{ts_start}
        GROUP BY s.Z_PK
        ORDER BY msg_count DESC
    """)
    for chat_id, name, msg_count, sent in rows:
        data['group_chats'].append({
            'id': chat_id,
            'name': name or "Unnamed Group",
            'total': msg_count,
            'members': 0,
            'sent': sent,
            'platform': 'whatsapp'
        })
    
    return data

def merge_all_data(imessage_data, whatsapp_data, has_imessage, has_whatsapp):
    """Merge all data from both platforms."""
    merged = {
        'contacts': [],
        'daily_counts': {},
        'hourly_counts': {str(i): 0 for i in range(24)},
        'day_of_week_counts': {},
        'group_chats': [],
        'summary': {}
    }
    
    # Merge contacts
    if has_imessage:
        merged['contacts'].extend(imessage_data['contacts'])
    if has_whatsapp:
        merged['contacts'].extend(whatsapp_data['contacts'])
    
    # Sort by total messages
    merged['contacts'].sort(key=lambda x: -x['total'])
    
    # Merge daily counts
    all_dates = set()
    if has_imessage:
        all_dates.update(imessage_data['daily_counts'].keys())
    if has_whatsapp:
        all_dates.update(whatsapp_data['daily_counts'].keys())
    
    for date in sorted(all_dates):
        im = imessage_data['daily_counts'].get(date, {'total': 0, 'sent': 0, 'received': 0}) if has_imessage else {'total': 0, 'sent': 0, 'received': 0}
        wa = whatsapp_data['daily_counts'].get(date, {'total': 0, 'sent': 0, 'received': 0}) if has_whatsapp else {'total': 0, 'sent': 0, 'received': 0}
        merged['daily_counts'][date] = {
            'total': im['total'] + wa['total'],
            'sent': im['sent'] + wa['sent'],
            'received': im['received'] + wa['received'],
            'imessage': im['total'],
            'whatsapp': wa['total']
        }
    
    # Merge hourly counts
    for hour in range(24):
        im = imessage_data['hourly_counts'].get(hour, 0) if has_imessage else 0
        wa = whatsapp_data['hourly_counts'].get(hour, 0) if has_whatsapp else 0
        merged['hourly_counts'][str(hour)] = im + wa
    
    # Merge day of week
    days = ['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday']
    for day in days:
        im = imessage_data['day_of_week_counts'].get(day, 0) if has_imessage else 0
        wa = whatsapp_data['day_of_week_counts'].get(day, 0) if has_whatsapp else 0
        merged['day_of_week_counts'][day] = im + wa
    
    # Merge group chats
    if has_imessage:
        merged['group_chats'].extend(imessage_data['group_chats'])
    if has_whatsapp:
        merged['group_chats'].extend(whatsapp_data['group_chats'])
    merged['group_chats'].sort(key=lambda x: -x['total'])
    
    # Calculate summary stats
    total_msgs = sum(c['total'] for c in merged['contacts'])
    total_sent = sum(c['sent'] for c in merged['contacts'])
    total_received = sum(c['received'] for c in merged['contacts'])
    
    im_total = sum(c['total'] for c in merged['contacts'] if c['platform'] == 'imessage')
    wa_total = sum(c['total'] for c in merged['contacts'] if c['platform'] == 'whatsapp')
    
    merged['summary'] = {
        'total_messages': total_msgs,
        'total_sent': total_sent,
        'total_received': total_received,
        'total_contacts': len(merged['contacts']),
        'total_groups': len(merged['group_chats']),
        'imessage_total': im_total,
        'whatsapp_total': wa_total,
        'has_imessage': has_imessage,
        'has_whatsapp': has_whatsapp
    }
    
    return merged

def generate_dashboard_html(data, year, output_path):
    """Generate the interactive dashboard HTML."""
    json_data = json.dumps(data, default=str)
    
    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Texts Dashboard {year}</title>
    <script src="https://unpkg.com/react@18/umd/react.production.min.js"></script>
    <script src="https://unpkg.com/react-dom@18/umd/react-dom.production.min.js"></script>
    <script src="https://unpkg.com/@babel/standalone/babel.min.js"></script>
    <script src="https://unpkg.com/recharts@2.8.0/umd/Recharts.js"></script>
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700&family=Plus+Jakarta+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {{
            --bg-primary: #0c0c0f;
            --bg-secondary: #151519;
            --bg-tertiary: #1c1c22;
            --bg-hover: #242430;
            --border: #2a2a36;
            --text-primary: #f5f5f7;
            --text-secondary: #8e8e93;
            --text-muted: #636366;
            --accent-green: #32d74b;
            --accent-blue: #0a84ff;
            --accent-purple: #bf5af2;
            --accent-orange: #ff9f0a;
            --accent-pink: #ff375f;
            --accent-cyan: #64d2ff;
            --imessage: #32d74b;
            --whatsapp: #25d366;
            --radius-sm: 6px;
            --radius-md: 10px;
            --radius-lg: 16px;
            --font-sans: 'Plus Jakarta Sans', -apple-system, sans-serif;
            --font-mono: 'JetBrains Mono', monospace;
        }}
        
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        
        body {{
            font-family: var(--font-sans);
            background: var(--bg-primary);
            color: var(--text-primary);
            min-height: 100vh;
            line-height: 1.5;
        }}
        
        .dashboard {{
            max-width: 1600px;
            margin: 0 auto;
            padding: 32px 24px;
        }}
        
        .header {{
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            margin-bottom: 32px;
            padding-bottom: 24px;
            border-bottom: 1px solid var(--border);
        }}
        
        .header-left h1 {{
            font-size: 28px;
            font-weight: 700;
            letter-spacing: -0.5px;
            margin-bottom: 4px;
        }}
        
        .header-left p {{
            color: var(--text-secondary);
            font-size: 14px;
        }}
        
        .header-right {{
            display: flex;
            gap: 12px;
        }}
        
        .platform-badge {{
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 8px 14px;
            background: var(--bg-secondary);
            border: 1px solid var(--border);
            border-radius: var(--radius-md);
            font-family: var(--font-mono);
            font-size: 13px;
        }}
        
        .platform-badge.imessage {{ color: var(--imessage); }}
        .platform-badge.whatsapp {{ color: var(--whatsapp); }}
        
        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 16px;
            margin-bottom: 32px;
        }}
        
        .stat-card {{
            background: var(--bg-secondary);
            border: 1px solid var(--border);
            border-radius: var(--radius-lg);
            padding: 20px;
        }}
        
        .stat-label {{
            font-size: 12px;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 8px;
        }}
        
        .stat-value {{
            font-family: var(--font-mono);
            font-size: 32px;
            font-weight: 600;
            letter-spacing: -1px;
        }}
        
        .stat-value.gradient {{
            background: linear-gradient(135deg, var(--imessage), var(--whatsapp));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }}
        
        .stat-subtext {{
            font-size: 13px;
            color: var(--text-secondary);
            margin-top: 4px;
        }}
        
        .filters {{
            display: flex;
            gap: 12px;
            margin-bottom: 24px;
            flex-wrap: wrap;
        }}
        
        .filter-group {{
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        
        .filter-label {{
            font-size: 13px;
            color: var(--text-secondary);
        }}
        
        .search-input {{
            background: var(--bg-secondary);
            border: 1px solid var(--border);
            border-radius: var(--radius-md);
            padding: 10px 14px;
            color: var(--text-primary);
            font-family: var(--font-sans);
            font-size: 14px;
            min-width: 240px;
            outline: none;
            transition: border-color 0.2s;
        }}
        
        .search-input:focus {{
            border-color: var(--accent-blue);
        }}
        
        .search-input::placeholder {{
            color: var(--text-muted);
        }}
        
        .toggle-group {{
            display: flex;
            background: var(--bg-secondary);
            border: 1px solid var(--border);
            border-radius: var(--radius-md);
            overflow: hidden;
        }}
        
        .toggle-btn {{
            padding: 8px 16px;
            font-size: 13px;
            font-weight: 500;
            background: transparent;
            border: none;
            color: var(--text-secondary);
            cursor: pointer;
            transition: all 0.2s;
        }}
        
        .toggle-btn:hover {{
            color: var(--text-primary);
            background: var(--bg-hover);
        }}
        
        .toggle-btn.active {{
            background: var(--accent-blue);
            color: white;
        }}
        
        .main-grid {{
            display: grid;
            grid-template-columns: 1fr 400px;
            gap: 24px;
        }}
        
        @media (max-width: 1200px) {{
            .main-grid {{
                grid-template-columns: 1fr;
            }}
        }}
        
        .card {{
            background: var(--bg-secondary);
            border: 1px solid var(--border);
            border-radius: var(--radius-lg);
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
            font-size: 15px;
            font-weight: 600;
        }}
        
        .card-body {{
            padding: 20px;
        }}
        
        .chart-container {{
            height: 300px;
            width: 100%;
        }}
        
        .contacts-list {{
            max-height: 500px;
            overflow-y: auto;
        }}
        
        .contact-row {{
            display: flex;
            align-items: center;
            padding: 12px 16px;
            border-bottom: 1px solid var(--border);
            cursor: pointer;
            transition: background 0.15s;
        }}
        
        .contact-row:hover {{
            background: var(--bg-hover);
        }}
        
        .contact-row.selected {{
            background: rgba(10, 132, 255, 0.15);
            border-left: 3px solid var(--accent-blue);
        }}
        
        .contact-rank {{
            font-family: var(--font-mono);
            font-size: 12px;
            color: var(--text-muted);
            width: 32px;
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
            font-size: 12px;
            color: var(--text-secondary);
            display: flex;
            gap: 12px;
            margin-top: 2px;
        }}
        
        .contact-count {{
            font-family: var(--font-mono);
            font-size: 14px;
            font-weight: 600;
            color: var(--accent-cyan);
        }}
        
        .platform-icon {{
            font-size: 14px;
            margin-left: 8px;
        }}
        
        .contact-bar {{
            height: 4px;
            background: var(--bg-tertiary);
            border-radius: 2px;
            margin-top: 8px;
            overflow: hidden;
        }}
        
        .contact-bar-fill {{
            height: 100%;
            border-radius: 2px;
            transition: width 0.3s ease;
        }}
        
        .contact-bar-fill.sent {{
            background: var(--accent-blue);
        }}
        
        .heatmap {{
            display: grid;
            grid-template-columns: repeat(7, 1fr);
            gap: 3px;
        }}
        
        .heatmap-cell {{
            aspect-ratio: 1;
            border-radius: 3px;
            background: var(--bg-tertiary);
            cursor: pointer;
            transition: transform 0.1s;
        }}
        
        .heatmap-cell:hover {{
            transform: scale(1.1);
        }}
        
        .heatmap-cell.level-1 {{ background: rgba(50, 215, 75, 0.2); }}
        .heatmap-cell.level-2 {{ background: rgba(50, 215, 75, 0.4); }}
        .heatmap-cell.level-3 {{ background: rgba(50, 215, 75, 0.6); }}
        .heatmap-cell.level-4 {{ background: var(--accent-green); }}
        
        .tooltip {{
            position: fixed;
            background: var(--bg-primary);
            border: 1px solid var(--border);
            border-radius: var(--radius-sm);
            padding: 8px 12px;
            font-size: 12px;
            pointer-events: none;
            z-index: 1000;
            box-shadow: 0 8px 24px rgba(0,0,0,0.4);
        }}
        
        .checkbox {{
            appearance: none;
            width: 18px;
            height: 18px;
            border: 2px solid var(--border);
            border-radius: 4px;
            cursor: pointer;
            transition: all 0.15s;
            margin-right: 8px;
        }}
        
        .checkbox:checked {{
            background: var(--accent-blue);
            border-color: var(--accent-blue);
        }}
        
        .checkbox:checked::after {{
            content: '✓';
            display: flex;
            justify-content: center;
            align-items: center;
            color: white;
            font-size: 12px;
            font-weight: bold;
        }}
        
        .selected-count {{
            background: var(--accent-blue);
            color: white;
            padding: 4px 10px;
            border-radius: var(--radius-sm);
            font-size: 12px;
            font-weight: 600;
        }}
        
        .clear-btn {{
            padding: 6px 12px;
            background: transparent;
            border: 1px solid var(--border);
            border-radius: var(--radius-sm);
            color: var(--text-secondary);
            font-size: 12px;
            cursor: pointer;
            transition: all 0.15s;
        }}
        
        .clear-btn:hover {{
            border-color: var(--accent-pink);
            color: var(--accent-pink);
        }}
        
        .insight-card {{
            padding: 16px;
            background: var(--bg-tertiary);
            border-radius: var(--radius-md);
            margin-bottom: 12px;
        }}
        
        .insight-title {{
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            color: var(--text-muted);
            margin-bottom: 6px;
        }}
        
        .insight-value {{
            font-size: 18px;
            font-weight: 600;
        }}
        
        .insight-value.green {{ color: var(--accent-green); }}
        .insight-value.blue {{ color: var(--accent-blue); }}
        .insight-value.purple {{ color: var(--accent-purple); }}
        .insight-value.orange {{ color: var(--accent-orange); }}
        
        ::-webkit-scrollbar {{
            width: 8px;
            height: 8px;
        }}
        
        ::-webkit-scrollbar-track {{
            background: var(--bg-secondary);
        }}
        
        ::-webkit-scrollbar-thumb {{
            background: var(--bg-hover);
            border-radius: 4px;
        }}
        
        ::-webkit-scrollbar-thumb:hover {{
            background: var(--border);
        }}
    </style>
</head>
<body>
    <div id="root"></div>
    
    <script type="text/babel">
        const {{ useState, useMemo, useCallback }} = React;
        const {{ 
            LineChart, Line, AreaChart, Area, BarChart, Bar, 
            XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
            PieChart, Pie, Cell
        }} = Recharts;
        
        const DATA = {json_data};
        const YEAR = "{year}";
        
        const COLORS = {{
            green: '#32d74b',
            blue: '#0a84ff',
            purple: '#bf5af2',
            orange: '#ff9f0a',
            pink: '#ff375f',
            cyan: '#64d2ff',
            imessage: '#32d74b',
            whatsapp: '#25d366'
        }};
        
        function Dashboard() {{
            const [searchTerm, setSearchTerm] = useState('');
            const [selectedContacts, setSelectedContacts] = useState(new Set());
            const [platformFilter, setPlatformFilter] = useState('all');
            const [sortBy, setSortBy] = useState('total');
            
            const filteredContacts = useMemo(() => {{
                let contacts = DATA.contacts;
                
                if (platformFilter !== 'all') {{
                    contacts = contacts.filter(c => c.platform === platformFilter);
                }}
                
                if (searchTerm) {{
                    const term = searchTerm.toLowerCase();
                    contacts = contacts.filter(c => 
                        c.name.toLowerCase().includes(term)
                    );
                }}
                
                return contacts.sort((a, b) => {{
                    if (sortBy === 'total') return b.total - a.total;
                    if (sortBy === 'sent') return b.sent - a.sent;
                    if (sortBy === 'received') return b.received - a.received;
                    if (sortBy === 'ratio') return b.ratio - a.ratio;
                    return 0;
                }});
            }}, [searchTerm, platformFilter, sortBy]);
            
            const chartData = useMemo(() => {{
                const daily = Object.entries(DATA.daily_counts).map(([date, counts]) => ({{
                    date,
                    total: counts.total,
                    sent: counts.sent,
                    received: counts.received,
                    imessage: counts.imessage || 0,
                    whatsapp: counts.whatsapp || 0
                }}));
                
                // Filter to selected contacts if any selected
                if (selectedContacts.size > 0) {{
                    // For now, show aggregate - could enhance to filter by contact
                }}
                
                return daily;
            }}, [selectedContacts]);
            
            const hourlyData = useMemo(() => {{
                return Object.entries(DATA.hourly_counts).map(([hour, count]) => ({{
                    hour: parseInt(hour),
                    label: parseInt(hour) === 0 ? '12am' : 
                           parseInt(hour) < 12 ? `${{hour}}am` : 
                           parseInt(hour) === 12 ? '12pm' : 
                           `${{hour - 12}}pm`,
                    count
                }})).sort((a, b) => a.hour - b.hour);
            }}, []);
            
            const dayOfWeekData = useMemo(() => {{
                const days = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
                return days.map(day => ({{
                    day: day.slice(0, 3),
                    fullDay: day,
                    count: DATA.day_of_week_counts[day] || 0
                }}));
            }}, []);
            
            const toggleContact = useCallback((id) => {{
                setSelectedContacts(prev => {{
                    const next = new Set(prev);
                    if (next.has(id)) {{
                        next.delete(id);
                    }} else {{
                        next.add(id);
                    }}
                    return next;
                }});
            }}, []);
            
            const clearSelection = useCallback(() => {{
                setSelectedContacts(new Set());
            }}, []);
            
            const maxTotal = filteredContacts[0]?.total || 1;
            
            const selectedStats = useMemo(() => {{
                if (selectedContacts.size === 0) return null;
                const selected = DATA.contacts.filter(c => selectedContacts.has(c.id));
                return {{
                    total: selected.reduce((s, c) => s + c.total, 0),
                    sent: selected.reduce((s, c) => s + c.sent, 0),
                    received: selected.reduce((s, c) => s + c.received, 0),
                    count: selected.length
                }};
            }}, [selectedContacts]);
            
            return (
                <div className="dashboard">
                    <header className="header">
                        <div className="header-left">
                            <h1>📱 Texts Dashboard {{YEAR}}</h1>
                            <p>Your messaging analytics across all platforms</p>
                        </div>
                        <div className="header-right">
                            {{DATA.summary.has_imessage && (
                                <div className="platform-badge imessage">
                                    📱 {{DATA.summary.imessage_total.toLocaleString()}}
                                </div>
                            )}}
                            {{DATA.summary.has_whatsapp && (
                                <div className="platform-badge whatsapp">
                                    💬 {{DATA.summary.whatsapp_total.toLocaleString()}}
                                </div>
                            )}}
                        </div>
                    </header>
                    
                    <div className="stats-grid">
                        <div className="stat-card">
                            <div className="stat-label">Total Messages</div>
                            <div className="stat-value gradient">
                                {{DATA.summary.total_messages.toLocaleString()}}
                            </div>
                            <div className="stat-subtext">
                                {{Math.round(DATA.summary.total_messages / 365)}} per day avg
                            </div>
                        </div>
                        <div className="stat-card">
                            <div className="stat-label">Sent</div>
                            <div className="stat-value" style={{{{color: COLORS.blue}}}}>
                                {{DATA.summary.total_sent.toLocaleString()}}
                            </div>
                            <div className="stat-subtext">
                                {{Math.round(DATA.summary.total_sent / DATA.summary.total_messages * 100)}}% of total
                            </div>
                        </div>
                        <div className="stat-card">
                            <div className="stat-label">Received</div>
                            <div className="stat-value" style={{{{color: COLORS.cyan}}}}>
                                {{DATA.summary.total_received.toLocaleString()}}
                            </div>
                            <div className="stat-subtext">
                                {{Math.round(DATA.summary.total_received / DATA.summary.total_messages * 100)}}% of total
                            </div>
                        </div>
                        <div className="stat-card">
                            <div className="stat-label">Contacts</div>
                            <div className="stat-value" style={{{{color: COLORS.purple}}}}>
                                {{DATA.summary.total_contacts}}
                            </div>
                            <div className="stat-subtext">
                                + {{DATA.summary.total_groups}} groups
                            </div>
                        </div>
                    </div>
                    
                    <div className="filters">
                        <input 
                            type="text"
                            className="search-input"
                            placeholder="Search contacts..."
                            value={{searchTerm}}
                            onChange={{(e) => setSearchTerm(e.target.value)}}
                        />
                        
                        <div className="toggle-group">
                            <button 
                                className={{"toggle-btn " + (platformFilter === 'all' ? 'active' : '')}}
                                onClick={{() => setPlatformFilter('all')}}
                            >All</button>
                            {{DATA.summary.has_imessage && (
                                <button 
                                    className={{"toggle-btn " + (platformFilter === 'imessage' ? 'active' : '')}}
                                    onClick={{() => setPlatformFilter('imessage')}}
                                >📱 iMessage</button>
                            )}}
                            {{DATA.summary.has_whatsapp && (
                                <button 
                                    className={{"toggle-btn " + (platformFilter === 'whatsapp' ? 'active' : '')}}
                                    onClick={{() => setPlatformFilter('whatsapp')}}
                                >💬 WhatsApp</button>
                            )}}
                        </div>
                        
                        <div className="filter-group">
                            <span className="filter-label">Sort:</span>
                            <div className="toggle-group">
                                <button 
                                    className={{"toggle-btn " + (sortBy === 'total' ? 'active' : '')}}
                                    onClick={{() => setSortBy('total')}}
                                >Total</button>
                                <button 
                                    className={{"toggle-btn " + (sortBy === 'sent' ? 'active' : '')}}
                                    onClick={{() => setSortBy('sent')}}
                                >Sent</button>
                                <button 
                                    className={{"toggle-btn " + (sortBy === 'received' ? 'active' : '')}}
                                    onClick={{() => setSortBy('received')}}
                                >Received</button>
                            </div>
                        </div>
                        
                        {{selectedContacts.size > 0 && (
                            <>
                                <span className="selected-count">
                                    {{selectedContacts.size}} selected
                                </span>
                                <button className="clear-btn" onClick={{clearSelection}}>
                                    Clear
                                </button>
                            </>
                        )}}
                    </div>
                    
                    {{selectedStats && (
                        <div className="stats-grid" style={{{{marginBottom: 24}}}}>
                            <div className="stat-card">
                                <div className="stat-label">Selected Total</div>
                                <div className="stat-value" style={{{{color: COLORS.orange}}}}>
                                    {{selectedStats.total.toLocaleString()}}
                                </div>
                            </div>
                            <div className="stat-card">
                                <div className="stat-label">Selected Sent</div>
                                <div className="stat-value" style={{{{color: COLORS.blue}}}}>
                                    {{selectedStats.sent.toLocaleString()}}
                                </div>
                            </div>
                            <div className="stat-card">
                                <div className="stat-label">Selected Received</div>
                                <div className="stat-value" style={{{{color: COLORS.cyan}}}}>
                                    {{selectedStats.received.toLocaleString()}}
                                </div>
                            </div>
                        </div>
                    )}}
                    
                    <div className="main-grid">
                        <div style={{{{display: 'flex', flexDirection: 'column', gap: 24}}}}>
                            <div className="card">
                                <div className="card-header">
                                    <span className="card-title">Message Activity</span>
                                </div>
                                <div className="card-body">
                                    <div className="chart-container">
                                        <ResponsiveContainer width="100%" height="100%">
                                            <AreaChart data={{chartData}}>
                                                <defs>
                                                    <linearGradient id="colorTotal" x1="0" y1="0" x2="0" y2="1">
                                                        <stop offset="5%" stopColor={{COLORS.green}} stopOpacity={{0.3}}/>
                                                        <stop offset="95%" stopColor={{COLORS.green}} stopOpacity={{0}}/>
                                                    </linearGradient>
                                                </defs>
                                                <CartesianGrid strokeDasharray="3 3" stroke="#2a2a36" />
                                                <XAxis 
                                                    dataKey="date" 
                                                    stroke="#636366"
                                                    tick={{{{fontSize: 11}}}}
                                                    tickFormatter={{(d) => new Date(d).toLocaleDateString('en-US', {{month: 'short', day: 'numeric'}})}}
                                                />
                                                <YAxis stroke="#636366" tick={{{{fontSize: 11}}}} />
                                                <Tooltip 
                                                    contentStyle={{{{
                                                        background: '#151519',
                                                        border: '1px solid #2a2a36',
                                                        borderRadius: 8
                                                    }}}}
                                                    labelFormatter={{(d) => new Date(d).toLocaleDateString('en-US', {{month: 'long', day: 'numeric', year: 'numeric'}})}}
                                                />
                                                <Area 
                                                    type="monotone" 
                                                    dataKey="total" 
                                                    stroke={{COLORS.green}} 
                                                    fillOpacity={{1}}
                                                    fill="url(#colorTotal)"
                                                />
                                            </AreaChart>
                                        </ResponsiveContainer>
                                    </div>
                                </div>
                            </div>
                            
                            <div style={{{{display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 24}}}}>
                                <div className="card">
                                    <div className="card-header">
                                        <span className="card-title">By Hour</span>
                                    </div>
                                    <div className="card-body">
                                        <div style={{{{height: 200}}}}>
                                            <ResponsiveContainer width="100%" height="100%">
                                                <BarChart data={{hourlyData}}>
                                                    <CartesianGrid strokeDasharray="3 3" stroke="#2a2a36" />
                                                    <XAxis 
                                                        dataKey="label" 
                                                        stroke="#636366"
                                                        tick={{{{fontSize: 10}}}}
                                                        interval={{2}}
                                                    />
                                                    <YAxis stroke="#636366" tick={{{{fontSize: 10}}}} />
                                                    <Tooltip 
                                                        contentStyle={{{{
                                                            background: '#151519',
                                                            border: '1px solid #2a2a36',
                                                            borderRadius: 8
                                                        }}}}
                                                    />
                                                    <Bar dataKey="count" fill={{COLORS.blue}} radius={{[4, 4, 0, 0]}} />
                                                </BarChart>
                                            </ResponsiveContainer>
                                        </div>
                                    </div>
                                </div>
                                
                                <div className="card">
                                    <div className="card-header">
                                        <span className="card-title">By Day</span>
                                    </div>
                                    <div className="card-body">
                                        <div style={{{{height: 200}}}}>
                                            <ResponsiveContainer width="100%" height="100%">
                                                <BarChart data={{dayOfWeekData}}>
                                                    <CartesianGrid strokeDasharray="3 3" stroke="#2a2a36" />
                                                    <XAxis dataKey="day" stroke="#636366" tick={{{{fontSize: 11}}}} />
                                                    <YAxis stroke="#636366" tick={{{{fontSize: 10}}}} />
                                                    <Tooltip 
                                                        contentStyle={{{{
                                                            background: '#151519',
                                                            border: '1px solid #2a2a36',
                                                            borderRadius: 8
                                                        }}}}
                                                        labelFormatter={{(d) => dayOfWeekData.find(x => x.day === d)?.fullDay}}
                                                    />
                                                    <Bar dataKey="count" fill={{COLORS.purple}} radius={{[4, 4, 0, 0]}} />
                                                </BarChart>
                                            </ResponsiveContainer>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                        
                        <div className="card">
                            <div className="card-header">
                                <span className="card-title">
                                    Contacts ({{filteredContacts.length}})
                                </span>
                            </div>
                            <div className="contacts-list">
                                {{filteredContacts.map((contact, idx) => (
                                    <div 
                                        key={{contact.id}}
                                        className={{"contact-row " + (selectedContacts.has(contact.id) ? 'selected' : '')}}
                                        onClick={{() => toggleContact(contact.id)}}
                                    >
                                        <input 
                                            type="checkbox"
                                            className="checkbox"
                                            checked={{selectedContacts.has(contact.id)}}
                                            onChange={{() => {{}}}}
                                        />
                                        <span className="contact-rank">#{{idx + 1}}</span>
                                        <div className="contact-info">
                                            <div className="contact-name">
                                                {{contact.name}}
                                                <span className="platform-icon">
                                                    {{contact.platform === 'imessage' ? '📱' : '💬'}}
                                                </span>
                                            </div>
                                            <div className="contact-meta">
                                                <span>↑ {{contact.sent.toLocaleString()}}</span>
                                                <span>↓ {{contact.received.toLocaleString()}}</span>
                                                {{contact.late_night > 0 && (
                                                    <span>🌙 {{contact.late_night}}</span>
                                                )}}
                                            </div>
                                            <div className="contact-bar">
                                                <div 
                                                    className="contact-bar-fill sent"
                                                    style={{{{width: `${{contact.total / maxTotal * 100}}%`}}}}
                                                />
                                            </div>
                                        </div>
                                        <span className="contact-count">
                                            {{contact.total.toLocaleString()}}
                                        </span>
                                    </div>
                                ))}}
                            </div>
                        </div>
                    </div>
                    
                    {{DATA.group_chats.length > 0 && (
                        <div className="card" style={{{{marginTop: 24}}}}>
                            <div className="card-header">
                                <span className="card-title">Group Chats ({{DATA.group_chats.length}})</span>
                            </div>
                            <div className="contacts-list" style={{{{maxHeight: 300}}}}>
                                {{DATA.group_chats.slice(0, 20).map((group, idx) => (
                                    <div key={{group.id}} className="contact-row">
                                        <span className="contact-rank">#{{idx + 1}}</span>
                                        <div className="contact-info">
                                            <div className="contact-name">
                                                {{group.name}}
                                                <span className="platform-icon">
                                                    {{group.platform === 'imessage' ? '📱' : '💬'}}
                                                </span>
                                            </div>
                                            <div className="contact-meta">
                                                <span>You sent {{group.sent.toLocaleString()}}</span>
                                                {{group.members > 0 && <span>{{group.members}} members</span>}}
                                            </div>
                                        </div>
                                        <span className="contact-count">
                                            {{group.total.toLocaleString()}}
                                        </span>
                                    </div>
                                ))}}
                            </div>
                        </div>
                    )}}
                </div>
            );
        }}
        
        ReactDOM.createRoot(document.getElementById('root')).render(<Dashboard />);
    </script>
</body>
</html>'''
    
    with open(output_path, 'w') as f:
        f.write(html)
    
    return output_path

def main():
    parser = argparse.ArgumentParser(description='Generate texts dashboard')
    parser.add_argument('--use-2024', action='store_true', help='Use 2024 data')
    parser.add_argument('-o', '--output', default='texts_dashboard.html', help='Output file')
    args = parser.parse_args()
    
    year = '2024' if args.use_2024 else '2025'
    
    # Timestamps
    if year == '2025':
        ts_imessage = 1735689600
        ts_whatsapp = 757382400
    else:
        ts_imessage = 1704067200
        ts_whatsapp = 725846400
    
    print(f"\n📊 Texts Dashboard {year}")
    print("=" * 40)
    
    print("Checking database access...")
    has_imessage, has_whatsapp = check_access()
    
    platforms = []
    if has_imessage: platforms.append("iMessage")
    if has_whatsapp: platforms.append("WhatsApp")
    print(f"✓ Found: {', '.join(platforms)}")
    
    imessage_data = {'contacts': [], 'daily_counts': {}, 'hourly_counts': {}, 'day_of_week_counts': {}, 'group_chats': []}
    whatsapp_data = {'contacts': [], 'daily_counts': {}, 'hourly_counts': {}, 'day_of_week_counts': {}, 'group_chats': []}
    
    if has_imessage:
        print("Extracting iMessage contacts...")
        imessage_contacts = extract_imessage_contacts()
        print(f"✓ Found {len(imessage_contacts)} contacts in AddressBook")
        
        print("Analyzing iMessage data...")
        imessage_data = get_all_imessage_data(ts_imessage, imessage_contacts)
        print(f"✓ Found {len(imessage_data['contacts'])} conversations")
    
    if has_whatsapp:
        print("Extracting WhatsApp contacts...")
        whatsapp_contacts = extract_whatsapp_contacts()
        print(f"✓ Found {len(whatsapp_contacts)} WhatsApp contacts")
        
        print("Analyzing WhatsApp data...")
        whatsapp_data = get_all_whatsapp_data(ts_whatsapp, whatsapp_contacts)
        print(f"✓ Found {len(whatsapp_data['contacts'])} conversations")
    
    print("Merging data...")
    merged_data = merge_all_data(imessage_data, whatsapp_data, has_imessage, has_whatsapp)
    print(f"✓ Total: {merged_data['summary']['total_messages']:,} messages")
    
    print("Generating dashboard...")
    output_path = generate_dashboard_html(merged_data, year, args.output)
    print(f"✓ Saved to {output_path}")
    
    print("\nOpening in browser...")
    subprocess.run(['open', output_path])
    
    print("\n✨ Done! Your dashboard is ready.")

if __name__ == '__main__':
    main()
