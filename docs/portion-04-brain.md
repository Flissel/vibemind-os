# Portion 4 — Brain (Tahlamus): Kognitives Routing

> **Teil 4 von 10** der VibeMind-OS-Systemdokumentation.
> **Umfang:** Größte Einzel-Portion. Das Brain ist mit 268+ Core-Modulen, 43
> Neuroscience-Modulen, 10 Bridges, 5 Attention-Ringen, 9 Cognitive-Loop-Phasen
> und 8 FSM-States die komplexeste Komponente des Systems.
> **Dependencies:** Portion 1 (System-Überblick), Portion 2 (LLM-Config),
> Portion 3 (Datenbank — v. a. `persistent_tasks` + `scheduled_tasks`).

---

## TL;DR

Das **Brain** (intern: *Tahlamus*) ist das kognitive Routing-Herzstück von
VibeMind-OS. Es läuft als FastAPI-Server auf **Port 5000** und implementiert
ein neurowissenschaftlich inspiriertes Modell in Code: ein 5-Ring Radial
Attention Network (**Sensory → Pattern → Semantic → Abstract → Meta**) wird von
**10 Neuromodulation-Bridges** moduliert, die zusammen 4 kompositierte
Modulationsfaktoren bilden (**attention_gain, precision_boost, ffn_throughput,
threshold_mod**). Ein **9-Phasen Cognitive Loop** treibt jede Anfrage durch
PERCEIVE → REMEMBER → ATTEND → MODULATE → REASON → REFLECT → LEARN →
CONSOLIDATE. Parallel läuft ein **Agent Loop V2** als FSM mit 8 States
(IDLE, PERCEIVING, THINKING, ACTING, WAITING_APPROVAL, OBSERVING, LEARNING,
DREAMING) der autonom Tasks bearbeitet. **Routing** geschieht in drei Layern
(TaskFeatureRouter → ConversationPathPlanner → DecisionRouter) und endet in
einer 10×4 Routing-Matrix. Idle-Phasen lösen einen **Dream-Zyklus** aus, der
Erfahrungen replayed und das Netzwerk via 4-Loss-Backprop weiterlernt.
Das Brain schreibt seine Zustände über SSE-Streams (2 Hz) und WebSockets an
neun HTML-Dashboards.

---

## 1. Philosophie & neurowissenschaftliche Inspiration

Das Brain ist **kein** generisches LLM-Wrapper. Es ist der Versuch, Prinzipien
aus der echten Neurowissenschaft als Software-Architektur zu implementieren:

| Biologisches Vorbild                          | Software-Umsetzung                                       |
|-----------------------------------------------|----------------------------------------------------------|
| Thalamus als Gating-/Relay-Zentrum            | `ThalamicGateMiddleware` + `ThalamicAdapter`             |
| Präfrontaler Kortex (PFC) — Executive Control | `prefrontal_cortex.py` + `cortex_bridge.py`              |
| Anteriores Zingulum (ACC) — Conflict-Monitor  | `anterior_cingulate.py` (ACC-Conflict-Signal)            |
| Dopaminsystem (VTA → NAcc)                    | `ventral_tegmental_area.py` + `NeuromodulationBridge`    |
| Locus Coeruleus (NE-System)                   | `locus_coeruleus.py` — Arousal & Gain-Modulation         |
| Amygdala — Threat & Valence                   | `amygdala_complex.py` + `limbic_bridge.py`               |
| Cerebellum — Forward Models & Timing          | `cerebellum_module.py` — Motor-Timing, Predictive Coding |
| Hippocampus — Episodic Memory                 | `hippocampus.py` + `experience_buffer.py`                |
| Default Mode Network (DMN)                    | `default_mode_network.py` — Self-Referential Processing  |
| Schlaf-Wach-Zyklen → Gedächtnis-Konsolidierung| `dream_mode.py` + `radial_sleep_trainer.py`              |

**Kern-Ideen:**

1. **Predictive Coding.** Jeder Ring prädiziert die Eingabe des nächsten Rings;
   die Differenz (Prediction Error) speist Lernen.
2. **Neuromodulation statt Hyperparameter.** Attention-Gain, Precision und
   Throughput sind nicht fix — sie werden live von 10 Bridges angepasst,
   basierend auf Emotion, Threat, Fatigue, Reward.
3. **Hebbian statt Gradient.** Attention-Biases lernen online via Hebb-Regel
   (`hebbian_plasticity.py`) — kein Backprop im normalen Betrieb. Gradient-
   Lernen passiert nur während der Dream-Phase.
4. **Gating sum = 1.0.** Das zentrale Invariant: alle Brain-Gates (10-dim
   Routing-Gewicht über 10 Ziele) summieren exakt zu 1.0. Bricht das, bricht
   das Routing (siehe Abschnitt 15 Invariants).
5. **ModulationContext clamp [0.3, 3.0].** Jeder Faktor wird in diesem
   Bereich geclamped — verhindert Runaway-Excitation und stille Deaktivierung.

---

## 2. Verzeichnis-Map

Das Brain-Root ist `/brain/the_brain/` (ca. 15 MB, ohne Datenbank-Files).

```
brain/the_brain/
├── CLAUDE.md                  ← Leitfaden für Claude-Code (175 Z.)
│
├── core/                      ← 268 Module, ~1.8 MB. Herz des Brains.
│   ├── radial_attention.py        5-Ring-Netzwerk (RingLayer)
│   ├── modulation_context.py      4 kompositierte Faktoren
│   ├── hebbian_plasticity.py      Online-Lernen (kein Gradient)
│   ├── experience_buffer.py       FIFO Replay-Buffer
│   ├── radial_sleep_trainer.py    4-Loss Backprop im Traum
│   │
│   ├── *_bridge.py  (10 Dateien)  ← Neuromodulation Bridges
│   │   neuromodulation_bridge.py, cortex_bridge.py, limbic_bridge.py,
│   │   sleep_wake_bridge.py, motor_bridge.py, defense_bridge.py,
│   │   memory_bridge.py, integration_bridge.py, visceral_bridge.py,
│   │   social_perception_bridge.py
│   │
│   ├── cognitive_loop.py          9-Phasen-Loop (LoopContext, LoopPhase)
│   ├── agent_loop.py              V2 FSM (1,853 Z.)
│   ├── sensor_systems.py          11 Sensor/Fusion-Module
│   ├── action_systems.py          7 Action/Approval-Module
│   ├── goal_management.py         Goal-Hierarchie + Conflict-Resolver
│   ├── motivation_drives.py       Curiosity, Competence, Homeostatic
│   ├── safety_regulation.py       Autonomy-Budget, Safety-Governor
│   │
│   ├── task_feature_router.py        Layer 1 (529 Z.)
│   ├── conversation_path_planner.py  Layer 2 (577 Z.)
│   ├── decision_router.py            Layer 3 (683 Z.)
│   ├── hierarchical_planner.py       Integration der 3 Layer
│   │
│   ├── dream_mode.py               Experience Replay + Counterfactuals (539 Z.)
│   ├── memory_consolidation.py     30s Sleep Cycle
│   ├── temporal_memory.py          Temporal Context
│   ├── brain_chat.py               Chat-Engine (200 KB)
│   ├── multi_llm_router.py         GPT-4o, DeepSeek R1, Claude 3.5, Gemini
│   ├── multi_ctm_ensemble.py       4 Continuous-Thought-Models
│   │
│   └── <43 Neuroscience-Module>    PFC, ACC, VTA, LC, Raphe, BF, Amygdala,
│                                   Hippocampus, Cerebellum, DMN, ...
│                                   (siehe Abschnitt 11)
│
├── web/                       ← FastAPI-Server + Dashboards
│   ├── brain_server.py            Haupt-Entry-Point (996 Z.)
│   ├── routers/                   11 API-Router
│   ├── streams/                   SSE/WebSocket-Streams
│   └── templates/                 9 HTML-Dashboards (9,881 Z. gesamt)
│
├── production/                ← Planner + unified service
│   ├── production_planner.py      Dream-Orchestrator (2,587 Z.)
│   ├── brain_heartbeat.py         30s Background-Tick (652 Z.)
│   └── api_server.py              REST-API für Bridge (Portion 5)
│
├── configs/
│   └── default.yaml               Master-Config (1,082 Z.)
│
├── tests/                     ← 206 Test-Dateien, 1.981+ Tests
├── training/                  ← CTM Ensemble Training
├── learning_engine/           ← Continuous Learning
├── logical_brain/             ← Logic-based Routing
├── memory_api/                ← Memory-Service-Interfaces
├── monitoring/                ← Metrics & System-Monitor
├── docs/                      ← 25+ interne Dokus
├── tribe/                     ← Multi-Agent-Koordination
└── demos/, examples/, integrations/, scripts/, setup/
```

**Scale-Zahlen:**
- 268 Python-Dateien in `core/`
- 206 Test-Dateien mit 1.981+ Tests
- 43 eigenständige Neuroscience-Module
- 10 Neuromodulation-Bridges
- 11 Web-Router + 2 Streaming-Endpoints
- 9 HTML-Dashboards (insgesamt 9.881 Zeilen)
- `default.yaml` mit 1.082 Konfigurations-Zeilen

