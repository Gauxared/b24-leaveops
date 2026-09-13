from __future__ import annotations

import re
import sqlite3
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from leaveops.service import LeaveOps

errors: list[str] = []


def error(message: str) -> None:
    errors.append(message)


def markdown_files() -> list[Path]:
    return sorted(
        path
        for path in ROOT.rglob("*.md")
        if not {".git", "runtime"}.intersection(path.relative_to(ROOT).parts)
        and path.name != "TASK.md"
    )


markdown = markdown_files()
link_re = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
for file in markdown:
    text = file.read_text(encoding="utf-8")
    for raw in link_re.findall(text):
        target = raw.strip().split(' "', 1)[0].strip("<>")
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        target = unquote(target.split("#", 1)[0])
        if target and not (file.parent / target).resolve().exists():
            error(f"broken link: {file.relative_to(ROOT)} -> {target}")

definition_specs = {
    "BR": [(ROOT / "docs/requirements/business-requirements.md", r"^\| `(BR-\d+)`")],
    "RULE": [(ROOT / "docs/requirements/business-rules.md", r"^\| `(RULE-\d+)`")],
    "FR": [(ROOT / "docs/requirements/functional-requirements.md", r"^\| `(FR-\d+)`")],
    "NFR": [(ROOT / "docs/requirements/non-functional-requirements.md", r"^\| `(NFR-\d+)`")],
    "PP": [(ROOT / "docs/discovery/pain-points.md", r"^\| `(PP-\d+)`")],
    "RISK": [(ROOT / "docs/architecture/integration-risks.md", r"^\| `(RISK-\d+)`")],
    "AC": [(ROOT / "docs/requirements/acceptance-criteria.md", r"^## `(AC-[A-Z]+-\d+)`")],
    "ADR": [(ROOT / "docs/architecture/decisions.md", r"^## (ADR-\d+)")],
    "UC": [(path, r"^# (UC-\d+)") for path in sorted((ROOT / "docs/use-cases").glob("uc-*.md"))],
}
definitions: dict[str, set[str]] = {}
for kind, specs in definition_specs.items():
    found: list[str] = []
    for file, pattern in specs:
        if not file.exists():
            error(f"canonical file missing for {kind}: {file.relative_to(ROOT)}")
            continue
        found.extend(re.findall(pattern, file.read_text(encoding="utf-8"), re.MULTILINE))
    duplicates = sorted({item for item in found if found.count(item) > 1})
    if duplicates:
        error(f"duplicate canonical {kind} IDs: {duplicates}")
    if not found:
        error(f"no canonical {kind} IDs found")
    definitions[kind] = set(found)

all_ids = set().union(*definitions.values())
id_re = re.compile(
    r"(?<![A-Z0-9-])(?:AC-[A-Z]+-\d+|ADR-\d+|RISK-\d+|RULE-\d+|NFR-\d+|BR-\d+|FR-\d+|PP-\d+|UC-\d+)(?![A-Z0-9-])"
)
for file in markdown:
    for referenced in id_re.findall(file.read_text(encoding="utf-8")):
        if referenced not in all_ids:
            error(f"unknown canonical ID in {file.relative_to(ROOT)}: {referenced}")

traceability = (ROOT / "docs/traceability.md").read_text(encoding="utf-8")
for kind in ("BR", "RULE", "FR", "NFR", "PP", "RISK", "UC"):
    for item in definitions[kind]:
        if item not in traceability:
            error(f"{item} missing from docs/traceability.md")

for file in markdown:
    if "```mermaid" in file.read_text(encoding="utf-8"):
        error(f"Mermaid remains: {file.relative_to(ROOT)}")

bpmn_ns = {"bpmn": "http://www.omg.org/spec/BPMN/20100524/MODEL"}
bpmn_files = sorted((ROOT / "docs/diagrams/src").glob("*.bpmn"))
for file in bpmn_files:
    try:
        root = ET.parse(file).getroot()
    except ET.ParseError as exc:
        error(f"invalid BPMN XML {file.name}: {exc}")
        continue

    xml_ids = [element.attrib["id"] for element in root.iter() if "id" in element.attrib]
    duplicates = sorted({item for item in xml_ids if xml_ids.count(item) > 1})
    if duplicates:
        error(f"duplicate BPMN IDs in {file.name}: {duplicates}")

    owners: dict[str, str] = {}
    for process in root.findall("bpmn:process", bpmn_ns):
        process_id = process.attrib["id"]
        for element in process.iter():
            if element_id := element.attrib.get("id"):
                owners[element_id] = process_id

    for process in root.findall("bpmn:process", bpmn_ns):
        process_id = process.attrib["id"]
        for flow in process.findall("bpmn:sequenceFlow", bpmn_ns):
            source = flow.attrib.get("sourceRef", "")
            target = flow.attrib.get("targetRef", "")
            if owners.get(source) != process_id or owners.get(target) != process_id:
                error(f"sequence flow crosses process in {file.name}: {flow.attrib.get('id')}")

    collaboration = root.find("bpmn:collaboration", bpmn_ns)
    if collaboration is None:
        error(f"collaboration missing: {file.name}")
        continue
    for flow in collaboration.findall("bpmn:messageFlow", bpmn_ns):
        source_owner = owners.get(flow.attrib.get("sourceRef", ""))
        target_owner = owners.get(flow.attrib.get("targetRef", ""))
        if source_owner is None or target_owner is None:
            error(f"message flow endpoint missing in {file.name}: {flow.attrib.get('id')}")
        elif source_owner == target_owner:
            error(f"message flow stays in one process in {file.name}: {flow.attrib.get('id')}")

    if file.name == "to-be.bpmn":
        for flow in collaboration.findall("bpmn:messageFlow", bpmn_ns):
            if owners.get(flow.attrib.get("sourceRef", "")) == "Process_Business" and owners.get(flow.attrib.get("targetRef", "")) == "Process_Worker":
                error("TO-BE business process must not start the worker by message flow")
        worker_start = root.find(".//bpmn:startEvent[@id='Start_Worker']", bpmn_ns)
        if worker_start is None or "ручн" not in worker_start.attrib.get("name", "").lower():
            error("TO-BE worker must have an explicit manual start event")
        elif worker_start.find("bpmn:messageEventDefinition", bpmn_ns) is not None:
            error("TO-BE manual worker start must not be a message start event")
        required_refs = {"DataStoreRef_OutboxBusiness", "DataStoreRef_OutboxWorker"}
        actual_refs = {item.attrib.get("id") for item in root.findall(".//bpmn:dataStoreReference", bpmn_ns)}
        if not required_refs.issubset(actual_refs):
            error("TO-BE must model the persisted outbox as a data store")

