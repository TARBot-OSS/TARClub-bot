---
name: tarclub-society
description: Comprehensive architecture, role-based access control (RBAC), annual term handover engine, theming system, and cross-bot TARVeri integration guide for TARClub Discord society management bot.
---

# 🏛️ TARClub — Society & Club Governance Bot Architecture

**TARClub** is a dedicated Discord governance, role-based access control (RBAC), and term management bot designed specifically for student societies, clubs, interest groups, and student representative bodies at TAR UMT (Tunku Abdul Rahman University of Management and Technology).

---

## 🧭 Executive Architecture Strategy: Why a Dedicated Bot?

When deciding whether to expand **TARVeri** or build a **new standalone bot (TARClub)**, the industry-standard recommendation is **a dedicated bot with a cross-bot integration API**.

```
┌───────────────────────────────────────┐         ┌───────────────────────────────────────┐
│              TARVeri                  │         │               TARClub                 │
│      (Identity & Gatekeeper)          │         │       (Governance & Society Ops)      │
├───────────────────────────────────────┤         ├───────────────────────────────────────┤
│ • Campus Student ID verification      │         │ • Society registration & custom theme │
│ • Dual SMTP OTP verification          │         │ • Annual committee terms (2025/26)    │
│ • OCR digital student card parsing    │  IPC /  │ • Custom 14-role template & RBAC      │
│ • Guest vouch & 2-tier approval       │ Webhook │ • Committee handover & alumni archive │
│ • Multi-guild global blacklist        │◄───────►│ • Event approvals & budget tracking   │
│ • PII & institutional email security  │ Token   │ • Public society directory & showcase │
└───────────────────────────────────────┘         └───────────────────────────────────────┘
```

### Architectural Rationale
1. **Separation of Concerns & Least Privilege**:
   - TARVeri handles sensitive Student PII (student ID, institutional email, matriculation cards, double-blind hashes). It holds elevated Discord administrative privileges for role provisioning across campus hubs.
   - TARClub operates inside dozens of individual student club servers with operational permissions (channel overrides, committee role assignments, event boards, announcements, budget audit logs).
   - Separating them ensures that everyday club committee activities or misconfigured permissions in student clubs never put institutional identity data or campus-wide verification at risk.
2. **Multi-Tenancy & Independence**:
   - Not every society server wants full campus-wide guest verification; some only want committee governance, custom themes, and annual election handovers.
   - Independent deployment allows clubs to update or customize TARClub without restarting or redeploying the mission-critical campus verification bot.
3. **Synergy via Cross-Bot API**:
   - When **both** bots are present on the same server, TARClub queries TARVeri's local API to enforce: *"Only verified TAR UMT students can hold Executive Committee (Exco) positions."*

---

## 🏗️ TARClub Module Layout & Repository Blueprint

Recommended structure for the new `TARClub-bot` repository:

```
TARClub-bot/
├── tarclub/
│   ├── __init__.py                # Package exports & versioning
│   ├── __main__.py                # python -m tarclub runner
│   ├── bot.py                     # TARClubBot lifecycle & persistent views
│   ├── config.py                  # Environment config, default theme palettes, token secrets
│   ├── database.py                # Database facade with WAL mode & PRAGMAs
│   ├── db/
│   │   ├── base.py                # SQLite connection pool & query helpers
│   │   ├── society_repo.py        # Society registration, profile & branding
│   │   ├── theme_repo.py          # Custom color palettes, banners & embed styling
│   │   ├── term_repo.py           # Annual terms (2025/2026), active flags & milestones
│   │   ├── position_repo.py       # Custom & template committee positions & hierarchy
│   │   ├── committee_repo.py      # Member-position assignments & history
│   │   ├── rbac_repo.py           # Permission flags & role-channel mappings
│   │   └── integration_repo.py    # Cross-bot paired guilds & API keys
│   ├── services/
│   │   ├── society_service.py     # Registration & society profile manager
│   │   ├── theme_service.py       # Theme applicator, hex colors & embed decorator
│   │   ├── term_service.py        # Annual term lifecycle & handover wizard
│   │   ├── rbac_service.py        # Fine-grained RBAC evaluation & Discord sync
│   │   ├── integration_service.py # TARVeri co-location auto-discovery & API client
│   │   └── audit_service.py       # Governance audit logging & timeline tracking
│   ├── views/
│   │   ├── registration_views.py  # Society setup wizard modal & theme selector
│   │   ├── committee_views.py     # Position editor, assignment dropdowns & modals
│   │   ├── handover_views.py      # Interactive annual handover confirmation & review
│   │   └── theme_views.py         # Color picker, banner URL input & live preview
│   └── cogs/
│       ├── society_cog.py         # /society (register, info, theme, directory)
│       ├── committee_cog.py       # /committee (roster, assign, edit-position, list)
│       ├── term_cog.py            # /term (start, handover, history, archive)
│       ├── rbac_cog.py            # /rbac (check, grant, revoke, sync-roles)
│       └── integration_cog.py     # /integrate-tarveri (status, link, sync-status)
├── tests/                         # Comprehensive unit & integration tests
├── scripts/
│   └── update.sh                  # Zero-downtime deployment script with backup
└── README.md                      # Setup & administration documentation
```

