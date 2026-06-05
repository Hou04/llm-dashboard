"""
Domain-Specific Prompts & Completions
======================================
Realistic QALITAS (QHSE) and GMAO Pro (Maintenance) content.
Used for both historical seeding and live simulation.
"""

import random

# ============================================================
# QALITAS — Quality, Health, Safety, Environment
# ============================================================

QALITAS_PROMPTS = {
    "nc_classifier": [
        "Classifier cette non-conformité et proposer des actions correctives IATF 16949 : Lot 2024-B47 de ressorts de suspension — dureté Brinell mesurée à 215 HB au lieu de 230±10 HB. Fournisseur : ACIER-TN.",
        "Analyser cette NC produit : contrôle dimensionnel pièce réf. SUS-4420, diamètre extérieur 42.3mm hors tolérance (spéc: 42.0±0.1mm). Ligne de production L3, poste de tournage T-07.",
        "Classifier le type de non-conformité : réclamation client STELLANTIS — bruit anormal sur lot de 500 amortisseurs livrés le 12/04. Retour terrain après 2000 km.",
        "NC détectée lors de l'audit interne processus traitement thermique : absence d'enregistrement de température pour le four F-02 entre 14h et 16h le 28/04. Évaluer la gravité.",
        "Non-conformité fournisseur : lot de vis M8x40 réf. VIS-M8-SS — 12% des échantillons présentent une résistance à la traction inférieure à la spécification ISO 898-1.",
    ],
    "audit_report_gen": [
        "Préparer la réponse à la question d'audit IATF 8.5.1 : Comment l'organisme maîtrise-t-il les conditions de production et de prestation de services pour les ressorts hélicoïdaux ?",
        "Générer la réponse d'audit ISO 9001 clause 7.1.5 : Comment assurez-vous la traçabilité métrologique des instruments de mesure sur la ligne de contrôle qualité ?",
        "Rédiger la réponse audit ISO 14001 clause 6.1.2 : Quels sont les aspects environnementaux significatifs identifiés pour le processus de traitement de surface ?",
        "Préparer la preuve de conformité pour l'audit IATF 10.2.3 : Démontrer l'application de la méthode 8D pour le traitement des réclamations client.",
        "Rédiger le rapport de clôture pour l'audit ISO 45001 processus soudage : 2 NC mineures identifiées, 1 observation, plan d'action proposé.",
    ],
    "risk_analyzer": [
        "Analyser les données AMDEC du processus de traitement thermique et identifier les modes de défaillance avec un IPR supérieur à 100. Proposer des actions de réduction.",
        "Effectuer une analyse SWOT pour le département qualité : forces — certification IATF, faiblesse — turnover techniciens, opportunité — nouveau contrat Stellantis, menace — hausse coût matière première.",
        "Évaluer les risques et opportunités liés à l'intégration d'un nouveau fournisseur de fil d'acier pour la production de ressorts. Critères : qualité, délai, coût, conformité.",
    ],
    "revue_direction": [
        "Générer la synthèse de revue de direction Q1-2026 : 47 NC traitées, taux de clôture 89%, 3 réclamations clients, satisfaction client 4.2/5, 2 audits internes réalisés, 0 accident de travail.",
        "Préparer les données d'entrée de la revue de direction : évolution des indicateurs processus production — TRS 87%, taux de rebut 1.2%, OTD 96%, coût de non-qualité 12,400 TND.",
    ],
    "supplier_evaluator": [
        "Synthétiser l'évaluation du fournisseur ACIER-TN ce trimestre : 3 lots reçus, 1 non-conforme (composition chimique hors spec), délai moyen 12 jours vs 10 contractuels. Note 72/100.",
        "Évaluer la performance fournisseur PLASTIK-MED : 8 livraisons, 0 NC, respect délais 100%, prix compétitif. Recommander le maintien ou l'upgrade de sa classification.",
    ],
}

