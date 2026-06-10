"""
LLM Service: Converts natural language questions to Cypher queries using
Azure OpenAI (gpt-4o-mini), executes them against Neo4j, and summarises the
results back into a conversational, natural-language answer.
"""

import re
import json
import logging

import httpx

from app.config import (
    LLM_BASE_URL, LLM_API_KEY, LLM_SUBSCRIPTION_KEY, LLM_MODEL, LLM_API_VERSION,
)
from app.db import db

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a Neo4j Cypher query expert. You translate natural language questions into Cypher queries for a graph database.

## Graph Schema

**Node Labels:**
- Project (uid, name, ecuFamilyName, version, status)
- Node (nodeKey, nodeId, name, directId, containerId, hasWarning)
- NodeType (uid, name, label, handler)
- Attribute (uid, name, displayName, directName, showAttributeName, enumerant, sort)
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
- "Assignment" holds actual data values for a node. "libraryValue" is the system default, "value" is the user-modified override (empty string means not modified).
- "Attribute" defines the field (name, displayName) and links to both a NodeType and a Widget. "enumerant" is the list of allowed values for dropdown-style attributes.
- A Project may NOT have a meaningful "name" in this dataset (often a single project). Prefer filtering nodes by NodeType or by node name rather than by project name.

## Rules:
1. ONLY generate READ queries (MATCH, OPTIONAL MATCH, RETURN, WITH, WHERE, ORDER BY, LIMIT, UNWIND over collected lists). NEVER generate CREATE, DELETE, SET, MERGE, REMOVE, DROP, LOAD CSV, or CALL {} IN TRANSACTIONS.
2. Always use LIMIT unless the user explicitly asks for all results or a count. Default LIMIT 25.
3. Return meaningful property values (names, labels, values), not raw node references.
4. When matching by name, use case-insensitive matching: toLower(n.name) CONTAINS toLower('search_term')
5. A node is "user-modified" when its Assignment.value is not null and not the empty string.
6. Return ONLY the Cypher query — no explanations, no markdown fences.
7. If the question cannot be answered from this schema, return exactly: // CANNOT_ANSWER: <reason>

## Examples:

User: "Show all projects"
Cypher: MATCH (p:Project) RETURN p.name, p.ecuFamilyName, p.version, p.status LIMIT 25

User: "List all node types"
Cypher: MATCH (nt:NodeType) RETURN nt.name, nt.label, nt.handler ORDER BY nt.name

User: "Show nodes of type functionModule"
Cypher: MATCH (n:Node)-[:OF_TYPE]->(nt:NodeType {name: 'functionModule'}) RETURN n.name, n.nodeId LIMIT 25

User: "What are the allowed values for the padClass attribute?"
Cypher: MATCH (a:Attribute {name: 'padClass'}) RETURN a.displayName, a.enumerant LIMIT 5

User: "What attributes does a pin have?"
Cypher: MATCH (a:Attribute)-[:BELONGS_TO_TYPE]->(nt:NodeType {name: 'pin'}) RETURN a.name, a.displayName, a.directName ORDER BY a.sort

User: "Show assignments for node CAN0_RXD"
Cypher: MATCH (n:Node)-[:HAS_ASSIGNMENT]->(asgn:Assignment)-[:FOR_ATTRIBUTE]->(attr:Attribute) WHERE toLower(n.name) CONTAINS toLower('CAN0_RXD') RETURN n.name, attr.displayName, asgn.libraryValue, asgn.value LIMIT 50

User: "Which nodes have user-modified values?"
Cypher: MATCH (n:Node)-[:HAS_ASSIGNMENT]->(asgn:Assignment) WHERE asgn.value IS NOT NULL AND asgn.value <> '' RETURN DISTINCT n.name, count(asgn) AS modifiedCount ORDER BY modifiedCount DESC LIMIT 25

User: "Show the children of node BS_CY329-CAN"
Cypher: MATCH (parent:Node {name: 'BS_CY329-CAN'})-[:HAS_CHILD]->(child:Node)-[:OF_TYPE]->(nt:NodeType) RETURN child.name, nt.label LIMIT 25
"""

ANSWER_PROMPT = """You are a helpful data assistant for an engineering configuration tool.
Given a user's question and the rows returned by a Neo4j query, write a short,
friendly, natural-language answer (1-3 sentences).