---

## 🗄️ Database Schema Specification

All tables use SQLite WAL mode with foreign key enforcement (`PRAGMA foreign_keys = ON;`).

```sql
-- 1. Registered Societies (Per Guild)
CREATE TABLE IF NOT EXISTS societies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER UNIQUE NOT NULL,
    society_code TEXT UNIQUE NOT NULL,      -- e.g. "TARUMT-CS", "TARUMT-IEESE"
    name TEXT NOT NULL,                     -- e.g. "Computer Science Society"
    faculty TEXT NOT NULL,                  -- e.g. "FOCS", "FOET", "FAFB", "FCC", "CPUS"
    category TEXT NOT NULL,                 -- "ACADEMIC", "SPORTS", "CULTURAL", "SPECIAL"
    description TEXT,
    founded_year INTEGER,
    registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 2. Society Theming & Branding
CREATE TABLE IF NOT EXISTS society_themes (
    society_id INTEGER PRIMARY KEY,
    primary_color TEXT DEFAULT "#1E88E5",   -- Hex code for embeds & primary roles
    secondary_color TEXT DEFAULT "#FFC107", -- Accent / highlight color
    banner_url TEXT,                        -- Discord CDN or Web image URL
    logo_url TEXT,                          -- High-res club logo
    emoji_badge TEXT DEFAULT "🏛️",          -- Club custom or standard emoji
    footer_text TEXT,                       -- Custom embed footer tagline
    FOREIGN KEY (society_id) REFERENCES societies(id) ON DELETE CASCADE
);

-- 3. Annual Terms (e.g. "2025/2026")
CREATE TABLE IF NOT EXISTS terms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    society_id INTEGER NOT NULL,
    term_name TEXT NOT NULL,                -- e.g. "Term 2025/2026"
    academic_year TEXT NOT NULL,            -- e.g. "2025/2026"
    is_active BOOLEAN DEFAULT 1,
    start_date DATE,
    end_date DATE,
    handover_at TIMESTAMP,
    handover_notes TEXT,
    FOREIGN KEY (society_id) REFERENCES societies(id) ON DELETE CASCADE,
    UNIQUE(society_id, term_name)
);

-- 4. Dynamic Committee Positions (Templates & Custom)
CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    society_id INTEGER NOT NULL,
    title TEXT NOT NULL,                    -- e.g. "Chairperson", "Art & Promotion Director"
    department TEXT NOT NULL,               -- "PRESIDIUM", "SECRETARIAT", "FINANCE", "DIRECTORSHIP"
    hierarchy_order INTEGER NOT NULL,       -- 1 = Chairperson (Highest), 15 = General Member
    discord_role_id INTEGER,                -- Linked Discord Role ID (auto-created)
    rbac_flags INTEGER NOT NULL DEFAULT 0,  -- Bitwise RBAC permissions
    is_template BOOLEAN DEFAULT 0,          -- 1 if default template, 0 if custom
    FOREIGN KEY (society_id) REFERENCES societies(id) ON DELETE CASCADE,
    UNIQUE(society_id, title)
);

-- 5. Committee Member Term Roster
CREATE TABLE IF NOT EXISTS committee_members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    term_id INTEGER NOT NULL,
    position_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,               -- Discord User ID
    student_id TEXT,                        -- Optional verified student ID from TARVeri
    is_verified_student BOOLEAN DEFAULT 0,  -- Flag verified by TARVeri
    appointed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    relieved_at TIMESTAMP,
    FOREIGN KEY (term_id) REFERENCES terms(id) ON DELETE CASCADE,
    FOREIGN KEY (position_id) REFERENCES positions(id) ON DELETE CASCADE,
    UNIQUE(term_id, position_id, user_id)
);

-- 6. Cross-Bot Integration Links (TARVeri Bridge)
CREATE TABLE IF NOT EXISTS tarveri_integrations (
    guild_id INTEGER PRIMARY KEY,
    is_linked BOOLEAN DEFAULT 0,
    paired_at TIMESTAMP,
    shared_secret_hash TEXT,
    last_health_check TIMESTAMP,
    enforce_student_verification BOOLEAN DEFAULT 1
);
```

---

## 👥 Standard 15-Role Starter Template & Default Hierarchy

Every newly registered society automatically receives the standard TAR UMT club organizational hierarchy. Committees can rename, add, remove, or modify positions at any time.

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

### Starter Template Roster Matrix

