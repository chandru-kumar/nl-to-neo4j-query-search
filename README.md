# PA Web Chatbot

A natural-language chatbot that lets users query a **Neo4j Graph Database** using plain English. The application translates user questions into Cypher queries via **Azure OpenAI (GPT-4o-mini)**, executes them against the graph, and returns structured results.

## Tech Stack

| Layer      | Technology                   |
|------------|------------------------------|
| UI         | Streamlit 1.37               |
| API        | Python FastAPI 0.115 + Uvicorn |
| Database   | Neo4j (Docker)               |
| LLM        | Azure OpenAI GPT-4o-mini     |

---

## Application Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                         USER (Browser)                           │
│                     http://localhost:8501                         │
└────────────────────────────┬─────────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│                    STREAMLIT UI (Port 8501)                       │
│   • Chat interface with history                                  │
│   • Sidebar: Load Data, Example Questions                        │
│   • Displays Cypher queries + tabular results                    │
└────────────────────────────┬─────────────────────────────────────┘
                             │  HTTP (POST /chat, POST /load-data)
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│                   FASTAPI SERVER (Port 8000)                      │
│                                                                  │
│   ┌─────────────┐   ┌──────────────┐   ┌────────────────────┐   │
│   │  /health    │   │  /chat       │   │  /load-data        │   │
│   │  /schema    │   │  NL → Cypher │   │  JSON → Graph      │   │
│   └─────────────┘   └──────┬───────┘   └────────────────────┘   │
│                             │                                    │
└─────────────────────────────┼────────────────────────────────────┘
                              │
              ┌───────────────┼───────────────┐
              ▼                               ▼
┌───────────────────────┐       ┌───────────────────────────────┐
│   AZURE OPENAI        │       │        NEO4J (Docker)         │
│   GPT-4o-mini         │       │                               │
│                       │       │   bolt://localhost:7687        │
│   NL → Cypher query   │       │   Browser: localhost:7474     │
│   generation          │       │                               │
└───────────────────────┘       │   Graph Model:                │
                                │   Project → Node → NodeType   │
                                │   Node → Assignment → Attr    │
                                │   Attribute → Widget          │
                                └───────────────────────────────┘
