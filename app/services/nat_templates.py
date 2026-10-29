"""Modelos prontos de portas para servidores de jogos e aplicações."""

TEMPLATES = [
    # Jogos
    {"id": "minecraft-java", "category": "Jogos", "name": "Minecraft (Java)", "protocol": "tcp", "port": "25565"},
    {"id": "minecraft-bedrock", "category": "Jogos", "name": "Minecraft (Bedrock)", "protocol": "udp", "port": "19132"},
    {"id": "cs2", "category": "Jogos", "name": "Counter-Strike 2 / Source", "protocol": "both", "port": "27015"},
    {"id": "valheim", "category": "Jogos", "name": "Valheim", "protocol": "udp", "port": "2456-2458"},
    {"id": "rust", "category": "Jogos", "name": "Rust", "protocol": "udp", "port": "28015"},
    {"id": "palworld", "category": "Jogos", "name": "Palworld", "protocol": "udp", "port": "8211"},
    {"id": "ark", "category": "Jogos", "name": "ARK: Survival", "protocol": "udp", "port": "7777-7778"},
    {"id": "terraria", "category": "Jogos", "name": "Terraria", "protocol": "tcp", "port": "7777"},
    {"id": "satisfactory", "category": "Jogos", "name": "Satisfactory", "protocol": "both", "port": "7777"},
    {"id": "zomboid", "category": "Jogos", "name": "Project Zomboid", "protocol": "udp", "port": "16261-16262"},
    {"id": "7dtd", "category": "Jogos", "name": "7 Days to Die", "protocol": "both", "port": "26900-26902"},
    {"id": "enshrouded", "category": "Jogos", "name": "Enshrouded", "protocol": "udp", "port": "15636-15637"},
    {"id": "factorio", "category": "Jogos", "name": "Factorio", "protocol": "udp", "port": "34197"},
    {"id": "fivem", "category": "Jogos", "name": "FiveM (GTA RP)", "protocol": "both", "port": "30120"},
    {"id": "teamspeak", "category": "Voz", "name": "TeamSpeak 3", "protocol": "udp", "port": "9987"},
    {"id": "mumble", "category": "Voz", "name": "Mumble", "protocol": "both", "port": "64738"},
    # Aplicações
    {"id": "http", "category": "Aplicações", "name": "Servidor Web (HTTP)", "protocol": "tcp", "port": "80"},
    {"id": "https", "category": "Aplicações", "name": "Servidor Web (HTTPS)", "protocol": "tcp", "port": "443"},
    {"id": "plex", "category": "Aplicações", "name": "Plex", "protocol": "tcp", "port": "32400"},
    {"id": "jellyfin", "category": "Aplicações", "name": "Jellyfin", "protocol": "tcp", "port": "8096"},
    {"id": "homeassistant", "category": "Aplicações", "name": "Home Assistant", "protocol": "tcp", "port": "8123"},
    {"id": "dvr", "category": "Aplicações", "name": "DVR / Câmeras (web)", "protocol": "tcp", "port": "8080"},
    {"id": "ssh", "category": "Acesso remoto", "name": "SSH", "protocol": "tcp", "port": "22",
     "warning": "Expor SSH na internet é arriscado. Prefira acessar pela VPN ou use whitelist de IPs."},
    {"id": "rdp", "category": "Acesso remoto", "name": "Área de Trabalho Remota (RDP)", "protocol": "tcp", "port": "3389",
     "warning": "RDP exposto é alvo constante de ataques. Prefira acessar pela VPN ou use whitelist de IPs."},
]
