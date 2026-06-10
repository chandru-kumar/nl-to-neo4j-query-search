import logging

from neo4j import GraphDatabase
from neo4j.exceptions import ServiceUnavailable, AuthError
from app.config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD

logger = logging.getLogger(__name__)


class Neo4jConnection:
    """Manages Neo4j driver lifecycle and query execution."""

    def __init__(self):
        self._driver = None

    def connect(self, verify: bool = True):
        """
        Create the driver. Creating a driver does NOT open a socket, so this
        is cheap and never fails on its own. If `verify` is set we probe the
        server, but a failure is logged (not raised) so the app can still
        start while Neo4j is booting — the connection is retried lazily on the
        first query.
        """
        if self._driver is None:
            self._driver = GraphDatabase.driver(
                NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD)
            )
        if verify:
            try:
                self._driver.verify_connectivity()
                logger.info(f"Connected to Neo4j at {NEO4J_URI}")
            except (ServiceUnavailable, AuthError, OSError) as e:
                logger.warning(
                    f"Neo4j not reachable yet at {NEO4J_URI} ({e}). "
                    "The API will keep running and retry on the first query."
                )
        return self

    def close(self):
        if self._driver:
            self._driver.close()
            self._driver = None

    def _ensure_driver(self):
        """Lazily (re)create the driver if it was never opened or got closed."""
        if self._driver is None:
            self.connect(verify=False)

    def is_healthy(self) -> bool:
        """Cheap connectivity probe for the /health endpoint."""
        try:
            self._ensure_driver()
            self._driver.verify_connectivity()
            return True
        except Exception:
            return False

    def execute_read(self, cypher: str, parameters: dict = None) -> list[dict]:
        """Execute a read-only Cypher query and return list of record dicts."""
        self._ensure_driver()
        with self._driver.session() as session:
            result = session.run(cypher, parameters or {})
            return [record.data() for record in result]

    def execute_write(self, cypher: str, parameters: dict = None):
        """Execute a write Cypher query."""
        self._ensure_driver()
        with self._driver.session() as session:
            session.run(cypher, parameters or {})

    def execute_write_batch(self, cypher: str, batch: list[dict]):
        """Execute a write Cypher query with UNWIND for batch insert."""
        self._ensure_driver()
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
