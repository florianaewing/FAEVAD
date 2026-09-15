(function () {
    var script = document.currentScript;
    var endpoint = script.getAttribute('data-endpoint');
    var venmoUsername = script.getAttribute('data-venmo-username');

    document.addEventListener('DOMContentLoaded', function () {
        var form = document.getElementById('venmo-form');
        if (!form) return;

        var sizeSelect = document.getElementById('v-size');
        var priceLabel = form.querySelector('.venmo-price');
        var statusEl = form.querySelector('.venmo-status');
        var resultEl = document.getElementById('venmo-result');
        var orderCodeEl = document.getElementById('venmo-order-code');
        var payLinkEl = document.getElementById('venmo-pay-link');
        var submitBtn = form.querySelector('button[type="submit"]');

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
            var price = currentPrice();
            var color = colorInput.value;
            var orderCode = makeOrderCode(pieceId);

            var formData = new FormData(form);
            formData.append('order_code', orderCode);
            formData.append('piece_title', pieceTitle);
            formData.append('price', price);
            formData.append('payment_method', 'venmo');

            submitBtn.disabled = true;
            showStatus('Submitting…');

            fetch(endpoint, {
                method: 'POST',
                headers: { 'Accept': 'application/json' },
                body: formData
            }).then(function (res) {
                if (!res.ok) throw new Error('submit failed');
                return res.json();
            }).then(function () {
                statusEl.hidden = true;
                form.hidden = true;

                var note = 'FAEVAD ' + orderCode + ' — ' + pieceTitle + ', ' + size + ', ' + color + ' backing';
                var params = new URLSearchParams({ txn: 'pay', amount: price, note: note });
                var venmoUrl = 'https://venmo.com/u/' + venmoUsername + '?' + params.toString();

                orderCodeEl.textContent = orderCode;
                payLinkEl.href = venmoUrl;
                payLinkEl.textContent = 'Pay $' + price + ' on Venmo';
                resultEl.hidden = false;
            }).catch(function () {
                submitBtn.disabled = false;
                showStatus('Something went wrong — please email florianaewing@gmail.com instead.');
            });
        });
    });
})();
