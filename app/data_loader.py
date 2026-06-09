"""
Data loader: Reads JSON files and populates Neo4j graph database.

Graph Model:
  (:Project) -[:HAS_NODE]-> (:Node) -[:HAS_CHILD]-> (:Node)
  (:Node) -[:OF_TYPE]-> (:NodeType)
  (:Node) -[:HAS_ASSIGNMENT]-> (:Assignment) -[:FOR_ATTRIBUTE]-> (:Attribute)
  (:Attribute) -[:USES_WIDGET]-> (:Widget)
  (:Attribute) -[:BELONGS_TO_TYPE]-> (:NodeType)
  (:NodeType) -[:CHILD_TYPE_OF]-> (:NodeType)
"""

import json
import os
import logging

from app.db import db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DATA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_json(filename: str):
    filepath = os.path.join(DATA_DIR, filename)
    logger.info(f"Loading {filepath}...")
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def clear_database():
    """Remove all nodes and relationships."""
    logger.info("Clearing existing data...")
    db.execute_write("MATCH (n) DETACH DELETE n")


def create_constraints():
    """Create uniqueness constraints and indexes."""
    constraints = [
        "CREATE CONSTRAINT IF NOT EXISTS FOR (w:Widget) REQUIRE w.uid IS UNIQUE",
        "CREATE CONSTRAINT IF NOT EXISTS FOR (nt:NodeType) REQUIRE nt.uid IS UNIQUE",
        "CREATE CONSTRAINT IF NOT EXISTS FOR (a:Attribute) REQUIRE a.uid IS UNIQUE",
        "CREATE CONSTRAINT IF NOT EXISTS FOR (p:Project) REQUIRE p.uid IS UNIQUE",
        "CREATE INDEX IF NOT EXISTS FOR (n:Node) ON (n.nodeId)",
        "CREATE INDEX IF NOT EXISTS FOR (n:Node) ON (n.name)",
    ]
    for c in constraints:
        db.execute_write(c)
    logger.info("Constraints and indexes created.")


def load_widgets():
    """Load Widget nodes."""
    data = load_json("Project-Paweb.Widget.json")
    batch = []
    for w in data:
        batch.append({
            "uid": w["_id"]["$oid"],
            "type": w["type"],
            "description": w.get("description", ""),
        })

    db.execute_write_batch(
        """
        UNWIND $batch AS row
        MERGE (w:Widget {uid: row.uid})
        SET w.type = row.type, w.description = row.description
        """,
        batch,
    )
    logger.info(f"Loaded {len(batch)} widgets.")


