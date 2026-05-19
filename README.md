# Hayden

An AI-powered Indian stock market research assistant. Hayden monitors NSE filings, clusters and scores market news by impact, and lets you chat with company annual reports using an agentic RAG pipeline.

The backend is a FastAPI server with OpenAI-powered agents that can download, index, and search annual report PDFs using hybrid retrieval (keyword + FAISS vector search). Sub-agents run in parallel with vision fallback for scanned pages. The frontend is a Next.js dashboard with a command center for chatting with reports, a news feed with AI-scored impact clustering, and a portfolio view.

## Setup

```bash
# Backend
cd backend
cp .env.example .env  # add your OpenAI key
pip install -r requirements.txt
python main.py

# Frontend
cd frontend
cp .env.local.example .env.local
npm install && npm run dev
```

## License

MIT