GMAO_PROMPTS = {
    "panne_diagnostic": [
        "Diagnostic panne : Presse hydraulique PH-003, pression insuffisante (150 bar au lieu de 250 bar), bruit anormal côté pompe. Historique : remplacement joints il y a 6 mois.",
        "Analyser la panne : Convoyeur CV-12 arrêt intempestif, variateur de fréquence en alarme surcharge. Température moteur 95°C. Dernière maintenance préventive il y a 45 jours.",
        "Diagnostic : Tour CNC T-07, vibrations excessives sur axe Z, pièces hors tolérance depuis ce matin. Changement de broche effectué il y a 2 mois.",
        "Panne critique : Four de traitement thermique F-02, régulation température défaillante — oscillation ±15°C autour de la consigne 850°C. Impact qualité pièces en cours.",
    ],
    "bon_travail_gen": [
        "Générer un bon de travail : Fuite huile hydraulique sur convoyeur CV-12, signalée par Mohamed Ben Ali via GMAO Pro, priorité haute, zone atelier B.",
        "Créer BT maintenance préventive : Révision complète compresseur CP-001, compteur à 8,450 heures, seuil constructeur 10,000h. Inclure : vidange, filtres, courroies.",
        "Générer BT correctif urgent : Pompe de refroidissement PR-05 — débit insuffisant, température liquide de coupe à 32°C vs 20°C nominal. Risque d'arrêt ligne L2.",
    ],
    "maintenance_predict": [
        "Analyser les compteurs du compresseur CP-001 : 8,450 heures de fonctionnement, dernière révision à 6,000h, seuil constructeur 10,000h. Planning maintenance préventive.",
        "Prédiction maintenance : Robot soudure RS-03, 125,000 cycles effectués, historique pannes — remplacement câble énergie à 80K et 110K cycles.",
        "Analyser l'historique des pannes du groupe électrogène GE-01 sur 12 mois. 7 interventions correctives enregistrées. Identifier patterns récurrents.",
    ],
    "stock_optimizer": [
        "Optimiser le stock pièces de rechange pour la ligne de suspension : consommation joints toriques réf JT-42 = 45 unités/mois, stock actuel = 12, délai fournisseur = 15 jours.",
        "Analyser la consommation de filtres hydrauliques sur 6 mois et proposer un niveau de stock optimisé. Consommation : Jan=8, Fev=6, Mar=12, Avr=7, Mai=9, Jun=14.",
    ],
}

ALL_PROMPTS = {**QALITAS_PROMPTS, **GMAO_PROMPTS}

# ============================================================
# SIMULATED COMPLETION TEXTS
# Realistic AI-generated responses for the Request Inspector
# ============================================================