plantuml_files = sorted((ROOT / "docs/diagrams/src").glob("*.puml"))
diagram_sources: set[str] = set()
for file in plantuml_files:
    text = file.read_text(encoding="utf-8")
    if file.name == "theme.puml":
        continue
    diagram_sources.add(file.stem)
    if not text.lstrip().startswith("@startuml") or not text.rstrip().endswith("@enduml"):
        error(f"invalid PlantUML envelope: {file.name}")
    for include in re.findall(r"^!include\s+([^\s]+)", text, re.MULTILINE):
        if not include.startswith("<") and not (file.parent / include).exists():
            error(f"missing PlantUML include in {file.name}: {include}")

use_case_source = (ROOT / "docs/diagrams/src/use-cases-integration.puml").read_text(encoding="utf-8")
if re.search(r"^actor\s+[\"']?Integration\s*Worker", use_case_source, re.IGNORECASE | re.MULTILINE):
    error("Integration Worker must not be a UML Use Case actor")
c4_source = (ROOT / "docs/diagrams/src/c4-containers.puml").read_text(encoding="utf-8")
if "ContainerDb(mock" in c4_source or "System_Ext(mock" not in c4_source:
    error("Mock Bitrix24 must be outside the LeaveOps C4 boundary")

rendered = {file.stem for file in (ROOT / "docs/diagrams/rendered").glob("*.svg")}
source_names = diagram_sources | {file.stem for file in bpmn_files}
if missing := sorted(source_names - rendered):
    error(f"missing rendered SVG: {missing}")
for file in (ROOT / "docs/diagrams/rendered").glob("*.svg"):
    try:
        ET.parse(file)
    except ET.ParseError as exc:
        error(f"invalid SVG {file.name}: {exc}")

with tempfile.TemporaryDirectory() as temporary:
    database = Path(temporary) / "schema.db"
    LeaveOps(database)
    db = sqlite3.connect(database)
    try:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        indexes = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'")}
    finally:
        db.close()
    expected_tables = {"employees", "balances", "departments", "rules", "requests", "history", "integration_events", "reschedule_proposals"}
    expected_indexes = {"integration_events_delivery", "reschedule_proposals_request"}
    if tables != expected_tables:
        error(f"physical tables differ: {sorted(tables)}")
    if indexes != expected_indexes:
        error(f"physical indexes differ: {sorted(indexes)}")
    physical_doc = (ROOT / "docs/data/physical-model.md").read_text(encoding="utf-8")
    for name in tables | indexes:
        if f"`{name}`" not in physical_doc:
            error(f"physical object undocumented: {name}")

web_source = (ROOT / "leaveops/web.py").read_text(encoding="utf-8")
api_routes = set(re.findall(r"url\.path\s*==\s*['\"](/api/[^'\"]+)['\"]", web_source))
api_doc = (ROOT / "docs/api/internal-api.md").read_text(encoding="utf-8")
for route in api_routes:
    if f"`{route}`" not in api_doc:
        error(f"HTTP route undocumented: {route}")

secret_re = re.compile(r"https://[^/\s]+\.bitrix24\.[^/\s]+/rest/\d+/[A-Za-z0-9_-]{8,}/", re.IGNORECASE)
for file in markdown + sorted(ROOT.rglob("*.py")) + sorted(ROOT.rglob("*.json")) + sorted(ROOT.rglob("*.yml")):
    if {".git", "runtime"}.intersection(file.relative_to(ROOT).parts):
        continue
    matches = secret_re.findall(file.read_text(encoding="utf-8"))
    if any("example.bitrix24.ru" not in match.lower() for match in matches):
        error(f"possible Bitrix24 webhook secret: {file.relative_to(ROOT)}")

if errors:
    print("\n".join(f"ERROR: {message}" for message in errors))
    raise SystemExit(1)

counts = ", ".join(f"{kind}={len(items)}" for kind, items in definitions.items())
print(f"Markdown links: OK ({len(markdown)} files)")
print(f"Canonical IDs: OK ({counts})")
print(f"BPMN structure: OK ({len(bpmn_files)} files)")
print(f"PlantUML sources/rendered SVG: OK ({len(diagram_sources)} diagrams)")
print(f"SQLite/API documentation: OK ({len(expected_tables)} tables, {len(expected_indexes)} indexes, {len(api_routes)} routes)")
print("Bitrix24 secret scan: OK")
