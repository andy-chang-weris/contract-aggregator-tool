# Interactive UI Chat Panel

Standalone test harness for the collapsible contract chatbot component. This folder lets us validate the UI against the real RAG agent without modifying the parent `../index.html` or `../proxy.py`.

## Files

- `index.html`: copied dashboard UI for integration testing, with the chat panel mounted into `main`.
- `chat-panel.js`: importable ES module that exports `ContractChatPanel` and `mountContractChatPanel`.
- `chat-panel.css`: scoped chat panel styles.
- `chat_server.py`: isolated Flask API that wraps `rag.RagAgent.ask(question)`.

## Run

From the `agent` directory:

```bash
python interactive-ui/chat_server.py
```

In a second terminal from the `agent` directory:

```bash
python -m http.server 8088 -d interactive-ui
```

Then open:

```text
http://localhost:8088
```

The component calls:

```text
POST http://localhost:5055/api/chat
```

Request body:

```json
{ "question": "Which opportunities fit an AI cloud vendor?" }
```

Response shape:

```json
{
  "answer": "...",
  "index_source": "db",
  "sources": [
    {
      "score": 0.82,
      "metadata": {},
      "title": "...",
      "agency": "...",
      "deadline": "...",
      "url": "..."
    }
  ]
}
```

## Later Parent UI Integration

When this is greenlit, copy the same component mount into the parent `../index.html` and move the `/api/chat` route from `chat_server.py` into `../proxy.py`.
