// FAEVAD Bitcoin invoice creator.
//
// Public endpoint (called from bitcoin-<id>.html on the site) that creates a
// single-use OpenNode charge for one of the three fixed print sizes. The
// price is looked up here from a hardcoded table -- never trust a
// client-supplied amount, or a request could be tampered with to invoice a
// larger print at a smaller price.
//
// Secret: OPENNODE_API_KEY must be set via `wrangler secret put
// OPENNODE_API_KEY` -- never hardcoded here, never sent to the browser.

const PRICES_USD = { "5x7": 15, "8x10": 30, "11x14": 50 };
const ALLOWED_ORIGIN = "https://florianaewing.github.io";

function corsHeaders() {
  return {
    "Access-Control-Allow-Origin": ALLOWED_ORIGIN,
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
  };
}

export default {
  async fetch(request, env) {
    if (request.method === "OPTIONS") {
      return new Response(null, { headers: corsHeaders() });
    }

    if (request.method !== "POST") {
      return new Response("Method not allowed", { status: 405, headers: corsHeaders() });
    }

    const origin = request.headers.get("Origin") || "";
    if (origin !== ALLOWED_ORIGIN) {
      return new Response("Forbidden", { status: 403, headers: corsHeaders() });
    }

    let body;
    try {
      body = await request.json();
    } catch (e) {
      return new Response("Bad request body", { status: 400, headers: corsHeaders() });
    }

    const size = body.size;
    const orderCode = body.order_code;
    const pieceTitle = String(body.piece_title || "").slice(0, 80);

    const amountUsd = PRICES_USD[size];
    if (!amountUsd) {
      return new Response("Invalid size", { status: 400, headers: corsHeaders() });
    }
    if (!orderCode || typeof orderCode !== "string" || orderCode.length > 40) {
      return new Response("Invalid order code", { status: 400, headers: corsHeaders() });
    }

    const description = `FAEVAD ${orderCode} — ${pieceTitle}, ${size} print`;

    let openNodeRes;
    try {
      openNodeRes = await fetch("https://api.opennode.com/v1/charges", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": env.OPENNODE_API_KEY,
        },
        body: JSON.stringify({
          amount: amountUsd,
          currency: "USD",
          description: description,
          order_id: orderCode,
          ttl: 15,
        }),
      });
    } catch (e) {
      return new Response("Could not reach payment processor", { status: 502, headers: corsHeaders() });
    }

    if (!openNodeRes.ok) {
      const errText = await openNodeRes.text().catch(() => "");
      return new Response("Invoice creation failed: " + errText, { status: 502, headers: corsHeaders() });
    }

    const payload = await openNodeRes.json();
    const charge = payload.data;

    // charge.amount is the BTC amount in satoshis.
    const btcAmount = charge.amount / 1e8;
    const rate = amountUsd / btcAmount;

    return new Response(
      JSON.stringify({
        hosted_checkout_url: charge.hosted_checkout_url,
        price_usd: amountUsd.toFixed(2),
        price_btc: btcAmount.toFixed(8),
        rate_usd_per_btc: rate.toFixed(2),
        expires_in_minutes: 15,
      }),
      { headers: { "Content-Type": "application/json", ...corsHeaders() } }
    );
  },
};
