# =====================================================================
#  Preparação do MikroTik para o WG Panel (RouterOS v7)
#  Cole no Terminal do Winbox/WebFig, AJUSTANDO os valores marcados.
# =====================================================================

# 1) Grupo com as permissões mínimas que o painel precisa
#    (se der erro "rest-api", sua versão é anterior à 7.9: remova ",rest-api")
/user group add name=wgpanel policy=read,write,api,rest-api,test,sensitive comment="WG Panel"

# 2) Usuário do painel  >>> TROQUE A SENHA <<<
/user add name=wgpanel group=wgpanel password="troque-esta-senha" comment="WG Panel"

# 3) API REST com HTTPS (certificado autoassinado)
#    >>> troque 192.168.88.1 pelo IP do seu MikroTik e 192.168.88.0/24 pela sua rede <<<
/certificate add name=wgpanel-ca common-name=wgpanel-ca key-usage=key-cert-sign,crl-sign days-valid=3650
/certificate sign wgpanel-ca
/certificate add name=wgpanel-https common-name=192.168.88.1 subject-alt-name=IP:192.168.88.1 days-valid=3650
/certificate sign wgpanel-https ca=wgpanel-ca
/ip service set www-ssl certificate=wgpanel-https disabled=no address=192.168.88.0/24

# --- Alternativa mais simples (HTTP sem criptografia, só na rede interna) ---
# /ip service set www disabled=no address=192.168.88.0/24
# e use MIKROTIK_URL=http://192.168.88.1 no .env

# 4) (Opcional, recomendado) DDNS grátis do MikroTik para acesso remoto
/ip cloud set ddns-enabled=yes
# Depois rode:  /ip cloud print   -> anote o "dns-name" (xxxx.sn.mynetname.net)
