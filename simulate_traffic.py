"""
TIM Tunisia — AI Gateway Demo Simulator
=========================================
Generates realistic traffic for TIM's two products:
  - QALITAS (QHSE management — quality, health, safety, environment)
  - GMAO Pro (maintenance management with AI)

Three real TIM clients are simulated as tenants:
  - caveo_automotive   (IATF 16949, ISO 14001, ISO 45001)
  - seamtech_lakatech  (ISO 9001, electronics manufacturing)
  - general_beton      (ISO 9001, concrete/BTP)

Usage:
  # Simulate traffic for one tenant
  python simulate_traffic.py --tenant caveo_automotive --calls 10

  # Full demo seed: create tenants + contracts + rules + traffic for all 3
  python simulate_traffic.py --seed-all

  # Traffic only for all 3 tenants
  python simulate_traffic.py --all-tenants --calls 8
"""

import os
import sys
import time
import random
import asyncio
import argparse
import uuid
from decimal import Decimal
from dotenv import load_dotenv
import httpx

# Add project root to path for direct DB imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from groq import AsyncGroq

load_dotenv()

groq_client = AsyncGroq(api_key=os.getenv("GROQ_API_KEY", ""))

API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000/v1")
ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASS = os.getenv("ADMIN_PASS", "Admin@1234")

MODEL = "llama-3.3-70b-versatile"
PROVIDER = "groq"

# ============================================================
# DOMAIN-SPECIFIC PROMPTS — Real QALITAS & GMAO Pro language
# ============================================================

QALITAS_AGENTS = {
    "nc_classifier": {
        "module": "QALITAS",
        "prompts": [
            "Classifier cette non-conformité et proposer des actions correctives IATF 16949 : Lot 2024-B47 de ressorts de suspension — dureté Brinell mesurée à 215 HB au lieu de 230±10 HB. Fournisseur : ACIER-TN.",
            "Analyser cette NC produit : contrôle dimensionnel pièce réf. SUS-4420, diamètre extérieur 42.3mm hors tolérance (spéc: 42.0±0.1mm). Ligne de production L3, poste de tournage T-07.",
            "Classifier le type de non-conformité : réclamation client STELLANTIS — bruit anormal sur lot de 500 amortisseurs livrés le 12/04. Retour terrain après 2000 km.",
            "NC détectée lors de l'audit interne processus traitement thermique : absence d'enregistrement de température pour le four F-02 entre 14h et 16h le 28/04. Évaluer la gravité.",
        ],
    },
    "audit_report_gen": {
        "module": "QALITAS",
        "prompts": [
            "Préparer la réponse à la question d'audit IATF 8.5.1 : Comment l'organisme maîtrise-t-il les conditions de production et de prestation de services pour les ressorts hélicoïdaux ?",
            "Générer la réponse d'audit ISO 9001 clause 7.1.5 : Comment assurez-vous la traçabilité métrologique des instruments de mesure sur la ligne de contrôle qualité ?",
            "Rédiger la réponse audit ISO 14001 clause 6.1.2 : Quels sont les aspects environnementaux significatifs identifiés pour le processus de traitement de surface ?",
            "Préparer la preuve de conformité pour l'audit IATF 10.2.3 : Démontrer l'application de la méthode 8D pour le traitement des réclamations client.",
        ],
    },
    "risk_analyzer": {
        "module": "QALITAS",
        "prompts": [
            "Analyser les données AMDEC du processus de traitement thermique et identifier les modes de défaillance avec un IPR supérieur à 100. Proposer des actions de réduction.",
            "Effectuer une analyse SWOT pour le département qualité : forces — certification IATF, faiblesse — turnover techniciens, opportunité — nouveau contrat Stellantis, menace — hausse coût matière première.",
            "Évaluer les risques et opportunités liés à l'intégration d'un nouveau fournisseur de fil d'acier pour la production de ressorts. Critères : qualité, délai, coût, conformité.",
        ],
    },
    "revue_direction": {
        "module": "QALITAS",
        "prompts": [
            "Générer la synthèse de revue de direction Q1-2026 : 47 NC traitées, taux de clôture 89%, 3 réclamations clients, satisfaction client 4.2/5, 2 audits internes réalisés, 0 accident de travail.",
            "Préparer les données d'entrée de la revue de direction : évolution des indicateurs processus production — TRS 87%, taux de rebut 1.2%, OTD 96%, coût de non-qualité 12,400 TND.",
        ],
    },
    "supplier_evaluator": {
        "module": "QALITAS",
        "prompts": [
            "Synthétiser l'évaluation du fournisseur ACIER-TN ce trimestre : 3 lots reçus, 1 non-conforme (composition chimique hors spec), délai moyen 12 jours vs 10 contractuels. Note passée de 85/100 à 72/100.",
            "Évaluer la performance fournisseur PLASTIK-MED : 8 livraisons, 0 NC, respect délais 100%, prix compétitif. Recommander le maintien ou l'upgrade de sa classification.",
        ],
    },
}

