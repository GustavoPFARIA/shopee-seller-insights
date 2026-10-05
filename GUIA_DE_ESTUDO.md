# Guia de estudo: como o Shopee Seller Insights foi construído

> Cole este arquivo numa nova conversa e peça, por exemplo:
> "Me explique a etapa 4 em detalhe, com o código do repositório" ou
> "Faça perguntas para testar se eu entendi a parte de segurança".
>
> Repositório: https://github.com/GustavoPFARIA/shopee-seller-insights
> PR principal: https://github.com/GustavoPFARIA/shopee-seller-insights/pull/1

---

## 1. O que é o projeto

Um aplicativo de análise de vendas para quem vende na Shopee. Ele responde a pergunta
**"quais produtos realmente dão lucro?"**, descontando as taxas da Shopee, os cupons, o
frete e o custo do produto.

**Stack:**

| Camada | Ferramenta | Função |
|---|---|---|
| Backend | Python 3.12, FastAPI, Pydantic v2 | API e validação dos dados |
| Banco | PostgreSQL 16, SQLAlchemy 2.0, Alembic | Dados e migrações do esquema |
| Processamento | pandas | Leitura das planilhas |
| Frontend | React, Vite, Recharts | Telas e gráficos |
| Infraestrutura | nginx, Docker Compose, container *worker* | Servir o app e rodar tarefas em segundo plano |
| Qualidade | pytest, ruff, mypy, Vitest, GitHub Actions | Testes, lint, tipos e CI |
| Opcional | Claude API (resumo semanal), SMTP (e-mail) | Funcionam só se configurados |

---

## 2. O processo de trabalho (a parte mais importante para estudar)

Cada funcionalidade passou pelo mesmo **ciclo de verificação**:

1. **Teste primeiro.** Escrever o teste que descreve o comportamento esperado. Em
   correção de bug, o teste precisa falhar antes da correção.
2. **Implementar** o mínimo necessário.
3. **Rodar as checagens:**
   - `ruff check` e `ruff format`: estilo do código;
   - `mypy --strict`: tipos;
   - `pytest` com cobertura de pelo menos 85% (ficou em 99%);
   - `npm run lint`, `npm test` e `npm run build` no frontend.
4. **Corrigir até ficar tudo verde.** Regra de ouro: **nunca enfraquecer um teste** para
   ele passar. Quando um teste do ABC falhou, quem foi corrigido foi o código, não o teste.
5. **Autorrevisão** do diff, pensando como um atacante e como o CI.
6. **Commit** com mensagem no padrão *Conventional Commits* (`feat:`, `fix:`, `perf:`,
   `docs:`) e **push**.
7. **Conferir o CI no GitHub.** Se falhar, achar a causa e corrigir. Nunca "rodar de novo
   para ver se passa".

Tudo isso está automatizado em `scripts/verify.sh`, que roda 5 estágios:

| Estágio | O que prova |
|---|---|
| `static` | Lint, tipos, testes do frontend e build |
| `test` | 216 testes do backend num PostgreSQL de verdade; as migrações sobem e descem |
| `security` | `pip-audit`, `npm audit` e `gitleaks` (procura segredos em todo o histórico do git) |
| `e2e` | Sobe o Docker do zero e roda 52 checagens pelo nginx, como um usuário (ou atacante) faria |
| `perf` | Carrega 400 mil pedidos e falha se uma consulta passar do tempo limite |

---

## 3. Linha do tempo (os commits contam a história)

Rode `git log --oneline` no repositório para ver a sequência:

1. **Esqueleto:** FastAPI, esquema do banco, migrações e login com JWT. CI desde o
   primeiro dia.
2. **Importação** de pedidos com validação e *idempotência* (reenviar o mesmo arquivo
   não duplica pedidos).
3. **Métricas:** margem real, curva ABC, comparação de períodos e exportação de CSV segura.
4. **Alertas e resumo com IA** (a IA recebe só números agregados, nunca dados de
   clientes).
5. **Seed de demonstração:** dados falsos que passam pelo **mesmo importador** dos
   usuários, então a demo também testa o código real.
6. **Frontend e Docker**, com um usuário de banco de privilégio mínimo.
7. **Roadmap:**
   - rate limit no Postgres;
   - refresh token em cookie;
   - nginx;
   - importação de custo e estoque;
   - equipes com papéis;
   - integração com a Shopee.
8. **Laço de verificação:** testes de ponta a ponta e benchmark. A importação de 50 mil
   linhas caiu de 167 s para 11 s.
9. **"Tudo que ficou de fora":**
   - configurações por loja;
   - devoluções;
   - e-mail;
   - notificações (webhook) da Shopee;
   - modo demo;
   - várias lojas por conta;
   - interface nova;
   - testes do frontend.
10. **Documentação padrão:** CONTRIBUTING, CHANGELOG, CODE_OF_CONDUCT e modelos de
    issue e de PR.

---

## 4. Conceitos técnicos para estudar (com onde encontrar no código)

### Multi-inquilino (cada vendedor vê só os seus dados)
- Cada requisição descobre **qual loja** está ativa pelo cabeçalho `X-Shop-Id`.
- Depois confere no banco se o usuário é membro dessa loja. Se não for, responde 403.
- Toda consulta filtra por `seller_id`. Um ID de outra loja responde 404, igual a um ID
  inexistente, para não revelar que ele existe.
- Onde: `backend/app/deps.py` e a tabela `memberships` em `backend/app/models.py`.