---

## 3. Entry-Points, Ports & Health-Check

### 3.1 Start-Kommandos

```bash
# Direkt (lokal, Debug)
python -m the_brain.web.brain_server

# Als Paket (installiert)
python -m brain.the_brain.web.brain_server

# Docker-Compose (Production)
docker-compose up brain
```

### 3.2 Port-Map

| Port  | Service                       | Quelle                          |
|-------|-------------------------------|----------------------------------|
| 5000  | FastAPI Brain-Server (Haupt)  | `web/brain_server.py`           |
| 5001  | API-Service (für Bridge)      | `production/api_server.py`      |
| 5002  | Swarm-Service                 | `production/swarm_service.py`   |
| 5003  | Unified Brain-Service         | `production/unified_service.py` |
| 5004  | Dashboard-Service             | `web/dashboard_service.py`      |
| 8001  | Memory-Service                | `memory_api/*`                  |

### 3.3 Health-Check

```bash
curl http://localhost:5000/api/health
# → {"status": "healthy", "uptime": 12345.67, "bridges_active": 10, ...}
```

### 3.4 Drei primäre Dashboards

```
http://localhost:5000/              → brain_dashboard.html (Main)
http://localhost:5000/brain         → unified_brain_dashboard.html (SVG-Rings)
http://localhost:5000/radial        → radial_dashboard.html (Live-Streams 2 Hz)
```

---

## 4. Radial Attention Network — 5 Ringe

Das zentrale Element des Brains. Implementiert in
`core/radial_attention.py` (529 Zeilen, Klassen `RingLayer` und
`RadialAttentionNetwork`).

### 4.1 Architektur

Eingabe (Seed-Vektor, 384 Dim) wird durch 5 konzentrische Ringe mit
wachsender/fallender Dimensionalität propagiert:

```
                  ┌─────────────────────────┐
                  │  Ring 4: Meta (128D)    │  ← Self-Model, Consciousness
                  │                         │
                  │  Ring 3: Abstract (256D)│  ← High-Level-Abstraktion
                  │                         │
                  │  Ring 2: Semantic (256D)│  ← Bedeutung, Multi-Head-Attn
                  │                         │
                  │  Ring 1: Pattern (128D) │  ← Pattern Matching, Hebb-Attn
                  │                         │
                  │  Ring 0: Sensory (64D)  │  ← Encoder-Output
                  └─────────────────────────┘
                              ▲
                         Seed (384D)
```

**Per-Ring Forward-Pass (`RingLayer.forward`):**

1. **Input-Projection** `in_dim → out_dim`
2. **Self-Attention** (4 Heads, Multi-Head Attention)
3. **Precision Gate** — lernbare Gewichte, die "Trust" in Prediction Errors
   steuern. Output: `attended` + `gate_weight` ∈ [0, 1]
4. **Predictive Coding**
   `error = attended − top_down_prediction` (die Prediction kommt vom Ring
   *darüber*, wenn vorhanden)
5. **Modulation** — 4 Composite-Faktoren aus `ModulationContext` (siehe
   Abschnitt 6) werden **multiplikativ** angewendet:
   - `attention_gain`  → auf Attention-Weights
   - `precision_boost` → auf Precision-Gate
   - `ffn_throughput`  → auf FFN-Aktivierung
6. **Feedforward Network** `out_dim → out_dim*4 → out_dim`, GELU-Aktivierung
7. **Residual Connection + Layer Norm**

**Output pro Ring:**
```python
{
    "activation": Tensor(out_dim),
    "attention_weights": Tensor(heads, seq, seq),
    "prediction_error": Tensor(out_dim),
    "gate_weight": float,        # 0..1
}
```

### 4.2 Ring-Beschreibungen im Detail

| Ring | Name     | Dim | Zweck & Biologisches Vorbild                                                    |
|------|----------|-----|---------------------------------------------------------------------------------|
| 0    | Sensory  | 64  | Roh-Features aus Seed-Encoder. Analog zu primären sensorischen Kortizes (V1/A1).|
| 1    | Pattern  | 128 | Pattern Matching mit Hebb-Biases. Analog zu sekundären Kortizes (V2, A2).       |
| 2    | Semantic | 256 | Bedeutungs-Repräsentation via Multi-Head-Attn. Analog zu temporalem Kortex.     |
| 3    | Abstract | 256 | High-Level-Abstraktion, Composable Features. Analog zu parietalem Assoziations-K.|
| 4    | Meta     | 128 | Self-Model, "Ich-bin-gerade-am-Denken"-Signal. Analog zu PFC + DMN.             |

**Warum Meta kleiner als Abstract?** Meta ist bewusst ein Bottleneck — nur die
wichtigsten Meta-Informationen werden komprimiert weitergegeben (analog zum
Bewusstseins-"Spotlight").

### 4.3 Hebbian Plasticity (Live-Lernen ohne Gradient)

Datei: `core/hebbian_plasticity.py`.

Die Attention-Biases werden während der normalen Verarbeitung live angepasst:

```
δW = η · activation_pre ⊗ activation_post   (Hebbs Regel)
W_new = W_old + δW − decay · W_old
```

Default aus `default.yaml`:
- `learning_rate: 0.001`
- `decay: 0.0001`
- **EWC Lambda: 100.0** — Elastic Weight Consolidation schützt wichtige
  Gewichte vor katastrophalem Vergessen

**Wichtig:** Hebbian läuft *immer* (im Forward Pass). Gradient-Descent läuft
*nur* im Dream Mode (Abschnitt 9).

### 4.4 Experience Buffer & Replay

Datei: `core/experience_buffer.py`.

- **Kapazität:** 5.000 Experiences (default)
- **Struktur:** FIFO-Queue, jede Experience speichert (seed, ring_activations,
  prediction_errors, modulation_state, reward)
- **Verwendung:** Im Dream Mode werden Batches gesampled und der
  `RadialSleepTrainer` macht Gradient-Descent.

---

## 5. Die 10 Neuromodulation-Bridges

Jede Bridge ist ein Paar aus `<Name>State` (Dataclass mit Skalar-Feldern) und
`<Name>Bridge` (Update-Logik). Alle Bridges folgen dem gleichen Protokoll:

```python
# Tick n:
state_n = bridge.update(prediction_errors, context)  # speichert state
# Tick n+1:
factors = state_n.compute_factors()                  # wird im Forward verwendet
```

Das heißt: **Neuromodulation ist immer um einen Tick verzögert.** Bridge-Signale
beeinflussen den *nächsten* Forward-Pass, nicht den aktuellen. Das vermeidet
Feedback-Schleifen und macht das System stabil.

### 5.1 Überblick-Tabelle

| # | Bridge                    | Datei                              | Biologisches Substrat        | Output-Skalare (Auszug)                     |
|---|---------------------------|------------------------------------|------------------------------|---------------------------------------------|
| 1 | NeuromodulationBridge     | `neuromodulation_bridge.py`        | VTA, LC, Raphe, BF, LHb      | DA, NE, 5-HT, ACh, anti_reward, ne_gain     |
| 2 | CortexBridge              | `cortex_bridge.py`                 | PFC, ACC, OFC                | pfc_bias, acc_conflict, ofc_value           |
| 3 | LimbicBridge              | `limbic_bridge.py`                 | Amygdala, NAcc, BNST         | valence, arousal, threat, salience          |
| 4 | SleepWakeBridge           | `sleep_wake_bridge.py`             | TMN, Pineal, PPN             | arousal, histamine, melatonin               |
| 5 | MotorBridge               | `motor_bridge.py`                  | Cerebellum, SN, ZI, RN       | model_confidence, action_tendency           |
| 6 | DefenseBridge             | `defense_bridge.py`                | PAG, Amygdala                | defense_intensity, anxiety_level            |
| 7 | MemoryBridge              | `memory_bridge.py`                 | Hippocampus, Septal, Mammil. | theta_power, consolidation_strength         |
| 8 | IntegrationBridge         | `integration_bridge.py`            | Claustrum, DMN, SC           | binding_strength, dmn_activity, orienting   |
| 9 | VisceralBridge            | `visceral_bridge.py`               | Insular, NTS, PBN            | afferent_strength, liking                   |
| 10| SocialPerceptionBridge    | `social_perception_bridge.py`      | Fusiform, TPJ                | social_salience, familiarity                |

**Gesamt-Skalare-Zoo:** 22 benannte Hormon-/Signal-Skalare (H1..H22 im Code-
Kommentar), plus abgeleitete Composites.

### 5.2 Bridge 1: Neuromodulation (VTA/LC/Raphe/BF/LHb)

**Datei:** `core/neuromodulation_bridge.py` (152 Zeilen).

Das klassische "Neuromodulator-Quartett" plus Anti-Reward:

| Signal       | Quelle                    | Berechnung (vereinfacht)                    | Wirkung                    |
|--------------|---------------------------|---------------------------------------------|----------------------------|
| DA (H2)      | VTA                       | `positive_reward − anti_reward`             | precision_boost ↑          |
| NE (H1)      | Locus Coeruleus (LC)      | `magnitude(prediction_errors)`              | attention_gain ↑           |
| 5-HT (H4)    | Raphe Nuclei              | gleitender Mittelwert Valence               | ffn_throughput ↓ (beruhigend)|
| ACh (H3)     | Basal Forebrain           | `salience · novelty`                        | ffn_throughput ↑ (lernbereit)|
| anti-reward  | Lateral Habenula (LHb)    | `max(0, expected_reward − actual_reward)`   | DA-Dämpfung                |
| NE-Gain      | LC                        | Multiplikator auf NE                         | Runaway-Schutz             |
| explore_ratio| VTA/Raphe-Ratio           | `DA / (DA + 5HT + ε)`                       | threshold_mod              |

### 5.3 Bridge 2: Cortex (PFC/ACC/OFC)

**Datei:** `core/cortex_bridge.py` (154 Zeilen).

Executive-Control-Signale aus den präfrontalen Arealen:

- `pfc_bias` (H7) — top-down Task-Bias, welche Features relevant sind
- `acc_conflict` (H8) — Konflikt zwischen Response-Optionen; steigt bei
  widersprüchlichen Prioritäten
- `ofc_value` (H9) — subjektiver Wert (Nutzen minus Kosten); moduliert
  Precision-Gate

### 5.4 Bridge 3: Limbic (Amygdala/NAcc/BNST)

**Datei:** `core/limbic_bridge.py` (181 Zeilen).

Emotional-affektive Dimension:

- `valence` — angenehm ↔ unangenehm, [-1, 1]
- `arousal` (H10) — Aktivierungs-Niveau, [0, 1]
- `threat` — direkte Bedrohung (z. B. Safety-Violation)
- `salience` (H11) — Wichtigkeit des aktuellen Reizes
- `nogo_drive` (H12) — Inhibitions-Drive (Stop-Signal)
- `urgency` (H13) — Zeitdruck

### 5.5 Bridge 4: Sleep/Wake (TMN/Pineal/PPN)

**Datei:** `core/sleep_wake_bridge.py` (217 Zeilen).

Zirkadiane & Schlaf-Regulation:

- `sleep_arousal` — Gesamt-Arousal-Zustand
- `histamine` (H15) — TMN-Output; hoch = wach, niedrig = schläfrig
- `melatonin` (H16) — Pineal-Output; hoch = Dream-Mode-Tendenz

Getriggered durch: `idle_seconds`, `sleep_pressure`, Uhrzeit-Simulation.

### 5.6 Bridge 5: Motor (Cerebellum/SN/ZI/RN)

**Datei:** `core/motor_bridge.py` (248 Zeilen).

Action-Selection + Motor-Confidence:

- `motor_confidence` (H17) — Cerebellum-Forward-Model-Sicherheit
- `action_tendency` (H18) — Tendenz zur Handlung vs. Warten

### 5.7 Bridge 6: Defense (PAG/Amygdala)

**Datei:** `core/defense_bridge.py` (203 Zeilen).

Fight / Flight / Freeze:

- `defense_intensity` (H19) — Gesamt-Defense-Aktivität
- `anxiety_level` (H20) — chronische Angst (vs. akute Threat)

Steigt bei wiederholten Safety-Violations oder hohen Prediction-Errors.

### 5.8 Bridge 7: Memory (Hippocampus/Septal/Mammilar)

**Datei:** `core/memory_bridge.py` (196 Zeilen).

Gedächtnis-Modulation:

- `theta_power` (H21) — Theta-Oszillation; hoch während Encoding/Recall
- `consolidation_strength` (H22) — im Dream Mode aktiv, steuert welche
  Experiences ins Langzeitgedächtnis wandern

### 5.9 Bridge 8: Integration (Claustrum/DMN/SC)

**Datei:** `core/integration_bridge.py` (275 Zeilen, größte Bridge).

Bewusstseins-Binding & Orienting:

- `binding_strength` — Cross-Modal-Binding (Claustrum)
- `dmn_activity` — Default-Mode; steigt bei Idle-Selbstreflexion
- `orienting` — Superior-Colliculus-artiges "Hinwenden"

### 5.10 Bridge 9: Visceral (Insular/NTS/PBN)

**Datei:** `core/visceral_bridge.py` (176 Zeilen).

Interoception — "Gefühl aus dem Körper":

- `afferent_strength` — wie stark Körpersignale ins Bewusstsein dringen
- `liking` — hedonic valence (VP-ähnlich), unterscheidet sich von `valence`
  (Limbic) durch Fokus auf *Konsum* vs. *Antizipation*

### 5.11 Bridge 10: Social Perception (Fusiform/TPJ)

**Datei:** `core/social_perception_bridge.py` (241 Zeilen).

- `social_salience` — wie stark soziale Reize (Gesichter, Sprache) priorisiert
  werden
- `familiarity` — Erkennen bekannter Entitäten (User, Agent-IDs)

---

## 6. ModulationContext — Die 4 kompositierten Faktoren

**Datei:** `core/modulation_context.py` (245 Zeilen).

Das **zentrale Aggregations-Objekt**. Es holt Signale aus *allen* 10 Bridges,
rechnet sie zu genau **4 Composite-Faktoren** zusammen und wendet sie im
Forward-Pass jedes Rings an:

```python
ctx = ModulationContext.from_bridges(all_bridge_states)
factors = ctx.compute()
# factors = {
#   "attention_gain":  float ∈ [0.3, 3.0],
#   "precision_boost": float ∈ [0.3, 3.0],
#   "ffn_throughput":  float ∈ [0.3, 3.0],
#   "threshold_mod":   float ∈ [0.3, 3.0],
# }
```

### 6.1 Faktor 1: attention_gain

**Wirkung:** Multiplikator auf die Attention-Weights jedes Rings. Erhöht oder
senkt den Fokus.

**Quellen:**
| Quelle                         | Signal                | Wirkung            |
|--------------------------------|-----------------------|--------------------|
| NeuromodulationBridge          | NE (H1)               | hoch → stark fokussiert |
| LimbicBridge                   | arousal (H10)         | hoch → wacher      |
| MotorBridge                    | action_tendency (H18) | hoch → Handlungs-Mode |
| DefenseBridge                  | defense_intensity (H19)| hoch → Tunnel-Vision |
| MemoryBridge                   | theta_power (H21)     | hoch → Encoding-Mode |

**Formel (vereinfacht):**
```
attention_gain = clamp(
    0.5 + 0.7*NE + 0.5*arousal + 0.3*action_tendency
         + 0.4*defense_intensity + 0.2*theta_power,
    min=0.3, max=3.0
)
```

### 6.2 Faktor 2: precision_boost

**Wirkung:** Multiplikator auf das **Precision Gate** jedes Rings. Hohe
Precision = mehr Vertrauen in Prediction-Errors (aktives Lernen). Niedrige
Precision = dämpfendes Verhalten.

**Quellen:**
| Quelle                | Signal                      | Wirkung                    |
|-----------------------|-----------------------------|----------------------------|
| NeuromodulationBridge | DA (H2) − anti_reward        | hoch → Belohnungs-Lernen  |
| CortexBridge          | OFC value (H9)              | hoch → value-driven       |
| LimbicBridge          | salience (H11)              | hoch → wichtige Features  |
| MemoryBridge          | consolidation_strength (H22)| hoch → festigt Gelerntes  |

### 6.3 Faktor 3: ffn_throughput

**Wirkung:** Multiplikator auf die Feedforward-Aktivierung. Reguliert "Geistes-
Bandbreite" — wie viel Information pro Tick durchgereicht wird.

**Quellen:**
| Quelle                | Signal                 | Wirkung                    |
|-----------------------|------------------------|----------------------------|
| NeuromodulationBridge | ACh (H3)              | hoch → lernbereit         |
| NeuromodulationBridge | 5-HT (H4)             | hoch → gedämpfter Throughput|
| LimbicBridge          | urgency (H13)         | hoch → schnell             |
| SleepWakeBridge       | histamine (H15)       | hoch → wach                |
| MotorBridge           | motor_confidence (H17)| hoch → schnelle Ausführung |
| DefenseBridge         | anxiety_level (H20)   | hoch → Gedanken-Rasen      |

### 6.4 Faktor 4: threshold_mod

**Wirkung:** Modifiziert den Decision-Threshold des `DualProcessRouter`.
Entscheidet, ob schnell (System 1) oder langsam (System 2) geroutet wird.

**Quellen:**
| Quelle                | Signal                 | Wirkung                    |
|-----------------------|------------------------|----------------------------|
| NeuromodulationBridge | explore_ratio (H6)    | hoch → explorativ         |
| CortexBridge          | acc_conflict (H8)     | hoch → System 2 bevorzugt |
| LimbicBridge          | nogo_drive (H12)      | hoch → Inhibition         |
| SleepWakeBridge       | melatonin (H16)       | hoch → langsamer/vorsichtiger|

