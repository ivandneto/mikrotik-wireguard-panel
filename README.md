# 🛡️ MikroTik WG Panel

Painel web para **criar e gerenciar VPN WireGuard**, **liberar portas (NAT)** para servidores de jogos e aplicações, e **monitorar tudo** no seu MikroTik, com alertas no **Telegram** e no **Discord**.

| Módulo | O que faz |
|---|---|
| 🔐 **VPN WireGuard** | Configuração inicial guiada, criação de clientes com 1 clique, **QR Code** para celular, download do **.conf**, endereço **remoto** (IP fixo/DDNS) ou **local** (IP na LAN), modo "só rede local" ou "toda a internet", ativar/desativar, regenerar chaves |
| 🎮 **Liberação de portas** | Port forward com **modelos prontos** (Minecraft, CS2, Valheim, Palworld, Rust, ARK, FiveM, Plex…), TCP/UDP/ambos, faixas de portas, **whitelist de IPs**, **liberação temporária** (fecha sozinha), detecção de conflito com regras existentes |
| 📈 **Monitoramento** | Quem está online, último handshake, IP de origem, tráfego por cliente, consumo por período, tráfego por porta liberada, **teste se o servidor está no ar**, CPU/memória/WAN do roteador, gráficos com histórico |
| 📣 **Alertas** | Telegram e Discord: cliente conectou/desconectou, serviço caiu/voltou, regra sumiu do MikroTik, porta temporária expirou, MikroTik offline |
| 👥 **Usuários** | Vários usuários: **administrador** ou **somente leitura** |
| 💾 **Backup** | Exporta/importa tudo do painel (JSON) e cria backup `.backup`/`.rsc` dentro do MikroTik |
| 📜 **Auditoria** | Registro de quem criou, alterou ou removeu o quê, e quando |

> **Segurança:** o painel só altera o que ele mesmo criou (tudo leva o comentário `[wgpanel]`). Suas regras atuais do MikroTik **não são tocadas**.

---

## Como funciona

```
 Navegador ──► WG Panel (Docker) ──► API REST ──► MikroTik (RouterOS v7)
                    │
                    └─ a cada 30s coleta status/tráfego, guarda histórico e envia alertas
```

O painel usa a **API REST do RouterOS v7**. Não precisa instalar nada no roteador, só criar um usuário e ativar o serviço web.

---

## 1. Preparar o MikroTik (5 minutos)

Abra o **Winbox → New Terminal** e cole os comandos do arquivo [`mikrotik-setup.rsc`](mikrotik-setup.rsc), **trocando antes**:

- a senha do usuário `wgpanel`;
- `192.168.88.1` pelo IP do seu MikroTik;
- `192.168.88.0/24` pela sua rede local.

O script faz o seguinte:

1. Cria o grupo `wgpanel` com as permissões necessárias.
2. Cria o usuário `wgpanel`.
3. Ativa a API REST via HTTPS (`www-ssl`), acessível **só da sua rede**.
4. Ativa o DDNS grátis do MikroTik (`xxxx.sn.mynetname.net`), útil se você não tem IP fixo.

> 💡 Se aparecer erro com `rest-api` no passo 1, sua versão é anterior à 7.9: remova `,rest-api` do comando.

Para conferir o endereço DDNS: `/ip cloud print` → campo `dns-name`.

---

## 2. Subir o painel

Requisito: **Docker** (Docker Desktop no Windows, ou Docker em qualquer Linux ou Raspberry Pi).

```bash
# 1. Copie o arquivo de configuração e edite
cp .env.example .env        # no Windows: copy .env.example .env
```

Edite o `.env`:

```ini
MIKROTIK_URL=https://192.168.88.1      # IP do seu MikroTik
MIKROTIK_USER=wgpanel
MIKROTIK_PASSWORD=a-senha-que-voce-definiu
ADMIN_PASSWORD=uma-senha-forte-para-o-painel
```

```bash
# 2. Suba
docker compose up -d --build

# 3. Veja os logs (se não definiu ADMIN_PASSWORD, a senha gerada aparece aqui)
docker compose logs -f
```

Acesse **http://IP-DO-COMPUTADOR:8080** e entre com `admin` e a senha.

> O painel precisa ficar ligado para monitorar e enviar alertas. O ideal é rodar em algo 24h (mini PC, Raspberry Pi, servidor ou VPS). Se rodar no seu PC, o monitoramento para quando ele desligar, mas a VPN e as portas continuam funcionando, porque ficam no MikroTik.

---

## 3. Configurar a VPN

