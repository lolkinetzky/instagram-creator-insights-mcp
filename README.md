# Instagram Creator Insights MCP Server 🎧

An MCP server that turns Claude into an influencer-marketing analyst. Point it at an Instagram handle and it can pull the profile, crunch the engagement numbers, tell you whether the creator is a good fit for a music-adjacent brand campaign, draft the outreach message, and find similar creators via embeddings — all in one conversation.

I built this because I wanted to see what influencer marketing tooling looks like when it's an agentic pipeline instead of a dashboard: not just *fetching* data, but analyzing it, acting on it, and getting smarter as it goes.

## The tools

| Tool | What it does |
|---|---|
| `get_creator_profile` | Pulls a creator's public profile — followers, bio, verification, the works |
| `get_recent_reels` | Recent posts with likes, comments, views, and computed engagement stats |
| `analyze_creator` | Chains profile + posts into a Claude call and returns a fit analysis for music-adjacent partnerships, with an outreach priority |
| `draft_outreach` | Writes a personalized outreach message for a given brand + campaign brief, referencing the creator's actual content |
| `find_similar_creators` | "Who else looks like this creator?" — semantic search over everyone you've researched |

## How it works

```
Instagram handle
      │
      ▼
ScrapeCreators API ──► flatten (~500KB of JSON → ~1KB snapshot)
      │                        │
      ▼                        ▼
Claude (analysis /      Chroma vector index
outreach drafting)      (auto-updated on every fetch)
                               │
                               ▼
                     find_similar_creators
```

A few design decisions I'd actually defend in a code review:

- **Aggressive flattening.** The raw Instagram profile response is enormous — DASH video manifests, five resolutions of every thumbnail, tracking tokens. Claude does not need to know the bitrate ladder of a reel from 2021. A parsing layer boils it down to the ~1KB that matters: bio, follower counts, recent posts with engagement metrics, and a computed engagement rate.
- **One API call, not two.** The profile endpoint already includes recent posts, so the analysis tools make a single ScrapeCreators call. Half the credits, same insight.
- **The similarity index builds itself.** Every time any tool fetches a profile, that creator gets embedded (bio + category + recent captions) and upserted into a local Chroma collection. Research ten creators, and `find_similar_creators` already has a neighborhood to search. No ingestion step, no cron job, no "please run the indexer."
- **Embeddings run locally.** Chroma's default ONNX model (all-MiniLM-L6-v2) — free, offline, zero extra API keys. At production scale you'd swap in a hosted embedding model and pgvector, but for a working prototype the tradeoff math says keep it simple.

Curious what the production version would look like? The full gap analysis is in [ARCHITECTURE.md](ARCHITECTURE.md).

## Setup

```bash
git clone https://github.com/lolkinetzky/instagram-creator-insights-mcp.git
cd instagram-creator-insights-mcp
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

Create a `.env` in the project root:

```
SCRAPECREATORS_API_KEY=your-key-here
ANTHROPIC_API_KEY=your-other-key-here
```

([ScrapeCreators](https://scrapecreators.com) for the Instagram data, [Anthropic](https://console.anthropic.com) for the analysis brain.)

## Hook it up to Claude

Add to your Claude Desktop or Claude Code MCP config:

```json
{
  "mcpServers": {
    "creator-insights": {
      "command": "/absolute/path/to/venv/bin/python",
      "args": ["/absolute/path/to/src/server.py"]
    }
  }
}
```

Then ask Claude things like:

> "Analyze @somecreator for a fit with our smart-amp launch, and if they look good, draft the outreach for Fender."

> "Who have we researched that's similar to @somecreator?"

## Stack

Python 3.14 · [MCP](https://modelcontextprotocol.io) (FastMCP) · Claude Sonnet · ScrapeCreators · ChromaDB · httpx

---

*Note: this uses publicly available Instagram data via a third-party API. Be a good citizen — respect rate limits and creators' privacy.*