### Sessões seguras
- O **access token** (JWT de 15 minutos) fica só na memória do navegador, então um script
  malicioso não acha ele no `localStorage`.
- O **refresh token** fica num cookie *HttpOnly* e *SameSite=Strict*.
  - Ele é trocado a cada uso (rotação).
  - Se um token antigo for reutilizado, todas as sessões daquele login são revogadas.
- Onde: `backend/app/api/auth.py` e `frontend/src/api.ts`.

### Proteções de entrada
- **Injeção de fórmula em planilha:** uma célula que começa com `=`, `+`, `-` ou `@`
  vira um comando no Excel. Ela é rejeitada na importação e neutralizada na exportação.
- **Limites:** tamanho, tipo real do arquivo (não só a extensão) e número de linhas.
- **Rate limit:** limite de tentativas de login e de upload, guardado no Postgres.
  - O IP é lido de forma confiável, aceitando `X-Forwarded-For` só vindo do nginx.
  - Isso foi um bug real: falsificar esse cabeçalho burlava o limite. Foi corrigido.

### LGPD (dados pessoais)
- Nome, telefone e endereço do comprador são **descartados** na importação.
- O usuário do comprador vira um **hash HMAC**, que não pode ser revertido.
- Nada pessoal vai para os logs. Um teste garante que nenhum token da Shopee aparece nos
  logs; esse vazamento existiu e foi corrigido.

### Integração Shopee (Open Platform v2)
- **Assinatura HMAC** em toda chamada.
- **OAuth** com `state` amarrado ao navegador, por cookie, para impedir que alguém ligue
  a loja dele na conta de outra pessoa.
- Tokens guardados **criptografados** com Fernet.
- A sincronização roda no **worker**, nunca dentro de uma requisição HTTP:
  - fila com `FOR UPDATE SKIP LOCKED`;
  - *advisory lock* do Postgres para a mesma loja nunca sincronizar duas vezes ao mesmo
    tempo.
- **Webhook (push):** só aceita notificação assinada. O conteúdo não é usado como dado,
  só como aviso de que vale sincronizar agora.
- **Shopee falsa** (`backend/devtools/fake_shopee.py`) para testar sem conta real. Ela
  confere as assinaturas como a real.

### Desempenho
- Filtro de período convertido **uma vez** para um intervalo em UTC, para o índice
  `(seller_id, ordered_at)` ser usado.
- Importação **em lote**: blocos de 2.000 pedidos, poucas idas ao banco.
- **Dinheiro sempre como `Decimal`** (texto na API), nunca `float`.

### Frontend
- Funções puras e testáveis separadas da interface: `format.ts` e `productTable.ts`.
- Contexto de sessão (`session.tsx`), um roteador simples (`router.ts`), avisos rápidos
  (toasts) e janelas (modais).
- Testes com Vitest e Testing Library em `frontend/src/test/`.

---

## 5. Erros reais que aconteceram (e as lições)

| Erro | Lição |
|---|---|
| Commit com teste falhando porque o `tail` escondia o código de saída | Usar `set -o pipefail` em scripts |
| `X-Forwarded-For` falsificado burlava o rate limit | Só confiar em cabeçalhos do proxy que você controla |
| Token da Shopee aparecia nos logs do httpx | Silenciar os loggers das bibliotecas e **testar** isso |
| CI e2e falhou porque o worker não tinha healthcheck | Healthcheck real (heartbeat), não um "sempre ok" |
| Benchmark quebrou depois da troca de `users.role` por `memberships` | Mudança de esquema afeta todo código que toca a tabela; rode **todos** os estágios |
| A tela continuou antiga depois do build | Confirme que o container foi **recriado** com a imagem nova |
| Refresh token revogado gerava um erro a cada 5 minutos | Classificar o erro e mostrar a ação certa ao usuário (botão "Reconectar") |

---

## 6. Documentação padrão de um repositório (o que cada arquivo faz)

| Arquivo | Para quê |
|---|---|
| `README.md` | O que é, como rodar, configuração, arquitetura, verificação |
| `LICENSE` | Licença (MIT) |
| `SECURITY.md` | Como reportar falhas de segurança e as limitações conhecidas |
| `CONTRIBUTING.md` | Como montar o ambiente, as regras e o processo de PR |
| `CHANGELOG.md` | Histórico de versões (formato *Keep a Changelog*) |
| `CODE_OF_CONDUCT.md` | Regras de convivência (Contributor Covenant) |
| `.env.example` | Todas as variáveis, sem valores secretos |
| `.github/pull_request_template.md` | Checklist que aparece em todo PR |
| `.github/ISSUE_TEMPLATE/` | Formulários de bug e de sugestão |
| `.github/workflows/ci.yml` | O pipeline de CI |

---

## 7. Exercícios sugeridos

1. Rode `scripts/verify.sh static test` e leia a saída de cada etapa.
2. Leia `backend/tests/test_shops.py` e os testes de isolamento em `test_metrics.py` e explique como
   ele prova que uma loja não vê a outra.
3. Suba o modo demo (`docker compose -f docker-compose.yml -f docker-compose.shopee-demo.yml up --build`):
   - conecte a loja falsa;
   - crie um pedido;
   - acompanhe a notificação chegando na página Integrações.
4. Quebre algo de propósito (por exemplo, tire o filtro `seller_id` de uma consulta) e
   veja qual teste falha.
5. Implemente o próximo item do roadmap, **recuperação de senha**, seguindo o
   CONTRIBUTING: teste primeiro, migração e documentação.