GMAO_AGENTS = {
    "panne_diagnostic": {
        "module": "GMAO_PRO",
        "prompts": [
            "Diagnostic panne : Presse hydraulique PH-003, pression insuffisante (150 bar au lieu de 250 bar), bruit anormal côté pompe. Historique : remplacement joints il y a 6 mois, vidange huile il y a 3 mois.",
            "Analyser la panne : Convoyeur CV-12 arrêt intempestif, variateur de fréquence en alarme surcharge. Température moteur 95°C. Dernière maintenance préventive il y a 45 jours.",
            "Diagnostic : Tour CNC T-07, vibrations excessives sur axe Z, pièces hors tolérance depuis ce matin. Changement de broche effectué il y a 2 mois. Identifier la cause probable.",
            "Panne critique : Four de traitement thermique F-02, régulation température défaillante — oscillation ±15°C autour de la consigne 850°C. Impact sur la qualité des pièces en cours.",
        ],
    },
    "bon_travail_gen": {
        "module": "GMAO_PRO",
        "prompts": [
            "Générer un bon de travail à partir de cette déclaration mobile : Fuite huile hydraulique sur convoyeur CV-12, signalée par le technicien Mohamed Ben Ali via l'application GMAO Pro, priorité haute, zone atelier B.",
            "Créer BT maintenance préventive : Révision complète compresseur CP-001, compteur à 8,450 heures, seuil constructeur 10,000h. Inclure : vidange, filtres, courroies, contrôle étanchéité.",
            "Générer BT correctif urgent : Pompe de refroidissement PR-05 — débit insuffisant, température liquide de coupe à 32°C vs 20°C nominal. Risque d'arrêt ligne L2.",
        ],
    },
    "maintenance_predict": {
        "module": "GMAO_PRO",
        "prompts": [
            "Analyser les compteurs du compresseur CP-001 : 8,450 heures de fonctionnement, dernière révision à 6,000h, seuil constructeur 10,000h. Recommander un planning de maintenance préventive.",
            "Prédiction maintenance : Robot soudure RS-03, 125,000 cycles effectués, historique pannes — remplacement câble énergie à 80K et 110K cycles. Prochaine intervention recommandée ?",
            "Analyser l'historique des pannes du groupe électrogène GE-01 sur 12 mois et identifier les patterns récurrents. 7 interventions correctives enregistrées.",
        ],
    },
    "stock_optimizer": {
        "module": "GMAO_PRO",
        "prompts": [
            "Optimiser le stock pièces de rechange pour la ligne de suspension : consommation joints toriques réf JT-42 = 45 unités/mois, stock actuel = 12, délai fournisseur = 15 jours. Calculer le point de commande.",
            "Analyser la consommation de filtres hydrauliques sur 6 mois et proposer un niveau de stock optimisé. Consommation : Jan=8, Fev=6, Mar=12, Avr=7, Mai=9, Jun=14.",
        ],
    },
}

ALL_AGENTS = {**QALITAS_AGENTS, **GMAO_AGENTS}

# ============================================================
# TENANT DEFINITIONS
# ============================================================

