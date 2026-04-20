# Portion 2: LLM-Konfiguration & Shared Infrastructure

> Das zentrale LLM-Config-System, das jede Komponente im Repo nutzt. Diese
> Portion beschreibt die "Single-Source-of-Truth"-Architektur fuer LLM-Calls,
> das Rollen-System, Per-Directory-Overrides, Embedding-Modelle und das
> CI/CD-Validierungssetup.

---

## 1. Prinzip: Single Source of Truth

**Eine Datei kontrolliert jeden LLM-Aufruf im gesamten System.**

```
llm_config.yml
   |
   v
vibemind_shared.get_client("role_name")
   |
   v
Alle Services (Brain, Coding-Engine, Voice, Security, Email, ...)
```

**Regel:** Code instanziiert **nie direkt** einen LLM-Client. Stattdessen:

```python
from vibemind_shared import get_client, get_model

client = get_client("coding_planner")
response = await client.chat.completions.create(
    model=get_model("coding_planner"),
    messages=[...],
)
```

Wird ein Modell oder Provider geaendert → **nur `llm_config.yml`** anfassen,
niemals Code. Das ist der zentrale Produktivitaets-Hebel: ein Model-Wechsel
fuer einen bestimmten Use-Case braucht kein Deployment, nur eine Config-Aenderung.

**Vertragskorollar:** Niemand umgeht das Factory. Der `audit_llm_usage.py`
Scanner im CI flaggt direkte Client-Instanziierungen. Siehe Abschnitt 9.

---

## 2. Die 5 Provider

| Provider     | Driver-Type | Base URL                                              | Typische Modelle                       |
|--------------|-------------|-------------------------------------------------------|----------------------------------------|
| `openai`     | openai      | `https://api.openai.com/v1`                           | gpt-4o, gpt-4o-mini, gpt-4o-realtime   |
| `anthropic`  | anthropic   | `https://api.anthropic.com`                           | claude-sonnet-4-5, claude-opus-4, claude-haiku-4-5 |
| `openrouter` | openai      | `https://openrouter.ai/api/v1`                        | openrouter/auto, 100+ Modelle, Free-Tier |
| `google`     | openai      | `https://generativelanguage.googleapis.com/v1beta/openai` | gemini-2.0-flash, gemini-1.5-pro   |
| `ollama`     | openai      | `http://127.0.0.1:11434/v1`                           | qwen2.5-coder:7b, llama3.2, deepseek   |

**Driver-Types** (nur 2!):
- `openai` - `AsyncOpenAI` Client (funktioniert fuer openai, openrouter, google, ollama – alle sind OpenAI-kompatibel)
- `anthropic` - `AsyncAnthropic` Client (nur fuer anthropic direkt)

**Philosophie:** Die unterstuetzte Menge wird **bewusst klein gehalten**. Kein
Bedrock, kein Vertex AI, kein Azure - diese brauchen eigene Auth-Modelle
(SigV4, Service Accounts) und wuerden den Factory-Code aufblasen. Wer sie
braucht, diskutiert es in einem Issue vorher.

---

## 3. Resolution-Chain

```
overrides[<dir>][<role>]  >  roles[<role>]  >  default
```

Ausgefuehrt in `system/llm_client.py:59-84` (`_resolve_role`):

```python
# 1. Starte mit globalem default
result = dict(cfg.get("default", {}))

# 2. Ueberschreibe mit rollen-spezifischer Config
if role in cfg.get("roles", {}):
    result.update(cfg["roles"][role])

# 3. Ueberschreibe mit directory-spezifischer Override
if directory:
    overrides = cfg.get("overrides", {}).get(dir_name, {})
    if role in overrides:
        result.update(overrides[role])
```

**Konkretes Beispiel:**

```python
# In /brain/the_brain/production/planner.py:
client = get_client("brain_planning", directory="the_brain")

# Resolution:
# 1. default: { provider: ollama, model: qwen2.5-coder:7b, temperature: 0 }
# 2. roles.brain_planning: { provider: anthropic, model: claude-sonnet-4-5, temperature: 0.7 }
# 3. overrides.the_brain.brain_planning: { model: claude-opus-4 }
# Final: { provider: anthropic, model: claude-opus-4, temperature: 0.7 }
```

