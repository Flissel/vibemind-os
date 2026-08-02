"""
Pitch Deck Generator Agent v5 - Autonom
=========================================
Generiert VC-ready Pitch Decks autonom mit Recherche + Charts.

Du gibst nur Firmenname + Beschreibung. Der Agent recherchiert alles selbst:
Markt, Wettbewerber, Business Model, Wachstumsprognosen.

Pipeline: Analyzer -> Researcher (3 LLM-Calls) -> Content -> ChartGenerator -> SlideBuilder

Nutzung:
  python pitch_deck_agent.py                                    # Interview
  python pitch_deck_agent.py "VibeMind" "AI-Agent Plattform"    # Quick
  python pitch_deck_agent.py "VibeMind" "Beschreibung" --theme=emerald

Output: vibemind_pitch_deck.pptx
"""

import asyncio
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from pitchdeck_rag import PitchdeckRAG

from autogen_core import (
    AgentId,
    MessageContext,
    SingleThreadedAgentRuntime,
    RoutedAgent,
    message_handler,
)
from vibemind_shared import OpenFangUnavailable, get_client_sync, get_model


PITCH_DECK_ROLE = "agent_pitch_deck"
IMAGE_UNAVAILABLE_MESSAGE = (
    "OpenFang gateway does not support AI image generation for pitch decks"
)


# =====================================================================
# Messages
# =====================================================================

THEME_FIELDS = [
    "theme_name", "primary", "secondary", "accent",
    "bg_dark", "bg_light", "text_light", "text_dark",
    "industry", "tone", "images",
]

@dataclass
class PitchDeckRequest:
    company_name: str
    description: str
    theme: str = "auto"
    images: bool = False

@dataclass
class EnrichedBriefing:
    company_name: str
    description: str  # Angereichertes Briefing aus RAG
    theme: str = "auto"
    images: bool = False

@dataclass
class AnalysisResult:
    company_name: str
    description: str
    theme_name: str
    primary: str
    secondary: str
    accent: str
    bg_dark: str
    bg_light: str
    text_light: str
    text_dark: str
    industry: str
    tone: str
    images: bool = False

@dataclass
class ResearchResult:
    company_name: str
    description: str
    research: str  # JSON-String mit allen Recherche-Ergebnissen
    theme_name: str
    primary: str
    secondary: str
    accent: str
    bg_dark: str
    bg_light: str
    text_light: str
    text_dark: str
    industry: str
    tone: str
    images: bool = False

@dataclass
class ContentResult:
    company_name: str
    slides: list
    theme_name: str
    primary: str
    secondary: str
    accent: str
    bg_dark: str
    bg_light: str
    text_light: str
    text_dark: str
    industry: str
    tone: str
    images: bool = False

@dataclass
class DeckBuildRequest:
    company_name: str
    slides: list
    theme_name: str
    primary: str
    secondary: str
    accent: str
    bg_dark: str
    bg_light: str
    text_light: str
    text_dark: str
    industry: str
    tone: str
    output_path: str = ""

@dataclass
class DeckBuildResponse:
    output_path: str
    slide_count: int
    success: bool


# =====================================================================
# Themes
# =====================================================================

THEMES = {
    "midnight": {
        "primary": "#1B2A4A", "secondary": "#2E86AB", "accent": "#00D4AA",
        "bg_dark": "#0F1B2D", "bg_light": "#F5F7FA",
        "text_light": "#FFFFFF", "text_dark": "#1B2A4A",
    },
    "emerald": {
        "primary": "#064E3B", "secondary": "#059669", "accent": "#D4AF37",
        "bg_dark": "#042F2E", "bg_light": "#F0FDF4",
        "text_light": "#FFFFFF", "text_dark": "#064E3B",
    },
    "crimson": {
        "primary": "#7F1D1D", "secondary": "#DC2626", "accent": "#F59E0B",
        "bg_dark": "#450A0A", "bg_light": "#FEF2F2",
        "text_light": "#FFFFFF", "text_dark": "#7F1D1D",
    },
    "arctic": {
        "primary": "#0C4A6E", "secondary": "#0EA5E9", "accent": "#38BDF8",
        "bg_dark": "#082F49", "bg_light": "#F0F9FF",
        "text_light": "#FFFFFF", "text_dark": "#0C4A6E",
    },
    "obsidian": {
        "primary": "#18181B", "secondary": "#3F3F46", "accent": "#A78BFA",
        "bg_dark": "#09090B", "bg_light": "#FAFAFA",
        "text_light": "#FFFFFF", "text_dark": "#18181B",
    },
    "sunset": {
        "primary": "#9A3412", "secondary": "#EA580C", "accent": "#FBBF24",
        "bg_dark": "#431407", "bg_light": "#FFF7ED",
        "text_light": "#FFFFFF", "text_dark": "#9A3412",
    },
}


# =====================================================================
# PitchdeckData RAG (Embeddings + ChromaDB)
# =====================================================================

# Globale RAG-Instanz — wird einmal initialisiert und von allen Agents geteilt
_rag_instance = None

def get_rag() -> PitchdeckRAG:
    """Lazy-Init der RAG-Instanz mit automatischem Indexing."""
    global _rag_instance
    if _rag_instance is None:
        _rag_instance = PitchdeckRAG()
        _rag_instance.index()  # no-op wenn Index schon existiert
    return _rag_instance


def hex_to_rgb(h):
    h = h.lstrip("#")
    return RGBColor(int(h[:2], 16), int(h[2:4], 16), int(h[4:6], 16))


# =====================================================================
# Prompts
# =====================================================================

ANALYSIS_PROMPT = """Du bist ein Branding-Experte. Analysiere die Firma und waehle das passende Theme.

Antworte NUR mit validem JSON:
{
  "theme": "eines von: midnight, emerald, crimson, arctic, obsidian, sunset",
  "industry": "Branche in 2-3 Worten",
  "tone": "eines von: visionaer, technisch, kreativ, serioes, disruptiv"
}

Theme-Guide:
- midnight: Tech, SaaS, AI, FinTech
- emerald: GreenTech, Bio, Health, Nachhaltigkeit
- crimson: Gaming, Entertainment, Food, Lifestyle
- arctic: Cloud, Data, Security, Wissenschaft
- obsidian: Luxury, Design, Premium, Mode
- sunset: Kreativ, Marketing, Social, Education"""

RESEARCH_MARKET_PROMPT = """Du bist ein Marktanalyst. EXTRAHIERE Marktdaten aus den bereitgestellten Firmendaten.
Ergaenze NUR fehlende Zahlen (TAM/SAM/SOM) mit deinem Wissen — alles andere MUSS aus den Firmendaten kommen.

Firma: {company}
Beschreibung: {description}
Branche: {industry}

Antworte NUR mit validem JSON:
{{
  "tam": "Gesamtmarkt mit konkreter Zahl (z.B. '€85 Mrd. globaler AI-Markt 2026')",
  "sam": "Erreichbarer Markt mit Zahl (z.B. '€12 Mrd. AI-Automatisierung Europa')",
  "som": "Realistisches Year-1 Ziel (z.B. '€2 Mio. DACH-Region')",
  "market_growth": "Jaehrliche Wachstumsrate (z.B. '38% CAGR')",
  "key_trends": ["Trend 1 (aus Firmendaten)", "Trend 2", "Trend 3"],
  "target_segments": ["Segment 1 aus Firmendaten", "Segment 2", "Segment 3"],
  "regulatory_advantage": "GDPR/Datenschutz-Vorteil falls in Firmendaten erwaehnt"
}}

WICHTIG: Wenn die Firmendaten Deployment-Tiers (Local/Custom/Cloud), GDPR-Compliance oder
spezifische Zielgruppen erwaehnen, nutze DIESE — erfinde keine eigenen."""

RESEARCH_COMPETITORS_PROMPT = """Du bist ein Wettbewerbsanalyst. Basierend auf den Firmendaten:
Identifiziere echte Wettbewerber und stelle die ECHTEN Produkt-Features als Vorteile dar.

Firma: {company}
Beschreibung: {description}
Branche: {industry}

Antworte NUR mit validem JSON:
{{
  "competitors": [
    {{
      "name": "Echten Wettbewerber-Namen",
      "strength": "Hauptstaerke in 5-8 Woertern",
      "weakness": "Hauptschwaeche in 5-8 Woertern",
      "category": "direct oder indirect"
    }},
    {{...}},
    {{...}}
  ],
  "our_positioning": "Unser Vorteil — NUR basierend auf echten Features aus den Firmendaten (1 Satz)",
  "moat": "Unser Burggraben — z.B. neurowissenschaftliche AI, 3D Multiverse, Voice-First (1 Satz)",
  "key_differentiators": ["Feature 1 aus Firmendaten", "Feature 2", "Feature 3"]
}}

WICHTIG: Nenne 3-4 ECHTE Unternehmen (z.B. Notion AI, Microsoft Copilot, Jasper, Rewind.ai).
Der 'moat' und 'our_positioning' MUESSEN auf echten Features aus den Firmendaten basieren,
nicht auf generischen Phrasen."""

