# FAEVAD

Online shop and gallery for Florian Ewing's original ink drawings and watercolors:
**https://florianaewing.github.io/FAEVAD/**

The repo has two parts. One is a static site where people can browse and buy prints. The other is
a small bot that posts one piece a day to social media, using a caption the artist writes.

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
- **Images:** [`images/artwork/`](images/artwork/) holds only 400px web copies with their EXIF
  data removed, so they can't be printed at good quality. The originals stay off the repo.
  [`images/instagram/`](images/instagram/) has white-matted copies of pieces whose shape
  Instagram won't accept.
- **Analytics:** [GoatCounter](https://www.goatcounter.com/) counts visitors without cookies,
  plus clicks on the buy buttons.

## The social bot ([`social/`](social/))

A Python service ([`social/bot/daily_bot.py`](social/bot/daily_bot.py)) that runs on the
artist's own computer as a systemd user service. Each day:

1. At 8:00 Pacific it sends the next unposted piece in [`social/queue.yml`](social/queue.yml)
   to the artist on Telegram. Watercolors go out on Wednesdays, inks on the other days.
2. The artist replies with a caption. It's used word for word; the bot never writes or edits
   text.
3. Each platform posts at its own best time of day (see [`social/strategy.yml`](social/strategy.yml)),
   with a tracked link to the piece's page and a few hashtags.
   - Instagram, Pinterest and X post through [Buffer](https://buffer.com).
   - Bluesky, Mastodon, Threads and the Facebook Page post directly through their own APIs.
4. If a platform fails, a bug report goes to Telegram and is filed as a GitHub issue labelled
   `bot-bug-report`. Once every platform is done, the results are written to `queue.yml`,
   committed and pushed.
5. Afterwards, it forwards new comments for a few hours, records each post's stats after 48
   hours, and sends a weekly summary on Sundays.

API keys and other secrets live outside the repo in `~/.config/faevad-social-bot/.env`.
[`social/bot/.env.example`](social/bot/.env.example) explains where each one comes from.
Setup steps are in the header of
[`faevad-social-bot.service`](social/bot/faevad-social-bot.service).

## Adding a piece

1. Save the full-size original outside the repo. Add a 400px copy with its EXIF data removed to
   `images/artwork/`.
2. Create its three Stripe print links.
3. Add the piece to `_data/artwork.yml` and create its `piece-`, `buy-` and `venmo-` pages.
4. Add it to the end of `social/queue.yml`.
5. Run `social/bot/.venv/bin/python social/bot/instagram_images.py` in case it needs an
   Instagram mat.
