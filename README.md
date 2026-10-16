# 🏛️ TARClub — Society & Club Governance Bot

[![Python 3.11 | 3.12 | 3.13](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue.svg)](https://www.python.org/)
[![Discord.py](https://img.shields.io/badge/discord.py-v2.4-5865F2.svg)](https://discordpy.readthedocs.io/)
[![SQLite WAL](https://img.shields.io/badge/sqlite-WAL%20mode-003B57.svg)](https://www.sqlite.org/wal.html)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

> **TARClub** is a dedicated Discord governance, role-based access control (RBAC), and term management bot designed specifically for student societies, clubs, and interest groups at **TAR UMT (Tunku Abdul Rahman University of Management and Technology)**.

---

## 🌟 Key Features

- **Multi-Tenant Society Registration**: Each server registers as an independent society (`/society register [name] [code] [faculty]`).
- **Dynamic Theming Engine**: Custom primary/secondary hex colors, branded Discord roles, banner assets, custom emojis, and embed footers per society.
- **14-Role Starter Template & Hierarchy**: Pre-loaded TAR UMT standard club hierarchy (Chairperson down to Internal Auditor) that committees can dynamically customize or expand.
- **Granular Bitwise RBAC**: 12 fine-grained permissions controlling announcements, event scheduling, budget requests, audit trails, and member assignments.
- **Annual Committee Term Handover**: Two-step quorum handover wizard that smoothly transitions active committees to `@Alumni Exco (Term)` and installs incoming teams.
- **Cross-Bot Bridge with TARVeri**: Co-location auto-discovery and eligibility checks ensuring only verified TAR UMT students hold executive committee seats.

---

## 👥 Standard 14-Role Starter Hierarchy

```
                    ┌────────────────────────┐
                    │      Chairperson       │
                    └───────────┬────────────┘
                                │
                    ┌───────────┴────────────┐
                    │    Vice Chairperson    │
                    └───────────┬────────────┘
                                │
            ┌───────────────────┴───────────────────┐
            │                                       │
┌───────────┴────────────┐             ┌────────────┴───────────┐
│   Deputy Chairperson   │             │ Deputy Vice Chairperson│
└────────────────────────┘             └────────────────────────┘
            │                                       │
            ├───────────────────────────────────────┤
            │         Secretariat & Finance         │
            ├───────────────────┬───────────────────┤
            │   Secretary       │     Treasurer     │
            │ Deputy Secretary  │  Deputy Treasurer │
            └───────────────────┴───────────────────┘
                                │
            ┌───────────────────┴───────────────────┐
            │             Directorships             │
            ├───────────────────────────────────────┤
            │ • Programme & Activities Director     │
            │ • Technical Resource Director         │
            │ • Publicity Director                  │
            │   └─ Publicity Member                 │
            │ • Art and Promotion Director          │
            │ • Internal Auditor                    │
            └───────────────────────────────────────┘
```

---

## 🚀 Quick Start

### 1. Clone & Setup
```bash
git clone https://github.com/TARBot-OSS/TARClub-bot.git
cd TARClub-bot

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure Environment
```bash
cp .env.example .env
# Edit .env with your Discord Bot Token
```

### 3. Run the Bot
```bash
python -m tarclub
```

---

## 🤝 Cross-Bot Integration with TARVeri

When both **TARVeri** and **TARClub** are present in the same Discord server, TARClub automatically bridges with TARVeri's student verification registry to ensure candidate eligibility for committee positions.

For architectural and runbook details, see [`.agents/skills/tarclub-society/SKILL.md`](.agents/skills/tarclub-society/SKILL.md).
