"""Posting integrations for the daily bot.

Buffer: REST app registration is closed to new developers; the current path
for a single personal account is a GraphQL API at api.buffer.com using a
personal API key (Bearer auth), generated from Buffer's own account
settings under "API". The exact mutation name/fields below are a
best-effort based on Buffer's documented auth pattern, NOT yet verified
against their live schema -- Buffer's docs page 404'd when checked, and no
personal API key existed yet to introspect the schema directly. Confirm/
adjust BUFFER_CREATE_POST_MUTATION before relying on this in production.

Reddit and Facebook Marketplace are intentionally NOT here -- neither has a
usable free/hobbyist API path as of 2026 (Reddit closed personal-script
token approval; Marketplace has no individual-seller API at all). Both are
parked as a later headless-browser-automation phase, done knowingly with
the ToS-violation/ban risk accepted -- see the roadmap memory. Do not add a
direct-API integration for either here; there isn't one available.
"""

import requests

BUFFER_GRAPHQL_ENDPOINT = "https://api.buffer.com/graphql"

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
