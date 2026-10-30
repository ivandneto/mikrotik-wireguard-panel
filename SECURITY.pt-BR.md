# 🔒 Segurança

🇺🇸 [English](SECURITY.md) · 🇧🇷 **Português**

> ## ⚠️ NUNCA exponha o painel na internet
> O painel tem acesso de configuração aos seus MikroTiks (VPN, firewall, NAT). Se alguém de fora conseguir abrir a tela de login, ele pode tentar adivinhar a senha e, entrando, mexer nos seus roteadores.
>
> **Acesse o painel só pela rede local ou pela VPN WireGuard.** Para usar de fora de casa, conecte primeiro na VPN e então abra o painel — é para isso que ela existe.

Este guia mostra **como garantir isso** nas duas formas de instalação e como **testar** que ficou fechado.

---

## 1. Regra de ouro: não crie liberação de porta para o painel

O painel só fica exposto na internet se existir um **port forward (dst-nat)** apontando para ele. Então:

- **Não** use a tela "Liberação de portas" (nem o Winbox) para liberar a porta do painel (`8080`, ou a que você usar).
- **Não** coloque o painel numa VPS/servidor com IP público sem firewall.

Confira no MikroTik se não existe nenhuma regra assim (troque `8080` pela sua porta):

```
/ip firewall nat print where dst-port=8080
/ip firewall nat print where to-ports=8080
```

As duas listas devem vir **vazias**. Se aparecer algo, remova.

---

## 2. Versão Docker (PC, servidor, Raspberry Pi)

### 2.1 Publique a porta só no IP da rede local

Por padrão o `docker-compose.yml` publica `8080:8080`, ou seja, em **todas** as interfaces da máquina. Se a máquina tiver mais de uma rede (ou um IP público), restrinja ao IP dela na LAN:

```yaml
    ports:
      - "192.168.88.61:8080:8080"   # troque pelo IP desta máquina na sua rede
```

Se só **este** computador vai abrir o painel, use `127.0.0.1:8080:8080`.

Depois: `docker compose up -d`.

### 2.2 Libere a porta só para a LAN e para a VPN no firewall da máquina

**Windows** (PowerShell como Administrador) — troque `10.10.0.0/24` pela faixa da sua VPN:

```powershell
New-NetFirewallRule -DisplayName "WG Panel (só LAN e VPN)" -Direction Inbound -Protocol TCP -LocalPort 8080 `
  -RemoteAddress LocalSubnet,10.10.0.0/24 -Action Allow
```

**Linux** — atenção: o Docker **ignora o ufw** nas portas publicadas. Por isso o passo 2.1 (publicar só no IP da LAN) é o que realmente protege. Se quiser filtrar por origem também, use a chain `DOCKER-USER`:

```bash
sudo iptables -I DOCKER-USER -p tcp --dport 8080 -j DROP
sudo iptables -I DOCKER-USER -p tcp --dport 8080 -s 192.168.88.0/24 -j ACCEPT
sudo iptables -I DOCKER-USER -p tcp --dport 8080 -s 10.10.0.0/24 -j ACCEPT
```

### 2.3 Fique de fora da internet por padrão

Na configuração de fábrica do MikroTik, o firewall já bloqueia tudo que vem da internet e não tem port forward. Mantenha assim (veja o item 1).

---

## 3. Versão container dentro do MikroTik

O painel fica em `172.20.0.2`, numa rede interna do roteador. Ele **não** é alcançável da internet enquanto não existir port forward (item 1), porque o firewall padrão descarta o que vem da WAN sem dst-nat.

Para deixar **explícito** que só a sua rede local e a VPN entram — mesmo que alguém crie uma regra por engano — cole no terminal do MikroTik (ajuste as faixas):

```
/ip firewall address-list add list=wgpanel-acesso address=192.168.88.0/24 comment="WG Panel: rede local"
/ip firewall address-list add list=wgpanel-acesso address=10.10.0.0/24 comment="WG Panel: VPN"
/ip firewall filter add chain=forward action=drop protocol=tcp dst-address=172.20.0.2 dst-port=8080 src-address-list=!wgpanel-acesso comment="WG Panel: painel so para LAN e VPN" place-before=[:pick [/ip firewall filter find where dynamic=no] 0]
```

(A última linha é um comando só; o `place-before` coloca a regra no topo do firewall, antes das regras que liberam o tráfego.)

A regra descarta qualquer acesso ao painel que **não** venha da rede local ou da VPN — inclusive da internet e dos MikroTiks de clientes.

> Se você usa mais faixas de VPN (ex.: `10.20.0.0/24` para outra interface) e quer abrir o painel por elas, adicione-as na lista `wgpanel-acesso`.

---

## 4. Teste: o painel está mesmo fechado para a internet?

1. No celular, **desligue o Wi-Fi** e **desligue a VPN** (use só o 4G).
2. Abra `http://SEU-DDNS:8080` (ex.: `http://xxxx.sn.mynetname.net:8080`).
3. **Não pode abrir.** Deve dar erro ou ficar carregando até expirar.
4. Agora **ligue a VPN** e abra `http://IP-DO-PAINEL:8080` (ex.: `http://192.168.88.61:8080` ou `http://172.20.0.2:8080`). **Deve abrir.**

Se o passo 3 abrir o painel, ele está exposto: revise o item 1 imediatamente.

---

## 5. Outras boas práticas

- **Senha forte** no `admin` do painel e troque a senha inicial (menu → 🔑 Trocar senha).
- Crie usuários **Somente leitura** para quem só acompanha.
- No MikroTik, use o usuário **`wgpanel`** com grupo limitado (veja `mikrotik-setup.rsc`) — **nunca o admin**.
- Restrinja a API do MikroTik às redes de onde o painel acessa: `/ip service set www-ssl available-from=192.168.88.0/24,10.10.0.0/24` (ajuste as faixas).
- O painel roda em **HTTP**. Dentro da rede local e da VPN (que já é criptografada) isso é aceitável. Se quiser HTTPS, coloque um proxy reverso (Caddy, Nginx) na frente e defina `COOKIE_SECURE=true` no `.env`.
- Guarde com cuidado: o `.env`, a pasta `data/` (banco e `secret.key`) e os backups exportados — eles contêm senhas e as chaves da VPN.
- A opção "Guardar a chave privada dos clientes no MikroTik" faz as chaves irem também para os backups do roteador.
- Mantenha o RouterOS e o painel atualizados.

---

## Encontrou uma falha de segurança?

**Não abra uma Issue pública.** Use **Security → Report a vulnerability** neste repositório (aviso privado). Descreva o problema, como reproduzir e a versão do painel.