TENANTS = {
    "caveo_automotive": {
        "name": "CAVEO Automotive",
        "tier": "enterprise",
        "budget": 500.0,
        "contact": "anis.ezzine@caveo.tn",
        "contract": {"type": "forfait", "base_fee": 200.0, "tokens": 5_000_000, "overage": 0.03},
        "agents": ["nc_classifier", "audit_report_gen", "risk_analyzer", "panne_diagnostic", "bon_travail_gen", "maintenance_predict"],
        "weight": 0.45,  # gets 45% of traffic
    },
    "seamtech_lakatech": {
        "name": "SEAMTECH - LAKATECH",
        "tier": "professional",
        "budget": 300.0,
        "contact": "supply.chain@seamtech.tn",
        "contract": {"type": "forfait", "base_fee": 150.0, "tokens": 3_000_000, "overage": 0.025},
        "agents": ["nc_classifier", "audit_report_gen", "supplier_evaluator", "stock_optimizer"],
        "weight": 0.35,
    },
    "general_beton": {
        "name": "General Beton",
        "tier": "pay_as_you_go",
        "budget": 150.0,
        "contact": "qualite@generalbeton.tn",
        "contract": {"type": "pay_as_you_go", "base_fee": 0.0, "tokens": 0, "overage": 0.0},
        "agents": ["nc_classifier", "revue_direction", "audit_report_gen"],
        "weight": 0.20,
    },
}

GOVERNANCE_RULES = [
    {"rule_type": "budget_cap",      "tenant_id": "general_beton",     "monthly_budget_usd": 150, "priority": 200, "description": "General Beton — budget plafond $150/mois (pay-as-you-go)"},
    {"rule_type": "tenant_limit",    "tenant_id": "general_beton",     "daily_token_limit": 300_000, "priority": 150, "description": "General Beton — limite 300K tokens/jour"},
    {"rule_type": "budget_cap",      "tenant_id": "caveo_automotive",  "monthly_budget_usd": 500, "priority": 200, "description": "CAVEO Automotive — budget plafond $500/mois (forfait enterprise)"},
    {"rule_type": "tenant_limit",    "tenant_id": "seamtech_lakatech", "daily_token_limit": 500_000, "priority": 150, "description": "SEAMTECH — limite 500K tokens/jour"},
    {"rule_type": "model_block",     "tenant_id": None, "model_name": "gpt-4", "priority": 300, "description": "Bloquer GPT-4 globalement — trop cher, utiliser Groq/Llama à la place"},
]

# ============================================================
# HTTP HELPERS
# ============================================================

async def authenticate() -> str:
    async with httpx.AsyncClient(timeout=60) as c:
        resp = await c.post(f"{API_BASE_URL}/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASS})
        resp.raise_for_status()
        return resp.json()["access_token"]

async def api_post(token: str, path: str, data: dict) -> dict | None:
    async with httpx.AsyncClient(timeout=120) as c:
        try:
            resp = await c.post(f"{API_BASE_URL}{path}", headers={"Authorization": f"Bearer {token}"}, json=data)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            print(f"  [WARN] {path} -> HTTP {e.response.status_code}: {e.response.text[:200]}")
            return None
        except Exception as e:
            print(f"  [ERROR] {path} -> {e}")
            return None

async def api_get(token: str, path: str) -> dict | None:
    async with httpx.AsyncClient(timeout=15) as c:
        try:
            resp = await c.get(f"{API_BASE_URL}{path}", headers={"Authorization": f"Bearer {token}"})
            resp.raise_for_status()
            return resp.json()
        except Exception:
            return None

# ============================================================
# GROQ LLM CALL
# ============================================================

async def make_groq_call(prompt: str) -> dict:
    start = time.time()
    try:
        response = await groq_client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=512,
        )
        latency_ms = int((time.time() - start) * 1000)
        usage = response.usage
        completion = response.choices[0].message.content if response.choices else ""
        return {
            "success": True,
            "in_tokens": usage.prompt_tokens if usage else len(prompt) // 4,
            "out_tokens": usage.completion_tokens if usage else 50,
            "latency_ms": latency_ms,
            "completion": completion,
            "error": None,
        }
    except Exception as e:
        return {
            "success": False,
            "in_tokens": len(prompt) // 4,
            "out_tokens": 0,
            "latency_ms": int((time.time() - start) * 1000),
            "completion": "",
            "error": str(e),
        }