1. Vá em **VPN WireGuard → Iniciar configuração**.
2. O assistente já sugere os valores:
   - **Porta UDP** (padrão 13231).
   - **Faixa da VPN** (padrão `10.10.0.1/24`). Use uma faixa que **não** exista na sua rede.
   - **Endereço remoto:** seu IP fixo ou DDNS, usado fora de casa.
   - **Endereço local:** IP do MikroTik na rede, usado dentro de casa.
3. Clique em **Aplicar no MikroTik**. O painel cria a interface, o IP e a regra de firewall, e coloca a VPN na lista LAN.
4. Clique em **Novo cliente**, dê um nome e escaneie o QR Code com o app **WireGuard**.

### Remoto × Local

Cada cliente pode baixar dois arquivos/QR codes:

| | Quando usar | Endpoint |
|---|---|---|
| 🌍 **Remoto** | Fora da rede (4G, outra casa, trabalho) | IP público ou DDNS |
| 🏠 **Local** | Dentro da mesma rede do MikroTik | IP local do MikroTik |

### Só rede local × Toda a internet

- **Só rede local** (split tunnel): o dispositivo acessa sua rede (NAS, câmeras, servidores) e a internet continua saindo pela conexão dele. Mais rápido.
- **Toda a internet** (full tunnel): todo o tráfego sai pelo seu MikroTik. Bom para Wi-Fi público.

---

## 4. Liberar portas (servidor de jogos/aplicações)

1. **Fixe o IP do servidor:** IP → DHCP Server → Leases → clique no servidor → **Make Static**.
2. **Liberação de portas → Liberar porta**, escolha um **modelo pronto** (ou preencha manualmente) e informe o IP do servidor.
3. Opcional:
   - **Whitelist:** só os IPs informados conseguem acessar (ótimo para RDP/SSH ou servidor entre amigos).
   - **Fechar automaticamente:** por exemplo, liberar só para a jogatina do fim de semana.
   - **Monitorar se está no ar:** o painel testa a porta TCP e alerta se o servidor cair.

> ⚠️ **CGNAT:** se a sua operadora usa CGNAT (o IP da WAN do MikroTik começa com `100.64` a `100.127`, ou é diferente do IP que aparece em sites como "meu ip"), liberação de porta e VPN de entrada **não funcionam**. Peça um IP público à operadora (muitas fornecem de graça ao pedir).

### Configuração do NAT (Configurações → Liberação de portas)

| Modo | Quando usar |
|---|---|
| **Lista de interfaces (WAN)** | Padrão do MikroTik (configuração de fábrica). Recomendado. |
| **Interface específica** | Se você não usa a lista WAN; informe `ether1`, `pppoe-out1` etc. |
| **Destino = IP do roteador** | Funciona também de dentro da rede acessando pelo IP público (para hairpin completo, crie também uma regra de masquerade). |

---

## 5. Alertas no Discord e Telegram

**Discord:**
1. No servidor, abra Configurações do canal → **Integrações → Webhooks → Novo webhook → Copiar URL**.
2. No painel, vá em **Alertas → Discord**, cole a URL, ative e clique em **Salvar e enviar teste**.

**Telegram:**
1. Fale com o **@BotFather** → `/newbot` → copie o **token**.
2. Mande qualquer mensagem para o seu bot.
3. Abra `https://api.telegram.org/bot<TOKEN>/getUpdates` e copie o número em `"chat":{"id": ...}`.
4. No painel, vá em **Alertas → Telegram**, preencha, ative e clique em **Salvar e enviar teste**.

Escolha quais eventos geram alerta. Todos ficam registrados na aba **Eventos**.

---

## Backup e atualização

- **Backup do painel:** aba **Backup → Baixar backup**. Contém as chaves privadas da VPN, então guarde em local seguro.
- **Dados ficam em** `./data` (banco SQLite + `secret.key`). Copie essa pasta para fazer backup completo. **Sem o `secret.key` as chaves salvas não podem ser lidas** (a não ser que você restaure pelo JSON exportado).
- **Atualizar o painel:** `docker compose up -d --build`.

---

## Problemas comuns

| Sintoma | Causa provável |
|---|---|
| "MikroTik offline" | `MIKROTIK_URL` errado, serviço `www-ssl`/`www` desativado, ou `address=` do serviço não inclui o IP do computador do painel |
| "Usuário ou senha do MikroTik inválidos" | Senha no `.env` ou grupo sem `rest-api`/`api` |
| Cliente VPN conecta mas não navega | Desmarcou "tratar VPN como LAN" no assistente, ou o firewall tem regras próprias que bloqueiam; marque e reaplique em **Reconfigurar** |
| VPN funciona em casa mas não fora | Usando o QR "local" fora de casa, CGNAT, ou porta UDP bloqueada pela operadora |
| Porta liberada não funciona | IP do servidor mudou (faça o lease estático), firewall do Windows do servidor bloqueando, ou CGNAT |
| "Ausente no MikroTik" | Alguém apagou a regra/peer direto no Winbox; clique em 🔁 **Recriar** |

