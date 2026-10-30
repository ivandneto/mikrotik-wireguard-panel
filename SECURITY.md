# 🔒 Security

🇺🇸 **English** · 🇧🇷 [Português](SECURITY.pt-BR.md)

> ## ⚠️ NEVER expose the panel to the internet
> The panel has configuration access to your MikroTiks (VPN, firewall, NAT). If someone outside can reach the login page, they can try to guess the password and, once in, change your routers.
>
> **Access the panel only from the local network or through the WireGuard VPN.** To use it away from home, connect to the VPN first and then open the panel — that's what the VPN is for.

This guide shows **how to make sure of that** for both installation types and how to **test** that it is closed.

---

## 1. Golden rule: do not create a port forward to the panel

The panel is only exposed to the internet if there is a **port forward (dst-nat)** pointing to it. So:

- **Do not** use the "Liberação de portas" (*Port forwarding*) screen (or Winbox) to open the panel's port (`8080`, or whichever you use).
- **Do not** put the panel on a VPS/server with a public IP and no firewall.

Check on the MikroTik that no such rule exists (replace `8080` with your port):

```
/ip firewall nat print where dst-port=8080
/ip firewall nat print where to-ports=8080
```

Both lists must come back **empty**. If anything shows up, remove it.

---

## 2. Docker version (PC, server, Raspberry Pi)

### 2.1 Publish the port only on the local network IP

By default `docker-compose.yml` publishes `8080:8080`, i.e. on **all** of the machine's interfaces. If the machine has more than one network (or a public IP), restrict it to its LAN IP:

```yaml
    ports:
      - "192.168.88.61:8080:8080"   # replace with this machine's IP on your network
```

If only **this** computer will open the panel, use `127.0.0.1:8080:8080`.

Then: `docker compose up -d`.

### 2.2 Allow the port only for the LAN and the VPN in the machine's firewall

**Windows** (PowerShell as Administrator) — replace `10.10.0.0/24` with your VPN range:

```powershell
New-NetFirewallRule -DisplayName "WG Panel (LAN and VPN only)" -Direction Inbound -Protocol TCP -LocalPort 8080 `
  -RemoteAddress LocalSubnet,10.10.0.0/24 -Action Allow
```

**Linux** — note: Docker **bypasses ufw** for published ports. That's why step 2.1 (publish only on the LAN IP) is what actually protects you. If you also want to filter by source, use the `DOCKER-USER` chain:

```bash
sudo iptables -I DOCKER-USER -p tcp --dport 8080 -j DROP
sudo iptables -I DOCKER-USER -p tcp --dport 8080 -s 192.168.88.0/24 -j ACCEPT
sudo iptables -I DOCKER-USER -p tcp --dport 8080 -s 10.10.0.0/24 -j ACCEPT
```

### 2.3 Stay off the internet by default

With MikroTik's factory configuration, the firewall already blocks everything coming from the internet that has no port forward. Keep it that way (see item 1).

---

## 3. Container version inside the MikroTik

The panel lives at `172.20.0.2`, on an internal network of the router. It is **not** reachable from the internet as long as there is no port forward (item 1), because the default firewall drops what comes from the WAN without dst-nat.

To make it **explicit** that only your local network and the VPN get in — even if someone creates a rule by mistake — paste this into the MikroTik terminal (adjust the ranges):

```
/ip firewall address-list add list=wgpanel-acesso address=192.168.88.0/24 comment="WG Panel: local network"
/ip firewall address-list add list=wgpanel-acesso address=10.10.0.0/24 comment="WG Panel: VPN"
/ip firewall filter add chain=forward action=drop protocol=tcp dst-address=172.20.0.2 dst-port=8080 src-address-list=!wgpanel-acesso comment="WG Panel: panel only for LAN and VPN" place-before=[:pick [/ip firewall filter find where dynamic=no] 0]
```

(The last line is a single command; `place-before` puts the rule at the top of the firewall, before the rules that accept traffic.)

The rule drops any access to the panel that does **not** come from the local network or the VPN — including from the internet and from clients' MikroTiks.

> If you use more VPN ranges (e.g. `10.20.0.0/24` for another interface) and want to open the panel through them, add them to the `wgpanel-acesso` list.

---

## 4. Test: is the panel really closed to the internet?

1. On your phone, **turn off Wi-Fi** and **turn off the VPN** (use only 4G).
2. Open `http://YOUR-DDNS:8080` (e.g. `http://xxxx.sn.mynetname.net:8080`).
3. **It must not open.** It should fail or keep loading until it times out.
4. Now **turn on the VPN** and open `http://PANEL-IP:8080` (e.g. `http://192.168.88.61:8080` or `http://172.20.0.2:8080`). **It must open.**

If step 3 opens the panel, it is exposed: review item 1 immediately.

---

## 5. Other good practices

- A **strong password** for the panel's `admin`, and change the initial password (menu → 🔑 Trocar senha / *Change password*).
- Create **read-only** users for people who only need to watch.
- On the MikroTik, use the **`wgpanel`** user with a limited group (see `mikrotik-setup.rsc`) — **never the admin**.
- Restrict the MikroTik API to the networks the panel connects from: `/ip service set www-ssl available-from=192.168.88.0/24,10.10.0.0/24` (adjust the ranges).
- The panel runs over **HTTP**. Inside the local network and the VPN (which is already encrypted) this is acceptable. If you want HTTPS, put a reverse proxy (Caddy, Nginx) in front of it and set `COOKIE_SECURE=true` in `.env`.
- Keep these safe: `.env`, the `data/` folder (database and `secret.key`) and exported backups — they contain passwords and VPN keys.
- The "Store the clients' private key on the MikroTik" option means the keys also end up in the router's backups.
- Keep RouterOS and the panel up to date.

---

## Found a security vulnerability?

**Do not open a public Issue.** Use **Security → Report a vulnerability** in this repository (private report). Describe the problem, how to reproduce it and the panel version. English or Portuguese is fine.
