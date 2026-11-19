# Global Opportunity Radar

Monitor que roda 24 horas por dia e procura **oportunidades oficiais** (Europa e EUA) que paguem incentivos a novos moradores, ofereçam emprego com moradia/passagem, bolsas e cursos remunerados, programas para jovens com despesas cobertas ou outras reduções reais de custo para morar fora.
Cada oportunidade vem com fonte oficial, **citação literal** dos requisitos, valor, prazo e uma avaliação separada em **elegível / precisa confirmar / inelegível**.

O perfil inicial (20 anos, cidadania brasileira e italiana, mora no Brasil, ensino médio completo, inglês B1) está no `config.yaml`, a fonte única de regras e filtros.

## Regras que o sistema nunca quebra

- **Nunca inventa** oferta, valor, prazo ou elegibilidade. O que a fonte não diz aparece como `desconhecido`.
- **Resposta de IA nunca é fonte primária.** A IA só extrai fatos do texto oficial; o radar confere cada citação literalmente no documento. Valor, prazo e situação sem citação conferida não contam.
- **A elegibilidade é determinística** (regras no `config.yaml`), não opinião de modelo. Cidadania italiana + residência no Brasil **não** torna ninguém elegível ao Corpo Europeu de Solidariedade; trabalho nos EUA exige evidência de autorização ou patrocínio.
- **Nunca envia candidatura.** O painel só guarda rascunhos e registra quem aprovou e quem enviou, sempre uma pessoa.
- **Respeita as fontes:** só coleta com termos de uso aprovados no `config.yaml`, checa `robots.txt` a cada execução, limita requisições por minuto e por execução, não contorna captcha.
- **Dado de teste nunca se passa por real.** Fixtures só rodam com `RADAR_ENV=test`; o banco impede (gatilho SQL) que dado de teste vire `real`; o painel marca teste e origem desconhecida de forma inconfundível.
- Credenciais só em variáveis de ambiente. Logs removem tokens e senhas.

## Como funciona

```
 config.yaml ──► fontes ──► coletor (Python) ──► documentos ──► extração (OpenAI, JSON com schema)
                                  │                                    │
                         robots/ToS/rate limit             conferência literal das citações
                                                                       │
                          elegibilidade determinística ◄───────────────┘
                                  │            └─► auditoria independente (Claude) quando a nota é alta,
                                  ▼                 a fonte mudou ou há conflito: só deixa o resultado MAIS conservador
                    pontuação (pesos do config.yaml) ──► versões + evidências no PostgreSQL
                                  │
                       alertas (Telegram/e-mail) só para elegível/a confirmar, com evidência
                       oficial e, para "urgente", prazo confirmado + situação aberta
```

| Pasta | O que é |
|---|---|
| `collector/` | Pacote Python `radar`: conectores, evidência, elegibilidade, pontuação, IA, fila (PostgreSQL), worker e CLI |
| `web/` | Next.js 16 + TypeScript + Tailwind: painel, API e login |
| `db/migrations/` | Esquema SQL (13 tabelas do projeto + fila, cache de IA e heartbeat) |
| `config.yaml` | Perfil, regras de programas, pesos, filtros, fontes e limites de custo. Campos desconhecidos são rejeitados |
| `docker-compose.yml` | Produção: `db`, `migrate`, `worker`, `web` e `backup` (perfil opcional) |
| `docker-compose.test.yml` | Testes em rede isolada, sem internet |
| `coolify/COOLIFY.md` | Passo a passo no Coolify |
| `scripts/` | `backup.sh`, `local-pg.sh` (PostgreSQL descartável para testes sem Docker) |

## Instalar na VPS (Docker Compose ou Coolify)

1. Copie `.env.example` para `.env` e preencha. Obrigatórios: `POSTGRES_PASSWORD`, `ADMIN_USER`, `ADMIN_PASSWORD_HASH`, `SESSION_SECRET`.
2. Gere o hash da senha do painel (a senha não fica no histórico do shell):
   ```bash
   cd web && npm ci && printf '%s' 'sua-senha-com-12-ou-mais-caracteres' | npm run --silent hash-password
   ```
   Cole o resultado em `ADMIN_PASSWORD_HASH`. O hash usa `:` como separador, então é seguro em `.env`.