---

## 4. Das Rollen-System (33 Rollen, 8 Gruppen)

**Prinzip:** Eine Rolle ist eine **logische LLM-Aufgabe**, nicht ein Modell.
`brain_planning` bleibt immer `brain_planning`, auch wenn das dahinterliegende
Modell wechselt. Code muss sich nie aendern.

### Gruppe 1: Brain (Tahlamus)
Acht Rollen fuer das kognitive System, differenziert nach Aufgabe:

| Rolle                             | Provider   | Default-Modell              | Temp | Zweck                           |
|-----------------------------------|------------|-----------------------------|------|---------------------------------|
| `brain_fast_reasoning`            | openrouter | openrouter/auto             | 0.3  | Schnelle Entscheidungen         |
| `brain_planning`                  | anthropic  | claude-sonnet-4-5           | 0.7  | Strategische Planung            |
| `brain_context_tracking`          | anthropic  | claude-sonnet-4-5           | 0.5  | Kontext/Session-Tracking        |
| `brain_communication`             | openai     | gpt-4o                      | 0.8  | Natuerliche Gespraeche          |
| `brain_long_term_memory`          | google     | gemini-2.0-flash            | 0.5  | Langzeit-Memory (2M Context)    |
| `brain_supermemory`               | openai     | gpt-4o-mini                 | 0.5  | Supermemory-Integration         |
| `brain_data_collector`            | openai     | gpt-4o-mini                 | 0.7  | Daten-Collection                |
| `brain_data_collector_anthropic`  | anthropic  | claude-haiku-4-5-20251001   | 0.7  | Alternative zu openai-Collector |

### Gruppe 2: Coding Engine
Sechs Rollen fuer die autonome Code-Generierung:

| Rolle                  | Provider  | Default-Modell     | Temp | Zweck                    |
|------------------------|-----------|--------------------|----- |--------------------------|
| `coding_planner`       | anthropic | claude-sonnet-4-5  | 0.7  | Architektur-Planung      |
| `coding_executor`      | anthropic | claude-sonnet-4-5  | 0    | Code-Generierung         |
| `coding_reviewer`      | anthropic | claude-sonnet-4-5  | 0    | Code-Review              |
| `coding_test_writer`   | openai    | gpt-4o             | 0    | Test-Generierung         |
| `coding_security_audit`| openai    | gpt-4o             | 0    | Security-Audit           |
| `coding_architect`     | anthropic | claude-sonnet-4-5  | 0.5  | Architektur-Entscheidungen|

### Gruppe 3: Voice / Realtime

| Rolle                     | Provider | Default-Modell            | Temp | Zweck                     |
|---------------------------|----------|---------------------------|------|---------------------------|
| `voice_realtime`          | openai   | gpt-4o-realtime-preview   | 0.8  | Sprach-zu-Sprach Realtime |
| `voice_intent_classifier` | ollama   | qwen2.5:7b                | 0    | Intent-Klassifikation (lokal) |
| `voice_summarizer`        | ollama   | qwen2.5:7b                | 0.3  | Zusammenfassung (lokal)   |
| `voice_space_agent`       | openai   | gpt-4o-mini               | 0    | Space-Agent-Calls         |

**Hinweis:** Realtime laeuft nur ueber OpenAI (die einzigen mit Realtime-API).

### Gruppe 4: Security (Red/Blue Team)

| Rolle                 | Provider   | Default-Modell     | Temp | Zweck                   |
|-----------------------|------------|--------------------|----- |-------------------------|
| `security_red_team`   | openrouter | openrouter/auto    | 0.7  | Angriffs-Simulation     |
| `security_blue_team`  | openrouter | openrouter/auto    | 0    | Verteidigungs-Analyse   |
| `security_judge`      | openrouter | openrouter/auto    | 0    | Angriff/Abwehr-Richter  |
| `security_analyzer`   | openrouter | openrouter/auto    | 0    | Forensik-Analyse        |