---

## Rodar dentro do próprio MikroTik (container)

Testado em um **RB5009 com RouterOS 7.24.4**: o painel usa ~60 MB de RAM e a imagem tem ~30 MB. É preciso um **pendrive ou SSD USB** (a flash interna não aguenta gravação constante).

> Desvantagem: se o roteador travar, o painel cai junto, então não existe o alerta "MikroTik inacessível".

**1. Gerar a imagem ARM64** (no PC, com Docker):
```bash
docker buildx build --platform linux/arm64 -f Dockerfile.mikrotik -t wgpanel:arm64 \
  --output type=docker,dest=dist/wgpanel-arm64.tar .
```

**2. Preparar o roteador** (terminal, usuário administrador):
```
/system device-mode update container=yes          # confirme apertando o botão/reiniciando, se pedir
/tool fetch url="https://download.mikrotik.com/routeros/<VERSÃO>/container-<VERSÃO>-arm64.npk"
/disk format usb1 file-system=ext4 label=wgpanel  # APAGA o pendrive
/system reboot                                    # reinicie pelo comando, não pela tomada
```

**3. Rede do container:**
```
/interface bridge add name=containers
/ip address add address=172.20.0.1/24 interface=containers
/interface veth add name=veth-wgpanel address=172.20.0.2/24 gateway=172.20.0.1
/interface bridge port add bridge=containers interface=veth-wgpanel
/interface list member add list=LAN interface=containers
/ip service set www-ssl available-from=192.168.88.0/24,10.10.0.0/24,172.20.0.0/24
```

**4. Configuração** (troque as senhas):
```
/container config set tmpdir=usb1/tmp
/container envs add list=wgpanel key=MIKROTIK_URL value=https://172.20.0.1
/container envs add list=wgpanel key=MIKROTIK_USER value=wgpanel
/container envs add list=wgpanel key=MIKROTIK_PASSWORD value="SENHA-DO-WGPANEL"
/container envs add list=wgpanel key=ADMIN_PASSWORD value="SENHA-DO-PAINEL"
/container envs add list=wgpanel key=POLL_INTERVAL value=60
/container mounts add list=wgpanel src=usb1/wgpanel/data dst=/data
```

**5. Enviar e iniciar:** arraste `dist/wgpanel-arm64.tar` para o `usb1` na janela **Files** do Winbox e rode:
```
/container add name=wgpanel file=usb1/wgpanel-arm64.tar interface=veth-wgpanel root-dir=usb1/wgpanel/root envlists=wgpanel mountlists=wgpanel dns=172.20.0.1 start-on-boot=yes logging=yes memory-max=256M
/container start wgpanel      # quando /container print mostrar que terminou de extrair
```
Acesse **http://172.20.0.2:8080**. Para levar os dados de um painel existente: **Backup → Baixar** no antigo, **Backup → Restaurar** no novo. Desligue o antigo para não receber alertas em dobro.

**Atualizar:** gere a imagem nova, envie, e rode `/container stop wgpanel`, `/container remove wgpanel` e o `/container add …` de novo (os dados ficam em `usb1/wgpanel/data`).

> Em RouterOS anterior à 7.20, troque `available-from` por `address`, e `list=`/`envlists=`/`mountlists=` por `name=`/`envlist=`/`mounts=`.

---

## Rodar sem Docker (opcional)

```bash
python -m venv .venv
.venv/Scripts/activate        # Linux: source .venv/bin/activate
pip install -r requirements.txt
# defina as variáveis do .env no ambiente e então:
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

Documentação da API: `http://IP:8080/api/docs`

## Estrutura

```
app/
  main.py              # aplicação FastAPI
  mikrotik.py          # cliente da API REST do RouterOS
  db.py                # modelos (SQLite)
  routers/             # endpoints: wireguard, nat, monitor, alerts, users, backup, settings
  services/collector.py   # coleta periódica + histórico + gatilhos de alerta
  services/alerts.py      # Telegram / Discord
  services/nat_ops.py     # criação/remoção das regras de NAT
static/                # interface web (Alpine.js + Tailwind + Chart.js)
mikrotik-setup.rsc     # preparação do roteador
```