### 6.5 Clamping-Invariant: [0.3, 3.0]

Jeder Faktor wird strikt geclamped:

```python
clamped = max(0.3, min(3.0, raw_value))
```

**Warum diese Grenzen?**
- **Untergrenze 0.3** — verhindert stille Deaktivierung eines Rings. Ein
  Faktor nahe 0 würde den Ring taub machen und nicht mehr recovern.
- **Obergrenze 3.0** — verhindert Runaway-Excitation. Ein Faktor > 3 würde
  Attention und FFN explodieren lassen; wertvolle Repräsentationen gingen
  in Rauschen unter.

Das ist eines der **zentralen System-Invariants** (siehe Abschnitt 15).

---

## 7. Cognitive Loop — Die 9 Phasen

**Datei:** `core/cognitive_loop.py` (1.981 Zeilen).

Jede eingehende Anfrage (Voice, Chat, Agent-Call) durchläuft einen fest
strukturierten **9-Phasen-Zyklus**. Zwischen den Phasen teilen sich alle
Module ein gemeinsames Dataclass-Workspace: `LoopContext`.

```python
class LoopPhase(Enum):
    PERCEIVE     = 1   # Features extrahieren
    REMEMBER     = 2   # Gedächtnis-Kontext
    ATTEND       = 3   # Attention-State aufbauen
    MODULATE     = 4   # Neuromodulation anwenden
    REASON       = 5   # Routing-Entscheidung
    REFLECT      = 6   # Prediction-Errors & Confidence
    LEARN        = 7   # Meta-Parameter-Updates
    CONSOLIDATE  = 8   # Temporal-Context bauen
```

*(Die 9. Phase — `LOOPBACK` — ist eine Re-Entry ins System, siehe 7.10.)*

### 7.1 Phase 1: PERCEIVE

**Was passiert:** `TaskFeatureRouter` extrahiert Features aus der Anfrage
(Text, Voice-Transkript, Event-Payload). Berechnet `RoutingState` mit
`routing_weights` (10-Dim) und `processing_mode` (`fast` | `deep`).

**Output in `LoopContext`:**
```python
ctx.features = {...}
ctx.routing_state = RoutingState(weights=[...], mode="deep")
ctx.layer1_prediction = Tensor(10)
```

### 7.2 Phase 2: REMEMBER

**Was passiert:** Memory-Context-Retrieval aus dem Moltbook-Store (semantic
embeddings) und dem `experience_buffer`. Berechnet:
- **Memory-Bias** — welche Ring-Outputs werden erwartet (Prior aus Memory)?
- **Confidence-Hint** — wie oft wurde ähnliches schon gelöst?

**Config:** `memory_routing_bias_strength: 0.25` (wie stark Memory ins Routing
reinzieht).

### 7.3 Phase 3: ATTEND

**Was passiert:** Berechnet den aktuellen Attention-State und die
Gating-Weights für jeden Ring. Entscheidet auch, welcher CTM (Continuous
Thought Model, 4 Domänen) aktiviert wird.

**Output:**
```python
ctx.attention_state = {...}
ctx.gating_weights = Tensor(5)   # pro Ring
ctx.ctm_domain_hint = "klotski" | "temporal" | "domain_transfer" | "hierarchical"
```

**Config:** `attention_gating_strength: 0.5`.

### 7.4 Phase 4: MODULATE

**Was passiert:** Alle 10 Bridges werden abgefragt, ihre `State`-Objekte
gelesen, und an `ModulationContext` weitergereicht. Der berechnet die 4
Composite-Faktoren (Abschnitt 6).

**Output:**
```python
ctx.modulation_factors = {
    "attention_gain":  1.2,
    "precision_boost": 0.9,
    "ffn_throughput":  1.0,
    "threshold_mod":   1.1,
}
ctx.gating_temperature = 0.8   # fuer Softmax ueber Routing-Ziele
```

### 7.5 Phase 5: REASON

**Was passiert:** Der Haupt-Routing-Schritt. Hier wird das **5-Ring-Netzwerk**
tatsächlich durchlaufen mit den Modulationsfaktoren aus Phase 4. Danach
kommt der **DecisionRouter** (Layer 3) und produziert:
- `brain_gates` — 10-Dim Softmax über 10 Routing-Ziele (Spaces/Agents)
- `confidence` — float ∈ [0, 1]

Wenn `ctm_domain_hint` aktiv: der entsprechende CTM wird parallel aufgerufen
(Timeout: 30 Sekunden, Config `ctm_timeout`).

**Kern-Invariant:** `sum(brain_gates) = 1.0 ± ε` (Abschnitt 15).

### 7.6 Phase 6: REFLECT

**Was passiert:** Selbst-Evaluation des vorherigen Ring-Durchlaufs:
- Prediction-Errors aggregieren
- Curiosity-Signal berechnen (Novelty × Competence-Gap)
- Consciousness-Metriken (Integration, Self-Model-Activation)
- **`should_reconsider`-Flag** setzen, falls Confidence zu niedrig ODER
  Prediction-Error zu hoch

Config: 14 Enable-Flags für Sub-Features (z. B.
`phase6_curiosity_enabled: true`).

### 7.7 Phase 7: LEARN

**Was passiert:** Meta-Parameter-Updates — hier wird *nicht* das Ring-Netzwerk
trainiert (das passiert im Dream, Abschnitt 9), sondern:
- Hebbian-Biases (online, `hebbian_plasticity.py`)
- Bridge-Decay-Parameter
- Routing-Layer-Learning-Rate-Anpassungen

### 7.8 Phase 8: CONSOLIDATE

**Was passiert:** Bauen des **Temporal-Context** für die nächste Anfrage.
Schreibt:
- Aktuellen State ins `temporal_memory`
- Wichtige Experiences in den `experience_buffer`
- Update der `session`-Einträge in Supabase (siehe Portion 3
  `conversation_sessions`)

### 7.9 Phase 9 / LOOPBACK

Wenn `should_reconsider = True` aus Phase 6: der Loop startet erneut mit
Phase 1 — aber mit angereichertem `LoopContext` (mehr Memory, andere
Modulation). Maximum 3 Iterationen pro Anfrage, sonst Zwangs-Exit mit
niedrigem-Confidence-Flag.

### 7.10 LoopContext — Shared Workspace

Alle 9 Phasen schreiben in ein einziges `LoopContext`-Dataclass:

```python
@dataclass
class LoopContext:
    request_id: str
    features: dict
    routing_state: RoutingState
    memory_context: list
    attention_state: dict
    modulation_factors: dict
    ring_activations: list[Tensor]  # 5 Tensoren
    brain_gates: Tensor             # 10-Dim Softmax
    confidence: float
    prediction_errors: list[float]
    should_reconsider: bool
    phase_timings: dict             # fuer Dashboard
```

Das Dashboard `/brain` visualisiert jede Phase live — man sieht, wo die Zeit
bleibt und welcher Ring gerade aktiv ist.

---

## 8. Agent Loop V2 — FSM mit 8 States

**Datei:** `core/agent_loop.py` (1.853 Zeilen).

Parallel zum Cognitive Loop läuft ein **autonomer** Agent-Loop als Finite
State Machine. Während der Cognitive Loop *pro Anfrage* läuft, tickt der
Agent Loop **kontinuierlich** im Hintergrund und bearbeitet eine Prioritäts-
Queue von Tasks.

### 8.1 Die 8 States

```
          ┌──────────────────────────────────┐
          │             IDLE                 │◄──────────┐
          └──────┬───────────────┬───────────┘           │
                 │               │                        │
                 ▼               ▼                        │
         ┌────────────┐   ┌──────────────┐               │
         │ PERCEIVING │   │   DREAMING   │───────────────┤
         └──────┬─────┘   └──────────────┘               │
                ▼                                        │
         ┌────────────┐                                  │
         │  THINKING  │                                  │
         └──────┬─────┘                                  │
                ▼                                        │
         ┌────────────┐    ┌──────────────────┐         │
         │   ACTING   │───►│ WAITING_APPROVAL │─────────┤
         └──────┬─────┘    └──────────────────┘         │
                ▼                                        │
         ┌────────────┐                                  │
         │  OBSERVING │                                  │
         └──────┬─────┘                                  │
                ▼                                        │
         ┌────────────┐                                  │
         │  LEARNING  │──────────────────────────────────┘
         └────────────┘

  Jeder State → STOPPED (Shutdown)
```

**Transitionen (aus `VALID_TRANSITIONS` dict):**

| Von              | Nach (erlaubt)                                   |
|------------------|--------------------------------------------------|
| IDLE             | PERCEIVING, DREAMING, STOPPED                    |
| PERCEIVING       | THINKING, IDLE, STOPPED                          |
| THINKING         | ACTING, IDLE, STOPPED                            |
| ACTING           | WAITING_APPROVAL, OBSERVING, STOPPED             |
| WAITING_APPROVAL | OBSERVING, IDLE, STOPPED                         |
| OBSERVING        | LEARNING, IDLE, STOPPED                          |
| LEARNING         | IDLE, STOPPED                                    |
| DREAMING         | IDLE, STOPPED                                    |