| Hierarchy | Position Title | Department | Default RBAC Flags | Description |
| :---: | :--- | :--- | :--- | :--- |
| **1** | **Chairperson** | Presidium | `ALL_PERMISSIONS` (0xFFFF) | Overall club head; full executive & administrative authority. |
| **2** | **Vice Chairperson** | Presidium | `ADMIN_EXECUTIVE` (0x7FFF) | Second-in-command; oversees directors and executes executive decisions. |
| **3** | **Deputy Chairperson** | Presidium | `ADMIN_EXECUTIVE` (0x7FFF) | Senior executive support to Chairperson; internal operations. |
| **4** | **Deputy Vice Chairperson** | Presidium | `ADMIN_EXECUTIVE` (0x7FFF) | Supports Vice Chairperson across activity execution and faculty liaison. |
| **5** | **Secretary** | Secretariat | `SECRETARIAT_CORE` (0x0F0F) | Meeting minutes, society registration documents, official letters. |
| **6** | **Deputy Secretary** | Secretariat | `SECRETARIAT_ASSIST` (0x0707) | Assists secretary; archive tracking, attendance and correspondence. |
| **7** | **Treasurer** | Finance | `FINANCE_CORE` (0x3030) | Budget proposals, receipts, club funds, ledger and reimbursement ledger. |
| **8** | **Deputy Treasurer** | Finance | `FINANCE_ASSIST` (0x1010) | Petty cash, event ticketing collection, sponsorship reconciliation. |
| **9** | **Programme & Activities Director** | Directorship | `PROGRAMME_DIRECTOR` (0x00A0) | Event planning, proposal drafting, timeline and booking management. |
| **10** | **Technical Resource Director** | Directorship | `TECH_DIRECTOR` (0x0050) | AV equipment, IT tools, Discord bot setups, website & tech gear. |
| **11** | **Publicity Director** | Directorship | `PUBLICITY_HEAD` (0x003C) | Social media management, announcements, marketing campaigns. |
| **12** | **Publicity Member** | Directorship | `PUBLICITY_STAFF` (0x0004) | Content writing, photography, broadcast assistance. |
| **13** | **Art and Promotion Director** | Directorship | `CREATIVE_HEAD` (0x0038) | Visual posters, branding guidelines, badges, aesthetic curation. |
| **14** | **Internal Auditor** | Directorship | `AUDITOR_CORE` (0x0081) | Inspects financial ledgers, compliance audits, minutes verification. |

---

## 🔐 Granular Bitwise RBAC Permission Matrix

TARClub utilizes integer bitmask flags for instant, zero-latency permission checks in Discord interactions:

```python
class SocietyPermission:
    MANAGE_SOCIETY = 1 << 0  # 0x0001: Edit society profile, theme & settings
    MANAGE_TERMS = 1 << 1  # 0x0002: Create terms, initiate handovers
    MANAGE_POSITIONS = 1 << 2  # 0x0004: Add, edit, reorder or delete committee positions
    ASSIGN_COMMITTEE = 1 << 3  # 0x0008: Assign or dismiss committee members
    POST_ANNOUNCEMENTS = 1 << 4  # 0x0010: Publish verified club-wide broadcasts
    CREATE_EVENTS = 1 << 5  # 0x0020: Schedule and manage club calendar events
    MANAGE_BUDGET = 1 << 6  # 0x0040: Propose or modify budget line items
    APPROVE_EXPENSES = 1 << 7  # 0x0080: Authorize reimbursements & financial sign-off
    AUDIT_LOGS = 1 << 8  # 0x0100: View full governance & financial audit trails
    MANAGE_ASSETS = 1 << 9  # 0x0200: Manage club equipment and technical resources
    EXPORT_DATA = 1 << 10  # 0x0400: Export member rosters & term reports (CSV/PDF)
    BYPASS_STUDENT_GATE = 1 << 11  # 0x0800: Override student verification requirement (Owner only)
```

### Pre-defined RBAC Bundles:
- **`ALL_PERMISSIONS`**: `0x0FFF` (Chairperson)
- **`EXECUTIVE_CORE`**: `MANAGE_SOCIETY | MANAGE_TERMS | ASSIGN_COMMITTEE | POST_ANNOUNCEMENTS | CREATE_EVENTS | AUDIT_LOGS`
- **`SECRETARIAT_CORE`**: `POST_ANNOUNCEMENTS | CREATE_EVENTS | AUDIT_LOGS | EXPORT_DATA`
- **`FINANCE_CORE`**: `MANAGE_BUDGET | APPROVE_EXPENSES | AUDIT_LOGS | EXPORT_DATA`
- **`DIRECTOR_CORE`**: `CREATE_EVENTS | POST_ANNOUNCEMENTS`

---

## 🎨 Society Dynamic Theming Engine

