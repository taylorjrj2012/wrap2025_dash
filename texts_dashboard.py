#!/usr/bin/env python3
"""
Texts Dashboard - Your messaging data in one interactive dashboard
Usage: python3 texts_dashboard_v2.py [--use-2024]
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
        print("\n[!] ACCESS DENIED")
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
    data = {'contacts': [], 'daily_counts': {}, 'hourly_counts': defaultdict(int), 'day_of_week_counts': defaultdict(int), 'group_chats': []}
    
    cte = """WITH chat_participants AS (SELECT chat_id, COUNT(*) as pc FROM chat_handle_join GROUP BY chat_id),
        one_on_one AS (SELECT m.ROWID as msg_id FROM message m JOIN chat_message_join cmj ON m.ROWID = cmj.message_id
        JOIN chat_participants cp ON cmj.chat_id = cp.chat_id WHERE cp.pc = 1)"""
    
    rows = q_imessage(f"""{cte}
        SELECT h.id, COUNT(*) as total, SUM(CASE WHEN m.is_from_me=1 THEN 1 ELSE 0 END) as sent,
            SUM(CASE WHEN m.is_from_me=0 THEN 1 ELSE 0 END) as received,
            SUM(CASE WHEN CAST(strftime('%H',datetime((m.date/1000000000+978307200),'unixepoch','localtime')) AS INT)<5 THEN 1 ELSE 0 END) as late
        FROM message m JOIN handle h ON m.handle_id=h.ROWID
        WHERE (m.date/1000000000+978307200)>{ts_start} AND m.ROWID IN (SELECT msg_id FROM one_on_one)
        AND NOT (LENGTH(REPLACE(REPLACE(h.id, '+', ''), '-', '')) BETWEEN 5 AND 6 AND REPLACE(REPLACE(h.id, '+', ''), '-', '') GLOB '[0-9]*')
        GROUP BY h.id ORDER BY total DESC""")
    
    for handle, total, sent, received, late in rows:
        name = get_name_imessage(handle, contacts)
        data['contacts'].append({'id': handle, 'name': name, 'total': total, 'sent': sent, 'received': received, 'late_night': late, 'ratio': round(sent/max(received,1), 2), 'platform': 'imessage'})
    
    for date, total, sent, received in q_imessage(f"SELECT DATE(datetime((date/1000000000+978307200),'unixepoch','localtime')) d, COUNT(*), SUM(CASE WHEN is_from_me=1 THEN 1 ELSE 0 END), SUM(CASE WHEN is_from_me=0 THEN 1 ELSE 0 END) FROM message WHERE (date/1000000000+978307200)>{ts_start} GROUP BY d"):
        data['daily_counts'][date] = {'total': total, 'sent': sent, 'received': received}
    
    for hour, count in q_imessage(f"SELECT CAST(strftime('%H',datetime((date/1000000000+978307200),'unixepoch','localtime')) AS INT), COUNT(*) FROM message WHERE (date/1000000000+978307200)>{ts_start} GROUP BY 1"):
        data['hourly_counts'][hour] = count
    
    days = ['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday']
    for day_num, count in q_imessage(f"SELECT CAST(strftime('%w',datetime((date/1000000000+978307200),'unixepoch','localtime')) AS INT), COUNT(*) FROM message WHERE (date/1000000000+978307200)>{ts_start} GROUP BY 1"):
        data['day_of_week_counts'][days[day_num]] = count
    
    for chat_id, name, msg_count, members, sent in q_imessage(f"""
        WITH cp AS (SELECT chat_id, COUNT(*) as pc FROM chat_handle_join GROUP BY chat_id), gc AS (SELECT chat_id FROM cp WHERE pc >= 2)
        SELECT c.ROWID, c.display_name, COUNT(*), (SELECT COUNT(*) FROM chat_handle_join WHERE chat_id = c.ROWID), SUM(CASE WHEN m.is_from_me=1 THEN 1 ELSE 0 END)
        FROM chat c JOIN chat_message_join cmj ON c.ROWID = cmj.chat_id JOIN message m ON cmj.message_id = m.ROWID
        WHERE c.ROWID IN (SELECT chat_id FROM gc) AND (m.date/1000000000+978307200)>{ts_start} GROUP BY c.ROWID ORDER BY 3 DESC"""):
        data['group_chats'].append({'id': chat_id, 'name': name or f"Group ({members})", 'total': msg_count, 'members': members, 'sent': sent, 'platform': 'imessage'})
    
    return data

def get_all_whatsapp_data(ts_start, contacts):
    data = {'contacts': [], 'daily_counts': {}, 'hourly_counts': defaultdict(int), 'day_of_week_counts': defaultdict(int), 'group_chats': []}
    
    cte = f"""WITH dm AS (SELECT Z_PK, ZCONTACTJID FROM ZWACHATSESSION WHERE ZSESSIONTYPE = 0),
        dm_msg AS (SELECT m.Z_PK as msg_id, s.ZCONTACTJID FROM ZWAMESSAGE m JOIN dm s ON m.ZCHATSESSION = s.Z_PK)"""
    
    rows = q_whatsapp(f"""{cte}
        SELECT dm_msg.ZCONTACTJID, COUNT(*), SUM(CASE WHEN m.ZISFROMME=1 THEN 1 ELSE 0 END),
            SUM(CASE WHEN m.ZISFROMME=0 THEN 1 ELSE 0 END),
            SUM(CASE WHEN CAST(strftime('%H',datetime(m.ZMESSAGEDATE+{COCOA_OFFSET},'unixepoch','localtime')) AS INT)<5 THEN 1 ELSE 0 END)
        FROM ZWAMESSAGE m JOIN dm_msg ON m.Z_PK = dm_msg.msg_id WHERE m.ZMESSAGEDATE>{ts_start} GROUP BY 1 ORDER BY 2 DESC""")
    
    for jid, total, sent, received, late in rows:
        name = get_name_whatsapp(jid, contacts)
        data['contacts'].append({'id': jid, 'name': name, 'total': total, 'sent': sent, 'received': received, 'late_night': late, 'ratio': round(sent/max(received,1), 2), 'platform': 'whatsapp'})
    
    for date, total, sent, received in q_whatsapp(f"SELECT DATE(datetime(ZMESSAGEDATE+{COCOA_OFFSET},'unixepoch','localtime')), COUNT(*), SUM(CASE WHEN ZISFROMME=1 THEN 1 ELSE 0 END), SUM(CASE WHEN ZISFROMME=0 THEN 1 ELSE 0 END) FROM ZWAMESSAGE WHERE ZMESSAGEDATE>{ts_start} GROUP BY 1"):
        data['daily_counts'][date] = {'total': total, 'sent': sent, 'received': received}
    
    for hour, count in q_whatsapp(f"SELECT CAST(strftime('%H',datetime(ZMESSAGEDATE+{COCOA_OFFSET},'unixepoch','localtime')) AS INT), COUNT(*) FROM ZWAMESSAGE WHERE ZMESSAGEDATE>{ts_start} GROUP BY 1"):
        data['hourly_counts'][hour] = count
    
    days = ['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday']
    for day_num, count in q_whatsapp(f"SELECT CAST(strftime('%w',datetime(ZMESSAGEDATE+{COCOA_OFFSET},'unixepoch','localtime')) AS INT), COUNT(*) FROM ZWAMESSAGE WHERE ZMESSAGEDATE>{ts_start} GROUP BY 1"):
        data['day_of_week_counts'][days[day_num]] = count
    
    for chat_id, name, msg_count, sent in q_whatsapp(f"""
        WITH gs AS (SELECT Z_PK, ZPARTNERNAME FROM ZWACHATSESSION WHERE ZSESSIONTYPE = 1)
        SELECT s.Z_PK, s.ZPARTNERNAME, COUNT(*), SUM(CASE WHEN m.ZISFROMME=1 THEN 1 ELSE 0 END)
        FROM ZWAMESSAGE m JOIN gs s ON m.ZCHATSESSION = s.Z_PK WHERE m.ZMESSAGEDATE>{ts_start} GROUP BY 1 ORDER BY 3 DESC"""):
        data['group_chats'].append({'id': chat_id, 'name': name or "Group", 'total': msg_count, 'members': 0, 'sent': sent, 'platform': 'whatsapp'})
    
    return data

def merge_data(im_data, wa_data, has_im, has_wa):
    merged = {'contacts': [], 'daily_counts': {}, 'hourly_counts': {str(i): 0 for i in range(24)}, 'day_of_week_counts': {}, 'group_chats': [], 'summary': {}}
    
    if has_im: merged['contacts'].extend(im_data['contacts'])
    if has_wa: merged['contacts'].extend(wa_data['contacts'])
    merged['contacts'].sort(key=lambda x: -x['total'])
    
    all_dates = set()
    if has_im: all_dates.update(im_data['daily_counts'].keys())
    if has_wa: all_dates.update(wa_data['daily_counts'].keys())
    
    for date in sorted(all_dates):
        im = im_data['daily_counts'].get(date, {'total': 0, 'sent': 0, 'received': 0}) if has_im else {'total': 0, 'sent': 0, 'received': 0}
        wa = wa_data['daily_counts'].get(date, {'total': 0, 'sent': 0, 'received': 0}) if has_wa else {'total': 0, 'sent': 0, 'received': 0}
        merged['daily_counts'][date] = {'total': im['total'] + wa['total'], 'sent': im['sent'] + wa['sent'], 'received': im['received'] + wa['received']}
    
    for h in range(24):
        im = im_data['hourly_counts'].get(h, 0) if has_im else 0
        wa = wa_data['hourly_counts'].get(h, 0) if has_wa else 0
        merged['hourly_counts'][str(h)] = im + wa
    
    for day in ['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday']:
        im = im_data['day_of_week_counts'].get(day, 0) if has_im else 0
        wa = wa_data['day_of_week_counts'].get(day, 0) if has_wa else 0
        merged['day_of_week_counts'][day] = im + wa
    
    if has_im: merged['group_chats'].extend(im_data['group_chats'])
    if has_wa: merged['group_chats'].extend(wa_data['group_chats'])
    merged['group_chats'].sort(key=lambda x: -x['total'])
    
    im_total = sum(c['total'] for c in merged['contacts'] if c['platform'] == 'imessage')
    wa_total = sum(c['total'] for c in merged['contacts'] if c['platform'] == 'whatsapp')
    
    merged['summary'] = {
        'total_messages': sum(c['total'] for c in merged['contacts']),
        'total_sent': sum(c['sent'] for c in merged['contacts']),
        'total_received': sum(c['received'] for c in merged['contacts']),
        'total_contacts': len(merged['contacts']),
        'total_groups': len(merged['group_chats']),
        'imessage_total': im_total,
        'whatsapp_total': wa_total,
        'has_imessage': has_im,
        'has_whatsapp': has_wa
    }
    return merged

def generate_html(data, year, output_path):
    json_data = json.dumps(data, ensure_ascii=True)
    
    html = f'''<!DOCTYPE html>
<html><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Texts Dashboard {year}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:'Inter',sans-serif;background:#0a0a0c;color:#f5f5f7;min-height:100vh}}
.dash{{max-width:1400px;margin:0 auto;padding:24px}}
.header{{display:flex;justify-content:space-between;align-items:center;margin-bottom:24px;flex-wrap:wrap;gap:16px}}
h1{{font-size:24px;font-weight:600}}
.badges{{display:flex;gap:10px}}
.badge{{padding:6px 12px;background:#1c1c1e;border:1px solid #2c2c2e;border-radius:8px;font-family:'JetBrains Mono',monospace;font-size:13px}}
.badge.im{{color:#32d74b}}.badge.wa{{color:#25d366}}
.stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:24px}}
.stat{{background:#1c1c1e;border:1px solid #2c2c2e;border-radius:12px;padding:16px}}
.stat-label{{font-size:11px;color:#8e8e93;text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px}}
.stat-val{{font-family:'JetBrains Mono',monospace;font-size:24px;font-weight:600}}
.stat-val.gr{{background:linear-gradient(135deg,#32d74b,#25d366);-webkit-background-clip:text;-webkit-text-fill-color:transparent}}
.stat-sub{{font-size:11px;color:#636366;margin-top:4px}}
.filters{{display:flex;gap:10px;margin-bottom:20px;flex-wrap:wrap;align-items:center}}
.search{{background:#1c1c1e;border:1px solid #2c2c2e;border-radius:8px;padding:8px 12px;color:#f5f5f7;font-size:14px;min-width:200px;outline:none}}
.search:focus{{border-color:#0a84ff}}
.btns{{display:flex;background:#1c1c1e;border:1px solid #2c2c2e;border-radius:8px;overflow:hidden}}
.btn{{padding:8px 14px;font-size:13px;background:transparent;border:none;color:#8e8e93;cursor:pointer}}
.btn:hover{{color:#f5f5f7;background:#2c2c2e}}
.btn.on{{background:#0a84ff;color:#fff}}
.sel-info{{display:none;align-items:center;gap:8px}}
.sel-count{{background:#0a84ff;color:#fff;padding:4px 10px;border-radius:6px;font-size:12px}}
.clear{{padding:4px 10px;background:transparent;border:1px solid #2c2c2e;border-radius:6px;color:#8e8e93;font-size:12px;cursor:pointer}}
.clear:hover{{border-color:#ff375f;color:#ff375f}}
.grid{{display:grid;grid-template-columns:1fr 340px;gap:20px}}
@media(max-width:1000px){{.grid{{grid-template-columns:1fr}}}}
.card{{background:#1c1c1e;border:1px solid #2c2c2e;border-radius:12px;overflow:hidden}}
.card-head{{padding:14px 16px;border-bottom:1px solid #2c2c2e;font-size:14px;font-weight:500}}
.card-body{{padding:16px}}
.chart-box{{height:260px}}
.chart-sm{{height:180px}}
.row2{{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:16px}}
@media(max-width:700px){{.row2{{grid-template-columns:1fr}}}}
.list{{max-height:550px;overflow-y:auto}}
.contact{{display:flex;align-items:center;padding:10px 14px;border-bottom:1px solid #2c2c2e;cursor:pointer;gap:10px}}
.contact:hover{{background:#2c2c2e}}
.contact.sel{{background:rgba(10,132,255,.15);border-left:3px solid #0a84ff}}
.chk{{appearance:none;width:16px;height:16px;border:2px solid #3c3c3e;border-radius:4px;cursor:pointer;flex-shrink:0}}
.chk:checked{{background:#0a84ff;border-color:#0a84ff}}
.rank{{font-family:'JetBrains Mono',monospace;font-size:11px;color:#636366;width:26px}}
.c-info{{flex:1;min-width:0}}
.c-name{{font-size:13px;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.c-meta{{font-size:11px;color:#8e8e93;display:flex;gap:8px;margin-top:2px}}
.c-count{{font-family:'JetBrains Mono',monospace;font-size:13px;font-weight:600;color:#64d2ff}}
.c-bar{{height:3px;background:#2c2c2e;border-radius:2px;margin-top:5px}}
.c-fill{{height:100%;background:#0a84ff;border-radius:2px}}
.groups{{margin-top:16px}}
::-webkit-scrollbar{{width:6px}}::-webkit-scrollbar-track{{background:#1c1c1e}}::-webkit-scrollbar-thumb{{background:#3c3c3e;border-radius:3px}}
</style></head>
<body>
<div class="dash">
<header class="header"><div><h1>📱 Texts Dashboard {year}</h1></div><div class="badges" id="badges"></div></header>
<div class="stats" id="stats"></div>
<div class="filters">
<input class="search" id="search" placeholder="Search contacts...">
<div class="btns" id="plat"></div>
<div class="btns" id="sort"></div>
<div class="sel-info" id="selInfo"><span class="sel-count" id="selCount">0</span><button class="clear" id="clearBtn">Clear</button></div>
</div>
<div class="grid">
<div>
<div class="card"><div class="card-head">Message Activity</div><div class="card-body"><div class="chart-box"><canvas id="actChart"></canvas></div></div></div>
<div class="row2">
<div class="card"><div class="card-head">By Hour</div><div class="card-body"><div class="chart-sm"><canvas id="hrChart"></canvas></div></div></div>
<div class="card"><div class="card-head">By Day</div><div class="card-body"><div class="chart-sm"><canvas id="dayChart"></canvas></div></div></div>
</div>
<div class="groups card" id="groupsCard" style="display:none"><div class="card-head" id="groupsHead">Groups</div><div class="list" id="groupsList" style="max-height:280px"></div></div>
</div>
<div class="card"><div class="card-head" id="cHead">Contacts</div><div class="list" id="cList"></div></div>
</div>
</div>
<script>
const D={json_data};
let sel=new Set(),plat='all',srt='total',searchTerm='';

function init(){{
    document.getElementById('badges').innerHTML=(D.summary.has_imessage?'<div class="badge im">📱 '+D.summary.imessage_total.toLocaleString()+'</div>':'')+(D.summary.has_whatsapp?'<div class="badge wa">💬 '+D.summary.whatsapp_total.toLocaleString()+'</div>':'');
    renderStats();
    document.getElementById('plat').innerHTML='<button class="btn on" data-p="all">All</button>'+(D.summary.has_imessage?'<button class="btn" data-p="imessage">📱</button>':'')+(D.summary.has_whatsapp?'<button class="btn" data-p="whatsapp">💬</button>':'');
    document.getElementById('sort').innerHTML='<button class="btn on" data-s="total">Total</button><button class="btn" data-s="sent">Sent</button><button class="btn" data-s="received">Recv</button>';
    render();
    charts();
    groups();
    
    document.getElementById('search').addEventListener('input',function(e){{
        searchTerm=e.target.value.toLowerCase();
        render();
    }});
    
    document.getElementById('plat').addEventListener('click',function(e){{
        if(e.target.dataset.p){{
            plat=e.target.dataset.p;
            this.querySelectorAll('.btn').forEach(function(b){{b.classList.remove('on')}});
            e.target.classList.add('on');
            render();
        }}
    }});
    
    document.getElementById('sort').addEventListener('click',function(e){{
        if(e.target.dataset.s){{
            srt=e.target.dataset.s;
            this.querySelectorAll('.btn').forEach(function(b){{b.classList.remove('on')}});
            e.target.classList.add('on');
            render();
        }}
    }});
    
    document.getElementById('cList').addEventListener('click',function(e){{
        var row=e.target.closest('.contact');
        if(row){{
            var id=row.dataset.id;
            if(sel.has(id)){{sel.delete(id)}}else{{sel.add(id)}}
            render();
            renderStats();
        }}
    }});
    
    document.getElementById('clearBtn').addEventListener('click',function(){{
        sel.clear();
        render();
        renderStats();
    }});
}}

function renderStats(){{
    var s=D.summary;
    var total=s.total_messages,sent=s.total_sent,recv=s.total_received,numContacts=s.total_contacts;
    var label='Total';
    
    if(sel.size>0){{
        total=0;sent=0;recv=0;
        D.contacts.forEach(function(c){{
            if(sel.has(c.id)){{
                total+=c.total;
                sent+=c.sent;
                recv+=c.received;
            }}
        }});
        numContacts=sel.size;
        label='Selected';
    }}
    
    var pd=Math.round(total/365);
    document.getElementById('stats').innerHTML='<div class="stat"><div class="stat-label">'+label+'</div><div class="stat-val gr">'+total.toLocaleString()+'</div><div class="stat-sub">'+pd+'/day</div></div><div class="stat"><div class="stat-label">Sent</div><div class="stat-val" style="color:#0a84ff">'+sent.toLocaleString()+'</div></div><div class="stat"><div class="stat-label">Received</div><div class="stat-val" style="color:#64d2ff">'+recv.toLocaleString()+'</div></div><div class="stat"><div class="stat-label">Contacts</div><div class="stat-val" style="color:#bf5af2">'+numContacts+'</div><div class="stat-sub">'+(sel.size>0?'selected':'+ '+s.total_groups+' groups')+'</div></div>';
}}

function render(){{
    var contacts=[].concat(D.contacts);
    
    if(plat!=='all'){{
        contacts=contacts.filter(function(x){{return x.platform===plat}});
    }}
    
    if(searchTerm){{
        contacts=contacts.filter(function(x){{return x.name.toLowerCase().indexOf(searchTerm)!==-1}});
    }}
    
    contacts.sort(function(a,b){{return b[srt]-a[srt]}});
    
    var mx=contacts.length>0?contacts[0].total:1;
    document.getElementById('cHead').textContent='Contacts ('+contacts.length+')';
    
    var html='';
    for(var i=0;i<contacts.length;i++){{
        var x=contacts[i];
        var isSelected=sel.has(x.id);
        var icon=x.platform==='imessage'?' 📱':' 💬';
        var barW=(x.total/mx*100).toFixed(1);
        html+='<div class="contact'+(isSelected?' sel':'')+'" data-id="'+x.id+'">';
        html+='<input type="checkbox" class="chk"'+(isSelected?' checked':'')+'>';
        html+='<span class="rank">#'+(i+1)+'</span>';
        html+='<div class="c-info">';
        html+='<div class="c-name">'+esc(x.name)+icon+'</div>';
        html+='<div class="c-meta"><span>↑'+x.sent.toLocaleString()+'</span><span>↓'+x.received.toLocaleString()+'</span>'+(x.late_night>0?'<span>🌙'+x.late_night+'</span>':'')+'</div>';
        html+='<div class="c-bar"><div class="c-fill" style="width:'+barW+'%"></div></div>';
        html+='</div>';
        html+='<span class="c-count">'+x.total.toLocaleString()+'</span>';
        html+='</div>';
    }}
    document.getElementById('cList').innerHTML=html;
    
    var si=document.getElementById('selInfo');
    if(sel.size>0){{
        si.style.display='flex';
        document.getElementById('selCount').textContent=sel.size+' selected';
    }}else{{
        si.style.display='none';
    }}
}}

function charts(){{
    var dates=Object.keys(D.daily_counts).sort();
    var vals=dates.map(function(d){{return D.daily_counts[d].total}});
    var labels=dates.map(function(d){{return new Date(d).toLocaleDateString('en-US',{{month:'short',day:'numeric'}})}});
    
    new Chart(document.getElementById('actChart'),{{
        type:'line',
        data:{{labels:labels,datasets:[{{data:vals,borderColor:'#32d74b',backgroundColor:'rgba(50,215,75,.1)',fill:true,tension:.4,pointRadius:0}}]}},
        options:{{responsive:true,maintainAspectRatio:false,plugins:{{legend:{{display:false}}}},scales:{{x:{{grid:{{color:'#2c2c2e'}},ticks:{{color:'#636366',maxTicksLimit:8}}}},y:{{grid:{{color:'#2c2c2e'}},ticks:{{color:'#636366'}}}}}}}}
    }});
    
    var hrs=[];for(var i=0;i<24;i++)hrs.push(i);
    var hrVals=hrs.map(function(h){{return D.hourly_counts[String(h)]||0}});
    var hrLbls=hrs.map(function(h){{return h===0?'12a':h<12?h+'a':h===12?'12p':(h-12)+'p'}});
    
    new Chart(document.getElementById('hrChart'),{{
        type:'bar',
        data:{{labels:hrLbls,datasets:[{{data:hrVals,backgroundColor:'#0a84ff',borderRadius:3}}]}},
        options:{{responsive:true,maintainAspectRatio:false,plugins:{{legend:{{display:false}}}},scales:{{x:{{grid:{{display:false}},ticks:{{color:'#636366',maxTicksLimit:8}}}},y:{{grid:{{color:'#2c2c2e'}},ticks:{{color:'#636366'}}}}}}}}
    }});
    
    var dys=['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
    var dyFull=['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'];
    var dyVals=dyFull.map(function(d){{return D.day_of_week_counts[d]||0}});
    
    new Chart(document.getElementById('dayChart'),{{
        type:'bar',
        data:{{labels:dys,datasets:[{{data:dyVals,backgroundColor:'#bf5af2',borderRadius:3}}]}},
        options:{{responsive:true,maintainAspectRatio:false,plugins:{{legend:{{display:false}}}},scales:{{x:{{grid:{{display:false}},ticks:{{color:'#636366'}}}},y:{{grid:{{color:'#2c2c2e'}},ticks:{{color:'#636366'}}}}}}}}
    }});
}}

function groups(){{
    if(!D.group_chats.length)return;
    document.getElementById('groupsCard').style.display='block';
    document.getElementById('groupsHead').textContent='Groups ('+D.group_chats.length+')';
    var html='';
    var list=D.group_chats.slice(0,12);
    for(var i=0;i<list.length;i++){{
        var g=list[i];
        var icon=g.platform==='imessage'?' 📱':' 💬';
        html+='<div class="contact"><span class="rank">#'+(i+1)+'</span><div class="c-info"><div class="c-name">'+esc(g.name)+icon+'</div><div class="c-meta"><span>You: '+g.sent.toLocaleString()+'</span></div></div><span class="c-count">'+g.total.toLocaleString()+'</span></div>';
    }}
    document.getElementById('groupsList').innerHTML=html;
}}

function esc(s){{var d=document.createElement('div');d.textContent=s;return d.innerHTML}}

init();
</script>
</body></html>'''
    
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)
    return output_path

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--use-2024', action='store_true')
    parser.add_argument('-o', '--output', default='texts_dashboard.html')
    args = parser.parse_args()
    
    year = '2024' if args.use_2024 else '2025'
    ts_im = 1704067200 if year == '2024' else 1735689600
    ts_wa = 725846400 if year == '2024' else 757382400
    
    print(f"\n📊 Texts Dashboard {year}\n" + "="*40)
    print("Checking access...")
    has_im, has_wa = check_access()
    print(f"✓ Found: {', '.join(filter(None, ['iMessage' if has_im else '', 'WhatsApp' if has_wa else '']))}")
    
    im_data = wa_data = {'contacts': [], 'daily_counts': {}, 'hourly_counts': {}, 'day_of_week_counts': {}, 'group_chats': []}
    
    if has_im:
        print("Analyzing iMessage...")
        im_contacts = extract_imessage_contacts()
        im_data = get_all_imessage_data(ts_im, im_contacts)
        print(f"✓ {len(im_data['contacts'])} conversations")
    
    if has_wa:
        print("Analyzing WhatsApp...")
        wa_contacts = extract_whatsapp_contacts()
        wa_data = get_all_whatsapp_data(ts_wa, wa_contacts)
        print(f"✓ {len(wa_data['contacts'])} conversations")
    
    print("Generating dashboard...")
    data = merge_data(im_data, wa_data, has_im, has_wa)
    output = generate_html(data, year, args.output)
    print(f"✓ {data['summary']['total_messages']:,} total messages")
    print(f"✓ Saved to {output}")
    
    subprocess.run(['open', output])
    print("\n✨ Done!")

if __name__ == '__main__':
    main()
