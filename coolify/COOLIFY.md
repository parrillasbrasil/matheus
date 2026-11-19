# Implantação no Coolify

> **Aviso:** estes passos seguem a documentação do Coolify para aplicações Docker Compose, mas **não foram executados** no ambiente onde o projeto foi desenvolvido (sem Coolify nem daemon Docker). Se algo divergir da sua versão do Coolify, o `docker-compose.yml` da raiz é a fonte de verdade.

## 1. Criar o recurso

1. No Coolify: **Projects > Add Resource > Docker Compose** (ou "Public/Private Repository" com *Build Pack = Docker Compose*).
2. Aponte para o repositório e para o arquivo `docker-compose.yml` na raiz (base directory `/`).
3. Em **Environment Variables**, cadastre as variáveis do `.env.example`.
   Obrigatórias: `POSTGRES_PASSWORD`, `ADMIN_USER`, `ADMIN_PASSWORD_HASH`, `SESSION_SECRET`. O restante habilita IA e alertas (veja a tabela do README).
   Marque `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`, `SMTP_PASSWORD`, `ADMIN_PASSWORD_HASH`, `SESSION_SECRET` e `POSTGRES_PASSWORD` como secretas.

## 2. Domínio e HTTPS

- No serviço **web**, informe o domínio (ex.: `https://radar.seudominio.com`) e a porta interna **3000**. O Coolify emite o certificado e faz o proxy.
- Mantenha `COOKIE_SECURE=true`.
- **Não** dê domínio aos serviços `db`, `worker` e `migrate`. O banco nunca é publicado.
- Opcional, mas recomendado: restrinja o acesso ao painel por IP ou VPN no firewall da VPS.

## 3. Volumes e backup

- `pgdata` guarda o banco. Mantenha como volume persistente (o compose já declara).
- Para backup diário, habilite o perfil `backup` (variável `COMPOSE_PROFILES=backup` nas variáveis do recurso). Os arquivos ficam no volume `backups`.
- **Copie os backups para fora da VPS** (ex.: *Scheduled Backups* do Coolify para S3, ou `rclone`). Backup só na mesma máquina não protege contra perda do disco.

## 4. Primeiro deploy

1. **Deploy.** A ordem é automática: `db` saudável, depois `migrate` (migrations + sincronização do `config.yaml`), depois `worker` e `web`.
2. Terminal do serviço `worker` no Coolify (ou `docker compose exec worker ...`):
   ```bash
   radar check-config      # o que falta para operar, em português
   radar scan --source us-dol-seasonaljobs-h2b      # primeira coleta supervisionada
   ```
3. Entre no painel e confira **Fontes** e **Coletas e falhas**. Na primeira execução real do feed DOL, veja `unmapped_keys` nas estatísticas da coleta (README, "Limitações conhecidas").

## 5. Verificar que está de pé 24 horas por dia

- `https://seu-dominio/api/health` deve responder `{"status":"ok","db":true,"worker":"ok"}`.
- O painel mostra "Coletor ativo" na visão geral. Se aparecer "Sem sinal do coletor", veja os logs do serviço `worker`.
- Healthchecks do compose: `worker` (`radar healthcheck`) e `web` (`/api/health`). O Coolify reinicia serviços que ficam `unhealthy`.
- Configure uma notificação de falha de deploy/serviço no próprio Coolify (Telegram, e-mail).

## 6. Atualizações

Push no repositório (ou botão *Redeploy*). O worker recebe SIGTERM e encerra com limpeza (`stop_grace_period: 60s`); jobs em andamento voltam à fila se o processo cair no meio.