Ungültige Transitionen werfen `InvalidStateTransitionError` — absichtlich
hart, damit Bugs im Task-Flow sofort auffallen.

### 8.2 Task-Priorität (4 Stufen)

```python
class TaskPriority(IntEnum):
    P0_USER_REQUEST    = 0   # User hat /predict oder Voice-Input
    P1_ALARM           = 1   # System-Alarm, Service-Down, Safety-Violation
    P2_SELF_INITIATED  = 2   # Curiosity-Drive, Goal-Pursuit
    P3_BACKGROUND      = 3   # Consolidation, Health-Checks
```

**Task-Scoring:**
```
score = priority_base
      + urgency_bonus         # aus LimbicBridge.urgency (H13)
      + importance_bonus      # aus CortexBridge.ofc_value
      − age_penalty           # aeltere Tasks werden staerker
```

### 8.3 Tick-Intervalle

| Zustand                                      | Interval | Config-Key                 |
|----------------------------------------------|----------|----------------------------|
| Tasks in Queue (aktiv)                       | 1,0 s    | `active_tick_interval`     |
| Kein Task, aber innerhalb Idle-Threshold    | 30 s     | `idle_tick_interval`       |
| Idle > 300 s → Dream-Mode                    | 60 s     | `dream_tick_interval`      |

**Dream-Dauer pro Zyklus:** 120 s (Config: `dream_duration`).

### 8.4 Safety-Governor & Autonomy-Budget

**Datei:** `core/safety_regulation.py`.

Der Agent Loop darf sich nicht beliebig austoben. Budgets aus `default.yaml`:

| Ressource            | Limit              |
|----------------------|--------------------|
| shell_command        | 50 / Stunde        |
| file_write           | 10 / Stunde        |
| coding_job           | 5 / Tag            |
| llm_token            | 1.000 / Minute     |
| network_call         | 30 / Stunde        |
| autonomous_actions   | 50 / Stunde (total)|

Zusätzlich Whitelist für `allowed_paths` und `allowed_hosts`. Wird ein Limit
überschritten: Task geht in `WAITING_APPROVAL` statt `ACTING`.

### 8.5 Sensor- & Action-Systeme

**Sensor-Systems** (`core/sensor_systems.py`, 11 Module): pollen Umgebung
(Filesystem, Memory, Time, LLM-Status, User-Presence, etc.) und produzieren
Events für den State-Machine-Input.

**Action-Systems** (`core/action_systems.py`, 61 KB, 7 Module): exekutieren
Aktionen — LLM-Call, File-Write, Shell-Command, Bridge-Call, DB-Query,
Notification, sub-agent spawn. Jede Action geht durch den Safety-Governor.

---

## 9. Hierarchical Routing — 3-Layer-System

Das Brain routet Anfragen nicht in einem Schritt, sondern in **drei
hierarchischen Ebenen** — jede Ebene verfeinert die vorherige.

### 9.1 Layer 1: TaskFeatureRouter

**Datei:** `core/task_feature_router.py` (529 Zeilen).

**Aufgabe:** Rohe Anfrage → strukturierter `RoutingState`.

**Input:** Text/Event/Voice-Transkript.
**Verarbeitung:**
1. Keyword-Extraktion
2. Task-Type-Klassifikation (z. B. `code_generation`, `question`, `action`,
   `scheduling`)
3. Complexity-Estimate (aus Länge + Vocabulary-Komplexität)
4. Urgency-Heuristik (z. B. Wörter wie "jetzt", "sofort")

**Output:**
```python
@dataclass
class RoutingState:
    weights: Tensor              # 10-Dim Soft-Routing
    task_type: str
    complexity: float            # 0..1
    urgency: float               # 0..1
    processing_mode: str         # "fast" | "deep"
```

### 9.2 Layer 2: ConversationPathPlanner

**Datei:** `core/conversation_path_planner.py` (577 Zeilen).

**Aufgabe:** `RoutingState` + Konversations-Historie → konkreter Pfad.

**Verarbeitung:** Baut einen gerichteten Graph aus möglichen Konversations-
Zuständen auf. Nutzt `conversation_history` (Portion 3) für Priors.

**Output:**
```python
@dataclass
class ConversationPath:
    nodes: list[PathNode]         # Sequenz von States
    predicted_sequence: list[str] # erwartete Actions
    confidence: float
```

### 9.3 Layer 3: DecisionRouter

**Datei:** `core/decision_router.py` (683 Zeilen).

**Aufgabe:** Konkrete 10×4-Routing-Matrix.

**Verarbeitung:** Nimmt ConversationPath + Memory + Attention-State. Berechnet
eine **10×4-Matrix** (10 Routing-Ziele × 4 downstream-Kategorien: LLM-Call,
Space-Agent, Tool-Use, Meta-Response).

**Output:**
```python
@dataclass
class Decision:
    brain_gates: Tensor(10)      # sum = 1.0 (Softmax)
    category_weights: Tensor(4)  # sum = 1.0
    actionable_decision: dict    # konkrete Action-Spec
```

### 9.4 HierarchicalPlanner — Integration

**Datei:** `core/hierarchical_planner.py`.

Verkettet die drei Layer, sammelt Feedback-Signale (z. B. Erfolg einer Action)
und propagiert sie zurück zum Training der Router-Gewichte. Dient auch als
Einzige-Schnittstelle, die die Bridge (Port 5100, Portion 5) aufruft:

```
POST /api/cortex/route
    { "text": "...", "context": {...} }
    → HierarchicalPlanner.plan()
    → {brain_gates, confidence, actionable_decision, ...}
```

---

## 10. Production Planner, Heartbeat & Dream-Cycle

### 10.1 ProductionPlanner

**Datei:** `production/production_planner.py` (2.587 Zeilen).

Der `ProductionPlanner` ist der **oberste Orchestrator** im Produktion-Modus.
Er besitzt (nicht nur nutzt) die wichtigsten Komponenten:

```python
class ProductionPlanner:
    agent_loop: AgentLoop                    # V2 FSM
    hierarchical_planner: HierarchicalPlanner # 3-Layer-Routing
    continuous_learning: ContinuousLearning   # Online-Matrix-Updates
    semantic_coherence: SemanticEncoder       # Coherence-Validation
```

**Konstruktor-Parameter:**

| Parameter                         | Default           | Zweck                                |
|-----------------------------------|-------------------|--------------------------------------|
| `session_log_dir`                 | `./logs/sessions` | Training-Logs                        |
| `matrix_dir`                      | `./matrices`      | Persistierte Routing-Matrizen        |
| `feedback_dir`                    | `./feedback`      | User-Feedback                        |
| `enable_continuous_learning`      | `True`            | Real-time Matrix-Updates (LR=0.005)  |
| `enable_semantic_coherence`       | `True`            | K_min=0.55, green_threshold=0.75     |

**`continuous_learning=True` bedeutet:** Jede erfolgreiche Routing-Entscheidung
aktualisiert die 10×4-Matrix im DecisionRouter sofort (ohne Backprop, via
Delta-Rule). Das ist parallel zum Hebbian-Lernen im Ring-Netzwerk.

### 10.2 Brain Heartbeat

**Datei:** `production/brain_heartbeat.py` (652 Zeilen).

Der Heartbeat tickt **alle 30 Sekunden** unabhängig vom Agent Loop und
kümmert sich um "Haushaltsaufgaben":

1. **Dream-Mode-Trigger** prüfen (Idle-Threshold erreicht?)
2. **Temporal-Updates** — zirkadiane Rhythmen, Uhrzeit-Simulation
3. **Neuromodulation-Decay** — alle Bridge-Skalare driften ohne Updates
   langsam zum Resting-Level
4. **Meta-Learning-Checks** — Cross-Validation von Routing-Entscheidungen
5. **Health-Monitoring** — Health-Status aller 10 Bridges
6. **Consolidation-Cycles** — 30-Sekunden-Memory-Consolidation (nicht das
   größere Dream-Consolidation!)

### 10.3 Dream-Mode

**Datei:** `core/dream_mode.py` (539 Zeilen).

Getriggert, wenn:
- Idle-Zeit > 300 s **ODER**
- `sleep_pressure > 0.8` (steigt um 0.001 pro Tick)

**Was passiert im Dream (4 Teilprozesse):**

#### a) Experience Replay
Aus dem `experience_buffer` (max 5.000 Experiences, FIFO) werden Batches
gesampled. Jedes Replay läuft einmal durch den Ring-Stack.

**Config:**
```yaml
replay_rate: 0.3            # P(replay) pro Dream-Zyklus
max_dreams_per_cycle: 5     # max Episoden pro Dream
```

