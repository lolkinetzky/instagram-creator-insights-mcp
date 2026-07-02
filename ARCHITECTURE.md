# Where I'd take this in production

This repo is a working prototype: fetch → flatten → index → analyze, in one process, on one machine. That's the right shape for a demo and the wrong shape for a product. Here's the honest gap analysis — the layers I'd add to turn this into a production creator-intelligence system, in the order I'd add them.

## 1. Ingestion becomes a pipeline, not a function call

Today, data is fetched when a user asks. Production inverts that: a worker queue (Celery, SQS + Lambda, take your pick) continuously refreshes tracked creators on TTL policies — profiles daily, posts hourly for creators in active campaigns.

- **Retries with exponential backoff** and per-provider rate limiting.
- **A second data provider with failover.** Scraping APIs die without warning; the product can't die with them.
- **Raw payloads land in object storage before parsing.** When Instagram changes its response shape (it will), you replay history through a fixed parser instead of losing it.

## 2. Storage: snapshots, not state

The biggest conceptual jump. Storing a creator's *current* follower count is trivia; storing a **time series of snapshots** is the product. Growth velocity, engagement decay, "this creator is about to pop" — every valuable signal lives in the derivative, not the value.

- **Postgres as source of truth**: `creators`, `posts`, `creator_snapshots` (follower count + engagement per crawl).
- **pgvector for embeddings**, replacing the standalone Chroma store. One database means transactional consistency between metadata filters and vector search, and one less system to operate until scale genuinely forces the split.
- Embeddings carry a **model-version column**, so upgrading the embedding model is a background re-index, not a flag day.

## 3. The ML layer grows past text

The prototype embeds bio + captions with a local MiniLM model. Fine for a demo; leaving signal on the table for a product.

- **Hosted embeddings** (Voyage, OpenAI) for quality; the local-model tradeoff stops making sense once relevance drives revenue.
- **Multimodal embeddings** of reel thumbnails/frames (CLIP-family). Creators are visual — captions undersell them.
- **Audio fingerprinting on reels** — which songs creators actually use. This unlocks "creators who organically feature artists like X," which is the single most music-native query this data can answer.
- **Audience-quality scoring** (fake-follower detection). It's the first question every brand asks.

## 4. Retrieval goes hybrid

Pure vector search is a demo. Production retrieval is **vector + BM25 + hard metadata filters**, with a reranker on top.

"Similar to @creator" is table stakes. "Similar to @creator, 50–500K followers, Brazil, >3% engagement, posted in the last 30 days" is a product.

## 5. The LLM layer gets engineering discipline

- **Structured outputs**: fit analyses become JSON-schema'd scores (audience match, content match, brand safety, priority), not prose. Prose is for humans; scores are for ranking 500 candidates.
- **Prompt versioning with an eval set.** You cannot improve a prompt you can't measure, and you cannot safely change one either.
- **Batch API** for bulk campaign scoring — half the cost when latency doesn't matter.
- **Per-tenant cost tracking**, because "how much does an analysis cost" becomes a pricing question.

## 6. Service, ops, compliance

- The MCP server becomes one thin client of a **FastAPI service** — the same brain also serves a web dashboard and a public API.
- **Tracing across the whole chain** (fetch → parse → embed → LLM), with LLM calls logged for eval and audit.
- **Data-deletion workflows** and provider-ToS hygiene. A creator-data company gets asked about GDPR in every enterprise deal; the answer needs to be a button, not a scramble.

## What I'd build first

If I had one week, not a roadmap: **layers 2 and 4**. Postgres + pgvector with snapshot history, and hybrid filtered search. Those two turn "neat prototype" into "small version of a real discovery product" — everything else compounds on top of them.
