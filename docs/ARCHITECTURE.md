# Architecture details

Complements sections 2–5 of the [README](../README.md). Everything below describes code that exists in
`src/emporio_ai/`.

## Module map

| Module | Main types / functions | Notes |
|---|---|---|
| `config.py` | `Settings.from_env`, `resolve_data_dir` | Reads `.env`; picks `data/` (original files) or `data/sample/` (fixtures). The API key field is excluded from `repr`. |
| `data_store.py` | `DataStore`, `DataQualityReport` | Reads CSVs as strings, coerces types, validates schema and integrity, never mutates the files. |
| `business_rules.py` | `BusinessRules`, `pix_price`, `installment_plan`, `metro_shipping_fee` | Pure functions; `anchors()` lists the text each rule must have in the manual. |
| `catalog.py` | `CatalogService.match_products`, `search`, `product_view`, `alternatives` | Joins products, categories and the best active promotion; computes availability and final price. |
| `orders.py` | `OrderService.lookup` → `OrderLookup` | Order id + e-mail check; output without personal data. |
| `policies.py` | `PolicyKnowledgeBase.search`, `find_section`, `store_info`, `verify_rules` | Page-aware chunking, BM25, synonyms, structured facts and conflicts. |
| `tools.py` | `ToolBox.call` + 8 tools | Argument validation, `sources`, trace with redacted arguments. |
| `guardrails.py` | `inspect_message`, `redact`, `PiiVault` | Heuristics are defence in depth; the main control is the read-only tool design. |
| `router.py` | `IntentRouter.route` → `Route` | Multi-label intents, products, categories, price range, sub-types, order id, e-mail, language, follow-up markers. |
| `offline_engine.py` | `OfflineEngine.respond` | One builder per intent; context resolution; curated wording only when the manual contains the facts. |
| `llm.py` | `GeminiAgent.respond`, `grounding_issues`, `SYSTEM_PROMPT`, `TOOL_DECLARATIONS` | Manual function-calling loop with the official SDK. |
| `store_hours.py` | `open_status`, `describe` | "Open now?" from the manual's hours in Campo Grande time (UTC-4). |
| `llm_openai.py` | `OpenAICompatibleAgent.respond` | Same tool loop over the OpenAI-compatible API (Ollama, LM Studio, llama.cpp, online providers) using only `urllib`. |
| `agent.py` | `EmporioAgent.ask` → `AgentResponse`, `ProviderConfig`, `build_providers`, `configure_session` | Sessions, provider chain, per-session settings, fallbacks, token accounting, warnings, provider-neutral transcript. |
| `usage.py` | `CallUsage`, `UsageMeter`, `TokenBudgetExceeded`, `is_quota_error` | Token counts per answer and per session, budgets, recognition of quota / rate-limit errors. |

## Product matching

`CatalogService.match_products` indexes each product name into *distinctive* tokens (model codes such as `gd20`,
`110e`, `ka-15s` → `ka15s`, and bigrams like `kit 1` → `kit1`), excluding generic words (`natural`, `nylon`,
`dreadnought`, ...). A query matches a product when it contains at least one distinctive model token; codes with
digits weigh more than words, the brand adds a small bonus and descriptive words break ties. Typing variants such as
`GD 20`, `gd-20` and `GD20` are equivalent. A brand-only mention returns that brand's products as a non-exact match
(`by_brand = True`); combined with a category (`violão Yamaha até 1500`) it becomes a filtered search.

## Typo tolerance

`IntentRouter.spell` compares each unknown word with a vocabulary of brand names, alphabetic model words and
category names (`difflib`, similarity ≥ 0.8). Words used by the router's own rules and common Portuguese/English
words are never corrected (so *abaixo* is not turned into *baixo*), and e-mail messages are left untouched. Each
correction is shown to the customer ("Entendi **Takamine**").

## Offline engine: intent → section

| Intent | Section builder | Tools called |
|---|---|---|
| `compare` | side-by-side table of 2–4 products + factual notes | `get_product_details` (one per product) |
| `recommend` | top 3 for a usage profile, each justified by its catalog description | `search_products` |
| `product_info` | product card(s) with specs, or disambiguation list | `get_product_details` |
| `product_search` | filtered list + hidden-unavailable note | `search_products` |
| `promotions` | product promotion status / campaign status / active list | `list_promotions` |
| `payment` | product payment options / amount simulation / policy summary | `calculate_payment_options`, `search_store_policies` |
| `returns`, `warranty`, `shipping`, `privacy`, `about` | rule summary + quoted excerpt with citation (+ order-aware escalation) | `search_store_policies`, `estimate_shipping` |
| `hours`, `contact` | structured facts (+ conflict note) | `get_store_info` |
| `order_status` | ask for missing id/e-mail, verified view, delivery follow-up | `get_order_status` |
| `accessory`, `human`, `catalog_overview` | redirection / handoff / category overview | — |
| none | policy fallback (BM25 score ≥ 4) or out-of-scope message | `search_store_policies` |