COMPLETIONS = {
    "nc_classifier": [
        "**Classification NC** : Non-conformité Produit — Catégorie : Caractéristique dimensionnelle hors tolérance.\n\n**Gravité** : Majeure (impact client direct)\n**Type IATF** : NC Processus (clause 8.5.1)\n\n**Actions correctives proposées :**\n1. **Immédiat** : Tri 100% du lot B47, isolement des pièces non conformes\n2. **Cause racine** : Appliquer méthode 5 Pourquoi + diagramme Ishikawa\n3. **Préventif** : Recalibrage des instruments de mesure poste T-07, fréquence MSA augmentée\n4. **Fournisseur** : Émettre un SCAR (Supplier Corrective Action Request) vers ACIER-TN\n\n**Délai de clôture recommandé** : 15 jours ouvrés",
        "**Analyse NC Produit** : Pièce réf. SUS-4420\n\n**Résultat** : Diamètre extérieur 42.3mm — HORS TOLÉRANCE (spéc: 42.0 ± 0.1mm, écart: +0.2mm)\n\n**Impact** : Risque d'interférence d'assemblage sur le module de suspension\n\n**Cause probable** : Usure de l'outil de tournage T-07 (vérifier le compteur de pièces depuis dernier changement d'outil)\n\n**Actions** :\n1. Arrêt immédiat du poste T-07 pour vérification outil\n2. Contrôle rétroactif des 50 dernières pièces produites\n3. Mise à jour de la gamme de contrôle : ajout d'une mesure intermédiaire",
    ],
    "audit_report_gen": [
        "**Réponse Audit IATF 8.5.1 — Maîtrise de la production**\n\nL'organisme maîtrise les conditions de production des ressorts hélicoïdaux par :\n\n1. **Plans de surveillance** : Chaque référence dispose d'un plan de surveillance validé (réf. PS-RES-xxx) définissant les caractéristiques critiques, méthodes de contrôle et fréquences.\n\n2. **Instructions de travail** : Les postes sont équipés d'instructions visuelles (IT-PROD-xxx) mises à jour selon le processus de modification.\n\n3. **Qualification des équipements** : Les machines de formage (enrouleuses CNC) font l'objet d'une qualification initiale et d'une requalification annuelle.\n\n4. **Traçabilité** : Chaque lot est identifié par un code-barres liant matière première, paramètres process et résultats de contrôle.\n\n**Preuves disponibles** : PS-RES-4420, IT-PROD-087, PV qualification enrouleuse E-03 (mars 2026).",
    ],
    "risk_analyzer": [
        "**Analyse AMDEC — Processus Traitement Thermique**\n\n| Mode de défaillance | Gravité | Occurrence | Détection | IPR |\n|---|---|---|---|---|\n| Température four non atteinte | 8 | 4 | 3 | **96** |\n| Temps de maintien insuffisant | 9 | 3 | 4 | **108** ⚠️ |\n| Trempe retardée (>10s) | 9 | 2 | 5 | **90** |\n| Atmosphère four contaminée | 8 | 3 | 6 | **144** ⚠️ |\n| Mauvais positionnement pièces | 7 | 5 | 3 | **105** ⚠️ |\n\n**Actions prioritaires (IPR > 100)** :\n1. Atmosphère four : installer analyseur O2/CO en continu (IPR 144→72)\n2. Temps de maintien : alarme automatique PLC si Δt < consigne (IPR 108→36)\n3. Positionnement : gabarit de chargement standardisé + poka-yoké (IPR 105→35)",
    ],
    "panne_diagnostic": [
        "**Diagnostic — Presse Hydraulique PH-003**\n\n**Symptômes** : Pression insuffisante (150 bar vs 250 bar nominal), bruit anormal pompe\n\n**Diagnostic probable** :\n1. **Cause primaire (80%)** : Usure des palettes de la pompe à palettes — confirmé par le bruit et la perte de pression progressive\n2. **Cause secondaire (15%)** : Fuite interne sur distributeur principal — vérifier les joints toriques\n3. **Cause tertiaire (5%)** : Contamination huile hydraulique — analyser échantillon\n\n**Actions immédiates** :\n- Arrêt machine, consignation LOTO\n- Prélèvement huile pour analyse particules\n- Commander kit réparation pompe Rexroth réf. R900xxx\n\n**Pièces nécessaires** : Kit palettes pompe (stock magasin, réf. KP-PH003-02)\n**Temps d'intervention estimé** : 4-6 heures",
    ],
    "bon_travail_gen": [
        "**BON DE TRAVAIL — BT-2026-0487**\n\n**Type** : Correctif Urgent\n**Équipement** : Convoyeur CV-12 — Zone Atelier B\n**Priorité** : ★★★ HAUTE\n**Signalé par** : Mohamed Ben Ali (App GMAO Pro Mobile)\n**Date/Heure** : 01/06/2026 14:35\n\n**Description** : Fuite huile hydraulique détectée au niveau du vérin de tension. Flaque au sol (~2L estimé). Risque HSE : glissade.\n\n**Actions requises** :\n1. Consignation et balisage zone\n2. Identification source fuite (vérin / raccord / flexible)\n3. Remplacement composant défaillant\n4. Appoint huile hydraulique ISO VG 46\n5. Test fonctionnel et remise en service\n\n**Technicien assigné** : Équipe maintenance poste 2\n**Délai** : 4 heures",
    ],
    "maintenance_predict": [
        "**Analyse Prédictive — Compresseur CP-001**\n\n**Données** : 8,450h de fonctionnement | Dernière révision : 6,000h | Seuil constructeur : 10,000h\n\n**Recommandations** :\n\n| Intervention | Échéance | Criticité |\n|---|---|---|\n| Vidange huile + analyse | **Immédiat** (retard de 450h) | 🔴 Haute |\n| Remplacement filtres air/huile | 9,000h (dans ~550h) | 🟡 Moyenne |\n| Contrôle courroies + tension | 9,000h | 🟡 Moyenne |\n| Révision complète (soupapes, segments) | 10,000h (dans ~1,550h) | 🔴 Haute |\n\n**Alerte** : La vidange à 8,000h n'a pas été effectuée. Risque d'usure accélérée des paliers. Planifier en urgence.",
    ],
    "revue_direction": [
        "**Synthèse Revue de Direction — Q1-2026**\n\n**Indicateurs clés** :\n- 47 NC traitées (vs 52 en Q4-2025 : **-10%** ✅)\n- Taux de clôture : 89% (objectif 85% : **atteint** ✅)\n- Réclamations clients : 3 (stable)\n- Satisfaction client : 4.2/5 (objectif 4.0 : **atteint** ✅)\n- Accidents de travail : 0 (**record** ✅)\n\n**Points d'attention** :\n1. NC fournisseur ACIER-TN récurrentes → plan d'action renforcé\n2. Turnover techniciens qualité 12% → plan de rétention RH\n\n**Décisions** :\n- Approuver le budget formation Q2 (8,500 TND)\n- Lancer l'audit fournisseur ACIER-TN avant fin avril\n- Maintenir l'objectif satisfaction client à 4.0/5",
    ],
    "supplier_evaluator": [
        "**Évaluation Fournisseur — ACIER-TN (Q1-2026)**\n\n| Critère | Score | Pondération | Note pondérée |\n|---|---|---|---|\n| Qualité produit | 60/100 | 40% | 24 |\n| Respect des délais | 70/100 | 25% | 17.5 |\n| Prix compétitif | 85/100 | 20% | 17 |\n| Réactivité SAV | 65/100 | 15% | 9.75 |\n| **Total** | | | **68.25/100** |\n\n**Statut** : ⚠️ SOUS SURVEILLANCE (seuil : 70/100)\n**Évolution** : 85 → 72 → 68.25 (tendance baissière sur 3 trimestres)\n\n**Recommandation** : Émettre un plan d'amélioration fournisseur (PAF) avec échéance 60 jours. Si score < 65 au prochain trimestre → déréférencement partiel.",
    ],
    "stock_optimizer": [
        "**Optimisation Stock — Joints Toriques JT-42**\n\n**Données** :\n- Consommation moyenne : 45 unités/mois (σ = 8)\n- Stock actuel : 12 unités ⚠️ (SOUS SEUIL)\n- Délai fournisseur : 15 jours\n- Coût unitaire : 2.30 TND\n\n**Calculs** :\n- Point de commande = (45/30 × 15) + stock sécurité = 22.5 + 12 = **35 unités**\n- Stock sécurité (z=1.65, service 95%) = 1.65 × 8 × √(15/30) = **9.3 ≈ 10 unités**\n- Quantité économique (EOQ) = √(2 × 540 × 15 / 0.46) = **188 unités**\n\n**Action** : Commander immédiatement 188 unités. Paramétrer le seuil d'alerte à 35 unités dans GMAO Pro.",
    ],
}


def get_random_prompt(agent_id: str) -> str:
    """Get a random prompt for the given agent."""
    prompts = ALL_PROMPTS.get(agent_id, QALITAS_PROMPTS.get("nc_classifier", [""]))
    return random.choice(prompts)


def get_random_completion(agent_id: str) -> str:
    """Get a random completion for the given agent."""
    completions = COMPLETIONS.get(agent_id)
    if completions:
        return random.choice(completions)
    # Fallback: generate a generic completion
    return f"Analyse terminée pour l'agent {agent_id}. Résultats traités avec succès. Veuillez consulter le rapport détaillé dans le module correspondant."