Guidelines:
- Lead with the direct answer (a count, a name, a yes/no).
- Mention notable specifics from the rows when useful, but do NOT dump the whole table — it is shown separately.
- If there are no rows, say so plainly and, if helpful, suggest a refinement.
- Never invent data that is not in the rows. Be concise."""

# Write/DDL clauses we must never run. Matched as whole words so a node named
# "CREATE_X" or a property value won't trigger a false positive.
WRITE_KEYWORDS = [
    "CREATE", "DELETE", "SET", "MERGE", "REMOVE", "DROP",
    "DETACH", "FOREACH", "LOAD CSV", "CALL DBMS",
]
_WRITE_RE = re.compile(
    r"\b(" + "|".join(k.replace(" ", r"\s+") for k in WRITE_KEYWORDS) + r")\b",
    re.IGNORECASE,
)


def _strip_string_literals(cypher: str) -> str:
    """Remove quoted string contents so the safety check ignores literals."""
    no_single = re.sub(r"'(?:[^'\\]|\\.)*'", "''", cypher)
    return re.sub(r'"(?:[^"\\]|\\.)*"', '""', no_single)


def _is_write_query(cypher: str) -> bool:
    return bool(_WRITE_RE.search(_strip_string_literals(cypher)))


def _call_llm(messages: list[dict], max_tokens: int = 500, temperature: float = 0.0) -> str:
    url = f"{LLM_BASE_URL}/chat/completions?api-version={LLM_API_VERSION}"
    headers = {
        "Content-Type": "application/json",
        "api-key": LLM_API_KEY,
        "Ocp-Apim-Subscription-Key": LLM_SUBSCRIPTION_KEY,
    }
    payload = {
        "model": LLM_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    with httpx.Client(timeout=30.0) as client:
        response = client.post(url, json=payload, headers=headers)
        response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"].strip()


def generate_cypher(user_question: str) -> str:
    """Call Azure OpenAI to convert a natural language question to Cypher."""
    cypher = _call_llm(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_question},
        ],
        max_tokens=500,
    )

    # Strip markdown fences if the model wrapped the query.
    if cypher.startswith("```"):
        cypher = re.sub(r"^```[a-zA-Z]*\n?", "", cypher)
        cypher = re.sub(r"\n?```$", "", cypher).strip()

    logger.info(f"Generated Cypher: {cypher}")
    return cypher


def summarize_results(user_question: str, data: list[dict]) -> str:
    """Turn query rows into a conversational answer via a second LLM call."""
    # Cap the payload so we don't blow the context window on large results.
    preview = data[:30]
    rows_json = json.dumps(preview, default=str, ensure_ascii=False)
    context = (
        f"Question: {user_question}\n"
        f"Total rows returned: {len(data)}\n"
        f"Rows (first {len(preview)} shown): {rows_json}"
    )
    return _call_llm(
        [
            {"role": "system", "content": ANSWER_PROMPT},
            {"role": "user", "content": context},
        ],
        max_tokens=250,
        temperature=0.2,
    )


def _fallback_answer(data: list[dict]) -> str:
    if not data:
        return "I couldn't find any results for that question."
    if len(data) == 1:
        return "I found 1 result."
    return f"I found {len(data)} results."


def chat(user_question: str) -> dict:
    """
    Full chat pipeline:
      1. Convert the question to Cypher.
      2. Guard against write operations.
      3. Execute against Neo4j.
      4. Summarise the rows into a conversational answer.
    """
    try:
        cypher = generate_cypher(user_question)

        if cypher.startswith("// CANNOT_ANSWER"):
            return {
                "answer": cypher.replace("// CANNOT_ANSWER:", "").strip()
                or "I can't answer that with the available data.",
                "cypher": None,
                "data": [],
                "error": None,
            }

        if _is_write_query(cypher):
            return {
                "answer": "I can only run read-only queries, and the generated query "
                          "contained a write operation, so I didn't execute it.",
                "cypher": cypher,
                "data": [],
                "error": "Write operation blocked",
            }

        results = db.execute_read(cypher)

        try:
            answer = summarize_results(user_question, results)
        except Exception as e:  # summarisation is best-effort
            logger.warning(f"Answer summarisation failed, using fallback: {e}")
            answer = _fallback_answer(results)

        return {"answer": answer, "cypher": cypher, "data": results, "error": None}

    except httpx.HTTPStatusError as e:
        logger.error(f"LLM API error: {e}")
        return {
            "answer": "I couldn't reach the language model service. Please try again.",
            "cypher": None,
            "data": [],
            "error": str(e),
        }
    except Exception as e:
        logger.error(f"Chat error: {e}")
        return {
            "answer": f"Something went wrong while answering: {e}",
            "cypher": None,
            "data": [],
            "error": str(e),
        }
