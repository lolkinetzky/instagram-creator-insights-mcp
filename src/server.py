import json
import os
from datetime import datetime, timezone

import chromadb
import httpx
import anthropic
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()

SCRAPECREATORS_API_KEY = os.getenv("SCRAPECREATORS_API_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

SCRAPECREATORS_BASE = "https://api.scrapecreators.com/v1/instagram"

mcp = FastMCP("Instagram Creator Insights")
client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


# --- ScrapeCreators helpers ---------------------------------------------------

async def fetch_profile(handle: str) -> dict:
    async with httpx.AsyncClient(timeout=30) as http:
        response = await http.get(
            f"{SCRAPECREATORS_BASE}/profile",
            params={"handle": handle},
            headers={"x-api-key": SCRAPECREATORS_API_KEY},
        )
        response.raise_for_status()
        return response.json()


def _iso_date(timestamp: int | None) -> str | None:
    if not timestamp:
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d")


def parse_profile(raw: dict) -> dict:
    """Flatten the ScrapeCreators profile response into the fields that matter
    for partnership analysis. The raw response nests everything under
    data.user and is dominated by CDN URLs and video manifests we don't need."""
    user = (raw.get("data") or {}).get("user") or {}
    return {
        "username": user.get("username"),
        "full_name": user.get("full_name"),
        "biography": user.get("biography"),
        "followers": (user.get("edge_followed_by") or {}).get("count"),
        "following": (user.get("edge_follow") or {}).get("count"),
        "total_posts": (user.get("edge_owner_to_timeline_media") or {}).get("count"),
        "is_verified": user.get("is_verified"),
        "is_business_account": user.get("is_business_account"),
        "is_professional_account": user.get("is_professional_account"),
        "category": user.get("category_name") or user.get("business_category_name"),
        "external_url": user.get("external_url"),
    }


def parse_timeline_posts(raw: dict, limit: int = 12) -> list[dict]:
    """Extract recent posts (reels, videos, images, carousels) with engagement
    metrics from the profile response's timeline media."""
    user = (raw.get("data") or {}).get("user") or {}
    edges = (user.get("edge_owner_to_timeline_media") or {}).get("edges") or []
    posts = []
    for edge in edges[:limit]:
        node = edge.get("node") or {}
        caption_edges = (node.get("edge_media_to_caption") or {}).get("edges") or []
        caption = caption_edges[0]["node"].get("text", "") if caption_edges else ""
        posts.append({
            "url": f"https://www.instagram.com/p/{node.get('shortcode')}/",
            "type": node.get("product_type") or node.get("__typename"),
            "is_video": node.get("is_video"),
            "caption": caption[:300],
            "likes": (node.get("edge_liked_by") or {}).get("count"),
            "comments": (node.get("edge_media_to_comment") or {}).get("count"),
            "views": node.get("video_view_count"),
            "posted_date": _iso_date(node.get("taken_at_timestamp")),
        })
    return posts


def engagement_summary(profile: dict, posts: list[dict]) -> dict:
    """Compute simple engagement stats across recent posts."""
    if not posts:
        return {}
    likes = [p["likes"] for p in posts if p.get("likes")]
    comments = [p["comments"] for p in posts if p.get("comments")]
    views = [p["views"] for p in posts if p.get("views")]
    followers = profile.get("followers") or 0
    avg_likes = sum(likes) / len(likes) if likes else 0
    avg_comments = sum(comments) / len(comments) if comments else 0
    summary = {
        "posts_sampled": len(posts),
        "avg_likes": round(avg_likes),
        "avg_comments": round(avg_comments),
        "avg_video_views": round(sum(views) / len(views)) if views else None,
    }
    if followers:
        summary["engagement_rate_pct"] = round(
            (avg_likes + avg_comments) / followers * 100, 2
        )
    return summary


def creator_snapshot(raw: dict) -> dict:
    """Full flattened view of a creator: profile + recent posts + stats.
    This is what gets passed to Claude instead of the raw API response."""
    profile = parse_profile(raw)
    posts = parse_timeline_posts(raw)
    return {
        "profile": profile,
        "engagement": engagement_summary(profile, posts),
        "recent_posts": posts,
    }


# --- Embedding index ------------------------------------------------------------
#
# Every profile fetch upserts the creator into a local Chroma collection, so the
# similarity index grows passively as the other tools are used. Documents are
# embedded with Chroma's default local model (all-MiniLM-L6-v2 via ONNX) — no
# extra API keys or network calls needed.

_collection = None


def get_collection():
    global _collection
    if _collection is None:
        path = os.getenv(
            "CREATOR_DB_PATH",
            os.path.join(os.path.dirname(__file__), "..", "chroma_db"),
        )
        chroma = chromadb.PersistentClient(path=path)
        _collection = chroma.get_or_create_collection(
            "creators", metadata={"hnsw:space": "cosine"}
        )
    return _collection


def creator_document(snapshot: dict) -> str:
    """Text representation of a creator used for embedding: who they are and
    what they post about."""
    profile = snapshot["profile"]
    captions = [p["caption"] for p in snapshot["recent_posts"] if p.get("caption")]
    parts = [
        f"{profile.get('full_name') or ''} (@{profile.get('username')})",
        profile.get("biography") or "",
        f"Category: {profile['category']}" if profile.get("category") else "",
        "Recent post captions: " + " | ".join(captions) if captions else "",
    ]
    return "\n".join(part for part in parts if part)


def index_snapshot(snapshot: dict) -> None:
    """Upsert a creator into the vector index, keyed by username."""
    username = snapshot["profile"].get("username")
    if not username:
        return
    metadata = {
        "username": username,
        "full_name": snapshot["profile"].get("full_name") or "",
        "followers": snapshot["profile"].get("followers") or 0,
        "engagement_rate_pct": snapshot["engagement"].get("engagement_rate_pct") or 0.0,
        "indexed_at": datetime.now(timezone.utc).isoformat(),
    }
    get_collection().upsert(
        ids=[username],
        documents=[creator_document(snapshot)],
        metadatas=[metadata],
    )


# --- MCP tools ----------------------------------------------------------------

@mcp.tool()
async def get_creator_profile(handle: str) -> dict:
    """Get public Instagram profile data for a creator by handle."""
    raw = await fetch_profile(handle)
    snapshot = creator_snapshot(raw)
    index_snapshot(snapshot)
    return snapshot["profile"]


@mcp.tool()
async def get_recent_reels(handle: str, limit: int = 10) -> dict:
    """Get recent posts for an Instagram creator with engagement metrics."""
    raw = await fetch_profile(handle)
    snapshot = creator_snapshot(raw)
    index_snapshot(snapshot)
    return {
        "handle": handle,
        "engagement": snapshot["engagement"],
        "posts": snapshot["recent_posts"][:limit],
    }


@mcp.tool()
async def analyze_creator(handle: str) -> str:
    """
    Analyze an Instagram creator's profile and recent posts,
    returning a natural language summary of their fit for
    music-adjacent brand partnerships.
    """
    raw = await fetch_profile(handle)
    snapshot = creator_snapshot(raw)
    index_snapshot(snapshot)

    prompt = f"""You are an influencer marketing analyst for a music industry platform.

Given the following Instagram creator data, provide a concise analysis of:
1. Their audience size and engagement signals
2. The type of content they produce
3. Whether they would be a good fit for music-adjacent brand partnerships
4. A recommended outreach priority: High / Medium / Low

Creator data:
{json.dumps(snapshot, indent=2)}

Keep your response to 150 words or less. Be direct and specific."""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )

    return message.content[0].text