#### b) Counterfactual-Learning
Der Planner generiert "Was-wäre-wenn"-Varianten vergangener Entscheidungen
(z. B. "was wäre passiert, wenn ich anders geroutet hätte?"). Die Deltas
speisen die Matrix-Updates.

**Config:** `counterfactual_rate: 0.2`.

#### c) Pattern-Extraction
Wiederkehrende Task-Decision-Muster werden erkannt (z. B. "bei Code-Requests
ab 20:00 Uhr wird oft DeepSeek statt GPT-4o gewählt"). Solche Patterns werden
als **Prior** in den `TaskFeatureRouter` eingebettet.

**Config:** `pattern_min_support: 3` (mindestens 3 Experiences für Pattern).

#### d) Memory-Consolidation (4-Loss-Backprop!)
**Datei:** `core/radial_sleep_trainer.py` (202 Zeilen).

Der einzige Ort, an dem Gradient-Descent auf dem Ring-Netzwerk läuft. Die
**4 Losses** sind:

| Loss              | Was er optimiert                                 |
|-------------------|--------------------------------------------------|
| reconstruction    | Ring-Output → Original-Input-Rekonstruktion      |
| prediction        | Top-Down-Prediction ≈ Bottom-Up-Activity        |
| routing           | brain_gates ≈ tatsächlich erfolgreich genutzte Ziele |
| contrastive       | Ähnliche Inputs → ähnliche Ring-Activations      |

**Config:** `consolidation_threshold: 0.7` — nur Experiences mit
Importance > 0.7 fließen ins Gradient-Update.

### 10.4 Homöostatische Regulation

Das Brain hat **künstliche Energie/Fatigue/Sleep-Variablen**, die über die
Zeit ansteigen/abfallen:

```yaml
energy_per_task:          0.02
energy_recovery_rate:     0.01    # im Idle
energy_rest_recovery:     0.3     # im Dream
fatigue_per_task:         0.015
fatigue_decay_rate:       0.005
sleep_accumulation_rate:  0.001   # pro Tick
sleep_threshold:          0.8     # > 0.8 erzwingt Dream
```

**Effekt:** Wenn zu viele Tasks ohne Dream-Pause laufen, steigt
`sleep_pressure`. Ab 0.8 wird das System *zwanghaft* in den Dream geschickt —
unabhängig von der User-Queue. Das verhindert katastrophales Vergessen und
hält das Online-Lernen stabil.

---

## 11. 43 Neuroscience-Module

Jedes Modul ist ein eigenständiges Python-File in `core/`, implementiert die
Funktion eines **konkreten anatomischen Hirnareals** und speist mindestens
eine der 10 Bridges. Die Bezeichnungen folgen der medizinischen Terminologie.

Ich gruppiere sie nach Funktion:

### 11.1 Neuromodulatorische Kerne (Tier 1 — klassische "brainstem nuclei")

| Modul                          | Areal                          | Signal-Output              |
|--------------------------------|--------------------------------|----------------------------|
| `ventral_tegmental_area.py`    | VTA                            | Dopamin (DA)               |
| `locus_coeruleus.py`           | LC                             | Noradrenalin (NE), Arousal |
| `raphe_nuclei.py`              | Raphe                          | Serotonin (5-HT), Mood     |
| `basal_forebrain.py` (13,8 KB) | BF                             | Acetylcholin (ACh)         |
| `lateral_habenula.py`          | LHb                            | Anti-Reward, Enttäuschung  |
| `substantia_nigra.py`          | SN                             | Motor-Gating               |
| `periaqueductal_gray.py`       | PAG                            | Defense-Circuit            |
| `reticular_formation.py`       | RF                             | Arousal, Wach/Schlaf       |
| `nucleus_tractus_solitarius.py`| NTS                            | Autonome Integration       |

### 11.2 Limbisches System (Emotion, Gedächtnis, Threat)

| Modul                              | Areal                       | Funktion                        |
|------------------------------------|-----------------------------|---------------------------------|
| `amygdala_complex.py` (21 KB)      | Amygdala                    | Threat-Detection, Valence       |
| `hippocampus.py`                   | Hippocampus                 | Episodic Memory                 |
| `entorhinal_cortex.py`             | Entorhinal Cortex           | Grid-Cells, Spatial-Coding      |
| `septal_nuclei.py`                 | Septum                      | Theta-Generation, Reward        |
| `mammillary_bodies.py`             | Mammilarkörper              | Memory-Consolidation            |
| `bed_nucleus_stria_terminalis.py`  | BNST                        | Threat/Uncertainty-Integration  |
| `hypothalamus_drives.py`           | Hypothalamus                | Homeostatic-Drives              |
| `ventral_pallidum.py`              | VP                          | Hedonic-Valence                 |

### 11.3 Cortex-Areale (Executive, Perception, Self-Model)

| Modul                         | Areal                      | Funktion                            |
|-------------------------------|----------------------------|-------------------------------------|
| `prefrontal_cortex.py`        | PFC                        | Executive-Control, Working-Memory   |
| `anterior_cingulate.py`       | ACC                        | Conflict-Monitoring, Error-Detection|
| `orbitofrontal_cortex.py`     | OFC                        | Value-Representation, Reversal      |
| `insular_cortex.py`           | Insula                     | Interoception, Feelings             |
| `posterior_parietal_cortex.py`| PPC                        | Spatial Attention                   |
| `temporoparietal_junction.py` | TPJ                        | Theory-of-Mind                      |
| `fusiform_gyrus.py`           | Fusiform Gyrus             | Face/Object-Recognition             |
| `cortical_column.py`          | Canonical Cortical Column  | 6-Layer-Micro-Circuit               |
| `default_mode_network.py`     | DMN                        | Self-Referential-Thought            |

### 11.4 Motor & Timing

| Modul                    | Areal                 | Funktion                              |
|--------------------------|-----------------------|---------------------------------------|
| `cerebellum_module.py` (34 KB) | Cerebellum     | Forward-Models, Motor-Timing          |
| `inferior_olive.py`      | IO                    | Oszillation, Error-Teaching (Cereb.)  |
| `zona_incerta.py`        | ZI                    | Motor-Inhibition                      |
| `red_nucleus.py`         | RN                    | Motor-Compensation                    |
| `pedunculopontine_nucleus.py` | PPN              | Cholinerge Arousal                    |
| `superior_colliculus.py` | SC                    | Orienting-Response                    |

### 11.5 Sensorik & Körper

| Modul                     | Areal                   | Funktion                          |
|---------------------------|-------------------------|-----------------------------------|
| `olfactory_system.py`     | Olfactory Bulb          | Geruch/Pheromone                  |
| `parabrachial_nucleus.py` | PBN                     | Pain/Temp/Visceral                |
| `tuberomammillary_nucleus.py` | TMN                 | Histamin, Sleep/Wake              |

### 11.6 Bewusstsein & Integration

| Modul                  | Areal              | Funktion                               |
|------------------------|--------------------|----------------------------------------|
| `claustrum.py` (22 KB) | Claustrum          | Cross-Modal-Binding (Bewusstsein)      |
| `corpus_callosum.py`   | Corpus Callosum    | Interhemisphärische Integration        |
| `pineal_gland.py`      | Pineal             | Melatonin, Circadian                   |

**(+ weitere ~11 kleinere Module, z. B. thalamische Kerne im Detail,
Basalganglien-Sub-Module.)**

### 11.7 Multi-CTM-Ensemble (4 Continuous-Thought-Domänen)

**Datei:** `core/multi_ctm_ensemble.py` + 5 Domänen-Dateien.

Die CTMs sind **neuronale Sub-Agents**, die bei bestimmten Task-Typen parallel
zum Haupt-Ring-Stack aufgerufen werden:

| CTM                          | Domäne                        | Wann aktiv                      |
|------------------------------|-------------------------------|---------------------------------|
| `klotski_ctm.py`             | Puzzle-artiges Reasoning      | Task mit räumlicher Kombinatorik|
| `temporal_ctm.py`            | Sequenzielle Reasoning        | Zeitreihen, Ursache-Wirkung     |
| `domain_transformer_ctm.py`  | Domain-Transfer               | Cross-Domain-Analogien          |
| `hierarchical_ctm.py`        | Hierarchische Dekomposition   | Komplexe Planung                |

Der `ctm_domain_router.py` (17,8 KB) entscheidet in Phase 3 (ATTEND), welcher
CTM aktiviert wird — oder keiner.

---

## 12. Config-System — `configs/default.yaml`

**Datei:** `brain/the_brain/configs/default.yaml` (1.082 Zeilen).

Alles am Brain ist konfigurierbar — Dimensionen, Learning-Rates, Bridge-
Parameter, Safety-Limits. Die Struktur (in 10 Top-Level-Blöcken):

### 12.1 Modalitäten (Block 1)

6 Sensor-Modalitäten + 4 Konversations-Traces, Dimensionen:

```yaml
modalities:
  vision:       { dim: 128 }
  audio:        { dim:  64 }
  touch:        { dim:  32 }
  taste:        { dim:  16 }
  vestibular:   { dim:  16 }
  threat:       { dim:   8 }
  conversation_semantic: { dim: 256 }
  conversation_emotion:  { dim:  64 }
  conversation_intent:   { dim:  32 }
  conversation_context:  { dim: 128 }
```

### 12.2 Radial Attention Network (Block 2)

```yaml
radial_network:
  seed_dim:      384      # Input-Encoder-Output
  thalamic_dim:  128      # Meta-Ring
  rings:
    - { name: sensory,  dim:  64, heads: 4 }
    - { name: pattern,  dim: 128, heads: 4 }
    - { name: semantic, dim: 256, heads: 4 }
    - { name: abstract, dim: 256, heads: 4 }
    - { name: meta,     dim: 128, heads: 4 }
  hebbian_learning_rate: 0.001
  hebbian_decay:         0.0001
  ewc_lambda:            100.0
  experience_buffer_capacity: 5000
```

### 12.3 Cognitive Loop (Block 3)

```yaml
cognitive_loop:
  enabled: true
  memory_routing_bias_strength: 0.25
  attention_gating_strength:    0.5
  ctm_timeout_seconds: 30
  phase6_enables:
    curiosity:      true
    consciousness:  true
    reconsider:     true
    # ... 11 weitere Flags
```

### 12.4 Alle 10 Bridge-Configs (Block 4)

Jede Bridge hat `enabled`, ihre eigenen Decay-Raten und Clip-Bounds.
Auszug:

```yaml
neuromodulation_bridge:
  enabled:     true
  da_baseline: 0.5
  ne_baseline: 0.3
  # ...
cortex_bridge:
  enabled: true
  pfc_learning_rate: 0.01
  acc_sensitivity:   1.2
# ... 8 weitere Bridges
```

### 12.5 Agent Loop V2 (Block 5)

```yaml
agent_loop:
  active_tick_interval:  1.0
  idle_tick_interval:    30
  dream_tick_interval:   60
  dream_duration:        120
  idle_threshold:        300
  max_pending_tasks:     50
  max_concurrent:         1
  max_autonomous_actions_per_hour: 50
```

### 12.6 Motivation-Drives (Block 6)

```yaml
drives:
  curiosity:        { gain: 0.6, decay: 0.01 }
  competence:       { zpd_window: [0.4, 0.8] }
  homeostatic:
    sleep_target:   0.5
    dopamine_target:0.5
    stress_target:  0.3
    energy_target:  0.7
    fatigue_target: 0.3
```

### 12.7 Safety-Regulation (Block 7)

```yaml
safety:
  budgets:
    shell_command:      { limit: 50,    period: hour }
    file_write:         { limit: 10,    period: hour }
    coding_job:         { limit: 5,     period: day }
    llm_token:          { limit: 1000,  period: minute }
    network_call:       { limit: 30,    period: hour }
  allowed_paths: ["./workspace", "./logs", "./feedback"]
  allowed_hosts: ["api.openai.com", "api.anthropic.com", ...]
```

### 12.8 Phase 5–7 Systeme (Block 8)

Schaltet höhere kognitive Funktionen an/aus:

```yaml
phase5:
  theory_of_mind:        true
  causal_reasoning:      true
  intrinsic_curiosity:   true
phase6:
  self_improvement:      true
  autonomous_goals:      true
  multimodal_fusion:     true
phase7:
  sensorimotor:          true
  formal_verifier:       false   # experimentell
  thought_decoder:       true
```

### 12.9 43 Neuroscience-Module (Block 9)

Jedes der 43 Module hat einen eigenen YAML-Block mit Baseline,
Learning-Rates, Thresholds, Dimensionen. Insgesamt ca. 600 Zeilen nur für
diesen Block. Beispiel:

```yaml
amygdala:
  enabled: true
  threat_sensitivity: 1.2
  valence_decay:      0.05
  fear_learning_rate: 0.02
  ...
```

### 12.10 Service-Ports (Block 10)

```yaml
services:
  unified_brain: 5003
  dashboard:     5004
  swarm:         5002
  api:           5001
  memory:        8001
```

---

## 13. Web-Server, Router & Streams

**Datei:** `web/brain_server.py` (996 Zeilen).

Der FastAPI-Server ist die **Tür nach außen**. Er lädt beim Boot faul alle
Brain-Komponenten, hängt sie in `app.state` und verteilt sie auf Router.

### 13.1 Lifespan & Middleware

```python
app = FastAPI(
    title="The Brain — Nervous System",
    lifespan=_lifespan,   # Startup: init Brain, Shutdown: save state
)
app.add_middleware(CORSMiddleware, allow_origins=["*"])
app.add_middleware(ThalamicGateMiddleware)   # thalamisches Gating aller Requests
```

Die `ThalamicGateMiddleware` ist **real wirksam**: jeder eingehende Request
wird durch den `ThalamicAdapter` gefiltert, bevor er zum Router kommt. Bei
hoher `defense_intensity` (DefenseBridge) kann sie Requests verzögern oder
rejecten.

### 13.2 Die 11 Router

| Router-Datei                            | Prefix                | Zweck                                      |
|-----------------------------------------|-----------------------|--------------------------------------------|
| `routers/training.py`                   | `/api/training`       | Training-Jobs, Matrix-Checkpoints          |
| `routers/oscillator.py` (1.123 Z.)      | `/api/oscillator`     | Introspection des Oszillators             |
| `routers/swarm.py`                      | `/api/swarm`          | Multi-Agent-Swarm-Steuerung                |
| `routers/introspection.py` (1.123 Z.)   | `/api/introspection`  | Voller Brain-State-Dump                    |
| `routers/knowledge.py` (18,8 KB)        | `/api/knowledge`      | Moltbook-Integration                       |
| `routers/cortex.py` (1.174 Z.)          | `/api/cortex`         | **Haupt-Routing-Endpoint für Bridge**      |
| `routers/radial.py` (19,5 KB)           | `/api/radial`         | Ring/Bridge-SSE-Stream (2 Hz)              |
| `routers/routing.py` (6,2 KB)           | `/api/routing`        | Direkt-Routing (bypassed Cortex-Layer)     |
| `routers/classification.py` (6,2 KB)    | `/api/classification` | Task-Type-Klassifikation                   |
| `streams/consciousness.py`              | `/api/consciousness`  | SSE Consciousness-Metriken                 |
| `streams/chat.py` (379 Z.)              | `/api/chat/ws`        | **WebSocket-Chat** mit Brain+LLM           |

### 13.3 Der wichtigste Router: `cortex.py`

Dieser Router implementiert den Endpoint, den die **Bridge** (Portion 5) ruft:

```http
POST /api/cortex/route
Content-Type: application/json

{
  "text": "Erstelle ein Video-Projekt fuer Sabrina",
  "context": { "session_id": "...", "user_id": "..." },
  "space_hint": null
}
```

**Response:**
```json
{
  "brain_gates": [0.02, 0.04, 0.74, 0.05, ...],
  "confidence": 0.83,
  "actionable_decision": {
    "target_space": "video",
    "action": "create_project",
    "params": { "person": "Sabrina" }
  },
  "phase_timings": { "perceive_ms": 12, ... }
}
```

Das `brain_gates`-Array summiert zu 1.0 (Invariant). Die Bridge picked das
Top-1-Target und routet an den entsprechenden Space-Agent.

### 13.4 WebSocket-Chat (`streams/chat.py`)

- **Endpoint:** `ws://localhost:5000/api/chat/ws`
- **Protokoll:** JSON-Messages `{type: "user"|"brain"|"state", content: "..."}`
- **Backend:** `BrainChat` + `MultiLLMRouter` + Brain-State-Coloring
- **Features:**
  - Semantische Thought-Matching (aus dem Moltbook-Store)
  - Brain-State-Overlay (Rings, Bridges, Gates in JSON mitgesendet)
  - Emotional Coloring (LimbicBridge → Response-Tonalität)

### 13.5 SSE-Stream `radial.py` (2 Hz)

Stream-Format:
```
event: ring_activations
data: {"ring_0": [...], "ring_1": [...], ..., "ring_4": [...]}

event: bridge_states
data: {"neuromod": {...}, "cortex": {...}, ...}

event: modulation_factors
data: {"attention_gain": 1.2, "precision_boost": 0.9, ...}
```

Das `radial_dashboard.html` abonniert diesen Stream und rendert live die
5 Ringe + 10 Bridges.

---

## 14. Die 9 HTML-Dashboards

**Ordner:** `web/templates/` (insgesamt 9.881 Zeilen HTML/JS).

| Dashboard                              | Zeilen | Inhalt                                                |
|----------------------------------------|-------:|-------------------------------------------------------|
| `brain_dashboard.html`                 | 2.179  | Unified-Hauptansicht: Gates, Goals, CTM, Chat          |
| `moltbook_dashboard.html`              | 1.639  | Knowledge-Graph, Thought-Stream, Retrieval-Ranking    |
| `unified_brain_dashboard.html`         | 1.172  | **SVG-Ringe & Bridges, Thought-Flow, Modulation**      |
| `klotski_dashboard.html`               |   993  | CTM-Puzzle-Visualisierung                             |
| `klotski_3d_rings.html`                |   852  | 3D-Ring-View für Klotski                              |
| `oscillator_dashboard.html`            |   815  | Temporal-Oszillator-State, Frequency-Control          |
| `evolutionary_training_dashboard.html` |   670  | CTM-Training, Generationen, Fitness                   |
| `autonomous_swarm.html`                |   459  | Swarm: 14 Micro-Agents, Task-Execution                |
| `cognitive_loop_viz.html`              |   412  | **9-Phasen-Loop-Visualisierung, Phase-Timings**        |

**SVG-Rendering:** `unified_brain_dashboard.html` ist das visuell aufwendigste
— es rendert die 5 konzentrischen Ringe als SVG, animiert die aktuelle
Modulation als Farb-/Größen-Pulse, und zeigt Bridge-Aktivitäten als
Verbindungs-Linien zwischen den Ringen.

**Echtzeit-Updates:** Alle Dashboards nutzen SSE (2 Hz) oder WebSocket.
Kein Polling.

---

## 15. System-Invariants

Das Brain hat **harte Invariants**, die in Tests durchgesetzt werden. Bricht
eines, ist das ein Zeichen für einen Bug — nie für "new normal".

### 15.1 Brain-Gates summieren zu 1.0

```python
assert abs(sum(brain_gates) - 1.0) < 1e-5
```

**Warum:** Die Gates sind eine Softmax-Wahrscheinlichkeits-Verteilung über
10 Routing-Ziele. Summiert es nicht zu 1.0, ist entweder der Softmax kaputt,
oder jemand hat manuell einzelne Gewichte skaliert.

**Test:** `tests/test_core.py::test_brain_gates_sum_to_one`

### 15.2 Modulation-Faktoren clampen zu [0.3, 3.0]

```python
for factor in modulation_factors.values():
    assert 0.3 <= factor <= 3.0
```

**Warum:** Siehe Abschnitt 6.5. Untergrenze verhindert stille Deaktivierung,
Obergrenze verhindert Runaway.

### 15.3 Ring-Dimensionen sind fix (64, 128, 256, 256, 128)

Diese Zahlen sind im Code mehrfach referenziert (Dim-Projections, Attention-
Heads, FFN-Sizes). Änderung erfordert Schema-Migration und Retraining.

### 15.4 FSM-Transitionen strikt

```python
if new_state not in VALID_TRANSITIONS[old_state]:
    raise InvalidStateTransitionError(old_state, new_state)
```

### 15.5 Bridge-Tick-Verzögerung

Bridges updaten auf Tick `n`, werden im Forward auf Tick `n+1` verwendet —
nie auf Tick `n` selbst. Das ist **architektonisch** und **nicht optional**.
Simultanes Update + Use würde Feedback-Loops und Oszillationen erzeugen.

### 15.6 Hebbian läuft *immer*, Gradient nur im Dream

Wenn `torch.autograd.enabled` im normalen Forward aktiv ist, ist das ein Bug.
Der `RadialSleepTrainer` ist der einzige Ort mit Gradient-Descent.

### 15.7 Safety-Budget-Exhaustion → WAITING_APPROVAL

Wenn ein Budget-Limit erreicht ist, **darf** der Agent nicht weiter
exekutieren — er muss in `WAITING_APPROVAL`. Das ist kein Graceful-Fallback,
sondern eine harte Grenze.

---

## 16. Testing & Quality

**Ordner:** `brain/the_brain/tests/` — **206 Test-Dateien, 1.981+ Tests**.

### 16.1 Test-Gliederung

```bash
pytest tests/ -v                          # Full-Suite
pytest tests/test_core.py -v              # Routing + Gates-Invariants
pytest tests/test_phase7_modules.py -v    # 43 Neuroscience-Module
pytest tests/test_phase_d_modules.py -v   # Tier 1 Brain Structures
pytest tests/test_phase_e_modules.py -v   # Tier 2 Brain Structures
pytest tests/test_phase_f_modules.py -v   # Tier 3 Brain Structures
pytest tests/test_cognitive_loop.py -v    # 9-Phasen-Loop
pytest tests/test_agent_loop_v2.py -v     # FSM-Transitionen
pytest tests/test_bridges.py -v           # 10 Bridges
pytest tests/test_dream_mode.py -v        # Replay + 4-Loss-Training
```

### 16.2 Kritische Test-Gruppen

- **Invariant-Tests:** Summe=1, Clamp-Bounds, FSM-Transitionen
- **Integration-Tests:** Voller Cognitive-Loop-Durchlauf
- **Property-Tests:** Hypothesis-basiert für Bridges (arbitrary inputs)
- **Performance-Tests:** Einzelner Forward-Pass < 50 ms auf CPU
- **Memory-Leak-Tests:** 10.000-Tick-Runs mit RSS-Check

### 16.3 CI-Relevanz

Die Full-Suite läuft nicht in jedem CI-Lauf (zu teuer). Stattdessen:
- PR-Gate: `test_core.py` + `test_bridges.py` + `test_cognitive_loop.py`
- Nightly: Full-Suite inkl. Phase D/E/F

---

## 17. Bezug zu anderen Portionen

### 17.1 Portion 2 (LLM-Config)

Das Brain nutzt `llm_config.yml` **indirekt** über `MultiLLMRouter`. Die
Rollen aus Portion 2 mappen auf Brain-Routing-Targets (z. B. `think`,
`report`, `judge`). Der `brain_chat.py` ruft `vibemind_shared.get_client(
role)` für jeden LLM-Call.

### 17.2 Portion 3 (Datenbank)

Das Brain schreibt in:
- `conversation_sessions` + `conversation_history` — jede Chat-Session
- `persistent_tasks` — Agent-Loop-Tasks (P0–P3)
- `scheduled_tasks` — alles, was der Heartbeat persistent schedulen will
- `ideas` (indirekt, via Bubbles aus Rowboat-Ingestion)

Das Brain **liest** aus:
- `ideas` + `conversation_history` für REMEMBER-Phase
- `user_preferences` für Personality-Anpassung

### 17.3 Portion 5 (Bridge & OpenFang) — kommt als Nächstes

Der Endpoint `POST /api/cortex/route` (Abschnitt 13.3) ist der Kontrakt
zwischen Brain und Bridge. Die Bridge nimmt die `brain_gates` und routet an
den Space-Agent mit dem höchsten Gewicht.

### 17.4 Portion 1 Section 13 Roadmap

Das Brain ist **nicht** Teil der Space-MCP-Migration. Aber: wenn Space-MCPs
live ihre Capabilities veröffentlichen (via `tools/list`), kann der
**DecisionRouter** diese dynamisch in seine Matrix einbauen — heute sind die
Routing-Ziele statisch (10 Ziele im Code).

**Folge-TODO (noch nicht getaggt):** `TODO(brain-dynamic-routing-targets)` —
wenn Space-MCPs laufen, DecisionRouter so umbauen, dass er Ziele aus dem
MCP-Registry liest, nicht aus hartcodiertem Array.

---

## 18. Zusammenfassung

| Komponente                     | Kennzahl                                          |
|--------------------------------|---------------------------------------------------|
| Core-Module                    | 268 Python-Dateien (~1,8 MB)                      |
| Neuroscience-Module            | 43 (Tier 1 + Limbic + Cortex + Motor + Sensorik)  |
| Neuromodulation-Bridges        | 10 (mit 22 benannten Signal-Skalaren)             |
| Radial-Ringe                   | 5 (Dimensionen 64 → 128 → 256 → 256 → 128)        |
| Cognitive-Loop-Phasen          | 9 (PERCEIVE → REMEMBER → ... → CONSOLIDATE)       |
| Agent-Loop-FSM-States          | 8 (IDLE, PERCEIVING, THINKING, ACTING, WAITING_APPROVAL, OBSERVING, LEARNING, DREAMING) |
| Task-Prioritäts-Stufen         | 4 (P0 User → P3 Background)                       |
| Hierarchical-Routing-Layer     | 3 (TaskFeatureRouter → PathPlanner → DecisionRouter) |
| Modulation-Faktoren            | 4 (attention_gain, precision_boost, ffn_throughput, threshold_mod) |
| Web-Router                     | 11 + 2 Streams                                    |
| Dashboards                     | 9 HTML-Views (9.881 Zeilen)                       |
| Tests                          | 1.981+ in 206 Dateien                             |
| Default-Config-Zeilen          | 1.082                                             |
| CTM-Domänen                    | 4 (Klotski, Temporal, Domain-Transfer, Hierarchical)|

**Nächste Portion:** *Portion 5 — Bridge & OpenFang.* Dort wird der Kontrakt
`POST /api/cortex/route` aus Abschnitt 13.3 auf der Gegenseite aufgenommen:
die Bridge picked das Top-Gate, mapped auf einen Space-Agent und ruft
OpenFang (Rust, Port 50051) auf.
