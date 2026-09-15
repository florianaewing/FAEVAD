"""Posting integrations for the daily bot.

Reddit: a real, stable OAuth API -- implemented and should work as-is once
real credentials are in the config file.

Buffer: REST app registration is closed to new developers; the current path
for a single personal account is a GraphQL API at api.buffer.com using a
personal API key (Bearer auth), generated from Buffer's own account
settings under "API". The exact mutation name/fields below are a
best-effort based on Buffer's documented auth pattern, NOT yet verified
against their live schema -- Buffer's docs page 404'd when checked, and no
personal API key existed yet to introspect the schema directly. Confirm/
adjust BUFFER_CREATE_POST_MUTATION before relying on this in production.
"""

import requests

BUFFER_GRAPHQL_ENDPOINT = "https://api.buffer.com/graphql"

REDDIT_ACCESS_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
REDDIT_SUBMIT_URL = "https://oauth.reddit.com/api/submit"

# TODO: verify against Buffer's actual GraphQL schema once a personal API
# key exists (Buffer account -> Settings -> API). This is a best guess at
# the shape of a "create post" mutation, not a confirmed working call.
BUFFER_CREATE_POST_MUTATION = """
mutation CreatePost($channelId: ID!, $text: String!, $mediaUrl: String!) {
  createPost(input: {channelId: $channelId, text: $text, media: {photo: $mediaUrl}}) {
    id
    status
  }
}
"""


def _buffer_create_post(api_key: str, channel_id: str, text: str, media_url: str) -> str:
    response = requests.post(
        BUFFER_GRAPHQL_ENDPOINT,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "query": BUFFER_CREATE_POST_MUTATION,
            "variables": {"channelId": channel_id, "text": text, "mediaUrl": media_url},
        },
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    if data.get("errors"):
        raise RuntimeError(f"Buffer API error: {data['errors']}")
    return "ok"


def post_to_buffer(cfg, image_url: str, caption: str) -> tuple[str, str]:
    """Posts to Instagram and Pinterest via Buffer. Returns (instagram_result, pinterest_result)."""
    instagram = _buffer_create_post(
        cfg.buffer_api_key, cfg.buffer_instagram_channel_id, caption, image_url
    )
    pinterest = _buffer_create_post(
        cfg.buffer_api_key, cfg.buffer_pinterest_channel_id, caption, image_url
    )
    return instagram, pinterest


def _reddit_access_token(cfg) -> str:
    response = requests.post(
        REDDIT_ACCESS_TOKEN_URL,
        auth=(cfg.reddit_client_id, cfg.reddit_client_secret),
        data={"grant_type": "refresh_token", "refresh_token": cfg.reddit_refresh_token},
        headers={"User-Agent": cfg.reddit_user_agent},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["access_token"]


def post_to_reddit(cfg, title: str, url: str) -> str:
    """Submits a link post (title = the artist's caption, link = the piece's buy page)."""
    access_token = _reddit_access_token(cfg)
    response = requests.post(
        REDDIT_SUBMIT_URL,
        headers={
            "Authorization": f"Bearer {access_token}",
            "User-Agent": cfg.reddit_user_agent,
        },
        data={
            "sr": cfg.reddit_subreddit,
            "kind": "link",
            "title": title,
            "url": url,
            "api_type": "json",
            "resubmit": "true",
        },
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    errors = data.get("json", {}).get("errors", [])
    if errors:
        raise RuntimeError(f"Reddit API error: {errors}")
    return "ok"