@mcp.tool()
async def draft_outreach(handle: str, brand_name: str, campaign_brief: str) -> str:
    """
    Analyze an Instagram creator and draft a personalized outreach message
    for a music-adjacent brand partnership.
    """
    raw = await fetch_profile(handle)
    snapshot = creator_snapshot(raw)
    index_snapshot(snapshot)

    prompt = f"""You are an influencer marketing specialist at a music industry platform.

Using the creator data below, draft a short, personalized outreach message on behalf of {brand_name}.

The message should:
- Reference something specific about their content or style
- Feel human and genuine, not templated
- Clearly explain the collaboration opportunity
- Be concise — 100 words or less
- End with a soft call to action

Campaign brief:
{campaign_brief}

Creator data:
{json.dumps(snapshot, indent=2)}

Write only the outreach message itself, no preamble."""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )

    return message.content[0].text


@mcp.tool()
async def find_similar_creators(handle: str, limit: int = 5) -> dict:
    """
    Find previously-indexed creators whose content and audience are most
    similar to the given creator, using embedding similarity over their
    bio, category, and recent post captions.

    Creators are indexed automatically whenever any other tool fetches
    their profile, so similarity results improve as the server is used.
    """
    collection = get_collection()

    # Use the stored document if this creator is already indexed — avoids
    # spending a ScrapeCreators credit on a repeat lookup.
    existing = collection.get(ids=[handle])
    if existing["ids"]:
        query_doc = existing["documents"][0]
    else:
        raw = await fetch_profile(handle)
        snapshot = creator_snapshot(raw)
        index_snapshot(snapshot)
        query_doc = creator_document(snapshot)

    results = collection.query(
        query_texts=[query_doc],
        n_results=limit + 1,  # +1 because the query creator matches itself
    )

    matches = []
    for creator_id, distance, metadata in zip(
        results["ids"][0], results["distances"][0], results["metadatas"][0]
    ):
        if creator_id == handle:
            continue
        matches.append({
            "username": creator_id,
            "full_name": metadata.get("full_name"),
            "followers": metadata.get("followers"),
            "engagement_rate_pct": metadata.get("engagement_rate_pct"),
            "similarity": round(1 - distance, 3),  # cosine distance -> similarity
        })

    return {
        "query_handle": handle,
        "indexed_creators": collection.count(),
        "matches": matches[:limit],
    }


if __name__ == "__main__":
    mcp.run()
