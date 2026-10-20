# FAEVAD

Online shop and gallery for Florian Ewing's original ink drawings and watercolors:
**https://florianaewing.github.io/FAEVAD/**

The repo has two parts. One is a static site where people can browse and buy prints. The other is
a small bot that posts one piece a day to social media, using a caption the artist writes, and
reminds the artist on Telegram about art competition deadlines.

## The site

A Jekyll site on GitHub Pages, with no server of its own.

- **Catalogue:** every piece lives in [`_data/artwork.yml`](_data/artwork.yml): title,
  collection, image, alt text, description, and its print sizes with their Stripe links.
  The pages are built from this file.
- **Pages:** each piece has three small stub pages at the repo root, `piece-<id>.html`,
  `buy-<id>.html` and `venmo-<id>.html`, built from the templates in [`_layouts/`](_layouts/).
  Each collection has a gallery page (`inks.html`, `watercolors.html`).
- **Checkout:**
  - **Stripe:** Stripe Payment Links for 5×7 ($15), 8×10 ($30) and 11×14 ($50) prints.
    Buyers choose black or white backing at checkout.
  - **Venmo:** an order form sent through Formspree, plus a Venmo link carrying an order code
    for matching payments by hand.
  - **Bitcoin:** checkout is built (`cloudflare/`, `bitcoin-<id>.html`) but switched off in
    `_config.yml`.
- **Images:** [`images/artwork/`](images/artwork/) holds only 500px web copies (500px on the
  long side, or on the short side for tall pieces) with their EXIF data removed, so they can't
  be printed at good quality. The originals stay off the repo.
  [`images/instagram/`](images/instagram/) has white-matted copies of pieces whose shape
  Instagram won't accept.
- **Analytics:** [GoatCounter](https://www.goatcounter.com/) counts visitors without cookies,
  plus clicks on the buy buttons.

## The social bot ([`social/`](social/))

A Python service ([`social/bot/daily_bot.py`](social/bot/daily_bot.py)) that runs on the
artist's own computer as a systemd user service. It posts Wednesday through Sunday (the days
are set in [`social/strategy.yml`](social/strategy.yml)). On each posting day:

1. At 8:00 Pacific it sends the next unposted piece in [`social/queue.yml`](social/queue.yml)
   to the artist on Telegram. Watercolors go out on Wednesdays, inks on Thursday, Saturday and
   Sunday.
2. The artist replies with a caption. It's used word for word; the bot never writes or edits
   text.
3. Each platform posts at its own best time of day (see [`social/strategy.yml`](social/strategy.yml)),
   with a tracked link to the piece's page and a few hashtags.
   - Instagram, Pinterest and X post through [Buffer](https://buffer.com).
   - Bluesky, Mastodon, Threads and the Facebook Page post directly through their own APIs.
4. If a platform fails, a bug report goes to Telegram and is filed as a GitHub issue labelled
   `bot-bug-report`. Once every platform is done, the results are written to `queue.yml`,
   committed and pushed.
5. Afterwards, it forwards new comments for a few hours and records each post's stats after 48
   hours.

**Posting a piece on a set day.** Give its `queue.yml` entry a `post_on` date (for example
`post_on: 2026-12-10`). The piece is held until that day and then goes ahead of the normal
order. If the bot misses the day, the piece goes back into the normal order. Watercolor and ink
days still apply, so pick a day that matches the piece.

**Weekly summary.** Every Sunday at 18:00 it sends each platform's stats for the week. When
`GOATCOUNTER_API_KEY` is set, the summary also covers the site: visits, where visitors came
from, the most-viewed pages and clicks on the buy buttons
([`social/bot/site_stats.py`](social/bot/site_stats.py)).

**Goblin Tales Fridays.** Fridays post the next comic from
[Goblin Tales](https://goblin-tales.com), a webcomic by Sam Thwaits, instead of one of the
artist's pieces. The comics and their order come from the `data.json` file in the
goblintales repo, and the caption is filled in automatically from the template in
`strategy.yml`, so there's nothing to reply to. The bot sends a heads-up, posts on the same
schedule, and records each comic in [`social/goblintales.yml`](social/goblintales.yml). It
never posts a comic larger than 500px; full-size originals are kept off the web.

**Art competition reminders.** [`social/competitions.yml`](social/competitions.yml) lists the
art competitions the artist has approved, with each one's current application period. At noon
Pacific the bot sends a Telegram reminder two weeks before applications open, on the day they
open, two weeks before the deadline, and on the deadline day
([`social/bot/competitions.py`](social/bot/competitions.py)). Reminders whose date has already
passed are skipped. Only entries with `approved: true` get reminders, and the bot never applies
to anything itself. Once a competition's deadline passes, update its entry with the next year's
dates.

API keys and other secrets live outside the repo in `~/.config/faevad-social-bot/.env`.
[`social/bot/.env.example`](social/bot/.env.example) explains where each one comes from.
Setup steps are in the header of
[`faevad-social-bot.service`](social/bot/faevad-social-bot.service).

## Adding a piece

1. Save the full-size original outside the repo. Add a 500px copy with its EXIF data removed to
   `images/artwork/` (500px on the long side; on the short side if the piece is taller than wide).
2. Create its three Stripe print links.
3. Add the piece to `_data/artwork.yml` and create its `piece-`, `buy-` and `venmo-` pages.
4. Add it to the end of `social/queue.yml`.
5. Run `social/bot/.venv/bin/python social/bot/instagram_images.py` in case it needs an
   Instagram mat.
