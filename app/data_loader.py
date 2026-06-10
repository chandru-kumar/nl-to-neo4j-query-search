"""
Data loader: Reads JSON files and populates the Neo4j graph database.

Graph Model:
  (:Project) -[:HAS_NODE]-> (:Node) -[:HAS_CHILD]-> (:Node)
  (:Node) -[:OF_TYPE]-> (:NodeType)
  (:Node) -[:HAS_ASSIGNMENT]-> (:Assignment) -[:FOR_ATTRIBUTE]-> (:Attribute)
  (:Attribute) -[:USES_WIDGET]-> (:Widget)
  (:Attribute) -[:BELONGS_TO_TYPE]-> (:NodeType)
  (:NodeType) -[:CHILD_TYPE_OF]-> (:NodeType)

Performance:
  The project node trees can contain hundreds to thousands of nodes each,
  every one carrying multiple assignments. To avoid one network round-trip
  per node/assignment/relationship (which made loading take hours), the
  tree is flattened in memory and written with batched UNWIND queries.
"""

import json
import os
import glob
import logging
from typing import Any

from app.db import db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DATA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Rows written per UNWIND batch. Large enough to amortise round-trips,
# small enough to keep each transaction's memory bounded.
CHUNK_SIZE = 5000


# ── Extended-JSON helpers ───────────────────────────────────────────────
# MongoDB exports wrap values as {"$oid": "..."} or {"$numberLong": "..."}.
# Depending on the export, a reference id may appear either as a bare string
# or wrapped. These helpers accept BOTH forms so links resolve regardless.

def extract_oid(value: Any) -> str:
    """Return an ObjectId string from a bare string or {"$oid": ...} form."""
    if value is None:
        return ""
    if isinstance(value, dict):
        return str(value.get("$oid", value.get("$numberLong", ""))) or ""
    return str(value)


def extract_scalar(value: Any) -> str:
    """Return a scalar (id/number) from a bare value or extended-JSON wrapper."""
    if value is None:
        return ""
    if isinstance(value, dict):
        return str(value.get("$numberLong", value.get("$oid", value.get("$numberInt", ""))))
    return str(value)


def find_data_file(name: str) -> str:
    """
    Locate a source JSON file. The export numbers files inconsistently
    (e.g. `Project-Paweb.Project.json` vs `Project-Paweb.Project-1.json`),
    so match on a glob and take the first hit.
    """
    candidates = sorted(glob.glob(os.path.join(DATA_DIR, name)))
    if not candidates:
        raise FileNotFoundError(
            f"No data file matching '{name}' in {DATA_DIR}. "
            "Place the exported JSON files in the project root."
        )
    return candidates[0]


def load_json(name: str):
    filepath = find_data_file(name)
    logger.info(f"Loading {os.path.basename(filepath)}...")
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def write_chunked(query: str, rows: list[dict]):
    """Run a batched UNWIND write in CHUNK_SIZE slices."""
    for i in range(0, len(rows), CHUNK_SIZE):
        db.execute_write_batch(query, rows[i:i + CHUNK_SIZE])


# ── Schema setup ────────────────────────────────────────────────────────

def clear_database():
    """Remove all nodes and relationships (in batches to avoid OOM)."""
    logger.info("Clearing existing data...")
    # CALL {} IN TRANSACTIONS keeps memory bounded on large graphs.
    db.execute_write(
        "MATCH (n) CALL { WITH n DETACH DELETE n } IN TRANSACTIONS OF 10000 ROWS"
    )


def create_constraints():
    """Create uniqueness constraints and indexes."""
    constraints = [
        "CREATE CONSTRAINT IF NOT EXISTS FOR (w:Widget) REQUIRE w.uid IS UNIQUE",
        "CREATE CONSTRAINT IF NOT EXISTS FOR (nt:NodeType) REQUIRE nt.uid IS UNIQUE",
        "CREATE CONSTRAINT IF NOT EXISTS FOR (a:Attribute) REQUIRE a.uid IS UNIQUE",
        "CREATE CONSTRAINT IF NOT EXISTS FOR (p:Project) REQUIRE p.uid IS UNIQUE",
        "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Node) REQUIRE n.nodeKey IS UNIQUE",
        "CREATE CONSTRAINT IF NOT EXISTS FOR (a:Assignment) REQUIRE a.assignKey IS UNIQUE",
        "CREATE INDEX IF NOT EXISTS FOR (n:Node) ON (n.nodeId)",
        "CREATE INDEX IF NOT EXISTS FOR (n:Node) ON (n.name)",
        "CREATE INDEX IF NOT EXISTS FOR (nt:NodeType) ON (nt.name)",
        "CREATE INDEX IF NOT EXISTS FOR (a:Attribute) ON (a.name)",
    ]
    for c in constraints:
        db.execute_write(c)
    logger.info("Constraints and indexes created.")


# ── Reference data (Widget / NodeType / Attribute) ──────────────────────