3. Suba tudo:
   ```bash
   docker compose up -d --build
   docker compose --profile backup up -d backup     # backup diário (opcional, recomendado)
   ```
4. Abra o painel (porta 3000 só no localhost; publique com HTTPS pelo proxy do Coolify, Caddy ou nginx).
5. Rode `docker compose exec worker radar check-config`. Ele lista, em português, o que ainda falta para operar.

No Coolify, veja `coolify/COOLIFY.md`.

## O que depende de credenciais

Sem uma credencial, o radar **continua coletando e guardando** os documentos; só a etapa dependente fica em espera e o painel diz qual.

| Variável | Habilita | Sem ela |
|---|---|---|
| `POSTGRES_PASSWORD` | Banco | Nada sobe |
| `ADMIN_USER`, `ADMIN_PASSWORD_HASH`, `SESSION_SECRET` | Login do painel | O painel não abre |
| `OPENAI_API_KEY` + `OPENAI_MODEL` | Extração de fatos de páginas e PDFs. O modelo não tem padrão: você escolhe | Documentos HTML/PDF ficam `pending_llm`. Feeds estruturados (DOL) seguem |
| `ANTHROPIC_API_KEY` (+ `ANTHROPIC_MODEL`) | Auditoria independente da evidência | `audit_status = unavailable`; o resultado não fica mais permissivo |
| `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` | Alertas no Telegram | Alertas ficam `pending` |
| `SMTP_HOST/PORT/USER/PASSWORD/FROM/TO` | Alertas por e-mail | Idem |
| `RADAR_CONTACT` | Contato no User-Agent do coletor | Funciona, com aviso |

Controle de custo: limite diário de tokens (sempre vale) e de dólares (só para modelos com preço em `llm.pricing_usd_per_mtok`; sem preço o custo aparece como **desconhecido**, nunca como zero), cache por conteúdo e modelo, nenhum reprocessamento de página idêntica. Veja a tela **Custos de IA**.

## Operação

- **Painel:** visão geral (faixas por origem do dado), oportunidades com filtros, detalhe com evidências, checagens, versões e candidatura; fontes, coletas e fila de falhas, custos de IA, alertas.
- **Coleta manual:** botão "Coletar agora" em Fontes (só enfileira; o worker executa) ou `docker compose exec worker radar scan --source <id>`.
- **Falhas:** uma fonte que cai não derruba as outras nem apaga dados. Jobs repetem com espera crescente; esgotadas as tentativas vão para a fila de falhas, onde você pode devolvê-los à fila.
- **Saúde:** `GET /api/health` (público, sem dados) e `radar healthcheck` no worker. O painel avisa quando o coletor para de dar sinal.
- **Backup:** `docker compose --profile backup up -d backup` grava `pg_dump` diário no volume `backups` e mantém 14 dias. Restaurar: `pg_restore --clean --if-exists --no-owner -d "$DATABASE_URL" arquivo.dump`.
- **Atualizar:** `git pull && docker compose up -d --build`. O serviço `migrate` aplica as migrations e sincroniza o `config.yaml` a cada deploy.

## Adicionar fontes (inclusive municípios pequenos)

1. Ache a página oficial do bando/programa e leia os **termos de uso** e o `robots.txt` do site.
2. Acrescente em `config.yaml > sources`:
   ```yaml
   - id: it-comune-exemplo-bonus
     name: Comune di Exemplo, bonus nuovi residenti
     kind: html_page            # html_page | html_hub (página de links) | pdf_document | seasonal_jobs_json
     origin: real
     enabled: true
     url: https://www.comune.exemplo.it/bonus-nuovi-residenti
     country: IT
     language: it
     interval_minutes: 720
     rate_limit: {requests_per_minute: 4, max_requests_per_run: 3}
     tos: {status: pending_review, note: "revisar termos"}
   ```