# ============================================================
# SEED: Create tenants, contracts, governance rules
# ============================================================

async def seed_tenants(token: str):
    print("\n== Creating Tenants ==")
    for tid, cfg in TENANTS.items():
        result = await api_post(token, "/tenants", {
            "tenant_id": tid, "name": cfg["name"], "tier": cfg["tier"],
            "monthly_budget_usd": cfg["budget"], "contact_email": cfg["contact"],
        })
        status = "[OK]" if result else "[WARN] (may already exist)"
        print(f"  {tid}: {status}")

async def seed_contracts():
    """Create billing contracts directly via DB (no REST endpoint for this)."""
    print("\n== Creating Billing Contracts ==")
    try:
        from core.database import AsyncSessionLocal
        from modules.billing.repositories.contract_repository import ContractRepository

        async with AsyncSessionLocal() as session:
            repo = ContractRepository(session)
            for tid, cfg in TENANTS.items():
                ct = cfg["contract"]
                await repo.upsert(
                    tenant_id=tid,
                    contract_type=ct["type"],
                    base_fee_usd=ct["base_fee"],
                    forfait_tokens=ct["tokens"],
                    overage_rate_per_1k=ct["overage"],
                    description=f"Contrat {ct['type']} — {cfg['name']}",
                )
                print(f"  {tid} ({ct['type']}): [OK]")
            await session.commit()
    except Exception as e:
        print(f"  [WARN] Contract seeding failed: {e}")
        print("  -> Contracts will be auto-created as pay_as_you_go on first invoice")

async def seed_governance(token: str):
    print("\n== Creating Governance Rules ==")
    for rule in GOVERNANCE_RULES:
        result = await api_post(token, "/gateway/governance/rules", rule)
        desc = rule["description"][:60]
        status = "[OK]" if result else "[WARN]"
        print(f"  [OK] {status} {desc}")

async def seed_anomalies():
    """Seed fake baselines and anomalies for demo visualization."""
    print("\n== Creating Baselines & Anomalies ==")
    try:
        from core.database import AsyncSessionLocal
        from modules.detection.models import LLMTokenBaseline, LLMAnomalyRecord
        from datetime import datetime, timezone, timedelta
        import uuid

        async with AsyncSessionLocal() as session:
            # 1. Create a baseline for CAVEO
            baseline = LLMTokenBaseline(
                tenant_id="caveo_automotive",
                agent_id="*",
                model="*",
                daily_mean=150000.0,
                daily_std_dev=20000.0,
                daily_min=100000.0,
                daily_max=200000.0,
                computed_at=datetime.now(timezone.utc),
            )
            session.add(baseline)
            
            # 2. Create a 'CRITICAL' token spike anomaly for CAVEO
            anomaly1 = LLMAnomalyRecord(
                tenant_id="caveo_automotive",
                anomaly_type="token_spike",
                severity="critical",
                detector_votes="stl,isolation_forest,cusum",
                vote_count=3,
                observed_value=450000.0,
                baseline_mean=150000.0,
                baseline_std_dev=20000.0,
                description="CRITICAL: today's usage (450,000 tokens) is 200% above baseline (150,000). Detectors: [stl, isolation_forest, cusum]. Votes: 3/3.",
                detected_at=datetime.now(timezone.utc) - timedelta(hours=2),
            )
            session.add(anomaly1)

            # 3. Create a 'WARNING' cost spike for SEAMTECH
            anomaly2 = LLMAnomalyRecord(
                tenant_id="seamtech_lakatech",
                anomaly_type="cost_spike",
                severity="warning",
                detector_votes="isolation_forest",
                vote_count=1,
                observed_value=12.5,
                baseline_mean=4.2,
                baseline_std_dev=1.5,
                description="WARNING: cost ($12.50) is significantly higher than daily average ($4.20). Potential model misuse detected.",
                detected_at=datetime.now(timezone.utc) - timedelta(hours=5),
            )
            session.add(anomaly2)

            await session.commit()
            print("  Baselines & Anomalies: [OK]")
    except Exception as e:
        print(f"  [WARN] Anomaly seeding failed: {e}")

# ============================================================
# TRAFFIC SIMULATION
# ============================================================

