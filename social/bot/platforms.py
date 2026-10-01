"""Posting integrations for the daily bot.

Instagram, Pinterest and X go through Buffer (its free plan's three
channels). Bluesky, Mastodon, Threads and the Facebook Page are posted to
directly, since each has a free official API for posting to your own
account. Every direct integration returns the public link to the new post,
or raises PlatformError saying which step failed and what the platform
answered -- that text is what ends up in the artist's Telegram bug report.

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

import html
import mimetypes
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests

BUFFER_GRAPHQL_ENDPOINT = "https://api.buffer.com/graphql"
BUFFER_PLATFORMS = ("instagram", "pinterest", "x")

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


def _buffer_graphql(api_key: str, query: str, variables: dict) -> dict:
    response = requests.post(
        BUFFER_GRAPHQL_ENDPOINT,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={"query": query, "variables": variables},
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    if data.get("errors"):
        raise RuntimeError(f"Buffer API error: {data['errors']}")
    return data["data"]


def _buffer_create_post(api_key: str, post_input: dict) -> str:
    """Creates a post and returns its Buffer post id."""
    result = _buffer_graphql(api_key, BUFFER_CREATE_POST_MUTATION, {"input": post_input})["createPost"]
    if result["__typename"] != "PostActionSuccess":
        raise RuntimeError(f"Buffer {result['__typename']}: {result.get('message')}")
    return result["post"]["id"]


BUFFER_POST_STATUS_QUERY = """
query PostStatus($input: PostInput!) {
  post(input: $input) { status externalLink error { message } }
}
"""


def get_buffer_post_status(api_key: str, post_id: str) -> dict:
    """Returns {"status", "link", "error"} for a Buffer post.

    status is one of Buffer's PostStatus values -- "sent" and "error" are
    final; "scheduled"/"sending" mean Buffer hasn't finished publishing yet.
    """
    post = _buffer_graphql(api_key, BUFFER_POST_STATUS_QUERY, {"input": {"id": post_id}})["post"]
    return {
        "status": post["status"],
        "link": post.get("externalLink"),
        "error": (post.get("error") or {}).get("message"),
    }


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


def buffer_channel_id(cfg, platform: str) -> str:
    return {
        "instagram": cfg.buffer_instagram_channel_id,
        "pinterest": cfg.buffer_pinterest_channel_id,
        "x": cfg.buffer_x_channel_id,
    }[platform]


def post_to_buffer_channel(
    cfg,
    platform: str,
    text: str,
    image_url: str,
    alt_text: str,
    title: str | None = None,
    destination_url: str | None = None,
    draft: bool = False,
) -> str:
    """Hands one post (Instagram, Pinterest or X) to Buffer; returns its Buffer post id.

    Buffer publishes asynchronously -- follow up with get_buffer_post_status.
    draft=True saves a draft in Buffer instead of publishing -- for testing.
    """
    post_input = _base_input(buffer_channel_id(cfg, platform), text, image_url, alt_text, draft)
    if platform == "instagram":
        post_input["metadata"] = {"instagram": {"type": "post", "shouldShareToFeed": True}}
    elif platform == "pinterest":
        post_input["metadata"] = {
            "pinterest": {
                "boardServiceId": cfg.buffer_pinterest_board_id,
                "title": title,
                # Pins link straight to the piece's page, where it can be bought.
                "url": destination_url,
            }
        }
    return _buffer_create_post(cfg.buffer_api_key, post_input)


BUFFER_POST_METRICS_QUERY = """
query PostMetrics($input: PostInput!) {
  post(input: $input) { metrics { name value } }
}
"""


def get_buffer_post_metrics(api_key: str, post_id: str) -> dict[str, float]:
    """Engagement numbers Buffer has collected for a published post."""
    post = _buffer_graphql(api_key, BUFFER_POST_METRICS_QUERY, {"input": {"id": post_id}})["post"]
    return {metric["name"]: metric["value"] for metric in post["metrics"]}


class PlatformError(Exception):
    """A direct platform post failed at a specific step."""

    def __init__(self, step: str, status: int | None = None, body: str | None = None):
        self.step = step
        self.status = status
        self.body = body
        message = f"{step} failed"
        if status is not None:
            message += f" (HTTP {status})"
        if body:
            message += f": {body[:1000]}"
        super().__init__(message)


def _checked(response: requests.Response, step: str, ok=(200,)) -> requests.Response:
    if response.status_code not in ok:
        raise PlatformError(step, response.status_code, response.text)
    return response


def _request(step: str, method: str, url: str, ok=(200,), **kwargs) -> requests.Response:
    """requests.request that turns network failures into PlatformError too."""
    try:
        response = requests.request(method, url, timeout=60, **kwargs)
    except requests.RequestException as exc:
        raise PlatformError(step, body=f"{type(exc).__name__}: {exc}") from exc
    return _checked(response, step, ok)


@dataclass
class Published:
    """A post that went up on a platform: its public link, and the platform's
    own id for it (used later to fetch its stats and new comments)."""

    link: str
    ref: str


@dataclass
class Comment:
    ref: str
    author: str
    text: str
    link: str


def _image_file(image_path: Path) -> tuple[bytes, str]:
    mime = mimetypes.guess_type(image_path.name)[0] or "application/octet-stream"
    return image_path.read_bytes(), mime


# --- Bluesky ---------------------------------------------------------------
# An app password (Bluesky -> Settings -> Privacy and security -> App
# passwords) signs in without the account's real password. Links and
# hashtags in Bluesky posts only work when marked up as "facets" with UTF-8
# byte offsets. Reading posts and replies needs no sign-in at all.

BLUESKY_PDS_URL = "https://bsky.social"
BLUESKY_PUBLIC_API_URL = "https://public.api.bsky.app"


def _byte_span(text: str, index: int, fragment: str) -> dict:
    start = len(text[:index].encode("utf-8"))
    return {"byteStart": start, "byteEnd": start + len(fragment.encode("utf-8"))}


def _bluesky_facets(text: str, link_url: str | None, hashtags: list[str]) -> list[dict]:
    facets = []
    if link_url and (index := text.rfind(link_url)) >= 0:
        facets.append({
            "index": _byte_span(text, index, link_url),
            "features": [{"$type": "app.bsky.richtext.facet#link", "uri": link_url}],
        })
    for tag in hashtags:
        if (index := text.rfind(f"#{tag}")) >= 0:
            facets.append({
                "index": _byte_span(text, index, f"#{tag}"),
                "features": [{"$type": "app.bsky.richtext.facet#tag", "tag": tag}],
            })
    return facets


def _bluesky_post_link(uri: str, handle: str) -> str:
    return f"https://bsky.app/profile/{handle}/post/{uri.rsplit('/', 1)[1]}"


def post_to_bluesky(
    handle: str,
    app_password: str,
    text: str,
    image_path: Path,
    alt_text: str,
    link_url: str | None,
    hashtags: list[str],
) -> Published:
    session = _request(
        "Bluesky sign-in",
        "POST",
        f"{BLUESKY_PDS_URL}/xrpc/com.atproto.server.createSession",
        json={"identifier": handle, "password": app_password},
    ).json()
    auth = {"Authorization": f"Bearer {session['accessJwt']}"}

    image_bytes, mime = _image_file(image_path)
    blob = _request(
        "Bluesky image upload",
        "POST",
        f"{BLUESKY_PDS_URL}/xrpc/com.atproto.repo.uploadBlob",
        headers={**auth, "Content-Type": mime},
        data=image_bytes,
    ).json()["blob"]

    record = {
        "$type": "app.bsky.feed.post",
        "text": text,
        "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "facets": _bluesky_facets(text, link_url, hashtags),
        "embed": {
            "$type": "app.bsky.embed.images",
            "images": [{"alt": alt_text, "image": blob}],
        },
    }
    created = _request(
        "Bluesky post",
        "POST",
        f"{BLUESKY_PDS_URL}/xrpc/com.atproto.repo.createRecord",
        headers=auth,
        json={"repo": session["did"], "collection": "app.bsky.feed.post", "record": record},
    ).json()
    return Published(_bluesky_post_link(created["uri"], session["handle"]), created["uri"])


def bluesky_metrics(uri: str) -> dict[str, float]:
    post = _request(
        "Bluesky stats",
        "GET",
        f"{BLUESKY_PUBLIC_API_URL}/xrpc/app.bsky.feed.getPosts",
        params={"uris": uri},
    ).json()["posts"][0]
    return {name: post.get(f"{name}Count", 0) for name in ("like", "repost", "reply", "quote")}


def bluesky_comments(uri: str) -> list[Comment]:
    thread = _request(
        "Bluesky replies",
        "GET",
        f"{BLUESKY_PUBLIC_API_URL}/xrpc/app.bsky.feed.getPostThread",
        params={"uri": uri, "depth": 1},
    ).json()["thread"]
    comments = []
    for reply in thread.get("replies", []):
        post = reply.get("post")
        if not post:  # blocked or deleted replies come back without a post
            continue
        handle = post["author"]["handle"]
        comments.append(Comment(
            post["uri"], f"@{handle}", post["record"].get("text", ""),
            _bluesky_post_link(post["uri"], handle),
        ))
    return comments


# --- Mastodon --------------------------------------------------------------
# Access token from the account's server: Preferences -> Development -> New
# application, with the read:statuses, write:media and write:statuses scopes.

MASTODON_MEDIA_WAIT_SECONDS = 60


def post_to_mastodon(
    instance_url: str, access_token: str, text: str, image_path: Path, alt_text: str
) -> Published:
    auth = {"Authorization": f"Bearer {access_token}"}
    image_bytes, mime = _image_file(image_path)
    media = _request(
        "Mastodon image upload",
        "POST",
        f"{instance_url}/api/v2/media",
        ok=(200, 202),
        headers=auth,
        files={"file": (image_path.name, image_bytes, mime)},
        data={"description": alt_text},
    ).json()

    # Large uploads are processed in the background (202, url still null);
    # a status can't attach the media until processing is done.
    deadline = time.monotonic() + MASTODON_MEDIA_WAIT_SECONDS
    while media.get("url") is None:
        if time.monotonic() >= deadline:
            raise PlatformError(
                "Mastodon image processing",
                body=f"still processing after {MASTODON_MEDIA_WAIT_SECONDS}s",
            )
        time.sleep(2)
        media = _request(
            "Mastodon image processing check",
            "GET",
            f"{instance_url}/api/v1/media/{media['id']}",
            ok=(200, 206),
            headers=auth,
        ).json()

    status = _request(
        "Mastodon post",
        "POST",
        f"{instance_url}/api/v1/statuses",
        # Makes a retried request post once, not twice.
        headers={**auth, "Idempotency-Key": str(uuid.uuid4())},
        data={"status": text, "media_ids[]": [media["id"]]},
    ).json()
    return Published(status["url"], status["id"])


def mastodon_metrics(instance_url: str, access_token: str, status_id: str) -> dict[str, float]:
    status = _request(
        "Mastodon stats",
        "GET",
        f"{instance_url}/api/v1/statuses/{status_id}",
        headers={"Authorization": f"Bearer {access_token}"},
    ).json()
    return {
        "favourites": status["favourites_count"],
        "boosts": status["reblogs_count"],
        "replies": status["replies_count"],
    }


def _plain_text(markup: str) -> str:
    """Mastodon statuses are HTML; turn one into readable text."""
    markup = re.sub(r"<br\s*/?>|</p>", "\n", markup)
    return html.unescape(re.sub(r"<[^>]+>", "", markup)).strip()


def mastodon_comments(instance_url: str, access_token: str, status_id: str) -> list[Comment]:
    context = _request(
        "Mastodon replies",
        "GET",
        f"{instance_url}/api/v1/statuses/{status_id}/context",
        headers={"Authorization": f"Bearer {access_token}"},
    ).json()
    return [
        Comment(reply["id"], f"@{reply['account']['acct']}", _plain_text(reply["content"]), reply["url"])
        for reply in context.get("descendants", [])
    ]


# --- Threads ---------------------------------------------------------------
# Needs a Meta developer app with the Threads API use case, and the FAEVAD
# Threads account added as a tester on it (no App Review needed for posting
# to your own account). Long-lived tokens last 60 days and have to be
# refreshed before then -- see refresh_threads_token.

THREADS_API_URL = "https://graph.threads.net/v1.0"
THREADS_CONTAINER_WAIT_SECONDS = 120


def refresh_threads_token(access_token: str) -> str:
    """Trades a long-lived Threads token for a fresh 60-day one."""
    return _request(
        "Threads token refresh",
        "GET",
        "https://graph.threads.net/refresh_access_token",
        params={"grant_type": "th_refresh_token", "access_token": access_token},
    ).json()["access_token"]


def post_to_threads(
    user_id: str,
    access_token: str,
    text: str,
    image_url: str,
    alt_text: str,
    topic_tag: str | None,
) -> Published:
    # Threads fetches the image from image_url itself, then publishing is a
    # separate call once that "container" has finished processing.
    container_fields = {
        "media_type": "IMAGE",
        "image_url": image_url,
        "text": text,
        "alt_text": alt_text,
        "access_token": access_token,
    }
    if topic_tag:
        container_fields["topic_tag"] = topic_tag
    container_id = _request(
        "Threads media container",
        "POST",
        f"{THREADS_API_URL}/{user_id}/threads",
        data=container_fields,
    ).json()["id"]

    deadline = time.monotonic() + THREADS_CONTAINER_WAIT_SECONDS
    while True:
        container = _request(
            "Threads container status",
            "GET",
            f"{THREADS_API_URL}/{container_id}",
            params={"fields": "status,error_message", "access_token": access_token},
        ).json()
        if container.get("status") == "FINISHED":
            break
        if container.get("status") in ("ERROR", "EXPIRED"):
            raise PlatformError(
                "Threads media processing",
                body=f"{container['status']}: {container.get('error_message')}",
            )
        if time.monotonic() >= deadline:
            raise PlatformError(
                "Threads media processing",
                body=f"not finished after {THREADS_CONTAINER_WAIT_SECONDS}s "
                f"(status {container.get('status')})",
            )
        time.sleep(5)

    post_id = _request(
        "Threads publish",
        "POST",
        f"{THREADS_API_URL}/{user_id}/threads_publish",
        data={"creation_id": container_id, "access_token": access_token},
    ).json()["id"]
    permalink = _request(
        "Threads permalink lookup",
        "GET",
        f"{THREADS_API_URL}/{post_id}",
        params={"fields": "permalink", "access_token": access_token},
    ).json()["permalink"]
    return Published(permalink, post_id)


def threads_metrics(access_token: str, post_id: str) -> dict[str, float]:
    data = _request(
        "Threads stats",
        "GET",
        f"{THREADS_API_URL}/{post_id}/insights",
        params={"metric": "views,likes,replies,reposts,quotes", "access_token": access_token},
    ).json()["data"]
    return {item["name"]: item["values"][0]["value"] for item in data}


def threads_comments(access_token: str, post_id: str) -> list[Comment]:
    replies = _request(
        "Threads replies",
        "GET",
        f"{THREADS_API_URL}/{post_id}/replies",
        params={"fields": "id,text,username,permalink", "access_token": access_token},
    ).json().get("data", [])
    return [
        Comment(reply["id"], f"@{reply.get('username', '?')}", reply.get("text", ""), reply.get("permalink", ""))
        for reply in replies
    ]


# --- Facebook Page ---------------------------------------------------------
# A Page access token obtained from a long-lived user token doesn't expire,
# so unlike Threads there's nothing to refresh.

FACEBOOK_GRAPH_URL = "https://graph.facebook.com/v23.0"


def post_to_facebook(
    page_id: str, page_access_token: str, text: str, image_url: str, alt_text: str
) -> Published:
    photo = _request(
        "Facebook photo post",
        "POST",
        f"{FACEBOOK_GRAPH_URL}/{page_id}/photos",
        data={
            "url": image_url,
            "caption": text,
            "alt_text_custom": alt_text,
            "access_token": page_access_token,
        },
    ).json()
    post_id = photo.get("post_id") or photo["id"]
    return Published(f"https://www.facebook.com/{post_id}", post_id)


def facebook_metrics(page_access_token: str, post_id: str) -> dict[str, float]:
    post = _request(
        "Facebook stats",
        "GET",
        f"{FACEBOOK_GRAPH_URL}/{post_id}",
        params={
            "fields": "reactions.summary(total_count).limit(0),"
            "comments.summary(total_count).limit(0),shares",
            "access_token": page_access_token,
        },
    ).json()
    return {
        "reactions": post.get("reactions", {}).get("summary", {}).get("total_count", 0),
        "comments": post.get("comments", {}).get("summary", {}).get("total_count", 0),
        "shares": post.get("shares", {}).get("count", 0),
    }


def facebook_comments(page_access_token: str, post_id: str) -> list[Comment]:
    comments = _request(
        "Facebook comments",
        "GET",
        f"{FACEBOOK_GRAPH_URL}/{post_id}/comments",
        params={"fields": "id,message,from,permalink_url", "access_token": page_access_token},
    ).json().get("data", [])
    return [
        Comment(
            comment["id"], (comment.get("from") or {}).get("name", "someone"),
            comment.get("message", ""), comment.get("permalink_url", ""),
        )
        for comment in comments
    ]