RESEARCH_BUSINESS_PROMPT = """Du bist ein Startup-Stratege. Erstelle ein Geschaeftsmodell basierend auf den Firmendaten.
Nutze die ECHTEN Features und Architektur als Grundlage fuer Revenue Streams und Pricing.

Firma: {company}
Beschreibung: {description}
Branche: {industry}

Antworte NUR mit validem JSON:
{{
  "revenue_streams": ["Stream 1 basierend auf echten Features", "Stream 2", "Stream 3"],
  "pricing": "Preisstrategie passend zum Produkt (z.B. Freemium Local + Enterprise Cloud)",
  "gtm_channels": ["Kanal 1 mit Strategie", "Kanal 2", "Kanal 3"],
  "unit_economics": {{
    "cac": "Geschaetzte Customer Acquisition Cost",
    "ltv": "Geschaetzter Lifetime Value",
    "payback": "Payback-Periode"
  }},
  "growth_projection": {{
    "labels": ["M1", "M2", "M3", "M4", "M5", "M6", "M7", "M8", "M9", "M10", "M11", "M12"],
    "users": [10, 25, 55, 100, 180, 300, 480, 720, 1050, 1500, 2100, 3000],
    "mrr": [0, 0, 500, 2000, 5000, 12000, 22000, 38000, 60000, 85000, 120000, 170000]
  }},
  "funding_allocation": [
    {{"category": "Produkt & Engineering", "percent": 40}},
    {{"category": "Sales & Marketing", "percent": 30}},
    {{"category": "Operations & Hiring", "percent": 15}},
    {{"category": "Reserve", "percent": 15}}
  ],
  "ask_amount": "€500K",
  "milestones_with_funding": ["Meilenstein 1 in 3 Monaten", "Meilenstein 2 in 6 Monaten", "Meilenstein 3 in 12 Monaten"]
}}

WICHTIG: Revenue Streams MUESSEN auf den echten Produkt-Features basieren (z.B. Spaces, Voice, Brain).
Pricing sollte die 3-Tier Architektur (Local/Custom/Cloud) aus den Firmendaten reflektieren."""

CONTENT_PROMPT = """You are a VC pitch deck expert. Create slides based on the research data and briefing.
The number of slides is DYNAMIC — create as many as needed to cover all important aspects.

Industry: "{industry}", Tone: "{tone}".

Research data:
{research}

Briefing:
{briefing}

Reply ONLY with valid JSON:
{{
  "slides": [
    {{"slide_type": "intro", "title": "Company Name", "subtitle": "Powerful tagline max 10 words", "bullets": [], "speaker_notes": "..."}},
    {{"slide_type": "problem", "title": "The Problem", "subtitle": "...", "bullets": ["With real data/numbers", "..."], "speaker_notes": "..."}},
    {{"slide_type": "solution", "title": "Our Solution", "subtitle": "...", "bullets": ["USP from real data", "..."], "speaker_notes": "..."}},
    {{"slide_type": "product", "title": "Feature name from data", "subtitle": "...", "bullets": ["Real detail", "..."], "speaker_notes": "..."}},
    ... CREATE MULTIPLE product slides — one per major module/space if the data supports it ...
    {{"slide_type": "how_it_works", "title": "How It Works", "subtitle": "Architecture", "bullets": ["Real step 1", "..."], "speaker_notes": "..."}},
    {{"slide_type": "competitive", "title": "Competitive Landscape", "subtitle": "...", "competitors": [...], "our_advantage": "...", "speaker_notes": "..."}},
    {{"slide_type": "market", "title": "Market Opportunity", "subtitle": "...", "tam": "...", "sam": "...", "som": "...", "speaker_notes": "..."}},
    {{"slide_type": "business_model", "title": "Business Model", "subtitle": "...", "bullets": ["..."], "speaker_notes": "..."}},
    {{"slide_type": "go_to_market", "title": "Go-to-Market", "subtitle": "...", "bullets": ["..."], "speaker_notes": "..."}},
    {{"slide_type": "traction", "title": "Traction & Forecast", "subtitle": "...", "bullets": ["..."], "growth_data": [...], "growth_labels": [...], "growth_metric": "Users", "speaker_notes": "..."}},
    {{"slide_type": "team", "title": "The Vibeminders", "subtitle": "Each one a Mini-CEO", "bullets": ["REAL names from data ONLY"], "speaker_notes": "..."}},
    {{"slide_type": "use_of_funds", "title": "Investment", "subtitle": "...", "ask_amount": "...", "allocation": [...], "milestones": ["..."], "speaker_notes": "..."}},
    {{"slide_type": "cta", "title": "Let's Build the Future", "subtitle": "Contact", "bullets": ["..."], "speaker_notes": "..."}}
  ]
}}

Rules:
- DYNAMIC slide count: minimum 12, but ADD MORE product slides if the data has multiple modules/spaces/features
- Each major product module (e.g. Brain.Space, Ideas.Space, Video.Space, Multiverse) should get its OWN slide
- Bullets per slide: use as many as needed (3-5 is ideal), no artificial limit
- ALL product features, team names, and technical details MUST come from the briefing/research — DO NOT invent
- DO NOT invent team members — use ONLY real names from the company data
- The 'product' slides MUST describe real Spaces/Modules/Agents, NOT generic features
- The 'how_it_works' slide MUST describe the real architecture (e.g. Voice->Intent->Agent->Space)
- Language: English, Tone: {tone}"""


# =====================================================================
# Agent 0: Briefing Generator (RAG → reiches Briefing)
# =====================================================================

BRIEFING_PROMPT = """Du bist ein Startup-Pitch-Experte. Erstelle ein umfassendes Briefing aus den Firmendaten.

Du bekommst ECHTE Produktdaten, Team-Infos und Vision. Fasse diese zu einem strukturierten Briefing zusammen.
Erfinde NICHTS — extrahiere nur aus den bereitgestellten Daten.

Antworte NUR mit validem JSON:
{{
  "company_name": "Firmenname",
  "tagline": "Kraftvolle Tagline in max 10 Woertern",
  "oneliner": "Was die Firma macht in einem Satz",
  "problem": "Welches Problem wird geloest (aus den Daten extrahiert)",
  "solution": "Wie die Loesung funktioniert (echte Features nennen)",
  "key_features": ["Feature 1 mit konkretem Detail", "Feature 2", "Feature 3", "Feature 4", "Feature 5"],
  "team_members": [
    {{"name": "Name", "role": "Rolle", "description": "Kurzbeschreibung"}},
  ],
  "team_philosophy": "Team-Philosophie in einem Satz",
  "tech_stack": ["Technologie 1", "Technologie 2", "Technologie 3"],
  "current_status": ["Was schon funktioniert 1", "Status 2", "Status 3"],
  "vision": "Langfristige Vision in 1-2 Saetzen",
  "ethical_positioning": "Ethische Positionierung falls vorhanden",
  "deployment_model": "Wie wird deployed (z.B. Local/Cloud/Hybrid)"
}}"""


class BriefingAgent(RoutedAgent):
    def __init__(self):
        super().__init__("BriefingGenerator")
        self.client = get_client_sync(PITCH_DECK_ROLE)

    @message_handler
    async def generate(self, message: PitchDeckRequest, ctx: MessageContext) -> EnrichedBriefing:
        print(f"  [BRIEFING] Generiere Briefing aus PitchdeckData via RAG...")

        rag = get_rag()
        product_info = rag.get_product_info()
        team_info = rag.get_team_info()
        vision_info = rag.get_vision_info()
        market_info = rag.get_market_info()

        all_context = "\n\n".join(filter(None, [product_info, team_info, vision_info, market_info]))

        if not all_context:
            print(f"  [BRIEFING] Keine PitchdeckData — nutze User-Beschreibung")
            return EnrichedBriefing(
                company_name=message.company_name,
                description=message.description,
                theme=message.theme,
                images=message.images,
            )

        resp = self.client.chat.completions.create(
            model=get_model(PITCH_DECK_ROLE),
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": BRIEFING_PROMPT},
                {"role": "user", "content": f"Firma: {message.company_name}\n\nUser-Beschreibung: {message.description}\n\nECHTE FIRMENDATEN:\n{all_context}"},
            ],
            temperature=0.3,
        )

        briefing = json.loads(resp.choices[0].message.content)
        print(f"  [BRIEFING] Tagline: {briefing.get('tagline', '?')}")
        print(f"  [BRIEFING] Features: {len(briefing.get('key_features', []))}")
        print(f"  [BRIEFING] Team: {len(briefing.get('team_members', []))} Mitglieder")

        # Briefing als reichen Text zusammenbauen
        parts = [
            f"Tagline: {briefing.get('tagline', '')}",
            f"Einzeiler: {briefing.get('oneliner', '')}",
            f"Problem: {briefing.get('problem', '')}",
            f"Loesung: {briefing.get('solution', '')}",
            f"Key Features: {', '.join(briefing.get('key_features', []))}",
            f"Tech Stack: {', '.join(briefing.get('tech_stack', []))}",
            f"Aktueller Status: {', '.join(briefing.get('current_status', []))}",
            f"Vision: {briefing.get('vision', '')}",
            f"Ethik: {briefing.get('ethical_positioning', '')}",
            f"Deployment: {briefing.get('deployment_model', '')}",
        ]

        # Team-Mitglieder
        team = briefing.get("team_members", [])
        if team:
            team_lines = [f"  - {m.get('name', '?')}: {m.get('role', '?')} — {m.get('description', '')}" for m in team]
            parts.append(f"Team ({briefing.get('team_philosophy', '')}):\n" + "\n".join(team_lines))

        enriched_desc = "\n".join(parts)

        return EnrichedBriefing(
            company_name=message.company_name,
            description=enriched_desc,
            theme=message.theme,
            images=message.images,
        )


# =====================================================================
# Agent 1: Semantic Analyzer
# =====================================================================

