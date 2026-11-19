# Global Opportunity Radar — plano de implementação (enxuto)

**Meta:** monitorar continuamente fontes oficiais (Europa + EUA) e avisar só sobre oportunidades com benefício concreto de mudança, com evidência verificável e elegibilidade calculada de forma determinística para um brasileiro-italiano de 20 anos residente no Brasil.

**Arquitetura:** Python (coletor/worker/motor de elegibilidade) + PostgreSQL (dados, fila, DLQ) + Next.js (painel/API/auth). `config.yaml` é a fonte única de regras; o loader **rejeita campos desconhecidos**. A IA nunca decide elegibilidade: OpenAI extrai JSON (schema estrito), regras determinísticas decidem, Claude audita a *evidência* (nunca recebe dados do candidato).

## Decisões fixas (spec + usuário)
- Cidadania ≠ residência ≠ autorização de trabalho. `us.assume_work_authorization_from_citizenship` só aceita `false`.
- Status de elegibilidade: `eligible` | `needs_confirmation` | `ineligible`; campo à parte `relocation_condition` = `none` | `before_apply` | `after_award`.
- Toda decisão exige: URL em domínio oficial (lista em config) + ≥1 trecho de evidência que **aparece literalmente** no documento. Citação que não confere → campo vira `unknown`.
- Dados têm origem explícita: `real` | `fixture` | `unknown`. Fixtures só rodam com `RADAR_ENV=test`.
- Nada é submetido sem aprovação humana (`applications` só guarda rascunho/aprovação).
- Falha de API/fonte nunca apaga dados: erros viram `source_runs.failed` e jobs com retry → DLQ.

## Árvore de pastas
```
config.yaml                     fonte única de regras/filtros/pesos/fontes
db/migrations/                  SQL puro (001_init, 002_queue)
collector/radar/                config, db, migrate, textutil, http (robots+rate limit),
  connectors/ (base, seasonal_jobs, html_page, rss, fixture), schema, evidence,
  llm/ (openai_client, anthropic_client, budget, prompts), eligibility, scoring,
  pipeline, alerts, jobqueue, worker, cli
collector/tests/                unit/ integration/ e2e/ fixtures/ (marcadas como TESTE)
web/                            Next.js + TS + Tailwind: painel, /api/*, login por cookie assinado
docker-compose.yml              db, migrate, worker, web (+ backup em profile)
docker-compose.test.yml         stack isolada (rede internal) para os testes
coolify/COOLIFY.md  .env.example  README.md  .github/workflows/ci.yml  scripts/backup.sh
```

## Tarefas (TDD: teste falhando → implementação → verde)
1. **Config estrita** — `load_config(path)->Config`; teste: campo desconhecido levanta erro; `assume_work_authorization_from_citizenship: true` levanta erro.
2. **Migrations + runner** — 13 tabelas do spec + `jobs`, `llm_cache`, `service_heartbeats`; teste de integração aplica em Postgres real e é idempotente.
3. **Texto/hash/evidência** — `normalize_text`, `content_hash` (remove padrões voláteis), `verify_quote(doc_text, quote)`, `parse_amount`.
4. **Elegibilidade** — `evaluate(profile, extraction, cfg)->EligibilityResult`; casos: ESC com residência BR ⇒ `ineligible`+`before_apply`; bônus de município com mudança posterior ⇒ `eligible`+`after_award`; vaga US sem patrocínio ⇒ `ineligible`/`needs_confirmation`; lista H-2 vencida ⇒ `needs_confirmation`.
5. **Pontuação** — pesos do config, explicação legível, valor desconhecido não pontua.
6. **Conectores** — `SeasonalJobsConnector` (ZIP/JSON, mapeamento de campos configurável) e `HtmlPageConnector` (HTML/PDF oficial, robots.txt, rate limit); testes só com fixtures.
7. **LLM** — `OpenAIExtractor` (JSON schema estrito), `ClaudeAuditor`, orçamento diário, cache por hash; testes com `httpx.MockTransport`.
8. **Pipeline** — fonte idêntica não duplica; mudança cria versão; vencida não alerta; falha de LLM não apaga dados.
9. **Fila/worker** — `SKIP LOCKED`, backoff exponencial, DLQ, agendador por fonte.
10. **Alertas** — Telegram/SMTP, idempotência, urgente só com prazo verificado e status aberto.
11. **Painel/API Next.js** — lista+filtros, detalhe com evidências/histórico, fontes, jobs/DLQ, custos, selo real/teste/desconhecido, `POST /api/scan`.
12. **Deploy/CI/docs** — Compose, Coolify, `.env.example`, README, workflow.

## Review Focus (entradas que o spec não menciona, cada uma com teste)
1. Prazo sem fuso ("até 30/04") → fuso assumido do país e marcado `tz_assumed`; **não** conta como prazo confirmado para alerta urgente.
2. Valores em formato local (`15.000,00` vs `15,000.00`) → `parse_amount` e checagem de que o valor aparece na citação.
3. PDF escaneado sem texto → `needs_ocr`, sem chamada de LLM e sem inventar campos.
4. Página gigante → seleção de trechos por relevância com teto de caracteres e flag `truncated`.
5. Prompt injection no conteúdo ("ignore as instruções…") → conteúdo vai delimitado como dado; saída só vale se passar schema + verificação literal de evidência.
6. `robots.txt` proíbe → fonte não é buscada, run = `blocked_by_robots`.
7. Mudança trivial (carimbo de data/contador) → não gera nova versão (padrões voláteis do config).

## Limites do ambiente de construção (serão repetidos no relatório final)
- Saída de rede só para pip/npm; sites oficiais retornam 403 do proxy ⇒ **nenhum conector foi/ será testado ao vivo aqui**.
- Sem daemon Docker ⇒ Compose validado só com `docker compose config`; testes rodam em Postgres 16 local.
- Sem chaves OpenAI/Anthropic/Telegram/SMTP ⇒ adaptadores testados com transporte simulado.
