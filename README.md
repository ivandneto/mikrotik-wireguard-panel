# 🛡️ MikroTik WireGuard Panel

🇺🇸 **English** · 🇧🇷 [Português](README.pt-BR.md)

[![Release](https://img.shields.io/github/v/release/ivandneto/mikrotik-wireguard-panel?label=release)](../../releases/latest)
[![MIT License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
![RouterOS 7](https://img.shields.io/badge/RouterOS-7.x-blue)
![Docker](https://img.shields.io/badge/Docker-ready-2496ED)

A web panel to **create and manage WireGuard VPNs**, **connect and manage your clients' MikroTiks**, **open ports (NAT)** for game servers and applications, and **monitor everything**, with alerts on **Telegram** and **Discord**. Runs on **Docker** or as a **container inside the MikroTik itself**.

> 🌐 **9 languages:** the panel (screens, guided tour, built-in manual, error messages and Discord/Telegram alerts) is available in **English, Português (Brasil), Español, Français, Deutsch, Italiano, Русский, 中文 and 日本語**. It follows your browser's language, and you can switch it on the login screen, in the side menu or in Settings.

> 💬 Have an idea or question? Use the **[Discussions](../../discussions)** tab. Found a bug? Open an **[Issue](../../issues/new/choose)**. English and Portuguese are both welcome.

> ⚠️ **Security: never expose the panel to the internet.** Access it only from your local network or through the VPN. See the step-by-step guide in **[🔒 Security](#-security)**.

| Module | What it does |
|---|---|
| 🔐 **WireGuard VPN** | Guided first-time setup, one-click clients, **QR Code** for phones, **.conf** download, **remote** (static IP/DDNS) or **local** (LAN IP) endpoint, "local network only" or "all internet traffic" mode, enable/disable, regenerate keys, **import peers that already exist on the MikroTik** (keys stored on the router → QR works on any panel) |
| 🖧 **Multiple MikroTiks** | Manage your MikroTik and your clients' MikroTiks in the same panel: selector at the top, a **Routers** page with the status of all of them, and alerts tagged with each router's name. A client's MikroTik is reached through the tunnel with a **limited user** (`wgpanel`, random password, login only through the tunnel) created by the script itself — admin passwords are never reused |
| 🖧 **Other MikroTik (site-to-site)** | Multiple WireGuard interfaces (e.g. `wg-home` and `wg-clients`); tunnel to a client's MikroTik including their network, with **➡️ one-way: only I access** (default) or **↔️ two-way**; the panel creates the peer, routes and rules on your MikroTik and generates an **.rsc script** to paste into the client's MikroTik; monitoring and an alert if the client goes down |
| 🎮 **Port forwarding** | Port forwards with **ready-made templates** (Minecraft, CS2, Valheim, Palworld, Rust, ARK, FiveM, Plex…), TCP/UDP/both, port ranges, **IP whitelist**, **temporary rules** (close by themselves), conflict detection with existing rules |
| 📈 **Monitoring** | Who is online, last handshake, source IP, traffic per client, usage per period, traffic per forwarded port, **check whether the server is up**, router CPU/memory/WAN, charts with history |
| 🌐 **WebFig and Terminal** | A **🌐 WebFig** button on each router (and at the top) opens the MikroTik's web page — including the **Terminal** — in a new tab, with your own login. For clients it works through the tunnel |
| 🛠️ **Tools** | **Ping** and **traceroute** run by the MikroTik itself (yours or a client's): does it reach the VPN client, the server, the internet? |
| 🛡️ **Security check** | Checks each router for the usual mistakes — port forward to the panel, missing input firewall, open services, default `admin` user, open DNS — with the command to fix each one. Read-only |
| ⬆️ **RouterOS updates** | Asks each router once a day whether a new RouterOS is available, shows it on the router cards and notifies on Discord/Telegram |
| 📖 **Manual and tour** | Full manual inside the panel, guided tour and contextual help (❓) on every screen |
| 🌐 **9 languages** | English, Português (Brasil), Español, Français, Deutsch, Italiano, Русский, 中文, 日本語 — screens, tour, manual, errors and alerts |
| 📣 **Alerts** | Telegram and Discord: client connected/disconnected, service down/up, rule removed from the MikroTik, temporary port expired, MikroTik offline, RouterOS update available, configuration changed — in the language you choose |
| 👥 **Users** | Multiple users: **administrator** or **read-only** |
| 💾 **Backup** | Export/import everything in the panel (JSON), create `.backup`/`.rsc` backups on the MikroTik and **automatic daily/weekly backups** of each MikroTik's configuration, kept **only when something changed**, with a **What changed** diff |
| 📜 **Audit log** | Record of who created, changed or removed what, and when |

> **Safety:** the panel only changes what it created itself (everything carries the `[wgpanel]` comment). Your existing MikroTik rules are **not touched**.

---

## 📸 Screenshots

> Demo data (fictional routers, clients and IPs). Shown in English — the panel also speaks Português, Español, Français, Deutsch, Italiano, Русский, 中文 and 日本語.

<p align="center"><a href="docs/screenshots/dashboard.png"><img src="docs/screenshots/dashboard.png" alt="Dashboard"></a><br><sub><b>Dashboard</b> — all your routers, CPU/memory, connected VPN clients, WAN traffic and recent events</sub></p>

<table>
  <tr>
    <td width="50%" valign="top"><a href="docs/screenshots/vpn.png"><img src="docs/screenshots/vpn.png" alt="WireGuard VPN"></a><br><sub><b>WireGuard VPN</b> — clients with status, last handshake, source IP and traffic; multiple interfaces as tabs</sub></td>
    <td width="50%" valign="top"><a href="docs/screenshots/qrcode.png"><img src="docs/screenshots/qrcode.png" alt="QR Code"></a><br><sub><b>QR Code</b> — scan with the WireGuard app; remote (DDNS) or local endpoint</sub></td>
  </tr>
  <tr>
    <td width="50%" valign="top"><a href="docs/screenshots/site-to-site.png"><img src="docs/screenshots/site-to-site.png" alt="Other MikroTik (site-to-site)"></a><br><sub><b>Other MikroTik (site-to-site)</b> — tunnels to clients' routers, one-way or two-way</sub></td>
    <td width="50%" valign="top"><a href="docs/screenshots/routers.png"><img src="docs/screenshots/routers.png" alt="Routers"></a><br><sub><b>Routers</b> — several MikroTiks in one panel, RouterOS update notices and WebFig</sub></td>
  </tr>
  <tr>
    <td width="50%" valign="top"><a href="docs/screenshots/port-forwarding.png"><img src="docs/screenshots/port-forwarding.png" alt="Port forwarding"></a><br><sub><b>Port forwarding</b> — whitelist, temporary rules and traffic per port</sub></td>
    <td width="50%" valign="top"><a href="docs/screenshots/new-port.png"><img src="docs/screenshots/new-port.png" alt="Open a port"></a><br><sub><b>Open a port</b> — ready-made templates for game servers and apps</sub></td>
  </tr>
  <tr>
    <td width="50%" valign="top"><a href="docs/screenshots/security.png"><img src="docs/screenshots/security.png" alt="Security check"></a><br><sub><b>Security check</b> — common mistakes on each router, with the command to fix them</sub></td>
    <td width="50%" valign="top"><a href="docs/screenshots/tools.png"><img src="docs/screenshots/tools.png" alt="Tools"></a><br><sub><b>Tools</b> — ping and traceroute run by the MikroTik itself</sub></td>
  </tr>
  <tr>
    <td width="50%" valign="top"><a href="docs/screenshots/backup-diff.png"><img src="docs/screenshots/backup-diff.png" alt="Automatic backup"></a><br><sub><b>Automatic backup</b> — each MikroTik's configuration, kept only when it changed, with a diff</sub></td>
    <td width="50%" valign="top"><a href="docs/screenshots/monitoring.png"><img src="docs/screenshots/monitoring.png" alt="Charts and usage"></a><br><sub><b>Charts and usage</b> — traffic history and usage per VPN client</sub></td>
  </tr>
  <tr>
    <td width="50%" valign="top"><a href="docs/screenshots/alerts.png"><img src="docs/screenshots/alerts.png" alt="Alerts"></a><br><sub><b>Alerts</b> — Telegram and Discord, pick which events notify you</sub></td>
    <td width="50%" valign="top"><a href="docs/screenshots/settings.png"><img src="docs/screenshots/settings.png" alt="Settings"></a><br><sub><b>Settings</b> — per-router options and the language of the screen and of the alerts</sub></td>
  </tr>
  <tr>
    <td width="50%" valign="top"><a href="docs/screenshots/events.png"><img src="docs/screenshots/events.png" alt="Events"></a><br><sub><b>Events</b> — everything that happened, also sent as alerts</sub></td>
    <td width="50%" valign="top"><a href="docs/screenshots/manual.png"><img src="docs/screenshots/manual.png" alt="Built-in manual"></a><br><sub><b>Built-in manual</b> — every screen explained, in 9 languages</sub></td>
  </tr>
</table>

<p align="center"><a href="docs/screenshots/mobile.png"><img src="docs/screenshots/mobile.png" alt="Mobile" width="260"></a>&nbsp;&nbsp;&nbsp;<a href="docs/screenshots/tour.png"><img src="docs/screenshots/tour.png" alt="Tour" width="520"></a><br><sub><b>Works on your phone</b> · <b>Guided tour</b> on first login</sub></p>

---

## Choose your installation

The panel runs in two ways. Both have **exactly the same features**; only where it runs changes. Ready-made files are in **[Releases](../../releases/latest)**.

| | 🐳 **Docker** (PC, server, Raspberry Pi) | 🖧 **Container on the MikroTik** |
|---|---|---|
| Where it runs | Any machine with Docker, on 24/7 | Inside the router itself (RouterOS 7, ARM64 — e.g. RB5009) |
| Release file | `wgpanel-docker-amd64-vX.Y.Z.tar.gz` + `docker-compose.yml` + `env.example` | `wgpanel-mikrotik-arm64-vX.Y.Z.tar` |
| Requires | Docker | `container` package on RouterOS + USB flash drive/SSD |
| "MikroTik down" alert | ✅ Works (the panel lives outside the router) | ❌ Not available (if the router goes down, the panel goes with it) |
| Updating | Download the new Release → `docker load` → `docker compose up -d` | Replace the container image (stop → remove → add → start) |
| Guide | [2. Start the panel](#2-start-the-panel) | [Running inside the MikroTik](#running-inside-the-mikrotik-container) |

Either way, start with [1. Prepare the MikroTik](#1-prepare-the-mikrotik-5-minutes).

---

## How it works

```
 Browser ──► WG Panel (Docker) ──► REST API ──► MikroTik (RouterOS v7)
                  │
                  └─ every 30s collects status/traffic, stores history and sends alerts
```

The panel uses the **RouterOS v7 REST API**. Nothing needs to be installed on the router — just create a user and enable the web service.

---

## 1. Prepare the MikroTik (5 minutes)

Open **Winbox → New Terminal** and paste the commands from [`mikrotik-setup.rsc`](mikrotik-setup.rsc), **after changing**:

- the password of the `wgpanel` user;
- `192.168.88.1` to your MikroTik's IP;
- `192.168.88.0/24` to your local network.

The script does the following:

1. Creates the `wgpanel` group with the required permissions (`ftp` = file access for the automatic backup and the update check; it does **not** enable the FTP service).
2. Creates the `wgpanel` user.
3. Enables the REST API over HTTPS (`www-ssl`), reachable **only from your network**.
4. Enables MikroTik's free DDNS (`xxxx.sn.mynetname.net`), useful if you don't have a static IP.

> 💡 If step 1 fails with an error about `rest-api`, your version is older than 7.9: remove `,rest-api` from the command.

To check your DDNS address: `/ip cloud print` → `dns-name` field.

---

## 2. Start the panel

Requirement: **Docker** (Docker Desktop on Windows, or Docker on any Linux or Raspberry Pi).

**Option A — prebuilt image from the Release (x86-64 PC/server, fastest):** download `wgpanel-docker-amd64-vX.Y.Z.tar.gz` (X.Y.Z = version), `docker-compose.yml` and `env.example` (rename it to `.env`) from the [latest Release](../../releases/latest) into a folder and run:
```bash
docker load -i wgpanel-docker-amd64-v1.3.0.tar.gz     # use the exact name of the downloaded file
```

**Option B — from source** (any machine, including Raspberry Pi): clone the repository; step 2 below uses `--build` to build the image.

```bash
# 1. Copy the configuration file and edit it
cp .env.example .env        # on Windows: copy .env.example .env
```

Edit `.env`:

```ini
MIKROTIK_URL=https://192.168.88.1      # your MikroTik's IP
MIKROTIK_USER=wgpanel
MIKROTIK_PASSWORD=the-password-you-set
ADMIN_PASSWORD=a-strong-password-for-the-panel
```

```bash
# 2. Start it (Option A: without --build, uses the loaded image)
docker compose up -d --build

# 3. Check the logs (if you didn't set ADMIN_PASSWORD, the generated password shows up here)
docker compose logs -f
```

Open **http://COMPUTER-IP:8080** and log in with `admin` and the password.

> ⚠️ Keep the panel reachable **only from the local network and the VPN** — see **[🔒 Security](#-security)** (do not create a port forward to port 8080).

> The panel must stay on to monitor and send alerts. Ideally run it on something that is on 24/7 (mini PC, Raspberry Pi, server or VPS). If you run it on your PC, monitoring stops when it is off, but the VPN and the forwarded ports keep working, because they live on the MikroTik.

---

## 3. Set up the VPN

> **New or reinstalled panel, but the MikroTik already has WireGuard?** The screen shows **"This MikroTik already has WireGuard"** — click **Adopt and import clients** and the interface and clients come back to the panel (with QR Code, if the keys are stored on the MikroTik). Or restore a backup in **Restore**.

1. Go to **WireGuard VPN → Start setup**.
2. The wizard suggests the values:
   - **UDP port** (default 13231).
   - **VPN range** (default `10.10.0.1/24`). Use a range that does **not** exist on your network.
   - **Remote address:** your static IP or DDNS, used away from home.
   - **Local address:** the MikroTik's IP on the network, used at home.
3. Click **Apply to MikroTik**. The panel creates the interface, the IP and the firewall rule, and adds the VPN to the LAN list.
4. Click **＋ New device**, give it a name and scan the QR Code with the **WireGuard** app.

### Remote × Local

Each client can download two files/QR codes:

| | When to use | Endpoint |
|---|---|---|
| 🌍 **Remote** | Away from the network (4G, another house, work) | Public IP or DDNS |
| 🏠 **Local** | Inside the same network as the MikroTik | MikroTik's local IP |

### Local network only × All internet traffic

- **Local network only** (split tunnel): the device reaches your network (NAS, cameras, servers) and its internet traffic keeps going out through its own connection. Faster.
- **All internet traffic** (full tunnel): all traffic goes out through your MikroTik. Good for public Wi-Fi.

---

## 4. Open ports (game servers/applications)

1. **Pin the server's IP:** IP → DHCP Server → Leases → click the server → **Make Static**.
2. **Port forwarding → Open port**, pick a **template** (or fill it in manually) and enter the server's IP.
3. Optional:
   - **Whitelist:** only the listed IPs can connect (great for RDP/SSH or a server among friends).
   - **Close automatically:** e.g. open it only for the weekend gaming session.
   - **Monitor whether it is up:** the panel tests the TCP port and alerts you if the server goes down.

> ⚠️ **CGNAT:** if your ISP uses CGNAT (the MikroTik's WAN IP starts with `100.64` to `100.127`, or differs from the IP shown on "what is my IP" sites), port forwarding and inbound VPN **will not work**. Ask your ISP for a public IP (many provide one for free on request).

### NAT settings (Settings → Port forwarding)

| Mode | When to use |
|---|---|
| **Interface list (WAN)** | MikroTik default (factory configuration). Recommended. |
| **Specific interface** | If you don't use the WAN list; enter `ether1`, `pppoe-out1` etc. |
| **Destination = router IP** | Also works from inside the network using the public IP (for full hairpin NAT, also create a masquerade rule). |

---

## 5. Discord and Telegram alerts

**Discord:**
1. On your server, open the channel settings → **Integrations → Webhooks → New Webhook → Copy Webhook URL**.
2. In the panel, go to **Alerts → Discord**, paste the URL, enable it and click **Save and send test**.

**Telegram:**
1. Talk to **@BotFather** → `/newbot` → copy the **token**.
2. Send any message to your bot.
3. Open `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy the number in `"chat":{"id": ...}`.
4. In the panel, go to **Alerts → Telegram**, fill it in, enable it and click **Save and send test**.

Choose which events trigger alerts. All of them are recorded in the **Events** tab.

---

## Backup and updates

- **Panel backup:** **Backup → Download backup**. It contains the VPN private keys, so store it somewhere safe.
- **Automatic MikroTik backup:** on by default. Every day (or week) the panel exports each router's configuration (without passwords) into `data/router-backups/` and keeps a copy only when something changed. See and compare them in **Backup → 🕒 Automatic MikroTik backup**.
- **Upgrading from 1.2 or older?** The automatic backup and the update check need the `ftp` permission on the `wgpanel` user. As admin, in the MikroTik terminal: `/user group set [find name=wgpanel] policy=read,write,api,rest-api,test,sensitive,ftp`
- **Data lives in** `./data` (SQLite database + `secret.key`). Copy this folder for a full backup. **Without `secret.key` the stored keys cannot be read** (unless you restore from the exported JSON).
- **Update the panel:** download the new Release and run `docker load -i wgpanel-docker-amd64-vX.Y.Z.tar.gz` and `docker compose up -d` (or, from source, `docker compose up -d --build`). The database is migrated automatically.
- **Reinstall or move to another machine:** restore the backup in **Backup → Restore** (brings everything back). Without a backup, use **Adopt and import clients** in **WireGuard VPN**: the interface and clients come back from the MikroTik itself.

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| "MikroTik offline" | Wrong `MIKROTIK_URL`, `www-ssl`/`www` service disabled, or the service's `address=` does not include the panel computer's IP |
| "Invalid MikroTik username or password" | Wrong password in `.env`, or group without `rest-api`/`api` |
| VPN client connects but has no internet | "Treat VPN as LAN" was unchecked in the wizard, or your own firewall rules block it; check it and reapply with ⚙️ (**Reconfigure interface**) |
| VPN works at home but not away | Using the "local" QR away from home, CGNAT, or UDP port blocked by the ISP |
| Forwarded port doesn't work | Server IP changed (make the lease static), Windows firewall on the server blocking, or CGNAT |
| "Missing on MikroTik" | Someone deleted the rule/peer directly in Winbox; click 🔁 **Recreate** |

---

## Running inside the MikroTik (container)

Tested on an **RB5009 with RouterOS 7.24.4**: the panel uses ~60 MB of RAM and the image is ~30 MB. A **USB flash drive or SSD** is required (the internal flash can't handle constant writes).

> Downside: if the router hangs, the panel goes down with it, so there is no "MikroTik unreachable" alert.

**1. Get the ARM64 image:** download `wgpanel-mikrotik-arm64-vX.Y.Z.tar` from the [latest Release](../../releases/latest) (X.Y.Z = version) — or build it from source (on a PC with Docker):
```bash
bash scripts/build-release.sh   # creates dist/release/wgpanel-mikrotik-arm64-vX.Y.Z.tar (and the Docker version)
```

**2. Prepare the router** (terminal, administrator user):
```
/system device-mode update container=yes          # confirm by pressing the button/rebooting if asked
/tool fetch url="https://download.mikrotik.com/routeros/<VERSION>/container-<VERSION>-arm64.npk"
/disk format usb1 file-system=ext4 label=wgpanel  # ERASES the flash drive
/system reboot                                    # reboot with the command, not by pulling the plug
```

**3. Container network:**
```
/interface bridge add name=containers
/ip address add address=172.20.0.1/24 interface=containers
/interface veth add name=veth-wgpanel address=172.20.0.2/24 gateway=172.20.0.1
/interface bridge port add bridge=containers interface=veth-wgpanel
/interface list member add list=LAN interface=containers
/ip service set www-ssl available-from=192.168.88.0/24,10.10.0.0/24,172.20.0.0/24
```

**4. Configuration** (change the passwords):
```
/container config set tmpdir=usb1/tmp
/container envs add list=wgpanel key=MIKROTIK_URL value=https://172.20.0.1
/container envs add list=wgpanel key=MIKROTIK_USER value=wgpanel
/container envs add list=wgpanel key=MIKROTIK_PASSWORD value="WGPANEL-PASSWORD"
/container envs add list=wgpanel key=ADMIN_PASSWORD value="PANEL-PASSWORD"
/container envs add list=wgpanel key=POLL_INTERVAL value=60
/container mounts add list=wgpanel src=usb1/wgpanel/data dst=/data
```

**5. Upload and start:** drag the `.tar` into `usb1` in Winbox's **Files** window and run the command below, **replacing `1.3.0` with the file's version** (it goes in the file name and in the container comment):
```
/container add name=wgpanel file=usb1/wgpanel-mikrotik-arm64-v1.3.0.tar comment="WG Panel v1.3.0" interface=veth-wgpanel root-dir=usb1/wgpanel/root envlists=wgpanel mountlists=wgpanel dns=172.20.0.1 start-on-boot=yes logging=yes memory-max=256M
/container start wgpanel      # once /container print shows extraction has finished
```
`/container print` now shows the version in the comment, and the panel shows the version in the sidebar (below "WG Panel").
Open **http://172.20.0.2:8080** (only from the local network or VPN — add the rule from [SECURITY.md](SECURITY.md), item 3). To bring data over from an existing panel: **Backup → Download backup** on the old one, **Backup → Restore** on the new one (brings everything). If you only need the VPN, open **WireGuard VPN** on the new panel and click **Adopt and import clients** — with the "Store the private key on the MikroTik" option enabled, clients come with a QR Code. Turn off the old panel so you don't get duplicate alerts.

**Update to a new version** (data stays in `usb1/wgpanel/data` and is not erased):
```
/container stop wgpanel
/container remove wgpanel
/file remove [find name~"usb1/wgpanel-mikrotik-arm64"]
```
Drag the new `.tar` (e.g. `wgpanel-mikrotik-arm64-v1.3.1.tar`) into `usb1` and run the `/container add …` from step 5 with the new version in the file name and comment; then `/container start wgpanel`. Check the version in the panel's sidebar.

> On RouterOS older than 7.20, replace `available-from` with `address`, and `list=`/`envlists=`/`mountlists=` with `name=`/`envlist=`/`mounts=`.

---

## Running without Docker (optional)

```bash
python -m venv .venv
.venv/Scripts/activate        # Linux: source .venv/bin/activate
pip install -r requirements.txt
# set the .env variables in the environment, then:
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

API documentation: `http://IP:8080/api/docs`

## Project structure

```
app/
  main.py                 # FastAPI application
  mikrotik.py             # RouterOS REST API client (one per router)
  routerctx.py            # which router the request is using (panel selector)
  db.py                   # models (SQLite) and automatic migrations
  routers/                # endpoints: mikrotiks (routers), wireguard, nat, monitor,
                          #            alerts, users, backup, settings, auth
  services/collector.py   # periodic collection from all routers + history + alerts
  services/alerts.py      # Telegram / Discord
  services/nat_ops.py     # creating/removing NAT rules
  services/router_ops.py  # removing routers from the panel
  services/router_tools.py # ping, traceroute, security check, config export, update check
  services/auto_tasks.py  # automatic backups and daily update check
  i18n.py + locales/      # server messages in each language
static/                   # web interface (Alpine.js + Tailwind + Chart.js)
  i18n.js + i18n/         # translations of screens, tour and manual (one file per language)
scripts/i18n_extract.py   # lists the phrases each language is still missing
Dockerfile                # Docker image (PC/server)
Dockerfile.mikrotik       # slim ARM64 image for the RouterOS container
mikrotik-setup.rsc        # router preparation
```

---

## 🔒 Security

> ### ⚠️ NEVER expose the panel to the internet
> It has configuration access to your MikroTiks. Access it **only from the local network or through the WireGuard VPN** — to use it from outside, connect to the VPN first.

**Step by step to keep the panel reachable only from the local network and the VPN** (full details and commands in **[SECURITY.md](SECURITY.md)**):

1. **Never create a port forward to the panel.** Check on the MikroTik that these lists come back empty:
   ```
   /ip firewall nat print where dst-port=8080
   /ip firewall nat print where to-ports=8080
   ```
2. **Docker:** publish the port only on the LAN IP (`"192.168.88.61:8080:8080"` in `docker-compose.yml`) and allow the port in the machine's firewall only for the local network and the VPN range.
3. **Container on the MikroTik:** add the rule that only lets the LAN and the VPN reach `172.20.0.2:8080` (commands in SECURITY.md, item 3).
4. **Test:** on your phone, using only 4G and **without VPN**, open `http://YOUR-DDNS:8080` — **it must not open**. Turn on the VPN and open it via the internal IP — **it must open**.

Also: a strong password for the panel's `admin`, a limited `wgpanel` user on the MikroTik (never the admin), the MikroTik API restricted to your networks, and care with `.env`, `data/` and backups. Security issues: report them privately (see SECURITY.md).

## 💬 Ideas, questions and contributions

- **[Discussions](../../discussions)** — ideas, installation/usage questions and sharing experiences.
- **[Issues](../../issues/new/choose)** — forms to 💡 suggest a feature or 🐛 report a bug.
- **Pull requests** are welcome. For big changes, open a discussion first.
- **Translations:** each language is one file in `static/i18n/` (screens, tour and manual) and one in `app/locales/` (server messages); the key is the original Portuguese text. Run `python scripts/i18n_extract.py` to see what a language is missing.

When reporting something, **never paste passwords, VPN keys, the `.env` file or backups**.

## License and disclaimer

Distributed under the **[MIT](LICENSE)** license.

Independent project, **not affiliated with, sponsored or endorsed by MikroTik**. MikroTik, RouterOS, WebFig and Winbox are trademarks of Mikrotīkls SIA. WireGuard is a registered trademark of Jason A. Donenfeld. Use at your own risk and back up your router before major changes.