def load_widgets():
    data = load_json("Project-Paweb.Widget*.json")
    batch = [
        {
            "uid": extract_oid(w["_id"]),
            "type": w.get("type", ""),
            "description": w.get("description", ""),
        }
        for w in data
    ]
    write_chunked(
        """
        UNWIND $batch AS row
        MERGE (w:Widget {uid: row.uid})
        SET w.type = row.type, w.description = row.description
        """,
        batch,
    )
    logger.info(f"Loaded {len(batch)} widgets.")


def load_node_types():
    data = load_json("Project-Paweb.NodeType*.json")
    batch, parent_links = [], []
    for nt in data:
        uid = extract_oid(nt["_id"])
        batch.append({
            "uid": uid,
            "name": nt.get("name", ""),
            "label": nt.get("label", nt.get("name", "")),
            "handler": nt.get("handler", ""),
        })
        if nt.get("parentId"):
            parent_links.append({"child_uid": uid, "parent_uid": extract_oid(nt["parentId"])})

    write_chunked(
        """
        UNWIND $batch AS row
        MERGE (nt:NodeType {uid: row.uid})
        SET nt.name = row.name, nt.label = row.label, nt.handler = row.handler
        """,
        batch,
    )
    if parent_links:
        write_chunked(
            """
            UNWIND $batch AS row
            MATCH (child:NodeType {uid: row.child_uid})
            MATCH (parent:NodeType {uid: row.parent_uid})
            MERGE (child)-[:CHILD_TYPE_OF]->(parent)
            """,
            parent_links,
        )
    logger.info(f"Loaded {len(batch)} node types with {len(parent_links)} parent links.")


def load_attributes():
    data = load_json("Project-Paweb.Attribute*.json")
    batch = []
    for attr in data:
        sort = 0
        if "sort" in attr:
            sort = int(extract_scalar(attr["sort"]) or 0)
        batch.append({
            "uid": extract_oid(attr["_id"]),
            "name": attr.get("name", ""),
            "displayName": attr.get("displayName", attr.get("name", "")),
            "directName": attr.get("directName", ""),
            "showAttributeName": attr.get("showAttributeName", False),
            "widgetId": extract_oid(attr.get("widgetId")),
            "nodeTypeId": extract_oid(attr.get("nodeTypeId")),
            "enumerant": attr.get("enumerant", []),
            "sort": sort,
        })

    write_chunked(
        """
        UNWIND $batch AS row
        MERGE (a:Attribute {uid: row.uid})
        SET a.name = row.name,
            a.displayName = row.displayName,
            a.directName = row.directName,
            a.showAttributeName = row.showAttributeName,
            a.enumerant = row.enumerant,
            a.sort = row.sort
        """,
        batch,
    )

    widget_links = [r for r in batch if r["widgetId"]]
    if widget_links:
        write_chunked(
            """
            UNWIND $batch AS row
            MATCH (a:Attribute {uid: row.uid})
            MATCH (w:Widget {uid: row.widgetId})
            MERGE (a)-[:USES_WIDGET]->(w)
            """,
            widget_links,
        )

    type_links = [r for r in batch if r["nodeTypeId"]]
    if type_links:
        write_chunked(
            """
            UNWIND $batch AS row
            MATCH (a:Attribute {uid: row.uid})
            MATCH (nt:NodeType {uid: row.nodeTypeId})
            MERGE (a)-[:BELONGS_TO_TYPE]->(nt)
            """,
            type_links,
        )

    logger.info(
        f"Loaded {len(batch)} attributes "
        f"({len(widget_links)} widget links, {len(type_links)} type links)."
    )


# ── Projects & node trees ───────────────────────────────────────────────

def _flatten_node_tree(project_uid, parent_key, nodes, acc):
    """
    Walk the recursive node tree and append plain rows to `acc` buckets so
    they can be written with batched UNWIND queries afterwards.
    """
    for node in nodes:
        node_id = extract_scalar(node.get("id"))
        node_key = f"{project_uid}_{node_id}"

        acc["nodes"].append({
            "nodeKey": node_key,
            "nodeId": node_id,
            "name": node.get("name", ""),
            "directId": extract_scalar(node.get("directId")),
            "containerId": extract_scalar(node.get("containerId")),
            "hasWarning": node.get("hasWarning", False),
        })

        nt_id = extract_oid(node.get("nodeTypeId"))
        if nt_id:
            acc["type_links"].append({"nodeKey": node_key, "ntUid": nt_id})

        if parent_key is None:
            acc["root_links"].append({"projUid": project_uid, "nodeKey": node_key})
        else:
            acc["child_links"].append({"parentKey": parent_key, "childKey": node_key})

        for i, assign in enumerate(node.get("assignments", []) or []):
            # Prefer the assignment's own stable id; fall back to its position.
            assign_id = extract_scalar(assign.get("id"))
            assign_key = f"{node_key}_{assign_id}" if assign_id else f"{node_key}_assign_{i}"
            acc["assignments"].append({
                "assignKey": assign_key,
                "nodeKey": node_key,
                "libValue": _stringify(assign.get("libraryValue")),
                "userValue": _stringify(assign.get("value")),
            })
            attr_id = extract_oid(assign.get("attributeId"))
            if attr_id:
                acc["attr_links"].append({"assignKey": assign_key, "attrUid": attr_id})

        children = node.get("children", []) or []
        if children:
            _flatten_node_tree(project_uid, node_key, children, acc)