**Hinweis:** Alle ueber OpenRouter-Free-Tier - Security-PoCs produzieren viele
Token, Free-Tier haelt die Kosten bei null.

### Gruppe 5: Email / Communication

| Rolle                | Provider   | Default-Modell                           | Temp |
|----------------------|------------|------------------------------------------|------|
| `email_personalizer` | openrouter | meta-llama/llama-3.3-70b-instruct        | 0.7  |
| `email_response`     | openrouter | meta-llama/llama-3.3-70b-instruct        | 0    |

### Gruppe 6: Search / RAG

| Rolle             | Provider | Default-Modell         | Temp |
|-------------------|----------|------------------------|------|
| `fungus_summary`  | ollama   | qwen2.5-coder:7b       | 0    |
| `fungus_judge`    | openai   | gpt-4o-mini            | 0    |
| `rag_classifier`  | google   | gemini-2.0-flash       | 0    |

### Gruppe 7: Agent Pipelines

| Rolle              | Provider  | Default-Modell     | Temp |
|--------------------|-----------|--------------------|----- |
| `agent_minibook`   | openai    | gpt-4o             | 0    |
| `agent_pitch_deck` | anthropic | claude-sonnet-4-5  | 0.7  |
| `agent_pc_cleaner` | openai    | gpt-4o-mini        | 0    |

### Gruppe 8: Local-Only (Homelab)

| Rolle           | Provider | Default-Modell     | Temp | Zweck                |
|-----------------|----------|--------------------|----- |----------------------|
| `local_default` | ollama   | qwen2.5-coder:7b   | 0    | Offline-Default      |
| `local_chat`    | ollama   | llama3.2           | 0.7  | Offline-Chat         |
| `local_fast`    | ollama   | qwen2.5:3b         | 0    | Offline-Fast         |

---

## 5. Per-Directory Overrides

Ein Service kann ein Modell pro Rolle ueberschreiben, **ohne Code anzufassen**:

```yaml
overrides:

  the_brain:
    # Brain in Produktion will die staerksten Modelle
    brain_planning:         { model: claude-opus-4 }
    brain_context_tracking: { model: claude-opus-4 }

  la-fungus-search:
    # Search ist local-only, keine Cloud-Calls
    default:         { provider: ollama, model: qwen2.5-coder:7b }
    fungus_summary:  { provider: ollama, model: qwen2.5-coder:7b }

  security:
    # Free OpenRouter reicht fuer Security-Tests
    default:         { provider: openrouter, model: openrouter/auto }
```

**Typische Use-Cases:**

1. **Kosten-Kontrolle:** In Produktion teurere Modelle, in Dev/Test billige
2. **Offline-Modus:** Ein bestimmter Service darf nur Ollama nutzen (no-network Sandbox)
3. **Spezialisierung:** Ein Service braucht ein nicht-Default-Modell (z.B. Brain will Opus statt Sonnet)
4. **Migration:** Ein Service wird probeweise auf ein neues Modell umgestellt, ohne andere zu beeinflussen

Die Directory-Erkennung laeuft ueber den letzten Pfad-Bestandteil:

```python
# system/llm_client.py:75
dir_name = directory.replace("\\", "/").rstrip("/").split("/")[-1]
```

D.h. `get_client("brain_planning", directory="/home/.../the_brain/")` matched
auf `overrides.the_brain`.

---

## 6. Embedding-Modelle (separat von Chat-LLMs)

Chat-LLMs und Embeddings sind getrennt, weil ihre APIs unterschiedlich sind.
Auflösung ueber `vibemind_shared.get_embedding_model("role_name")`.

**Drei Driver-Typen:**

| Driver                | Wo es laeuft    | Typische Modelle                            |
|-----------------------|-----------------|---------------------------------------------|
| `sentence_transformers` | lokal, Python | all-MiniLM-L6-v2, google/embeddinggemma-300m |
| `openai`              | OpenAI/OR-kompat. | text-embedding-3-large/small              |
| `ollama`              | lokal, Ollama   | nomic-embed-text                            |

