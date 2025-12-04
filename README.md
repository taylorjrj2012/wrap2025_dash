# 📱 iMessage Wrapped 2025

Your texting habits, exposed. A Spotify Wrapped-style visualization of your iMessage history.

**[→ wrap2025.com](https://wrap2025.com)**

## Features

- 📊 **Total messages** - sent, received, per day
- 👑 **Top 5 contacts** - your inner circle
- 🧠 **Texting personality** - based on your habits
- ⏱️ **Response time** - how fast you reply
- 🌙 **3AM bestie** - late night conversations
- 🔥 **Heating up** - growing relationships
- 👻 **Ghosted** - who stopped texting
- 😍 **Down bad** - who you simp for
- 📅 **Busiest day** - your most unhinged day
- 💬 **Who texts first** - conversation initiator %

## Installation

### 1. Download the script

```bash
curl -O https://raw.githubusercontent.com/kothari-nikunj/wrap2025/main/imessage_wrapped.py
```

Or download directly from [wrap2025.com](https://wrap2025.com)

### 2. Grant Terminal access

The script needs to read your Messages database:

**System Settings → Privacy & Security → Full Disk Access → Add Terminal**

(Or iTerm/Warp if you use those)

### 3. Run it

```bash
python3 imessage_wrapped.py
```

Your wrapped will open in your browser automatically.

## Options

```bash
# Use 2024 data instead of 2025
python3 imessage_wrapped.py --use-2024

# Custom output filename
python3 imessage_wrapped.py -o my_wrapped.html
```

If you don't have enough 2025 messages yet, the script will automatically fall back to 2024.

## Privacy

🔒 **100% Local** - Your data never leaves your computer

- No servers, no uploads, no tracking
- No external dependencies (Python stdlib only)
- All analysis happens locally
- Output is a single HTML file

You can read the entire source code yourself—it's ~500 lines of Python.

## Requirements

- macOS (uses local iMessage database)
- Python 3 (pre-installed on macOS)
- Full Disk Access for Terminal

## How it works

The script reads your local `chat.db` (iMessage database) and `AddressBook` (Contacts) using SQLite queries. It analyzes your message patterns, resolves phone numbers to contact names, and generates a self-contained HTML file with an interactive gallery.

## FAQ

**Q: Is this safe?**  
A: Yes. The script only reads local databases, writes one HTML file, and makes zero network requests. No data is sent anywhere.

**Q: Why do I need Full Disk Access?**  
A: Apple protects the Messages database. Terminal needs permission to read it.

**Q: Can I run this on iOS?**  
A: No, iOS doesn't allow access to the Messages database. macOS only.

**Q: The names are showing as phone numbers**  
A: The script tries to match phone numbers to your Contacts. Some may not resolve if the formatting differs.

## Credits

Made by [@nikunj](https://x.com/nikunj)

Not affiliated with Apple or Spotify.

## License

MIT