def _stringify(value: Any) -> str:
    """Normalise an assignment value to a string for storage/search."""
    if value is None:
        return ""
    if isinstance(value, dict):
        return extract_scalar(value)
    if isinstance(value, (list, tuple)):
        return ", ".join(_stringify(v) for v in value)
    return str(value)


def _flush_project(acc):
    """Write one project's accumulated rows to Neo4j with batched queries."""
    write_chunked(
        """
        UNWIND $batch AS row
        MERGE (n:Node {nodeKey: row.nodeKey})
        SET n.nodeId = row.nodeId,
            n.name = row.name,
            n.directId = row.directId,
            n.containerId = row.containerId,
            n.hasWarning = row.hasWarning
        """,
        acc["nodes"],
    )
    write_chunked(
        """
        UNWIND $batch AS row
        MATCH (n:Node {nodeKey: row.nodeKey})
        MATCH (nt:NodeType {uid: row.ntUid})
        MERGE (n)-[:OF_TYPE]->(nt)
        """,
        acc["type_links"],
    )
    write_chunked(
        """
        UNWIND $batch AS row
        MATCH (p:Project {uid: row.projUid})
        MATCH (n:Node {nodeKey: row.nodeKey})
        MERGE (p)-[:HAS_NODE]->(n)
        """,
        acc["root_links"],
    )
    write_chunked(
        """
        UNWIND $batch AS row
        MATCH (parent:Node {nodeKey: row.parentKey})
        MATCH (child:Node {nodeKey: row.childKey})
        MERGE (parent)-[:HAS_CHILD]->(child)
        """,
        acc["child_links"],
    )
    write_chunked(
        """
        UNWIND $batch AS row
        MATCH (n:Node {nodeKey: row.nodeKey})
        MERGE (a:Assignment {assignKey: row.assignKey})
        SET a.libraryValue = row.libValue, a.value = row.userValue
        MERGE (n)-[:HAS_ASSIGNMENT]->(a)
        """,
        acc["assignments"],
    )
    write_chunked(
        """
        UNWIND $batch AS row
        MATCH (a:Assignment {assignKey: row.assignKey})
        MATCH (attr:Attribute {uid: row.attrUid})
        MERGE (a)-[:FOR_ATTRIBUTE]->(attr)
        """,
        acc["attr_links"],
    )


def load_projects():
    """Load Project nodes and their hierarchical node trees (batched)."""
    data = load_json("Project-Paweb.Project*.json")
    logger.info(f"Processing {len(data)} projects...")

    proj_rows = []
    for idx, proj in enumerate(data):
        version = proj.get("projectVersion", {}) or {}
        proj_rows.append({
            "uid": extract_oid(proj["_id"]),
            "name": proj.get("name", f"Project_{idx}"),
            "ecuFamily": proj.get("ecuFamilyName", ""),
            "version": extract_scalar(version.get("version", 0)),
            "status": version.get("status", ""),
        })
    write_chunked(
        """
        UNWIND $batch AS row
        MERGE (p:Project {uid: row.uid})
        SET p.name = row.name,
            p.ecuFamilyName = row.ecuFamily,
            p.version = row.version,
            p.status = row.status
        """,
        proj_rows,
    )

    total_nodes = total_assignments = 0
    for idx, proj in enumerate(data):
        proj_uid = extract_oid(proj["_id"])
        acc = {
            "nodes": [], "type_links": [], "root_links": [],
            "child_links": [], "assignments": [], "attr_links": [],
        }
        # The tree is under "node" in this export.
        _flatten_node_tree(proj_uid, None, proj.get("node", []) or [], acc)
        _flush_project(acc)

        total_nodes += len(acc["nodes"])
        total_assignments += len(acc["assignments"])
        if (idx + 1) % 20 == 0 or (idx + 1) == len(data):
            logger.info(
                f"  Processed {idx + 1}/{len(data)} projects "
                f"({total_nodes} nodes, {total_assignments} assignments so far)..."
            )

    logger.info(
        f"Loaded {len(data)} projects, {total_nodes} nodes, "
        f"{total_assignments} assignments."
    )


def _summary_counts() -> dict:
    """Count the loaded graph for a load confirmation message."""
    rows = db.execute_read(
        """
        RETURN
          count{ (p:Project) }    AS projects,
          count{ (n:Node) }       AS nodes,
          count{ (nt:NodeType) }  AS nodeTypes,
          count{ (a:Attribute) }  AS attributes,
          count{ (w:Widget) }     AS widgets,
          count{ (asg:Assignment) } AS assignments
        """
    )
    return rows[0] if rows else {}


def run_loader():
    """Main entry point for data loading. Returns a summary of counts."""
    db.connect()
    try:
        clear_database()
        create_constraints()
        load_widgets()
        load_node_types()
        load_attributes()
        load_projects()
        summary = _summary_counts()
        logger.info(f"Data loading complete! {summary}")
        return summary
    finally:
        db.close()


if __name__ == "__main__":
    run_loader()