3. Rode `radar check-config`. A fonte nasce em `pending_review` e **não roda**.
4. Depois de ler os termos, troque para `tos: {status: allowed, reviewed_on: AAAA-MM-DD, evidence_url: <página dos termos>, note: ...}` ou `forbidden`. No deploy seguinte a fonte passa a rodar.

## Testes

```bash
# sem Docker (PostgreSQL descartável local)
scripts/local-pg.sh start
export RADAR_TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:54329/postgres
pip install -e "collector[dev]" && (cd collector && python -m pytest)      # unitários + integração + ponta a ponta com fixtures
(cd web && npm ci && npm run typecheck && npm test && npm run build && npm run test:e2e)

# em Docker, rede isolada
docker compose -f docker-compose.test.yml up --build --abort-on-container-exit --exit-code-from collector-tests collector-tests db-test
docker compose -f docker-compose.test.yml run --rm web-tests
```

As fixtures (`collector/tests/fixtures`) são sintéticas e rotuladas como TESTE. Os testes de LLM, Telegram e SMTP usam transportes simulados (`httpx.MockTransport`); nenhuma chave real é necessária para testar.

## Limitações conhecidas (leia antes de confiar)

- **Nenhuma coleta ao vivo foi testada.** O ambiente de desenvolvimento não alcançava os sites oficiais. Os conectores foram testados com fixtures e respostas simuladas. A primeira execução real pode exigir ajustes.
- **Feed DOL SeasonalJobs:** os nomes de campo e o modelo de URL das vagas (`https://seasonaljobs.dol.gov/jobs/{case_number}`) são suposições baseadas na documentação pública, não verificadas contra um arquivo real. O mapeamento é configurável (`options.field_map`) e a primeira execução registra os campos não reconhecidos (`unmapped_keys`). Confira antes de confiar nos dados.
- **Termos de uso do feed DOL:** estão marcados `allowed` porque a página oficial de feeds descreve o uso por terceiros, mas **não encontrei texto de licença**. Confirme antes de operar em produção.
- **Só 2 das 7 fontes reais rodam** (os feeds DOL). As 5 fontes italianas/europeias estão em `pending_review` até alguém ler os termos e o `robots.txt`.
- **Lista de países elegíveis ao H-2 está vencida** (validade 2025-11-07): vagas H-2 saem como "precisa confirmar" até você atualizar `config.yaml` com a lista vigente do DHS.
- **Câmbio de referência** do `config.yaml` é aproximado e datado; o radar avisa quando passa de 90 dias.
- **Injeção de prompt:** páginas com instruções dirigidas à IA são descartadas por padrões conhecidos (EN/IT/PT/ES) e o texto vai ao modelo como dado não confiável. Um texto malicioso que também esteja literalmente na página pode passar na conferência de citação; a auditoria e a revisão humana mitigam, não eliminam.
- **A auditoria Claude só vale para extrações feitas por IA**; feeds estruturados são determinísticos e não passam por ela.
- **Docker, Compose e Coolify não foram executados** no ambiente de desenvolvimento (sem daemon Docker). Os arquivos foram validados com `docker compose config`, e o pacote Python e o build standalone do Next foram executados fora do repositório, simulando as imagens. O workflow `ci.yml` constrói as imagens e roda os testes em Docker na primeira execução.
- O painel tem **um** usuário administrador (variáveis de ambiente). Sem papéis nem multiusuário.

## Segurança e privacidade

Cookie de sessão `HttpOnly`, `SameSite=Strict`, `Secure` atrás de HTTPS; senha com scrypt; limite de tentativas de login; verificação de origem nos POST; toda escrita do painel vai para `audit_events`. O banco não é publicado no host. O perfil do candidato contém só idade, nacionalidades, país de residência, escolaridade e idiomas, **sem documentos**. À auditoria da Anthropic o radar envia apenas o trecho de evidência, sem dados do candidato.