**Acht definierte Embedding-Rollen:**

| Rolle              | Driver                | Modell                         | Dim  | Zweck                         |
|--------------------|------------------------|--------------------------------|------|-------------------------------|
| `default`          | sentence_transformers | all-MiniLM-L6-v2               | 384  | Globaler Fallback             |
| `fungus_search`    | sentence_transformers | all-MiniLM-L6-v2               | 384  | la-fungus-search Code-Search  |
| `openai_large`     | openai                | text-embedding-3-large         | 3072 | High-Quality Cloud            |
| `openai_small`     | openai                | text-embedding-3-small         | 1536 | Cloud-Embedding gueenstig     |
| `ollama_local`     | ollama                | nomic-embed-text               | 768  | Lokal ueber Ollama            |
| `brain_clustering` | sentence_transformers | all-MiniLM-L6-v2               | 384  | Brain semantic clustering     |
| `embeddinggemma`   | sentence_transformers | google/embeddinggemma-300m     | 768  | Google EmbeddingGemma (lokal) |
| `code_search`      | sentence_transformers | all-MiniLM-L6-v2               | 384  | Project-Indexer Code-Search   |

**Warum 384 Dimensionen dominieren:** `all-MiniLM-L6-v2` ist der Standard fuer
alles, was semantisch clustering/search ist. Klein, schnell, local-first.
Nur wenn wirklich Qualitaet noetig ist, werden `openai_large` oder
`embeddinggemma` rausgeholt.

---

## 7. API-Keys & Environment

**Regel:** Keys **nie** in `llm_config.yml`. Nur `${VAR_NAME}` als Referenz:

```yaml
keys:
  openai:      ${OPENAI_API_KEY}
  anthropic:   ${ANTHROPIC_API_KEY}
  openrouter:  ${OPENROUTER_API_KEY}
  google:      ${GOOGLE_API_KEY}
  ollama:      null  # local, kein Key
```

Die Aufloesung passiert in `system/llm_client.py:34-41` (`_resolve_env`):

```python
pattern = re.compile(r"\$\{(\w+)\}")
def replacer(match):
    return os.environ.get(match.group(1), "")
return pattern.sub(replacer, value)
```

`.env` wird ueber `python-dotenv` geladen, bevor die Config gelesen wird.
Reihenfolge:
1. `.env` im Projekt-Root gelesen
2. `llm_config.yml` parsed
3. `${VAR}` durch `os.environ[VAR]` ersetzt
4. Client mit echtem Key instanziiert

---

## 8. `vibemind_shared` - das pip-Paket

Das Submodule `shared/` enthaelt `vibemind_shared`, die tatsaechliche
Factory-Implementierung. Installation:

```bash
pip install -e shared/
```

**Oeffentliche API:**

```python
from vibemind_shared import (
    get_client,           # AsyncOpenAI | AsyncAnthropic
    get_client_sync,      # OpenAI | Anthropic
    get_model,            # str
    get_temperature,      # float
    get_embedding_model,  # EmbeddingModel wrapper
    get_provider_info,    # dict fuer UI/Debug
    estimate_cost,        # kostenschaetzung basierend auf models_pricing.yml
)
```

**Scripts** (`shared/scripts/`):

| Script                    | Zweck                                                  |
|---------------------------|--------------------------------------------------------|
| `validate_config.py`      | Strukturpruefung von `llm_config.yml`                 |
| `sanitize_env.py`         | Scannt Repo auf geleakte API-Keys, redactet `.env`     |
| `audit_llm_usage.py`      | Findet direkte LLM-Client-Instanziierungen (Migration-Progress) |
| `health_check.py`         | Testet alle konfigurierten Keys auf echte API-Erreichbarkeit |

---

## 9. CI/CD Validation

`.github/workflows/check-config.yml` laeuft bei jedem Push/PR:

**Job 1: `validate`**
```yaml
steps:
  - pip install -e shared/
  - cp llm_config.yml.example llm_config.yml
  - python shared/scripts/validate_config.py   # Struktur-Check
  - python shared/scripts/sanitize_env.py --check-only  # keine Leaks
  - pytest shared/tests/ -v                    # Factory-Tests
  - python shared/scripts/audit_llm_usage.py   # Migration-Fortschritt
```

**Job 2: `no-hardcoded-keys`**

Blockt PRs, die verdaechtige Key-Muster im Code enthalten:

```bash
git grep -nE "sk-or-v[0-9]+-[A-Za-z0-9]{20,}|sk-proj-[A-Za-z0-9_-]{30,}|\
              sk-ant-[A-Za-z0-9_-]{30,}|gsk_[A-Za-z0-9]{30,}|sm_[a-zA-Z0-9]{30,}|\
              ghp_[A-Za-z0-9]{30,}|AIza[A-Za-z0-9_-]{30,}"
```

Patterns fuer OpenRouter, OpenAI, Anthropic, Groq, Supermemory, GitHub,
Google-API-Keys. Wenn einer matched → CI schlaegt fehl → PR blockiert.

---

## 10. `models_pricing.yml` - Kosten-Referenz

Neben `llm_config.yml` existiert `models_pricing.yml` fuer Kosten-Schaetzung.
Pro Modell:

```yaml
gpt-4o:
  provider: openai
  input:   2.50    # $/1M input tokens
  output:  10.00   # $/1M output tokens
  context: 128000  # max context window

claude-opus-4:
  provider: anthropic
  input:   15.00
  output:  75.00
  context: 200000
```

Genutzt ueber `vibemind_shared.estimate_cost(tokens_in, tokens_out, model)`.
Ollama-Modelle haben alle `input: 0, output: 0` (lokal = frei).

Pflege: Preise werden aus offiziellen Docs aktualisiert:
- OpenAI: https://openai.com/api/pricing/
- Anthropic: https://www.anthropic.com/pricing#anthropic-api
- OpenRouter: https://openrouter.ai/models
- Google: https://ai.google.dev/pricing

---

## 11. Erweiterungsworkflow

### Neue Rolle hinzufuegen

```
1. llm_config.yml.example editieren:
   roles:
     my_new_role: { provider: anthropic, model: claude-sonnet-4-5, temperature: 0.5 }

2. Validieren:
   python shared/scripts/validate_config.py

3. Im Code nutzen:
   client = get_client("my_new_role")
```

### Neuen Provider hinzufuegen (selten)

Neue Provider brauchen eine Issue-Diskussion vorher. Schritte:

```
1. Provider in llm_config.yml definieren (type + base_url + key_ref)
2. shared/src/vibemind_shared/llm_client.py:get_client erweitern
3. Tests in shared/tests/test_factory.py
4. CONTRIBUTING.md updaten
```

**Wichtig:** Neue Provider nur wenn `type: openai` ODER `type: anthropic` nicht
reichen (z.B. AWS-SigV4 fuer Bedrock, Service-Accounts fuer Vertex).

### Migration-Skill

Fuer Altcode mit direkten Client-Instanziierungen:

```
/llm-config-migration
```

Der Skill (`.claude/skills/llm-config-migration/SKILL.md`) geht datei-fuer-datei
durch und ersetzt direkte Instanziierungen durch `get_client(...)`.

---

## 12. Drift & Warnings (aktuelle Inkonsistenzen)

Bei der Analyse sind **zwei parallele LLM-Config-Systeme** aufgefallen, die
driften koennen:

### Root: `llm_config.yml.example`
- 5 Provider: openai, anthropic, openrouter, **google**, ollama
- 33 Rollen in 8 Gruppen
- Moderne Role-Namen: `brain_planning`, `coding_executor`, ...

