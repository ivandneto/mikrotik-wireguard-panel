# Segurança

## Encontrou uma falha?

**Não abra uma Issue pública.** Use **Security → Report a vulnerability** neste repositório (aviso privado). Descreva o problema, como reproduzir e a versão do painel.

## Boas práticas ao usar o painel

O painel tem acesso de configuração aos seus MikroTiks. Para usá-lo com segurança:

- **Não exponha o painel na internet.** Acesse pela rede local ou pela própria VPN WireGuard. Ele não foi feito para ficar aberto em IP público.
- Por padrão ele roda em **HTTP**. Se precisar de HTTPS, coloque um proxy reverso (Caddy, Nginx) na frente e defina `COOKIE_SECURE=true`.
- Use o usuário **`wgpanel`** com grupo limitado (veja `mikrotik-setup.rsc`), **nunca o admin** do MikroTik.
- Restrinja a API do MikroTik (`/ip service` → `available-from`) às redes de onde o painel acessa.
- Guarde com cuidado: o arquivo `.env`, a pasta `data/` (banco e `secret.key`) e os backups exportados — eles contêm senhas e as chaves da VPN.
- A opção "Guardar a chave privada dos clientes no MikroTik" faz as chaves irem também para os backups do roteador.
- Troque a senha do usuário `admin` do painel após a instalação e crie usuários "Somente leitura" para quem só acompanha.
