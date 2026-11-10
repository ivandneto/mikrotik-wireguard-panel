# ==================================================================
#  MikroTik preparation for WG Panel (RouterOS v7)
#  Paste into the Winbox/WebFig Terminal, CHANGING the marked values.
# ==================================================================

# 1) Group with the minimum permissions the panel needs
#    (if you get a "rest-api" error, your version is older than 7.9: remove ",rest-api")
/user group add name=wgpanel policy=read,write,api,rest-api,test,sensitive comment="WG Panel"

# 2) Panel user  >>> CHANGE THE PASSWORD <<<
/user add name=wgpanel group=wgpanel password="change-this-password" comment="WG Panel"

# 3) REST API over HTTPS (self-signed certificate)
#    >>> replace 192.168.88.1 with your MikroTik's IP and 192.168.88.0/24 with your network <<<
/certificate add name=wgpanel-ca common-name=wgpanel-ca key-usage=key-cert-sign,crl-sign days-valid=3650
/certificate sign wgpanel-ca
/certificate add name=wgpanel-https common-name=192.168.88.1 subject-alt-name=IP:192.168.88.1 days-valid=3650
/certificate sign wgpanel-https ca=wgpanel-ca
#    RouterOS 7.20+ uses "available-from"; on older versions replace it with "address".
#    10.10.0.0/24 = the VPN range: lets whoever is connected to the VPN open WebFig/Winbox.
/ip service set www-ssl certificate=wgpanel-https disabled=no available-from=192.168.88.0/24,10.10.0.0/24

# --- Simpler alternative (HTTP without encryption, internal network only) ---
# /ip service set www disabled=no available-from=192.168.88.0/24,10.10.0.0/24
# and use MIKROTIK_URL=http://192.168.88.1 in .env

# 4) (Optional, recommended) MikroTik's free DDNS for remote access
/ip cloud set ddns-enabled=yes
# Then run:  /ip cloud print   -> note the "dns-name" (xxxx.sn.mynetname.net)
