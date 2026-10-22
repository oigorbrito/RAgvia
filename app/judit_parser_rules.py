"""Deterministic lexical annotations adapted from oigorbrito/rag_resumo.

Attribution: resumo/parser.py at 0d0f8cbb495292a3f3d61fba75428f3136059ea8.
These annotations are source interpretations, NOT established judicial facts or
instructions. See docs/migrations/ragvia-m01-parser-parity.md for deliberate
privacy/chronology divergences. No generation or donor pipeline is imported.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Callable

TRIBUNAIS = {
    "TJAC": "Tribunal de Justiça do Acre", "TJAL": "Tribunal de Justiça de Alagoas",
    "TJAP": "Tribunal de Justiça do Amapá", "TJAM": "Tribunal de Justiça do Amazonas",
    "TJBA": "Tribunal de Justiça da Bahia", "TJCE": "Tribunal de Justiça do Ceará",
    "TJDFT": "Tribunal de Justiça do Distrito Federal e dos Territórios",
    "TJES": "Tribunal de Justiça do Espírito Santo", "TJGO": "Tribunal de Justiça de Goiás",
    "TJMA": "Tribunal de Justiça do Maranhão", "TJMT": "Tribunal de Justiça de Mato Grosso",
    "TJMS": "Tribunal de Justiça de Mato Grosso do Sul", "TJMG": "Tribunal de Justiça de Minas Gerais",
    "TJPA": "Tribunal de Justiça do Pará", "TJPB": "Tribunal de Justiça da Paraíba",
    "TJPR": "Tribunal de Justiça do Paraná", "TJPE": "Tribunal de Justiça de Pernambuco",
    "TJPI": "Tribunal de Justiça do Piauí", "TJRJ": "Tribunal de Justiça do Rio de Janeiro",
    "TJRN": "Tribunal de Justiça do Rio Grande do Norte", "TJRS": "Tribunal de Justiça do Rio Grande do Sul",
    "TJRO": "Tribunal de Justiça de Rondônia", "TJRR": "Tribunal de Justiça de Roraima",
    "TJSC": "Tribunal de Justiça de Santa Catarina", "TJSP": "Tribunal de Justiça de São Paulo",
    "TJSE": "Tribunal de Justiça de Sergipe", "TJTO": "Tribunal de Justiça do Tocantins",
}
SITUACOES = {"ATIVO": "ativa", "ARQUIVADO": "arquivada", "FINALIZADO": "finalizada",
             "SUSPENSO": "suspensa", "BAIXADO": "baixada", "EXTINTO": "extinta"}
CLASSES_INCIDENTAIS = ("EMBARGOS", "AGRAVO", "RECURSO", "APELACAO", "RECLAMACAO")
RE_DATAHORA = re.compile(r"(\d{2}/\d{2}/\d{4})(?:\s+(\d{2}:\d{2}))?")


def normalize_text(txt: str | None) -> str:
    if not txt:
        return ""
    t = unicodedata.normalize("NFKD", str(txt))
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", t).strip().upper()


def side_label(side: str | None) -> str:
    return {"active": "ativo", "passive": "passivo"}.get((side or "").lower(), "outro")


def format_oab(documents: list[dict] | None) -> str | None:
    for d in documents or []:
        if isinstance(d, dict) and str(d.get("document_type") or "").lower() == "oab":
            m = re.match(r"([A-Za-z]{2})\s*(\d+)", str(d.get("document") or ""))
            if m:
                return f"OAB/{m.group(1).upper()} {m.group(2)}"
    return None


def movement_number(text: str) -> int | None:
    m = re.match(r"^(\d+)\s*-\s*(.*)$", text)
    return int(m.group(1)) if m else None


def classify_movement(texto: str) -> tuple[str, dict]:
    # Literal donor precedence, including potentially ambiguous/negated matches.
    # Never use this return value as proof that an act happened.
    t = normalize_text(texto)
    det: dict[str, Any] = {}
    if "AUDIENCIA" in t and "DESIGNADA" in t:
        m = re.search(r"AUDI[ÊE]NCIA DE (.+?) DESIGNADA", texto, re.I)
        if m:
            det["modalidade"] = m.group(1).strip().lower()
        datas = RE_DATAHORA.findall(texto)
        if datas:
            d, h = datas[-1]
            det["data_agendada"] = d + (f" às {h}" if h else "")
        return "audiencia_designada", det
    if "INTIMACAO" in t or "CITACAO" in t:
        tipo = "citacao" if "CITACAO" in t else "intimacao"
        m = re.search(r"PRAZO:\s*(\d+)\s*DIA", t)
        if m:
            det["prazo_dias"] = int(m.group(1))
        m = re.search(r"\(\s*([A-ZÉÁÍÓÚ ]+?)\s*-\s*([^)]+)\)", texto.upper())
        if m:
            det["destinatario"] = f"{m.group(2).strip()} ({m.group(1).strip().lower()})"
        if "AUDIENCIA" in t:
            det["finalidade"] = "comunicar audiência"
        m = re.search(r"REFER\.?\s*AO EVENTO\s*(\d+)", t)
        if m:
            det["referente_evento"] = int(m.group(1))
        datas = RE_DATAHORA.findall(texto)
        if datas:
            det["disponibilizacao_diario"] = datas[-1][0]
        return tipo, det
    regras = [
        ("DISTRIBUIDO", "distribuicao"), ("100% DIGITAL", "juizo_100_digital"),
        ("REMETIDOS OS AUTOS", "remessa_interna"), ("ATO ORDINATORIO", "ato_ordinatorio"),
        ("DISPONIBILIZADO NO DJ", "publicacao_diario"), ("JUNTADA", "juntada"),
        ("SENTENCA", "sentenca"), ("DECISAO", "decisao"), ("DESPACHO", "despacho"),
        ("CONTESTACAO", "contestacao"), ("CONCLUSOS", "conclusao"), ("ARQUIVAD", "arquivamento"),
        ("TRANSITO EM JULGADO", "transito_em_julgado"), ("ACORDO", "acordo"),
    ]
    for chave, tipo in regras:
        if chave in t:
            if tipo == "distribuicao" and "SORTEIO" in t:
                det["forma"] = "sorteio"
            return tipo, det
    return "outro", det


def party_annotations(raw: list[dict], safe_entity: Callable[[dict], Any]) -> list[dict]:
    entities: list[dict] = []
    seen: set[str] = set()
    lawyers_by_side: dict[str, list[dict]] = {}
    for i, party in enumerate(raw or []):
        if not isinstance(party, dict):
            continue
        safe = safe_entity(party)
        if safe is None or normalize_text(safe["name"]) in seen:
            continue
        kind = str(party.get("person_type") or "").upper()
        side = side_label(str(party.get("side") or ""))
        lawyers = []
        for j, lawyer in enumerate(party.get("lawyers") or []):
            if not isinstance(lawyer, dict):
                continue
            item = safe_entity(lawyer)
            if item is not None:
                lawyers.append(item["name"])
                lawyers_by_side.setdefault(side, []).append({
                    "name": item["name"], "type": "ADVOGADO", "side": side,
                    "document": format_oab(lawyer.get("documents")), "lawyers": [],
                    "source_ref": f"parties/{i}/lawyers/{j}",
                })
        entities.append({"name": safe["name"], "type": kind, "side": side,
                         "document": format_oab(party.get("documents")) if "ADVOGAD" in normalize_text(kind) else safe.get("masked_person_id"),
                         "lawyers": lawyers, "source_ref": f"parties/{i}"})
        seen.add(normalize_text(safe["name"]))
    for side, lawyers in lawyers_by_side.items():
        for lawyer in lawyers:
            if normalize_text(lawyer["name"]) in seen:
                continue
            index = max((i for i, item in enumerate(entities) if item["side"] == side), default=len(entities)-1)
            entities.insert(index+1, lawyer)
            seen.add(normalize_text(lawyer["name"]))
    for i, entity in enumerate(entities, 1):
        entity["source_id"] = f"p{i}"
    return entities


def registry_items(raw: list[dict], prefix: str, field: str) -> list[dict[str, Any]]:
    return [{"source_id": f"{prefix}{i}", "source_ref": f"{field}/{i-1}",
             "code": str(r.get("code")) if r.get("code") else None,
             "name": str(r.get("name") or "").strip(), "suspect": False, "reason": None}
            for i, r in enumerate(raw or [], 1) if isinstance(r, dict) and r.get("name")]


def registry_annotations(d: dict, steps: list[dict], local_date: Callable[[Any], Any], *, step_field: str = "steps") -> dict:
    classes = registry_items(d.get("classifications") or [], "c", "classifications")
    subjects = registry_items(d.get("subjects") or [], "a", "subjects")
    alerts = []
    phase = normalize_text(d.get("phase"))
    kinds = {s["metadata"]["source_parser"]["kind"] for s in steps}
    has_decision = bool(kinds.intersection({"sentenca", "decisao"}))
    if len(classes) > 1:
        for c in classes:
            if any(k in normalize_text(c["name"]) for k in CLASSES_INCIDENTAIS) and phase in ("INICIAL", "") and not has_decision:
                c["suspect"] = True
                c["reason"] = ("classe incidental/recursal numa ação em fase inicial, sem decisão "
                               "registrada; provável erro de captura do cadastro")
                alerts.append({"rule": "incidental_class", "source_refs": [c["source_ref"], "phase", step_field],
                               "message": f"Classe '{c['name']}' provavelmente é erro de cadastro: {c['reason']}."})
    area = normalize_text(d.get("area"))
    if "TRABALHO" in area and "TRABALH" not in " ".join(normalize_text(a["name"]) for a in subjects) and str(d.get("justice")) != "5":
        alerts.append({"rule": "area_conflict", "source_refs": ["area", "subjects", "justice", "justice_description"],
                       "message": f"Área cadastrada '{d.get('area')}' não condiz com os assuntos ({', '.join(a['name'] for a in subjects)}) nem com a Justiça ({d.get('justice_description')}). Ignore a área."})
    courts = d.get("courts") or [{}]
    court = courts[0] if isinstance(courts[0], dict) else {}
    if normalize_text(d.get("county")) and normalize_text(court.get("name")) and "FAZENDA" in normalize_text(d.get("county")) and "JUIZADO ESPECIAL CIVEL" in normalize_text(court.get("name")):
        alerts.append({"rule": "county_court_conflict", "source_refs": ["county", "courts/0/name"],
                       "message": "O campo de comarca/vara menciona 'Vara da Fazenda Pública', mas o órgão julgador é um Juizado Especial Cível. Use o órgão julgador."})
    for alert in alerts:
        alert["kind"] = "heuristic"
    distribution = local_date(d.get("distribution_date"))
    sigla = str(d.get("tribunal_acronym") or "") or None
    return {
        "semantics": "lexical_annotations_not_verified_facts", "rules_version": "judit-lexical-v1", "classifications": classes,
        "subjects": subjects, "indicators": alerts,
        "registry": {"tribunal_name": TRIBUNAIS.get(sigla or "", sigla),
                     "city_label": str(d["city"]).title() if d.get("city") else None,
                     "status_label": SITUACOES.get(normalize_text(d.get("status")), str(d.get("status") or "").lower() or None),
                     "phase_label": str(d.get("phase") or "").lower() or None,
                     "distribution_date_label": distribution.strftime("%d/%m/%Y") if distribution else None,
                     "digital_court_lexical_hint": "juizo_100_digital" in kinds,
                     "citation_lexical_hint": "citacao" in kinds},
        "registry_sources": {"tribunal_name": ["tribunal_acronym"], "city_label": ["city"],
                             "status_label": ["status"], "phase_label": ["phase"],
                             "distribution_date_label": ["distribution_date"], "amount_label": ["amount"],
                             "digital_court_lexical_hint": [step_field], "citation_lexical_hint": [step_field]},
        "source_refs": ["tribunal_acronym", "city", "status", "phase", "distribution_date", "amount", step_field],
    }
