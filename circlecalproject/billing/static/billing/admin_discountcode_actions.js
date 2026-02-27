(function () {
  function initDiscountCodeActionButton() {
    var actionSelect = document.querySelector('#changelist-actions select[name="action"]') || document.querySelector('select[name="action"]');
    if (!actionSelect) return;

    var form = actionSelect.closest('form');
    if (!form) return;

    // Avoid duplicates on dynamic redraws.
    if (form.querySelector('#cc-apply-action-btn')) return;

    var host = actionSelect.parentElement || form;

    var btn = document.createElement('button');
    btn.type = 'submit';
    btn.name = 'index';
    btn.value = '0';
    btn.id = 'cc-apply-action-btn';
    btn.textContent = 'Apply Action';
    btn.style.marginLeft = '8px';
    btn.style.padding = '6px 10px';
    btn.style.borderRadius = '6px';
    btn.style.border = '1px solid rgba(255,255,255,0.45)';
    btn.style.background = '#111827';
    btn.style.color = '#ffffff';
    btn.style.cursor = 'pointer';
    btn.style.fontSize = '13px';
    btn.style.fontWeight = '600';
    btn.title = 'Run selected action';

    host.appendChild(btn);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initDiscountCodeActionButton);
  } else {
    initDiscountCodeActionButton();
  }

  // Re-run shortly in case admin theme hydrates/replaces toolbar after load.
  setTimeout(initDiscountCodeActionButton, 400);
  setTimeout(initDiscountCodeActionButton, 1200);
})();
