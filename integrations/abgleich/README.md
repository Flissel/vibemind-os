# Abgleich OpenAI-Katalog gegen VibeMind-Vorlagen

Erzeugt von `scripts/katalog_vorlagen.py` aus `recherche.json` und dem gepinnten Katalog. Nicht von Hand bearbeiten.

Eintraege gesamt: 180

| Ergebnis | Anzahl |
|---|---|
| adresse_je_kunde | 5 |
| braucht_registrierung | 5 |
| domain_pruefen | 0 |
| id_ungueltig | 0 |
| kein_offizieller_server | 40 |
| nicht_erreichbar | 0 |
| skills_teil_5 | 24 |
| unklar | 38 |
| vorlage | 62 |
| vorlage_bestand | 2 |
| vorlage_mitgeliefert | 4 |

| Name | Sorte | Ergebnis | Grund/Klasse | Quelle | Pruefdatum |
|---|---|---|---|---|---|
| actively | app | kein_offizieller_server | Marketing page mentions an Actively MCP/API but gives no server URL or docs; unresolved. | https://www.actively.ai/products/mcp-server |  |
| aiera | app | vorlage | einrichtbar_oauth | https://rest.aiera.com/docs/mcp-sdks | 2026-11-10 |
| airtable | app | unklar | ermittlung fehlgeschlagen: resource passt nicht | https://airtable.com/developers/agents/mcp/getting-started | 2026-11-10 |
| alation | app | adresse_je_kunde | adresse_je_kunde | https://alation.github.io/agent-studio-docs/integrations/mcp/quick-start/ | 2026-11-10 |
| alpaca | app | braucht_registrierung | oauth_ohne_dcr | https://docs.alpaca.markets/us/docs/broker-mcp-server | 2026-11-10 |
| amplitude | app | vorlage | einrichtbar_oauth | https://amplitude.com/docs/amplitude-ai/amplitude-mcp | 2026-11-10 |
| apollo | app | vorlage | einrichtbar_oauth | https://docs.apollo.io/docs/apollo-mcp | 2026-11-10 |
| asana | app | braucht_registrierung | oauth_ohne_dcr | https://developers.asana.com/docs/using-asanas-mcp-server | 2026-11-10 |
| atlassian-rovo | app | vorlage | einrichtbar_oauth | https://support.atlassian.com/atlassian-rovo-mcp-server/docs/getting-started-with-the-atlassian-remote-mcp-server/ | 2026-11-10 |
| attio | app | vorlage | einrichtbar_oauth | https://docs.attio.com/mcp/overview | 2026-11-10 |
| base44 | app | vorlage | einrichtbar_oauth | https://docs.base44.com/developers/backend/overview/mcp-server.md | 2026-11-10 |
| binance | app | kein_offizieller_server | Binance publishes a skills hub (local, API credentials), no verified official remote MCP URL; a third-party guide cites agent.binance.com/mcp/agentic but it could not be confirmed. | https://github.com/binance/binance-skills-hub |  |
| biorender | app | kein_offizieller_server | Official BioRender connector exists (Claude/ChatGPT directories); URL mcp.services.biorender.com seen only in secondary sources and help center returned 403, so not recorded. | https://help.biorender.com/hc/en-gb/articles/37237276158109-How-to-use-the-BioRender-MCP-connector |  |
| boltz-api-cli | skills | skills_teil_5 | nur Skills/Befehle/Agenten |  |  |
| box | app | unklar | ermittlung fehlgeschlagen: fremder host | https://developer.box.com/guides/box-mcp/remote/ | 2026-11-10 |
| brand24 | app | unklar | ermittlung fehlgeschlagen: resource passt nicht | https://help.brand24.com/en/articles/13011375-brand24-mcp | 2026-11-10 |
| brex | app | unklar | ermittlung fehlgeschlagen: resource passt nicht | https://developer.brex.com/docs/mcp | 2026-11-10 |
| brighthire | app | unklar | ermittlung fehlgeschlagen: anmeldeserver-metadaten nicht lesbar | https://help.brighthire.ai/en/articles/14808753-brighthire-mcp | 2026-11-10 |
| build-ios-apps | mcp | kein_offizieller_server | Not a vendor product with a remote MCP server; no official remote server found. |  |  |
| build-macos-apps | skills | skills_teil_5 | nur Skills/Befehle/Agenten |  |  |
| build-web-apps | skills | skills_teil_5 | nur Skills/Befehle/Agenten |  |  |
| build-web-data-visualization | skills | skills_teil_5 | nur Skills/Befehle/Agenten |  |  |
| calendly | app | vorlage | einrichtbar_oauth; Anmeldung scheitert: Calendly verlangt einen https-Rueckruf, OpenFang bietet nur http://127.0.0.1 | https://developer.calendly.com/calendly-mcp-server | 2026-11-10 |
| canva | app | unklar | ermittlung fehlgeschlagen: fremder host | https://www.canva.dev/docs/apps/quickstart/?app-surface=Canva%20for%20AI%20assistants | 2026-11-10 |
| carta-crm | app | vorlage | einrichtbar_oauth | https://docs.carta.com/api-platform/guides/guides/claude-mcp-setup/ | 2026-11-10 |
| catalyst-by-zoho | app | unklar | ermittlung fehlgeschlagen: fremder host | https://docs.catalyst.zoho.com/en/ai-toolkit/catalyst-mcp-server/connect-catalyst-mcp/global-mcp/connect-global-mcp-custom/ | 2026-11-10 |
| cb-insights | app | vorlage | einrichtbar_oauth | https://github.com/cbinsights/cbi-mcp-server | 2026-11-10 |
| channel99 | app | kein_offizieller_server | Channel99 kuendigt einen MCP-Server an (Testphase, Demo auf Anfrage), aber keine URL dokumentiert. | https://www.channel99.com/articles/mcp-server-for-performance-marketing |  |
| chronograph-gp | app | kein_offizieller_server | Chronograph betreibt einen MCP-Connector (Claude/ChatGPT/Perplexity, nur fuer Kunden), die Server-URL ist auf der eigenen Domain nicht dokumentiert. | https://www.chronograph.pe/chronograph-mcp-announcement/ |  |
| chronograph-lp | app | kein_offizieller_server | Gleicher Chronograph-MCP-Connector wie GP, nur fuer Kunden; Server-URL nicht offiziell dokumentiert. | https://www.chronograph.pe/chronograph-mcp-announcement/ |  |
| circleback | app | vorlage | einrichtbar_oauth | https://circleback.ai/docs/support/platform/circleback-mcp | 2026-11-10 |
| circleci | skills | skills_teil_5 | nur Skills/Befehle/Agenten |  |  |
| clay | app | unklar | ermittlung fehlgeschlagen: fremder host | https://university.clay.com/docs/connect-to-clay-mcp | 2026-11-10 |
| clickup | app | vorlage | einrichtbar_oauth | https://clickup.com/blog/how-to-use-clickup-mcp-server/ | 2026-11-10 |
| close | app | unklar | ermittlung fehlgeschlagen: fremder host | https://developer.close.com/mcp | 2026-11-10 |
| cloudflare | mcp | vorlage | einrichtbar_oauth | https://developers.cloudflare.com/agents/model-context-protocol/mcp-servers-for-cloudflare/ | 2026-11-10 |
| cloudinary | app | unklar | ermittlung fehlgeschlagen: resource passt nicht | https://github.com/cloudinary/mcp-servers | 2026-11-10 |
| coderabbit | skills | skills_teil_5 | nur Skills/Befehle/Agenten |  |  |
| codex-security | mcp | kein_offizieller_server | OpenAI-eigenes Produkt; kein eigener Remote-MCP-Server gefunden. |  |  |
| cogedim | app | kein_offizieller_server | Keine Hinweise auf einen MCP-Server von Cogedim gefunden. |  |  |
| common-room | app | vorlage | einrichtbar_oauth | https://www.commonroom.io/docs/using-common-room/mcp-server/ | 2026-11-10 |
| conductor | app | unklar | ermittlung fehlgeschlagen: fremder host | https://www.conductor.com/docs/mcp/custom-connectors.md | 2026-11-10 |
| convex | app | kein_offizieller_server | Offizieller Server nur lokal (npx -y convex@latest mcp start); die Remote-Komponente @convex-dev/mcp ist nur ein Baustein fuer eigene Apps. | https://docs.convex.dev/ai/convex-mcp-server |  |
| coupler-io | app | kein_offizieller_server | Coupler.io erzeugt Endpunkt und Zugriffstoken pro Datenfluss (Custom MCP); feste URL in eigener Doku nicht gefunden (mcp.coupler.io nur in Verzeichnissen), Header-Syntax nicht bestaetigt. | https://docs.coupler.io/destinations/categories/ai/custom_mcp |  |
| coveo | app | braucht_registrierung | oauth_ohne_dcr | https://docs.coveo.com/en/q1mb0212/ | 2026-11-10 |
| cube | app | vorlage | einrichtbar_oauth | https://docs.cube.dev/docs/integrations/mcp-server | 2026-11-10 |
| daloopa | app | vorlage | einrichtbar_oauth | https://docs.daloopa.com/docs/daloopa-mcp | 2026-11-10 |
| datadog | app | unklar | ermittlung fehlgeschlagen: anmeldeserver-metadaten nicht lesbar | https://docs.datadoghq.com/bits_ai/mcp_server/setup/ | 2026-11-10 |
| datasite | app | kein_offizieller_server | Datasite betreibt einen MCP-Server (nur fuer freigeschaltete Konten), die URL steht aber nur bei Dritten (mcp.global.datasite.com/mcp), nicht auf Datasite-Seiten. | https://datasite.com/en/resources/ai-at-datasite/datasite-mcp |  |
| deepnote | app | vorlage | einrichtbar_oauth | https://deepnote.com/docs/deepnote-mcp | 2026-11-10 |
| demandbase | app | kein_offizieller_server | Offizieller MCP-Server existiert (per CSM freizuschalten, ueber Claude-Connector-Verzeichnis), aber keine URL in den Docs. | https://developer.demandbase.com/docs/mcp |  |
| digitalocean | app | unklar | ermittlung fehlgeschlagen: resource passt nicht | https://docs.digitalocean.com/reference/mcp/configure-mcp/ | 2026-11-10 |
| dnb-finance-analytics | app | kein_offizieller_server | D&B bietet MCP-Server nach Zugangsanfrage; URL nur bei Dritten (agents.riskanalytics.dnb.com/mcp), nicht auf D&B-Seiten belegt. | https://www.dnb.co.uk/solutions/artificial-intelligence/connectors/dnb-mcp.html |  |
| docket | app | vorlage | einrichtbar_oauth | https://help.docket.io/articles/8225942528-connect-docket-demand-mcp | 2026-11-10 |
| docusign | app | unklar | ermittlung fehlgeschlagen: fremder host | https://www.docusign.com/blog/developers/docusign-mcp-server-general-availability | 2026-11-10 |
| domotz-preview | app | unklar | ermittlung fehlgeschlagen: resource passt nicht | https://help.domotz.com/integrations/domotz-mcp-server-setup/ | 2026-11-10 |
| dovetail | app | braucht_registrierung | oauth_ohne_dcr | https://developers.dovetail.com/docs/mcp | 2026-11-10 |
| dow-jones-factiva | app | kein_offizieller_server | Kein offizieller Remote-MCP-Server dokumentiert gefunden (Factiva nur als ChatGPT-App gelistet). |  |  |
| egnyte | app | vorlage | einrichtbar_oauth | https://developers.egnyte.com/docs/read/Remote_MCP_Server | 2026-11-10 |
| expo | skills | skills_teil_5 | nur Skills/Befehle/Agenten |  |  |
| factset | app | vorlage | einrichtbar_oauth | https://developer.factset.com/mcp/factset-ai-ready-data-mcp | 2026-11-10 |
| fal | app | vorlage | einrichtbar_oauth | https://fal.ai/docs/documentation/setting-up/mcp/other-clients | 2026-11-10 |
| figma | mcp | unklar | ermittlung fehlgeschlagen: fremder host | https://developers.figma.com/docs/figma-mcp-server/remote-server-installation | 2026-11-10 |
| finn | app | kein_offizieller_server | Anbieter nicht eindeutig aufloesbar; kein offizieller Remote-MCP-Server gefunden (nur Community-Server fuer FINN.no). |  |  |
| fireflies | app | vorlage | einrichtbar_oauth | https://docs.fireflies.ai/getting-started/mcp-configuration | 2026-11-10 |
| fiscal-ai | app | vorlage | einrichtbar_oauth | https://docs.fiscal.ai/docs/guides/mcp-integration | 2026-11-10 |
| fyxer | app | unklar | ermittlung fehlgeschlagen: resource passt nicht | https://fyxer.support.site/article/fyxer-mcp | 2026-11-10 |
| game-studio | skills | skills_teil_5 | nur Skills/Befehle/Agenten |  |  |
| github | mcp | vorlage_bestand | Vorlage liegt schon vor |  |  |
| glean | app | adresse_je_kunde | adresse_je_kunde | https://docs.glean.com/administration/platform/mcp/create-mcp-servers | 2026-11-10 |
| gmail | app | vorlage_mitgeliefert | mitgelieferte Vorlage bleibt | https://developers.google.com/workspace/guides/configure-mcp-servers | 2026-11-10 |
| google-calendar | app | vorlage_mitgeliefert | mitgelieferte Vorlage bleibt | https://developers.google.com/workspace/guides/configure-mcp-servers | 2026-11-10 |
| google-drive | app | vorlage_mitgeliefert | mitgelieferte Vorlage bleibt | https://developers.google.com/workspace/guides/configure-mcp-servers | 2026-11-10 |
| govtribe | app | unklar | ermittlung fehlgeschlagen: anmeldeserver-metadaten nicht lesbar | https://govtribe.com/docs/govtribe-user-guide/govtribe-mcp | 2026-11-10 |
| granola | app | vorlage | einrichtbar_oauth | https://docs.granola.ai/help-center/sharing/integrations/mcp | 2026-11-10 |
| happenstance | app | unklar | ermittlung fehlgeschlagen: issuer passt nicht | https://developer.happenstance.ai/mcp/connect | 2026-11-10 |
| hebbia | app | kein_offizieller_server | Hebbia blog says a Hebbia MCP exists (Matrix, Projects) but no public endpoint URL found; likely customer-gated. |  |  |
| help-scout | app | vorlage | einrichtbar_oauth | https://docs.helpscout.com/article/1779-connect-your-ai-agent-with-help-scout-to-search-conversations-and-pull-reports | 2026-11-10 |
| hex | app | vorlage | einrichtbar_oauth | https://learn.hex.tech/docs/api-integrations/mcp-server | 2026-11-10 |
| heygen | app | vorlage | einrichtbar_oauth | https://developers.heygen.com/mcp/overview | 2026-11-10 |
| hg-insights | app | kein_offizieller_server | HG MCP Server exists (API key issued by admin) but endpoint URL not public; demo-gated. | https://hginsights.com/product/hg-mcp-server |  |
| highlevel | app | vorlage | einrichtbar_oauth; Endpunkt fuer Anthropic-Clients laut Doku; bei Fehler beim Einrichten pruefen | https://marketplace.gohighlevel.com/docs/other/mcp | 2026-11-10 |
| hostinger | app | vorlage | einrichtbar_oauth | https://www.hostinger.com/support/11079316-hostinger-api-mcp-server/ | 2026-11-10 |
| hubspot | app | braucht_registrierung | oauth_ohne_dcr | https://developers.hubspot.com/docs/apps/developer-platform/build-apps/integrate-with-the-remote-hubspot-mcp-server | 2026-11-10 |
| hugging-face | app | vorlage | einrichtbar_oauth | https://huggingface.co/docs/hub/agents-mcp | 2026-11-10 |
| hyperframes | skills | skills_teil_5 | nur Skills/Befehle/Agenten |  |  |
| intercom | app | vorlage | einrichtbar_oauth | https://developers.intercom.com/docs/guides/mcp | 2026-11-10 |
| jam | app | vorlage | einrichtbar_oauth | https://jam.dev/docs/debug-a-jam/mcp | 2026-11-10 |
| keybid-puls | app | kein_offizieller_server | Could not identify vendor or any MCP server. |  |  |
| life-science-research | skills | skills_teil_5 | nur Skills/Befehle/Agenten |  |  |
| linear | mcp | vorlage | einrichtbar_oauth | https://linear.app/docs/mcp | 2026-11-10 |
| lovable | app | vorlage | einrichtbar_oauth | https://docs.lovable.dev/integrations/lovable-mcp-server | 2026-11-10 |
| lseg | app | kein_offizieller_server | LSEG operates an official MCP server (Claude, ChatGPT, Copilot etc., licensed LSEG credentials required) but no URL on lseg.com found; aggregators list https://api.analytics.lseg.com/lfa/mcp/server-cl (unverified). | https://www.lseg.com/en/solutions/ai-finance-solutions/lseg-mcp |  |
| magicpath | skills | skills_teil_5 | nur Skills/Befehle/Agenten |  |  |
| marcopolo | app | vorlage | einrichtbar_oauth | https://docs.marcopolo.dev/getting-started | 2026-11-10 |
| mem | app | unklar | ermittlung fehlgeschlagen: fremder host | https://docs.mem.ai/mcp/setup.md | 2026-11-10 |
| meticulate | app | kein_offizieller_server | AI sales prospecting startup (meticulate.ai); no MCP server documentation found. |  |  |
| midpage | app | vorlage | einrichtbar_oauth | https://docs.midpage.ai/documentation/integration/mcp-tools | 2026-11-10 |
| mixpanel | app | unklar | ermittlung fehlgeschlagen: fremder host | https://docs.mixpanel.com/docs/mcp | 2026-11-10 |
| mixpanel-headless | skills | skills_teil_5 | nur Skills/Befehle/Agenten |  |  |
| monday-com | app | unklar | ermittlung fehlgeschlagen: anmeldeserver-metadaten nicht lesbar | https://developer.monday.com/api-reference/docs/integrate-with-monday-mcp | 2026-11-10 |
| moody-s | app | unklar | ermittlung fehlgeschlagen: resource passt nicht | https://www.moodys.com/web/en/us/genai/model-context-protocol.html | 2026-11-10 |
| morningstar | app | kein_offizieller_server | Morningstar runs an MCP server (OAuth, license required); aggregators cite https://mcp.morningstar.com/mcp but I could not confirm the URL on a Morningstar-owned page. | https://learn.microsoft.com/en-us/connectors/morningstar/ |  |
| motherduck | app | vorlage | einrichtbar_oauth | https://motherduck.com/docs/sql-reference/mcp/ | 2026-11-10 |
| mt-newswires | app | kein_offizieller_server | Official hosted remote MCP server exists (login with MT Newswires credentials via connector directories) but the URL is not published on its site; aggregators list https://mcp.mtnewswires.com/mcp (unverified). | https://www.mtnewswires.com/mcp-server |  |
| myregistry-com | app | kein_offizieller_server | ChatGPT plugin exists (member login) but no public MCP server documentation found. |  |  |
| neon-postgres | app | vorlage | einrichtbar_oauth | https://neon.com/docs/ai/neon-mcp-server | 2026-11-10 |
| netlify | app | vorlage | einrichtbar_oauth | https://docs.netlify.com/build/build-with-ai/netlify-mcp-server/ | 2026-11-10 |
| network-solutions | app | kein_offizieller_server | Has a ChatGPT app listing but no public MCP server documentation found. |  |  |
| ngs-analysis | skills | skills_teil_5 | nur Skills/Befehle/Agenten |  |  |
| notion | mcp | vorlage | einrichtbar_oauth | https://developers.notion.com/docs/get-started-with-mcp | 2026-11-10 |
| nvidia | skills | skills_teil_5 | nur Skills/Befehle/Agenten |  |  |
| omni-analytics | app | vorlage | einrichtbar_oauth | https://docs.omni.co/ai/mcp/cursor | 2026-11-10 |
| openai-ads-conversions | skills | skills_teil_5 | nur Skills/Befehle/Agenten |  |  |
| openai-developers | mcp | vorlage | einrichtbar_ohne_anmeldung | https://developers.openai.com/learn/docs-mcp | 2026-11-10 |
| otter-ai | app | unklar | ermittlung fehlgeschlagen: resource passt nicht | https://help.otter.ai/hc/en-us/articles/35287607569687-Otter-MCP-Server | 2026-11-10 |
| outlook-calendar | app | adresse_je_kunde | adresse_je_kunde | https://learn.microsoft.com/en-us/microsoft-copilot-studio/mcp-calendar-tools | 2026-11-10 |
| outlook-email | app | adresse_je_kunde | adresse_je_kunde | https://learn.microsoft.com/en-us/microsoft-copilot-studio/mcp-mail-tools | 2026-11-10 |
| outreach | app | vorlage | einrichtbar_oauth | https://support.outreach.io/support/solutions/articles/159000435681-use-outreach-mcp-with-claude | 2026-11-10 |
| particl-market-research | app | vorlage | einrichtbar_schluessel | https://particl.com/docs/mcp/quickstart | 2026-11-10 |
| picsart | app | vorlage | einrichtbar_oauth | https://picsart.com/gen-ai-mcp | 2026-11-10 |
| pipedrive | app | kein_offizieller_server | Official native MCP exists (Claude connector with Pipedrive sign-in), but the URL appears only on third-party directories (mcp.pipedrive.ai/mcp), not confirmed on Pipedrive's own pages. | https://support.pipedrive.com/en/article/mcp |  |
| pitchbook | app | kein_offizieller_server | Official PitchBook Premium Connector (SSO, licence required) exists, but URL (premium.mcp.pitchbook.com/mcp) only seen on third-party directories. | https://pitchbook.com/help/pitchbook-premium-connector-for-claude-by-anthropic |  |
| plugin-eval | skills | skills_teil_5 | nur Skills/Befehle/Agenten |  |  |
| policynote | app | kein_offizieller_server | FiscalNote announced native MCP for PolicyNote API but no endpoint URL found in accessible docs (product page returned 403). | https://fiscalnote.com/products/policynote-mcp |  |
| posthog | app | vorlage | einrichtbar_oauth | https://posthog.com/docs/model-context-protocol | 2026-11-10 |
| pylon | app | vorlage | einrichtbar_oauth | https://docs.usepylon.com/pylon-docs/integrations/mcp | 2026-11-10 |
| quartr | app | vorlage | einrichtbar_oauth | https://mcp.quartr.com/docs | 2026-11-10 |
| quickbooks | app | unklar | ermittlung fehlgeschlagen: resource passt nicht | https://github.com/IntuitDeveloper/intuit-3p-ai-pilot | 2026-11-10 |
| quicknode | app | vorlage | einrichtbar_oauth | https://www.quicknode.com/docs/build-with-ai/quicknode-mcp | 2026-11-10 |
| ranked-ai | app | vorlage | einrichtbar_oauth | https://www.ranked.ai/developers/mcp/setup.md | 2026-11-10 |
| razorpay | app | unklar | ermittlung fehlgeschlagen: resource passt nicht | https://razorpay.com/docs/mcp-server/remote/ | 2026-11-10 |
| read-ai | app | unklar | ermittlung fehlgeschlagen: fremder host | https://support.read.ai/hc/en-us/articles/49381158409491-MCP-Server | 2026-11-10 |
| readwise | app | vorlage | einrichtbar_oauth | https://docs.readwise.io/tools/mcp | 2026-11-10 |
| remotion | skills | skills_teil_5 | nur Skills/Befehle/Agenten |  |  |
| render | skills | skills_teil_5 | nur Skills/Befehle/Agenten |  |  |
| replayio | app | unklar | antwortet_ohne_anmeldung | https://docs.replay.io/basics/replay-mcp/quickstart | 2026-11-10 |
| replit | app | vorlage | einrichtbar_oauth | https://docs.replit.com/platforms/mcp-server | 2026-11-10 |
| responsive | app | vorlage | einrichtbar_oauth | https://help.responsive.io/en-US/responsive/article/duV3ckq5-using-responsive-mcp-server-with-generative-ai-tools | 2026-11-10 |
| rox | app | kein_offizieller_server | No official Rox MCP server documentation found. |  |  |
| s-p | app | unklar | ermittlung fehlgeschlagen: issuer passt nicht | https://docs.kensho.com/adaptive-retrieval/mcp | 2026-11-10 |
| scite | app | vorlage | einrichtbar_oauth | https://docs.scite.ai/mcp/overview | 2026-11-10 |
| semrush | app | unklar | ermittlung fehlgeschlagen: fremder host | https://developer.semrush.com/api/basics/semrush-mcp/ | 2026-11-10 |
| sendgrid | app | kein_offizieller_server | Only community/local servers found; no official SendGrid remote MCP server. |  |  |
| sentry | skills | skills_teil_5 | nur Skills/Befehle/Agenten |  |  |
| setu-bharat-connect-billpay | app | kein_offizieller_server | Setu says it built a Bill Payments MCP server but publishes no endpoint URL or auth details (contact sales). | https://setu.co/real-time-payments/bbps/agentic-bill-payments |  |
| sharepoint | app | unklar | ermittlung fehlgeschlagen: issuer passt nicht | https://learn.microsoft.com/en-us/microsoft-365/copilot/extensibility/work-iq/mcp/quickstart/github-copilot-cli | 2026-11-10 |
| shopify | app | adresse_je_kunde | adresse_je_kunde | https://shopify.dev/docs/apps/build/storefront-mcp | 2026-11-10 |
| shutterstock | app | kein_offizieller_server | Only third-party/community servers found. |  |  |
| signnow | app | vorlage | einrichtbar_oauth | https://docs.signnow.com/docs/mcp-server | 2026-11-10 |
| similarweb | app | unklar | anmeldeart_unbekannt | https://developers.similarweb.com/docs/similarweb-mcp | 2026-11-10 |
| skywatch | app | vorlage | einrichtbar_ohne_anmeldung | https://docs.skywatch.com/docs/mcp/client-integration | 2026-11-10 |
| slack | app | vorlage_mitgeliefert | mitgelieferte Vorlage bleibt | https://docs.slack.dev/ai/mcp-server | 2026-11-10 |
| statsig | app | vorlage | einrichtbar_oauth | https://docs.statsig.com/integrations/mcp/manual-setup | 2026-11-10 |
| streak | app | kein_offizieller_server | Only community and aggregator servers found. |  |  |
| stripe | app | unklar | ermittlung fehlgeschlagen: anmeldeserver-metadaten nicht lesbar | https://docs.stripe.com/mcp | 2026-11-10 |
| supabase | app | vorlage | einrichtbar_oauth | https://supabase.com/docs/guides/getting-started/mcp | 2026-11-10 |
| superhuman | app | vorlage | einrichtbar_oauth | https://help.superhuman.com/hc/en-us/articles/49810745762067-Superhuman-Mail-MCP-Server-Beta | 2026-11-10 |
| superpowers | skills | skills_teil_5 | nur Skills/Befehle/Agenten |  |  |
| taxdown | app | kein_offizieller_server | No official MCP documentation found (only a ChatGPT app listing by TAXDOWN S.L. in a third-party tracker). |  |  |
| teams | app | unklar | ermittlung fehlgeschlagen: issuer passt nicht | https://learn.microsoft.com/en-us/microsoft-365/copilot/extensibility/work-iq/mcp/quickstart/github-copilot-cli | 2026-11-10 |
| teamwork-com | app | vorlage | einrichtbar_oauth | https://github.com/Teamwork/mcp | 2026-11-10 |
| temporal | skills | skills_teil_5 | nur Skills/Befehle/Agenten |  |  |
| test-android-apps | skills | skills_teil_5 | nur Skills/Befehle/Agenten |  |  |
| third-bridge | app | kein_offizieller_server | Vendor announced an MCP (client/trial access only) but publishes no URL; only third-party listings show ai.thirdbridge.com/mcp/sse, so left empty. | https://www.thirdbridge.com/en-us/about-us/media/news/third-bridge-launches-model-context-protocol-mcp |  |
| thoughtspot | app | unklar | ermittlung fehlgeschlagen: schutzbeschreibung nicht lesbar | https://developers.thoughtspot.com/docs/mcp-integration | 2026-11-10 |
| tinman-ai | app | kein_offizieller_server | Could not identify the vendor or any MCP server. |  |  |
| twilio-developer-kit | skills | skills_teil_5 | nur Skills/Befehle/Agenten |  |  |
| united-rentals | app | kein_offizieller_server | No official MCP server found. |  |  |
| vantage | app | unklar | ermittlung fehlgeschlagen: schutzbeschreibung nicht lesbar | https://docs.vantage.sh/vantage_mcp | 2026-11-10 |
| vercel | app | vorlage_bestand | Vorlage liegt schon vor |  |  |
| waldo | app | kein_offizieller_server | No official Waldo MCP server found (Waldo docs now live under Tricentis). |  |  |
| weatherpromise | app | kein_offizieller_server | No MCP server found for this weather-insurance vendor. |  |  |
| windsor-ai | app | vorlage | einrichtbar_oauth | https://windsor.ai/documentation/windsor-mcp/how-to-connect-windsor-mcp-to-any-ai-client/ | 2026-11-10 |
| wix | app | vorlage | einrichtbar_oauth | https://dev.wix.com/docs/sdk/articles/use-the-wix-mcp/about-the-wix-mcp | 2026-11-10 |
| yepcode | app | vorlage | einrichtbar_oauth | http://yepcode.io/docs/mcp-server/quickstart/ | 2026-11-10 |
| zoho | app | kein_offizieller_server | Official hosted Zoho MCP (mcp.zoho.com, regional domains) but each server gets a unique per-user URL from the console Connect tab, so no fixed URL. | https://www.zoho.com/analytics/api/v2/zoho-analytics-mcp-server/remote-mcp/zoho-mcp.html |  |
| zoom | app | unklar | ermittlung fehlgeschlagen: resource passt nicht | https://developers.zoom.us/docs/mcp/servers/ | 2026-11-10 |
| zoominfo | app | unklar | ermittlung fehlgeschlagen: fremder host | https://docs.gtm.ai/docs/connect-to-zoominfo-mcp | 2026-11-10 |
| zotero | skills | skills_teil_5 | nur Skills/Befehle/Agenten |  |  |
