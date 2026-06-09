"""
LLM Service: Converts natural language questions to Cypher queries
using Azure OpenAI (gpt-4o-mini) and executes them against Neo4j.
"""

import httpx
import json
import logging

from app.config import LLM_BASE_URL, LLM_API_KEY, LLM_SUBSCRIPTION_KEY, LLM_MODEL, LLM_API_VERSION
from app.db import db

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a Neo4j Cypher query expert. You translate natural language questions into Cypher queries for a graph database.

## Graph Schema

**Node Labels:**
- Project (uid, name, ecuFamilyName, version, status)
- Node (nodeKey, nodeId, name, directId, containerId, hasWarning)
- NodeType (uid, name, label, handler)
- Attribute (uid, name, displayName, directName, showAttributeName, sort)
- Widget (uid, type, description)
- Assignment (assignKey, libraryValue, value)

**Relationships:**
- (Project)-[:HAS_NODE]->(Node) — top-level nodes of a project
- (Node)-[:HAS_CHILD]->(Node) — parent-child node hierarchy
- (Node)-[:OF_TYPE]->(NodeType) — node's type
- (Node)-[:HAS_ASSIGNMENT]->(Assignment) — node's data values
- (Assignment)-[:FOR_ATTRIBUTE]->(Attribute) — which attribute the assignment is for
- (Attribute)-[:USES_WIDGET]->(Widget) — UI widget type for the attribute
- (Attribute)-[:BELONGS_TO_TYPE]->(NodeType) — which node type the attribute belongs to
- (NodeType)-[:CHILD_TYPE_OF]->(NodeType) — type hierarchy

**Key Concepts:**
- A "Node" represents a hardware/software component (circuit, function module, interface, pin, etc.)
- "NodeType" defines what kind of component it is (resultingCircuit, functionModule, function, interfaceVariant, pin, resourceRequirement, connectionNet, etc.)
- "Assignment" holds actual data values for a node. "libraryValue" is the system default, "value" is user-modified.
- "Attribute" defines the field (name, displayName) and links to both a NodeType and a Widget.

## Rules:
1. ONLY generate READ queries (MATCH, RETURN, WITH, WHERE, ORDER BY, LIMIT). NEVER generate CREATE, DELETE, SET, MERGE, or REMOVE.
2. Always use LIMIT unless the user explicitly asks for all results. Default LIMIT 25.
3. Return meaningful property values, not just node references.
4. When searching by name, use case-insensitive matching: toLower(n.name) CONTAINS toLower('search_term')
5. Return ONLY the Cypher query, no explanations or markdown formatting.
6. If the question cannot be answered with the schema, return: // CANNOT_ANSWER: <reason>

## Examples:

User: "Show all projects"
Cypher: MATCH (p:Project) RETURN p.name, p.ecuFamilyName, p.version, p.status LIMIT 25

User: "List all node types"
Cypher: MATCH (nt:NodeType) RETURN nt.name, nt.label, nt.handler ORDER BY nt.name

User: "Show nodes of type functionModule in project FCOMP"
Cypher: MATCH (p:Project {name: 'FCOMP'})-[:HAS_NODE]->(n:Node)-[:OF_TYPE]->(nt:NodeType {name: 'functionModule'}) RETURN n.name, n.nodeId LIMIT 25

User: "What attributes does a pin have?"
Cypher: MATCH (a:Attribute)-[:BELONGS_TO_TYPE]->(nt:NodeType {name: 'pin'}) RETURN a.name, a.displayName, a.directName ORDER BY a.sort

User: "Show assignments for node CAN0_RXD"
Cypher: MATCH (n:Node)-[:HAS_ASSIGNMENT]->(asgn:Assignment)-[:FOR_ATTRIBUTE]->(attr:Attribute) WHERE toLower(n.name) CONTAINS toLower('CAN0_RXD') RETURN n.name, attr.displayName, asgn.libraryValue, asgn.value LIMIT 50

User: "Which nodes have user-modified values?"
Cypher: MATCH (n:Node)-[:HAS_ASSIGNMENT]->(asgn:Assignment) WHERE asgn.value IS NOT NULL AND asgn.value <> '' RETURN DISTINCT n.name, count(asgn) AS modifiedCount ORDER BY modifiedCount DESC LIMIT 25

User: "Show the children of node BS_CY329-CAN"
Cypher: MATCH (parent:Node {name: 'BS_CY329-CAN'})-[:HAS_CHILD]->(child:Node)-[:OF_TYPE]->(nt:NodeType) RETURN child.name, nt.label LIMIT 25
"""


def generate_cypher(user_question: str) -> str:
    """Call Azure OpenAI to convert natural language to Cypher."""
    url = f"{LLM_BASE_URL}/chat/completions?api-version={LLM_API_VERSION}"

    headers = {
        "Content-Type": "application/json",
        "api-key": LLM_API_KEY,
        "Ocp-Apim-Subscription-Key": LLM_SUBSCRIPTION_KEY,
    }

    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_question},
        ],
        "temperature": 0.0,
        "max_tokens": 500,
    }

    with httpx.Client(timeout=30.0) as client:
        response = client.post(url, json=payload, headers=headers)
        response.raise_for_status()

    result = response.json()
    cypher = result["choices"][0]["message"]["content"].strip()

    # Clean up potential markdown formatting
    if cypher.startswith("```"):
        lines = cypher.split("\n")
        cypher = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])

    logger.info(f"Generated Cypher: {cypher}")
    return cypher


def chat(user_question: str) -> dict:
    """
    Full chat pipeline:
    1. Convert question to Cypher
    2. Execute against Neo4j
    3. Return results
    """
    try:
        cypher = generate_cypher(user_question)

        if cypher.startswith("// CANNOT_ANSWER"):
            return {
                "answer": cypher.replace("// CANNOT_ANSWER:", "").strip(),
                "cypher": None,
                "data": [],
                "error": None,
            }

        # Safety check: reject write operations
        dangerous_keywords = ["CREATE", "DELETE", "SET ", "MERGE", "REMOVE", "DROP", "DETACH"]
        cypher_upper = cypher.upper()
        for kw in dangerous_keywords:
            if kw in cypher_upper:
                return {
                    "answer": "I can only execute read queries. The generated query contained write operations.",
                    "cypher": cypher,
                    "data": [],
                    "error": "Write operation blocked",
                }

        results = db.execute_read(cypher)

        if not results:
            answer = "No results found for your query."
        elif len(results) == 1:
            answer = f"Found 1 result."
        else:
            answer = f"Found {len(results)} results."

        return {
            "answer": answer,
            "cypher": cypher,
            "data": results,
            "error": None,
        }

    except httpx.HTTPStatusError as e:
        logger.error(f"LLM API error: {e}")
        return {
            "answer": "Failed to connect to the LLM service.",
            "cypher": None,
            "data": [],
            "error": str(e),
        }
    except Exception as e:
        logger.error(f"Chat error: {e}")
        return {
            "answer": f"An error occurred: {str(e)}",
            "cypher": None,
            "data": [],
            "error": str(e),
        }