async def run_traffic(token: str, tenant_id: str, num_calls: int):
    cfg = TENANTS[tenant_id]
    agents = cfg["agents"]
    session_id = f"sess_{tenant_id}_{uuid.uuid4().hex[:8]}"

    print(f"\n== Simulating {num_calls} calls for {cfg['name']} ==")
    print(f"   Agents: {', '.join(agents)}")
    print(f"   Session: {session_id}\n")

    for i in range(num_calls):
        agent_id = random.choice(agents)
        agent_cfg = ALL_AGENTS[agent_id]
        prompt = random.choice(agent_cfg["prompts"])
        module = agent_cfg["module"]

        print(f"  [{i+1}/{num_calls}] {agent_id} -> '{prompt[:50]}...'")

        # Real Groq call
        result = await make_groq_call(prompt)

        in_tok = result["in_tokens"]
        out_tok = result["out_tokens"]
        cost = float(round((in_tok * 0.0005 + out_tok * 0.0015) / 1000, 8))

        payload = {
            "tenant_id": tenant_id,
            "model": MODEL,
            "provider": PROVIDER,
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "total_tokens": in_tok + out_tok,
            "cost_usd": cost,
            "agent_id": agent_id,
            "module": module,
            "duration_ms": result["latency_ms"],
            "status": "success" if result["success"] else "error",
            "error_message": result["error"],
            "prompt_text": prompt,
            "completion_text": result["completion"][:5000] if result["completion"] else None,
            "session_id": session_id,
            "metadata": {"source": "simulate_traffic", "agent": agent_id},
        }

        gw = await api_post(token, "/gateway/log", payload)
        if gw:
            decision = gw.get("decision", "?")
            icon = "[OK]" if decision == "allow" else ("[DOWN]" if "downgrade" in decision else "[BLOCK]")
            print(f"    {icon} {decision.upper()} | in={in_tok} out={out_tok} lat={result['latency_ms']}ms")

        await asyncio.sleep(random.uniform(0.8, 2.5))

# ============================================================
# MAIN
# ============================================================

async def main():
    parser = argparse.ArgumentParser(
        description="TIM Tunisia — AI Gateway Demo Simulator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python simulate_traffic.py --seed-all              # Full demo setup
  python simulate_traffic.py --tenant caveo_automotive --calls 10
  python simulate_traffic.py --all-tenants --calls 8
  python simulate_traffic.py --seed-only              # Create data without traffic
        """,
    )
    parser.add_argument("--tenant", type=str, help="Single tenant ID to simulate")
    parser.add_argument("--calls", type=int, default=10, help="Number of calls per tenant")
    parser.add_argument("--all-tenants", action="store_true", help="Run traffic for all 3 tenants")
    parser.add_argument("--seed-all", action="store_true", help="Full demo: seed + traffic for all tenants")
    parser.add_argument("--seed-only", action="store_true", help="Only create tenants/contracts/rules, no traffic")
    args = parser.parse_args()

    print("=" * 60)
    print("  TIM TUNISIA — AI GATEWAY DEMO SIMULATOR")
    print("  QALITAS (QHSE) & GMAO Pro (Maintenance)")
    print("=" * 60)

    token = await authenticate()
    print("  [OK] Authenticated\n")

    # Seed if requested
    if args.seed_all or args.seed_only:
        await seed_tenants(token)
        await seed_contracts()
        await seed_governance(token)
        await seed_anomalies()
        if args.seed_only:
            print("\n[OK] Seeding complete. No traffic generated.")
            return

    # Determine which tenants to run
    if args.seed_all or args.all_tenants:
        targets = list(TENANTS.keys())
    elif args.tenant:
        if args.tenant not in TENANTS:
            print(f"Unknown tenant '{args.tenant}'. Available: {', '.join(TENANTS.keys())}")
            return
        targets = [args.tenant]
    else:
        targets = list(TENANTS.keys())

    # Run traffic
    for tid in targets:
        await run_traffic(token, tid, args.calls)

    print("\n" + "=" * 60)
    print("  [OK] SIMULATION COMPLETE")
    print("=" * 60)

if __name__ == "__main__":
    asyncio.run(main())
