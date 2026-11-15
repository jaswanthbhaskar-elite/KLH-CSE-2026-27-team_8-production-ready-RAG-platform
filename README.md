\# Production-Ready RAG Platform using FastAPI and React



An AI-powered knowledge management platform designed to provide governed, secure and context-aware access to institutional knowledge using Retrieval-Augmented Generation (RAG).



\## Project Information



| Detail | Information |

|---|---|

| Institution | KLH University |

| Branch | CSE |

| Academic Year | 2026â€“27 |

| Team ID | 8 |

| Project Supervisor | Dr. K. Anusha |

| Project Phase | CO1 â€“ Governance and Document Ingestion |



\## Team Members



| Name | University ID |

|---|---|

| V Deepak | 2520030503 |

| B Jaswanth Bhaskar | 2520030553 |

| C Ashesh | 2520030114 |



\## Abstract



The Production-Ready RAG Platform is designed to provide reliable and governed access to institutional knowledge. The system aims to combine document ingestion, information retrieval and AI-generated responses while enforcing role-based access control, purpose limitations and auditability.



The platform will support document uploads, text extraction, chunking, knowledge retrieval and context-grounded responses. It will prioritize security, transparency and traceability to ensure that users receive information appropriate to their access permissions.



\## Technology Stack



\- \*\*Backend:\*\* Python, FastAPI, Pydantic

\- \*\*Frontend:\*\* React (planned)

\- \*\*Database:\*\* SQLite

\- \*\*Document Processing:\*\* PyPDF

\- \*\*Testing:\*\* Pytest, HTTPX

\- \*\*API Documentation:\*\* Swagger UI / OpenAPI

\- \*\*Version Control:\*\* Git and GitHub



\## Current Project Status



\### Completed

\- FastAPI backend initialization.

\- Governed query API.

\- Role-based access control.

\- Purpose-based authorization.

\- Structured audit logging with trace IDs.

\- PDF and TXT document ingestion.

\- Text extraction and document chunking.

\- SQLite document and chunk storage.

\- Admin-only document management endpoints.

\- Automated test suite: 233 tests passed.



\### In Progress

\- Retrieval-Augmented Generation pipeline.

\- Embedding generation and vector-based retrieval.

\- Hybrid search and grounded response generation.

\- React frontend integration.

\- Evaluation and security enhancements.

## Project Structure

```text
KLH-CSE-2026-27-team_8-production-ready-RAG-platform/
¦
+-- src/
¦   +-- backend/
¦       +-- governance/
¦       +-- ingestion/
¦       +-- routers/
¦       +-- tests/
¦       +-- docs/
¦       +-- main.py
¦       +-- database.py
¦       +-- requirements.txt
¦
+-- docs/
+-- data/
+-- results/
+-- reports/
+-- README.md
+-- .gitignore
```

## Setup and Execution

### 1. Clone the repository

```bash
git clone https://github.com/jaswanthbhaskar-elite/KLH-CSE-2026-27-team_8-production-ready-RAG-platform.git
cd KLH-CSE-2026-27-team_8-production-ready-RAG-platform
```

### 2. Navigate to the backend

```bash
cd src/backend
```

### 3. Create a virtual environment

```bash
python -m venv venv
```

### 4. Activate the environment

Windows PowerShell:

```powershell
.\venv\Scripts\Activate.ps1
```

### 5. Install dependencies

```bash
pip install -r requirements.txt
```

### 6. Start the API server

```bash
uvicorn main:app --reload
```

### 7. Access API documentation

Open:

http://127.0.0.1:8000/docs

### 8. Run automated tests

```bash
python -m pytest -v
```

\## Security Note



The current prototype uses placeholder user identities for role mapping. It is intended for academic development and testing, not production deployment. Real authentication and authorization infrastructure will be required before production use.



\## Documentation



Additional technical documentation is available in:



\- `backend/docs/API.md`

\- `backend/docs/DOCUMENT\_INGESTION.md`



\## Repository



GitHub: https://github.com/jaswanthbhaskar-elite/KLH-CSE-2026-27-team_8-production-ready-RAG-platform



\---



\*\*Project Supervisor:\*\* Dr. K. Anusha

\*\*KLH University | CSE | Academic Year 2026â€“27\*\*