```

---

## Request Flow

```
1. User types a question in the Streamlit chat UI
2. Streamlit sends POST /chat { "question": "..." } to FastAPI
3. FastAPI calls Azure OpenAI with the graph schema + user question
4. LLM returns a Cypher query
5. FastAPI validates the query (read-only, no writes allowed)
6. FastAPI executes the Cypher query against Neo4j
7. Results are returned as JSON to Streamlit
8. Streamlit displays the Cypher query and results as a table
```

---

## Source Data

The application ingests data from four JSON files exported from a MongoDB-based system:

| File | Contents |
|------|----------|
| `Project-Paweb.Project-1.json` | Array of Projects, each containing a **recursive tree of Nodes** (100–1000+ per project) |
| `Project-Paweb.NodeType.json` | Node type definitions (e.g. Resulting Circuit, Function Module, Interface Variant) with parent hierarchy |
| `Project-Paweb.Attribute.json` | Attribute definitions linked to a NodeType and a Widget |
| `Project-Paweb.Widget.json` | UI widget types (Text, Dropdown, Checkbox, Number, String-List, etc.) |

### Data Relationships

- **Projects** contain an array of **Nodes** in a recursive tree (`children[]`).
- Each **Node** has a `nodeTypeId` referencing a **NodeType**.
- Nodes carry **Assignments** — dynamic key-value data. Each assignment has:
  - `libraryValue` — the default/system value
  - `value` — user-modified data (overrides libraryValue when present)
  - `attributeId` — references an **Attribute** definition
- **Attributes** define what data a node type can hold, and reference a **Widget** for UI rendering.
- **NodeTypes** form a parent-child hierarchy (e.g. Function Module → Function → Interface).

### Neo4j Graph Model

The JSON data is transformed into the following graph structure:

```
(Project)-[:HAS_NODE]->(Node)-[:HAS_CHILD]->(Node)
(Node)-[:OF_TYPE]->(NodeType)-[:CHILD_TYPE_OF]->(NodeType)
(Node)-[:HAS_ASSIGNMENT]->(Assignment)-[:FOR_ATTRIBUTE]->(Attribute)
(Attribute)-[:USES_WIDGET]->(Widget)
(Attribute)-[:BELONGS_TO_TYPE]->(NodeType)
```

**Node properties:** `name`, `directId`, `containerId`  
**Assignment properties:** `libraryValue`, `value` (user override)  
**Attribute properties:** `name`, `displayName`, `directName`  
**NodeType properties:** `name`, `label`, `handler`  
**Widget properties:** `type`, `description`

---

## NL-to-Cypher Query Generation

The application uses **Azure OpenAI GPT-4o-mini** to convert natural language questions into Cypher queries:

```
LLM_BASE_URL = https://aoai-farm.bosch-temp.com/api/openai/deployments/askbosch-prod-farm-openai-gpt-4o-mini-2024-07-18
LLM_MODEL    = gpt-4o-mini
API_VERSION  = 2024-08-01-preview
Auth         = Ocp-Apim-Subscription-Key header
```

The LLM receives the full graph schema (node labels, relationship types, properties) as context and generates **read-only** Cypher queries. Write operations (CREATE, DELETE, SET, MERGE, DROP) are blocked at the API level.

---

## Project Structure

```
pa-web-chatbot/
├── app/
│   ├── __init__.py
│   ├── api.py              # FastAPI endpoints
│   ├── config.py           # Environment variable loader
│   ├── data_loader.py      # JSON → Neo4j graph ingestion
│   ├── db.py               # Neo4j connection manager
│   ├── llm_service.py      # Azure OpenAI NL→Cypher service
│   └── streamlit_app.py    # Streamlit chat UI
├── Project-Paweb.*.json    # Source data files
├── docker-compose.yml      # Neo4j container
├── requirements.txt        # Python dependencies
├── .env                    # Environment config (not committed)
└── .gitignore
```

---

## Prerequisites

- **Python 3.13** (3.14 may lack pre-built wheels for some packages)
- **Docker Desktop** (for Neo4j)
- **Azure OpenAI** access with a valid subscription key

---

## Setup & Run

### 1. Start Neo4j

```powershell
docker compose up -d
```

Neo4j will be available at:
- Browser: http://localhost:7474
- Bolt: bolt://localhost:7687
- Credentials: `neo4j` / `password123`

### 2. Create Python Virtual Environment

```powershell
py -3.13 -m venv .venv
.venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -r requirements.txt
```

### 3. Configure Environment

Copy `.env.example` to `.env` and fill in your credentials:

```env
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=password123
LLM_BASE_URL=<your-azure-openai-endpoint>
LLM_SUBSCRIPTION_KEY=<your-subscription-key>
LLM_MODEL=gpt-4o-mini
LLM_API_VERSION=2024-08-01-preview
API_HOST=0.0.0.0
API_PORT=8000
```

### 4. Run the API Server

```powershell
.venv\Scripts\Activate.ps1
uvicorn app.api:app --host 0.0.0.0 --port 8000 --reload
```

API available at: http://localhost:8000

### 5. Run the Streamlit UI

Open a **separate terminal**:

```powershell
.venv\Scripts\Activate.ps1
streamlit run app/streamlit_app.py
```

UI available at: http://localhost:8501

### 6. Load Data

Once both services are running:
- Click **"Load Data into Neo4j"** in the Streamlit sidebar, OR
- Send a POST request:

```powershell
Invoke-WebRequest -Method POST -Uri "http://localhost:8000/load-data"
```

---

## API Endpoints

| Method | Endpoint     | Description                          |
|--------|-------------|--------------------------------------|
| GET    | `/health`   | Health check                         |
| GET    | `/schema`   | Returns current Neo4j graph schema   |
| POST   | `/chat`     | NL question → Cypher → results       |
| POST   | `/load-data`| Load JSON data files into Neo4j      |

### POST /chat

```json
// Request
{ "question": "How many projects are there?" }

// Response
{
  "question": "How many projects are there?",
  "cypher": "MATCH (p:Project) RETURN count(p) AS total_projects",
  "results": [{ "total_projects": 221 }],
  "error": null
}
```

---

## Example Questions

- How many projects are there?
- List all node types
- Which attributes use the "Dropdown" widget?
- Show me the children of node type "Plant"
- What attributes does the "Machine" node type have?
