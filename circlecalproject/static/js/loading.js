(function(){
    // Ensure global showLoading/hideLoading exist (base provides them, but keep safe fallback)
    function _showLoading(msg){
        const ov = document.getElementById('global-loading-overlay');
        if (!ov) return;
        try { if (msg) { const txt = ov.querySelector('div.text-gray-700'); if (txt) txt.textContent = msg; } } catch(e){}
        ov.classList.remove('hidden');
        ov.classList.add('flex');
    }
    function _hideLoading(){
        const ov = document.getElementById('global-loading-overlay');
        if (!ov) return;
        ov.classList.add('hidden');
        ov.classList.remove('flex');
        try { const txt = ov.querySelector('div.text-gray-700'); if (txt) txt.textContent = 'Loading…'; } catch(e){}
    }

    // Use existing functions from templates if defined, otherwise provide ours
    if (typeof window.showLoading !== 'function') window.showLoading = _showLoading;
    if (typeof window.hideLoading !== 'function') window.hideLoading = _hideLoading;

    // Helper to wrap an async action with loading overlay
    function wrapAsyncAction(fn, msg){
        return async function(...args){
            try{
                if (msg) showLoading(msg); else showLoading();
                const res = await fn.apply(this, args);
                return res;
            } finally {
                hideLoading();
            }
        };
    }

    // Attach automatic behavior for forms and elements
    function attachAutoHandlers(){
        // Forms with class 'show-loading-on-submit' will show loader when submitted
        document.querySelectorAll('form.show-loading-on-submit').forEach(form => {
            form.addEventListener('submit', function(){
                // If the form is submitted via AJAX, developers should use wrapAsyncAction.
                showLoading();
            }, {capture: true});
        });

        // Elements with attribute data-loading-on-click will show loader on click.
        // Useful for buttons/links that trigger server changes and then navigate/reload.
        document.querySelectorAll('[data-loading-on-click]').forEach(el => {
            el.addEventListener('click', function(e){
                const msg = el.getAttribute('data-loading-msg') || undefined;
                showLoading(msg);
                // If the element returns false or prevents default, hide later.
                // For safety hide after 10s if nothing happens (avoids stuck overlay).
                setTimeout(()=>{ try{ hideLoading(); }catch(e){} }, 10000);
            });
        });
    }

    // Small utility: optionally show loader for fetch calls that opt-in by setting a header
    (function(){
        if (!window.fetch) return;
        const origFetch = window.fetch.bind(window);
        window.fetch = function(input, init){
            try{
                const shouldShow = (init && init.headers && (init.headers['X-Show-Loading'] || init.headers['x-show-loading'])) ||
                                   (typeof input === 'string' && /\/ajax\//.test(input)) ||
                                   (typeof input === 'string' && /\/api\//.test(input));
                let timer = null;
                if (shouldShow) {
                    // show after short delay to avoid flicker on quick requests
                    timer = setTimeout(()=> showLoading(), 150);
                }
                return origFetch(input, init).then(r => {
                    if (timer) { clearTimeout(timer); hideLoading(); }
                    return r;
                }).catch(e => {
                    if (timer) { clearTimeout(timer); hideLoading(); }
                    throw e;
                });
            } catch(e){
                return origFetch(input, init);
            }
        };
    })();

    // Expose utility
    window.wrapAsyncAction = wrapAsyncAction;

    // Attach handlers on DOM ready
    if (document.readyState === 'loading'){
        document.addEventListener('DOMContentLoaded', attachAutoHandlers);
    } else {
        attachAutoHandlers();
    }
})();
