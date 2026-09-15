(function () {
    var script = document.currentScript;
    var formspreeEndpoint = script.getAttribute('data-formspree-endpoint');
    var workerEndpoint = script.getAttribute('data-worker-endpoint');

    document.addEventListener('DOMContentLoaded', function () {
        var form = document.getElementById('bitcoin-form');
        if (!form) return;

        var sizeSelect = document.getElementById('b-size');
        var priceLabel = form.querySelector('.bitcoin-price');
        var statusEl = form.querySelector('.bitcoin-status');
        var submitBtn = form.querySelector('button[type="submit"]');

        var resultEl = document.getElementById('bitcoin-result');
        var orderCodeEl = document.getElementById('bitcoin-order-code');
        var checkEl = document.getElementById('bitcoin-check');
        var payLinkEl = document.getElementById('bitcoin-pay-link');

        function currentPrice() {
            var opt = sizeSelect.options[sizeSelect.selectedIndex];
            return opt ? opt.getAttribute('data-price') : '0';
        }

        function updatePriceLabel() {
            if (priceLabel) priceLabel.textContent = '$' + currentPrice();
        }
        sizeSelect.addEventListener('change', updatePriceLabel);
        updatePriceLabel();

        function makeOrderCode(pieceId) {
            var base = pieceId.replace(/-/g, '').toUpperCase().slice(0, 8);
            var rand = Math.random().toString(36).slice(2, 6).toUpperCase();
            return base + '-' + rand;
        }

        function showStatus(message) {
            statusEl.hidden = false;
            statusEl.textContent = message;
        }

        form.addEventListener('submit', function (evt) {
            evt.preventDefault();

            var colorInput = form.querySelector('input[name="backing_color"]:checked');
            if (!colorInput) {
                showStatus('Please choose a backing color.');
                return;
            }

            var pieceId = form.getAttribute('data-piece-id');
            var pieceTitle = form.getAttribute('data-piece-title');
            var size = sizeSelect.value;
            var color = colorInput.value;
            var orderCode = makeOrderCode(pieceId);

            submitBtn.disabled = true;
            showStatus('Creating your Bitcoin invoice…');

            fetch(workerEndpoint, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ size: size, piece_title: pieceTitle, order_code: orderCode })
            }).then(function (res) {
                if (!res.ok) throw new Error('invoice creation failed');
                return res.json();
            }).then(function (invoice) {
                showStatus('Submitting your order…');

                var formData = new FormData(form);
                formData.append('order_code', orderCode);
                formData.append('piece_title', pieceTitle);
                formData.append('payment_method', 'bitcoin');
                formData.append('price_usd', invoice.price_usd);
                formData.append('price_btc', invoice.price_btc);
                formData.append('rate_usd_per_btc', invoice.rate_usd_per_btc);
                var check = invoice.price_btc + ' BTC × $' + invoice.rate_usd_per_btc + '/BTC ≈ $' + invoice.price_usd;
                formData.append('reconciliation_check', check);

                return fetch(formspreeEndpoint, {
                    method: 'POST',
                    headers: { 'Accept': 'application/json' },
                    body: formData
                }).then(function (res) {
                    if (!res.ok) throw new Error('order submission failed');
                    return invoice;
                });
            }).then(function (invoice) {
                statusEl.hidden = true;
                form.hidden = true;

                orderCodeEl.textContent = orderCode;
                checkEl.textContent = invoice.price_btc + ' BTC × $' + invoice.rate_usd_per_btc + '/BTC ≈ $' + invoice.price_usd;
                payLinkEl.href = invoice.hosted_checkout_url;
                payLinkEl.textContent = 'Pay ' + invoice.price_btc + ' BTC (~$' + invoice.price_usd + ')';
                resultEl.hidden = false;
            }).catch(function () {
                submitBtn.disabled = false;
                showStatus('Something went wrong — please email florianaewing@gmail.com instead.');
            });
        });
    });
})();