class SemanticAnalyzerAgent(RoutedAgent):
    def __init__(self):
        super().__init__("SemanticAnalyzer")
        self.client = get_client_sync(PITCH_DECK_ROLE)

    @message_handler
    async def analyze(self, message: PitchDeckRequest, ctx: MessageContext) -> AnalysisResult:
        print(f"  [ANALYZE] Analysiere '{message.company_name}'...")

        # RAG: Vision/Purpose fuer bessere Theme-Analyse
        rag = get_rag()
        vision_ctx = rag.get_vision_info()
        enriched_desc = message.description
        if vision_ctx:
            enriched_desc += f"\n\nFirmendaten:\n{vision_ctx}"
            print(f"  [ANALYZE] RAG: Vision-Kontext geladen")

        if message.theme != "auto" and message.theme in THEMES:
            theme_name = message.theme
            data = {"industry": "Tech", "tone": "visionaer"}
            print(f"  [ANALYZE] Theme manuell: {theme_name}")
        else:
            resp = self.client.chat.completions.create(
                model=get_model(PITCH_DECK_ROLE),
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": ANALYSIS_PROMPT},
                    {"role": "user", "content": f"Firma: {message.company_name}\n{enriched_desc}"},
                ],
                temperature=0.3,
            )
            data = json.loads(resp.choices[0].message.content)
            theme_name = data.get("theme", "midnight")
            if theme_name not in THEMES:
                theme_name = "midnight"
            print(f"  [ANALYZE] Theme: {theme_name} | Branche: {data.get('industry')} | Ton: {data.get('tone')}")

        theme = THEMES[theme_name]
        return AnalysisResult(
            company_name=message.company_name, description=message.description,
            theme_name=theme_name, industry=data.get("industry", "Tech"),
            tone=data.get("tone", "visionaer"), images=message.images, **theme,
        )


# =====================================================================
# Agent 2: Researcher (3 parallele LLM-Calls)
# =====================================================================

class ResearcherAgent(RoutedAgent):
    def __init__(self):
        super().__init__("Researcher")
        self.client = get_client_sync(PITCH_DECK_ROLE)

    @message_handler
    async def research(self, message: AnalysisResult, ctx: MessageContext) -> ResearchResult:
        print(f"  [RESEARCH] Starte autonome Recherche...")

        # RAG: Relevante Daten per Semantic Search laden
        rag = get_rag()
        market_ctx = rag.get_market_info()
        product_ctx = rag.get_product_info()
        team_ctx = rag.get_team_info()
        print(f"    RAG geladen: market={len(market_ctx)}ch, product={len(product_ctx)}ch, team={len(team_ctx)}ch")

        ctx_vars = {
            "company": message.company_name,
            "description": message.description,
            "industry": message.industry,
        }

        DATA_PRIORITY = """

WICHTIG: Die folgenden Firmendaten sind ECHTE, VERIFIZIERTE Informationen ueber Vibemind.
Nutze diese als PRIMAERE Quelle. Erfinde KEINE Details die diesen Daten widersprechen.
Vibemind ist ein Desktop AI Assistant mit 9 Spaces (3D Multiverse), Voice Dialog (Rachel),
und neurowissenschaftlich inspirierten Agenten. Das Team arbeitet nach dem Mini-CEO Prinzip.

ECHTE FIRMENDATEN:
"""

        print(f"    Marktanalyse...")
        market_prompt = RESEARCH_MARKET_PROMPT.format(**ctx_vars)
        if market_ctx:
            market_prompt += DATA_PRIORITY + market_ctx
        market = self._call(market_prompt)

        print(f"    Wettbewerbsanalyse...")
        comp_prompt = RESEARCH_COMPETITORS_PROMPT.format(**ctx_vars)
        if product_ctx:
            comp_prompt += DATA_PRIORITY + product_ctx
        competitors = self._call(comp_prompt)

        print(f"    Business-Strategie...")
        biz_prompt = RESEARCH_BUSINESS_PROMPT.format(**ctx_vars)
        biz_context = "\n".join(filter(None, [market_ctx, product_ctx]))
        if biz_context:
            biz_prompt += DATA_PRIORITY + biz_context
        business = self._call(biz_prompt)

        # Alles zusammenfuegen inkl. echte Team-Daten
        research_data = {
            "market": market,
            "competitors": competitors,
            "business": business,
            "team_context": team_ctx,
        }

        print(f"  [RESEARCH] Recherche abgeschlossen:")
        print(f"    Markt: TAM={market.get('tam', '?')}")
        print(f"    Wettbewerber: {len(competitors.get('competitors', []))} gefunden")
        print(f"    Prognose: {len(business.get('growth_projection', {}).get('users', []))} Monate")

        return ResearchResult(
            company_name=message.company_name, description=message.description,
            research=json.dumps(research_data, ensure_ascii=False),
            **{f: getattr(message, f) for f in THEME_FIELDS},
        )

    def _call(self, prompt):
        # The OpenFang role is the only LLM path. Do not retry with a direct provider.
        try:
            resp = self.client.chat.completions.create(
                model=get_model(PITCH_DECK_ROLE),
                web_search_options={"search_context_size": "medium"},
                messages=[{"role": "user", "content": prompt + "\n\nAntworte NUR mit validem JSON."}],
                temperature=0.5,
            )
            text = resp.choices[0].message.content
            # Bereinige Markdown-Code-Bloecke falls vorhanden
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0]
            elif "```" in text:
                text = text.split("```")[1].split("```")[0]
            return json.loads(text.strip())
        except OpenFangUnavailable:
            raise
        except Exception as e:
            print(f"    [WARN] Recherche fehlgeschlagen: {e}")
            return {}


# =====================================================================
# Agent 3: Content Generator
# =====================================================================

class ContentGeneratorAgent(RoutedAgent):
    def __init__(self):
        super().__init__("ContentGenerator")
        self.client = get_client_sync(PITCH_DECK_ROLE)

    @message_handler
    async def handle(self, message: ResearchResult, ctx: MessageContext) -> ContentResult:
        print(f"  [CONTENT] Generiere Slides dynamisch basierend auf Datenmenge...")

        # RAG: Produkt + Team + Vision fuer Content-Slides
        rag = get_rag()
        product_ctx = rag.get_product_info()
        team_ctx = rag.get_team_info()
        vision_ctx = rag.get_vision_info()

        enriched_briefing = message.description
        extra_context = "\n\n".join(filter(None, [product_ctx, team_ctx, vision_ctx]))
        if extra_context:
            enriched_briefing += (
                "\n\nWICHTIG — ECHTE FIRMENDATEN (nutze NUR diese fuer Produkt, Team, und Vision!):\n"
                f"{extra_context}"
            )
            print(f"  [CONTENT] RAG-Kontext eingefuegt ({len(extra_context)} Zeichen)")

        prompt = CONTENT_PROMPT.format(
            industry=message.industry, tone=message.tone,
            research=message.research, briefing=enriched_briefing,
        )

        resp = self.client.chat.completions.create(
            model=get_model(PITCH_DECK_ROLE),
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": f"Firma: {message.company_name}"},
            ],
            temperature=0.7,
        )

        data = json.loads(resp.choices[0].message.content)
        slides = data.get("slides", [])
        print(f"  [CONTENT] {len(slides)} Slides generiert.")

        return ContentResult(
            company_name=message.company_name, slides=slides,
            **{f: getattr(message, f) for f in THEME_FIELDS},
        )


# =====================================================================
# Agent 4: Design Director (LLM entscheidet Layout pro Slide)
# =====================================================================

DESIGN_DIRECTOR_PROMPT = """Du bist ein preisgekroenter Presentation Designer. Fuer jede Slide waehlst du das beste Layout.

Verfuegbare Layouts:
- "bullets": Standard-Bullets mit Glow-Dots (fuer allgemeine Inhalte)
- "icon-bullets": Bullets mit passenden Emojis statt Dots (fuer Features, Vorteile)
- "hero-number": Eine grosse Zahl/Metrik zentriert mit Beschreibung darunter (fuer Impact-Slides)
- "split-highlight": Links grosse Zahl/Keyword, rechts Bullets (fuer Daten + Kontext)
- "timeline": Schritte mit verbindender Linie (fuer Prozesse, Roadmaps)
- "grid-cards": 2x2 oder 3x Grid von Cards (fuer Features, Team-aehnliche Inhalte)
- "chart-split": Chart links, Key-Points rechts (fuer Daten-Slides - wird automatisch bei chart_path gewaehlt)
- "team-cards": Team-Karten mit Avataren (nur fuer Team-Slide)

Regeln:
- intro und cta Slides NICHT aendern (die haben eigene Layouts)
- Slides mit chart_path werden automatisch "chart-split" - nicht aendern
- Team-Slides werden automatisch "team-cards" - nicht aendern
- Fuer alle anderen: waehle das BESTE Layout basierend auf dem Inhalt
- NICHT jede Slide gleich machen - Abwechslung ist wichtig!
- "hero-number" nur wenn es eine starke Zahl gibt
- "timeline" ideal fuer "How it works" oder Meilenstein-Slides
- "icon-bullets" passend fuer Feature-Listen
- "grid-cards" gut wenn 3-4 gleichwertige Punkte nebeneinander passen

Antworte NUR mit validem JSON:
{{
  "designs": [
    {{
      "slide_index": 0,
      "layout": "hero-center",
      "reason": "Intro slide"
    }},
    {{
      "slide_index": 1,
      "layout": "icon-bullets",
      "icons": ["\\u26a1", "\\ud83d\\udd17", "\\ud83d\\udd12"],
      "reason": "Problem-Slide: Icons machen Schmerzpunkte visuell greifbar"
    }},
    {{
      "slide_index": 5,
      "layout": "hero-number",
      "hero_value": "\\u20ac50 Mrd.",
      "hero_label": "TAM - Globaler AI-Agent Markt",
      "reason": "Markt-Slide: Grosse Zahl als Eyecatcher"
    }},
    {{
      "slide_index": 7,
      "layout": "timeline",
      "reason": "How-it-works: Schritte mit Flow"
    }},
    {{
      "slide_index": 8,
      "layout": "split-highlight",
      "highlight_value": "38%",
      "highlight_label": "CAGR",
      "reason": "Wachstums-Slide: Grosse Zahl links, Details rechts"
    }}
  ]
}}

Nur Slides auflisten die NICHT das Standard-Layout "bullets" bekommen sollen.
Intro, CTA, Chart-Slides und Team weglassen."""


