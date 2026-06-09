from neo4j import GraphDatabase
from app.config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD


class Neo4jConnection:
    """Manages Neo4j driver lifecycle and query execution."""

    def __init__(self):
        self._driver = None

    def connect(self):
        self._driver = GraphDatabase.driver(
            NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD)
        )
        self._driver.verify_connectivity()
        return self

    def close(self):
        if self._driver:
            self._driver.close()

    def execute_read(self, cypher: str, parameters: dict = None) -> list[dict]:
        """Execute a read-only Cypher query and return list of record dicts."""
        with self._driver.session() as session:
            result = session.run(cypher, parameters or {})
            return [record.data() for record in result]

    def execute_write(self, cypher: str, parameters: dict = None):
        """Execute a write Cypher query."""
        with self._driver.session() as session:
            session.run(cypher, parameters or {})

    def execute_write_batch(self, cypher: str, batch: list[dict]):
        """Execute a write Cypher query with UNWIND for batch insert."""
        with self._driver.session() as session:
            session.run(cypher, {"batch": batch})

    def get_schema_info(self) -> str:
        """Return a text description of the current graph schema."""
        labels = self.execute_read(
            "CALL db.labels() YIELD label RETURN collect(label) AS labels"
        )
        rels = self.execute_read(
            "CALL db.relationshipTypes() YIELD relationshipType "
            "RETURN collect(relationshipType) AS types"
        )
        props = self.execute_read(
            "CALL db.schema.nodeTypeProperties() YIELD nodeType, propertyName "
            "RETURN nodeType, collect(propertyName) AS properties"
        )

        schema_parts = ["## Neo4j Graph Schema\n"]
        if labels:
            schema_parts.append(f"**Node Labels:** {', '.join(labels[0]['labels'])}\n")
        if rels:
            schema_parts.append(f"**Relationships:** {', '.join(rels[0]['types'])}\n")
        if props:
            schema_parts.append("**Node Properties:**")
            for row in props:
                schema_parts.append(f"  - {row['nodeType']}: {row['properties']}")

        return "\n".join(schema_parts)


# Singleton instance
db = Neo4jConnection()