### System: `system/llm_config.yml` + `system/llm_client.py`
- 6 Provider: openai, anthropic, openrouter, **gemini**, **groq**, ollama
- 10 Rollen, alte Namen: `red_team`, `blue_team`, `judge`, `analyzer`, ...
- Key-Aufloesung ueber `${GEMINI_API_KEY}` / `${GROQ_API_KEY}`

**Konsequenz:**
- CI validiert nur Root-`llm_config.yml.example`, nicht `system/llm_config.yml`
- `system/llm_client.py` ist eine Parallelimplementierung zu `vibemind_shared`
- `groq` ist in System-Config, aber nicht in Root-Config gelistet

### TODO: Konsolidierung

**Zielzustand:** Ein einziges System. Empfehlung:
- `system/llm_config.yml` als Directory-Override (`overrides.system:`) in die
  Root-Config migrieren
- `system/llm_client.py` durch `from vibemind_shared import get_client` ersetzen
- `groq` als Provider entweder in Root-Config aufnehmen oder aus System entfernen
- Alte Role-Namen auf neue Konvention umbenennen (`red_team` → `security_red_team`)

Anekdote gehoert in `system/llm_client.py` (TODO-Tag: `llm-config-consolidation`).

---

## 13. Bezug zur Space-MCP-Migration

**Diese Portion ist von der Migration NICHT betroffen.** Das LLM-Config-System
ist orthogonal zur Space-MCP-Architektur:

- LLM-Config: *Welches Modell fuer welche Rolle?* → bleibt
- Space-MCP: *Welches Tool fuer welchen Event?* → migriert

Nach der Migration bleiben die 33 Rollen unveraendert. Die einzige Ueberlappung:
Space-MCPs werden `get_client("<space>_*")` nutzen, d.h. neue Rollen kommen
dazu (z.B. `bubbles_agent`, `ideas_agent`), aber das Factory-Schema aendert
sich nicht.

---

## 14. Kern-Dateien Referenz

| Datei                                       | Zweck                                               |
|---------------------------------------------|-----------------------------------------------------|
| `llm_config.yml.example`                    | Tracked Template, Source of Truth (213 Zeilen)     |
| `llm_config.yml`                            | Lokale Kopie mit echten Werten (gitignored)        |
| `models_pricing.yml`                        | Preis-Referenz pro Modell                          |
| `shared/src/vibemind_shared/llm_client.py`  | Factory-Implementierung                            |
| `shared/scripts/validate_config.py`         | Config-Struktur-Check                              |
| `shared/scripts/sanitize_env.py`            | Secret-Scanner                                     |
| `shared/scripts/audit_llm_usage.py`         | Migration-Progress-Tracker                         |
| `shared/scripts/health_check.py`            | Echte API-Connectivity-Tests                       |
| `system/llm_client.py`                      | Parallel-Implementierung (TODO: konsolidieren)     |
| `system/llm_config.yml`                     | Parallel-Config (TODO: konsolidieren)              |
| `.github/workflows/check-config.yml`        | CI Validation + Secret-Scanning                    |
| `CONTRIBUTING.md`                           | Entwickler-Leitfaden zur LLM-Config                |
| `.claude/skills/llm-config-migration/`      | Automatisierte Migration fuer Altcode              |

---

## 15. Offene Fragen / TODOs

- [ ] `system/llm_config.yml` mit Root-Config konsolidieren (Parallelschiene aufloesen)
- [ ] `groq` Provider in Root-Config aufnehmen oder aus System entfernen (Entscheidung)
- [ ] `audit_llm_usage.py` Report als GitHub-Kommentar posten (statt nur Summary)
- [ ] Pro-Rolle Kosten-Dashboard bauen (`estimate_cost` pro Role aggregieren)
- [ ] Rate-Limiting pro Rolle einfuehren (heute nur per-Agent in OpenFang TOML)
- [ ] `fungus_search`, `brain_clustering`, `code_search` nutzen alle das gleiche Modell - zu einer Rolle `default_local_embed` zusammenfuehren?
- [ ] Neue Role-Convention dokumentieren: `<subsystem>_<purpose>` (z.B. `bubbles_agent`) fuer die kommenden Space-MCPs