class DesignDirectorAgent(RoutedAgent):
    def __init__(self):
        super().__init__("DesignDirector")
        self.client = get_client_sync(PITCH_DECK_ROLE)

    @message_handler
    async def direct(self, message: ContentResult, ctx: MessageContext) -> ContentResult:
        print(f"  [DESIGN] Design Director analysiert {len(message.slides)} Slides...")

        slides_summary = json.dumps(
            [{"i": i, "type": s.get("slide_type"), "title": s.get("title"),
              "subtitle": s.get("subtitle"), "bullets": s.get("bullets", [])[:3],
              "has_chart": bool(s.get("chart_path"))}
             for i, s in enumerate(message.slides)],
            ensure_ascii=False,
        )

        try:
            resp = self.client.chat.completions.create(
                model=get_model(PITCH_DECK_ROLE),
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": DESIGN_DIRECTOR_PROMPT},
                    {"role": "user", "content": f"Slides:\n{slides_summary}"},
                ],
                temperature=0.6,
            )
            data = json.loads(resp.choices[0].message.content)
            designs = {d["slide_index"]: d for d in data.get("designs", [])}

            applied = 0
            for idx, design in designs.items():
                if 0 <= idx < len(message.slides):
                    s = message.slides[idx]
                    st = s.get("slide_type")
                    # Nicht Intro/CTA/Team/Chart-Slides ueberschreiben
                    if st in ("intro", "cta", "team"):
                        continue
                    if s.get("chart_path"):
                        continue
                    s["layout"] = design.get("layout", "bullets")
                    if design.get("icons"):
                        s["icons"] = design["icons"]
                    if design.get("hero_value"):
                        s["hero_value"] = design["hero_value"]
                    if design.get("hero_label"):
                        s["hero_label"] = design["hero_label"]
                    if design.get("highlight_value"):
                        s["highlight_value"] = design["highlight_value"]
                    if design.get("highlight_label"):
                        s["highlight_label"] = design["highlight_label"]
                    applied += 1

            print(f"  [DESIGN] {applied} Slides mit speziellem Layout versehen.")

        except OpenFangUnavailable:
            raise
        except Exception as e:
            print(f"  [DESIGN] Fehler: {str(e)[:80]} - verwende Standard-Layouts")

        return ContentResult(
            company_name=message.company_name, slides=message.slides,
            **{f: getattr(message, f) for f in THEME_FIELDS},
        )


# =====================================================================
# Agent 5: Chart Generator (Matplotlib)
# =====================================================================

