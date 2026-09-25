"""Posting integrations for the daily bot.

Buffer: REST app registration is closed to new developers; the current path
for a single personal account is a GraphQL API at api.buffer.com using a
personal API key (Bearer auth), generated from Buffer's own account
settings under "API". The createPost mutation below was checked against
Buffer's live schema via introspection (2026-09-24).

Reddit and Facebook Marketplace are intentionally NOT here -- neither has a
usable free/hobbyist API path as of 2026 (Reddit closed personal-script
token approval; Marketplace has no individual-seller API at all). Both are
parked as a later headless-browser-automation phase, done knowingly with
the ToS-violation/ban risk accepted -- see the roadmap memory. Do not add a
direct-API integration for either here; there isn't one available.
"""

import requests

BUFFER_GRAPHQL_ENDPOINT = "https://api.buffer.com/graphql"

# createPost returns a union: PostActionSuccess on success, or one of several
# error types that all implement MutationError.
BUFFER_CREATE_POST_MUTATION = """
mutation CreatePost($input: CreatePostInput!) {
  createPost(input: $input) {
    __typename
    ... on PostActionSuccess { post { id status } }
    ... on MutationError { message }
  }
}
"""


def _buffer_create_post(api_key: str, post_input: dict) -> str:
    response = requests.post(
        BUFFER_GRAPHQL_ENDPOINT,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={"query": BUFFER_CREATE_POST_MUTATION, "variables": {"input": post_input}},
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    if data.get("errors"):
        raise RuntimeError(f"Buffer API error: {data['errors']}")
    result = data["data"]["createPost"]
    if result["__typename"] != "PostActionSuccess":
        raise RuntimeError(f"Buffer {result['__typename']}: {result.get('message')}")
    post = result["post"]
    return f"{post['status']} (buffer post {post['id']})"


def _base_input(channel_id: str, caption: str, image_url: str, alt_text: str, draft: bool) -> dict:
    return {
        "channelId": channel_id,
        "text": caption,
        "assets": [{"image": {"url": image_url, "metadata": {"altText": alt_text}}}],
        "mode": "shareNow",
        "schedulingType": "automatic",
        "needsApproval": False,
        "saveToDraft": draft,
    }


def post_to_buffer(
    cfg,
    image_url: str,
    caption: str,
    title: str,
    alt_text: str,
    piece_url: str,
    draft: bool = False,
) -> dict[str, str]:
    """Posts to Instagram and Pinterest via Buffer.

    Each platform is attempted independently, so one failing doesn't stop
    the other. Returns {"instagram": result, "pinterest": result}, where a
    result is either the Buffer post status/id or "error: ...".
    draft=True saves drafts in Buffer instead of publishing -- for testing.
    """
    instagram_input = _base_input(
        cfg.buffer_instagram_channel_id, caption, image_url, alt_text, draft
    )
    instagram_input["metadata"] = {"instagram": {"type": "post", "shouldShareToFeed": True}}

    pinterest_input = _base_input(
        cfg.buffer_pinterest_channel_id, caption, image_url, alt_text, draft
    )
    pinterest_input["metadata"] = {
        "pinterest": {
            "boardServiceId": cfg.buffer_pinterest_board_id,
            "title": title,
            # Pins link straight to the piece's page, where it can be bought.
            "url": piece_url,
        }
    }

    results = {}
    for platform, post_input in (("instagram", instagram_input), ("pinterest", pinterest_input)):
        try:
            results[platform] = _buffer_create_post(cfg.buffer_api_key, post_input)
        except Exception as exc:
            results[platform] = f"error: {exc}"
    return results