Each society can configure its identity without modifying bot source code:

1. **Custom Embed Theming**:
   - Primary Hex Color applies to all information embeds, rosters, and official announcements.
   - Secondary Accent Color applies to warnings, prompts, and highlight cards.
2. **Branded Role Generation**:
   - TARClub automatically provisions Discord roles styled with the society's primary/secondary colors (e.g. `@CS Chairperson`, `@CS Committee 25/26`).
3. **Dynamic Assets**:
   - Society Logo and Banner are rendered in `/society info` and public directories.
   - Embeds feature customized footer taglines and custom emojis.

---

## 🔄 Annual Committee Term Handover Lifecycle

```mermaid
sequenceDiagram
    autonumber
    actor Chair as Outgoing Chairperson
    participant Bot as TARClub Bot
    participant DB as SQLite DB
    participant Discord as Discord API
    actor Incoming as Incoming Chairperson

    Chair->>Bot: /term handover start [next_term="2026/2027"]
    Bot->>DB: Query active term & current committee members
    Bot-->>Chair: Display Handover Checklist & Incoming Nominees Modal
    Chair->>Bot: Submit incoming roster assignments
    Bot->>Bot: Validate candidate verification status via TARVeri API
    Bot->>Discord: 1. Create Archive Role "@Alumni Exco (2025/2026)"
    Bot->>Discord: 2. Transfer outgoing committee to Alumni Role
    Bot->>Discord: 3. Remove active Exco permissions from outgoing
    Bot->>Discord: 4. Assign active Exco roles to incoming committee
    Bot->>DB: Mark Term 2025/2026 as ARCHIVED; Activate Term 2026/2027
    Bot->>Discord: Post public Handover Proclamation Embed in Announcements
```

### Handover Safety Guards
1. **Two-Person Quorum** (implemented): `/term handover` proposes the handover and names the `incoming_chair`. That member or the Server Owner runs `/term confirm-handover`; the proposer cannot confirm and cannot be the incoming chair. `/term cancel-handover` clears a pending proposal. Only one proposal may be pending per society.
2. **Alumni Preservation** (implemented): On confirm, the bot creates `@Alumni Exco (<academic year>)` if missing and grants it to every member on the outgoing roster. The proclamation reports how many grants succeeded. Not yet implemented: removing position roles from outgoing members, because the bot does not create position roles (`discord_role_id` is never set), and assigning incoming roles automatically.
3. **Audit Trail**: Generates a tamper-proof markdown/PDF record of the completed term's tenure.

---

## 🔗 Cross-Bot Integration Specification (TARClub <-> TARVeri)

When both **TARVeri** and **TARClub** operate in the same server, they establish a secure local bridge.

### 1. Auto-Discovery Protocol
Both bots listen for Discord's `on_guild_join` and `on_member_join`:
- TARClub checks: `guild.get_member(TARVERI_BOT_CLIENT_ID) is not None`
- TARVeri checks: `guild.get_member(TARCLUB_BOT_CLIENT_ID) is not None`
- When co-location is detected, both bots post an admin notification:
  > *"✨ Both TARVeri (Verification) and TARClub (Society Management) are detected in this server. Click **[Enable Integration]** to enforce verified student eligibility for committee roles."*

### 2. Manual Pairing Handshake
For multi-server setups (where TARVeri is on the main Campus Hub and TARClub is on a club server):
- Server Owner runs on TARVeri Hub:
  `/admin generate-society-token guild_id:<club_server_id>`
  -> Outputs a 10-minute HMAC token: `TARV-LINK-<hash>`
- Server Owner runs on TARClub Server:
  `/integrate-tarveri link token:<token>`
  -> Handshake established and stored in `tarveri_integrations`.

### 3. Verification Validation API (Local REST / IPC / Direct SQLite Read)

Implemented today: direct read-only SQLite lookup of TARVeri's `verifications` table, batched by Discord ID (`IntegrationService.query_co_located_tarveri_students`). The REST/IPC endpoint below is a planned design and is not built.

#### Local Unix Socket / Loopback REST Endpoint:
`GET /api/v1/tarveri/student?user_id=123456789012345678`

**Headers**:
`Authorization: Bearer <shared_hmac_secret>`

**Response Payload**:
```json
{
  "status": "success",
  "data": {
    "user_id": 123456789012345678,
    "is_verified": true,
    "student_id": "24WMD12345",
    "faculty": "FOCS",
    "academic_level": "DEGREE",
    "is_alumni": false,
    "card_expired": false
  }
}
```

#### Governance Rule Enforcement:
- If `enforce_student_verification == True`:
  - When assigning any position in `/committee assign`, TARClub queries the API.
  - If the candidate is **not** a verified TAR UMT student, the assignment is blocked:
    > *"❌ Cannot appoint @User: They must first be verified as an active TAR UMT student through TARVeri."*