Context rules: a follow-up ("e no pix?", "and this one?") or a short price/availability question without a product
reuses the last products; a budget-only follow-up ("e até 600?") reuses the last category; an order id or e-mail
given in a later message completes a pending verification; general questions ("quais promoções...") never reuse context.

## Gemini loop

```text
contents = history[-12:] + [user message with e-mails tokenised]
repeat up to max_tool_rounds:
    response = client.models.generate_content(model, contents, config(system prompt, tools, AFC disabled, T=0.2))
    if no function calls: break
    contents += [model turn]                         # keeps thought signatures intact
    contents += [function responses from ToolBox]   # tools resolve <EMAIL_n> locally
grounding_issues(answer, tool results, policy constants, store contacts) == []  → mode "gemini"
otherwise / on any exception                                                       → next provider + warning
```

## Provider chain, fallback and tokens

```text
chain = session override (UI "Configurações") or defaults from .env, e.g. [Gemini, Local]
for provider in chain:
    skip if UsageMeter.remaining(provider) == 0          # per-session token budget
    try provider.respond(message, state, tools, usage)   # usage filled after every API request
    except quota (HTTP 429 / RESOURCE_EXHAUSTED) or any error → record, warn, next provider
    if grounded → answer (fallback_used = provider is not the first)
    else        → record, warn, next provider
deterministic engine → answer
transcript += (user message with e-mails tokenised, final answer)   # shared by all providers
```

* Usage numbers come from the provider responses (Gemini `usage_metadata.prompt_token_count /
  candidates_token_count / total_token_count`; OpenAI-compatible `usage.prompt_tokens / completion_tokens /
  total_tokens`).
* A UI key is stored in a frozen `ProviderConfig` attached to that session only (`repr` hides it); "Nova conversa"
  clears the conversation but keeps the session's settings and counters.

## Data-quality findings on the original snapshot

Produced by `DataStore` at start-up (see the notebook output or `agent.store.report.issues`):

| Finding | Handling |
|---|---|
| Categories 6, 7, 8 have no products | Agent states that there are no items of that category in the system. |
| `promotions.csv` has no validity dates | Validity = `is_active`; documented limitation. |
| Product 136 is `discontinued` with stock 13; product 130 is `coming_soon` with stock 2 | Never offered as available; status explained; alternatives suggested. |
| Orders 3 and 20: recorded total ≠ current list price × quantity | Recorded total shown as recorded. |
| Several products have more than one (inactive) promotion record | Only active promotions count; the best single active one is applied. |

## Conflicts found in the policy manual

| Topic | Conflict | Agent behaviour |
|---|---|---|
| WhatsApp | §1.2 lists "(67) 3341-4444" as phone/WhatsApp; §7 names "(67) 3321-4500" as the WhatsApp channel | Shows both numbers with a note; handoff line uses the §7 WhatsApp channel. |
| E-mail | Cover page `contato@emporiodamusica.com.br`; §1.2 `contato@empóriodamusica.com.br` (accented domain) | Uses the ASCII address; reports the inconsistency in `conflicts_in_manual`. |
| Installments | §3 table: "parcela mínima de R$ 100,00"; §3.1: R$ 50 (≤3x), R$ 80 (4–6x), R$ 100 (7–12x) | Applies the more specific §3.1 rule. |
| Defect vs. warranty | §4.2: store exchange within 30 days of purchase, then manufacturer; §8.1: 90-day legal warranty from receipt | Explains both and escalates cases between 30 and 90 days instead of denying. |

## Decision log

| Decision | Alternatives considered | Reason |
|---|---|---|
| Provider-agnostic tool layer; Gemini default, local model optional | Local model only; Gemini only | The case allows both; Gemini is faster and more reliable at tool use, a local model gives cost-free, offline operation. Grounding checks are shared. |
| Ordered provider chain with per-session settings | Single global provider; restart to change | Lets the user switch models and keep answering when Gemini runs out of quota, without a key typed by one visitor affecting another. |
| Native function calling, manual loop | LangChain agent; automatic function calling | Full control of validation, tracing, bounds and PII; fewer dependencies. |
| Deterministic tools for numbers | LLM-generated pandas/SQL | Exact, testable arithmetic; no access path to customer data. |
| BM25 over section chunks | Embeddings + vector DB | 28 chunks; lexical ranking is precise and explainable; no extra service. |
| Offline engine kept as first-class mode | LLM-only | Works without credentials, is the fallback for provider/grounding failures and the reference for tests. |
| Order id + e-mail check | Open lookup by id; full auth | Minimum reasonable protection for a prototype; documented as not authentication. |
| Synthetic fixtures for the public repo | Publishing original files | The manual is marked restricted and the customer file holds personal data. |