def load_node_types():
    """Load NodeType nodes and parent relationships."""
    data = load_json("Project-Paweb.NodeType.json")
    batch = []
    parent_links = []
    for nt in data:
        uid = nt["_id"]["$oid"]
        batch.append({
            "uid": uid,
            "name": nt["name"],
            "label": nt.get("label", nt["name"]),
            "handler": nt.get("handler", ""),
        })
        if "parentId" in nt:
            parent_links.append({
                "child_uid": uid,
                "parent_uid": nt["parentId"]["$oid"],
            })

    db.execute_write_batch(
        """
        UNWIND $batch AS row
        MERGE (nt:NodeType {uid: row.uid})
        SET nt.name = row.name, nt.label = row.label, nt.handler = row.handler
        """,
        batch,
    )

    if parent_links:
        db.execute_write_batch(
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
    """Load Attribute nodes with relationships to Widget and NodeType."""
    data = load_json("Project-Paweb.Attribute.json")
    batch = []
    for attr in data:
        batch.append({
            "uid": attr["_id"]["$oid"],
            "name": attr["name"],
            "displayName": attr.get("displayName", attr["name"]),
            "directName": attr.get("directName", ""),
            "showAttributeName": attr.get("showAttributeName", False),
            "widgetId": attr["widgetId"]["$oid"] if "widgetId" in attr else None,
            "nodeTypeId": attr["nodeTypeId"]["$oid"] if "nodeTypeId" in attr else None,
            "sort": int(attr["sort"]["$numberLong"]) if "sort" in attr else 0,
        })

    db.execute_write_batch(
        """
        UNWIND $batch AS row
        MERGE (a:Attribute {uid: row.uid})
        SET a.name = row.name,
            a.displayName = row.displayName,
            a.directName = row.directName,
            a.showAttributeName = row.showAttributeName,
            a.sort = row.sort
        """,
        batch,
    )

    # Link Attribute -> Widget
    widget_links = [r for r in batch if r["widgetId"]]
    if widget_links:
        db.execute_write_batch(
            """
            UNWIND $batch AS row
            MATCH (a:Attribute {uid: row.uid})
            MATCH (w:Widget {uid: row.widgetId})
            MERGE (a)-[:USES_WIDGET]->(w)
            """,
            widget_links,
        )

    # Link Attribute -> NodeType
    type_links = [r for r in batch if r["nodeTypeId"]]
    if type_links:
        db.execute_write_batch(
            """
            UNWIND $batch AS row
            MATCH (a:Attribute {uid: row.uid})
            MATCH (nt:NodeType {uid: row.nodeTypeId})
            MERGE (a)-[:BELONGS_TO_TYPE]->(nt)
            """,
            type_links,
        )

    logger.info(f"Loaded {len(batch)} attributes.")


def load_projects():
    """Load Project nodes and their hierarchical node trees."""
    data = load_json("Project-Paweb.Project.json")
    logger.info(f"Processing {len(data)} projects...")

    for idx, proj in enumerate(data):
        proj_uid = proj["_id"]["$oid"]
        proj_name = proj.get("name", f"Project_{idx}")

        # Create Project node
        db.execute_write(
            """
            MERGE (p:Project {uid: $uid})
            SET p.name = $name,
                p.ecuFamilyName = $ecuFamily,
                p.version = $version,
                p.status = $status
            """,
            {
                "uid": proj_uid,
                "name": proj_name,
                "ecuFamily": proj.get("ecuFamilyName", ""),
                "version": proj.get("projectVersion", {}).get("version", 0),
                "status": proj.get("projectVersion", {}).get("status", ""),
            },
        )

        # Process node tree
        nodes = proj.get("node", [])
        if nodes:
            _load_node_tree(proj_uid, None, nodes)

        if (idx + 1) % 20 == 0:
            logger.info(f"  Processed {idx + 1}/{len(data)} projects...")

    logger.info(f"Loaded {len(data)} projects.")


def _load_node_tree(project_uid: str, parent_node_key: str | None, nodes: list):
    """Recursively load node tree into Neo4j."""
    for node in nodes:
        node_id = str(node["id"].get("$numberLong", node["id"])) if isinstance(node["id"], dict) else str(node["id"])
        node_name = node.get("name", "")
        node_type_id = node.get("nodeTypeId", "")
        direct_id = ""
        if "directId" in node:
            direct_id = str(node["directId"].get("$numberLong", node["directId"])) if isinstance(node["directId"], dict) else str(node["directId"])
        container_id = ""
        if "containerId" in node:
            container_id = str(node["containerId"].get("$numberLong", node["containerId"])) if isinstance(node["containerId"], dict) else str(node["containerId"])
        has_warning = node.get("hasWarning", False)

        # Unique key: project_uid + node_id (node ids are unique within a project)
        node_key = f"{project_uid}_{node_id}"

        # Create Node
        db.execute_write(
            """
            MERGE (n:Node {nodeKey: $nodeKey})
            SET n.nodeId = $nodeId,
                n.name = $name,
                n.directId = $directId,
                n.containerId = $containerId,
                n.hasWarning = $hasWarning
            """,
            {
                "nodeKey": node_key,
                "nodeId": node_id,
                "name": node_name,
                "directId": direct_id,
                "containerId": container_id,
                "hasWarning": has_warning,
            },
        )

        # Link Node -> NodeType
        if node_type_id:
            db.execute_write(
                """
                MATCH (n:Node {nodeKey: $nodeKey})
                MATCH (nt:NodeType {uid: $ntUid})
                MERGE (n)-[:OF_TYPE]->(nt)
                """,
                {"nodeKey": node_key, "ntUid": node_type_id},
            )

        # Link to Project or Parent
        if parent_node_key is None:
            db.execute_write(
                """
                MATCH (p:Project {uid: $projUid})
                MATCH (n:Node {nodeKey: $nodeKey})
                MERGE (p)-[:HAS_NODE]->(n)
                """,
                {"projUid": project_uid, "nodeKey": node_key},
            )
        else:
            db.execute_write(
                """
                MATCH (parent:Node {nodeKey: $parentKey})
                MATCH (child:Node {nodeKey: $childKey})
                MERGE (parent)-[:HAS_CHILD]->(child)
                """,
                {"parentKey": parent_node_key, "childKey": node_key},
            )

        # Process assignments
        assignments = node.get("assignments", [])
        for i, assign in enumerate(assignments):
            attr_id = assign.get("attributeId", "")
            lib_value = assign.get("libraryValue", "")
            user_value = assign.get("value", "")
            assign_key = f"{node_key}_assign_{i}"

            db.execute_write(
                """
                MERGE (a:Assignment {assignKey: $assignKey})
                SET a.libraryValue = $libValue,
                    a.value = $userValue
                WITH a
                MATCH (n:Node {nodeKey: $nodeKey})
                MERGE (n)-[:HAS_ASSIGNMENT]->(a)
                """,
                {
                    "assignKey": assign_key,
                    "nodeKey": node_key,
                    "libValue": lib_value,
                    "userValue": user_value,
                },
            )

            # Link Assignment -> Attribute
            if attr_id:
                db.execute_write(
                    """
                    MATCH (a:Assignment {assignKey: $assignKey})
                    MATCH (attr:Attribute {uid: $attrUid})
                    MERGE (a)-[:FOR_ATTRIBUTE]->(attr)
                    """,
                    {"assignKey": assign_key, "attrUid": attr_id},
                )

        # Recurse into children
        children = node.get("children", [])
        if children:
            _load_node_tree(project_uid, node_key, children)


def run_loader():
    """Main entry point for data loading."""
    db.connect()
    try:
        clear_database()
        create_constraints()
        load_widgets()
        load_node_types()
        load_attributes()
        load_projects()
        logger.info("Data loading complete!")
    finally:
        db.close()


if __name__ == "__main__":
    run_loader()