class ChartGeneratorAgent(RoutedAgent):
    def __init__(self):
        super().__init__("ChartGenerator")
        self.chart_dir = Path(__file__).parent / "deck_charts"

    @message_handler
    async def generate(self, message: ContentResult, ctx: MessageContext) -> DeckBuildRequest:
        # The pinned OpenFang gateway exposes no image-generation capability.
        if message.images:
            raise OpenFangUnavailable(IMAGE_UNAVAILABLE_MESSAGE)

        self.chart_dir.mkdir(exist_ok=True)
        print(f"  [CHARTS] Generiere Visualisierungen...")

        accent = message.accent
        primary = message.primary
        secondary = message.secondary
        bg = message.bg_light
        count = 0

        for i, s in enumerate(message.slides):
            st = s.get("slide_type")
            path = None

            if st == "traction":
                path = self._traction(s, accent, primary, bg, i)
            elif st == "market":
                path = self._market(s, accent, primary, secondary, bg, i)
            elif st == "use_of_funds":
                path = self._funds(s, accent, primary, secondary, bg, i)
            elif st == "competitive":
                path = self._competitive(s, accent, primary, secondary, bg, i)

            if path:
                s["chart_path"] = str(path)
                count += 1

        print(f"  [CHARTS] {count} Charts generiert.")

        return DeckBuildRequest(
            company_name=message.company_name, slides=message.slides,
            **{f: getattr(message, f) for f in THEME_FIELDS if f != "images"},
        )

    def _h(self, c):
        return c if c.startswith("#") else f"#{c}"

    def _traction(self, s, accent, primary, bg, idx):
        data = s.get("growth_data", [])
        labels = s.get("growth_labels", [])
        if not data or len(data) < 2:
            return None

        fig, ax = plt.subplots(figsize=(7, 4))
        fig.patch.set_facecolor(self._h(bg))
        ax.set_facecolor(self._h(bg))

        x = range(len(data))
        ax.fill_between(x, data, alpha=0.15, color=self._h(accent))
        ax.plot(x, data, color=self._h(accent), linewidth=3, marker="o", markersize=8)

        if labels and len(labels) == len(data):
            ax.set_xticks(list(x))
            ax.set_xticklabels(labels, fontsize=10, color=self._h(primary))

        ax.set_ylabel(s.get("growth_metric", ""), fontsize=11, color=self._h(primary))
        for spine in ["top", "right"]:
            ax.spines[spine].set_visible(False)
        for spine in ["left", "bottom"]:
            ax.spines[spine].set_color(self._h(primary))
        ax.tick_params(colors=self._h(primary))
        ax.grid(axis="y", alpha=0.3)

        path = self.chart_dir / f"traction_{idx}.png"
        fig.savefig(path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
        plt.close(fig)
        return path

    def _market(self, s, accent, primary, secondary, bg, idx):
        tam = s.get("tam", "")
        sam = s.get("sam", "")
        som = s.get("som", "")
        if not tam:
            return None

        fig, ax = plt.subplots(figsize=(5, 5))
        fig.patch.set_facecolor(self._h(bg))
        ax.set_facecolor(self._h(bg))

        for rad, col, label, alp in [
            (0.45, self._h(primary), f"TAM\n{tam}", 0.2),
            (0.30, self._h(secondary), f"SAM\n{sam}", 0.4),
            (0.15, self._h(accent), f"SOM\n{som}", 0.6),
        ]:
            ax.add_patch(plt.Circle((0.5, 0.5), rad, color=col, alpha=alp))
            ax.text(0.5, 0.5 - rad + rad * 0.4, label,
                    ha="center", va="center", fontsize=9, fontweight="bold", color="white")

        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.set_aspect("equal")
        ax.axis("off")

        path = self.chart_dir / f"market_{idx}.png"
        fig.savefig(path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
        plt.close(fig)
        return path

    def _funds(self, s, accent, primary, secondary, bg, idx):
        alloc = s.get("allocation", [])
        if not alloc:
            return None

        labels = [a["category"] for a in alloc]
        sizes = [a["percent"] for a in alloc]
        colors = [self._h(accent), self._h(secondary), self._h(primary), "#888888", "#AAAAAA"]

        fig, ax = plt.subplots(figsize=(5, 5))
        fig.patch.set_facecolor(self._h(bg))

        wedges, texts, autos = ax.pie(
            sizes, labels=labels, autopct="%1.0f%%",
            colors=colors[:len(sizes)], startangle=90, pctdistance=0.75,
            textprops={"fontsize": 10, "color": self._h(primary)},
        )
        for at in autos:
            at.set_color("white")
            at.set_fontweight("bold")

        ax.add_patch(plt.Circle((0, 0), 0.50, fc=self._h(bg)))
        ax.text(0, 0, s.get("ask_amount", ""), ha="center", va="center",
                fontsize=18, fontweight="bold", color=self._h(primary))

        path = self.chart_dir / f"funds_{idx}.png"
        fig.savefig(path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
        plt.close(fig)
        return path

    def _competitive(self, s, accent, primary, secondary, bg, idx):
        comps = s.get("competitors", [])
        if not comps:
            return None

        fig, ax = plt.subplots(figsize=(7, 4))
        fig.patch.set_facecolor(self._h(bg))
        ax.set_facecolor(self._h(bg))

        names = [c["name"] for c in comps] + ["Wir"]
        colors = [self._h(secondary)] * len(comps) + [self._h(accent)]
        vals = [60 + i * 5 for i in range(len(comps))] + [95]

        ax.barh(range(len(names)), vals, color=colors, height=0.5, alpha=0.85)
        ax.set_yticks(range(len(names)))
        ax.set_yticklabels(names, fontsize=12, color=self._h(primary))
        ax.set_xlim(0, 100)
        for sp in ["top", "right"]:
            ax.spines[sp].set_visible(False)
        for sp in ["left", "bottom"]:
            ax.spines[sp].set_color(self._h(primary))
        ax.tick_params(colors=self._h(primary))
        ax.invert_yaxis()

        path = self.chart_dir / f"comp_{idx}.png"
        fig.savefig(path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
        plt.close(fig)
        return path


# =====================================================================
# Agent 5: HTML Slide Builder + Playwright PDF
# =====================================================================

def _img_to_base64(path):
    """Konvertiert Bilddatei zu base64 data-URI."""
    import base64
    p = Path(path)
    if not p.exists():
        return ""
    suffix = p.suffix.lower()
    mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png"}.get(suffix.lstrip("."), "png")
    return f"data:image/{mime};base64,{base64.b64encode(p.read_bytes()).decode()}"


class SlideBuilderAgent(RoutedAgent):
    def __init__(self):
        super().__init__("SlideBuilder")

    @message_handler
    async def build(self, message: DeckBuildRequest, ctx: MessageContext) -> DeckBuildResponse:
        print(f"  [BUILDER] Erstelle HTML Deck ({len(message.slides)} Slides)...")

        a = message
        total = len(message.slides)
        slides_html = []

        for idx, sd in enumerate(message.slides):
            st = sd.get("slide_type", "content")
            t = sd.get("title", "")
            sub = sd.get("subtitle", "")
            bul = sd.get("bullets", [])[:5]
            chart = sd.get("chart_path")
            bg_img = sd.get("bg_image")

            layout = sd.get("layout", "bullets")

            if st == "intro":
                slides_html.append(self._intro(a, t, sub, bg_img))
            elif st == "cta":
                slides_html.append(self._cta(a, t, sub, bul, bg_img))
            elif st == "team":
                slides_html.append(self._team(a, t, sub, bul, idx + 1, total))
            elif chart and Path(chart).exists():
                slides_html.append(self._chart(a, t, sub, sd, chart, idx + 1, total))
            elif layout == "hero-number":
                slides_html.append(self._hero_number(a, t, sub, sd, bul, idx + 1, total))
            elif layout == "split-highlight":
                slides_html.append(self._split_highlight(a, t, sub, sd, bul, idx + 1, total))
            elif layout == "timeline":
                slides_html.append(self._timeline(a, t, sub, bul, idx + 1, total))
            elif layout == "icon-bullets":
                slides_html.append(self._icon_bullets(a, t, sub, sd, bul, idx + 1, total))
            elif layout == "grid-cards":
                slides_html.append(self._grid_cards(a, t, sub, bul, idx + 1, total))
            else:
                slides_html.append(self._content(a, t, sub, bul, idx + 1, total))

        html = self._wrap(a, slides_html)

        from datetime import datetime
        ts = datetime.now().strftime("%H%M%S")
        base = message.output_path or f"{message.company_name.lower().replace(' ', '_')}_deck_{ts}"
        base = base.replace(".pptx", "").replace(".pdf", "").replace(".html", "")

        html_path = f"{base}.html"
        Path(html_path).write_text(html, encoding="utf-8")
        print(f"  [BUILDER] HTML: {html_path}")

        # PDF via Playwright (async)
        pdf_path = f"{base}.pdf"
        try:
            from playwright.async_api import async_playwright
            async with async_playwright() as pw:
                browser = await pw.chromium.launch()
                page = await browser.new_page()
                await page.goto(f"file:///{Path(html_path).resolve()}")
                await page.pdf(
                    path=pdf_path,
                    width="1280px",
                    height="720px",
                    print_background=True,
                    margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
                )
                await browser.close()
            print(f"  [BUILDER] PDF: {pdf_path}")
        except Exception as e:
            print(f"  [BUILDER] PDF-Fehler: {str(e)[:100]}")
            pdf_path = html_path

        return DeckBuildResponse(output_path=pdf_path, slide_count=total, success=True)

    # --- HTML Wrapper ---

    def _wrap(self, a, slides):
        return f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap');

*, *::before, *::after {{ margin: 0; padding: 0; box-sizing: border-box; }}

body {{
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    margin: 0; padding: 20px 0;
    background: #0a0a0a;
}}

.slide {{
    width: 1280px; height: 720px;
    position: relative; overflow: hidden;
    page-break-after: always; break-after: page;
    margin: 0 auto 32px auto;
    box-shadow: 0 12px 48px rgba(0,0,0,0.5);
    border-radius: 6px;
}}
@media print {{
    body {{ background: none; padding: 0; }}
    .slide {{ margin: 0; box-shadow: none; border-radius: 0; page-break-inside: avoid; }}
}}

/* ===== DARK SLIDES (Intro / CTA) ===== */
.slide-dark {{
    background: linear-gradient(145deg, {a.bg_dark} 0%, {a.primary} 50%, {a.secondary}44 100%);
    color: {a.text_light};
    display: flex; flex-direction: column;
    justify-content: center; align-items: center; text-align: center;
}}
.slide-dark.has-bg {{ background-size: cover; background-position: center; }}
.slide-dark .overlay {{
    position: absolute; inset: 0;
    background: linear-gradient(145deg, {a.bg_dark}e8 0%, {a.primary}cc 60%, {a.secondary}66 100%);
    backdrop-filter: blur(2px);
}}
.slide-dark .content {{ position: relative; z-index: 2; padding: 60px 80px; max-width: 1100px; }}
.slide-dark h1 {{
    font-size: 58px; font-weight: 900; letter-spacing: -2px;
    margin-bottom: 20px;
    background: linear-gradient(180deg, {a.text_light} 0%, {a.text_light}cc 100%);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    text-shadow: none;
}}
.slide-dark .tagline {{
    font-size: 21px; font-weight: 300; color: {a.accent};
    margin-bottom: 36px; max-width: 800px;
    letter-spacing: 0.5px;
}}
.slide-dark .accent-line {{
    width: 80px; height: 3px;
    background: linear-gradient(90deg, {a.accent}, {a.secondary});
    margin: 0 auto; border-radius: 2px;
}}
.slide-dark .cta-bullets {{
    list-style: none; margin-top: 28px; font-size: 19px; font-weight: 400;
}}
.slide-dark .cta-bullets li {{ margin: 10px 0; opacity: 0.9; }}
.slide-dark .cta-bullets li::before {{ content: "\\2794  "; color: {a.accent}; }}
.accent-bar-bottom {{
    position: absolute; bottom: 0; left: 0; right: 0; height: 4px;
    background: linear-gradient(90deg, {a.accent}, {a.secondary}, {a.accent});
}}

/* ===== CONTENT SLIDES (Dark Glass) ===== */
.slide-light {{
    background: linear-gradient(160deg, {a.bg_dark} 0%, {a.primary} 40%, {a.secondary}33 100%);
    color: {a.text_light};
    position: relative;
}}
.slide-light::before {{
    content: ''; position: absolute; inset: 0;
    background:
        radial-gradient(ellipse 600px 400px at 85% 75%, {a.accent}15, transparent),
        radial-gradient(ellipse 500px 300px at 10% 30%, {a.secondary}10, transparent);
    pointer-events: none;
}}
.slide-light .accent-bar {{
    position: absolute; left: 0; top: 0; width: 4px; height: 100%;
    background: linear-gradient(180deg, {a.accent}, {a.secondary}88, transparent);
    z-index: 2;
}}
.slide-light .header {{
    background: rgba(0,0,0,0.25);
    border-bottom: 1px solid {a.accent}22;
    padding: 24px 48px 20px 48px; margin-left: 4px;
    display: flex; align-items: baseline; gap: 20px;
    position: relative; z-index: 1;
    backdrop-filter: blur(8px);
}}
.slide-light .header h2 {{
    font-size: 30px; font-weight: 700; color: {a.text_light};
    letter-spacing: -0.5px;
}}
.slide-light .header .subtitle {{
    font-size: 15px; font-weight: 400; color: {a.accent}; opacity: 0.9;
}}
.slide-light .body {{
    padding: 28px 48px 16px 48px; margin-left: 4px;
    display: flex; gap: 36px;
    height: calc(720px - 76px - 28px); overflow: hidden;
    position: relative; z-index: 1;
}}
.slide-light .body-full {{ flex: 1; display: flex; flex-direction: column; justify-content: center; }}
.slide-light .body-text {{ flex: 1; display: flex; flex-direction: column; justify-content: center; }}
.slide-light .body-chart {{
    flex: 1.2; display: flex; align-items: center; justify-content: center;
    background: rgba(255,255,255,0.08); border-radius: 14px; padding: 16px;
    box-shadow: 0 4px 24px rgba(0,0,0,0.15);
    border: 1px solid rgba(255,255,255,0.08);
    backdrop-filter: blur(8px);
}}
.slide-light .body-chart img {{ max-width: 100%; max-height: 100%; border-radius: 8px; }}

/* ===== BULLETS (Glass Cards) ===== */
.bullet-list {{ list-style: none; padding: 0; }}
.bullet-list li {{
    display: flex; align-items: flex-start; gap: 16px;
    margin-bottom: 10px; font-size: 18px; font-weight: 400;
    line-height: 1.5; color: {a.text_light}dd;
    padding: 14px 20px; border-radius: 10px;
    background: rgba(255,255,255,0.06);
    border: 1px solid rgba(255,255,255,0.08);
    border-left: 3px solid {a.accent}55;
    backdrop-filter: blur(4px);
    transition: all 0.2s;
}}
.bullet-list li:hover {{
    background: rgba(255,255,255,0.1);
    border-left-color: {a.accent};
    box-shadow: 0 2px 16px rgba(0,0,0,0.1);
}}
.bullet-dot {{
    width: 10px; height: 10px; min-width: 10px;
    border-radius: 50%; background: {a.accent};
    margin-top: 7px; box-shadow: 0 0 12px {a.accent}66;
}}
.bullet-list li span:last-child {{
    overflow: hidden; text-overflow: ellipsis;
    display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
}}

/* ===== METRICS (Glass) ===== */
.metrics-row {{
    display: flex; gap: 24px; margin-bottom: 20px;
}}
.metric-card {{
    flex: 1; border-radius: 14px; padding: 20px 24px;
    background: rgba(255,255,255,0.08);
    box-shadow: 0 4px 20px rgba(0,0,0,0.15);
    border: 1px solid rgba(255,255,255,0.1);
    border-top: 3px solid {a.accent};
    backdrop-filter: blur(8px);
    text-align: center;
}}
.metric-value {{
    font-size: 32px; font-weight: 800; color: {a.accent};
    letter-spacing: -1px; margin-bottom: 4px;
}}
.metric-label {{
    font-size: 12px; font-weight: 500; color: {a.text_light}88;
    text-transform: uppercase; letter-spacing: 1px;
}}

/* ===== HERO NUMBER ===== */
.hero-number {{
    display: flex; flex-direction: column; align-items: center;
    justify-content: center; text-align: center; height: 100%;
}}
.hero-value {{
    font-size: 72px; font-weight: 900; color: {a.accent};
    letter-spacing: -3px; line-height: 1;
    text-shadow: 0 0 40px {a.accent}33;
    margin-bottom: 12px;
}}
.hero-label {{
    font-size: 18px; font-weight: 400; color: {a.text_light}99;
    text-transform: uppercase; letter-spacing: 2px;
    margin-bottom: 32px;
}}
.hero-sub-bullets {{ list-style: none; padding: 0; text-align: center; }}
.hero-sub-bullets li {{
    display: inline-block; margin: 0 16px;
    font-size: 15px; color: {a.text_light}88;
    padding: 8px 20px; border-radius: 20px;
    background: rgba(255,255,255,0.06);
    border: 1px solid rgba(255,255,255,0.08);
}}

/* ===== SPLIT HIGHLIGHT ===== */
.split-layout {{
    display: flex; gap: 40px; height: 100%;
    align-items: center;
}}
.split-left {{
    flex: 0 0 320px; display: flex; flex-direction: column;
    align-items: center; justify-content: center; text-align: center;
    padding: 20px;
    background: rgba(255,255,255,0.04);
    border-radius: 16px;
    border: 1px solid rgba(255,255,255,0.06);
}}
.split-value {{
    font-size: 56px; font-weight: 900; color: {a.accent};
    letter-spacing: -2px; line-height: 1;
    text-shadow: 0 0 30px {a.accent}33;
    margin-bottom: 8px;
}}
.split-label {{
    font-size: 14px; font-weight: 500; color: {a.text_light}77;
    text-transform: uppercase; letter-spacing: 1.5px;
}}
.split-right {{ flex: 1; display: flex; flex-direction: column; justify-content: center; }}

/* ===== TIMELINE ===== */
.timeline {{ list-style: none; padding: 0; position: relative; }}
.timeline::before {{
    content: ''; position: absolute; left: 24px; top: 24px; bottom: 24px;
    width: 2px; background: linear-gradient(180deg, {a.accent}, {a.secondary}44);
}}
.timeline li {{
    display: flex; align-items: flex-start; gap: 20px;
    margin-bottom: 8px; padding: 12px 16px 12px 0;
    position: relative;
}}
.timeline-dot {{
    width: 16px; height: 16px; min-width: 16px;
    border-radius: 50%; background: {a.accent};
    box-shadow: 0 0 12px {a.accent}55;
    margin-top: 4px; position: relative; z-index: 1;
    margin-left: 17px;
}}
.timeline-content {{
    flex: 1; font-size: 17px; color: {a.text_light}cc;
    line-height: 1.5;
    padding: 12px 20px; border-radius: 10px;
    background: rgba(255,255,255,0.05);
    border: 1px solid rgba(255,255,255,0.06);
}}
.timeline-step {{
    font-size: 11px; font-weight: 700; color: {a.accent};
    text-transform: uppercase; letter-spacing: 1px;
    margin-bottom: 4px; display: block;
}}

/* ===== ICON BULLETS ===== */
.icon-bullet-list {{ list-style: none; padding: 0; }}
.icon-bullet-list li {{
    display: flex; align-items: flex-start; gap: 16px;
    margin-bottom: 10px; font-size: 18px; font-weight: 400;
    line-height: 1.5; color: {a.text_light}dd;
    padding: 14px 20px; border-radius: 10px;
    background: rgba(255,255,255,0.06);
    border: 1px solid rgba(255,255,255,0.08);
}}
.icon-emoji {{
    font-size: 24px; min-width: 32px; text-align: center;
    margin-top: 0px;
}}

/* ===== GRID CARDS ===== */
.grid-cards {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
    gap: 16px; padding: 0;
}}
.grid-card {{
    background: rgba(255,255,255,0.06);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 12px; padding: 24px;
    border-top: 3px solid {a.accent}55;
}}
.grid-card-title {{
    font-size: 15px; font-weight: 700; color: {a.accent};
    margin-bottom: 8px; text-transform: uppercase;
    letter-spacing: 0.5px;
}}
.grid-card-text {{
    font-size: 16px; color: {a.text_light}bb; line-height: 1.5;
}}

/* ===== TEAM CARDS ===== */
.team-grid {{
    display: flex; gap: 20px; flex-wrap: wrap;
    justify-content: center; align-items: stretch;
    padding: 0 20px;
}}
.team-card {{
    flex: 1; min-width: 200px; max-width: 340px;
    background: rgba(255,255,255,0.06); border-radius: 14px; padding: 28px 24px;
    box-shadow: 0 4px 20px rgba(0,0,0,0.15);
    border: 1px solid rgba(255,255,255,0.08);
    border-top: 3px solid {a.accent};
    backdrop-filter: blur(8px);
    text-align: center;
    display: flex; flex-direction: column; align-items: center; gap: 8px;
}}
.team-avatar {{
    width: 56px; height: 56px; border-radius: 50%;
    background: linear-gradient(135deg, {a.primary}, {a.secondary});
    display: flex; align-items: center; justify-content: center;
    font-size: 22px; font-weight: 700; color: {a.text_light};
    margin-bottom: 4px;
}}
.team-name {{
    font-size: 17px; font-weight: 700; color: {a.text_light};
}}
.team-role {{
    display: inline-block; font-size: 11px; font-weight: 600;
    color: {a.accent}; background: {a.accent}15;
    padding: 3px 12px; border-radius: 20px;
    text-transform: uppercase; letter-spacing: 0.8px;
}}
.team-desc {{
    font-size: 13px; color: {a.text_light}; opacity: 0.6;
    line-height: 1.4; margin-top: 4px;
}}

/* ===== SLIDE NUMBER ===== */
.slide-num {{
    position: absolute; bottom: 12px; right: 20px;
    font-size: 11px; color: rgba(255,255,255,0.3); font-weight: 400;
    z-index: 2;
}}

/* ===== FOOTER LINE ===== */
.slide-light .footer-line {{
    position: absolute; bottom: 0; left: 4px; right: 0; height: 2px;
    background: linear-gradient(90deg, {a.accent}44, transparent);
    z-index: 2;
}}
</style>
</head>
<body>
{''.join(slides)}
</body>
</html>"""

    # --- Slide generators ---

    def _esc(self, t):
        return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def _intro(self, a, title, subtitle, bg_img=None):
        bg_style = ""
        overlay = ""
        cls = "slide slide-dark"
        if bg_img and Path(bg_img).exists():
            b64 = _img_to_base64(bg_img)
            bg_style = f' style="background-image:url({b64})"'
            overlay = '<div class="overlay"></div>'
            cls += " has-bg"

        sub_html = f'<div class="tagline">{self._esc(subtitle)}</div>' if subtitle else ""
        return f"""<div class="{cls}"{bg_style}>
  {overlay}
  <div class="content">
    <h1>{self._esc(title)}</h1>
    {sub_html}
    <div class="accent-line"></div>
  </div>
  <div class="accent-bar-bottom"></div>
</div>"""

    def _cta(self, a, title, subtitle, bullets, bg_img=None):
        bg_style = ""
        overlay = ""
        cls = "slide slide-dark"
        if bg_img and Path(bg_img).exists():
            b64 = _img_to_base64(bg_img)
            bg_style = f' style="background-image:url({b64})"'
            overlay = '<div class="overlay"></div>'
            cls += " has-bg"

        sub_html = f'<div class="tagline">{self._esc(subtitle)}</div>' if subtitle else ""
        bul_html = ""
        if bullets:
            items = "".join(f"<li>{self._esc(b)}</li>" for b in bullets)
            bul_html = f'<ul class="cta-bullets">{items}</ul>'

        return f"""<div class="{cls}"{bg_style}>
  {overlay}
  <div class="content">
    <h1>{self._esc(title)}</h1>
    {sub_html}
    {bul_html}
    <div class="accent-line"></div>
  </div>
  <div class="accent-bar-bottom"></div>
</div>"""

    def _content(self, a, title, subtitle, bullets, num, total):
        sub_html = f'<span class="subtitle">{self._esc(subtitle)}</span>' if subtitle else ""
        bul_html = ""
        if bullets:
            items = "".join(
                f'<li><span class="bullet-dot"></span><span>{self._esc(b)}</span></li>'
                for b in bullets
            )
            bul_html = f'<ul class="bullet-list">{items}</ul>'

        return f"""<div class="slide slide-light">
  <div class="accent-bar"></div>
  <div class="header">
    <h2>{self._esc(title)}</h2>
    {sub_html}
  </div>
  <div class="body">
    <div class="body-full">{bul_html}</div>
  </div>
  <div class="footer-line"></div>
  <div class="slide-num">{num} / {total}</div>
</div>"""

    def _hero_number(self, a, title, subtitle, sd, bullets, num, total):
        sub_html = f'<span class="subtitle">{self._esc(subtitle)}</span>' if subtitle else ""
        val = sd.get("hero_value", "")
        label = sd.get("hero_label", "")
        sub_bullets = ""
        if bullets:
            items = "".join(f"<li>{self._esc(b)}</li>" for b in bullets[:4])
            sub_bullets = f'<ul class="hero-sub-bullets">{items}</ul>'

        return f"""<div class="slide slide-light">
  <div class="accent-bar"></div>
  <div class="header"><h2>{self._esc(title)}</h2>{sub_html}</div>
  <div class="body">
    <div class="body-full">
      <div class="hero-number">
        <div class="hero-value">{self._esc(val)}</div>
        <div class="hero-label">{self._esc(label)}</div>
        {sub_bullets}
      </div>
    </div>
  </div>
  <div class="footer-line"></div>
  <div class="slide-num">{num} / {total}</div>
</div>"""

    def _split_highlight(self, a, title, subtitle, sd, bullets, num, total):
        sub_html = f'<span class="subtitle">{self._esc(subtitle)}</span>' if subtitle else ""
        val = sd.get("highlight_value", "")
        label = sd.get("highlight_label", "")
        bul_html = ""
        if bullets:
            items = "".join(
                f'<li><span class="bullet-dot"></span><span>{self._esc(b)}</span></li>'
                for b in bullets
            )
            bul_html = f'<ul class="bullet-list">{items}</ul>'

        return f"""<div class="slide slide-light">
  <div class="accent-bar"></div>
  <div class="header"><h2>{self._esc(title)}</h2>{sub_html}</div>
  <div class="body">
    <div class="split-layout">
      <div class="split-left">
        <div class="split-value">{self._esc(val)}</div>
        <div class="split-label">{self._esc(label)}</div>
      </div>
      <div class="split-right">{bul_html}</div>
    </div>
  </div>
  <div class="footer-line"></div>
  <div class="slide-num">{num} / {total}</div>
</div>"""

    def _timeline(self, a, title, subtitle, bullets, num, total):
        sub_html = f'<span class="subtitle">{self._esc(subtitle)}</span>' if subtitle else ""
        items = ""
        for i, b in enumerate(bullets):
            step_label = f"Schritt {i+1}" if not b.lower().startswith("schritt") else ""
            items += f"""<li>
  <div class="timeline-dot"></div>
  <div class="timeline-content">
    {"<span class='timeline-step'>" + step_label + "</span>" if step_label else ""}
    {self._esc(b)}
  </div>
</li>"""

        return f"""<div class="slide slide-light">
  <div class="accent-bar"></div>
  <div class="header"><h2>{self._esc(title)}</h2>{sub_html}</div>
  <div class="body">
    <div class="body-full" style="justify-content:center;">
      <ul class="timeline">{items}</ul>
    </div>
  </div>
  <div class="footer-line"></div>
  <div class="slide-num">{num} / {total}</div>
</div>"""

    def _icon_bullets(self, a, title, subtitle, sd, bullets, num, total):
        sub_html = f'<span class="subtitle">{self._esc(subtitle)}</span>' if subtitle else ""
        icons = sd.get("icons", [])
        items = ""
        for i, b in enumerate(bullets):
            icon = icons[i] if i < len(icons) else "\u2022"
            items += f'<li><span class="icon-emoji">{icon}</span><span>{self._esc(b)}</span></li>'

        return f"""<div class="slide slide-light">
  <div class="accent-bar"></div>
  <div class="header"><h2>{self._esc(title)}</h2>{sub_html}</div>
  <div class="body">
    <div class="body-full">
      <ul class="icon-bullet-list">{items}</ul>
    </div>
  </div>
  <div class="footer-line"></div>
  <div class="slide-num">{num} / {total}</div>
</div>"""

    def _grid_cards(self, a, title, subtitle, bullets, num, total):
        sub_html = f'<span class="subtitle">{self._esc(subtitle)}</span>' if subtitle else ""
        cards = ""
        for b in bullets:
            parts = b.split(":", 1)
            if len(parts) == 2:
                card_title = parts[0].strip()
                card_text = parts[1].strip()
            else:
                card_title = ""
                card_text = b
            title_html = f'<div class="grid-card-title">{self._esc(card_title)}</div>' if card_title else ""
            cards += f'<div class="grid-card">{title_html}<div class="grid-card-text">{self._esc(card_text)}</div></div>'

        return f"""<div class="slide slide-light">
  <div class="accent-bar"></div>
  <div class="header"><h2>{self._esc(title)}</h2>{sub_html}</div>
  <div class="body">
    <div class="body-full" style="justify-content:center;">
      <div class="grid-cards">{cards}</div>
    </div>
  </div>
  <div class="footer-line"></div>
  <div class="slide-num">{num} / {total}</div>
</div>"""

    def _team(self, a, title, subtitle, bullets, num, total):
        sub_html = f'<span class="subtitle">{self._esc(subtitle)}</span>' if subtitle else ""
        cards = ""
        for b in bullets:
            # Parse "Name — Role: Description" or "Name: Description"
            parts = b.replace("\u2014", "—").split("—", 1)
            if len(parts) == 2:
                name = parts[0].strip()
                rest = parts[1].strip()
            else:
                parts = b.split(":", 1)
                name = parts[0].strip()
                rest = parts[1].strip() if len(parts) > 1 else ""

            # Try to extract role from rest
            role = ""
            desc = rest
            if ";" in rest:
                role_part, desc = rest.split(";", 1)
                role = role_part.strip()
                desc = desc.strip()
            elif "." in rest and len(rest.split(".")[0]) < 40:
                role = rest.split(".")[0].strip()
                desc = ".".join(rest.split(".")[1:]).strip()

            initials = "".join(w[0].upper() for w in name.split()[:2] if w)
            role_html = f'<div class="team-role">{self._esc(role)}</div>' if role else ""
            desc_html = f'<div class="team-desc">{self._esc(desc)}</div>' if desc else ""

            cards += f"""<div class="team-card">
  <div class="team-avatar">{initials}</div>
  <div class="team-name">{self._esc(name)}</div>
  {role_html}
  {desc_html}
</div>"""

        return f"""<div class="slide slide-light">
  <div class="accent-bar"></div>
  <div class="header">
    <h2>{self._esc(title)}</h2>
    {sub_html}
  </div>
  <div class="body">
    <div class="body-full" style="display:flex;align-items:center;justify-content:center;">
      <div class="team-grid">{cards}</div>
    </div>
  </div>
  <div class="footer-line"></div>
  <div class="slide-num">{num} / {total}</div>
</div>"""

    def _chart(self, a, title, subtitle, sd, chart_path, num, total):
        sub_html = f'<span class="subtitle">{self._esc(subtitle)}</span>' if subtitle else ""
        chart_b64 = _img_to_base64(chart_path)

        # Key-points
        points = []
        if sd.get("our_advantage"):
            points.append(f"\u2714 {sd['our_advantage']}")
        if sd.get("ask_amount"):
            points.append(f"Ask: {sd['ask_amount']}")
        for m in sd.get("milestones", [])[:3]:
            points.append(m)
        for b in sd.get("bullets", [])[:3]:
            if len(points) < 5:
                points.append(b)

        points_html = ""
        if points:
            items = "".join(
                f'<li><span class="bullet-dot"></span><span>{self._esc(p)}</span></li>'
                for p in points
            )
            points_html = f'<ul class="bullet-list">{items}</ul>'

        # Metrics cards for ask_amount
        metrics_html = ""
        if sd.get("ask_amount"):
            metrics_html = f"""<div class="metrics-row">
  <div class="metric-card"><div class="metric-value">{self._esc(sd['ask_amount'])}</div><div class="metric-label">Investment</div></div>
</div>"""

        return f"""<div class="slide slide-light">
  <div class="accent-bar"></div>
  <div class="header">
    <h2>{self._esc(title)}</h2>
    {sub_html}
  </div>
  <div class="body">
    <div class="body-chart">
      <img src="{chart_b64}" alt="chart">
    </div>
    <div class="body-text">
      {metrics_html}
      {points_html}
    </div>
  </div>
  <div class="footer-line"></div>
  <div class="slide-num">{num} / {total}</div>
</div>"""


# =====================================================================
# Feature: Follow-Up Emails
# =====================================================================

EMAIL_PROMPT = """Du bist ein Startup-Fundraising-Experte. Erstelle 3 Investor-Email-Varianten.

Firma: {company}
Branche: {industry}
Investment-Ask: {ask}
Key-Traction: {traction}

Erstelle 3 professionelle Emails auf Deutsch:

1. KALT-AKQUISE (Erster Kontakt, kein vorheriger Bezug)
2. WARM-INTRO (Ueber gemeinsamen Kontakt / Event)
3. FOLLOW-UP (Nach einem ersten Meeting/Call)

Jede Email soll:
- Betreff-Zeile haben
- Kurz sein (max 8 Saetze)
- Die Key-Metriken erwaehnen
- Einen klaren Call-to-Action haben
- Professionell aber nicht steif klingen

Format:
---
VARIANTE 1: KALT-AKQUISE
Betreff: ...
[Email-Text]
---
VARIANTE 2: WARM-INTRO
Betreff: ...
[Email-Text]
---
VARIANTE 3: FOLLOW-UP
Betreff: ...
[Email-Text]
---"""


def generate_investor_emails(company, research_json, output_path):
    """Generiert 3 Investor-Email-Varianten basierend auf Deck-Daten."""
    print(f"\n  [EMAILS] Generiere Investor-Emails...")
    client = get_client_sync(PITCH_DECK_ROLE)

    research = json.loads(research_json) if isinstance(research_json, str) else research_json
    market = research.get("market", {})
    business = research.get("business", {})

    ask = business.get("ask_amount", "€500K")
    traction_items = []
    proj = business.get("growth_projection", {})
    if proj.get("users"):
        traction_items.append(f"Nutzer-Prognose: {proj['users'][-1]} in 12 Monaten")
    if proj.get("mrr"):
        traction_items.append(f"MRR-Prognose: €{proj['mrr'][-1]:,} in 12 Monaten")
    if market.get("tam"):
        traction_items.append(f"TAM: {market['tam']}")

    prompt = EMAIL_PROMPT.format(
        company=company,
        industry=market.get("key_trends", ["Tech"])[0] if market.get("key_trends") else "Tech",
        ask=ask,
        traction="; ".join(traction_items) or "Early Stage",
    )

    try:
        resp = client.chat.completions.create(
            model=get_model(PITCH_DECK_ROLE),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
        )
        emails = resp.choices[0].message.content

        email_file = Path(output_path).with_suffix(".emails.txt")
        email_file.write_text(emails, encoding="utf-8")
        print(f"  [EMAILS] Gespeichert: {email_file}")
        return str(email_file)
    except OpenFangUnavailable:
        raise
    except Exception as e:
        print(f"  [EMAILS] Fehler: {e}")
        return None


# =====================================================================
# Feature: PDF-Export
# =====================================================================

def export_pdf(pptx_path):
    """Konvertiert PPTX zu PDF via LibreOffice CLI."""
    import subprocess
    import shutil

    print(f"\n  [PDF] Konvertiere zu PDF...")

    # LibreOffice finden
    soffice = shutil.which("soffice")
    if not soffice:
        # Windows Standard-Pfade
        for p in [
            r"C:\Program Files\LibreOffice\program\soffice.exe",
            r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        ]:
            if Path(p).exists():
                soffice = p
                break

    if not soffice:
        print("  [PDF] LibreOffice nicht gefunden!")
        print("  [PDF] Installiere LibreOffice: https://www.libreoffice.org/download/")
        return None

    try:
        out_dir = str(Path(pptx_path).parent)
        result = subprocess.run(
            [soffice, "--headless", "--convert-to", "pdf", str(pptx_path), "--outdir", out_dir],
            capture_output=True, text=True, timeout=60,
        )
        pdf_path = str(Path(pptx_path).with_suffix(".pdf"))
        if Path(pdf_path).exists():
            print(f"  [PDF] Gespeichert: {pdf_path}")
            return pdf_path
        else:
            print(f"  [PDF] Konvertierung fehlgeschlagen: {result.stderr}")
            return None
    except Exception as e:
        print(f"  [PDF] Fehler: {e}")
        return None


# =====================================================================
# Feature: Feedback-Loop
# =====================================================================

FEEDBACK_PROMPT = """Du bist ein Pitch-Deck-Editor. Der User moechte eine oder mehrere Slides aendern.

Aktuelle Slides:
{slides_json}

User-Feedback: "{feedback}"

Bestimme welche Slides betroffen sind und aendere NUR diese.
Antworte mit validem JSON:
{{
  "changed_indices": [3, 5],
  "slides": [komplettes aktualisiertes Slides-Array mit ALLEN 12 Slides]
}}

Regeln:
- Aendere NUR die Slides die vom Feedback betroffen sind
- Lass alle anderen Slides EXAKT gleich
- Behalte slide_type, growth_data, allocation etc. bei wenn nicht explizit geaendert
- 4-5 Bullets pro Content-Slide, max 3 fuer Intro/CTA"""


async def feedback_loop(slides, company, theme_fields, runtime):
    """Interaktiver Feedback-Loop zum Verbessern einzelner Slides."""
    client = get_client_sync(PITCH_DECK_ROLE)

    while True:
        print("\n  Feedback? (z.B. 'Slide 3 aggressiver' oder Enter zum Beenden)")
        feedback = input("  > ").strip()
        if not feedback:
            break

        print(f"  [FEEDBACK] Verarbeite: '{feedback}'...")

        slides_json = json.dumps(
            [{"index": i, "type": s.get("slide_type"), "title": s.get("title"),
              "subtitle": s.get("subtitle"), "bullets": s.get("bullets", [])}
             for i, s in enumerate(slides)],
            ensure_ascii=False,
        )

        prompt = FEEDBACK_PROMPT.format(slides_json=slides_json, feedback=feedback)

        try:
            resp = client.chat.completions.create(
                model=get_model(PITCH_DECK_ROLE),
                response_format={"type": "json_object"},
                messages=[{"role": "user", "content": prompt}],
                temperature=0.5,
            )
            data = json.loads(resp.choices[0].message.content)
            changed = data.get("changed_indices", [])
            new_slides = data.get("slides", slides)

            if new_slides and len(new_slides) == len(slides):
                # Merge: behalte chart_path, image_path etc. aus alten Slides
                for i, old in enumerate(slides):
                    for key in ["chart_path", "bg_image", "image_path",
                                "growth_data", "growth_labels", "growth_metric",
                                "allocation", "ask_amount", "competitors"]:
                        if key in old and key not in new_slides[i]:
                            new_slides[i][key] = old[key]
                slides = new_slides
                print(f"  [FEEDBACK] {len(changed)} Slide(s) geaendert: {changed}")

                # Rebuild: Charts + PPTX
                content_msg = ContentResult(
                    company_name=company, slides=slides, **theme_fields,
                )
                charts = await runtime.send_message(content_msg, AgentId("charts", "default"))
                result = await runtime.send_message(charts, AgentId("builder", "default"))
                if result.success:
                    print(f"  [FEEDBACK] Neues Deck: {result.output_path}")
            else:
                print(f"  [FEEDBACK] Konnte Slides nicht aktualisieren.")
        except OpenFangUnavailable:
            raise
        except Exception as e:
            print(f"  [FEEDBACK] Fehler: {e}")

    return slides


# =====================================================================
# Interview (optional)
# =====================================================================

INTERVIEW_QUESTIONS = [
    ("company_name",    "Firmenname:"),
    ("oneliner",        "Was macht ihr in einem Satz?"),
    ("problem",         "Welches Problem loest ihr?"),
    ("solution",        "Was ist eure Loesung?"),
    ("team",            "Wer ist im Team? (Namen, Rollen)"),
    ("traction",        "Was habt ihr schon erreicht?"),
    ("contact",         "Kontakt? (Email, Website)"),
]


def run_interview():
    print("=" * 60)
    print("  PITCH DECK INTERVIEW")
    print("  Gib so viel wie du weisst - den Rest recherchiert der Agent.")
    print("  Enter = ueberspringen")
    print("=" * 60)

    answers = {}
    for key, q in INTERVIEW_QUESTIONS:
        print(f"\n  {q}")
        a = input("  > ").strip()
        if a:
            answers[key] = a

    print(f"\n  Farbschema? ({', '.join(THEMES.keys())}, oder 'auto')")
    answers["theme"] = input("  > ").strip() or "auto"

    print("\n  Hintergrundbilder fuer Intro/CTA? (DALL-E, ~$0.08)")
    print("    1) ja   2) nein")
    answers["images"] = input("  > ").strip() in ("1", "ja", "j", "yes")
    return answers


def build_briefing(answers):
    mapping = [
        ("company_name", "Firmenname"), ("oneliner", "Einzeiler"),
        ("problem", "Problem"), ("solution", "Loesung"),
        ("team", "Team"), ("traction", "Traction"), ("contact", "Kontakt"),
    ]
    return "\n".join(f"{l}: {answers[k]}" for k, l in mapping if answers.get(k))


# =====================================================================
# Main
# =====================================================================

async def main():
    env_file = Path(__file__).parent / ".env"
    if env_file.exists():
        with open(env_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    os.environ[key.strip()] = value.strip()

    if not os.environ.get("OPENFANG_API_KEY"):
        print("FEHLER: OPENFANG_API_KEY nicht gesetzt!")
        sys.exit(1)

    # CLI Flags parsen
    quick = len(sys.argv) >= 3
    flags = {"images": True, "pdf": False, "email": False, "feedback": False}  # images default ON

    if quick:
        company = sys.argv[1]
        desc = sys.argv[2]
        theme = "auto"
        for arg in sys.argv[3:]:
            if arg.startswith("--theme="):
                theme = arg.split("=", 1)[1]
            elif arg == "--images":
                flags["images"] = True
            elif arg == "--pdf":
                flags["pdf"] = True
            elif arg == "--email":
                flags["email"] = True
            elif arg == "--feedback":
                flags["feedback"] = True
    else:
        answers = run_interview()
        company = answers.get("company_name", "Startup")
        desc = build_briefing(answers)
        theme = answers.get("theme", "auto")
        flags["images"] = answers.get("images", False)

    print("\n" + "=" * 60)
    print("Pitch Deck Generator v6")
    print("=" * 60)
    print(f"\n  Firma:     {company}")
    print(f"  Theme:     {theme}")
    active = [k for k, v in flags.items() if v]
    print(f"  Features:  {', '.join(active) if active else 'standard'}\n")

    runtime = SingleThreadedAgentRuntime()
    await BriefingAgent.register(runtime, "briefing", lambda: BriefingAgent())
    await SemanticAnalyzerAgent.register(runtime, "analyzer", lambda: SemanticAnalyzerAgent())
    await ResearcherAgent.register(runtime, "researcher", lambda: ResearcherAgent())
    await ContentGeneratorAgent.register(runtime, "content", lambda: ContentGeneratorAgent())
    await DesignDirectorAgent.register(runtime, "designer", lambda: DesignDirectorAgent())
    await ChartGeneratorAgent.register(runtime, "charts", lambda: ChartGeneratorAgent())
    await SlideBuilderAgent.register(runtime, "builder", lambda: SlideBuilderAgent())
    runtime.start()

    # Pipeline: Briefing -> Analyze -> Research -> Content -> Design -> Charts -> Build
    enriched = await runtime.send_message(
        PitchDeckRequest(company_name=company, description=desc, theme=theme, images=flags["images"]),
        AgentId("briefing", "default"),
    )
    analysis = await runtime.send_message(
        PitchDeckRequest(company_name=enriched.company_name, description=enriched.description, theme=enriched.theme, images=enriched.images),
        AgentId("analyzer", "default"),
    )
    research = await runtime.send_message(analysis, AgentId("researcher", "default"))
    content = await runtime.send_message(research, AgentId("content", "default"))
    designed = await runtime.send_message(content, AgentId("designer", "default"))
    charts = await runtime.send_message(designed, AgentId("charts", "default"))
    result = await runtime.send_message(charts, AgentId("builder", "default"))

    print("\n" + "=" * 60)
    if result.success:
        print(f"  Pitch Deck: {result.output_path}")
        print(f"  Slides:     {result.slide_count}")
        print(f"  Theme:      {charts.theme_name}")
    else:
        print("  FEHLER.")
    print("=" * 60)

    # Post-Processing: Emails
    if flags["email"] and result.success:
        generate_investor_emails(company, research.research, result.output_path)

    # Post-Processing: PDF
    if flags["pdf"] and result.success:
        export_pdf(result.output_path)

    # Post-Processing: Feedback-Loop
    if flags["feedback"] and result.success:
        theme_fields = {f: getattr(charts, f) for f in THEME_FIELDS if f != "images"}
        await feedback_loop(content.slides, company, theme_fields, runtime)

    await runtime.stop()


if __name__ == "__main__":
    asyncio.run(main())
